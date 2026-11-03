"""Name-only source identities never reinterpret inventory codes as barcodes."""
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_catalog import builder, export, restoration_review, category_review


def source_record_review(source, title='Example Grape Soda', row=1):
    raw = list(csv.reader(io.StringIO(source.decode())))[row]
    catalog, _ = builder.build(source)
    product, variant = next((p, v) for p in catalog['products'] for v in p['variants']
                            if v['sku'] == raw[0])
    entry = dict(sku=raw[0], expectedUpc=variant['upc'], expectedSourceRow=row,
                 expectedRawFieldsSha256=builder.review_evidence_sha256(raw),
                 expectedPublicName=product['name'], expectedLabel=variant['label'],
                 expectedCategory=product['category'], expectedDepartment=product['department'],
                 proposedPublicTitle=title, reason='Reviewed original-record product wording only.',
                 identityBasis=builder.SOURCE_RECORD_NAME_IDENTITY)
    identity = dict(identifierNamespace='original_inventory_MAINUPC', barcodeMatchClaimed=False,
                    sku=entry['sku'], sourceRow=row, rawFieldsSha256=entry['expectedRawFieldsSha256'],
                    publicUpc=variant['upc'], expectedPublicName=product['name'], proposedPublicTitle=title)
    entry['evidence'] = [dict(kind=builder.SOURCE_RECORD_NAME_IDENTITY,
                              url='https://manufacturer.example/grape',
                              supports='Product wording reviewed against the original inventory record; no barcode match is claimed.',
                              sourceRecord=identity)]
    return {'schemaVersion': 1, 'reviews': [entry]}


class SourceRecordNameTests(unittest.TestCase):
    def source(self, code='123456'):
        return export({'MAINUPC': code, 'ItemName': 'Example Grape 12oz', 'DEPNAME': 'Juice'})

    def test_store_codes_remain_literal_and_only_title_changes(self):
        for code in ('123456', '000123', '123456789013'):
            with self.subTest(code=code):
                self.assertFalse(builder.valid_gtin(code))
                source = self.source(code)
                before, baseline = builder.build(source)
                after, audit = builder.build(source, public_name_reviews_manifest=source_record_review(source))
                self.assertEqual(after['products'][0]['name'], 'Example Grape Soda')
                self.assertEqual(after['products'][0]['variants'], before['products'][0]['variants'])
                self.assertEqual(after['products'][0]['variants'][0]['upc'], code)
                self.assertEqual(audit['records'][0]['rawFields'], baseline['records'][0]['rawFields'])
                self.assertEqual(audit['records'][0]['source'], baseline['records'][0]['source'])
                self.assertIn('not a verified barcode', audit['records'][0]['flags'][-1])
                self.assertNotIn('exact-UPC', audit['records'][0]['flags'][-1])

    def test_descriptive_inventory_identifier_stays_withheld(self):
        source = self.source('EXAMPLE12')
        review = source_record_review(source)
        self.assertEqual(review['reviews'][0]['expectedUpc'], '')
        catalog, audit = builder.build(source, public_name_reviews_manifest=review)
        self.assertEqual(catalog['products'][0]['variants'][0]['upc'], '')
        self.assertEqual(audit['records'][0]['rawFields'][1], 'EXAMPLE12')
        self.assertNotIn('EXAMPLE12', json.dumps(catalog))
        self.assertNotIn('EXAMPLE12', json.dumps(review))

    def test_opt_in_cannot_weaken_legacy_gtin_requirement(self):
        for code in ('123456', 'EXAMPLE12'):
            source = self.source(code)
            proposal = source_record_review(source)
            del proposal['reviews'][0]['identityBasis']
            del proposal['reviews'][0]['evidence'][0]['sourceRecord']
            with self.subTest(code=code), self.assertRaises(ValueError):
                builder.build(source, public_name_reviews_manifest=proposal)
        source = self.source('012345678905')
        with self.assertRaises(ValueError):
            builder.build(source, public_name_reviews_manifest=source_record_review(source))

    def test_unsupported_or_empty_original_identifiers_are_not_recovered(self):
        for code in ('', '123', '000000', ' 123456', '123456 ', '1.99', '$2', '012345678905'):
            source = self.source(code)
            with self.subTest(code=code), self.assertRaises(ValueError):
                builder.build(source, public_name_reviews_manifest=source_record_review(source))

    def test_every_source_and_public_guard_is_enforced(self):
        source = self.source()
        proposal = source_record_review(source)
        for key, value in [('sku', '2'), ('expectedUpc', '0123456'), ('expectedSourceRow', 2),
                           ('expectedRawFieldsSha256', 'a' * 64), ('expectedPublicName', 'Other Name'),
                           ('expectedLabel', '20 oz'), ('expectedCategory', 'food'),
                           ('expectedDepartment', 'Soda')]:
            bad = copy.deepcopy(proposal)
            bad['reviews'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, public_name_reviews_manifest=bad)
        for changed in (source.replace(b'123456', b'123457'), source.replace(b'19.99', b'29.99'),
                        source.replace(b'Example Grape', b'Example Peach')):
            with self.assertRaises(ValueError):
                builder.build(changed, public_name_reviews_manifest=proposal)

    def test_raw_fields_and_source_dictionary_must_agree(self):
        source = self.source('EXAMPLE12')
        raw = list(csv.reader(io.StringIO(source.decode())))[1]
        record = dict(zip(builder.EXPECTED_HEADERS, raw), sourceRow=1)
        public = builder.classify_record(record)[2]
        entry = source_record_review(source)['reviews'][0]
        for key, value in [('MAINUPC', 'EXAMPLE99'), ('ItemName', 'Example Peach 12oz'), ('ItemPrice', '99.99')]:
            changed = dict(record, **{key: value})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'consistent original source'):
                builder.apply_public_name_review(copy.deepcopy(public), changed, raw, entry)

    def test_typed_evidence_cannot_be_empty_or_claim_a_barcode_match(self):
        source = self.source('EXAMPLE12')
        proposal = source_record_review(source)
        mutations = [lambda e: e.update(evidence=[]),
                     lambda e: e['evidence'][0].pop('sourceRecord'),
                     lambda e: e['evidence'][0].update(sourceRecord={}),
                     lambda e: e['evidence'][0].update(kind='exact_upc_listing'),
                     lambda e: e['evidence'][0].update(url='file:///private/evidence'),
                     lambda e: e['evidence'][0]['sourceRecord'].update(barcodeMatchClaimed=True),
                     lambda e: e['evidence'][0]['sourceRecord'].update(barcodeMatchClaimed=0),
                     lambda e: e['evidence'][0]['sourceRecord'].update(sourceRow=True),
                     lambda e: e['evidence'][0]['sourceRecord'].update(identifierNamespace='consumer_gtin'),
                     lambda e: e['evidence'][0]['sourceRecord'].update(publicUpc='123456'),
                     lambda e: e['evidence'][0]['sourceRecord'].update(sku='2'),
                     lambda e: e['evidence'][0]['sourceRecord'].update(rawFieldsSha256='0' * 64),
                     lambda e: e['evidence'][0]['sourceRecord'].update(proposedPublicTitle='Example Peach Soda'),
                     lambda e: e['evidence'][0]['sourceRecord'].update(originalIdentifier='EXAMPLE12')]
        for mutate in mutations:
            bad = copy.deepcopy(proposal)
            mutate(bad['reviews'][0])
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                builder.build(source, public_name_reviews_manifest=bad)

    def test_unknown_fields_types_and_unsafe_proposals_reject(self):
        source = self.source()
        for key, value in [('identityBasis', None), ('identityBasis', True), ('identityBasis', 'unknown'),
                           ('expectedUpc', 123456), ('expectedUpc', []), ('expectedSourceRow', True),
                           ('expectedRawFieldsSha256', ''), ('privateRawCode', 'EXAMPLE12'),
                           ('proposedPublicTitle', 'Soda'), ('proposedPublicTitle', 'Example $5 Soda')]:
            bad = source_record_review(source)
            bad['reviews'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                builder.build(source, public_name_reviews_manifest=bad)

    def test_source_record_review_cannot_coexist_with_source_or_name_overrides(self):
        source = self.source()
        names = source_record_review(source)
        raw = list(csv.reader(io.StringIO(source.decode())))[1]
        correction = dict(sku='1', expectedUpc=raw[1],
                          expectedNameSha256=hashlib.sha256(raw[2].encode()).hexdigest(),
                          replaceTextFrom='Example', replaceTextTo='Example Brand',
                          evidence=[{'kind': 'manufacturer', 'url': 'https://manufacturer.example/grape'}])
        with self.assertRaisesRegex(ValueError, 'cannot coexist'):
            builder.build(source, public_name_reviews_manifest=names,
                          name_manifest={'schemaVersion': 1, 'corrections': [correction]})
        with self.assertRaisesRegex(ValueError, 'cannot coexist'):
            builder.build(source, public_name_reviews_manifest=names,
                          source_reviews_manifest=restoration_review(source, 'Example Grape 12oz'))
        with self.assertRaisesRegex(ValueError, 'cannot coexist'):
            builder.build(source, public_name_reviews_manifest=names,
                          category_reviews_manifest={'schemaVersion':1, 'reviews':[category_review(source)]})

    def test_fresh_evidence_cannot_override_current_code_or_source_projection(self):
        source = self.source('EXAMPLE12')
        proposal = source_record_review(source)
        entry = proposal['reviews'][0]
        entry['expectedUpc'] = '123456'
        entry['evidence'][0]['sourceRecord']['publicUpc'] = '123456'
        with self.assertRaisesRegex(ValueError, 'current identity mismatch'):
            builder.build(source, public_name_reviews_manifest=proposal)
        source = self.source('123456')
        proposal = source_record_review(source)
        entry = proposal['reviews'][0]
        entry['expectedUpc'] = ''
        entry['evidence'][0]['sourceRecord']['publicUpc'] = ''
        with self.assertRaisesRegex(ValueError, 'current identity mismatch'):
            builder.build(source, public_name_reviews_manifest=proposal)

    def test_source_record_title_cannot_restore_unretained_merchandise(self):
        source = self.source()
        proposal = source_record_review(source)['reviews'][0]
        raw = list(csv.reader(io.StringIO(source.decode())))[1]
        record = dict(zip(builder.EXPECTED_HEADERS, raw), sourceRow=1)
        with self.assertRaisesRegex(ValueError, 'retained source merchandise'):
            builder.apply_public_name_review(None, record, raw, proposal)

    def test_projected_private_evidence_preserves_behavior_and_hides_sources(self):
        source = self.source('EXAMPLE12')
        original = source_record_review(source)
        evidence = original['reviews'][0]['evidence']
        digest = builder.review_evidence_sha256(evidence)
        projected = copy.deepcopy(original)
        projected['reviews'][0]['evidence'] = {'privateEvidenceSha256': digest}
        expected = builder.build(source, public_name_reviews_manifest=original)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.json'
            ledger = dict(schemaVersion=1, kind=builder.PRIVATE_REVIEW_EVIDENCE_KIND,
                          records={digest: evidence})
            path.write_text(json.dumps(ledger))
            with patch.dict(os.environ, QUICKSTOP_PRIVATE_REVIEW_EVIDENCE=str(path)):
                self.assertEqual(builder.build(source, public_name_reviews_manifest=projected), expected)
                ledger['records'][digest][0]['sourceRecord']['barcodeMatchClaimed'] = True
                path.write_text(json.dumps(ledger))
                with self.assertRaisesRegex(ValueError, 'missing or does not match'):
                    builder.build(source, public_name_reviews_manifest=projected)
        self.assertNotIn('https://', json.dumps(projected))
        self.assertNotIn('EXAMPLE12', json.dumps(projected))


if __name__ == '__main__':
    unittest.main()
