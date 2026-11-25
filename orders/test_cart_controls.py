from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from groups.models import DiningGroup
from groups.services import create_group, join_group
from menu.models import Category, MenuItem
from . import services
from .models import CartItem, Order


class CartQuantityControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user('quantity-owner')
        cls.friend = User.objects.create_user('quantity-friend')
        cls.outsider = User.objects.create_user('quantity-outsider', is_staff=True)
        cls.group = create_group(owner=cls.owner, name='Quantity controls')
        cls.order = cls.group.order
        join_group(user=cls.friend, code=cls.group.code)
        cls.member = cls.group.memberships.get(user=cls.owner)
        category = Category.objects.create(name='Quantity test dishes', slug='quantity-test')
        cls.food = MenuItem.objects.create(category=category, name='Quantity roti', price='9.50')
        cls.second = MenuItem.objects.create(category=category, name='Quantity dal', price='45.25')
        cls.cart = services.add_to_cart(group_id=cls.group.pk, actor=cls.owner, menu_item=cls.food, quantity=2)
        cls.other_cart = services.add_to_cart(group_id=cls.group.pk, actor=cls.owner, menu_item=cls.second)
        services.add_to_cart(group_id=cls.group.pk, actor=cls.friend, menu_item=cls.food, quantity=10)

    def setUp(self):
        self.client.force_login(self.owner)
        self.url = reverse('orders:update_cart', args=[self.cart.pk])

    def post_json(self, data, url=None):
        return self.client.post(url or self.url, data, HTTP_ACCEPT='application/json')

    def test_increment_returns_server_prices_and_only_personal_cart_total(self):
        response = self.post_json({'step': '1', 'subtotal': '0.01', 'total': '0.01', 'price': '0.01'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'quantity': 3, 'subtotal': '28.50', 'total': '73.75', 'removed': False,
            'item_count': 2, 'total_quantity': 4, 'can_increase': True,
        })
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.quantity, 3)
        self.order.refresh_from_db()
        self.assertEqual(self.order.total, Decimal('0.00'))
        self.assertIn('no-store', response['Cache-Control'])

    def test_decrement_last_portion_removes_row_and_final_row_empties_cart(self):
        CartItem.objects.filter(pk=self.cart.pk).update(quantity=1)
        response = self.post_json({'step': '-1'})
        self.assertEqual(response.json(), {
            'quantity': 0, 'subtotal': '0.00', 'total': '45.25', 'removed': True,
            'item_count': 1, 'total_quantity': 1, 'can_increase': False,
        })
        self.assertFalse(CartItem.objects.filter(pk=self.cart.pk).exists())
        response = self.post_json({'step': '-1'}, reverse('orders:update_cart', args=[self.other_cart.pk]))
        self.assertEqual(response.json()['total'], '0.00')
        self.assertEqual(response.json()['item_count'], 0)
        self.assertEqual(response.json()['total_quantity'], 0)
        self.assertFalse(CartItem.objects.filter(member=self.member).exists())

    def test_step_reads_latest_quantity_after_shared_order_lock(self):
        actual_lock = services.locked_order

        def changed_before_lock_acquired(order_id):
            result = actual_lock(order_id)
            # Models another request completing while this request waited.
            CartItem.objects.filter(pk=self.cart.pk).update(quantity=4)
            return result

        with patch('orders.services.locked_order', side_effect=changed_before_lock_acquired):
            result = services.update_cart(cart_id=self.cart.pk, actor=self.owner, step=1)
        self.assertEqual(result['quantity'], 5)
        self.assertEqual(result['subtotal'], '47.50')
        self.assertEqual(result['total'], '92.75')

    def test_unavailable_item_can_be_decreased_and_removed_but_not_increased(self):
        MenuItem.objects.filter(pk=self.food.pk).update(is_available=False)
        response = self.post_json({'step': '1'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('unavailable', response.json()['error'])
        response = self.post_json({'step': '-1'})
        self.assertEqual(response.json()['quantity'], 1)
        self.assertFalse(response.json()['can_increase'])
        self.assertTrue(self.post_json({'step': '-1'}).json()['removed'])

    def test_missing_price_does_not_block_removing_unavailable_item(self):
        MenuItem.objects.filter(pk=self.food.pk).update(price=None)
        self.assertEqual(self.post_json({'step': '1'}).status_code, 400)
        response = self.post_json({'step': '-1'})
        self.assertEqual(response.json()['quantity'], 1)
        self.assertEqual(response.json()['subtotal'], '0.00')
        self.assertEqual(response.json()['total'], '45.25')
        self.assertTrue(self.post_json({'step': '-1'}).json()['removed'])

    def test_upper_quantity_boundary_rejects_increment_without_changing_cart(self):
        CartItem.objects.filter(pk=self.cart.pk).update(quantity=998)
        response = self.post_json({'step': '1'})
        self.assertEqual(response.json()['quantity'], 999)
        self.assertFalse(response.json()['can_increase'])
        response = self.post_json({'step': '1'})
        self.assertEqual(response.status_code, 400)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.quantity, 999)
        self.assertEqual(self.post_json({'step': '-1'}).json()['quantity'], 998)

    def test_native_forms_and_legacy_absolute_quantities_still_redirect(self):
        response = self.client.post(self.url, {'step': '1'})
        self.assertRedirects(response, reverse('orders:cart', args=[self.order.pk]))
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.quantity, 3)
        response = self.client.post(self.url, {'quantity': '7'})
        self.assertRedirects(response, reverse('orders:cart', args=[self.order.pk]))
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.quantity, 7)
        response = self.post_json({'quantity': '0'})
        self.assertTrue(response.json()['removed'])

    def test_malformed_ambiguous_or_duplicate_values_do_not_change_quantity(self):
        for data in [
            {}, {'step': '0'}, {'step': '2'}, {'step': '-2'}, {'step': '1.0'},
            {'step': '+1'}, {'step': '1', 'quantity': '999'},
            {'step': ['1', '-1']}, {'quantity': ['1', '5']},
            {'quantity': 'NaN'}, {'quantity': '-1'}, {'quantity': '1000'},
        ]:
            with self.subTest(data=data):
                self.assertEqual(self.post_json(data).status_code, 400)
                self.cart.refresh_from_db()
                self.assertEqual(self.cart.quantity, 2)

    def test_json_request_body_is_rejected_before_any_mutation(self):
        response = self.client.post(self.url, {'step': -1}, content_type='application/json', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 415)
        self.assertIn('error', response.json())
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.quantity, 2)

    def test_other_member_and_staff_cannot_edit_another_persons_cart(self):
        for actor in [self.friend, self.outsider]:
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                self.assertEqual(self.post_json({'step': '-1'}).status_code, 404)
                with self.assertRaises(PermissionDenied):
                    services.update_cart(cart_id=self.cart.pk, actor=actor, step=1)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.quantity, 2)

    def test_closed_group_or_finalized_order_rejects_all_quantity_changes(self):
        for group_status, order_status in [
            (DiningGroup.Status.CANCELLED, Order.Status.DRAFT),
            (DiningGroup.Status.FINALIZED, Order.Status.FINALIZED),
            (DiningGroup.Status.ORDERING, Order.Status.FINALIZED),
        ]:
            with self.subTest(group=group_status, order=order_status):
                DiningGroup.objects.filter(pk=self.group.pk).update(status=group_status)
                Order.objects.filter(pk=self.order.pk).update(status=order_status)
                self.assertEqual(self.post_json({'step': '-1'}).status_code, 400)
                self.assertEqual(self.post_json({'quantity': '0'}).status_code, 400)
                self.cart.refresh_from_db()
                self.assertEqual(self.cart.quantity, 2)

    def test_missing_row_after_submission_has_safe_json_error(self):
        self.cart.delete()
        response = self.post_json({'step': '-1'})
        self.assertEqual(response.status_code, 404)
        self.assertIn('error', response.json())

    def test_service_rejects_boolean_and_non_integer_controls(self):
        for kwargs in [{'step': True}, {'step': 1.0}, {'step': '1'}, {'step': 0}, {'quantity': True}, {'quantity': 1.5}, {'step': 1, 'quantity': 2}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                services.update_cart(cart_id=self.cart.pk, actor=self.owner, **kwargs)

    def test_authentication_post_and_csrf_guards_remain_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.post_json({'step': '-1'}).status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        response = csrf_client.post(self.url, {'step': '-1'}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 403)
        csrf_client.get(reverse('orders:cart', args=[self.order.pk]))
        token = csrf_client.cookies['csrftoken'].value
        response = csrf_client.post(self.url, {'step': '-1'}, HTTP_ACCEPT='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['quantity'], 1)
