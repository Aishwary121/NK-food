from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from orders.models import BillAllocation, Order
from .forms import CounterPaymentForm, PaymentClaimForm, PaymentReviewForm, QRSettingsForm
from .models import CounterPayment, Payment, PaymentQRSettings
from .services import build_upi_uri, collection_payments, collector_revision, demo_pay as simulate_payment, demo_payments_enabled, generate_qr_png, get_payment, is_collector, is_host_allocation, member_payment_context, payment_totals, record_counter_payment, require_collector, review_payment, save_qr_settings, submit_payment

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
    totals = payment_totals(order)
    return render(request, 'payments/collector.html', {
        'order': order, 'config': None if demo_payments_enabled() else PaymentQRSettings.objects.filter(order=order).first(),
        'counter_form': CounterPaymentForm(),
        'audit': order.payment_audit.select_related('actor', 'payment__allocation__member__user')[:100],
        'collector_revision': collector_revision(order),
        **totals,
    })


@login_required
@never_cache
@require_GET
def collector_status(request, pk):
    order = _collector_order(pk, request.user)
    return JsonResponse({'revision': collector_revision(order)})

@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def settings_view(request, pk):
    order = _collector_order(pk, request.user)
    if demo_payments_enabled():
        messages.info(request, 'Demo mode is active. UPI setup is not needed; no real money is transferred.')
        return redirect('payments:collector', pk=pk)
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
    preview_allocations = order.allocations.filter(amount__gt=0).select_related('member__user')
    if order.split_policy == 'EQUAL':
        preview_allocations = preview_allocations.exclude(member__user_id=order.group.owner_id)
    preview = preview_allocations.first() if order.status == 'FINALIZED' else None
    locked = collection_payments(order).filter(status__in=['PENDING', 'VERIFIED']).exists()
    return render(request, 'payments/settings.html', {'order': order, 'config': config, 'form': form, 'preview': preview, 'settings_locked': locked})

@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def member(request, pk):
    allocation = _allocation_for_user(pk, request.user)
    demo_mode = demo_payments_enabled()
    if demo_mode and request.method == 'POST':
        messages.info(request, 'Use Pay (demo) to simulate this share. Real payment claims are disabled in demo mode.')
        return redirect('payments:member', pk=pk)
    host_share = is_host_allocation(allocation)
    payment = None if host_share else get_payment(allocation)
    config = None if demo_mode else PaymentQRSettings.objects.filter(order=allocation.order).first()
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
    method = config.preferred_method if config else ''
    if (not host_share and config and config.upi_id and allocation.amount > 0
            and method in ['STATIC', 'DYNAMIC'] and payment.status in ['UNPAID', 'REJECTED']):
        try:
            uri = build_upi_uri(allocation)
        except ValidationError:
            pass
    ready = not host_share and allocation.amount > 0 and bool(config and ((method == 'STATIC' and config.static_qr) or (method == 'DYNAMIC' and uri)))
    action_context = (member_payment_context(allocation.order, request.user)
                      if allocation.member.user_id == request.user.pk else {'payment_action': None})
    return render(request, 'payments/member.html', {
        'allocation': allocation, 'order': allocation.order, 'payment': payment,
        'config': config, 'form': form, 'upi_uri': uri, 'ready': ready,
        'is_host_share': host_share, 'is_host_allocation': host_share,
        'counter_payment': CounterPayment.objects.filter(order=allocation.order).first(),
        **action_context,
        'can_demo_pay': bool(action_context['payment_action'] and action_context['payment_action']['state'] == 'DEMO_READY'),
        'can_submit': not host_share and allocation.member.user_id == request.user.pk and payment.status in ['UNPAID', 'REJECTED'] and ready,
    })

@login_required
@never_cache
@require_GET
def qr(request, pk):
    if demo_payments_enabled():
        raise Http404('Real payment QR codes are disabled in demo mode.')
    allocation = _allocation_for_user(pk, request.user)
    if is_host_allocation(allocation):
        raise Http404('The host pays at the counter; no transfer to themselves is needed.')
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
    if demo_payments_enabled():
        raise Http404('Real payment QR codes are disabled in demo mode.')
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
    if demo_payments_enabled():
        messages.info(request, 'Real payment verification is disabled in demo mode. Members can use Pay (demo).')
        return redirect('payments:collector', pk=payment.allocation.order_id)
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


@login_required
@never_cache
@require_POST
def counter_paid(request, pk):
    order = _collector_order(pk, request.user)
    if demo_payments_enabled():
        try:
            record_counter_payment(order.pk, request.user)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(request, 'Demo counter payment completed. No money was transferred.')
        return redirect('payments:collector', pk=order.pk)
    form = CounterPaymentForm(request.POST)
    if form.is_valid():
        try:
            record = record_counter_payment(order.pk, request.user, form.cleaned_data['reference'])
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            if record.is_demo:
                messages.success(request, 'Demo counter payment completed. No money was transferred.')
            else:
                messages.success(request, 'Full counter payment recorded as declared. Member transfers still require separate verification.')
    else:
        messages.error(request, ' '.join(message for errors in form.errors.values() for message in errors))
    return redirect('payments:collector', pk=order.pk)


@login_required
@never_cache
@require_POST
def demo_pay(request, pk):
    if not demo_payments_enabled():
        raise Http404('Demo payments are disabled.')
    allocation = get_object_or_404(
        BillAllocation.objects.select_related('order__group', 'member'), pk=pk,
        member__user=request.user,
    )
    try:
        payment = simulate_payment(allocation.pk, request.user)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        if payment.status == Payment.Status.VERIFIED:
            messages.info(request, 'This payment was already verified; its recorded status has been preserved.')
        else:
            messages.success(request, 'Demo paid. Your host can see the completed demo payment. No money was transferred.')
    destination = request.POST.get('next', 'member')
    if destination == 'order' or allocation.order.status != Order.Status.FINALIZED:
        return redirect('orders:detail', pk=allocation.order_id)
    if destination == 'group':
        return redirect('groups:detail', pk=allocation.order.group_id)
    return redirect('payments:member', pk=allocation.pk)
