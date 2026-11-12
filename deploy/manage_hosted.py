"""Run a management command with a private environment JSON configuration."""
import json
import os
from pathlib import Path
import sys

if len(sys.argv) < 3:
    raise SystemExit('Usage: python deploy/manage_hosted.py /private/environment.json COMMAND [ARGS]')

configuration = json.loads(Path(sys.argv[1]).read_text())
os.environ.update({key: str(value) for key, value in configuration.items()})
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nk_foodcourt.settings')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from django.core.management import execute_from_command_line
execute_from_command_line(['manage.py', *sys.argv[2:]])
