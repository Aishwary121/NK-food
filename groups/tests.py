from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch
from payments.models import Payment, PaymentAudit, PaymentQRSettings
from payments.storage import private_qr_storage
from orders.models import BillAllocation, Order
from .models import Membership
from .services import cancel_group, complete_group, create_group, form_pair, join_group, leave_group, set_collector


class GroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user('organizer', password='Group-test-123')
        cls.friend = User.objects.create_user('friend', password='Group-test-123')
        cls.third = User.objects.create_user('third', password='Group-test-123')
        cls.outsider = User.objects.create_user('outsider', password='Group-test-123')
        cls.group = create_group(owner=cls.owner, name='Lunch club', max_members=4)
        # Historical item-based orders retain their original collector workflow.
        Order.objects.filter(group=cls.group).update(split_policy=Order.SplitPolicy.ITEMIZED)
        cls.group.order.refresh_from_db()
        cls.owner_member = cls.group.memberships.get(user=cls.owner)
        cls.friend_member = join_group(user=cls.friend, code=cls.group.code)
        cls.third_member = join_group(user=cls.third, code=cls.group.code)

    def test_creation_sets_owner_collector_and_order(self):
        self.assertEqual(self.group.owner, self.owner)
        self.assertEqual(self.group.collector, self.owner)
        self.assertEqual(self.group.order.status, 'DRAFT')
        self.assertEqual(len(self.group.code), 8)

    def test_duplicate_join_is_idempotent_and_database_enforced(self):
        self.assertEqual(join_group(user=self.friend, code=self.group.code.lower()).pk, self.friend_member.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(group=self.group, user=self.friend)

    def test_capacity_and_invalid_code(self):
        self.group.max_members = 3
        self.group.save()
        with self.assertRaises(ValidationError):
            join_group(user=self.outsider, code=self.group.code)
        with self.assertRaises(ValidationError):
            join_group(user=self.outsider, code='INVALID1')

    def test_pair_creation_and_reassignment(self):
        original = form_pair(group=self.group, actor=self.owner, member_ids=[self.owner_member.pk, self.friend_member.pk], name='Pair one')
        new = form_pair(group=self.group, actor=self.owner, member_ids=[self.owner_member.pk, self.third_member.pk], name='Pair two')
        self.friend_member.refresh_from_db()
        self.owner_member.refresh_from_db()
        self.assertIsNone(self.friend_member.pair_id)
        self.assertEqual(self.owner_member.pair_id, new.pk)
        self.assertFalse(self.group.pairs.filter(pk=original.pk).exists())

    def test_only_owner_forms_pairs(self):
        with self.assertRaises(PermissionDenied):
            form_pair(group=self.group, actor=self.friend, member_ids=[self.owner_member.pk, self.friend_member.pk], name='Bad pair')

    def test_cross_group_and_duplicate_pair_members_rejected(self):
        other = create_group(owner=self.outsider, name='Other table')
        foreign = other.memberships.get()
        for ids in ([self.owner_member.pk, foreign.pk], [self.owner_member.pk, self.owner_member.pk]):
            with self.assertRaises(ValidationError):
                form_pair(group=self.group, actor=self.owner, member_ids=ids, name='Bad pair')

    def test_nonmembers_cannot_view_group_or_invitation_qr(self):
        self.client.force_login(self.outsider)
        for name in ('groups:detail', 'groups:join_qr'):
            self.assertEqual(self.client.get(reverse(name, args=[self.group.pk])).status_code, 404)

    def test_join_qr_is_png_and_read_does_not_join(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('groups:join_qr', args=[self.group.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertTrue(response.content.startswith(b'\x89PNG'))
        self.assertEqual(self.group.memberships.count(), 3)

    def test_member_can_leave_open_group(self):
        leave_group(group=self.group, user=self.friend)
        self.assertFalse(self.group.memberships.filter(user=self.friend).exists())

    def test_owner_and_collector_cannot_leave(self):
        set_collector(group=self.group, actor=self.owner, membership=self.friend_member)
        for user in (self.owner, self.friend):
            with self.assertRaises(ValidationError):
                leave_group(group=self.group, user=user)

    def test_finalized_group_blocks_membership_pair_and_collector_changes(self):
        self.group.status = 'FINALIZED'
        self.group.save()
        actions = [
            lambda: join_group(user=self.outsider, code=self.group.code),
            lambda: leave_group(group=self.group, user=self.friend),
            lambda: set_collector(group=self.group, actor=self.owner, membership=self.friend_member),
            lambda: form_pair(group=self.group, actor=self.owner, member_ids=[self.owner_member.pk, self.friend_member.pk], name='Late pair'),
        ]
        for action in actions:
            with self.assertRaises(ValidationError):
                action()

    def test_group_pages_render_for_members(self):
        self.client.force_login(self.owner)
        for name, args in [('groups:list', []), ('groups:create', []), ('groups:join', []), ('groups:detail', [self.group.pk])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_mutations_require_post_and_authentication(self):
        self.assertEqual(self.client.get(reverse('groups:detail', args=[self.group.pk])).status_code, 302)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('groups:pair', args=[self.group.pk])).status_code, 405)

    def test_collector_change_clears_old_receiver_and_private_image(self):
        PaymentQRSettings.objects.create(order=self.group.order, upi_id='oldcollector@upi', display_name='Old collector',
                                          preferred_method='STATIC', static_qr='payment_qr/old.png')
        with patch.object(private_qr_storage, 'delete') as delete:
            with self.captureOnCommitCallbacks(execute=True):
                set_collector(group=self.group, actor=self.owner, membership=self.friend_member)
                delete.assert_not_called()
            delete.assert_called_once_with('payment_qr/old.png')
        self.group.refresh_from_db()
        self.assertEqual(self.group.collector_id, self.friend.pk)
        self.assertFalse(PaymentQRSettings.objects.filter(order=self.group.order).exists())
        self.assertTrue(PaymentAudit.objects.filter(order=self.group.order, action='COLLECTOR_CHANGED').exists())

    def test_same_collector_preserves_settings_and_nonowner_cannot_change(self):
        config = PaymentQRSettings.objects.create(order=self.group.order, upi_id='owner@upi', display_name='Owner')
        set_collector(group=self.group, actor=self.owner, membership=self.owner_member)
        self.assertTrue(PaymentQRSettings.objects.filter(pk=config.pk).exists())
        with self.assertRaises(PermissionDenied):
            set_collector(group=self.group, actor=self.friend, membership=self.friend_member)

    def test_collector_membership_is_reloaded_not_trusted_from_caller(self):
        self.friend_member.user = self.outsider
        set_collector(group=self.group, actor=self.owner, membership=self.friend_member)
        self.group.refresh_from_db()
        self.assertEqual(self.group.collector_id, self.friend.pk)

    def test_cancellation_preserves_history_and_prevents_new_orders(self):
        with self.assertRaises(PermissionDenied):
            cancel_group(group=self.group, actor=self.friend)
        cancel_group(group=self.group, actor=self.owner)
        self.group.refresh_from_db()
        self.assertEqual(self.group.status, 'CANCELLED')
        self.assertTrue(self.group.order.allocation_history.filter(action='GROUP_CANCELLED').exists())
        with self.assertRaises(ValidationError):
            join_group(user=self.outsider, code=self.group.code)
        self.client.force_login(self.owner)
        response = self.client.get(reverse('orders:detail', args=[self.group.order.pk]))
        self.assertContains(response, 'Cancelled')
        self.assertNotContains(response, 'Finalize group order')

    def test_completion_requires_all_positive_allocations_verified(self):
        order = self.group.order
        order.status = 'FINALIZED'
        order.save()
        self.group.status = 'FINALIZED'
        self.group.save()
        allocation = BillAllocation.objects.create(order=order, member=self.friend_member, amount='25.00')
        BillAllocation.objects.create(order=order, member=self.owner_member, amount=0)
        with self.assertRaises(ValidationError):
            complete_group(group=self.group, actor=self.owner)
        payment = Payment.objects.create(allocation=allocation, status='PENDING')
        with self.assertRaises(ValidationError):
            complete_group(group=self.group, actor=self.owner)
        with self.assertRaises(PermissionDenied):
            complete_group(group=self.group, actor=self.third)
        payment.status = 'VERIFIED'
        payment.save()
        complete_group(group=self.group, actor=self.owner)
        self.group.refresh_from_db()
        self.assertEqual(self.group.status, 'COMPLETED')
        self.assertTrue(order.payment_audit.filter(action='GROUP_COMPLETED').exists())
        with self.assertRaises(ValidationError):
            cancel_group(group=self.group, actor=self.owner)


class EqualGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.host = User.objects.create_user('equal-host')
        cls.friend = User.objects.create_user('equal-friend')
        cls.group = create_group(owner=cls.host, name='Equal lunch')
        cls.friend_member = join_group(user=cls.friend, code=cls.group.code)

    def test_new_group_splits_equally_and_host_remains_collector(self):
        self.assertEqual(self.group.order.split_policy, Order.SplitPolicy.EQUAL)
        self.assertEqual(self.group.collector_id, self.host.pk)
        with self.assertRaises(ValidationError):
            set_collector(group=self.group, actor=self.host, membership=self.friend_member)
        self.group.refresh_from_db()
        self.assertEqual(self.group.collector_id, self.host.pk)

    def test_equal_member_can_leave_shared_items_added_by_host(self):
        from decimal import Decimal
        from menu.models import Category, MenuItem
        from orders.services import add_to_cart, bill_breakdown, submit_cart
        category = Category.objects.create(name='Group lunch', slug='equal-group-lunch')
        food = MenuItem.objects.create(category=category, name='Lunch', price='300.00')
        add_to_cart(group_id=self.group.pk, actor=self.host, menu_item=food)
        order = submit_cart(order_id=self.group.order.pk, actor=self.host)
        self.assertEqual([row['amount'] for row in bill_breakdown(order)], [Decimal('150.00'), Decimal('150.00')])
        leave_group(group=self.group, user=self.friend)
        self.assertEqual([row['amount'] for row in bill_breakdown(order)], [Decimal('300.00')])
        self.assertEqual(order.items.get().allocations.count(), 1)
        join_group(user=self.friend, code=self.group.code)
        self.assertEqual([row['amount'] for row in bill_breakdown(order)], [Decimal('150.00'), Decimal('150.00')])
        self.assertEqual(order.items.get().allocations.count(), 2)

    def test_equal_collector_endpoint_cannot_redirect_receipts_to_another_member(self):
        self.client.force_login(self.host)
        response = self.client.post(reverse('groups:collector', args=[self.group.pk]), {'collector': self.friend_member.pk})
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.collector_id, self.host.pk)
