from datetime import datetime, time
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Category, MenuItem


class MenuTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Paneer", slug="paneer")
        cls.drinks = Category.objects.create(name="Drinks", slug="drinks")
        cls.paneer = MenuItem.objects.create(category=cls.category, name="Shahi Paneer", price=Decimal("145.00"))
        cls.tea = MenuItem.objects.create(category=cls.drinks, name="Tea", price=Decimal("15.00"))

    def test_menu_search_and_category_filter(self):
        response = self.client.get(reverse("menu:list"), {"category": "paneer"})
        self.assertContains(response, "Shahi Paneer")
        self.assertNotContains(response, ">Tea<")
        response = self.client.get(reverse("menu:list"), {"q": "tea"})
        self.assertEqual(list(response.context["items"]), [self.tea])
        self.assertEqual(self.paneer.price, Decimal("145.00"))

    def test_unpriced_unavailable_and_time_limited_items(self):
        self.paneer.price = None
        self.assertFalse(self.paneer.available_now)
        self.paneer.price = Decimal("145.00")
        self.paneer.is_available = False
        self.assertFalse(self.paneer.available_now)
        self.paneer.is_available = True
        self.paneer.availability_end = time(17)
        with patch("menu.models.timezone.localtime", return_value=datetime(2026, 11, 2, 16, 59, tzinfo=ZoneInfo("Asia/Kolkata"))):
            self.assertTrue(self.paneer.available_now)
        with patch("menu.models.timezone.localtime", return_value=datetime(2026, 11, 2, 17, 0, tzinfo=ZoneInfo("Asia/Kolkata"))):
            self.assertFalse(self.paneer.available_now)

    @override_settings(DEBUG=True)
    def test_seed_is_complete_idempotent_and_preserves_admin_edits(self):
        MenuItem.objects.all().delete()
        Category.objects.all().delete()
        call_command("seed_data", stdout=StringIO())
        self.assertEqual(Category.objects.count(), 19)
        self.assertEqual(MenuItem.objects.count(), 185)
        self.assertIsNone(MenuItem.objects.get(name="Ice Cream").price)
        self.assertEqual(MenuItem.objects.get(name="Plain Prantha").availability_end, time(17))
        from groups.models import DiningGroup
        demo = DiningGroup.objects.get(name="Lunch at N.K. — sample bill")
        allocations = {allocation.member.user.username: allocation.amount for allocation in demo.order.allocations.select_related("member__user")}
        self.assertEqual(allocations, {"rahul": Decimal("175.50"), "aman": Decimal("107.50"), "rohit": Decimal("73.50"), "karan": Decimal("80.50")})
        self.assertEqual(demo.order.total, Decimal("437.00"))
        self.assertEqual(demo.pairs.count(), 2)
        tea = MenuItem.objects.get(name="Special Tea")
        tea.price = Decimal("18.00")
        tea.save()
        rahul = get_user_model().objects.get(username="rahul")
        rahul.set_password("ChangedPassword!123")
        rahul.save()
        call_command("seed_data", stdout=StringIO())
        tea.refresh_from_db()
        rahul.refresh_from_db()
        self.assertEqual(MenuItem.objects.count(), 185)
        self.assertEqual(DiningGroup.objects.count(), 2)
        self.assertEqual(tea.price, Decimal("18.00"))
        self.assertTrue(rahul.check_password("ChangedPassword!123"))

    @override_settings(DEBUG=False)
    def test_production_seed_requires_menu_only(self):
        with self.assertRaises(CommandError):
            call_command("seed_data", stdout=StringIO())
        call_command("seed_data", menu_only=True, stdout=StringIO())
        self.assertEqual(get_user_model().objects.count(), 0)


class MenuSyncTests(TestCase):
    """Source corrections must not replace rows referenced by existing bills."""

    source_data = [{
        "name": "TAWA PRANTHA (Till 5PM)",
        "aliases": ["TAWA PARANTHA (Till 5PM)"],
        "items": [
            {"name": "Plain Prantha", "aliases": ["Plain Parantha"], "price": "25.00", "until_17": True},
            {"name": "Paneer Prantha With Butter", "aliases": ["Paneer Parantha With Butter"], "price": "55.00", "until_17": True, "description": "Printed description from the source menu."},
        ],
    }]

    def setUp(self):
        self.source_patch = patch("menu.management.commands.seed_data.Command.load_source_data", return_value=self.source_data)
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.category = Category.objects.create(name="TAWA PARANTHA (Till 5PM)", slug="admin-custom-category", sort_order=99)
        self.plain = MenuItem.objects.create(category=self.category, name="Plain Parantha", price="19.00", description="Preserve this administrator note.", availability_start=time(9), availability_end=time(15), is_available=False, image="menu/my-dish.png")
        self.paneer = MenuItem.objects.create(category=self.category, name="Paneer Parantha With Butter", price="50.00", description="Old thali or dish description.")

    def run_seed(self, sync=True):
        call_command("seed_data", menu_only=True, sync_menu=sync, stdout=StringIO())

    @override_settings(DEBUG=True)
    def test_sync_requires_explicit_menu_only(self):
        with self.assertRaisesMessage(CommandError, "Use --menu-only"):
            call_command("seed_data", sync_menu=True, stdout=StringIO())
        self.assertEqual(get_user_model().objects.count(), 0)
        self.plain.refresh_from_db()
        self.assertEqual(self.plain.price, Decimal("19.00"))

    def test_default_seed_recognizes_aliases_and_preserves_admin_changes(self):
        self.run_seed(sync=False)
        self.category.refresh_from_db()
        self.plain.refresh_from_db()
        self.paneer.refresh_from_db()
        self.assertEqual(Category.objects.count(), 1)
        self.assertEqual(MenuItem.objects.count(), 2)
        self.assertEqual(self.category.name, "TAWA PARANTHA (Till 5PM)")
        self.assertEqual(self.category.slug, "admin-custom-category")
        self.assertEqual(self.plain.name, "Plain Parantha")
        self.assertEqual(self.plain.price, Decimal("19.00"))
        self.assertEqual(self.plain.availability_end, time(15))
        self.assertFalse(self.plain.is_available)
        self.assertEqual(self.paneer.description, "Old thali or dish description.")

    def test_sync_updates_source_fields_in_place_and_is_idempotent(self):
        original_ids = set(MenuItem.objects.values_list("pk", flat=True))
        self.run_seed()
        self.run_seed()
        self.category.refresh_from_db()
        self.plain.refresh_from_db()
        self.paneer.refresh_from_db()
        self.assertEqual(set(MenuItem.objects.values_list("pk", flat=True)), original_ids)
        self.assertEqual(Category.objects.count(), 1)
        self.assertEqual(self.category.name, "TAWA PRANTHA (Till 5PM)")
        self.assertEqual(self.category.slug, "tawa-prantha-till-5pm")
        self.assertEqual(self.category.sort_order, 0)
        self.assertEqual(self.plain.name, "Plain Prantha")
        self.assertEqual(self.plain.price, Decimal("25.00"))
        self.assertEqual(self.plain.availability_end, time(17))
        self.assertEqual(self.plain.availability_start, time(9))
        self.assertEqual(self.plain.image.name, "menu/my-dish.png")
        self.assertFalse(self.plain.is_available)
        self.assertEqual(self.plain.description, "Preserve this administrator note.")
        self.assertEqual(self.paneer.description, "Printed description from the source menu.")
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_sync_preserves_historical_order_allocation_and_verified_payment(self):
        from groups.models import DiningGroup, Membership
        from orders.models import BillAllocation, ItemAllocation, Order, OrderItem
        from payments.models import Payment, PaymentAudit

        owner = get_user_model().objects.create_user("collector", password="AlreadyChanged!321")
        password_hash = owner.password
        group = DiningGroup.objects.create(name="Earlier lunch", owner=owner, collector=owner, status="FINALIZED")
        member = Membership.objects.create(group=group, user=owner)
        order = Order.objects.create(group=group, status="FINALIZED", subtotal="38.00", total="38.00")
        item = OrderItem.objects.create(order=order, menu_item=self.plain, added_by=owner, item_name="Plain Parantha", quantity=2, unit_price="19.00")
        item_allocation = ItemAllocation.objects.create(order_item=item, member=member, amount="38.00")
        allocation = BillAllocation.objects.create(order=order, member=member, amount="38.00")
        payment = Payment.objects.create(allocation=allocation, status="VERIFIED", reviewed_by=owner, reference="EXISTING-TRANSFER")
        event = PaymentAudit.objects.create(order=order, payment=payment, actor=owner, action="PAYMENT_REVIEWED", old_status="PENDING", new_status="VERIFIED")

        self.run_seed()

        for record in [owner, order, item, item_allocation, allocation, payment]:
            record.refresh_from_db()
        self.assertEqual(item.menu_item_id, self.plain.pk)
        self.assertEqual(item.item_name, "Plain Parantha")
        self.assertEqual(item.unit_price, Decimal("19.00"))
        self.assertEqual(item.subtotal, Decimal("38.00"))
        self.assertEqual(item_allocation.amount, Decimal("38.00"))
        self.assertEqual(allocation.amount, Decimal("38.00"))
        self.assertEqual(order.total, Decimal("38.00"))
        self.assertEqual(order.status, "FINALIZED")
        self.assertEqual(payment.status, "VERIFIED")
        self.assertEqual(payment.reference, "EXISTING-TRANSFER")
        self.assertTrue(PaymentAudit.objects.filter(pk=event.pk, payment=payment, new_status="VERIFIED").exists())
        self.assertEqual(owner.password, password_hash)

    def test_conflicting_dish_aliases_roll_back_earlier_corrections(self):
        duplicate = MenuItem.objects.create(category=self.category, name="Paneer Prantha With Butter", price="65.00")
        with self.assertRaisesMessage(CommandError, "Ambiguous dish"):
            self.run_seed()
        self.category.refresh_from_db()
        self.plain.refresh_from_db()
        self.assertEqual(self.category.name, "TAWA PARANTHA (Till 5PM)")
        self.assertEqual(self.category.slug, "admin-custom-category")
        self.assertEqual(self.plain.name, "Plain Parantha")
        self.assertEqual(self.plain.price, Decimal("19.00"))
        self.assertTrue(MenuItem.objects.filter(pk=duplicate.pk).exists())
        self.assertEqual(MenuItem.objects.count(), 3)

    def test_conflicting_category_aliases_are_not_merged(self):
        canonical = Category.objects.create(name="TAWA PRANTHA (Till 5PM)", slug="canonical-existing")
        with self.assertRaisesMessage(CommandError, "Ambiguous category"):
            self.run_seed()
        self.assertTrue(Category.objects.filter(pk=canonical.pk).exists())
        self.assertEqual(Category.objects.count(), 2)
        self.plain.refresh_from_db()
        self.assertEqual(self.plain.category_id, self.category.pk)

    def test_unrelated_category_slug_conflict_is_reported_without_updates(self):
        Category.objects.create(name="Administrator's other category", slug="tawa-prantha-till-5pm")
        with self.assertRaisesMessage(CommandError, "already used"):
            self.run_seed()
        self.category.refresh_from_db()
        self.assertEqual(self.category.slug, "admin-custom-category")


class DiningJourneyTests(TestCase):
    """Exercise real page URLs across the menu, group, order, and payment apps."""

    def test_two_members_order_split_and_verify_payment_through_forms(self):
        from groups.models import DiningGroup
        from orders.models import CartItem
        from payments.models import Payment

        owner = get_user_model().objects.create_user("rahul", first_name="Rahul")
        guest = get_user_model().objects.create_user("aman", first_name="Aman")
        category = Category.objects.create(name="Lunch", slug="lunch")
        roti = MenuItem.objects.create(category=category, name="Tawa Roti", price=Decimal("7.00"))
        paneer = MenuItem.objects.create(category=category, name="Shahi Paneer", price=Decimal("145.00"))
        coffee = MenuItem.objects.create(category=category, name="Cold Coffee", price=Decimal("75.00"))
        self.client.force_login(owner)
        response = self.client.post(reverse("groups:create"), {"name": "Friends at lunch", "max_members": 2, "description": "Shared lunch"})
        self.assertEqual(response.status_code, 302)
        group = DiningGroup.objects.get(name="Friends at lunch")
        self.client.force_login(guest)
        self.assertRedirects(self.client.post(reverse("groups:join"), {"code": group.code}), reverse("groups:detail", args=[group.pk]))
        self.client.force_login(owner)
        members = {m.user_id: m for m in group.memberships.all()}
        self.client.post(reverse("groups:pair", args=[group.pk]), {"name": "Lunch pair", "members": [members[owner.pk].pk, members[guest.pk].pk]})
        self.assertEqual(group.pairs.count(), 1)
        response = self.client.get(reverse("menu:list"), {"group": group.pk})
        self.assertContains(response, "Add to cart")
        self.assertEqual(response.context["active_group"], group)
        for dish, quantity in ((roti, 9), (paneer, 1), (coffee, 1)):
            response = self.client.post(reverse("orders:add"), {"group_id": group.pk, "item_id": dish.pk, "quantity": quantity})
            self.assertRedirects(response, reverse("orders:cart", args=[group.order.pk]))
        self.assertEqual(CartItem.objects.count(), 3)
        self.assertRedirects(self.client.post(reverse("orders:submit_cart", args=[group.order.pk])), reverse("orders:detail", args=[group.order.pk]))
        item = group.order.items.get(menu_item=roti)
        response = self.client.post(reverse("orders:split", args=[item.pk]), {"quantity": 9, "mode": "QUANTITY", "members": [members[owner.pk].pk, members[guest.pk].pk], f"value_{members[owner.pk].pk}": 4, f"value_{members[guest.pk].pk}": 5})
        self.assertRedirects(response, reverse("orders:detail", args=[group.order.pk]))
        shared_item = group.order.items.get(menu_item=paneer)
        response = self.client.post(reverse("orders:split", args=[shared_item.pk]), {"quantity": 1, "mode": "EQUAL", "members": [members[owner.pk].pk, members[guest.pk].pk]})
        self.assertRedirects(response, reverse("orders:detail", args=[group.order.pk]))
        self.assertRedirects(self.client.post(reverse("orders:finalize", args=[group.order.pk])), reverse("orders:detail", args=[group.order.pk]))
        allocation = group.order.allocations.get(member=members[guest.pk])
        self.assertEqual(allocation.amount, Decimal("107.50"))
        self.assertEqual(group.order.allocations.get(member=members[owner.pk]).amount, Decimal("175.50"))
        response = self.client.post(reverse("payments:settings", args=[group.order.pk]), {"upi_id": "collector@example", "display_name": "Rahul", "preferred_method": "DYNAMIC"})
        self.assertRedirects(response, reverse("payments:settings", args=[group.order.pk]))
        self.client.force_login(guest)
        response = self.client.get(reverse("payments:member", args=[allocation.pk]))
        self.assertContains(response, "107.50")
        self.assertContains(response, "Open UPI App")
        qr_response = self.client.get(reverse("payments:qr", args=[allocation.pk]))
        self.assertEqual(qr_response.status_code, 200)
        self.assertTrue(qr_response.content.startswith(b"\x89PNG"))
        response = self.client.post(reverse("payments:member", args=[allocation.pk]), {"reference": "DEMO-REFERENCE"})
        self.assertRedirects(response, reverse("payments:member", args=[allocation.pk]))
        payment = Payment.objects.get(allocation=allocation)
        self.assertEqual(payment.status, "PENDING")
        self.client.force_login(owner)
        response = self.client.post(reverse("payments:review", args=[payment.pk]), {"decision": "VERIFIED", "reason": "Receipt checked in bank app"})
        self.assertRedirects(response, reverse("payments:collector", args=[group.order.pk]))
        payment.refresh_from_db()
        self.assertEqual(payment.status, "VERIFIED")
        self.assertContains(self.client.get(reverse("orders:receipt", args=[group.order.pk])), "283.00")
