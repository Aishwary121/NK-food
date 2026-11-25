from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from groups.models import DiningGroup
from menu.models import MenuItem
from payments.services import member_payment_context
from .forms import AdjustmentForm, SplitForm
from .models import CartItem, Order, OrderItem
from .services import (add_to_cart, bill_breakdown, change_allocation, finalize_order,
                       kitchen_summary, remove_item, set_adjustments, submit_cart, update_cart)


def accessible_order(user, pk):
    queryset = Order.objects.select_related('group__owner', 'group__collector')
    if not user.is_staff:
        queryset = queryset.filter(group__memberships__user=user).distinct()
    return get_object_or_404(queryset, pk=pk)


def show_error(request, error):
    messages.error(request, '; '.join(error.messages))


def allocation_context(order):
    allocations = list(order.allocations.select_related('member__user')) if order.status == 'FINALIZED' else bill_breakdown(order)
    amounts = [row.amount if order.status == 'FINALIZED' else row['amount'] for row in allocations]
    return {
        'allocations': allocations, 'allocation_count': len(allocations),
        'equal_rounding': order.split_policy == Order.SplitPolicy.EQUAL and len(set(amounts)) > 1,
    }


@login_required
@never_cache
def order_list(request):
    orders = Order.objects.filter(group__memberships__user=request.user).select_related('group').distinct()
    return render(request, 'orders/list.html', {'orders': orders})


@login_required
@never_cache
def detail(request, pk):
    order = accessible_order(request.user, pk)
    items = order.items.select_related('menu_item', 'added_by').prefetch_related('allocations__member__user', 'allocations__member__pair')
    form = AdjustmentForm(initial={'tax': order.tax, 'discount': order.discount})
    return render(request, 'orders/detail.html', {'order': order, 'group': order.group, 'items': items,
                                                  **allocation_context(order), **member_payment_context(order, request.user),
                                                  'kitchen': kitchen_summary(order), 'adjustment_form': form})


@login_required
@never_cache
@require_GET
def payment_status(request, pk):
    """Refresh a member's waiting card without creating a payment claim."""
    order = accessible_order(request.user, pk)
    action = member_payment_context(order, request.user)['payment_action']
    return JsonResponse({
        'state': action['state'] if action else 'NONE',
        'amount': format(action['amount'], '.2f') if action and action['amount'] is not None else None,
        'finalized': order.status == Order.Status.FINALIZED,
        'payment_status': action['status'] if action else None,
    })


@login_required
@never_cache
def cart(request, pk):
    order = accessible_order(request.user, pk)
    member = get_object_or_404(order.group.memberships, user=request.user)
    items = member.cart_items.select_related('menu_item')
    return render(request, 'orders/cart.html', {'order': order, 'items': items,
                                               'total': sum((item.subtotal for item in items), Decimal('0'))})


@login_required
@require_POST
def add(request):
    try:
        group_id = int(request.POST.get('group_id', ''))
        item_id = int(request.POST.get('item_id', ''))
    except (TypeError, ValueError):
        messages.error(request, 'Choose a dining group and a menu item first.')
        return redirect('groups:list')
    group = get_object_or_404(DiningGroup.objects.filter(memberships__user=request.user), pk=group_id)
    item = get_object_or_404(MenuItem, pk=item_id)
    try:
        quantity = int(request.POST.get('quantity', '1'))
        add_to_cart(group_id=group.pk, actor=request.user, menu_item=item, quantity=quantity)
    except ValueError:
        messages.error(request, 'Enter a valid whole-number quantity.')
    except ValidationError as error:
        show_error(request, error)
    else:
        messages.success(request, f'{item.name} added to your personal cart.')
    return redirect('orders:cart', pk=group.order.pk)


@login_required
@never_cache
@require_POST
def cart_update(request, pk):
    wants_json = request.get_preferred_type(['text/html', 'application/json']) == 'application/json'
    try:
        item = get_object_or_404(CartItem.objects.select_related('member__group__order'), pk=pk, member__user=request.user)
    except Http404:
        if wants_json:
            return JsonResponse({'error': 'This item is not available in your cart. Refresh the cart to continue.'}, status=404)
        raise
    order_id = item.member.group.order.pk
    if request.content_type not in ('application/x-www-form-urlencoded', 'multipart/form-data'):
        return JsonResponse({'error': 'Submit the quantity controls as form data.'}, status=415)
    try:
        if 'step' in request.POST:
            steps = request.POST.getlist('step')
            if len(steps) != 1 or steps[0] not in ('1', '-1') or 'quantity' in request.POST:
                raise ValidationError('Choose either an increase or decrease of one portion.')
            result = update_cart(cart_id=item.pk, actor=request.user, step=int(steps[0]))
        else:
            if len(request.POST.getlist('quantity')) != 1:
                raise ValidationError('Enter a valid whole-number quantity.')
            quantity = int(request.POST['quantity'])
            result = update_cart(cart_id=item.pk, actor=request.user, quantity=quantity)
    except ValueError:
        if wants_json:
            return JsonResponse({'error': 'Enter a valid whole-number quantity.'}, status=400)
        messages.error(request, 'Enter a valid whole-number quantity.')
    except ValidationError as error:
        if wants_json:
            return JsonResponse({'error': '; '.join(error.messages)}, status=400)
        show_error(request, error)
    else:
        if wants_json:
            return JsonResponse(result)
    return redirect('orders:cart', pk=order_id)


@login_required
@require_POST
def cart_submit(request, pk):
    order = accessible_order(request.user, pk)
    try:
        submit_cart(order_id=pk, actor=request.user)
    except ValidationError as error:
        show_error(request, error)
        return redirect('orders:cart', pk=pk)
    message = ('Your selection has been added. The whole bill is shared equally by every group member, including the host.'
               if order.split_policy == Order.SplitPolicy.EQUAL else
               'Your selection has been added to the group order. Review how each item is shared.')
    messages.success(request, message)
    return redirect('orders:detail', pk=pk)


@login_required
def edit_split(request, pk):
    item = get_object_or_404(OrderItem.objects.select_related('order__group'), pk=pk)
    accessible_order(request.user, item.order_id)
    form = SplitForm(request.POST or None, item=item)
    if request.method == 'POST' and form.is_valid():
        try:
            change_allocation(item_id=item.pk, actor=request.user, quantity=form.cleaned_data['quantity'],
                              mode=form.cleaned_data['mode'], values=form.cleaned_data['values'])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Quantity updated. The bill remains equally shared by everyone.' if item.order.split_policy == Order.SplitPolicy.EQUAL else 'Sharing updated. Every paise is accounted for.')
            return redirect('orders:detail', pk=item.order_id)
    return render(request, 'orders/split.html', {'form': form, 'item': item})


@login_required
@require_POST
def item_remove(request, pk):
    item = get_object_or_404(OrderItem, pk=pk)
    accessible_order(request.user, item.order_id)
    order_id = item.order_id
    try:
        remove_item(item_id=pk, actor=request.user)
    except ValidationError as error:
        show_error(request, error)
    return redirect('orders:detail', pk=order_id)


@login_required
@require_POST
def adjustments(request, pk):
    order = accessible_order(request.user, pk)
    form = AdjustmentForm(request.POST)
    if form.is_valid():
        try:
            set_adjustments(order_id=pk, actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            show_error(request, error)
        else:
            messages.success(request, 'Tax and discount updated. The grand total is shared equally by everyone.' if order.split_policy == Order.SplitPolicy.EQUAL else 'Tax and discount updated and distributed proportionally.')
    else:
        messages.error(request, 'Enter valid non-negative tax and discount amounts, with up to two decimal places.')
    return redirect('orders:detail', pk=pk)


@login_required
@require_POST
def finalize(request, pk):
    accessible_order(request.user, pk)
    try:
        order = finalize_order(order_id=pk, actor=request.user)
    except ValidationError as error:
        show_error(request, error)
    else:
        messages.success(request, 'Order finalized. Everyone\'s share is now locked.')
        action = member_payment_context(order, request.user)['payment_action']
        if action and not action['setup_ready'] and not settings.PAYMENT_DEMO_MODE:
            messages.info(request, 'Set up the payment collector\'s UPI / QR to let members pay their shares.')
    return redirect('orders:detail', pk=pk)


@login_required
def receipt(request, pk):
    order = accessible_order(request.user, pk)
    return render(request, 'orders/receipt.html', {'order': order, **allocation_context(order), 'kitchen': kitchen_summary(order),
                                                  'items': order.items.prefetch_related('allocations__member__user')})
