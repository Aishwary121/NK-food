"""Local-first Django settings; production secrets come from the environment."""
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('DJANGO_DEBUG', 'true').lower() == 'true'
# This academic demo simulates payment only; it never initiates a bank transfer.
PAYMENT_DEMO_MODE = os.getenv('PAYMENT_DEMO_MODE', 'true').lower() == 'true'
# Public Firebase Web SDK identifiers. No service account key or OAuth secret is needed.
_firebase_web_config = {
    'apiKey': os.getenv('FIREBASE_API_KEY', '').strip(),
    'authDomain': os.getenv('FIREBASE_AUTH_DOMAIN', '').strip(),
    'projectId': os.getenv('FIREBASE_PROJECT_ID', '').strip(),
    'appId': os.getenv('FIREBASE_APP_ID', '').strip(),
}
FIREBASE_WEB_CONFIG = _firebase_web_config if all(_firebase_web_config.values()) else {}
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('Set DJANGO_SECRET_KEY when DJANGO_DEBUG=false.')
    SECRET_KEY = 'development-only-nk-foodcourt-do-not-use-in-production'
ALLOWED_HOSTS = [host.strip() for host in os.getenv('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost,[::1]').split(',') if host.strip()]
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if origin.strip()]
# Enable only behind a trusted HTTPS proxy which overwrites this header.
if os.getenv('DJANGO_TRUST_PROXY', 'false').lower() == 'true':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'accounts', 'menu', 'groups', 'orders', 'payments', 'dashboard',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
if not DEBUG:
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')
ROOT_URLCONF = 'nk_foodcourt.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True, 'OPTIONS': {'context_processors': [
        'django.template.context_processors.request', 'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
        'nk_foodcourt.context_processors.payment_mode',
        'nk_foodcourt.context_processors.firebase_auth',
    ]},
}]
WSGI_APPLICATION = 'nk_foodcourt.wsgi.application'
ASGI_APPLICATION = 'nk_foodcourt.asgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': Path(os.getenv('DJANGO_DB_PATH', str(BASE_DIR / 'db.sqlite3'))), 'OPTIONS': {'timeout': 20}}}
if os.getenv('DB_ENGINE') == 'postgresql':
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': os.environ['DB_NAME'],
        'USER': os.environ['DB_USER'], 'PASSWORD': os.environ['DB_PASSWORD'],
        'HOST': os.getenv('DB_HOST', 'localhost'), 'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': 60,
    }
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'en-in'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
if not DEBUG:
    STORAGES = {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
    }
# Public dish images and private payment QR uploads never share a URL root.
MEDIA_URL = '/media/'
MEDIA_DIRECTORY = Path(os.getenv('DJANGO_MEDIA_DIR', str(BASE_DIR / 'media')))
MEDIA_ROOT = MEDIA_DIRECTORY / 'public'
PRIVATE_MEDIA_ROOT = MEDIA_DIRECTORY / 'private'
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
FILE_UPLOAD_PERMISSIONS = 0o600
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'menu:home'
LOGOUT_REDIRECT_URL = 'menu:home'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
