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

