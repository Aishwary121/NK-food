"""Curated local stock photography with the original creator/license credits.

The shipped photographs are loaded without contacting an external image service.
An administrator's dish photo always takes precedence over the stock catalog.
"""
import json
from functools import lru_cache
from pathlib import Path

from django.templatetags.static import static


@lru_cache(maxsize=1)
def photo_catalog():
    assets, dishes = {}, {}
    folder = Path(__file__).parent / 'data' / 'photos'
    for path in sorted(folder.glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        overlap = assets.keys() & data['assets'].keys()
        if overlap:
            raise ValueError(f'Duplicate photo asset keys in {path.name}: {overlap}')
        if dishes.keys() & data['dishes'].keys():
            raise ValueError(f'Duplicate dish photo mappings in {path.name}.')
        assets.update(data['assets'])
        dishes.update(data['dishes'])
    return assets, dishes


def stock_photo(name):
    assets, dishes = photo_catalog()
    key = dishes.get(name)
    if key is None:
        return None
    asset = assets.get(key)
    if asset is None:
        return None
    return {**asset, 'key': key, 'url': static(asset['path'])}


def photo_credits():
    assets, dishes = photo_catalog()
    rows = []
    for key, asset in assets.items():
        used_by = sorted(name for name, asset_key in dishes.items() if asset_key == key)
        if used_by:
            rows.append({**asset, 'key': key, 'url': static(asset['path']), 'dishes': used_by})
    return sorted(rows, key=lambda row: row['dishes'][0])
