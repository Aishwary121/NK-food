from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import F, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from groups.models import DiningGroup
from menu.models import MenuItem
from orders.models import BillAllocation, Order, OrderItem
from payments.models import CounterPayment, Payment


@login_required
def home(request):
    """Operational reporting is restricted to staff; all totals come from the ledger."""
    if not request.user.is_staff:
        raise PermissionDenied

    finalized = Order.objects.filter(status=Order.Status.FINALIZED)
    zero = Decimal("0.00")
    sales_total = finalized.aggregate(value=Sum("total"))["value"] or zero
    reimbursements = Payment.objects.exclude(
        allocation__order__split_policy=Order.SplitPolicy.EQUAL,
        allocation__member__user_id=F('allocation__order__group__owner_id'),
    )
    verified_total = reimbursements.filter(status="VERIFIED").aggregate(
        value=Sum("allocation__amount")
    )["value"] or zero
    allocated_total = BillAllocation.objects.filter(order__status="FINALIZED").exclude(
        order__split_policy=Order.SplitPolicy.EQUAL,
        member__user_id=F('order__group__owner_id'),
    ).aggregate(
        value=Sum("amount")
    )["value"] or zero
    awaiting_verification = reimbursements.filter(status="PENDING").count()
    counter_declared_total = CounterPayment.objects.aggregate(value=Sum('amount'))['value'] or zero

    today = timezone.localdate()
    first_day = today - timedelta(days=6)
    daily_values = dict(
        finalized.filter(finalized_at__date__gte=first_day)
        .annotate(day=TruncDate("finalized_at"))
        .values("day")
        .annotate(value=Sum("total"))
        .values_list("day", "value")
    )
    maximum = max(daily_values.values(), default=Decimal("1.00")) or Decimal("1.00")
    chart_days = []
    for offset in range(7):
        day = first_day + timedelta(days=offset)
        amount = daily_values.get(day, zero)
        chart_days.append({"date": day, "amount": amount, "width": int(amount / maximum * 100)})

    popular_items = (
        OrderItem.objects.filter(order__status="FINALIZED")
        .values("menu_item_id", "item_name")
        .annotate(quantity_ordered=Sum("quantity"))
        .order_by("-quantity_ordered", "item_name")[:5]
    )
    context = {
        "sales_total": sales_total,
        "verified_total": verified_total,
        "pending_total": allocated_total - verified_total,
        "awaiting_verification": awaiting_verification,
        "counter_declared_total": counter_declared_total,
        "order_count": finalized.count(),
        "active_groups": DiningGroup.objects.filter(status__in=["OPEN", "ORDERING"]).count(),
        "menu_count": MenuItem.objects.count(),
        "available_menu_count": MenuItem.objects.filter(is_available=True, price__isnull=False).count(),
        "recent_orders": Order.objects.select_related("group", "group__collector").order_by("-created_at")[:8],
        "popular_items": popular_items,
        "chart_days": chart_days,
        "today": today,
    }
    return render(request, "dashboard/home.html", context)
