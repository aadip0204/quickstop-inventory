"""Deployment integrity must survive concurrent asset verification."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error


SPEC = importlib.util.spec_from_file_location(
    'deployment_verifier', Path(__file__).resolve().parents[1] / 'scripts/verify_deployment.py')
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class DeploymentIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = 'https://example.test/catalog/'
        entry = {
            'sku': '001', 'expectedUpc': '001234567890',
            'expectedName': 'Exact product', 'expectedLabel': '750 mL',
            'src': 'images/products/001.jpg', 'alt': 'Exact product, 750 mL',
            'sha256': 'unused-by-byte-verifier', 'verifiedOn': '2026-10-31',
            'verification': {}, 'usageBasis': 'user_authorized_direct_reference',
            'attributionRequired': False,
        }
        image = {key: entry[key] for key in VERIFIER.DIRECT_PUBLIC_IMAGE_FIELDS}
        catalog = {'counts': {'publicVariants': 1},
                   'products': [{'variants': [{'sku': '001', 'image': image}]}]}
        self.assets = {
            'index.html': b'<main>Catalog</main>', 'style.css': b'body {}',
            'script.js': b'const catalog = true;', 'images/logo.png': b'logo',
            'images/products/001.jpg': b'exact product photo',
            'catalog.json': json.dumps(catalog).encode(),
            'data/image-manifest.json': json.dumps({'schemaVersion': 1, 'images': [entry]}).encode(),
        }
        for name, content in self.assets.items():
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        self.served = dict(self.assets)

    def fetch(self, url):
        name = url.removeprefix(self.base)
        if name not in self.served:
            raise urllib.error.HTTPError(url, 404, 'Not found', {}, None)
        return self.served[name]

    def test_all_assets_checked_and_legacy_urls_retired(self):
        with patch.object(VERIFIER, 'fetch', self.fetch):
            result = VERIFIER.verify(self.base, self.root)
        checked = {row['path'] for row in result['assetChecks'] if row.get('matchesCheckout')}
        self.assertEqual(checked, set(self.assets))
        self.assertEqual(result['verifiedPhotoAssetsServed'], 1)
        self.assertEqual(len([row for row in result['assetChecks'] if row.get('status') == 404]), len(VERIFIER.RETIRED_PATHS))

    def test_build_only_review_file_still_served_fails(self):
        self.served['data/source-reviews.json'] = b'review evidence'
        with patch.object(VERIFIER, 'fetch', self.fetch):
            with self.assertRaisesRegex(AssertionError, 'Retired public artifact is still served'):
                VERIFIER.verify(self.base, self.root)

    def test_wrong_photo_bytes_fail_even_when_core_assets_match(self):
        self.served['images/products/001.jpg'] = b'wrong photo'
        with patch.object(VERIFIER, 'fetch', self.fetch):
            with self.assertRaisesRegex(AssertionError, '001.jpg does not match'):
                VERIFIER.verify(self.base, self.root)

    def test_retired_price_file_still_served_fails(self):
        self.served['products.json'] = b'legacy data'
        with patch.object(VERIFIER, 'fetch', self.fetch):
            with self.assertRaisesRegex(AssertionError, 'Retired public artifact is still served'):
                VERIFIER.verify(self.base, self.root)

    def test_mismatched_catalog_manifest_assignments_fail(self):
        manifest_path = self.root / 'data/image-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['images'] = []
        manifest_path.write_text(json.dumps(manifest))
        with patch.object(VERIFIER, 'fetch', self.fetch):
            with self.assertRaisesRegex(AssertionError, 'assignments differ'):
                VERIFIER.verify(self.base, self.root)
