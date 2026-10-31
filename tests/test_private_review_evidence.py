"""Private evidence references must preserve guarded behavior and fail closed."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from test_catalog import builder, export


class PrivateReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'evidence.json'
        self.evidence = [{'kind': 'exact_upc_record', 'url': 'https://manufacturer.example/001234567890.jpg'}]
        self.digest = builder.review_evidence_sha256(self.evidence)
        self.reference = {'privateEvidenceSha256': self.digest}
        self.ledger = {'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND,
                       'records': {self.digest: self.evidence}}
        self.path.write_text(json.dumps(self.ledger))

    def test_projected_review_preserves_build_and_source_guard(self):
        name = 'Example Vodka 750ml'
        source = export({'ItemName': name})
        review = {'sku': '1', 'expectedUpc': '001234567890',
                  'expectedNameSha256': hashlib.sha256(name.encode()).hexdigest(),
                  'replaceTextFrom': name, 'replaceTextTo': 'Example Brand Vodka 750ml',
                  'evidence': self.evidence}
        original = {'schemaVersion': 1, 'corrections': [review]}
        projected = copy.deepcopy(original)
        projected['corrections'][0]['evidence'] = self.reference
        expected, _ = builder.build(source, name_manifest=original)
        with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(self.path)}):
            actual, _ = builder.build(source, name_manifest=projected)
            self.assertEqual(actual, expected)
            projected['corrections'][0]['expectedUpc'] = '999999999999'
            with self.assertRaises(ValueError):
                builder.build(source, name_manifest=projected)
        self.assertNotIn('manufacturer.example', json.dumps(projected))

    def test_missing_ledger_is_not_a_validation_bypass(self):
        with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': ''}):
            with self.assertRaisesRegex(ValueError, 'require QUICKSTOP_PRIVATE_REVIEW_EVIDENCE'):
                builder.expand_private_review_evidence(self.reference)

    def test_tampered_or_missing_record_is_rejected(self):
        for records in ({}, {self.digest: [{'kind': 'changed', 'url': 'https://other.example/photo.jpg'}]}):
            self.path.write_text(json.dumps(dict(self.ledger, records=records)))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(self.path)}):
                with self.assertRaisesRegex(ValueError, 'missing or does not match'):
                    builder.expand_private_review_evidence(self.reference)

    def test_ledger_inside_public_repository_is_rejected(self):
        with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(builder.ROOT / 'catalog.json')}):
            with self.assertRaisesRegex(ValueError, 'OUTSIDE'):
                builder.expand_private_review_evidence(self.reference)

    def test_current_review_files_do_not_expose_direct_photo_urls(self):
        image_url = re.compile(r'https?://[^\s"<>]+(?:\.(?:jpe?g|png|webp|gif|avif|svg)(?:[?#]|$)|/is/image/)', re.I)
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
        for name in ['source-reviews', 'name-corrections', 'category-reviews', 'public-name-reviews']:
            manifest = json.loads((builder.ROOT / 'data' / (name + '.json')).read_text())
            self.assertFalse(any(image_url.search(value) for value in strings(manifest)), name)
