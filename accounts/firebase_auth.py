"""Verify Firebase Google ID tokens and establish a separate Django identity.

Only Google's fixed public certificate endpoint is contacted. ID tokens are not
persisted, and this public-key verification does not perform revocation checks.
See https://firebase.google.com/docs/auth/admin/verify-id-tokens
"""
import json
from http.client import HTTPException
import re
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import jwt
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.views.decorators.debug import sensitive_variables

from .models import FirebaseIdentity, Profile

CERTIFICATE_URL = 'https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com'
CERTIFICATE_CACHE_KEY = 'firebase-google-public-certificates-v1'
MAX_TOKEN_LENGTH = 16384
MAX_CERTIFICATE_BYTES = 65536


class FirebaseSignInError(Exception):
    """Public-safe error; never include a token or private claims in its text."""


class InvalidFirebaseToken(FirebaseSignInError):
    pass


class FirebaseSignInUnavailable(FirebaseSignInError):
    pass


def firebase_project_id():
    config = getattr(settings, 'FIREBASE_WEB_CONFIG', {})
    project_id = config.get('projectId') if isinstance(config, dict) else None
    if not isinstance(project_id, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]{0,127}', project_id):
        raise FirebaseSignInUnavailable('Google sign-in is not configured yet. Please use your username and password.')
    return project_id


def _download_certificates():
    try:
        request = Request(CERTIFICATE_URL, headers={'Accept': 'application/json'})
        with urlopen(request, timeout=5) as response:
            if response.geturl() != CERTIFICATE_URL:
                raise ValueError('Unexpected certificate endpoint.')
            content = response.read(MAX_CERTIFICATE_BYTES + 1)
            if len(content) > MAX_CERTIFICATE_BYTES:
                raise ValueError('Certificate response too large.')
            certificates = json.loads(content)
            cache_control = response.headers.get('Cache-Control', '')
            match = re.search(r'(?:^|,)\s*max-age\s*=\s*"?(\d+)', cache_control, re.I)
            age = int(response.headers.get('Age', '0'))
            ttl = max(0, min(86400, int(match.group(1)) - age)) if match else 0
        if (not isinstance(certificates, dict) or not certificates or len(certificates) > 32
                or any(not isinstance(key, str) or not 0 < len(key) <= 256
                       or not isinstance(value, str) or not 0 < len(value) <= 20000
                       for key, value in certificates.items())):
            raise ValueError('Invalid certificate response.')
    except (URLError, OSError, HTTPException, ValueError, TypeError, UnicodeError) as exc:
        raise FirebaseSignInUnavailable('Google sign-in is temporarily unavailable. Please try again shortly.') from exc
    entry = {'certificates': certificates, 'fetched_at': time.time()}
    if ttl:
        cache.set(CERTIFICATE_CACHE_KEY, entry, timeout=ttl)
    return entry


def _certificate_for_key(key_id):
    entry = cache.get(CERTIFICATE_CACHE_KEY)
    if entry is None:
        entry = _download_certificates()
    elif key_id not in entry['certificates'] and time.time() - entry['fetched_at'] >= 60:
        # Permit key rotation without allowing arbitrary key IDs to cause an
        # outbound certificate request on every rejected sign-in attempt.
        entry = _download_certificates()
    certificate = entry['certificates'].get(key_id)
    if certificate is None:
        raise InvalidFirebaseToken('Google sign-in could not be verified. Please sign in again.')
    try:
        public_key = x509.load_pem_x509_certificate(certificate.encode('ascii')).public_key()
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise ValueError('An RSA public key is required.')
    except (ValueError, TypeError, UnicodeError, UnsupportedAlgorithm) as exc:
        raise FirebaseSignInUnavailable('Google sign-in is temporarily unavailable. Please try again shortly.') from exc
    return public_key


@sensitive_variables('id_token', 'claims')
def verify_firebase_token(id_token):
    project_id = firebase_project_id()
    if not isinstance(id_token, str) or not 0 < len(id_token) <= MAX_TOKEN_LENGTH:
        raise InvalidFirebaseToken('A valid Google sign-in token is required.')
    try:
        header = jwt.get_unverified_header(id_token)
        key_id = header.get('kid')
        if (header.get('alg') != 'RS256' or not isinstance(key_id, str)
                or not 0 < len(key_id) <= 256 or header.get('crit')):
            raise jwt.InvalidTokenError('Invalid signing header.')
        claims = jwt.decode(
            id_token, _certificate_for_key(key_id), algorithms=['RS256'],
            audience=project_id, issuer=f'https://securetoken.google.com/{project_id}',
            options={'require': ['exp', 'iat', 'auth_time', 'aud', 'iss', 'sub'], 'strict_aud': True},
        )
        now = time.time()
        if any(type(claims[key]) is not int for key in ('exp', 'iat', 'auth_time')):
            raise jwt.InvalidTokenError('Invalid timestamp types.')
        if not (0 <= claims['auth_time'] <= claims['iat'] <= now < claims['exp']):
            raise jwt.InvalidTokenError('Invalid authentication time.')
        uid = claims['sub']
        if not isinstance(uid, str) or not uid.strip() or len(uid) > 128 or any(ord(char) < 32 for char in uid):
            raise jwt.InvalidTokenError('Invalid identity.')
        firebase = claims.get('firebase')
        if (not isinstance(firebase, dict) or firebase.get('sign_in_provider') != 'google.com'
                or firebase.get('tenant')):
            raise jwt.InvalidTokenError('Only this project\'s Google sign-in is supported.')
        email = claims.get('email')
        if claims.get('email_verified') is not True or not isinstance(email, str) or len(email) > 254:
            raise jwt.InvalidTokenError('A verified email address is required.')
        validate_email(email)
    except (jwt.PyJWTError, ValidationError, ValueError, TypeError) as exc:
        raise InvalidFirebaseToken('Google sign-in could not be verified. Please sign in again.') from exc
    return claims


def _usable_identity_user(identity):
    user = identity.user
    if not user.is_active:
        raise InvalidFirebaseToken('This account is inactive. Please contact the administrator.')
    if user.is_staff or user.is_superuser:
        raise InvalidFirebaseToken('Use username and password sign-in for an administrator account.')
    return user


@sensitive_variables('claims')
def user_for_firebase_identity(claims):
    """Resolve only a verified project/UID, never an email or browser user ID."""
    project_id, uid = firebase_project_id(), claims['sub']
    identity = FirebaseIdentity.objects.select_related('user').filter(project_id=project_id, uid=uid).first()
    if identity is not None:
        return _usable_identity_user(identity)
    name = claims.get('name', '')
    if not isinstance(name, str):
        name = ''
    name = ' '.join(name.split())[:300]
    first_name, _, last_name = name.partition(' ')
    try:
        with transaction.atomic():
            user = get_user_model().objects.create_user(
                username='google_' + uuid4().hex, password=None,
                email=claims['email'], first_name=first_name[:150], last_name=last_name[:150],
                is_active=True, is_staff=False, is_superuser=False,
            )
            identity = FirebaseIdentity.objects.create(user=user, project_id=project_id, uid=uid)
            Profile.objects.create(user=user)
    except IntegrityError:
        # Simultaneous first sign-ins for one UID must not leave an extra user.
        # The losing transaction rolls back; only the unique mapping may win.
        identity = FirebaseIdentity.objects.select_related('user').filter(project_id=project_id, uid=uid).first()
        if identity is None:
            raise FirebaseSignInUnavailable('Google sign-in could not create the account. Please try again.') from None
    return _usable_identity_user(identity)
