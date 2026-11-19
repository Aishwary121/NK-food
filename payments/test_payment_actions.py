from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from groups.models import DiningGroup, Membership
from orders.models import BillAllocation, CartItem, Order
from .models import Payment, PaymentAudit, PaymentQRSettings
from .services import member_payment_context


@override_settings(PAYMENT_DEMO_MODE=False)
class MemberPaymentActionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.host = User.objects.create_user('host', first_name='Aishwary', last_name='Mishra')
        cls.member = User.objects.create_user('member', first_name='Kushagra')
        cls.outsider = User.objects.create_user('outsider')
        cls.admin = User.objects.create_user('admin', is_staff=True)
        cls.group = DiningGroup.objects.create(name='Shared lunch', owner=cls.host, collector=cls.host, status='FINALIZED')
        cls.host_member = Membership.objects.create(group=cls.group, user=cls.host)
        cls.friend_member = Membership.objects.create(group=cls.group, user=cls.member)
        cls.order = Order.objects.create(group=cls.group, status='FINALIZED', subtotal='300.00', total='300.00')
        cls.host_share = BillAllocation.objects.create(order=cls.order, member=cls.host_member, amount='150.00')
        cls.member_share = BillAllocation.objects.create(order=cls.order, member=cls.friend_member, amount='150.00')
        cls.config = PaymentQRSettings.objects.create(order=cls.order, upi_id='aishwary@bank', display_name='Aishwary & Friends', preferred_method='DYNAMIC')

    def action(self, user=None):
        return member_payment_context(self.order, user or self.member)['payment_action']

    def test_ready_link_uses_final_member_amount_and_host_receiver(self):
        action = self.action()
        self.assertEqual(action['state'], 'READY')
        self.assertEqual(action['allocation'].pk, self.member_share.pk)
        self.assertEqual(action['amount'], Decimal('150.00'))
        self.assertEqual(action['receiver'], self.host)
        params = parse_qs(urlparse(action['upi_uri']).query)
        self.assertEqual(params['am'], ['150.00'])
        self.assertEqual(params['pa'], ['aishwary@bank'])
        self.assertEqual(params['pn'], ['Aishwary & Friends'])
        self.assertEqual(params['cu'], ['INR'])
        self.assertIn('%26', action['upi_uri'])
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(PaymentAudit.objects.exists())

    def test_order_and_group_pages_only_expose_logged_in_members_own_action(self):
        self.client.force_login(self.member)
        for name, pk in [('orders:detail', self.order.pk), ('groups:detail', self.group.pk)]:
            response = self.client.get(reverse(name, args=[pk]), {'am': '0.01', 'pa': 'attacker@bank'})
            self.assertEqual(response.status_code, 200)
            action = response.context['payment_action']
            self.assertEqual(action['allocation'].pk, self.member_share.pk)
            self.assertEqual(parse_qs(urlparse(action['upi_uri']).query)['am'], ['150.00'])
            self.assertNotIn('attacker', action['upi_uri'])
            self.assertContains(response, 'Pay ₹150.00 via UPI')
            self.assertContains(response, 'href="upi://pay?')
            self.assertIn('no-store', response['Cache-Control'])
        self.assertFalse(Payment.objects.exists())

    def test_caller_mutated_totals_or_status_do_not_create_draft_link(self):
        Order.objects.filter(pk=self.order.pk).update(status='DRAFT')
        DiningGroup.objects.filter(pk=self.group.pk).update(status='ORDERING')
        self.order.total = Decimal('999.00')
        action = self.action()
        self.assertEqual(action['state'], 'DRAFT')
        self.assertEqual(action['amount'], Decimal('150.00'))
        self.assertEqual(action['upi_uri'], '')
        self.assertIsNone(action['allocation'])

    def test_draft_explains_finalization_and_personal_cart_blockers(self):
        from menu.models import Category, MenuItem
        Order.objects.filter(pk=self.order.pk).update(status='DRAFT')
        DiningGroup.objects.filter(pk=self.group.pk).update(status='ORDERING')
        category = Category.objects.create(name='Lunch', slug='action-lunch')
        food = MenuItem.objects.create(category=category, name='Lunch plate', price='150.00')
        CartItem.objects.create(member=self.friend_member, menu_item=food)
        host = self.action(self.host)
        member = self.action()
        self.assertEqual(host['state'], 'DRAFT')
        self.assertTrue(host['is_host'])
        self.assertTrue(host['can_finalize'])
        self.assertTrue(host['setup_ready'])
        self.assertEqual(host['cart_item_count'], 1)
        self.assertEqual(host['own_cart_item_count'], 0)
        self.assertEqual(member['own_cart_item_count'], 1)
        self.assertFalse(member['can_finalize'])
        self.assertEqual(member['upi_uri'], '')
        self.client.force_login(self.host)
        response = self.client.get(reverse('orders:detail', args=[self.order.pk]))
        self.assertContains(response, 'Finalize &amp; enable payments')
        self.assertContains(response, '#finalize-order')
        self.assertNotContains(response, 'href="upi://pay?')
        self.client.force_login(self.member)
        response = self.client.get(reverse('orders:detail', args=[self.order.pk]))
        self.assertContains(response, 'Waiting for the host to finalize')
        self.assertNotContains(response, 'Finalize &amp; enable payments')

    def test_host_has_collection_action_and_no_transfer_to_self(self):
        action = self.action(self.host)
        self.assertEqual(action['state'], 'HOST')
        self.assertTrue(action['can_manage'])
        self.assertEqual(action['amount'], Decimal('150.00'))
        self.assertEqual(action['upi_uri'], '')
        self.assertFalse(Payment.objects.exists())

    def test_outsider_or_staff_without_membership_does_not_get_member_pay_action(self):
        for user in [self.outsider, self.admin]:
            self.assertIsNone(self.action(user))
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('orders:detail', args=[self.order.pk])).status_code, 404)

    def test_missing_receiver_setup_has_no_deep_link(self):
        self.config.delete()
        action = self.action()
        self.assertEqual(action['state'], 'SETUP_REQUIRED')
        self.assertFalse(action['setup_ready'])
        self.assertEqual(action['upi_uri'], '')

    def test_disabled_collection_suppresses_uri_even_if_upi_id_remains(self):
        self.config.preferred_method = 'NONE'
        self.config.save()
        action = self.action()
        self.assertEqual(action['state'], 'SETUP_REQUIRED')
        self.assertEqual(action['upi_uri'], '')
        self.client.force_login(self.member)
        response = self.client.get(reverse('payments:member', args=[self.member_share.pk]))
        self.assertEqual(response.context['upi_uri'], '')
        self.assertFalse(response.context['ready'])

    def test_static_only_qr_does_not_invent_deep_link(self):
        self.config.preferred_method = 'STATIC'
        self.config.upi_id = ''
        self.config.static_qr = 'payment_qr/example.png'
        self.config.save()
        action = self.action()
        self.assertEqual(action['state'], 'STATIC_ONLY')
        self.assertTrue(action['setup_ready'])
        self.assertEqual(action['upi_uri'], '')
        self.config.upi_id = 'aishwary@bank'
        self.config.save()
        self.assertEqual(self.action()['state'], 'READY')

    def test_pending_or_verified_member_is_never_prompted_to_pay_again(self):
        payment = Payment.objects.create(allocation=self.member_share, status='PENDING')
        self.client.force_login(self.member)
        for state in ['PENDING', 'VERIFIED']:
            payment.status = state
            if state == 'VERIFIED':
                from django.utils import timezone
                payment.reviewed_by, payment.reviewed_at = self.host, timezone.now()
            payment.save()
            action = self.action()
            self.assertEqual(action['state'], state)
            self.assertEqual(action['upi_uri'], '')
            response = self.client.get(reverse('payments:member', args=[self.member_share.pk]))
            self.assertEqual(response.context['upi_uri'], '')
            self.assertFalse(response.context['can_submit'])
            self.assertNotContains(response, 'href="upi://pay?')
            response = self.client.get(reverse('orders:detail', args=[self.order.pk]))
            self.assertNotContains(response, 'href="upi://pay?')
        self.assertFalse(PaymentAudit.objects.exists())

    def test_rejected_payment_can_retry_same_authoritative_amount(self):
        payment = Payment.objects.create(allocation=self.member_share, status='REJECTED', rejection_reason='Receipt not found')
        action = self.action()
        self.assertEqual(action['state'], 'READY')
        self.assertEqual(action['status'], 'REJECTED')
        self.assertEqual(action['payment'], payment)
        self.assertEqual(parse_qs(urlparse(action['upi_uri']).query)['am'], ['150.00'])
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'REJECTED')

    def test_zero_share_and_cancelled_group_have_no_upi_action(self):
        BillAllocation.objects.filter(pk=self.member_share.pk).update(amount='0.00')
        self.assertEqual(self.action()['state'], 'ZERO')
        self.assertEqual(self.action()['upi_uri'], '')
        DiningGroup.objects.filter(pk=self.group.pk).update(status='CANCELLED')
        self.assertEqual(self.action()['state'], 'CANCELLED')
        self.assertEqual(self.action()['upi_uri'], '')

    def test_legacy_itemized_owner_allocation_keeps_original_payment_behavior(self):
        Order.objects.filter(pk=self.order.pk).update(split_policy='ITEMIZED')
        action = self.action(self.host)
        self.assertEqual(action['state'], 'READY')
        self.assertFalse(action['is_host'])
        self.assertEqual(action['allocation'].pk, self.host_share.pk)

    def test_waiting_status_refresh_is_private_authorized_get_only(self):
        url = reverse('orders:payment_status', args=[self.order.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(url).status_code, 405)
        response = self.client.get(url)
        self.assertEqual(response.json(), {'state': 'READY', 'amount': '150.00', 'finalized': True, 'payment_status': 'UNPAID'})
        self.assertIn('no-store', response['Cache-Control'])
        self.assertFalse(Payment.objects.exists())
        Order.objects.filter(pk=self.order.pk).update(status='DRAFT')
        DiningGroup.objects.filter(pk=self.group.pk).update(status='ORDERING')
        self.assertEqual(self.client.get(url).json()['state'], 'DRAFT')
