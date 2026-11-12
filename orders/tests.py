from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from groups.services import create_group, form_pair, join_group
from menu.models import Category, MenuItem
from .models import BillAllocation, CartItem
from .services import (add_to_cart, bill_breakdown, calculate_split, change_allocation,
                       distribute, finalize_order, kitchen_summary, remove_item,
                       set_adjustments, submit_cart, update_cart)


class SplitEngineTests(SimpleTestCase):
    def test_equal_split_preserves_total_and_deterministic_paise(self):
        result = calculate_split(unit_price='1', quantity=1, mode='EQUAL', values={3: 1, 1: 1, 2: 1})
        self.assertEqual(result, {1: Decimal('0.34'), 2: Decimal('0.33'), 3: Decimal('0.33')})
        self.assertEqual(sum(result.values()), Decimal('1.00'))

    def test_nine_rotis_split_four_and_five(self):
        self.assertEqual(calculate_split(unit_price='7', quantity=9, mode='QUANTITY', values={1: 4, 2: 5}),
                         {1: Decimal('28.00'), 2: Decimal('35.00')})

    def test_individual_and_custom(self):
        self.assertEqual(calculate_split(unit_price='75', quantity=1, mode='INDIVIDUAL', values={8: 1}), {8: Decimal('75.00')})
        self.assertEqual(calculate_split(unit_price='200', quantity=1, mode='CUSTOM', values={1: 120, 2: 80}),
                         {1: Decimal('120.00'), 2: Decimal('80.00')})

    def test_percent_rounding_and_total(self):
        result = calculate_split(unit_price='10.01', quantity=1, mode='PERCENT', values={1: 33, 2: 33, 3: 34})
        self.assertEqual(sum(result.values()), Decimal('10.01'))
        self.assertEqual(result[3], Decimal('3.41'))

    def test_invalid_split_inputs(self):
        cases = [
            ('QUANTITY', {1: 4, 2: 4}), ('QUANTITY', {1: '4.5', 2: '4.5'}),
            ('CUSTOM', {1: 20, 2: 40}), ('CUSTOM', {1: '-1', 2: 64}),
            ('PERCENT', {1: 40, 2: 50}), ('PERCENT', {1: 'NaN'}),
            ('INDIVIDUAL', {1: 1, 2: 1}), ('EQUAL', {}), ('WRONG', {1: 1}),
        ]
        for mode, values in cases:
            with self.subTest(mode=mode, values=values), self.assertRaises(ValidationError):
                calculate_split(unit_price=7, quantity=9, mode=mode, values=values)

    def test_quantities_and_amounts_are_finite_positive_and_bounded(self):
        for quantity in (-1, 0, 1.5, 1000, True):
            with self.assertRaises(ValidationError):
                calculate_split(unit_price=7, quantity=quantity, mode='EQUAL', values={1: 1})
        for value in ('Infinity', 'NaN', '-1', '0.001'):
            with self.assertRaises(ValidationError):
                distribute(value, {1: 1})


class OrderFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user('order-owner', password='Order-test-123')
        cls.friend = User.objects.create_user('order-friend', password='Order-test-123')
        cls.third = User.objects.create_user('order-third', password='Order-test-123')
        cls.outsider = User.objects.create_user('order-outsider', password='Order-test-123')
        cls.group = create_group(owner=cls.owner, name='Order table')
        cls.owner_member = cls.group.memberships.get(user=cls.owner)
        cls.friend_member = join_group(user=cls.friend, code=cls.group.code)
        cls.third_member = join_group(user=cls.third, code=cls.group.code)
        form_pair(group=cls.group, actor=cls.owner, member_ids=[cls.owner_member.pk, cls.friend_member.pk], name='First pair')
        cls.order = cls.group.order
        category = Category.objects.create(name='Indian breads', slug='test-breads')
        cls.roti = MenuItem.objects.create(category=category, name='Tawa Roti', price='7.00')
        category = Category.objects.create(name='Paneer Special', slug='test-paneer')
        cls.paneer = MenuItem.objects.create(category=category, name='Shahi Paneer', price='140.00')

    def add_roti(self, actor=None, quantity=9):
        actor = actor or self.owner
        add_to_cart(group_id=self.group.pk, actor=actor, menu_item=self.roti, quantity=quantity)
        submit_cart(order_id=self.order.pk, actor=actor)
        return self.order.items.filter(added_by=actor).latest('pk')

    def test_cart_quantity_subtotal_and_submission(self):
        cart = add_to_cart(group_id=self.group.pk, actor=self.owner, menu_item=self.roti, quantity=4)
        add_to_cart(group_id=self.group.pk, actor=self.owner, menu_item=self.roti, quantity=5)
        cart.refresh_from_db()
        self.assertEqual(cart.quantity, 9)
        self.assertEqual(cart.subtotal, Decimal('63.00'))
        order = submit_cart(order_id=self.order.pk, actor=self.owner)
        self.assertEqual(order.total, Decimal('63.00'))
        self.assertFalse(CartItem.objects.exists())

    def test_unavailable_and_invalid_quantity_rejected(self):
        self.roti.is_available = False
        self.roti.save()
        with self.assertRaises(ValidationError):
            add_to_cart(group_id=self.group.pk, actor=self.owner, menu_item=self.roti)
        self.roti.is_available = True
        for quantity in (-1, 0, 1000):
            with self.assertRaises(ValidationError):
                add_to_cart(group_id=self.group.pk, actor=self.owner, menu_item=self.roti, quantity=quantity)

    def test_cart_cannot_be_edited_by_another_member(self):
        cart = add_to_cart(group_id=self.group.pk, actor=self.owner, menu_item=self.roti)
        with self.assertRaises(PermissionDenied):
            update_cart(cart_id=cart.pk, actor=self.friend, quantity=2)

    def test_shared_dishes_default_to_pair_equal_split(self):
        add_to_cart(group_id=self.group.pk, actor=self.owner, menu_item=self.paneer)
        submit_cart(order_id=self.order.pk, actor=self.owner)
        item = self.order.items.get()
        self.assertEqual(item.split_mode, 'EQUAL')
        self.assertEqual(list(item.allocations.values_list('amount', flat=True)), [Decimal('70'), Decimal('70')])

    def test_kitchen_consolidates_while_allocations_stay_distinct(self):
        self.add_roti(quantity=4)
        self.add_roti(actor=self.friend, quantity=5)
        summary = list(kitchen_summary(self.order))
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]['quantity'], 9)
        self.assertEqual(self.order.items.count(), 2)

    def test_change_quantity_custom_share_and_history(self):
        item = self.add_roti()
        change_allocation(item_id=item.pk, actor=self.owner, quantity=9, mode='QUANTITY',
                          values={self.owner_member.pk: 4, self.friend_member.pk: 5})
        self.assertEqual(dict(item.allocations.values_list('member_id', 'amount')),
                         {self.owner_member.pk: Decimal('28'), self.friend_member.pk: Decimal('35')})
        audit = self.order.allocation_history.filter(action='SPLIT_CHANGED').get()
        self.assertEqual(audit.before['mode'], 'INDIVIDUAL')
        self.assertEqual(audit.after['mode'], 'QUANTITY')

    def test_cross_group_allocation_and_nonowner_outside_pair_rejected(self):
        item = self.add_roti(actor=self.friend)
        other = create_group(owner=self.outsider, name='Other')
        foreign = other.memberships.get()
        with self.assertRaises(ValidationError):
            change_allocation(item_id=item.pk, actor=self.owner, quantity=9, mode='EQUAL', values={foreign.pk: 1})
        with self.assertRaises(PermissionDenied):
            change_allocation(item_id=item.pk, actor=self.friend, quantity=9, mode='EQUAL', values={self.third_member.pk: 1})

    def test_other_member_cannot_modify_or_remove_another_item(self):
        item = self.add_roti()
        with self.assertRaises(PermissionDenied):
            change_allocation(item_id=item.pk, actor=self.friend, quantity=1, mode='INDIVIDUAL', values={self.friend_member.pk: 1})
        with self.assertRaises(PermissionDenied):
            remove_item(item_id=item.pk, actor=self.friend)

    def test_owner_can_share_whole_group_and_adjustments_preserve_total(self):
        item = self.add_roti(quantity=1)
        values = {member.pk: 1 for member in self.group.memberships.all()}
        change_allocation(item_id=item.pk, actor=self.owner, quantity=1, mode='EQUAL', values=values)
        set_adjustments(order_id=self.order.pk, actor=self.owner, tax='1.01', discount='0.50')
        self.order.refresh_from_db()
        rows = bill_breakdown(self.order)
        self.assertEqual(sum(row['amount'] for row in rows), Decimal('7.51'))

    def test_finalization_snapshots_amounts_and_locks_all_mutations(self):
        item = self.add_roti()
        change_allocation(item_id=item.pk, actor=self.owner, quantity=9, mode='QUANTITY', values={self.owner_member.pk: 4, self.friend_member.pk: 5})
        order = finalize_order(order_id=self.order.pk, actor=self.owner)
        self.assertEqual(order.status, 'FINALIZED')
        self.assertEqual(sum(order.allocations.values_list('amount', flat=True)), Decimal('63.00'))
        self.assertEqual(order.allocations.get(member=self.friend_member).amount, Decimal('35.00'))
        self.roti.price = '9.00'
        self.roti.save()
        self.assertEqual(order.allocations.get(member=self.friend_member).amount, Decimal('35.00'))
        actions = [
            lambda: finalize_order(order_id=order.pk, actor=self.owner),
            lambda: remove_item(item_id=item.pk, actor=self.owner),
            lambda: change_allocation(item_id=item.pk, actor=self.owner, quantity=1, mode='EQUAL', values={self.owner_member.pk: 1}),
            lambda: add_to_cart(group_id=self.group.pk, actor=self.owner, menu_item=self.roti),
            lambda: set_adjustments(order_id=order.pk, actor=self.owner, tax=0, discount=0),
        ]
        for action in actions:
            with self.assertRaises(ValidationError):
                action()

    def test_only_owner_can_finalize_and_unsubmitted_carts_block(self):
        self.add_roti()
        with self.assertRaises(PermissionDenied):
            finalize_order(order_id=self.order.pk, actor=self.friend)
        add_to_cart(group_id=self.group.pk, actor=self.friend, menu_item=self.roti)
        with self.assertRaises(ValidationError):
            finalize_order(order_id=self.order.pk, actor=self.owner)
        self.assertFalse(BillAllocation.objects.exists())

    def test_pages_render_and_foreign_orders_hidden(self):
        item = self.add_roti()
        self.client.force_login(self.owner)
        for name, pk in [('orders:detail', self.order.pk), ('orders:cart', self.order.pk), ('orders:receipt', self.order.pk), ('orders:split', item.pk)]:
            self.assertEqual(self.client.get(reverse(name, args=[pk])).status_code, 200)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('orders:detail', args=[self.order.pk])).status_code, 404)

    def test_finalized_bill_page_renders_payment_links(self):
        self.add_roti()
        finalize_order(order_id=self.order.pk, actor=self.owner)
        self.client.force_login(self.owner)
        response = self.client.get(reverse('orders:detail', args=[self.order.pk]))
        self.assertContains(response, 'Payment details')
        self.assertContains(response, 'Collector dashboard')

    def test_existing_final_allocations_block_duplicate_finalization(self):
        self.add_roti()
        BillAllocation.objects.create(order=self.order, member=self.owner_member, amount='63.00')
        with self.assertRaises(ValidationError):
            finalize_order(order_id=self.order.pk, actor=self.owner)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'DRAFT')
        self.assertEqual(self.order.allocations.count(), 1)

    def test_four_member_two_pair_workflow_exact_bill(self):
        fourth_member = join_group(user=self.outsider, code=self.group.code)
        form_pair(group=self.group, actor=self.owner, member_ids=[self.third_member.pk, fourth_member.pk], name='Second pair')
        self.paneer.price = Decimal('145.00')
        self.paneer.save()
        drinks = Category.objects.create(name='Drinks', slug='test-drinks')
        coffee = MenuItem.objects.create(category=drinks, name='Cold Coffee', price='75.00')
        nimbu = MenuItem.objects.create(category=drinks, name='Nimbu Paani', price='27.00')
        main = Category.objects.create(name='Main Course — Dal & Sabzi', slug='test-main')
        dal = MenuItem.objects.create(category=main, name='Dal Makhani', price='65.00')
        for food, quantity in [(self.roti, 9), (self.paneer, 1), (coffee, 1)]:
            add_to_cart(group_id=self.group.pk, actor=self.owner, menu_item=food, quantity=quantity)
        submit_cart(order_id=self.order.pk, actor=self.owner)
        roti_one = self.order.items.get(menu_item=self.roti, added_by=self.owner)
        change_allocation(item_id=roti_one.pk, actor=self.owner, quantity=9, mode='QUANTITY',
                          values={self.owner_member.pk: 4, self.friend_member.pk: 5})
        for food, quantity in [(self.roti, 5), (dal, 1), (nimbu, 2)]:
            add_to_cart(group_id=self.group.pk, actor=self.third, menu_item=food, quantity=quantity)
        submit_cart(order_id=self.order.pk, actor=self.third)
        roti_two = self.order.items.get(menu_item=self.roti, added_by=self.third)
        change_allocation(item_id=roti_two.pk, actor=self.owner, quantity=5, mode='QUANTITY',
                          values={self.third_member.pk: 2, fourth_member.pk: 3})
        lemonade = self.order.items.get(menu_item=nimbu)
        change_allocation(item_id=lemonade.pk, actor=self.owner, quantity=2, mode='QUANTITY',
                          values={self.third_member.pk: 1, fourth_member.pk: 1})
        final = finalize_order(order_id=self.order.pk, actor=self.owner)
        self.assertEqual(final.total, Decimal('437.00'))
        self.assertEqual(dict(final.allocations.values_list('member_id', 'amount')), {
            self.owner_member.pk: Decimal('175.50'), self.friend_member.pk: Decimal('107.50'),
            self.third_member.pk: Decimal('73.50'), fourth_member.pk: Decimal('80.50'),
        })
        kitchen = {row['menu_item_id']: row['quantity'] for row in kitchen_summary(final)}
        self.assertEqual(kitchen[self.roti.pk], 14)
        self.assertEqual(kitchen[nimbu.pk], 2)
        for user in (self.owner, self.friend, self.third, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('orders:detail', args=[final.pk])).status_code, 200)
            self.assertEqual(self.client.get(reverse('orders:receipt', args=[final.pk])).status_code, 200)
