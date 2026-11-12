from decimal import Decimal, InvalidOperation, ROUND_DOWN
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from groups.models import DiningGroup, Membership
from .models import AllocationAudit, BillAllocation, CartItem, ItemAllocation, Order, OrderItem

CENT = Decimal('0.01')


def money(value):
    try:
        value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError('Enter a valid amount.')
    if not value.is_finite() or value < 0 or value > Decimal('99999999.99'):
        raise ValidationError('Amounts must be finite, non-negative and within the supported limit.')
    if value != value.quantize(CENT):
        raise ValidationError('Amounts may have at most two decimal places.')
    return value.quantize(CENT)


def distribute(total, weights):
    """Largest remainder in integer paise; ties break by membership ID.

    Each allocated paise is accounted for exactly once. No float conversion is
    permitted, including for fractional percentages and tax allocation.
    """
    total = money(total)
    if not weights:
        raise ValidationError('Choose at least one participant.')
    parsed = {}
    for key, value in weights.items():
        try:
            weight = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError('Enter valid sharing values.')
        if not weight.is_finite() or weight < 0 or weight > Decimal('99999999'):
            raise ValidationError('Sharing values must be finite and non-negative.')
        parsed[key] = weight
    denominator = sum(parsed.values())
    if denominator <= 0:
        if total == 0:
            return {key: Decimal('0.00') for key in parsed}
        raise ValidationError('At least one sharing value must be greater than zero.')
    cents = int(total * 100)
    exact = {key: Decimal(cents) * weight / denominator for key, weight in parsed.items()}
    floor = {key: int(value.to_integral_value(rounding=ROUND_DOWN)) for key, value in exact.items()}
    remaining = cents - sum(floor.values())
    ranking = sorted(parsed, key=lambda key: (-(exact[key] - floor[key]), key))
    for key in ranking[:remaining]:
        floor[key] += 1
    return {key: (Decimal(value) / 100).quantize(CENT) for key, value in floor.items()}


def calculate_split(*, unit_price, quantity, mode, values):
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 999:
        raise ValidationError('Quantity must be a whole number from 1 to 999.')
    total = money(money(unit_price) * quantity)
    if not values:
        raise ValidationError('Select at least one group member.')
    if mode == OrderItem.SplitMode.EQUAL:
        return distribute(total, {member: 1 for member in values})
    if mode == OrderItem.SplitMode.INDIVIDUAL:
        if len(values) != 1:
            raise ValidationError('Individual items need exactly one member.')
        return {next(iter(values)): total}
    if mode == OrderItem.SplitMode.QUANTITY:
        weights = {}
        for member, value in values.items():
            try:
                number = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError('Quantities must be positive whole numbers.')
            if not number.is_finite() or number < 1 or number > 999 or number != int(number):
                raise ValidationError('Quantities must be positive whole numbers.')
            weights[member] = int(number)
        if sum(weights.values()) != quantity:
            raise ValidationError('Member quantities must add up to the item quantity.')
        return {member: money(unit_price) * count for member, count in weights.items()}
    if mode == OrderItem.SplitMode.CUSTOM:
        amounts = {member: money(value) for member, value in values.items()}
        if sum(amounts.values()) != total:
            raise ValidationError('Custom amounts must add up to the item total.')
        return amounts
    if mode == OrderItem.SplitMode.PERCENT:
        try:
            percentages = {member: Decimal(str(value)) for member, value in values.items()}
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError('Enter valid percentages.')
        if any(not value.is_finite() or value < 0 for value in percentages.values()) or sum(percentages.values()) != 100:
            raise ValidationError('Percentages must be non-negative and add up to 100.')
        return distribute(total, percentages)
    raise ValidationError('Choose a valid splitting method.')


def locked_order(order_id):
    initial = Order.objects.get(pk=order_id)
    group = DiningGroup.objects.select_for_update().get(pk=initial.group_id)
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status != Order.Status.DRAFT or not group.is_open:
        raise ValidationError('This order is finalized. Its bill allocations are locked.')
    return order, group


def actor_member(group, actor):
    try:
        return group.memberships.get(user=actor)
    except Membership.DoesNotExist:
        raise PermissionDenied('You must be a member of this dining group.')


def recalculate(order):
    subtotal = money(sum((item.subtotal for item in order.items.all()), Decimal('0')))
    if order.discount > subtotal:
        raise ValidationError('Discount cannot exceed the food subtotal. Update the discount first.')
    order.subtotal = subtotal
    order.total = money(subtotal + order.tax - order.discount)
    order.save(update_fields=['subtotal', 'total'])


def item_snapshot(item):
    return {
        'item': item.item_name, 'quantity': item.quantity, 'unit_price': str(item.unit_price), 'mode': item.split_mode,
        'allocations': {str(a.member_id): str(a.amount) for a in item.allocations.all()},
    }


def save_item_allocations(item, amounts, values):
    item.allocations.all().delete()
    ItemAllocation.objects.bulk_create([
        ItemAllocation(order_item=item, member_id=member_id, amount=amount,
                       quantity=int(Decimal(str(values[member_id]))) if item.split_mode == 'QUANTITY' else None)
        for member_id, amount in amounts.items()
    ])


@transaction.atomic
def add_to_cart(*, group_id, actor, menu_item, quantity=1):
    group = DiningGroup.objects.select_for_update().get(pk=group_id)
    member = actor_member(group, actor)
    if not group.is_open or group.order.status != Order.Status.DRAFT:
        raise ValidationError('This group has already finalized its order.')
    if not menu_item.available_now or menu_item.price is None:
        raise ValidationError('This item is currently unavailable.')
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 999:
        raise ValidationError('Choose a quantity from 1 to 999.')
    cart, created = CartItem.objects.get_or_create(member=member, menu_item=menu_item, defaults={'quantity': quantity})
    if not created:
        if cart.quantity + quantity > 999:
            raise ValidationError('A cart item cannot exceed 999 portions.')
        cart.quantity += quantity
        cart.save(update_fields=['quantity'])
    return cart


@transaction.atomic
def update_cart(*, cart_id, actor, quantity):
    initial = CartItem.objects.select_related('member__group').get(pk=cart_id)
    group = DiningGroup.objects.select_for_update().get(pk=initial.member.group_id)
    cart = CartItem.objects.get(pk=cart_id)
    if cart.member.user_id != actor.pk:
        raise PermissionDenied('You may only edit your own cart.')
    if not group.is_open:
        raise ValidationError('This order is finalized.')
    if not isinstance(quantity, int) or not 0 <= quantity <= 999:
        raise ValidationError('Quantity must be from 0 to 999.')
    if quantity == 0:
        cart.delete()
    else:
        cart.quantity = quantity
        cart.save(update_fields=['quantity'])


@transaction.atomic
def submit_cart(*, order_id, actor):
    order, group = locked_order(order_id)
    member = actor_member(group, actor)
    carts = list(member.cart_items.select_related('menu_item'))
    if not carts:
        raise ValidationError('Your cart is empty.')
    for cart in carts:
        food = cart.menu_item
        if not food.available_now or food.price is None:
            raise ValidationError(f'{food.name} is no longer available. Remove it from your cart.')
        category_name = food.category.name.lower()
        shared = member.pair_id and any(word in category_name for word in ('paneer special', 'dal & sabzi', 'main course'))
        if shared:
            participants = list(group.memberships.filter(pair_id=member.pair_id).values_list('pk', flat=True))
            mode = OrderItem.SplitMode.EQUAL
        else:
            participants = [member.pk]
            mode = OrderItem.SplitMode.INDIVIDUAL
        values = {pk: 1 for pk in participants}
        amounts = calculate_split(unit_price=food.price, quantity=cart.quantity, mode=mode, values=values)
        item = OrderItem.objects.create(order=order, menu_item=food, added_by=actor, item_name=food.name,
                                        quantity=cart.quantity, unit_price=food.price, split_mode=mode)
        save_item_allocations(item, amounts, values)
        AllocationAudit.objects.create(order=order, item=item, actor=actor, action='ITEM_ADDED', after=item_snapshot(item))
    member.cart_items.all().delete()
    group.status = DiningGroup.Status.ORDERING
    group.save(update_fields=['status'])
    recalculate(order)
    return order


@transaction.atomic
def change_allocation(*, item_id, actor, quantity, mode, values):
    initial = OrderItem.objects.get(pk=item_id)
    order, group = locked_order(initial.order_id)
    member = actor_member(group, actor)
    item = OrderItem.objects.get(pk=item_id)
    if actor.pk != group.owner_id and item.added_by_id != actor.pk:
        raise PermissionDenied('Only the organizer or the person who added this item can edit it.')
    participant_ids = set(values)
    all_members = set(group.memberships.values_list('pk', flat=True))
    if not participant_ids <= all_members:
        raise ValidationError('All participants must belong to this dining group.')
    if actor.pk != group.owner_id:
        permitted = {member.pk}
        if member.pair_id:
            permitted.update(group.memberships.filter(pair_id=member.pair_id).values_list('pk', flat=True))
        if not participant_ids <= permitted:
            raise PermissionDenied('Ask the organizer to share an item outside your pair.')
    amounts = calculate_split(unit_price=item.unit_price, quantity=quantity, mode=mode, values=values)
    before = item_snapshot(item)
    item.quantity, item.split_mode = quantity, mode
    item.save(update_fields=['quantity', 'split_mode'])
    save_item_allocations(item, amounts, values)
    recalculate(order)
    AllocationAudit.objects.create(order=order, item=item, actor=actor, action='SPLIT_CHANGED', before=before, after=item_snapshot(item))
    return item


@transaction.atomic
def remove_item(*, item_id, actor):
    initial = OrderItem.objects.get(pk=item_id)
    order, group = locked_order(initial.order_id)
    actor_member(group, actor)
    item = OrderItem.objects.get(pk=item_id)
    if actor.pk not in (group.owner_id, item.added_by_id):
        raise PermissionDenied('You cannot remove another member\'s item.')
    AllocationAudit.objects.create(order=order, actor=actor, action='ITEM_REMOVED', before=item_snapshot(item))
    item.delete()
    recalculate(order)


@transaction.atomic
def set_adjustments(*, order_id, actor, tax, discount):
    order, group = locked_order(order_id)
    if actor.pk != group.owner_id:
        raise PermissionDenied('Only the organizer can adjust the bill.')
    before = {'tax': str(order.tax), 'discount': str(order.discount)}
    order.tax, order.discount = money(tax), money(discount)
    if order.discount > order.subtotal:
        raise ValidationError('Discount cannot exceed the food subtotal.')
    order.save(update_fields=['tax', 'discount'])
    recalculate(order)
    AllocationAudit.objects.create(order=order, actor=actor, action='ADJUSTMENT', before=before,
                                    after={'tax': str(order.tax), 'discount': str(order.discount)})


def bill_breakdown(order):
    members = list(order.group.memberships.select_related('user'))
    base = {member.pk: Decimal('0.00') for member in members}
    for allocation in ItemAllocation.objects.filter(order_item__order=order):
        if allocation.member_id not in base:
            raise ValidationError('This order contains an invalid group allocation.')
        base[allocation.member_id] += allocation.amount
    # Allocate the discounted subtotal proportionally, then allocate tax. This
    # never charges somebody with no food when other members have a food share.
    discounted = distribute(order.subtotal - order.discount, base)
    tax_weights = base if sum(base.values()) else {key: 1 for key in base}
    taxes = distribute(order.tax, tax_weights)
    return [{'member': member, 'food_amount': base[member.pk],
             'amount': discounted[member.pk] + taxes[member.pk]} for member in members]


@transaction.atomic
def finalize_order(*, order_id, actor):
    order, group = locked_order(order_id)
    if actor.pk != group.owner_id:
        raise PermissionDenied('Only the organizer can finalize the order.')
    if not group.memberships.filter(user=group.collector).exists():
        raise ValidationError('Choose a payment collector who belongs to this group.')
    if CartItem.objects.filter(member__group=group).exists():
        raise ValidationError('Some members still have items in their personal carts. Submit or remove them first.')
    if not order.items.exists():
        raise ValidationError('Add at least one food item before finalizing.')
    if order.allocations.exists():
        raise ValidationError('Final member bills already exist. This order cannot be finalized twice.')
    recalculate(order)
    for item in order.items.prefetch_related('allocations'):
        allocations = list(item.allocations.all())
        if not allocations or sum((a.amount for a in allocations), Decimal('0')) != item.subtotal:
            raise ValidationError('Every item must be allocated exactly before finalization.')
        if any(a.member.group_id != group.pk for a in allocations):
            raise ValidationError('An allocation belongs to a different group.')
    breakdown = bill_breakdown(order)
    if sum((row['amount'] for row in breakdown), Decimal('0')) != order.total:
        raise ValidationError('The member bills do not match the restaurant total.')
    finalized_at = timezone.now()
    changed = Order.objects.filter(pk=order.pk, status=Order.Status.DRAFT).update(
        status=Order.Status.FINALIZED, finalized_at=finalized_at,
    )
    if changed != 1:
        raise ValidationError('This order was finalized by another request. Refresh to see the final bill.')
    BillAllocation.objects.bulk_create([BillAllocation(order=order, member=row['member'], amount=row['amount']) for row in breakdown])
    order.status, order.finalized_at = Order.Status.FINALIZED, finalized_at
    group.status = DiningGroup.Status.FINALIZED
    group.save(update_fields=['status'])
    AllocationAudit.objects.create(order=order, actor=actor, action='FINALIZED',
                                    after={str(row['member'].pk): str(row['amount']) for row in breakdown})
    return order


def kitchen_summary(order):
    return order.items.values('menu_item_id', 'item_name').annotate(quantity=Sum('quantity')).order_by('item_name')
