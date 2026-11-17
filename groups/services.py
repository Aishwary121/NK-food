from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from .models import DiningGroup, Membership, Pair


@transaction.atomic
def create_group(*, owner, name, max_members=4, description=''):
    from orders.models import Order
    group = DiningGroup(owner=owner, collector=owner, name=name, max_members=max_members, description=description)
    group.full_clean()
    group.save()
    Membership.objects.create(group=group, user=owner)
    Order.objects.create(group=group)
    return group


@transaction.atomic
def join_group(*, user, code):
    from orders.services import sync_equal_item_allocations
    try:
        group = DiningGroup.objects.select_for_update().get(code=code.strip().upper())
    except DiningGroup.DoesNotExist:
        raise ValidationError('That group code was not found.')
    existing = group.memberships.filter(user=user).first()
    if existing:
        return existing
    if not group.is_open:
        raise ValidationError('This group is no longer accepting members.')
    if group.memberships.count() >= group.max_members:
        raise ValidationError('This dining group is full.')
    member = Membership.objects.create(group=group, user=user)
    sync_equal_item_allocations(group.order)
    return member


def require_owner(group, actor):
    if group.owner_id != actor.pk:
        raise PermissionDenied('Only the group organizer can do this.')
    if not group.is_open:
        raise ValidationError('This group is finalized and can no longer be changed.')


@transaction.atomic
def form_pair(*, group, actor, member_ids, name):
    group = DiningGroup.objects.select_for_update().get(pk=group.pk)
    require_owner(group, actor)
    ids = set(member_ids)
    members = list(group.memberships.filter(pk__in=ids))
    if len(ids) != 2 or len(members) != 2:
        raise ValidationError('Choose exactly two members from this group.')
    # Reassign a whole previous pair; never leave an accidental one-person pair.
    old_pairs = {m.pair_id for m in members if m.pair_id}
    for pair_id in old_pairs:
        group.memberships.filter(pair_id=pair_id).update(pair=None)
        Pair.objects.filter(pk=pair_id, group=group).delete()
    pair = Pair.objects.create(group=group, name=name)
    group.memberships.filter(pk__in=ids).update(pair=pair)
    return pair


@transaction.atomic
def set_collector(*, group, actor, membership):
    from orders.models import Order
    from payments.models import PaymentAudit, PaymentQRSettings
    group = DiningGroup.objects.select_for_update().get(pk=group.pk)
    require_owner(group, actor)
    # Match the group -> order lock order used by cart, finalization and QR
    # settings writes. This serializes receiver changes with configuration saves.
    order = Order.objects.select_for_update().get(group=group)
    if order.status != Order.Status.DRAFT:
        raise ValidationError('The payment collector is locked after finalization.')
    membership = group.memberships.select_related('user').filter(pk=membership.pk).first()
    if membership is None:
        raise ValidationError('The collector must be a member of the group.')
    if order.split_policy == Order.SplitPolicy.EQUAL and membership.user_id != group.owner_id:
        raise ValidationError('The host pays the restaurant bill and collects the other members\' shares. The host must remain the payment collector.')
    if group.collector_id == membership.user_id:
        return
    config = PaymentQRSettings.objects.filter(order=order).first()
    if config:
        old_name, storage = config.static_qr.name, config.static_qr.storage
        config.delete()
        if old_name:
            transaction.on_commit(lambda: storage.delete(old_name))
    group.collector = membership.user
    group.save(update_fields=['collector'])
    PaymentAudit.objects.create(order=order, actor=actor, action='COLLECTOR_CHANGED',
                                detail='Collector changed before finalization. Previous receiver and QR settings were cleared.')


@transaction.atomic
def leave_group(*, group, user):
    from orders.models import Order
    from orders.services import sync_equal_item_allocations
    group = DiningGroup.objects.select_for_update().get(pk=group.pk)
    if not group.is_open:
        raise ValidationError('You cannot leave after finalization.')
    if user.pk in (group.owner_id, group.collector_id):
        raise ValidationError('The organizer and collector must stay in the group.')
    member = group.memberships.get(user=user)
    order = Order.objects.select_for_update().get(group=group)
    if order.items.filter(added_by=user).exists() or (order.split_policy != Order.SplitPolicy.EQUAL and member.item_allocations.exists()):
        raise ValidationError('Remove or reassign your ordered food before leaving.')
    if order.split_policy == Order.SplitPolicy.EQUAL:
        member.item_allocations.filter(order_item__order=order).delete()
    if member.pair_id:
        Pair.objects.filter(pk=member.pair_id).delete()
    member.delete()
    sync_equal_item_allocations(order)


@transaction.atomic
def cancel_group(*, group, actor):
    from orders.models import AllocationAudit, Order
    group = DiningGroup.objects.select_for_update().get(pk=group.pk)
    require_owner(group, actor)
    order = Order.objects.select_for_update().get(group=group)
    if order.status != Order.Status.DRAFT or order.allocations.exists():
        raise ValidationError('A finalized order cannot be cancelled.')
    group.status = DiningGroup.Status.CANCELLED
    group.save(update_fields=['status'])
    AllocationAudit.objects.create(order=order, actor=actor, action='GROUP_CANCELLED',
                                    after={'group_status': group.status})


@transaction.atomic
def complete_group(*, group, actor):
    from orders.models import Order
    from payments.models import CounterPayment, Payment, PaymentAudit
    group = DiningGroup.objects.select_for_update().get(pk=group.pk)
    if actor.pk not in (group.owner_id, group.collector_id) and not actor.is_staff:
        raise PermissionDenied('Only the organizer, collector or an administrator can complete this group.')
    order = Order.objects.select_for_update().get(group=group)
    if group.status != DiningGroup.Status.FINALIZED or order.status != Order.Status.FINALIZED:
        raise ValidationError('Only a finalized group can be completed.')
    if order.split_policy == 'EQUAL' and actor.pk != group.owner_id and not actor.is_staff:
        raise PermissionDenied('Only the host or an administrator can complete this equal-split group.')
    if not order.allocations.exists():
        raise ValidationError('This order has no finalized member bills.')
    payable = order.allocations.filter(amount__gt=0)
    if order.split_policy == 'EQUAL':
        if not CounterPayment.objects.filter(order=order).exists():
            raise ValidationError('The host must record paying the full restaurant bill at the counter first.')
        payable = payable.exclude(member__user_id=group.owner_id)
    if payable.exclude(payment__status=Payment.Status.VERIFIED).exists():
        raise ValidationError('Every payable member share must be verified before completing the group.')
    group.status = DiningGroup.Status.COMPLETED
    group.save(update_fields=['status'])
    PaymentAudit.objects.create(order=order, actor=actor, action='GROUP_COMPLETED',
                                detail=('Full counter payment declared and all positive member transfers verified.'
                                        if order.split_policy == 'EQUAL' else 'All positive member allocations were verified before completion.'))
