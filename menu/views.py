from django.db.models import Q
from django.shortcuts import render

from .models import Category, MenuItem
from .restaurant import restaurant_details
from .photos import photo_credits, stock_photo


def home(request):
    featured = MenuItem.objects.filter(is_featured=True, is_available=True).select_related("category")[:6]
    recent_items = []
    if request.user.is_authenticated:
        from orders.models import OrderItem
        recent_ids = list(dict.fromkeys(OrderItem.objects.filter(added_by=request.user).order_by("-pk").values_list("menu_item_id", flat=True)[:30]))[:3]
        recent_lookup = MenuItem.objects.select_related("category").in_bulk(recent_ids)
        recent_items = [recent_lookup[item_id] for item_id in recent_ids if item_id in recent_lookup]
    return render(request, "menu/home.html", {"featured": featured, "categories": Category.objects.all(), "dish_count": MenuItem.objects.count(), "recent_items": recent_items, "restaurant": restaurant_details(), "hero_photo": stock_photo("N.K Special Thali")})


def menu_list(request):
    items = MenuItem.objects.select_related("category").all()
    search = request.GET.get("q", "").strip()[:100]
    category = request.GET.get("category", "")[:100]
    if search:
        items = items.filter(Q(name__icontains=search) | Q(description__icontains=search))
    if category:
        items = items.filter(category__slug=category)
    memberships = []
    active_group = None
    if request.user.is_authenticated:
        from groups.models import Membership
        memberships = Membership.objects.filter(user=request.user, group__status__in=("OPEN", "ORDERING"), group__order__isnull=False).select_related("group__order")
        requested_group = request.GET.get("group", "")
        if requested_group.isdigit():
            active_group = next((m.group for m in memberships if m.group_id == int(requested_group)), None)
        if active_group is None and len(memberships) == 1:
            active_group = memberships[0].group
    return render(request, "menu/list.html", {"items": items, "categories": Category.objects.all(), "search": search, "selected_category": category, "memberships": memberships, "active_group": active_group, "restaurant": restaurant_details()})


def photography(request):
    return render(request, 'menu/photography.html', {'credits': photo_credits()})
