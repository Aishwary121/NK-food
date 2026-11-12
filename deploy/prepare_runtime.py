"""Create configured runtime directories; never seed users or expose secrets."""
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nk_foodcourt.settings')

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

if settings.DEBUG:
    raise ImproperlyConfigured('Set DJANGO_DEBUG=false for the production start command.')

if settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    Path(settings.DATABASES['default']['NAME']).parent.mkdir(parents=True, exist_ok=True)
Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
Path(settings.PRIVATE_MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
