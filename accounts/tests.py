from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Profile


class AccountTests(TestCase):
    def test_registration_hashes_password_and_creates_profile(self):
        response = self.client.post(reverse("accounts:register"), {"full_name": "Nisha Sharma", "username": "nisha", "email": "nisha@example.com", "mobile": "9876543210", "password1": "UniqueStrong!123", "password2": "UniqueStrong!123"})
        self.assertRedirects(response, reverse("menu:home"))
        user = get_user_model().objects.get(username="nisha")
        self.assertEqual(user.get_full_name(), "Nisha Sharma")
        self.assertTrue(user.check_password("UniqueStrong!123"))
        self.assertNotEqual(user.password, "UniqueStrong!123")
        self.assertEqual(Profile.objects.get(user=user).mobile, "9876543210")

    def test_restricted_profile_and_post_logout(self):
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 302)
        user = get_user_model().objects.create_user("member", password="Strong!12345")
        response = self.client.post(reverse("accounts:login"), {"username": user.username, "password": "Strong!12345"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 302)

    def test_invalid_mobile_and_duplicate_user_rejected(self):
        get_user_model().objects.create_user("taken")
        response = self.client.post(reverse("accounts:register"), {"full_name": "Someone", "username": "taken", "email": "someone@example.com", "mobile": "invalid", "password1": "UniqueStrong!123", "password2": "UniqueStrong!123"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("mobile", response.context["form"].errors)
        self.assertIn("username", response.context["form"].errors)

    def test_profile_edit_only_updates_signed_in_account(self):
        user = get_user_model().objects.create_user("owner", password="Strong!12345")
        other = get_user_model().objects.create_user("another", first_name="Keep")
        self.client.force_login(user)
        response = self.client.post(reverse("accounts:profile_edit"), {"first_name": "Updated", "last_name": "Name", "email": "updated@example.com", "mobile": "9876543210", "id": other.pk})
        self.assertRedirects(response, reverse("accounts:profile"))
        user.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(user.first_name, "Updated")
        self.assertEqual(other.first_name, "Keep")
        self.assertTrue(user.check_password("Strong!12345"))
        self.assertEqual(user.profile.mobile, "9876543210")

