"""First-run setup on the owner's PythonAnywhere account; no credentials in source.

Run from an uploaded project with Python 3.11 or later:
    python3.11 deploy/pythonanywhere_setup.py --hostname USERNAME.pythonanywhere.com
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hostname', required=True, help='Exact hostname assigned in the PythonAnywhere Web tab.')
    parser.add_argument('--skip-admin', action='store_true', help='Prepare files and print the administrator command without prompting.')
    args = parser.parse_args()
    hostname = args.hostname.strip().lower()
    if sys.platform == 'win32' or sys.version_info < (3, 11):
        parser.error('Run this script in a PythonAnywhere Bash console with Python 3.11 or later.')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.pythonanywhere\.com', hostname):
        parser.error('Use the assigned *.pythonanywhere.com hostname, without a scheme or path.')

    project = Path(__file__).resolve().parents[1]
    home_directory = Path.home()
    virtual_environment = home_directory / '.virtualenvs' / 'nk-food'
    interpreter = virtual_environment / 'bin' / 'python'
    if not interpreter.exists():
        print('Creating the nk-food virtual environment...')
        venv.EnvBuilder(with_pip=True).create(virtual_environment)

    subprocess.run([
        str(interpreter), '-m', 'pip', 'install', '--no-cache-dir',
        '-r', str(project / 'requirements.txt'),
    ], check=True)

    environment_file = home_directory / '.nk-food-environment.json'
    configuration = json.loads(environment_file.read_text()) if environment_file.exists() else {}
    # Preserve an existing key and data locations when this setup is rerun.
    configuration.setdefault('DJANGO_SECRET_KEY', secrets.token_urlsafe(64))
    configuration.setdefault('DJANGO_DB_PATH', str(home_directory / 'nk-food-data' / 'db.sqlite3'))
    configuration.setdefault('DJANGO_MEDIA_DIR', str(home_directory / 'nk-food-data' / 'media'))
    configuration.update({
        'DJANGO_DEBUG': 'false',
        'DJANGO_ALLOWED_HOSTS': hostname,
        'DJANGO_CSRF_TRUSTED_ORIGINS': f'https://{hostname}',
        # PythonAnywhere terminates HTTPS at its own trusted reverse proxy.
        'DJANGO_TRUST_PROXY': 'true',
    })
    # Create with restrictive permissions before writing any secret bytes.
    descriptor = os.open(environment_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
        json.dump(configuration, target, indent=2)
    environment_file.chmod(0o600)
    environment = os.environ.copy()
    environment.update({key: str(value) for key, value in configuration.items()})

    for command in [
        ['deploy/prepare_runtime.py'],
        ['manage.py', 'migrate', '--noinput'],
        ['manage.py', 'seed_data', '--menu-only'],
        ['manage.py', 'collectstatic', '--noinput'],
        ['manage.py', 'check', '--deploy', '--fail-level', 'WARNING'],
    ]:
        subprocess.run([str(interpreter), *command], cwd=project, env=environment, check=True)

    wsgi_content = f'''# Generated for this account; contains no secret values.
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, {str(project)!r})
configuration = json.loads(Path({str(environment_file)!r}).read_text())
os.environ.update({{key: str(value) for key, value in configuration.items()}})
os.environ['DJANGO_SETTINGS_MODULE'] = 'nk_foodcourt.settings'

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
'''
    wsgi_file = project / 'deploy' / 'pythonanywhere_wsgi.generated.txt'
    wsgi_file.write_text(wsgi_content, encoding='utf-8')

    print('\nPreparation complete. Finish the Web tab configuration:')
    print(f'1. Add a Web app for {hostname}, selecting Manual configuration and Python {sys.version_info.major}.{sys.version_info.minor}.')
    print(f'2. Source code and working directory: {project}')
    print(f'3. Virtualenv: {virtual_environment}')
    print(f'4. Replace the Web tab WSGI file contents with: {wsgi_file}')
    print(f'5. Static mapping: /static/ -> {project / "staticfiles"}')
    print('6. Enable Force HTTPS, reload, and test the HTTPS URL. Do not add a /media/ static mapping.')
    print(f'URL to verify after reload: https://{hostname}/')
    print('The host is not live until the Web tab has been configured and reloaded.')

    admin_command = [str(interpreter), 'deploy/manage_hosted.py', str(environment_file), 'createsuperuser']
    if not args.skip_admin and sys.stdin.isatty():
        print('\nCreate a unique administrator account now. Do not reuse the local demo password.')
        subprocess.run(admin_command, cwd=project, check=True)
    else:
        import shlex
        print('Create an administrator from the project directory:')
        print(shlex.join(admin_command))


if __name__ == '__main__':
    main()
