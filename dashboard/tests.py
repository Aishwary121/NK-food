from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class DashboardPermissionsTests(TestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)

    def test_customer_cannot_read_business_totals(self):
        customer = get_user_model().objects.create_user("customer", password="Test-Password-123")
        self.client.force_login(customer)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 403)

    def test_staff_can_read_empty_dashboard(self):
        staff = get_user_model().objects.create_user("manager", password="Test-Password-123", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your food court, at a glance.")
        self.assertEqual(response.context["sales_total"], 0)
        self.assertEqual(response.context["verified_total"], 0)

    def test_host_counter_total_is_separate_from_member_reimbursements(self):
        from decimal import Decimal
        from groups.services import create_group, join_group
        from orders.models import BillAllocation
        from payments.models import CounterPayment, Payment
        User = get_user_model()
        host = User.objects.create_user('counter-host', is_staff=True)
        friend = User.objects.create_user('counter-friend')
        group = create_group(owner=host, name='Equal bill')
        member = join_group(user=friend, code=group.code)
        order = group.order
        order.status, order.total = 'FINALIZED', Decimal('300.00')
        order.save()
        BillAllocation.objects.create(order=order, member=group.memberships.get(user=host), amount='150.00')
        due = BillAllocation.objects.create(order=order, member=member, amount='150.00')
        payment = Payment.objects.create(allocation=due, status='PENDING')
        CounterPayment.objects.create(order=order, recorded_by=host, amount='300.00')
        self.client.force_login(host)
        response = self.client.get(reverse('dashboard:home'))
        self.assertEqual(response.context['sales_total'], Decimal('300.00'))
        self.assertEqual(response.context['counter_declared_total'], Decimal('300.00'))
        self.assertEqual(response.context['pending_total'], Decimal('150.00'))
        self.assertEqual(response.context['verified_total'], Decimal('0.00'))
        payment.status = 'VERIFIED'
        payment.save()
        response = self.client.get(reverse('dashboard:home'))
        self.assertEqual(response.context['verified_total'], Decimal('150.00'))
        self.assertEqual(response.context['pending_total'], Decimal('0.00'))

