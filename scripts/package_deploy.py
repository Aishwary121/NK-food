"""Build a source-only deployment ZIP without databases, uploads or secrets."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
output = root / 'release' / 'NK-Food-deploy.zip'
output.parent.mkdir(exist_ok=True)
directories = ['accounts', 'dashboard', 'groups', 'menu', 'nk_foodcourt', 'orders',
               'payments', 'static', 'templates', 'deploy', 'docs', 'scripts']
files = [root / name for name in ['manage.py', 'requirements.txt', 'requirements-dev.txt',
          'requirements-production.txt', 'README.md', '.env.example', '.gitignore'] if (root / name).exists()]
for directory in directories:
    for candidate in (root / directory).rglob('*'):
        if not candidate.is_file() or '__pycache__' in candidate.parts:
            continue
        if candidate.suffix in ['.pyc', '.pyo', '.sqlite3', '.log'] or '.generated.' in candidate.name:
            continue
        if candidate.name == '.env' or candidate.name.startswith('.nk-food-environment'):
            continue
        files.append(candidate)
with ZipFile(output, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
    for source in sorted(set(files)):
        archive.write(source, 'NK-Food/' + source.relative_to(root).as_posix())
with ZipFile(output) as archive:
    assert archive.testzip() is None
    names = archive.namelist()
    assert not any('/.venv/' in n or '/media/' in n or '.sqlite3' in n or '/.report-work/' in n for n in names)
print(f'{output}: {len(names)} source files, {output.stat().st_size / 1024 / 1024:.2f} MiB; no database, uploads or runtime secrets.')
