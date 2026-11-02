"""Synthetic literal-GTIN name restoration never verifies a selling unit."""
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

from test_catalog import builder, export, restoration_review, report_manifest
from test_name_only_restoration import project_evidence


CODE = '012345678905'
TITLE = 'Example Brand Barbecue Potato Chips'


def fixture(source=None, title=TITLE, row=1):
    source = source or export({'MAINUPC': CODE, 'ItemName': CODE, 'DEPNAME': 'Snacks',
                               'ItemPrice': '7.31', 'TotalQty_Multi': '-4'})
    raw = list(csv.reader(io.StringIO(source.decode())))[row]
    raw_hash = builder.review_evidence_sha256(raw)
    review = {'sku': raw[0], 'expectedUpc': raw[1],
              'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
              'expectedSourceRecordSha256': raw_hash, 'expectedSourceRow': row,
              'action': 'restore_verified_numeric_name',
              'verificationMethod': builder.NUMERIC_NAME_RESTORATION_METHOD,
              'reviewedOn': '2024-02-29', 'reason': 'Restore only a reviewed literal code name.'}
    public_title = {'sku': raw[0], 'expectedUpc': raw[1], 'expectedSourceRow': row,
                    'expectedRawFieldsSha256': raw_hash, 'expectedPublicName': raw[2],
                    'expectedLabel': 'Size needs confirmation',
                    'expectedCategory': builder.category_for(raw[4]), 'expectedDepartment': raw[4],
                    'proposedPublicTitle': title, 'reason': 'Exact retailer name-only proof; unit unresolved.',
                    'evidence': [{'kind': 'retailer_exact_upc_name',
                                  'url': 'https://retailer.example.test/synthetic-chips',
                                  'supports': f'Literal UPC {raw[1]} identifies {title}; name only. '
                                              'Net quantity and packaging remain unresolved.'}]}
    return source, {'source_reviews_manifest': {'schemaVersion': 1, 'reviews': [review]},
                    'public_name_reviews_manifest': {'schemaVersion': 1, 'reviews': [public_title]}}


def by_sku(catalog):
    return {v['sku']: (p, v) for p in catalog['products'] for v in p['variants']}


class NumericNameRestorationTests(unittest.TestCase):
    def test_name_only_restores_exact_source_preserving_neighbors_and_unknown_unit(self):
        source = export({'MAINUPC': CODE, 'ItemName': CODE, 'DEPNAME': 'Snacks',
                         'ItemPrice': '7.31', 'TotalQty_Multi': '-4'},
                        {'MAINUPC': CODE, 'ItemName': TITLE, 'DEPNAME': 'Snacks'})
        baseline, _ = builder.build(source)
        self.assertEqual(baseline['counts']['needsReview'], 1)
        source, kwargs = fixture(source)
        original_options = copy.deepcopy(kwargs)
        catalog, audit = builder.build(source, **kwargs)
        items = by_sku(catalog)
        self.assertEqual(items['1'][0]['name'], TITLE)
        self.assertEqual(items['1'][1], {'sku': '1', 'upc': CODE, 'sourceRow': 1,
                                       'label': 'Size needs confirmation', 'image': None})
        self.assertEqual(items['2'], by_sku(baseline)['2'])
        self.assertNotEqual(items['1'][0]['id'], items['2'][0]['id'])
        self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])
        self.assertEqual(audit['records'][0]['source']['ItemName'], CODE)
        self.assertEqual(audit['records'][0]['source']['PackName'], '')
        self.assertEqual(audit['records'][0]['source']['ItemPrice'], '7.31')
        self.assertEqual(audit['records'][0]['numericNameRestoration'], {
            'originalNumericName': CODE, 'verificationMethod': builder.NUMERIC_NAME_RESTORATION_METHOD,
            'sellingUnitVerified': False, 'photoApproved': False})
        self.assertEqual(kwargs, original_options)

    def test_original_literal_gtin_lengths_and_leading_zeroes_stay_exact(self):
        for body in ['1234567', '01234567890', '123456789012', '1234567890123']:
            total = sum(int(digit) * (3 if index % 2 == 0 else 1)
                        for index, digit in enumerate(reversed(body)))
            code = body + str((10 - total % 10) % 10)
            source, kwargs = fixture(export({'MAINUPC': code, 'ItemName': code, 'DEPNAME': 'Snacks'}))
            with self.subTest(code=code):
                catalog, _ = builder.build(source, **kwargs)
                self.assertEqual(by_sku(catalog)['1'][1]['upc'], code)

    def test_two_restored_rows_never_merge_even_with_the_same_name_and_code(self):
        source = export(*[{'MAINUPC': CODE, 'ItemName': CODE, 'DEPNAME': 'Snacks'} for _ in range(2)])
        _, first = fixture(source)
        _, second = fixture(source, row=2)
        for key in first:
            first[key]['reviews'] += second[key]['reviews']
        catalog, _ = builder.build(source, **first)
        self.assertEqual({tuple(v['sku'] for v in p['variants']) for p in catalog['products']}, {('1',), ('2',)})

    def test_different_numeric_title_amount_shortcut_and_bad_codes_fail(self):
        for name, upc, department in [('012345678912', CODE, 'Snacks'), ('57', CODE, 'Snacks'),
                (CODE.zfill(14), CODE, 'Snacks'), ('0.50', CODE, 'Snacks'),
                ('$0.50', CODE, 'Snacks'), ('57', '57', 'Snacks'),
                ('012345678906', '012345678906', 'Snacks'),
                ('000000000000', '000000000000', 'Snacks'),
                (CODE, CODE, 'ShortCut'), (CODE, CODE, 'Tax'), (CODE, CODE, 'Instant'),
                (' ' + CODE, CODE, 'Snacks'), (CODE, CODE + ' ', 'Snacks')]:
            source, kwargs = fixture(export({'MAINUPC': upc, 'ItemName': name, 'DEPNAME': department}))
            with self.subTest(name=name, upc=upc, department=department), self.assertRaises(ValueError):
                builder.build(source, **kwargs)

    def test_pack_metadata_or_non_numeric_names_are_not_eligible(self):
        for name, pack in [(CODE, '250 g'), (CODE, '4PK'), (CODE, ' '),
                           ('NEW ITEM', ''), ('Example Chips', ''), ('0.73 candy', '')]:
            source, kwargs = fixture(export({'MAINUPC': CODE, 'ItemName': name, 'PackName': pack,
                                             'DEPNAME': 'Snacks'}))
            with self.subTest(name=name, pack=pack), self.assertRaises(ValueError):
                builder.build(source, **kwargs)

    def test_every_source_guard_method_row_and_calendar_date_is_required(self):
        source, kwargs = fixture()
        for key, value in {'expectedUpc': CODE.zfill(14), 'expectedNameSha256': '0' * 64,
                'expectedSourceRecordSha256': '0' * 64, 'expectedSourceRow': 2,
                'verificationMethod': builder.NAME_ONLY_RESTORATION_METHOD,
                'reviewedOn': '2026-02-30'}.items():
            changed = copy.deepcopy(kwargs); changed['source_reviews_manifest']['reviews'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)
        for key in kwargs['source_reviews_manifest']['reviews'][0]:
            changed = copy.deepcopy(kwargs); del changed['source_reviews_manifest']['reviews'][0][key]
            with self.subTest(missing=key), self.assertRaises(ValueError): builder.build(source, **changed)
        for field in ['expectedSourceRow']:
            changed = copy.deepcopy(kwargs); changed['source_reviews_manifest']['reviews'][0][field] = True
            with self.assertRaises(ValueError): builder.build(source, **changed)
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            builder.build(source.replace(b'7.31', b'7.32'), **kwargs)

    def test_matching_public_title_guard_cannot_be_omitted_or_changed(self):
        source, kwargs = fixture()
        changed = copy.deepcopy(kwargs); changed.pop('public_name_reviews_manifest')
        with self.assertRaisesRegex(ValueError, 'matching guarded'): builder.build(source, **changed)
        for key, value in {'expectedPublicName': 'Generic chips', 'expectedSourceRow': 2,
                'expectedRawFieldsSha256': '0' * 64, 'expectedLabel': '750 mL',
                'expectedCategory': 'essentials', 'expectedDepartment': 'Juice'}.items():
            changed = copy.deepcopy(kwargs); changed['public_name_reviews_manifest']['reviews'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_retailer_name_proof_must_match_both_the_code_and_the_actual_title(self):
        source, kwargs = fixture()
        for evidence in [[], [{'kind': 'manufacturer', 'url': 'https://example.test/' + CODE,
                              'supports': TITLE}],
                [{'kind': 'retailer_exact_upc_name', 'url': 'https://retailer.example.test/' + CODE,
                  'supports': 'Another Product'}],
                [{'kind': 'retailer_exact_upc_name', 'url': 'https://retailer.example.test/item',
                  'supports': TITLE}],
                [{'kind': 'retailer_exact_upc_name',
                  'url': 'https://retailer.example.test/' + CODE.zfill(14),
                  'supports': f'Padded alias {CODE.zfill(14)} identifies {TITLE}.'}]]:
            changed = copy.deepcopy(kwargs); changed['public_name_reviews_manifest']['reviews'][0]['evidence'] = evidence
            with self.subTest(evidence=evidence), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_quantity_container_pack_and_proof_smuggling_in_title_fail(self):
        for title in ['Example Chips 8oz', 'Example Chips Four Pack', 'Example Chips Bag',
                      'Example Chips Family Size', 'Example Chips 80 Proof', 'Example Chips Single',
                      'Example Chips Carton', 'Example Chips Gallon', 'Example Chips Proof',
                      'Example Chips multipack', 'Example Chips Multi-Pack',
                      'Example Chips FamilySize', 'Example Chips TwoPack']:
            source, kwargs = fixture(title=title)
            with self.subTest(title=title), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_unit_photo_resolution_and_sibling_fields_are_not_in_this_action(self):
        source, kwargs = fixture()
        for key, value in {'verifiedLabel': '250 g', 'identityResolution': {},
                'foodSizeResolution': {}, 'imageIdentityReview': {},
                'expectedResultingGroupSkus': ['1', '2'], 'forceSizeNeedsConfirmation': False,
                'sanitizedSourceName': TITLE}.items():
            changed = copy.deepcopy(kwargs); changed['source_reviews_manifest']['reviews'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            builder.build(source, image_manifest={'schemaVersion': 1, 'images': [{'sku': '1'}]}, **kwargs)
        legacy = restoration_review(source, TITLE)
        with self.assertRaises(ValueError): builder.build(source, source_reviews_manifest=legacy)

    def test_joined_quantity_and_container_words_cannot_smuggle_a_selling_unit(self):
        compounds = ['TwelvePack', 'SingleServe', 'QuartBottle', 'TwentyPack', 'DozenBags', 'TwelveCases',
                     'SingleServing', 'LitreCarton', 'TwelveOunce', 'MultiPack', 'FamilySize']
        for compound in compounds:
            for spelling in [compound, compound.lower(), compound.upper()]:
                title = 'Example Brand ' + spelling + ' Chips'
                source, kwargs = fixture(title=title)
                with self.subTest(title=title), self.assertRaisesRegex(ValueError, 'quantity, container or strength'):
                    builder.build(source, **kwargs)
        for brand in ['Quartet', 'Pintastic', 'Singlehanded', 'Twelvefold', 'Bottlebrush']:
            title = 'Example Brand ' + brand + ' Chips'
            source, kwargs = fixture(title=title)
            with self.subTest(brand=brand):
                catalog, _ = builder.build(source, **kwargs)
                self.assertEqual(by_sku(catalog)['1'][0]['name'], title)
                self.assertEqual(by_sku(catalog)['1'][1]['label'], 'Size needs confirmation')
                self.assertIsNone(by_sku(catalog)['1'][1]['image'])

    def test_name_or_identity_override_cannot_coexist_and_report_origin_cannot_restore(self):
        source, kwargs = fixture(); r = kwargs['source_reviews_manifest']['reviews'][0]
        base = {'sku': '1', 'expectedUpc': CODE, 'expectedNameSha256': r['expectedNameSha256'],
                'evidence': [{'url': 'https://example.test/name'}]}
        for names in [{'schemaVersion': 1, 'corrections': [dict(base, replaceTextFrom=CODE,
                      replaceTextTo=TITLE, scope='brand_spelling_only')]},
                      {'schemaVersion': 1, 'identityReviews': [dict(base, action='quarantine', reason='Conflict')]},
                      {'schemaVersion': 1, 'nameVerifications': [dict(base, expectedPublicName=TITLE,
                        expectedLabel='Size needs confirmation', verifiedOn='2024-02-29',
                        verification=dict.fromkeys(['brand', 'expression', 'upc', 'visual'], True),
                        evidence=[{'kind': kind, 'url': 'https://example.test/' + kind} for kind in
                                  ['manufacturer', 'exact_upc_listing', 'inspected_package_photo']])]}]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                builder.build(source, name_manifest=names, **kwargs)
        category = {'sku': '1', 'expectedUpc': CODE, 'expectedNameSha256': r['expectedNameSha256'],
                    'expectedSourceRecordSha256': r['expectedSourceRecordSha256'],
                    'expectedDepartment': 'Snacks', 'expectedPublicName': CODE,
                    'expectedLabel': 'Size needs confirmation', 'expectedCategory': 'food',
                    'category': 'essentials', 'reviewedOn': '2024-02-29', 'reason': 'Conflicting override',
                    'evidence': [{'kind': 'retailer', 'url': 'https://example.test/category'}]}
        with self.assertRaisesRegex(ValueError, 'cannot coexist'):
            builder.build(source, category_reviews_manifest={'schemaVersion': 1, 'reviews': [category]}, **kwargs)
        with self.assertRaisesRegex(ValueError, 'absent from the original source'):
            builder.build(export(), report_additions_manifest=report_manifest({'sku': '1', 'name': TITLE,
                'pack': '', 'department': 'Snacks'}), **kwargs)

    def test_private_projection_retains_the_same_name_only_guards(self):
        source, kwargs = fixture(); expected, _ = builder.build(source, **kwargs)
        records = {}; projected = project_evidence(kwargs['public_name_reviews_manifest'], records)
        self.assertNotIn('retailer.example.test', json.dumps(projected))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review-evidence.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND,
                                        'records': records}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                actual, audit = builder.build(source, source_reviews_manifest=kwargs['source_reviews_manifest'],
                                               public_name_reviews_manifest=projected)
                self.assertEqual(actual, expected)
                changed = copy.deepcopy(projected); changed['reviews'][0]['expectedSourceRow'] = 2
                with self.assertRaises(ValueError):
                    builder.build(source, source_reviews_manifest=kwargs['source_reviews_manifest'],
                                  public_name_reviews_manifest=changed)
        self.assertNotIn('retailer.example.test', json.dumps(actual))
        self.assertNotIn('7.31', json.dumps(actual))
        self.assertEqual(audit['records'][0]['source']['ItemPrice'], '7.31')


if __name__ == '__main__':
    unittest.main()
