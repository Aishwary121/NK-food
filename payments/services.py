"""Server-authoritative UPI requests and audited manual payment verification."""
from decimal import Decimal
from io import BytesIO
from urllib.parse import quote, urlencode
import qrcode
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from groups.models import DiningGroup
from orders.models import BillAllocation, Order
from .models import Payment, PaymentAudit, PaymentQRSettings
from .validators import validate_upi_id

def is_collector(user, order):
    return user.is_authenticated and (user.is_staff or order.group.collector_id == user.pk)

def require_collector(user, order):
    if not is_collector(user, order):
        raise PermissionDenied('Only the designated collector or an administrator can manage payments.')

def build_upi_uri(allocation):
    # Reload the allocation: neither request parameters nor caller-mutated model
    # objects can override the finalized member amount used in the payment URI.
    pk = allocation.pk if isinstance(allocation, BillAllocation) else allocation
    record = BillAllocation.objects.select_related('order__group', 'member').get(pk=pk)
    if record.order.status != 'FINALIZED' or record.amount <= 0:
        raise ValidationError('A positive finalized bill allocation is required for a payment QR.')
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

@transaction.atomic
def save_qr_settings(order_id, actor, cleaned_data):
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
    if Payment.objects.filter(allocation__order=order, status__in=['PENDING', 'VERIFIED']).exists():
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
    allocation = BillAllocation.objects.select_related('order__group', 'member').get(pk=allocation_id)
    Order.objects.select_for_update().get(pk=allocation.order_id)
    if allocation.member.user_id != actor.pk:
        raise PermissionDenied('Only the member can submit their payment claim.')
    if allocation.order.status != 'FINALIZED' or allocation.amount <= 0:
        raise ValidationError('This allocation is not payable.')
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
    payment = Payment.objects.select_related('allocation__order__group').get(pk=payment_id)
    order = Order.objects.select_for_update().select_related('group').get(pk=payment.allocation.order_id)
    require_collector(actor, order)
    if order.status != 'FINALIZED':
        raise ValidationError('Only finalized orders can receive payment reviews.')
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

def payment_totals(order):
    amounts = {state: Decimal('0.00') for state in Payment.Status.values}
    rows = []
    for allocation in order.allocations.select_related('member__user').order_by('member_id'):
        payment = get_payment(allocation)
        amounts[payment.status] += allocation.amount
        rows.append({'allocation': allocation, 'payment': payment})
    total = sum(amounts.values(), Decimal('0.00'))
    return {
        'rows': rows, 'total': total, 'received': amounts['VERIFIED'],
        'pending_verification': amounts['PENDING'], 'outstanding': total - amounts['VERIFIED'],
    }
