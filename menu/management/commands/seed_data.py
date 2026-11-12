import json
from datetime import time
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from accounts.models import Profile
from menu.models import Category, MenuItem


class Command(BaseCommand):
    help = "Load the N.K. menu and optional demo accounts; explicitly reconcile source corrections with --menu-only --sync-menu."

    def add_arguments(self, parser):
        parser.add_argument("--menu-only", action="store_true", help="Load menu without creating demo accounts (required when DEBUG=False).")
        parser.add_argument("--sync-menu", action="store_true", help="With --menu-only, apply source names, prices, descriptions and time limits to matching menu rows in place.")

    def load_source_data(self):
        data_path = Path(__file__).resolve().parents[2] / "data" / "menu.json"
        return json.loads(data_path.read_text(encoding="utf-8"))

    @staticmethod
    def matching_record(queryset, source, label):
        """Aliases identify old spellings without replacing IDs referenced by orders."""
        names = {source["name"], *source.get("aliases", [])}
        matches = list(queryset.select_for_update().filter(name__in=names)[:2])
        if len(matches) > 1:
            raise CommandError(f"Ambiguous {label} '{source['name']}': multiple canonical/alias rows exist. Resolve them before synchronizing; no records were changed.")
        return matches[0] if matches else None

    @staticmethod
    def update_fields(instance, values):
        changed = []
        for field, value in values.items():
            if getattr(instance, field) != value:
                setattr(instance, field, value)
                changed.append(field)
        if changed:
            instance.save(update_fields=changed)
        return bool(changed)

    @transaction.atomic
    def handle(self, *args, **options):
        sync_menu = options["sync_menu"]
        if sync_menu and not options["menu_only"]:
            raise CommandError("Use --menu-only with --sync-menu. Menu reconciliation must not create demo accounts or orders.")
        if not settings.DEBUG and not options["menu_only"]:
            raise CommandError("Demo accounts are only available with DEBUG=True. Use --menu-only in production.")
        data = self.load_source_data()
        added = 0
        updated = 0
        seen_categories, seen_items = set(), set()
        featured = {"Shahi Paneer", "Masala Dosa", 'Veggie Blast Pizza 8"', "N.K Special Thali", "Cold Coffee", "Spl. Gulab Jamun 2pc"}
        for index, category_data in enumerate(data):
            category = self.matching_record(Category.objects.all(), category_data, "category")
            slug = slugify(category_data["name"])
            if category is None or sync_menu:
                if Category.objects.filter(slug=slug).exclude(pk=category.pk if category else None).exists():
                    raise CommandError(f"Category slug '{slug}' is already used by a different category. No records were changed.")
            if category is None:
                category = Category.objects.create(name=category_data["name"], slug=slug, sort_order=index)
            elif sync_menu:
                self.update_fields(category, {"name": category_data["name"], "slug": slug, "sort_order": index})
            if category.pk in seen_categories:
                raise CommandError("Multiple source categories resolve to the same category. No records were changed.")
            seen_categories.add(category.pk)
            for item in category_data["items"]:
                record = self.matching_record(MenuItem.objects.filter(category=category), item, "dish")
                values = {"name": item["name"], "price": Decimal(item["price"]) if item["price"] is not None else None, "availability_end": time(17, 0) if item["until_17"] else None}
                # Only supplied descriptions are authoritative. Preserve useful
                # administrator text for dishes with no printed description.
                if "description" in item:
                    values["description"] = item["description"]
                if record is None:
                    record = MenuItem.objects.create(category=category, **values, is_featured=item["name"] in featured)
                    added += 1
                elif sync_menu:
                    updated += int(self.update_fields(record, values))
                if record.pk in seen_items:
                    raise CommandError("Multiple source dishes resolve to the same menu row. No records were changed.")
                seen_items.add(record.pk)
        if not options["menu_only"]:
            User = get_user_model()
            for username, password, full_name in (("admin", "Admin@12345", "N.K. Administrator"), ("rahul", "Rahul@123", "Rahul"), ("aman", "Aman@123", "Aman"), ("rohit", "Rohit@123", "Rohit"), ("karan", "Karan@123", "Karan")):
                user, created = User.objects.get_or_create(username=username, defaults={"first_name": full_name, "email": f"{username}@example.com", "is_staff": username == "admin", "is_superuser": username == "admin"})
                if created:
                    user.set_password(password)
                    user.save()
                Profile.objects.get_or_create(user=user)
            self.seed_demo_order()
            self.stdout.write(self.style.WARNING("Local demo accounts added if missing. Change demo passwords before deployment."))
        if sync_menu:
            self.stdout.write(self.style.SUCCESS(f"Menu synchronized: {len(data)} source categories; {added} dishes added; {updated} dishes corrected in place. Images, enabled flags, accounts and historical bills were preserved."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Menu ready: {len(data)} source categories; {added} dishes added. Existing menu edits and passwords were preserved."))

    def seed_demo_order(self):
        """Build the demo through the same authorization and allocation services as the UI."""
        from groups.models import DiningGroup
        from groups.services import create_group, form_pair, join_group
        from orders.services import add_to_cart, change_allocation, finalize_order, submit_cart

        users = {name: get_user_model().objects.get(username=name) for name in ("rahul", "aman", "rohit", "karan")}
        owner = users["rahul"]
        demo_name = "Lunch at N.K. — sample bill"
        if not DiningGroup.objects.filter(owner=owner, name=demo_name).exists():
            group = create_group(owner=owner, name=demo_name, max_members=4, description="A sample shared lunch: count the rotis, share the curries, and pay your own drinks.")
            for name in ("aman", "rohit", "karan"):
                join_group(user=users[name], code=group.code)
            members = {m.user.username: m for m in group.memberships.select_related("user")}
            form_pair(group=group, actor=owner, member_ids=[members["rahul"].pk, members["aman"].pk], name="Rahul + Aman")
            form_pair(group=group, actor=owner, member_ids=[members["rohit"].pk, members["karan"].pk], name="Rohit + Karan")
            order = group.order
            dishes = {name: MenuItem.objects.get(name=name) for name in ("Tawa Roti", "Shahi Paneer", "Cold Coffee", "Dal Makhani", "Nimbu Paani")}
            for name, quantity in (("Tawa Roti", 9), ("Shahi Paneer", 1), ("Cold Coffee", 1)):
                add_to_cart(group_id=group.pk, actor=owner, menu_item=dishes[name], quantity=quantity)
            submit_cart(order_id=order.pk, actor=owner)
            roti = order.items.get(menu_item=dishes["Tawa Roti"], added_by=owner)
            change_allocation(item_id=roti.pk, actor=owner, quantity=9, mode="QUANTITY", values={members["rahul"].pk: 4, members["aman"].pk: 5})
            for name, quantity in (("Tawa Roti", 5), ("Dal Makhani", 1), ("Nimbu Paani", 2)):
                add_to_cart(group_id=group.pk, actor=users["rohit"], menu_item=dishes[name], quantity=quantity)
            submit_cart(order_id=order.pk, actor=users["rohit"])
            roti = order.items.get(menu_item=dishes["Tawa Roti"], added_by=users["rohit"])
            change_allocation(item_id=roti.pk, actor=owner, quantity=5, mode="QUANTITY", values={members["rohit"].pk: 2, members["karan"].pk: 3})
            drinks = order.items.get(menu_item=dishes["Nimbu Paani"])
            change_allocation(item_id=drinks.pk, actor=owner, quantity=2, mode="QUANTITY", values={members["rohit"].pk: 1, members["karan"].pk: 1})
            finalize_order(order_id=order.pk, actor=owner)
            self.stdout.write("Created sample bill: Rahul INR 175.50, Aman INR 107.50, Rohit INR 73.50, Karan INR 80.50 at supplied menu prices. Collector: Rahul. Enter your own UPI ID before paying.")
        next_name = "Our next lunch — open table"
        if not DiningGroup.objects.filter(owner=owner, name=next_name).exists():
            group = create_group(owner=owner, name=next_name, max_members=4, description="Ready for your next group order. Add food from the menu.")
            for name in ("aman", "rohit", "karan"):
                join_group(user=users[name], code=group.code)

