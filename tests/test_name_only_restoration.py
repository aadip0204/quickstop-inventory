"""Synthetic exact-code name-only restoration; no real product or unit proof."""
import copy
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_catalog import builder, export, restoration_review, report_manifest, category_review, public_name_review
SPEC = importlib.util.spec_from_file_location('name_restoration_projection',
    Path(__file__).resolve().parents[1] / 'scripts/project_review_evidence.py')
projection = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {'build_catalog': builder}):
    SPEC.loader.exec_module(projection)
project_evidence = projection.project_evidence


TITLE = 'Example Brand Tangy Candy'


def fixture(source=None, descriptor='candy', title=TITLE, row=1):
    source = source or export({'ItemName': '0.73 candy', 'MAINUPC': '012345678905',
                               'DEPNAME': 'Snacks', 'ItemPrice': '7.31', 'TotalQty_Multi': '-4'})
    raw = list(csv.reader(io.StringIO(source.decode())))[row]
    reviews = restoration_review(source, descriptor, row=row)
    reviews['reviews'][0].update(verificationMethod=builder.NAME_ONLY_RESTORATION_METHOD,
                                 reviewedOn='2024-02-29')
    titles = {'schemaVersion': 1, 'reviews': [{
        'sku': raw[0], 'expectedUpc': raw[1], 'expectedSourceRow': row,
        'expectedRawFieldsSha256': builder.review_evidence_sha256(raw),
        'expectedPublicName': descriptor, 'expectedLabel': 'Size needs confirmation',
        'expectedCategory': builder.category_for(raw[4]), 'expectedDepartment': raw[4],
        'proposedPublicTitle': title, 'reason': 'Exact literal consumer code proves only this specific product name.',
        'evidence': [{'kind': 'retailer_exact_upc_name', 'url': 'https://retailer.example.test/synthetic-candy',
                      'supports': f'Own literal UPC {raw[1]} and inspected matching printed barcode establish the name only; selling unit and package weight stay unresolved.'}]}]}
    return source, {'source_reviews_manifest': reviews, 'public_name_reviews_manifest': titles}


def by_sku(catalog):
    return {v['sku']: (p, v) for p in catalog['products'] for v in p['variants']}


class NameOnlyRestorationTests(unittest.TestCase):
    def test_exact_name_restoration_preserves_raw_neighbor_unknown_unit_and_isolation(self):
        source = export({'ItemName': '0.73 candy', 'MAINUPC': '012345678905', 'DEPNAME': 'Snacks',
                         'ItemPrice': '7.31', 'TotalQty_Multi': '-4'},
                        {'ItemName': TITLE, 'MAINUPC': '012345678912', 'DEPNAME': 'Snacks'})
        baseline, _ = builder.build(source)
        source, kwargs = fixture(source)
        original = copy.deepcopy(kwargs)
        catalog, audit = builder.build(source, **kwargs)
        items = by_sku(catalog)
        self.assertEqual(items['1'][0]['name'], TITLE)
        self.assertEqual(items['1'][1], {'sku': '1', 'upc': '012345678905',
            'label': 'Size needs confirmation', 'sourceRow': 1, 'image': None})
        self.assertEqual(items['2'], by_sku(baseline)['2'])
        self.assertNotEqual(items['1'][0]['id'], items['2'][0]['id'])
        self.assertEqual({tuple(v['sku'] for v in p['variants']) for p in catalog['products']}, {('1',), ('2',)})
        self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])
        self.assertEqual(audit['records'][0]['source']['ItemName'], '0.73 candy')
        self.assertEqual(audit['records'][0]['source']['ItemPrice'], '7.31')
        self.assertEqual(audit['records'][0]['source']['TotalQty_Multi'], '-4')
        self.assertEqual(audit['records'][0]['sanitizedSourceName'], 'candy')
        self.assertEqual(audit['records'][0]['publicNameCorrection']['expectedPublicName'], 'candy')
        self.assertEqual(audit['records'][0]['nameOnlyRestoration'],
                         {'sanitizedDescriptor': 'candy', 'sellingUnitVerified': False, 'photoApproved': False})
        self.assertEqual(catalog['counts']['needsReview'], baseline['counts']['needsReview'] - 1)
        self.assertEqual(kwargs, original)

    def test_legacy_generic_cleanup_and_title_review_alone_cannot_restore(self):
        source, kwargs = fixture()
        raw = list(csv.DictReader(io.StringIO(source.decode())))[0]
        raw['ItemName'] = 'candy'; raw['sourceRow'] = 1
        self.assertIsNone(builder.classify_record(raw)[2])
        legacy = copy.deepcopy(kwargs)
        legacy['source_reviews_manifest']['reviews'][0].pop('verificationMethod')
        with self.assertRaisesRegex(ValueError, 'did not produce safe merchandise'):
            builder.build(source, **legacy)
        with self.assertRaises(ValueError):
            builder.build(source, public_name_reviews_manifest=kwargs['public_name_reviews_manifest'])

    def test_other_sku_category_and_name_reviews_keep_normal_order(self):
        source = export({'ItemName': '0.73 candy', 'MAINUPC': '012345678905', 'DEPNAME': 'Snacks'},
                        {'ItemName': 'Example Dog Food 8oz', 'MAINUPC': '012345678912', 'DEPNAME': 'Snacks'})
        source, kwargs = fixture(source)
        other_title = public_name_review(source, 'Example Brand Pet Food', row=2, category='essentials')
        kwargs['public_name_reviews_manifest']['reviews'] += other_title['reviews']
        kwargs['category_reviews_manifest'] = {'schemaVersion': 1, 'reviews': [category_review(source, 'essentials', row=2)]}
        catalog, audit = builder.build(source, **kwargs)
        self.assertEqual(by_sku(catalog)['1'][0]['name'], TITLE)
        self.assertEqual(by_sku(catalog)['1'][1]['label'], 'Size needs confirmation')
        other, variant = by_sku(catalog)['2']
        self.assertEqual((other['name'], other['category'], other['department'], variant['label']),
                         ('Example Brand Pet Food', 'essentials', 'Snacks', '8 oz'))
        self.assertEqual(audit['categoryReviews'], 1)
        self.assertEqual(audit['publicNameCorrections'], 2)

    def test_only_edge_annotations_may_be_cleaned_without_dropping_identity_or_pack(self):
        for original in ['candy 0.73', '$ 0.73 candy', 'candy 0.73 $', '€0.73 candy', '0.73candy']:
            source, kwargs = fixture(export({'ItemName': original, 'MAINUPC': '012345678905', 'DEPNAME': 'Snacks'}))
            with self.subTest(original=original):
                catalog, audit = builder.build(source, **kwargs)
                self.assertEqual(by_sku(catalog)['1'][0]['name'], TITLE)
                self.assertEqual(by_sku(catalog)['1'][1]['label'], 'Size needs confirmation')
                self.assertEqual(audit['records'][0]['source']['ItemName'], original)
        for original in ['0.73 OtherBrand candy', 'OtherBrand candy 0.73', 'candy 4oz 0.73',
                         '0.73 orange candy', 'candy', 'candy 2 for 0.73', '0.73 candy 12ct',
                         '0.73 candy unknown', '0.73 candy?']:
            source, kwargs = fixture(export({'ItemName': original, 'MAINUPC': '012345678905', 'DEPNAME': 'Snacks'}))
            with self.subTest(original=original), self.assertRaises(ValueError):
                builder.build(source, **kwargs)
        for pack in [' ', 'Single', 'each', '12ct', '8oz', 'case']:
            source, kwargs = fixture(export({'ItemName': '0.73 candy', 'MAINUPC': '012345678905',
                                             'DEPNAME': 'Snacks', 'PackName': pack}))
            with self.subTest(pack=pack), self.assertRaises(ValueError):
                builder.build(source, **kwargs)

    def test_literal_valid_gtin_not_price_alias_or_normalization_is_required(self):
        for upc in ['', '0.73', '$0.73', 'merchant-button', '012345678906', ' 012345678905',
                    '012345678905 ', '０１２３４５６７８９０５']:
            source, kwargs = fixture(export({'ItemName': '0.73 candy', 'MAINUPC': upc, 'DEPNAME': 'Snacks'}))
            with self.subTest(upc=upc), self.assertRaises(ValueError):
                builder.build(source, **kwargs)

    def test_exact_opt_in_unknown_size_true_and_no_identity_resolution_are_required(self):
        source, kwargs = fixture()
        for key, value in [('verificationMethod', 'unknown'), ('verificationMethod', None),
                           ('forceSizeNeedsConfirmation', False), ('forceSizeNeedsConfirmation', 1),
                           ('reviewedOn', '2024-99-99'), ('reviewedOn', None),
                           ('identityResolution', {})]:
            changed = copy.deepcopy(kwargs); changed['source_reviews_manifest']['reviews'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                builder.build(source, **changed)
        for key in ['verificationMethod', 'forceSizeNeedsConfirmation', 'reviewedOn']:
            changed = copy.deepcopy(kwargs); changed['source_reviews_manifest']['reviews'][0].pop(key)
            with self.subTest(missing=key), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_guarded_title_review_must_match_all_original_descriptor_and_unknown_unit_guards(self):
        source, kwargs = fixture()
        for key, value in [('sku', '2'), ('expectedUpc', '012345678912'), ('expectedSourceRow', 2),
                           ('expectedRawFieldsSha256', '0' * 64), ('expectedPublicName', 'snack'),
                           ('expectedLabel', 'Size not specified'), ('expectedCategory', 'drinks'),
                           ('expectedDepartment', 'Candy')]:
            changed = copy.deepcopy(kwargs); changed['public_name_reviews_manifest']['reviews'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)
        for key in ['expectedNameSha256', 'expectedSourceRecordSha256']:
            changed = copy.deepcopy(kwargs); changed['source_reviews_manifest']['reviews'][0][key] = '0' * 64
            with self.subTest(source_key=key), self.assertRaises(ValueError): builder.build(source, **changed)
        for replacement in [None, {'schemaVersion': 1, 'reviews': []}]:
            changed = copy.deepcopy(kwargs); changed['public_name_reviews_manifest'] = replacement
            with self.subTest(title_manifest=replacement), self.assertRaisesRegex(ValueError, 'matching guarded'):
                builder.build(source, **changed)

    def test_title_proof_is_name_only_safe_specific_exact_code_and_quantity_free(self):
        source, kwargs = fixture()
        titles = ['candy', 'Example Brand 8oz Candy', 'Example Brand 12-pack Candy',
                  'Example Brand Two Candy', 'Example Brand Single Candy', 'Example Brand jar',
                  'Example Brand 2largecans Candy', 'Example Brand dozen Candy',
                  'Example Brand Family Size Candy',
                  'Example Brand $3 Candy', '/Users/private/Candy', 'Example Brand 5% Candy']
        for title in titles:
            changed = copy.deepcopy(kwargs); changed['public_name_reviews_manifest']['reviews'][0]['proposedPublicTitle'] = title
            with self.subTest(title=title), self.assertRaises(ValueError): builder.build(source, **changed)
        for evidence in [[], [{'kind': 'retailer', 'url': 'https://retailer.example.test/name', 'supports': 'No literal code.'}],
                         [{'kind': 'retailer', 'url': 'http://retailer.example.test/name', 'supports': '012345678905'}]]:
            changed = copy.deepcopy(kwargs); changed['public_name_reviews_manifest']['reviews'][0]['evidence'] = evidence
            with self.subTest(evidence=evidence), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_conflicting_spelling_identity_category_and_name_verification_reject(self):
        source, kwargs = fixture(); review = kwargs['source_reviews_manifest']['reviews'][0]
        base = {'sku': '1', 'expectedUpc': review['expectedUpc'], 'expectedNameSha256': review['expectedNameSha256'],
                'evidence': [{'url': 'https://evidence.example.test/synthetic'}]}
        manifests = [
            {'schemaVersion': 1, 'corrections': [dict(base, replaceTextFrom='candy', replaceTextTo=TITLE, scope='brand_spelling_only')]},
            {'schemaVersion': 1, 'identityReviews': [dict(base, action='quarantine', reason='Conflicting identity')]},
            {'schemaVersion': 1, 'nameVerifications': [dict(base, expectedPublicName=TITLE,
                expectedLabel='Size needs confirmation', verifiedOn='2024-02-29',
                verification=dict.fromkeys(['brand', 'expression', 'upc', 'visual'], True),
                evidence=[{'kind': kind, 'url': 'https://evidence.example.test/' + kind}
                          for kind in ['manufacturer', 'exact_upc_listing', 'inspected_package_photo']])]}]
        for names in manifests:
            with self.subTest(names=list(names)), self.assertRaisesRegex(ValueError, 'cannot coexist'):
                builder.build(source, name_manifest=names, **kwargs)
        category = {'sku': '1', 'expectedUpc': review['expectedUpc'], 'expectedNameSha256': review['expectedNameSha256'],
                    'expectedSourceRecordSha256': review['expectedSourceRecordSha256'], 'expectedDepartment': 'Snacks',
                    'expectedPublicName': 'candy', 'expectedLabel': 'Size needs confirmation',
                    'expectedCategory': 'food', 'category': 'essentials', 'reviewedOn': '2024-02-29',
                    'reason': 'Synthetic conflicting category', 'evidence': [{'kind': 'retailer', 'url': 'https://evidence.example.test/category'}]}
        with self.assertRaisesRegex(ValueError, 'cannot coexist'):
            builder.build(source, category_reviews_manifest={'schemaVersion': 1, 'reviews': [category]}, **kwargs)

    def test_unquantified_standard_units_and_package_counts_are_not_name_only_proof(self):
        # No number is needed for these titles to assert a selling unit. Check
        # ordinary unit spellings/plurals, UK spellings and retail abbreviations
        # with fresh complete source/title guards, not parser-derived labels.
        words = ['Quart', 'Quarts', 'qt', 'qts', 'Gallon', 'Gallons', 'gal', 'gals',
                 'Pint', 'Pints', 'pt', 'pts', 'Kilogram', 'Kilograms', 'Milligram',
                 'Micrograms', 'Milliliter', 'Milliliters', 'Millilitre', 'Millilitres',
                 'Liter', 'Liters', 'Litre', 'Litres', 'Centilitre', 'Deciliter',
                 'Kilolitre', 'L', 'lt', 'lts', 'ltr', 'ml', 'cl', 'dl', 'Ounce', 'Ounces',
                 'Fluid Ounce', 'Pound', 'Grams', 'Foot', 'Feet', 'Meter', 'Metre',
                 'Pair', 'Pairs', 'Couple', 'Couples', 'Set', 'Sets']
        titles = [f'Example Brand {word} Candy' for word in words]
        titles += ['Example Brand Quart Juice', 'Example Brand Gallon Juice',
                   'Example Brand Pint Juice', 'Example Brand half-gallon Candy']
        for title in titles:
            source, kwargs = fixture(title=title)
            with self.subTest(title=title), self.assertRaisesRegex(ValueError, 'quantity, container or selling unit'):
                builder.build(source, **kwargs)
        for title in ['Quartet Brand Tangy Candy', 'Pintastic Brand Candy', 'Pairwise Brand Candy']:
            source, kwargs = fixture(title=title)
            with self.subTest(incidental_word=title):
                catalog, _ = builder.build(source, **kwargs)
                self.assertEqual(by_sku(catalog)['1'][0]['name'], title)
                self.assertEqual(by_sku(catalog)['1'][1]['label'], 'Size needs confirmation')
                self.assertIsNone(by_sku(catalog)['1'][1]['image'])

    def test_nonproducts_pos_placeholders_and_report_origin_cannot_be_restored(self):
        for name, department in [('0.73 candy', 'Tax'), ('0.73 candy', 'Instant'), ('0.73 candy', 'ShortCut'),
                                 ('discount', 'Snacks'), ('NEW ITEM', 'Snacks'), ('machine lotto', 'ShortCut')]:
            source, kwargs = fixture(export({'ItemName': name, 'DEPNAME': department, 'MAINUPC': '012345678905'}))
            with self.subTest(name=name, department=department), self.assertRaises(ValueError):
                builder.build(source, **kwargs)
        source, kwargs = fixture()
        empty = export()
        with self.assertRaisesRegex(ValueError, 'absent from the original source'):
            builder.build(empty, report_additions_manifest=report_manifest({'sku': '1', 'name': 'Synthetic Candy', 'pack': '', 'department': 'Snacks'}),
                          source_reviews_manifest=kwargs['source_reviews_manifest'])

    def test_photo_attempt_is_blocked_and_projected_name_evidence_stays_private(self):
        source, kwargs = fixture()
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            builder.build(source, image_manifest={'schemaVersion': 1, 'images': [{'sku': '1'}]}, **kwargs)
        ledger = {}
        projected = project_evidence(kwargs['public_name_reviews_manifest'], ledger)
        self.assertNotIn('retailer.example.test', json.dumps(projected))
        self.assertNotIn('selling unit', json.dumps(projected))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND, 'records': ledger}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                catalog, audit = builder.build(source, source_reviews_manifest=kwargs['source_reviews_manifest'],
                                                public_name_reviews_manifest=projected)
        public = json.dumps(catalog)
        for forbidden in ['0.73', '7.31', 'retailer.example.test', 'source_fields', 'ItemPrice']:
            self.assertNotIn(forbidden, public)
        self.assertEqual(by_sku(catalog)['1'][0]['name'], TITLE)
        self.assertIsNone(by_sku(catalog)['1'][1]['image'])
        self.assertEqual(audit['records'][0]['source']['ItemPrice'], '7.31')


if __name__ == '__main__':
    unittest.main()
