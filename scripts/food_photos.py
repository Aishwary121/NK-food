"""Fetch attributed, freely licensed Commons food photographs for the local catalog.

This is an authoring helper, never a request-time network dependency. Inspect the
candidate descriptions and downloaded photographs before mapping a dish to one.
"""
import argparse
import hashlib
import html
import io
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = ('drinks', 'mains', 'snacks')
HEADERS = {'User-Agent': 'NKFoodCourtMenu/1.0 (local educational menu; attributed Commons reuse)'}
HOSTS = {'commons.wikimedia.org', 'upload.wikimedia.org', 'thumb.wikimedia.org'}


def read_url(url, limit=20 * 1024 * 1024):
    if urllib.parse.urlparse(url).hostname not in HOSTS:
        raise ValueError('Only Wikimedia image and metadata hosts are supported.')
    for attempt in range(4):
        try:
            time.sleep(0.7)
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
                if urllib.parse.urlparse(response.url).hostname not in HOSTS:
                    raise ValueError('Unexpected redirect host.')
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ValueError('Download exceeds the size limit.')
                return data
        except (urllib.error.URLError, TimeoutError) as exc:
            if 'CERTIFICATE_VERIFY_FAILED' in str(exc):
                # Windows curl uses the OS trust store. TLS validation remains
                # enabled; never use an insecure SSL context or curl -k.
                result = subprocess.run([
                    'curl.exe', '--fail', '--silent', '--show-error', '--location',
                    '--max-time', '40', '--max-filesize', str(limit),
                    '--proto', '=https', '--proto-redir', '=https',
                    '--user-agent', HEADERS['User-Agent'], url,
                ], capture_output=True)
                if result.returncode == 0 and len(result.stdout) <= limit:
                    return result.stdout
            if attempt == 3:
                raise
            time.sleep(8 + attempt * 4 if isinstance(exc, urllib.error.HTTPError) and exc.code == 429 else 1 + attempt * 2)


def api(**params):
    return json.loads(read_url('https://commons.wikimedia.org/w/api.php?' + urllib.parse.urlencode({'action': 'query', 'format': 'json', **params})))


def plain(value):
    return ' '.join(html.unescape(re.sub('<[^>]+>', '', str(value))).split())


def metadata(page):
    info = page.get('imageinfo', [{}])[0]
    ext = info.get('extmetadata', {})
    value = lambda key: plain(ext.get(key, {}).get('value', ''))
    return {
        'title': page['title'], 'source_url': info.get('descriptionurl', ''),
        'author': value('Artist') or value('Credit'), 'license': value('LicenseShortName'),
        'license_url': value('LicenseUrl'), 'description': value('ImageDescription'),
        'source_width': info.get('width'), 'source_height': info.get('height'),
        'download_url': info.get('thumburl') or info.get('url'),
    }


def is_free(record):
    license_name = record['license'].lower()
    return (license_name.startswith(('cc by ', 'cc by-sa ', 'cc0', 'public domain'))
            and 'noncommercial' not in license_name and 'no derivatives' not in license_name)


def search(query, limit=6):
    result = api(generator='search', gsrsearch=query + ' filetype:bitmap', gsrnamespace=6,
                 gsrlimit=limit, prop='imageinfo', iiprop='url|extmetadata|size', iiurlwidth=960)
    pages = sorted(result.get('query', {}).get('pages', {}).values(), key=lambda p: p.get('index', 0))
    records = [metadata(page) for page in pages]
    return [{**r, 'description': r['description'][:600], 'free_license': is_free(r)} for r in records]


def manifest_path(collection):
    return ROOT / 'menu' / 'data' / 'photos' / f'{collection}.json'


def read_manifest(collection):
    path = manifest_path(collection)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'assets': {}, 'dishes': {}}


def save_manifest(collection, manifest):
    path = manifest_path(collection)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def preload_metadata(collection, titles):
    """A single Commons request can resolve 50 selected file titles."""
    path = ROOT / '.browser-qa' / f'{collection}-candidates.json'
    path.parent.mkdir(exist_ok=True)
    cache = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    known = {r['title'].replace('_', ' ') for rows in cache.values() for r in rows}
    missing = [title for title in dict.fromkeys(titles) if title.replace('_', ' ') not in known]
    for start in range(0, len(missing), 50):
        result = api(titles='|'.join(missing[start:start + 50]), prop='imageinfo',
                     iiprop='url|extmetadata|size', iiurlwidth=960)
        rows = [metadata(page) for page in result.get('query', {}).get('pages', {}).values() if 'imageinfo' in page]
        cache.setdefault('__selected_files__', []).extend(rows)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Loaded metadata for {len(rows)} selected photographs.', flush=True)


def download(collection, key, title):
    if not re.fullmatch(re.escape(collection) + r'-[a-z0-9-]+', key):
        raise ValueError('Asset key must use the collection prefix and lowercase letters/digits/hyphens.')
    if not title.startswith('File:'):
        raise ValueError('Use an exact Commons File: title from the search results.')
    cache_path = ROOT / '.browser-qa' / f'{collection}-candidates.json'
    candidates = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    record = next((dict(candidate) for results in candidates.values() for candidate in results if candidate['title'].replace('_', ' ') == title.replace('_', ' ')), None)
    if record is None:
        result = api(titles=title, prop='imageinfo', iiprop='url|extmetadata|size', iiurlwidth=960)
        page = next(iter(result['query']['pages'].values()))
        record = metadata(page)
    record.pop('free_license', None)
    if not is_free(record) or not record['author']:
        raise ValueError('A confirmed free license and creator credit are required.')
    if re.search(r'AI.generated|Midjourney|DALL.E|Stable Diffusion', record['description'], re.I):
        raise ValueError('This task requires real photographs, not generated images.')
    data = read_url(record['download_url'])
    with Image.open(io.BytesIO(data)) as image:
        if image.width < 250 or image.height < 180:
            raise ValueError('Photograph is too small for a menu card.')
        image = ImageOps.exif_transpose(image).convert('RGB')
        image.thumbnail((960, 900), Image.Resampling.LANCZOS)
        path = ROOT / 'static' / 'images' / 'food' / f'{key}.webp'
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format='WEBP', quality=85, method=6)
        record.update(path=f'images/food/{key}.webp', width=image.width, height=image.height)
    record['changes'] = 'Resized and converted to WebP; displayed crops may vary by screen size.'
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not record['license_url'] and record['license'].lower() == 'public domain':
        record['license_url'] = 'https://creativecommons.org/publicdomain/mark/1.0/'
    manifest = read_manifest(collection)
    manifest['assets'][key] = record
    save_manifest(collection, manifest)
    return record


def local_json(path):
    path = (ROOT / path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Input plans must be inside the workspace.')
    return json.loads(path.read_text(encoding='utf-8'))


def sheet(collection):
    assets = read_manifest(collection)['assets']
    rows = (len(assets) + 4) // 5
    sheet_image = Image.new('RGB', (1100, max(1, rows) * 180), 'white')
    draw = ImageDraw.Draw(sheet_image)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 13)
    except OSError:
        font = ImageFont.load_default()
    for index, (key, asset) in enumerate(assets.items()):
        x, y = (index % 5) * 220, (index // 5) * 180
        with Image.open(ROOT / 'static' / asset['path']) as image:
            image.thumbnail((210, 140))
            sheet_image.paste(image, (x + (210 - image.width) // 2, y))
        draw.text((x + 4, y + 143), key.replace(collection + '-', '')[:30], fill='black', font=font)
    target = ROOT / '.browser-qa' / f'photos-{collection}.jpg'
    target.parent.mkdir(exist_ok=True)
    sheet_image.save(target, quality=88)
    return str(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('search')
    p.add_argument('query')
    p.add_argument('--limit', type=int, default=6)
    p = commands.add_parser('search-batch')
    p.add_argument('collection', choices=COLLECTIONS)
    p.add_argument('plan', help='Workspace JSON mapping asset keys to search queries.')
    p.add_argument('--limit', type=int, default=5)
    p = commands.add_parser('download')
    p.add_argument('collection', choices=COLLECTIONS)
    p.add_argument('key')
    p.add_argument('title')
    p = commands.add_parser('download-batch')
    p.add_argument('collection', choices=COLLECTIONS)
    p.add_argument('plan', help='Workspace JSON mapping asset keys to chosen File: titles.')
    p = commands.add_parser('sheet')
    p.add_argument('collection', choices=COLLECTIONS)
    args = parser.parse_args()
    if args.command == 'search':
        print(json.dumps(search(args.query, args.limit), ensure_ascii=True, indent=2))
    elif args.command == 'search-batch':
        path = ROOT / '.browser-qa' / f'{args.collection}-candidates.json'
        path.parent.mkdir(exist_ok=True)
        results = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        for key, query in local_json(args.plan).items():
            if key in results and results[key]:
                continue
            try:
                results[key] = search(query, args.limit)
                print(f'{key}: {len(results[key])} candidates', flush=True)
                path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception as exc:
                print(f'{key}: ERROR {type(exc).__name__}', flush=True)
        print(path, flush=True)
    elif args.command == 'download':
        print(json.dumps(download(args.collection, args.key, args.title), ensure_ascii=True, indent=2))
    elif args.command == 'download-batch':
        plan = local_json(args.plan)
        manifest = read_manifest(args.collection)
        missing_titles = [title for key, title in plan.items() if key not in manifest['assets'] or manifest['assets'][key]['title'] != title]
        try:
            preload_metadata(args.collection, missing_titles)
        except Exception as exc:
            print(f'Metadata batch deferred: {type(exc).__name__}; reusing cached candidates.', flush=True)
        for key, title in plan.items():
            existing = read_manifest(args.collection)['assets'].get(key)
            if existing and existing['title'] == title and (ROOT / 'static' / existing['path']).exists():
                print(f'{key}: cached', flush=True)
                continue
            try:
                download(args.collection, key, title)
                print(f'{key}: saved', flush=True)
            except Exception as exc:
                print(f'{key}: ERROR {type(exc).__name__}: {str(exc).encode("ascii", "replace").decode()}', flush=True)
    else:
        print(sheet(args.collection))


if __name__ == '__main__':
    main()
