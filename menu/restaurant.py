import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def restaurant_details():
    """Printed restaurant information transcribed from the supplied menu photos."""
    return json.loads((Path(__file__).parent / 'data' / 'restaurant.json').read_text(encoding='utf-8'))
