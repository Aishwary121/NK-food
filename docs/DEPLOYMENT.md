# Deploying N.K. Food Court

This application requires a Python/Django host. Publishing the repository through GitHub Pages, or uploading only its HTML files, does not run its database, authentication, bill splitting or payment workflow. The files in `deploy/` prepare the existing Django application for hosting; their presence is not evidence that a live deployment has succeeded.

## Shared production configuration

Use Python 3.11 or a compatible later version. Install `requirements.txt` for a host which supplies WSGI, or `requirements-production.txt` when running Gunicorn on Linux. Set these environment variables through the host's settings or a private configuration file:

| Variable | Required value |
| --- | --- |
| `DJANGO_DEBUG` | `false` |
| `DJANGO_SECRET_KEY` | A long, randomly generated, unique secret. Keep it out of Git and the report. |
| `DJANGO_ALLOWED_HOSTS` | Exact assigned hostname(s), separated by commas, without `https://`. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Exact HTTPS origin(s), such as `https://your-account.pythonanywhere.com`. |
| `DJANGO_TRUST_PROXY` | `true` only when the trusted host proxy overwrites `X-Forwarded-Proto` with the original request scheme. Otherwise leave `false`. |
| `DJANGO_DB_PATH` | Absolute SQLite filename on persistent storage; ignored when PostgreSQL is configured. |
| `DJANGO_MEDIA_DIR` | Absolute directory on persistent storage. Its `public/` and `private/` children remain separate. |

The existing `DB_ENGINE=postgresql`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` and `DB_PORT` settings remain available. Install `psycopg[binary]` if choosing PostgreSQL. Database configuration does not migrate an existing SQLite database into PostgreSQL.

Production settings enable HTTPS redirects, secure cookies and WhiteNoise for collected static assets. Proxy trust is explicit so the application does not trust arbitrary forwarded headers on an unconfigured host. Run `python manage.py collectstatic --noinput` before serving requests. WhiteNoise serves application assets and food photographs, not uploaded files. The application's `/media/` endpoint serves only safe, currently referenced dish images. Static collector QR images remain behind the existing authenticated payment views; never publish the private directory as a static-file mapping.

Start with a fresh production database, run `python manage.py migrate`, then `python manage.py seed_data --menu-only`. Create the administrator interactively with `python manage.py createsuperuser` and unique credentials. The production seed command never creates the local demonstration users or groups. Do not upload the local `db.sqlite3`, `.env`, screenshots containing private information, or private QR uploads to a public repository.

## PythonAnywhere: host-managed WSGI

Follow the provider's [existing Django project deployment guide](https://help.pythonanywhere.com/pages/DeployExistingDjangoProject/) and select a plan with the resources needed for this project. Confirm the chosen account's current limits before creating an application.

For a new account, upload and extract the source, open a Bash console, change into the directory containing `manage.py`, and run:

```bash
python3.11 deploy/pythonanywhere_setup.py --hostname USERNAME.pythonanywhere.com
```

Replace the hostname with the one assigned to the account (including the EU hostname if applicable), and use a Python version available to the account that is at least 3.11. The helper creates or reuses `~/.virtualenvs/nk-food`, installs without a pip download cache, stores a generated secret in an owner-only file outside the source directory, prepares the persistent database and media directories, loads only menu data, collects assets and performs deployment checks. It generates a secret-free WSGI file and prints the exact Web-tab fields. It prompts for an administrator only in an interactive console; otherwise it prints the command. No hosting account, paid resource or public Web app is created by the helper.

Complete the Web tab instructions printed by the helper and verify the resulting HTTPS application. To configure each step manually, use the procedure below.

1. Upload the source or clone the user's repository into `/home/USERNAME/NK-food`. Create a virtual environment using Python 3.11 or a compatible available version, and install `requirements.txt`.
2. Create a private file `/home/USERNAME/.nk-food-environment.json` containing the production variables above as a JSON object. Use the actual assigned host. Set `DJANGO_DB_PATH` to `/home/USERNAME/nk-food-data/db.sqlite3` and `DJANGO_MEDIA_DIR` to `/home/USERNAME/nk-food-data/media`. Restrict that file to the account owner with `chmod 600`. Do not place it inside the Git repository.
3. In a Bash console with the virtual environment active, run the following Python block from the project root. It loads the same configuration used by WSGI and prepares only this application's data and assets:

   ```bash
   python - <<'PY'
   import json, os, subprocess
   from pathlib import Path
   config = json.loads((Path.home() / '.nk-food-environment.json').read_text())
   os.environ.update({key: str(value) for key, value in config.items()})
   for arguments in [
       ['deploy/prepare_runtime.py'],
       ['manage.py', 'migrate', '--noinput'],
       ['manage.py', 'seed_data', '--menu-only'],
       ['manage.py', 'collectstatic', '--noinput'],
       ['manage.py', 'check', '--deploy', '--fail-level', 'WARNING'],
   ]:
       subprocess.run(['python', *arguments], check=True)
   PY
   ```

   Then create the administrator interactively, using the same private environment:

   ```bash
   python deploy/manage_hosted.py /home/USERNAME/.nk-food-environment.json createsuperuser
   ```

4. Create the Web app with **Manual configuration**, matching its Python version to the virtual environment. Set the source directory and virtual-environment path on the Web tab.
5. Copy `deploy/pythonanywhere_wsgi.py.example` into the provider's WSGI file linked on the Web tab, replacing both `USERNAME` placeholders. This file reads the private JSON configuration; it does not contain the secret itself.
6. Map `/static/` to `/home/USERNAME/NK-food/staticfiles` if using the provider's static-file service. Do not map `/media/` or the private media directory: Django handles the controlled image routes. Turn on the host's HTTPS enforcement, reload the app and verify the assigned HTTPS address. If the proxy passes HTTPS through `X-Forwarded-Proto`, enable the corresponding trusted-proxy setting.

Do not run Gunicorn or `runserver` inside the PythonAnywhere Web app; the provider supplies the WSGI server. Use the same private configuration when running later management commands.

## Render: persistent storage required

Render's [Django guide](https://render.com/docs/deploy-django) describes its Python build and deployment workflow. For the current filesystem-backed uploads and SQLite configuration, a persistent disk is required. Render [persistent disks](https://render.com/docs/disks) are attached to **paid** services. No paid service or disk is created by these files; the owner must choose and approve the plan in the hosting account.

A Render Free service's local filesystem is temporary, so it is not an appropriate deployment for preserving this project's uploaded QR images and SQLite data. PostgreSQL alone does not solve the uploaded-file persistence requirement. An alternative architecture would need separate durable, private-capable object storage before removing the disk requirement.

For an explicitly selected disk-backed service:

| Setting | Value |
| --- | --- |
| Language | Python 3 |
| Build command | `bash deploy/build.sh` |
| Start command | `bash deploy/start.sh` |
| Persistent disk mount | `/var/data` |
| `DJANGO_DB_PATH` | `/var/data/nk-food/db.sqlite3` |
| `DJANGO_MEDIA_DIR` | `/var/data/nk-food/media` |
| `WEB_CONCURRENCY` | `1` for the modest SQLite demonstration |
| `DJANGO_TRUST_PROXY` | `true`, using Render's trusted HTTPS proxy |

Set the remaining production environment values from the shared table using the assigned `onrender.com` hostname. A service needs the GitHub repository to contain the source and the hosting account to have repository access. No `render.yaml` is provided that could implicitly create a paid service.

The build installs dependencies, collects static assets and runs deployment checks. It does not touch the database because Render's persistent disk is not mounted during the build. The start command creates the configured runtime directories, applies migrations, adds missing menu records without overwriting administrator edits, then starts Gunicorn. Create the administrator in the service shell with `python manage.py createsuperuser`.

For higher concurrency, use PostgreSQL, test concurrent order/payment operations and retain durable storage for media. Back up the database and both media directories together, and verify restore procedures.

## Evidence required before reporting a successful deployment

Record the provider's successful deployment result and actual HTTPS URL. Open the deployed application and check registration/login, the menu's real photos, group creation, order finalization, bill totals and collector/member authorization. Upload a test QR only to a controlled demonstration group and verify it survives a restart. Check that an unauthenticated request cannot fetch private QR media.

Capture report screenshots from the verified deployment, using controlled test data and hiding credentials, real payment details and private hosting information. If hosting access is still missing, describe deployment as pending; local screenshots must be labelled as local demonstrations. Do not substitute a static mockup or invent a live URL.
