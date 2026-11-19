from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from groups.models import DiningGroup, Membership
from groups.services import complete_group
from orders.models import BillAllocation, Order
from .models import CounterPayment, Payment, PaymentAudit, PaymentQRSettings
from .services import demo_pay, member_payment_context, payment_totals, record_counter_payment


@override_settings(PAYMENT_DEMO_MODE=True)
class DemoPaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.host = User.objects.create_user('demo-host', first_name='Aishwary', is_staff=True)
        cls.friend = User.objects.create_user('demo-member', first_name='Kushagra')
        cls.outsider = User.objects.create_user('demo-outsider')
        cls.group = DiningGroup.objects.create(name='Demo lunch', owner=cls.host, collector=cls.host, status='FINALIZED')
        host_member = Membership.objects.create(group=cls.group, user=cls.host)
        friend_member = Membership.objects.create(group=cls.group, user=cls.friend)
        cls.order = Order.objects.create(group=cls.group, status='FINALIZED', subtotal='300.00', total='300.00')
        cls.host_share = BillAllocation.objects.create(order=cls.order, member=host_member, amount='150.00')
        cls.share = BillAllocation.objects.create(order=cls.order, member=friend_member, amount='150.00')

    def pay_url(self):
        return reverse('payments:demo_pay', args=[self.share.pk])

    def test_one_click_needs_no_upi_and_does_not_verify_real_money(self):
        self.client.force_login(self.friend)
        response = self.client.post(self.pay_url(), {'amount': '0.01', 'status': 'VERIFIED', 'pa': 'untrusted@bank'})
        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.get(allocation=self.share)
        self.assertEqual(payment.status, 'DEMO_PAID')
        self.assertIsNone(payment.reviewed_by)
        self.share.refresh_from_db()
        self.assertEqual(self.share.amount, Decimal('150.00'))
        self.assertFalse(PaymentQRSettings.objects.exists())
        audit = PaymentAudit.objects.get(payment=payment)
        self.assertEqual(audit.actor, self.friend)
        self.assertEqual(audit.new_status, 'DEMO_PAID')
        totals = payment_totals(self.order)
        self.assertEqual(totals['received'], Decimal('0.00'))
        self.assertEqual(totals['demo_amount'], Decimal('150.00'))
        self.assertEqual(totals['demo_count'], 1)

    def test_duplicate_click_is_idempotent_and_audited_once(self):
        first = demo_pay(self.share.pk, self.friend)
        second = demo_pay(self.share.pk, self.friend)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PaymentAudit.objects.filter(payment=first, new_status='DEMO_PAID').count(), 1)

    def test_get_and_missing_csrf_cannot_pay(self):
        self.client.force_login(self.friend)
        self.assertEqual(self.client.get(self.pay_url()).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.friend)
        self.assertEqual(strict.post(self.pay_url()).status_code, 403)
        self.assertFalse(Payment.objects.exists())

    def test_other_members_admin_and_host_cannot_pay_someone_elses_share(self):
        for actor in [self.outsider, self.host]:
            with self.assertRaises(PermissionDenied):
                demo_pay(self.share.pk, actor)
        with self.assertRaises(ValidationError):
            demo_pay(self.host_share.pk, self.host)
        self.assertFalse(Payment.objects.exists())

    def test_draft_cancelled_and_zero_allocations_cannot_be_paid(self):
        Order.objects.filter(pk=self.order.pk).update(status='DRAFT')
        with self.assertRaises(ValidationError):
            demo_pay(self.share.pk, self.friend)
        Order.objects.filter(pk=self.order.pk).update(status='FINALIZED')
        DiningGroup.objects.filter(pk=self.group.pk).update(status='CANCELLED')
        with self.assertRaises(ValidationError):
            demo_pay(self.share.pk, self.friend)
        DiningGroup.objects.filter(pk=self.group.pk).update(status='FINALIZED')
        BillAllocation.objects.filter(pk=self.share.pk).update(amount=0)
        with self.assertRaises(ValidationError):
            demo_pay(self.share.pk, self.friend)
        self.assertFalse(Payment.objects.exists())

    def test_pending_and_rejected_can_be_simulated_but_verified_is_preserved(self):
        payment = Payment.objects.create(allocation=self.share, status='PENDING', reference='Existing reference')
        demo_pay(self.share.pk, self.friend)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'DEMO_PAID')
        payment.status = 'REJECTED'
        payment.save()
        demo_pay(self.share.pk, self.friend)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'DEMO_PAID')
        payment.status = 'VERIFIED'
        payment.reviewed_by = self.host
        payment.save()
        audits = PaymentAudit.objects.count()
        demo_pay(self.share.pk, self.friend)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'VERIFIED')
        self.assertEqual(payment.reviewed_by, self.host)
        self.assertEqual(PaymentAudit.objects.count(), audits)

    def test_member_pages_have_demo_pay_without_any_upi_links(self):
        self.client.force_login(self.friend)
        for route, pk in [('orders:detail', self.order.pk), ('groups:detail', self.group.pk), ('payments:member', self.share.pk)]:
            response = self.client.get(reverse(route, args=[pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.pay_url())
            self.assertContains(response, '150.00')
            self.assertNotContains(response, 'href="upi://')
            self.assertNotContains(response, 'Open UPI App')
            self.assertNotContains(response, 'I Have Paid')
        self.assertEqual(member_payment_context(self.order, self.friend)['payment_action']['state'], 'DEMO_READY')
        demo_pay(self.share.pk, self.friend)
        action = member_payment_context(self.order, self.friend)['payment_action']
        self.assertEqual(action['state'], 'DEMO_PAID')
        response = self.client.get(reverse('orders:detail', args=[self.order.pk]))
        self.assertContains(response, 'Demo paid')
        self.assertNotContains(response, self.pay_url())

    def test_host_sees_member_demo_paid_immediately(self):
        demo_pay(self.share.pk, self.friend)
        self.client.force_login(self.host)
        response = self.client.get(reverse('payments:collector', args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kushagra')
        self.assertContains(response, 'Demo paid')
        self.assertEqual(response.context['demo_count'], 1)
        self.assertEqual(response.context['received'], 0)

    def test_host_status_poll_changes_after_payment_and_is_read_only(self):
        self.client.force_login(self.host)
        url = reverse('payments:collector_status', args=[self.order.pk])
        before = self.client.get(url)
        self.assertEqual(before.status_code, 200)
        self.assertIn('no-store', before['Cache-Control'])
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(PaymentAudit.objects.exists())
        demo_pay(self.share.pk, self.friend)
        after = self.client.get(url)
        self.assertNotEqual(before.json()['revision'], after.json()['revision'])
        self.assertEqual(len(after.json()['revision']), 64)
        self.assertEqual(PaymentAudit.objects.count(), 1)

    def test_collector_poll_and_demo_post_are_private(self):
        url = reverse('payments:collector_status', args=[self.order.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(self.pay_url()).status_code, 302)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(self.pay_url()).status_code, 404)
        self.client.force_login(self.friend)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertFalse(Payment.objects.exists())

    def test_real_payment_endpoints_are_disabled_in_demo_mode(self):
        self.client.force_login(self.host)
        response = self.client.post(reverse('payments:settings', args=[self.order.pk]), {
            'upi_id': 'host@bank', 'display_name': 'Host', 'preferred_method': 'DYNAMIC'})
        self.assertIn(response.status_code, [302, 404])
        self.assertFalse(PaymentQRSettings.objects.exists())
        self.client.force_login(self.friend)
        self.assertEqual(self.client.get(reverse('payments:qr', args=[self.share.pk])).status_code, 404)

    def test_counter_demo_does_not_require_real_paid_confirmation(self):
        self.client.force_login(self.host)
        response = self.client.post(reverse('payments:counter_paid', args=[self.order.pk]), {'amount': '0.01'})
        self.assertEqual(response.status_code, 302)
        counter = CounterPayment.objects.get(order=self.order)
        self.assertTrue(counter.is_demo)
        self.assertEqual(counter.amount, Decimal('300.00'))

    def test_group_can_complete_after_demo_member_and_counter_payments(self):
        demo_pay(self.share.pk, self.friend)
        with self.assertRaises(ValidationError):
            complete_group(group=self.group, actor=self.host)
        record_counter_payment(self.order.pk, self.host)
        complete_group(group=self.group, actor=self.host)
        self.group.refresh_from_db()
        self.assertEqual(self.group.status, 'COMPLETED')

    def test_demo_records_cannot_complete_group_with_demo_mode_disabled(self):
        demo_pay(self.share.pk, self.friend)
        record_counter_payment(self.order.pk, self.host)
        with override_settings(PAYMENT_DEMO_MODE=False):
            with self.assertRaises(ValidationError):
                complete_group(group=self.group, actor=self.host)
            CounterPayment.objects.filter(order=self.order).update(is_demo=False)
            with self.assertRaises(ValidationError):
                complete_group(group=self.group, actor=self.host)

    @override_settings(PAYMENT_DEMO_MODE=False)
    def test_demo_payment_disabled_outside_demo_mode(self):
        with self.assertRaises(ValidationError):
            demo_pay(self.share.pk, self.friend)
        self.client.force_login(self.friend)
        self.assertEqual(self.client.post(self.pay_url()).status_code, 404)
        self.assertFalse(Payment.objects.exists())

    def test_staff_dashboard_separates_simulated_and_real_money(self):
        demo_pay(self.share.pk, self.friend)
        record_counter_payment(self.order.pk, self.host)
        self.client.force_login(self.host)
        response = self.client.get(reverse('dashboard:home'))
        self.assertEqual(response.context['demo_total'], Decimal('150.00'))
        self.assertEqual(response.context['demo_counter_total'], Decimal('300.00'))
        self.assertEqual(response.context['verified_total'], 0)
        self.assertEqual(response.context['counter_declared_total'], 0)
        self.assertEqual(response.context['pending_total'], 0)
