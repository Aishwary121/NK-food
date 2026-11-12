from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from orders.models import BillAllocation, Order
from .forms import PaymentClaimForm, PaymentReviewForm, QRSettingsForm
from .models import Payment, PaymentQRSettings
from .services import build_upi_uri, generate_qr_png, get_payment, is_collector, payment_totals, require_collector, review_payment, save_qr_settings, submit_payment

def _allocation_for_user(pk, user):
    allocation = get_object_or_404(BillAllocation.objects.select_related('order__group__collector', 'member__user'), pk=pk, order__status='FINALIZED')
    if allocation.member.user_id != user.pk and not is_collector(user, allocation.order):
        raise Http404
    return allocation

def _collector_order(pk, user):
    order = get_object_or_404(Order.objects.select_related('group__collector'), pk=pk)
    require_collector(user, order)
    return order

@login_required
@never_cache
@require_GET
def collector(request, pk):
    order = _collector_order(pk, request.user)
    return render(request, 'payments/collector.html', {
        'order': order, 'config': PaymentQRSettings.objects.filter(order=order).first(),
        'audit': order.payment_audit.select_related('actor', 'payment__allocation__member__user')[:100],
        **payment_totals(order),
    })

@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_view(request, pk):
    order = _collector_order(pk, request.user)
    config = PaymentQRSettings.objects.filter(order=order).first()
    form = QRSettingsForm(request.POST or None, request.FILES or None, instance=config) if request.method == 'POST' else QRSettingsForm(instance=config)
    if request.method == 'POST' and form.is_valid():
        try:
            save_qr_settings(order.pk, request.user, form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Payment QR settings saved.')
            return redirect('payments:settings', pk=pk)
    preview = order.allocations.filter(amount__gt=0).select_related('member__user').first() if order.status == 'FINALIZED' else None
    locked = Payment.objects.filter(allocation__order=order, status__in=['PENDING', 'VERIFIED']).exists()
    return render(request, 'payments/settings.html', {'order': order, 'config': config, 'form': form, 'preview': preview, 'settings_locked': locked})

@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def member(request, pk):
    allocation = _allocation_for_user(pk, request.user)
    payment = get_payment(allocation)
    config = PaymentQRSettings.objects.filter(order=allocation.order).first()
    form = PaymentClaimForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            submit_payment(allocation.pk, request.user, form.cleaned_data['reference'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.info(request, 'Payment submitted for verification. The collector will check receipt before verifying it.')
            return redirect('payments:member', pk=pk)
    uri = ''
    if config and config.upi_id and allocation.amount > 0:
        try:
            uri = build_upi_uri(allocation)
        except ValidationError:
            pass
    method = config.preferred_method if config else ''
    ready = allocation.amount > 0 and bool(config and ((method == 'STATIC' and config.static_qr) or (method == 'DYNAMIC' and uri)))
    return render(request, 'payments/member.html', {
        'allocation': allocation, 'order': allocation.order, 'payment': payment,
        'config': config, 'form': form, 'upi_uri': uri, 'ready': ready,
        'can_submit': allocation.member.user_id == request.user.pk and payment.status in ['UNPAID', 'REJECTED'] and ready,
    })

@login_required
@never_cache
@require_GET
def qr(request, pk):
    allocation = _allocation_for_user(pk, request.user)
    config = get_object_or_404(PaymentQRSettings, order=allocation.order)
    # A collector can preview either configured method. Amounts/receiver values
    # in query strings are deliberately never read.
    method = request.GET.get('method', config.preferred_method)
    if method not in ['STATIC', 'DYNAMIC'] or (config.preferred_method == 'NONE' and not is_collector(request.user, allocation.order)):
        raise Http404
    filename = f'nk-{allocation.order.number}-member-{allocation.member_id}.png'
    if method == 'STATIC':
        if not config.static_qr:
            raise Http404
        try:
            response = FileResponse(config.static_qr.open('rb'), content_type='image/png')
        except FileNotFoundError as exc:
            raise Http404 from exc
    else:
        try:
            response = HttpResponse(generate_qr_png(build_upi_uri(allocation)), content_type='image/png')
        except ValidationError:
            return HttpResponse('The collector must configure a valid UPI account first.', status=409, content_type='text/plain')
    if request.GET.get('download') == '1':
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response

@login_required
@never_cache
@require_GET
def static_preview(request, pk):
    order = _collector_order(pk, request.user)
    config = get_object_or_404(PaymentQRSettings, order=order)
    if not config.static_qr:
        raise Http404
    try:
        return FileResponse(config.static_qr.open('rb'), content_type='image/png')
    except FileNotFoundError as exc:
        raise Http404 from exc

@login_required
@never_cache
@require_POST
def review(request, pk):
    payment = get_object_or_404(Payment.objects.select_related('allocation__order__group'), pk=pk)
    require_collector(request.user, payment.allocation.order)
    form = PaymentReviewForm(request.POST)
    if form.is_valid():
        try:
            review_payment(pk, request.user, **form.cleaned_data)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(request, 'Payment review recorded in the audit log.')
    else:
        messages.error(request, ' '.join(message for errors in form.errors.values() for message in errors))
    return redirect('payments:collector', pk=payment.allocation.order_id)
