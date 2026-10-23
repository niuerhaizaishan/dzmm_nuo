"""Fetch publisher cover thumbnails for local UI review, not a source release."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import io
import json
import requests
from PIL import Image

ROOT = Path(__file__).parent / 'publish' / 'assets'
TITLES = [('frieren', '1974725766'), ('witch', '1974715469'),
          ('blue', '1974734625'), ('skip', '1974734641'),
          ('yotsuba', '1974738930'), ('summer', '1974709930')]

def fetch(item):
    slug, isbn = item
    session = requests.Session()
    session.trust_env = False
    try:
        url = 'https://dw9to29mmj727.cloudfront.net/products/' + isbn + '.jpg'
        response = session.get(url, timeout=12)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        path = ROOT / (slug + '.jpg')
        image.convert('RGB').save(path, quality=90)
        return {'id': slug, 'file': path.name, 'source': url, 'dimensions': list(image.size)}
    except Exception as error:
        return {'id': slug, 'error': type(error).__name__}

if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(fetch, TITLES))
    (ROOT / 'sources.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=True))
