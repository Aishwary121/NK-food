"""Google login tests use local RSA signatures; no Google account or network."""
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.error import URLError

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .firebase_auth import (
    CERTIFICATE_CACHE_KEY, CERTIFICATE_URL, MAX_TOKEN_LENGTH,
    FirebaseSignInUnavailable, InvalidFirebaseToken, verify_firebase_token,
)
from .models import FirebaseIdentity, Profile

CONFIG = {'projectId': 'nk-local-auth-test', 'apiKey': 'public-test-key', 'authDomain': 'nk-local-auth-test.firebaseapp.com', 'appId': 'test-app'}


class SignedTokenFixture:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Local test certificate only')])
        now = datetime.now(timezone.utc)
        cls.certificate = (
            x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(cls.private_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(cls.private_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode('ascii')
        )

    def setUp(self):
        super().setUp()
        cache.delete(CERTIFICATE_CACHE_KEY)
        self.download = patch('accounts.firebase_auth.urlopen').start()
        self.addCleanup(patch.stopall)
        response = self.download.return_value.__enter__.return_value
        response.geturl.return_value = CERTIFICATE_URL
        response.read.return_value = json.dumps({'test-key': self.certificate}).encode()
        response.headers = {'Cache-Control': 'public, max-age=3600', 'Age': '0'}
        self.response = response

    def token(self, overrides=None, *, remove=(), key=None, headers=None):
        now = int(time.time())
        claims = {
            'iss': 'https://securetoken.google.com/nk-local-auth-test', 'aud': 'nk-local-auth-test',
            'sub': 'firebase-user-123', 'iat': now - 1, 'auth_time': now - 30, 'exp': now + 3600,
            'email': 'google-user@example.com', 'email_verified': True, 'name': 'Google Test User',
            'firebase': {'sign_in_provider': 'google.com'},
        }
        claims.update(overrides or {})
        for field in remove:
            claims.pop(field, None)
        return jwt.encode(claims, key or self.private_key, algorithm='RS256', headers=headers or {'kid': 'test-key'})


@override_settings(FIREBASE_WEB_CONFIG=CONFIG)
class FirebaseTokenTests(SignedTokenFixture, SimpleTestCase):
    def test_real_rsa_signature_and_fixed_public_certificate_endpoint(self):
        claims = verify_firebase_token(self.token(headers={'kid': 'test-key', 'jku': 'https://untrusted.example/keys'}))
        self.assertEqual(claims['sub'], 'firebase-user-123')
        request = self.download.call_args.args[0]
        self.assertEqual(request.full_url, CERTIFICATE_URL)
        self.assertEqual(self.download.call_args.kwargs, {'timeout': 5})

    def test_certificate_max_age_is_cached_and_unknown_key_does_not_refetch_immediately(self):
        token = self.token()
        with patch('accounts.firebase_auth.cache.set', wraps=cache.set) as save:
            verify_firebase_token(token)
            self.assertEqual(save.call_args.kwargs['timeout'], 3600)
        verify_firebase_token(token)
        with self.assertRaises(InvalidFirebaseToken):
            verify_firebase_token(self.token(headers={'kid': 'unknown-key'}))
        self.assertEqual(self.download.call_count, 1)

    def test_rotated_key_refreshes_older_cached_certificates(self):
        cache.set(CERTIFICATE_CACHE_KEY, {'certificates': {'old-key': self.certificate}, 'fetched_at': time.time() - 61}, timeout=3600)
        self.assertEqual(verify_firebase_token(self.token())['sub'], 'firebase-user-123')
        self.assertEqual(self.download.call_count, 1)

    def test_public_certificate_cache_honors_age_header(self):
        self.response.headers = {'Cache-Control': 'public,max-age=300', 'Age': '60'}
        with patch('accounts.firebase_auth.cache.set', wraps=cache.set) as save:
            verify_firebase_token(self.token())
            self.assertEqual(save.call_args.kwargs['timeout'], 240)

    def test_unavailable_or_malformed_certificates_fail_closed(self):
        self.download.side_effect = URLError('offline')
        with self.assertRaises(FirebaseSignInUnavailable):
            verify_firebase_token(self.token())
        self.download.side_effect = None
        for content in [b'[]', b'not json', b'x' * 65537, b'{"test-key":"invalid certificate"}']:
            with self.subTest(content=content[:30]):
                cache.delete(CERTIFICATE_CACHE_KEY)
                self.response.read.return_value = content
                with self.assertRaises(FirebaseSignInUnavailable):
                    verify_firebase_token(self.token())

    def test_invalid_signature_is_rejected(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with self.assertRaises(InvalidFirebaseToken):
            verify_firebase_token(self.token(key=other_key))

    def test_wrong_algorithms_missing_key_and_malformed_tokens_are_rejected_without_download(self):
        for token in [
            jwt.encode({'sub': 'attacker'}, 'a' * 40, algorithm='HS256', headers={'kid': 'test-key'}),
            jwt.encode({'sub': 'attacker'}, None, algorithm='none'),
            self.token(headers={'typ': 'JWT'}),
            'not-a-jwt', '', None, 'x' * (MAX_TOKEN_LENGTH + 1),
        ]:
            with self.subTest(token_type=type(token).__name__):
                with self.assertRaises(InvalidFirebaseToken):
                    verify_firebase_token(token)
        self.download.assert_not_called()

    def test_project_issuer_audience_and_required_claims(self):
        for change in [
            {'aud': 'another-project'}, {'aud': ['nk-local-auth-test']},
            {'iss': 'https://securetoken.google.com/another-project'},
            {'iss': 'https://securetoken.google.com/nk-local-auth-test/evil'},
        ]:
            with self.subTest(change=change), self.assertRaises(InvalidFirebaseToken):
                verify_firebase_token(self.token(change))
        for field in ['aud', 'iss', 'sub', 'iat', 'exp', 'auth_time']:
            with self.subTest(missing=field), self.assertRaises(InvalidFirebaseToken):
                verify_firebase_token(self.token(remove=[field]))

    def test_expiry_future_authentication_and_non_numeric_timestamps_are_rejected(self):
        now = int(time.time())
        for change in [
            {'exp': now - 1}, {'iat': now + 600}, {'auth_time': now + 600},
            {'auth_time': now, 'iat': now - 60}, {'auth_time': -1},
            {'iat': str(now - 1)}, {'auth_time': True}, {'exp': str(now + 300)},
        ]:
            with self.subTest(change=change), self.assertRaises(InvalidFirebaseToken):
                verify_firebase_token(self.token(change))

    def test_google_provider_verified_email_and_valid_uid_are_required(self):
        for change in [
            {'email_verified': False}, {'email_verified': 'true'}, {'email': 'invalid'},
            {'email': None}, {'email': 'a' * 250 + '@example.com'},
            {'firebase': {'sign_in_provider': 'password'}}, {'firebase': None},
            {'firebase': {'sign_in_provider': 'google.com', 'tenant': 'unconfigured-tenant'}},
            {'sub': ''}, {'sub': ' '}, {'sub': 'x' * 129}, {'sub': 123}, {'sub': 'bad\nuid'},
        ]:
            with self.subTest(change=change), self.assertRaises(InvalidFirebaseToken):
                verify_firebase_token(self.token(change))

    @override_settings(FIREBASE_WEB_CONFIG={})
    def test_disabled_configuration_never_contacts_google(self):
        with self.assertRaises(FirebaseSignInUnavailable):
            verify_firebase_token(self.token())
        self.download.assert_not_called()


@override_settings(FIREBASE_WEB_CONFIG=CONFIG)
class FirebaseSignInTests(SignedTokenFixture, TestCase):
    def post_token(self, token=None, **payload):
        return self.client.post(reverse('accounts:google_sign_in'), {'id_token': token or self.token(), **payload}, content_type='application/json')

    def test_signed_google_token_creates_unprivileged_user_profile_and_django_session(self):
        token = self.token({'is_staff': True, 'is_superuser': True, 'user_id': 999})
        session = self.client.session
        session['before_login'] = True
        session.save()
        old_session_key = session.session_key
        response = self.post_token(token, next='/groups/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'redirect': '/groups/'})
        identity = FirebaseIdentity.objects.select_related('user').get()
        user = identity.user
        self.assertEqual((identity.project_id, identity.uid), ('nk-local-auth-test', 'firebase-user-123'))
        self.assertTrue(user.username.startswith('google_'))
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.email, 'google-user@example.com')
        self.assertEqual(user.get_full_name(), 'Google Test User')
        self.assertTrue(Profile.objects.filter(user=user, mobile='').exists())
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertNotEqual(old_session_key, self.client.session.session_key)
        self.assertNotIn(token, json.dumps(dict(self.client.session)))
        self.assertIn('no-store', response['Cache-Control'])

    def test_existing_password_account_with_same_email_is_never_linked(self):
        existing = get_user_model().objects.create_user('existing', email='google-user@example.com', password='Existing-password!23', is_staff=True)
        self.assertEqual(self.post_token().status_code, 200)
        identity = FirebaseIdentity.objects.get()
        self.assertNotEqual(identity.user_id, existing.pk)
        self.assertEqual(get_user_model().objects.filter(email='google-user@example.com').count(), 2)
        existing.refresh_from_db()
        self.assertTrue(existing.check_password('Existing-password!23'))
        self.assertTrue(existing.is_staff)

    def test_stable_uid_reuses_identity_even_if_google_email_changes(self):
        self.post_token()
        first_user_id = FirebaseIdentity.objects.get().user_id
        response = self.post_token(self.token({'email': 'changed@example.com'}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FirebaseIdentity.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(int(self.client.session['_auth_user_id']), first_user_id)

    def test_same_uid_from_different_pinned_project_has_separate_identity(self):
        self.post_token()
        self.client.logout()
        with override_settings(FIREBASE_WEB_CONFIG={**CONFIG, 'projectId': 'second-project'}):
            response = self.post_token(self.token({'aud': 'second-project', 'iss': 'https://securetoken.google.com/second-project'}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FirebaseIdentity.objects.count(), 2)
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_inactive_or_privileged_existing_identity_cannot_sign_in(self):
        self.post_token()
        user = FirebaseIdentity.objects.get().user
        self.client.logout()
        for flags in [{'is_active': False}, {'is_active': True, 'is_staff': True}, {'is_staff': False, 'is_superuser': True}]:
            get_user_model().objects.filter(pk=user.pk).update(**flags)
            response = self.post_token()
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('_auth_user_id', self.client.session)
        user.refresh_from_db()
        self.assertTrue(user.is_superuser)

    def test_only_local_redirect_paths_are_returned(self):
        for unsafe in ['https://evil.example/', '//evil.example/', '/\\evil.example/', 'javascript:alert(1)', '/\nredirect', ['invalid'], 'x' * 3000]:
            with self.subTest(next=unsafe):
                self.assertEqual(self.post_token(next=unsafe).json()['redirect'], reverse('menu:home'))
        self.assertEqual(self.post_token(next='/orders/3/?tab=payment#bill').json()['redirect'], '/orders/3/?tab=payment#bill')

    def test_json_body_bounds_and_post_method(self):
        url = reverse('accounts:google_sign_in')
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {'id_token': self.token()}).status_code, 400)
        for body in ['null', '[]', '{broken', '{}', '{"id_token": 123}', json.dumps({'id_token': 'x' * (MAX_TOKEN_LENGTH + 1)}), 'x' * 22000]:
            with self.subTest(length=len(body)):
                self.assertEqual(self.client.post(url, body, content_type='application/json').status_code, 400)
        self.assertEqual(FirebaseIdentity.objects.count(), 0)
        self.download.assert_not_called()

    def test_csrf_required_and_valid_csrf_allows_signed_google_login(self):
        client = Client(enforce_csrf_checks=True)
        body = {'id_token': self.token()}
        url = reverse('accounts:google_sign_in')
        self.assertEqual(client.post(url, body, content_type='application/json').status_code, 403)
        client.get(reverse('accounts:login'))
        csrf_token = client.cookies['csrftoken'].value
        response = client.post(url, body, content_type='application/json', HTTP_X_CSRFTOKEN=csrf_token)
        self.assertEqual(response.status_code, 200)
        self.assertIn('_auth_user_id', client.session)

    def test_failed_signature_creates_no_session_or_identity_and_does_not_expose_token(self):
        token = self.token({'aud': 'wrong-project'})
        response = self.post_token(token)
        self.assertEqual(response.status_code, 401)
        self.assertNotContains(response, token, status_code=401)
        self.assertEqual(FirebaseIdentity.objects.count(), 0)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_certificate_outage_returns_recoverable_safe_json(self):
        self.download.side_effect = URLError('internal failure details')
        response = self.post_token()
        self.assertEqual(response.status_code, 503)
        self.assertIn('error', response.json())
        self.assertNotIn('internal failure', response.json()['error'])
        self.assertEqual(FirebaseIdentity.objects.count(), 0)

    @override_settings(FIREBASE_WEB_CONFIG={})
    def test_not_configured_endpoint_is_disabled(self):
        self.assertEqual(self.post_token().status_code, 503)
        self.download.assert_not_called()
        self.assertEqual(FirebaseIdentity.objects.count(), 0)

    def test_popup_policy_is_scoped_to_configured_authentication_pages(self):
        for url in [reverse('accounts:login'), reverse('accounts:register'), reverse('login')]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url)['Cross-Origin-Opener-Policy'], 'same-origin-allow-popups')
        self.assertNotEqual(self.client.get(reverse('menu:home'))['Cross-Origin-Opener-Policy'], 'same-origin-allow-popups')
        with override_settings(FIREBASE_WEB_CONFIG={}):
            for url in [reverse('accounts:login'), reverse('accounts:register')]:
                self.assertNotEqual(self.client.get(url)['Cross-Origin-Opener-Policy'], 'same-origin-allow-popups')
