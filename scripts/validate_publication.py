"""Check local page links, fragments, images, and published media hashes."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.links, self.ids = [], set()
        self.feed(path.read_text(encoding='utf-8'))

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            assert attrs['id'] not in self.ids, 'Duplicate element ID'
            self.ids.add(attrs['id'])
        for key in ['href', 'src', 'poster']:
            if key in attrs:
                self.links.append(attrs[key])
        if tag == 'img':
            assert attrs.get('alt'), 'Image has no alternative text'


pages = {path: Page(path) for path in DOCS.glob('*.html')}
for path, page in pages.items():
    for value in page.links:
        url = urlsplit(value)
        if url.scheme or url.netloc:
            assert url.scheme == 'https', 'Unexpected external URL scheme'
            continue
        target = (path.parent / unquote(url.path)).resolve() if url.path else path
        assert target.is_relative_to(DOCS), 'Link leaves public directory'
        assert target.is_file(), f'Missing local link in {path.name}'
        if url.fragment and target.suffix == '.html':
            assert url.fragment in pages[target].ids, f'Missing fragment in {path.name}'

manifest = json.loads((DOCS/'run.json').read_text())
for name, info in manifest['assets'].items():
    data = (DOCS/'assets'/name).read_bytes()
    assert len(data) == info['bytes'], f'Incorrect size: {name}'
    assert hashlib.sha256(data).hexdigest() == info['sha256'], f'Incorrect hash: {name}'

print(f'PASS: {len(pages)} HTML pages, local links/fragments, alternative text, and {len(manifest["assets"])} media hashes')
