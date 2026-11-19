"""Server-authoritative UPI requests and audited manual payment verification."""
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from urllib.parse import quote, urlencode
import qrcode
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from groups.models import DiningGroup
from orders.models import BillAllocation, Order
from .models import CounterPayment, Payment, PaymentAudit, PaymentQRSettings
from .validators import validate_upi_id


def demo_payments_enabled():
    return settings.PAYMENT_DEMO_MODE


def require_real_payments():
    if demo_payments_enabled():
        raise ValidationError('Demo mode is active. Real UPI payments and verification are disabled.')

def is_collector(user, order):
    collector_id = order.group.owner_id if order.split_policy == 'EQUAL' else order.group.collector_id
    return user.is_authenticated and (user.is_staff or collector_id == user.pk)


def is_host_allocation(allocation):
    return allocation.order.split_policy == 'EQUAL' and allocation.member.user_id == allocation.order.group.owner_id


def collection_payments(order):
    payments = Payment.objects.filter(allocation__order=order, allocation__amount__gt=0)
    if order.split_policy == 'EQUAL':
        payments = payments.exclude(allocation__member__user_id=order.group.owner_id)
    return payments

def require_collector(user, order):
    if not is_collector(user, order):
        raise PermissionDenied('Only the designated collector or an administrator can manage payments.')

def build_upi_uri(allocation):
    require_real_payments()
    # Reload the allocation: neither request parameters nor caller-mutated model
    # objects can override the finalized member amount used in the payment URI.
    pk = allocation.pk if isinstance(allocation, BillAllocation) else allocation
    record = BillAllocation.objects.select_related('order__group', 'member').get(pk=pk)
    if record.order.status != 'FINALIZED' or record.amount <= 0:
        raise ValidationError('A positive finalized bill allocation is required for a payment QR.')
    if is_host_allocation(record):
        raise ValidationError('The host pays the full bill at the counter and does not transfer a share to themselves.')
    try:
        config = PaymentQRSettings.objects.get(order=record.order)
    except PaymentQRSettings.DoesNotExist as exc:
        raise ValidationError('The collector has not configured a UPI account yet.') from exc
    validate_upi_id(config.upi_id)
    if not config.display_name:
        raise ValidationError('The collector must enter the UPI account display name.')
    params = {
        'pa': config.upi_id, 'pn': config.display_name,
        'am': format(record.amount, '.2f'), 'cu': 'INR',
        'tn': f'NK_FOODCOURT_{record.order.number}',
        # Distinct member reference even when two allocations have equal amounts.
        'tr': f'NK{record.order_id}M{record.member_id}',
    }
    return 'upi://pay?' + urlencode(params, quote_via=quote)

def generate_qr_png(uri):
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    code.add_data(uri)
    code.make(fit=True)
    output = BytesIO()
    code.make_image(fill_color='black', back_color='white').save(output, format='PNG')
    return output.getvalue()

def get_payment(allocation):
    return Payment.objects.get_or_create(allocation=allocation)[0]


def member_payment_context(order, user):
    """Read-only, member-specific next action for the group and order screens.

    Draft totals are estimates only. Every actionable UPI amount comes from the
    immutable final allocation, and merely viewing this card never creates or
    transitions a payment claim.
    """
    if not user.is_authenticated:
        return {'payment_action': None}
    order = Order.objects.select_related('group__owner', 'group__collector').get(pk=order.pk)
    member = order.group.memberships.filter(user=user).first()
    if member is None:
        return {'payment_action': None}
    demo_mode = demo_payments_enabled()
    config = None if demo_mode else PaymentQRSettings.objects.filter(order=order).first()
    method = config.preferred_method if config else ''
    direct_ready = False
    if config and method in (PaymentQRSettings.Method.DYNAMIC, PaymentQRSettings.Method.STATIC):
        try:
            validate_upi_id(config.upi_id)
            direct_ready = bool(config.display_name.strip())
        except ValidationError:
            pass
    setup_ready = demo_mode or bool(config and (
        (method == PaymentQRSettings.Method.DYNAMIC and direct_ready) or
        (method == PaymentQRSettings.Method.STATIC and config.static_qr)
    ))
    host = order.split_policy == Order.SplitPolicy.EQUAL and user.pk == order.group.owner_id
    receiver = order.group.owner if order.split_policy == Order.SplitPolicy.EQUAL else order.group.collector
    action = {
        'state': 'SETUP_REQUIRED', 'order': order, 'amount': None,
        'allocation': None, 'payment': None, 'upi_uri': '', 'config': config,
        'receiver': receiver, 'receiver_name': receiver.get_full_name() or receiver.username,
        'is_host': host, 'can_manage': is_collector(user, order),
        'can_finalize': user.pk == order.group.owner_id and order.group.is_open,
        'finalized': order.status == Order.Status.FINALIZED,
        'status': Payment.Status.UNPAID, 'setup_ready': setup_ready,
        'cart_item_count': 0, 'own_cart_item_count': 0,
        'demo_mode': demo_mode,
    }
    context = {'payment_action': action}
    if order.group.status == DiningGroup.Status.CANCELLED:
        action['state'] = 'CANCELLED'
        return context
    if order.status != Order.Status.FINALIZED:
        from orders.models import CartItem
        from orders.services import bill_breakdown
        action['state'] = 'DRAFT'
        action['amount'] = next((row['amount'] for row in bill_breakdown(order)
                                 if row['member'].pk == member.pk), None)
        carts = CartItem.objects.filter(member__group=order.group)
        action['cart_item_count'] = carts.count()
        action['own_cart_item_count'] = carts.filter(member=member).count()
        return context
    allocation = order.allocations.filter(member=member).select_related('member__user').first()
    action['allocation'] = allocation
    if allocation is None:
        return context
    action['amount'] = allocation.amount
    if host:
        action['state'] = 'HOST'
        return context
    payment = Payment.objects.filter(allocation=allocation).first()
    action['payment'] = payment
    action['status'] = payment.status if payment else Payment.Status.UNPAID
    if allocation.amount <= 0:
        action['state'] = 'ZERO'
    elif action['status'] in (Payment.Status.VERIFIED, Payment.Status.DEMO_PAID):
        action['state'] = action['status']
    elif demo_mode:
        action['state'] = 'DEMO_READY'
    elif action['status'] == Payment.Status.PENDING:
        action['state'] = 'PENDING'
    elif setup_ready:
        if direct_ready:
            try:
                action['upi_uri'] = build_upi_uri(allocation)
            except ValidationError:
                pass
        if action['upi_uri']:
            action['state'] = 'READY'
        elif method == PaymentQRSettings.Method.STATIC:
            action['state'] = 'STATIC_ONLY'
    return context

@transaction.atomic
def save_qr_settings(order_id, actor, cleaned_data):
    require_real_payments()
    # Match group/order service lock order; a previous collector must not save
    # stale receiving details concurrently with collector reassignment.
    group_id = Order.objects.values_list('group_id', flat=True).get(pk=order_id)
    DiningGroup.objects.select_for_update().get(pk=group_id)
    order = Order.objects.select_for_update().select_related('group').get(pk=order_id)
    require_collector(actor, order)
    if order.group.status == 'CANCELLED':
        raise ValidationError('Cancelled orders cannot receive payments.')
    # Receiver changes while a payment is under review or verified can make
    # audit evidence ambiguous. Keep that order's collection destination fixed.
    if collection_payments(order).filter(status__in=['PENDING', 'VERIFIED']).exists():
        raise ValidationError('QR settings are locked while any payment is pending verification or verified.')
    config, _ = PaymentQRSettings.objects.get_or_create(order=order)
    old_name = config.static_qr.name
    config.upi_id = cleaned_data.get('upi_id', '').strip()
    config.display_name = cleaned_data['display_name'].strip()
    config.preferred_method = cleaned_data['preferred_method']
    if config.upi_id:
        validate_upi_id(config.upi_id)
    upload = cleaned_data.get('static_qr')
    if upload:
        config.static_qr = upload
    elif cleaned_data.get('remove_qr'):
        config.static_qr = ''
    if config.preferred_method == 'DYNAMIC' and not config.upi_id:
        raise ValidationError('A UPI ID is required for dynamic QR codes.')
    if config.preferred_method == 'STATIC' and not config.static_qr:
        raise ValidationError('An uploaded QR is required for static payments.')
    config.full_clean()
    config.save()
    PaymentAudit.objects.create(order=order, actor=actor, action='QR_SETTINGS_UPDATED', detail=f'Preferred method: {config.get_preferred_method_display()}; uploaded image {"present" if config.static_qr else "absent"}.')
    if old_name and old_name != config.static_qr.name:
        storage = config.static_qr.storage
        transaction.on_commit(lambda: storage.delete(old_name))
    return config

@transaction.atomic
def submit_payment(allocation_id, actor, reference=''):
    require_real_payments()
    allocation = BillAllocation.objects.select_related('order__group', 'member').get(pk=allocation_id)
    Order.objects.select_for_update().get(pk=allocation.order_id)
    if allocation.member.user_id != actor.pk:
        raise PermissionDenied('Only the member can submit their payment claim.')
    if allocation.order.status != 'FINALIZED' or allocation.amount <= 0:
        raise ValidationError('This allocation is not payable.')
    if is_host_allocation(allocation):
        raise ValidationError('Record the full counter payment from the host dashboard instead of paying yourself.')
    config = PaymentQRSettings.objects.filter(order=allocation.order).first()
    if not config or config.preferred_method not in ['STATIC', 'DYNAMIC'] or (config.preferred_method == 'STATIC' and not config.static_qr) or (config.preferred_method == 'DYNAMIC' and not config.upi_id):
        raise ValidationError('The collector must configure a payment method first.')
    payment = get_payment(allocation)
    previous = payment.status
    if previous not in [Payment.Status.UNPAID, Payment.Status.REJECTED]:
        raise ValidationError('This payment has already been submitted or verified.')
    reference = reference.strip()
    if len(reference) > 80:
        raise ValidationError('Transaction reference is too long.')
    now = timezone.now()
    # Conditional update prevents a second request from transitioning stale state.
    changed = Payment.objects.filter(pk=payment.pk, status=previous).update(
        status=Payment.Status.PENDING, reference=reference, submitted_at=now,
        reviewed_at=None, reviewed_by=None, rejection_reason='',
    )
    if changed != 1:
        raise ValidationError('Payment status changed. Refresh and try again.')
    PaymentAudit.objects.create(order=allocation.order, payment=payment, actor=actor, action='PAYMENT_SUBMITTED', old_status=previous, new_status=Payment.Status.PENDING)
    payment.refresh_from_db()
    return payment

@transaction.atomic
def review_payment(payment_id, actor, decision, reason=''):
    require_real_payments()
    payment = Payment.objects.select_related('allocation__order__group').get(pk=payment_id)
    order = Order.objects.select_for_update().select_related('group').get(pk=payment.allocation.order_id)
    require_collector(actor, order)
    if order.status != 'FINALIZED':
        raise ValidationError('Only finalized orders can receive payment reviews.')
    if is_host_allocation(payment.allocation):
        raise ValidationError('The host contribution is recorded with the full counter payment, not as a member transfer.')
    if decision not in [Payment.Status.VERIFIED, Payment.Status.REJECTED]:
        raise ValidationError('Choose verify or reject.')
    reason = reason.strip()
    if decision == Payment.Status.REJECTED and not reason:
        raise ValidationError('A rejection reason is required.')
    if len(reason) > 300:
        raise ValidationError('Rejection reason is too long.')
    changed = Payment.objects.filter(pk=payment.pk, status=Payment.Status.PENDING).update(
        status=decision, reviewed_by=actor, reviewed_at=timezone.now(),
        rejection_reason=reason if decision == Payment.Status.REJECTED else '',
    )
    if changed != 1:
        raise ValidationError('Only pending payments can be reviewed. This payment may already have been reviewed.')
    PaymentAudit.objects.create(order=order, payment=payment, actor=actor, action='PAYMENT_REVIEWED', old_status=Payment.Status.PENDING, new_status=decision, detail=reason if decision == 'REJECTED' else 'Collector confirmed receipt independently.')
    payment.refresh_from_db()
    return payment


@transaction.atomic
def demo_pay(allocation_id, actor):
    """Settle the member's synthetic share without recording a real transfer.

    The ledger amount and identity are reloaded under the order lock. Repeated
    submissions are harmless and produce exactly one simulation audit event.
    """
    if not demo_payments_enabled():
        raise ValidationError('Demo payments are disabled.')
    group_id = BillAllocation.objects.values_list('order__group_id', flat=True).get(pk=allocation_id)
    DiningGroup.objects.select_for_update().get(pk=group_id)
    order_id = BillAllocation.objects.values_list('order_id', flat=True).get(pk=allocation_id)
    order = Order.objects.select_for_update().select_related('group').get(pk=order_id)
    allocation = BillAllocation.objects.select_related('member').get(pk=allocation_id)
    allocation.order = order
    if not actor.is_authenticated or allocation.member.user_id != actor.pk:
        raise PermissionDenied('Only the member can simulate payment of their own share.')
    if order.status != Order.Status.FINALIZED or allocation.amount <= 0 or order.group.status == DiningGroup.Status.CANCELLED:
        raise ValidationError('A positive finalized share in an active group is required.')
    if is_host_allocation(allocation):
        raise ValidationError('The host records the full counter bill instead of paying themselves.')
    payment = get_payment(allocation)
    previous = payment.status
    if previous in (Payment.Status.DEMO_PAID, Payment.Status.VERIFIED):
        return payment
    if previous not in (Payment.Status.UNPAID, Payment.Status.REJECTED, Payment.Status.PENDING):
        raise ValidationError('This payment cannot be simulated.')
    changed = Payment.objects.filter(pk=payment.pk, status=previous).update(
        status=Payment.Status.DEMO_PAID, submitted_at=timezone.now(),
        reviewed_at=None, reviewed_by=None, rejection_reason='', reference='',
    )
    if changed != 1:
        payment.refresh_from_db()
        if payment.status in (Payment.Status.DEMO_PAID, Payment.Status.VERIFIED):
            return payment
        raise ValidationError('Payment status changed. Refresh and try again.')
    PaymentAudit.objects.create(
        order=order, payment=payment, actor=actor, action='DEMO_PAYMENT_COMPLETED',
        old_status=previous, new_status=Payment.Status.DEMO_PAID,
        detail=f'Simulated share INR {allocation.amount:.2f}. No money transferred or bank verification performed.',
    )
    payment.refresh_from_db()
    return payment


@transaction.atomic
def record_counter_payment(order_id, actor, reference=''):
    """Record an explicit host declaration using only the finalized server bill."""
    group_id = Order.objects.values_list('group_id', flat=True).get(pk=order_id)
    DiningGroup.objects.select_for_update().get(pk=group_id)
    order = Order.objects.select_for_update().select_related('group').get(pk=order_id)
    if not actor.is_authenticated or (actor.pk != order.group.owner_id and not actor.is_staff):
        raise PermissionDenied('Only the host or an administrator can record the full counter payment.')
    if order.split_policy != 'EQUAL' or order.status != 'FINALIZED' or order.group.status != 'FINALIZED':
        raise ValidationError('Finalize this equal-split group bill before recording the counter payment.')
    reference = reference.strip()
    if len(reference) > 80:
        raise ValidationError('Counter payment reference is too long.')
    record, created = CounterPayment.objects.get_or_create(order=order, defaults={
        'amount': order.total, 'recorded_by': actor, 'reference': reference,
        'is_demo': demo_payments_enabled(),
    })
    if not created:
        raise ValidationError('The full counter payment has already been recorded.')
    if record.is_demo:
        action = 'DEMO_COUNTER_PAYMENT'
        detail = f'Simulated counter bill INR {order.total:.2f}. No money transferred or bank verification performed.'
    else:
        action = 'COUNTER_PAYMENT_DECLARED'
        detail = f'Full counter bill INR {order.total:.2f} declared paid. This is a declaration, not bank verification.'
    PaymentAudit.objects.create(order=order, actor=actor, action=action, detail=detail)
    return record


def payment_totals(order):
    amounts = {state: Decimal('0.00') for state in Payment.Status.values}
    counts = {state: 0 for state in Payment.Status.values}
    counter_payment = CounterPayment.objects.filter(order=order).select_related('recorded_by').first()
    host_share = Decimal('0.00')
    rows = []
    for allocation in order.allocations.select_related('member__user').order_by('member_id'):
        # Reuse the selected order so host checks do not introduce N+1 queries.
        allocation.order = order
        host = is_host_allocation(allocation)
        payment = None if host else get_payment(allocation)
        if host:
            host_share += allocation.amount
            status_code = 'COUNTER_RECORDED' if counter_payment else 'OWN_SHARE'
            status_label = 'Own share included at counter' if counter_payment else 'Own share - pay at counter'
            if counter_payment and counter_payment.is_demo:
                status_code, status_label = 'DEMO_COUNTER_RECORDED', 'Own share included in demo counter payment'
        else:
            amounts[payment.status] += allocation.amount
            if allocation.amount > 0:
                counts[payment.status] += 1
            status_code = payment.status
            status_label = 'Completed' if payment.status == 'VERIFIED' else payment.get_status_display()
            if allocation.amount == 0:
                status_code, status_label = 'NO_PAYMENT_DUE', 'No payment due'
        rows.append({'allocation': allocation, 'payment': payment, 'is_host': host,
                     'status_code': status_code, 'status_label': status_label})
    total = sum(amounts.values(), Decimal('0.00'))
    return {
        'rows': rows, 'total': total + host_share, 'received': amounts['VERIFIED'],
        'pending_verification': amounts['PENDING'], 'outstanding': total - amounts['VERIFIED'],
        'equal_split': order.split_policy == 'EQUAL', 'counter_payment': counter_payment,
        'host_share': host_share, 'member_due': total, 'member_count': sum(counts.values()),
        'completed_count': counts['VERIFIED'], 'verified_count': counts['VERIFIED'],
        'pending_count': counts['PENDING'], 'unpaid_count': counts['UNPAID'], 'rejected_count': counts['REJECTED'],
        'demo_count': counts['DEMO_PAID'], 'demo_amount': amounts['DEMO_PAID'],
        'demo_outstanding': total - amounts['VERIFIED'] - amounts['DEMO_PAID'],
    }


def collector_revision(order):
    """Opaque read-only revision for member payment and counter status refresh."""
    states = list(Payment.objects.filter(allocation__order=order).order_by('allocation_id').values_list(
        'allocation_id', 'status', 'submitted_at', 'reviewed_at', 'rejection_reason'))
    counter = CounterPayment.objects.filter(order=order).values_list('pk', 'is_demo', 'recorded_at').first()
    value = (order.status, order.group.status, str(order.total), demo_payments_enabled(), states, counter)
    return sha256(repr(value).encode('utf-8')).hexdigest()
