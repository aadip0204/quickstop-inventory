"""Late quantity fills preserve original name checks, source rows and isolation."""
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

from test_catalog import (builder, export, manufacturer_correction,
                          direct_reference_image_fixture, direct_reference_evidence_fixture)


def missing_fixture(source=None, label='4.5 fl oz', names=None):
    # All identity/evidence here is synthetic. Existing local bytes exercise
    # integrity checks without approving real merchandise or writing assets.
    source = source or export({'ItemName': 'Example Ice Cookie', 'DEPNAME': 'Snacks',
                               'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
    baseline, _ = builder.build(source, name_manifest=names)
    product, variant = next((p, v) for p in baseline['products'] for v in p['variants'] if v['sku'] == '1')
    raw = list(csv.reader(io.StringIO(source.decode())))[1]
    image = direct_reference_image_fixture(source)
    image.update(expectedName=product['name'], expectedLabel=label)
    private_images = direct_reference_evidence_fixture(image)
    unit = {'supports': 'Synthetic retail unit evidence; no real product claim.',
            'observedUpc': raw[1], 'retailUnitLabel': label, 'quantityBasis': 'retail_net_content',
            'retailUnitsPerPackage': 1, 'sourceSha256': 'a' * 64, 'retrievedOn': '2026-10-31'}
    primary = dict(unit, kind='manufacturer', url='https://wellsfoodservice.com/synthetic-unit',
                   publisher='Wells Enterprises', publisherEvidenceUrl='https://wellsfoodservice.com/synthetic-unit',
                   observedName=product['name'], retiredNotice=False)
    retailer = dict(unit, kind='exact_upc_listing', url='https://retailer.example.test/unit/' + raw[1])
    photo = {'kind': 'inspected_package_photo', 'url': private_images['entries'][0]['sourceImageUrl'],
             'supports': 'Independent synthetic inspection fixture for these exact bytes.',
             'sha256': image['sha256'], 'inspectedOn': '2026-10-31', 'inspectionReceiptSha256': 'b' * 64}
    review = {'sku': '1', 'expectedUpc': raw[1],
              'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
              'expectedSourceRecordSha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
              'action': 'resolve_verified_missing_measurement', 'expectedSourceRow': 1,
              'expectedPublicName': product['name'], 'expectedLabel': 'Size not specified',
              'expectedCategory': product['category'], 'expectedDepartment': product['department'],
              'expectedProductId': product['id'], 'expectedResultingGroupSkus': [v['sku'] for v in product['variants']],
              'verifiedLabel': label, 'expectedImageSha256': image['sha256'],
              'verification': dict.fromkeys(('brand', 'expression', 'size', 'packaging', 'upc', 'visual'), True),
              'evidence': [primary, retailer, photo], 'reason': 'Only an absent retail measurement is filled.',
              'reviewedOn': '2026-10-31'}
    kwargs = {'source_reviews_manifest': {'schemaVersion': 1, 'reviews': [review]},
              'image_manifest': {'schemaVersion': 1, 'images': [image]},
              'private_direct_image_evidence_manifest': private_images}
    if names:
        kwargs['name_manifest'] = names
    return source, baseline, kwargs


def typed_gtin14_fixture(source=None):
    source, baseline, kwargs = missing_fixture(source, label='16.9 fl oz')
    retailer = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][1]
    unit_upc = retailer.pop('observedUpc')
    retailer.update(kind='exact_unit_gtin14_listing', observedUnitGtin14=unit_upc.zfill(14),
                    unitCodeSourceField='Product.gtin14')
    return source, baseline, kwargs


def historical_fixture(source=None, names=None):
    source, baseline, kwargs = missing_fixture(source, label='3.125 oz', names=names)
    review = kwargs['source_reviews_manifest']['reviews'][0]
    primary, _, photo = review['evidence']
    primary['retiredNotice'] = True
    primary['historicalPackageReview'] = {
        'scope': 'original_inventory_unit', 'producerProductPageUrl': primary['url'],
        'producerResponseSha256': primary['sourceSha256'], 'sourceImageUrl': photo['url'],
        'sourceImageSha256': review['expectedImageSha256'], 'visibleNetContent': review['verifiedLabel'],
        'retailUnitsPerPackage': 1, 'manufacturerPrimaryImageConfirmed': True,
        'inspectedOn': photo['inspectedOn'], 'inspectionReceiptSha256': photo['inspectionReceiptSha256'],
    }
    return source, baseline, kwargs


class MissingMeasurementTests(unittest.TestCase):
    def coca_fixture(self):
        source, baseline, kwargs = missing_fixture(label='20 fl oz')
        primary = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]
        primary.update(publisher='The Coca-Cola Company',
                       url='https://smartlabel.coca-colaproductfacts.com/nutrition/index.html?upc=012345678905',
                       publisherEvidenceUrl='https://smartlabel.coca-colaproductfacts.com/products/index.html')
        return source, baseline, kwargs

    def test_coca_owned_record_resolves_only_reviewed_missing_unit_and_photo(self):
        source, baseline, kwargs = self.coca_fixture()
        catalog, audit = builder.build(source, **kwargs)
        actual, prior = catalog['products'][0], baseline['products'][0]
        self.assertEqual((actual['id'], actual['name'], actual['category'], actual['department']),
                         (prior['id'], prior['name'], prior['category'], prior['department']))
        variant = actual['variants'][0]
        self.assertEqual(variant['label'], '20 fl oz')
        self.assertIsNotNone(variant['image'])
        self.assertEqual({k: v for k, v in variant.items() if k not in ('label', 'image')},
                         {k: v for k, v in prior['variants'][0].items() if k not in ('label', 'image')})
        self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])

    def test_coca_record_does_not_trust_related_or_lookalike_hosts(self):
        for field in ('url', 'publisherEvidenceUrl'):
            for url in ('https://smartlabel.coca-colaproductfacts.com.evil.test/record',
                        'https://other.coca-colaproductfacts.com/record',
                        'https://assets.coke.com/record',
                        'https://user@smartlabel.coca-colaproductfacts.com/record',
                        'https://smartlabel.coca-colaproductfacts.com:443/record'):
                source, _, kwargs = self.coca_fixture()
                kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0][field] = url
                with self.subTest(field=field, url=url), self.assertRaises(ValueError):
                    builder.build(source, **kwargs)

    def test_coca_unit_still_requires_exact_code_and_independent_retailer(self):
        for kind, value in (('primary_upc', '012345678912'), ('retailer_upc', '012345678912'),
                            ('retailer_host', 'https://smartlabel.coca-colaproductfacts.com/unit'),
                            ('retailer_host', 'https://mirror.smartlabel.coca-colaproductfacts.com/unit')):
            source, _, kwargs = self.coca_fixture()
            primary, retailer, _ = kwargs['source_reviews_manifest']['reviews'][0]['evidence']
            if kind == 'primary_upc': primary['observedUpc'] = value
            elif kind == 'retailer_upc': retailer['observedUpc'] = value
            else: retailer['url'] = value
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                builder.build(source, **kwargs)

    def test_historical_exact_package_preserves_entire_catalog_except_target_label_photo(self):
        source = export({'ItemName': 'Example Cookie', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905',
                         'ItemPrice': '', 'TotalQty_Multi': ''},
                        {'ItemName': 'Example Cookie 8oz', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678912',
                         'ItemPrice': '', 'TotalQty_Multi': ''})
        for typed in [False, True]:
            source, baseline, kwargs = historical_fixture(source)
            if typed:
                retailer = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][1]
                code = retailer.pop('observedUpc')
                retailer.update(kind='exact_unit_gtin14_listing', observedUnitGtin14=code.zfill(14),
                                unitCodeSourceField='Product.gtin14')
            original = copy.deepcopy(kwargs)
            with self.subTest(typed=typed):
                catalog, audit = builder.build(source, **kwargs)
                expected = copy.deepcopy(baseline)
                variant = next(v for p in expected['products'] for v in p['variants'] if v['sku'] == '1')
                actual = next(v for p in catalog['products'] for v in p['variants'] if v['sku'] == '1')
                variant.update(label='3.125 oz', image=actual['image'])
                self.assertEqual(catalog, expected)
                self.assertEqual(kwargs, original)
                self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])
                self.assertEqual(audit['records'][0]['source']['MAINUPC'], '012345678905')
                self.assertIs(audit['records'][0]['sourceReview']['evidence'][0]['retiredNotice'], True)

    def test_historical_review_has_exact_schema_no_current_stock_or_generation_scope(self):
        source, _, kwargs = historical_fixture()
        historical = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]['historicalPackageReview']
        for missing in historical:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][0]['historicalPackageReview'].pop(missing)
            with self.subTest(missing=missing), self.assertRaises(ValueError): builder.build(source, **changed)
        for value in [None, [], 'original_inventory_unit']:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][0]['historicalPackageReview'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): builder.build(source, **changed)
        for scope in ['current_stock', 'current_package_generation', 'retired_product', True]:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][0]['historicalPackageReview']['scope'] = scope
            with self.subTest(scope=scope), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        changed['source_reviews_manifest']['reviews'][0]['evidence'][0]['historicalPackageReview']['stockConfirmed'] = True
        with self.assertRaises(ValueError): builder.build(source, **changed)

    def test_historical_capture_image_unit_receipt_and_strict_types_must_all_match(self):
        source, _, kwargs = historical_fixture()
        changes = [('producerProductPageUrl', 'https://wellsfoodservice.com/different'),
                   ('producerResponseSha256', '0' * 64), ('sourceImageUrl', 'https://other.test/photo.jpg'),
                   ('sourceImageSha256', '0' * 64), ('visibleNetContent', '4 oz'),
                   ('retailUnitsPerPackage', True), ('retailUnitsPerPackage', 1.0),
                   ('retailUnitsPerPackage', '1'), ('retailUnitsPerPackage', 24),
                   ('manufacturerPrimaryImageConfirmed', 1), ('manufacturerPrimaryImageConfirmed', 'true'),
                   ('manufacturerPrimaryImageConfirmed', False), ('inspectedOn', '2026-10-30'),
                   ('inspectionReceiptSha256', '0' * 64), ('producerResponseSha256', None)]
        for key, value in changes:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][0]['historicalPackageReview'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_historical_field_is_not_allowed_on_active_missing_or_boolean_alias_notices(self):
        source, _, kwargs = historical_fixture()
        for notice in [False, None, 0, 1, 'True', 'False']:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][0]['retiredNotice'] = notice
            with self.subTest(notice=notice), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        changed['source_reviews_manifest']['reviews'][0]['evidence'][0].pop('retiredNotice')
        with self.assertRaises(ValueError): builder.build(source, **changed)
        source, _, active = missing_fixture(label='3.125 oz')
        expected, _ = builder.build(source, **active)
        changed = copy.deepcopy(kwargs)
        primary = changed['source_reviews_manifest']['reviews'][0]['evidence'][0]
        primary['retiredNotice'] = False
        primary.pop('historicalPackageReview')
        actual, _ = builder.build(source, **changed)
        self.assertEqual(actual, expected)
        changed = copy.deepcopy(active)
        changed['source_reviews_manifest']['reviews'][0]['evidence'][0]['retiredNotice'] = True
        with self.assertRaises(ValueError): builder.build(source, **changed)

    def test_historical_units_still_follow_original_name_and_final_unchanged_name_guards(self):
        source = export({'ItemName': 'Example Cook', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905'})
        correction = manufacturer_correction(source, 'Example Cookie', 'Example Cookie', 'Size not specified')
        verification = {key: correction[key] for key in ('sku', 'expectedUpc', 'expectedNameSha256',
                        'expectedLabel', 'verifiedOn', 'evidence', 'manufacturerRecord')}
        verification.update(expectedPublicName='Example Cookie', verificationMethod='manufacturer_exact_upc_record',
                            verification={'brand': True, 'expression': True, 'upc': True, 'visual': False})
        names = {'schemaVersion': 1, 'corrections': [correction], 'nameVerifications': [verification]}
        source, _, kwargs = historical_fixture(source, names)
        catalog, audit = builder.build(source, **kwargs)
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '3.125 oz')
        self.assertEqual(audit['records'][0]['nameCorrection']['expectedLabel'], 'Size not specified')
        self.assertEqual(audit['records'][0]['nameVerification']['expectedLabel'], 'Size not specified')
        for field, message in [('corrections', 'Manufacturer name correction changed a guarded'),
                               ('nameVerifications', 'Name verification does not match')]:
            changed = copy.deepcopy(kwargs); changed['name_manifest'][field][0]['expectedLabel'] = '3.125 oz'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message): builder.build(source, **changed)

    def test_historical_evidence_accepts_real_leap_days_in_all_three_dates(self):
        source, _, kwargs = historical_fixture()
        expected, _ = builder.build(source, **kwargs)
        for value, reverse in [('2024-02-29', False), ('2000-02-29', True)]:
            changed = copy.deepcopy(kwargs)
            primary, retailer, photo = changed['source_reviews_manifest']['reviews'][0]['evidence']
            primary['retrievedOn'] = retailer['retrievedOn'] = photo['inspectedOn'] = value
            primary['historicalPackageReview']['inspectedOn'] = value
            if reverse:
                changed['source_reviews_manifest']['reviews'][0]['evidence'].reverse()
            with self.subTest(value=value, reverse=reverse):
                actual, _ = builder.build(source, **changed)
                self.assertEqual(actual, expected)

    def test_historical_evidence_rejects_impossible_dates_and_type_shape_aliases(self):
        source, _, kwargs = historical_fixture()
        impossible = ['2026-02-30', '2026-13-01', '0000-00-00',
                      '2025-02-29', '2100-02-29', '2026-04-31']
        aliases = [None, True, 20240229, '20240229', '2024-W09-4', '2024-2-29', '2024-02-29T00:00:00Z']
        for role in ['inspection', 'primary', 'retailer']:
            for value in impossible + aliases:
                changed = copy.deepcopy(kwargs)
                primary, retailer, photo = changed['source_reviews_manifest']['reviews'][0]['evidence']
                if role == 'inspection':
                    # Synchronize both declarations so equality alone cannot
                    # hide an impossible manual-inspection calendar date.
                    primary['historicalPackageReview']['inspectedOn'] = photo['inspectedOn'] = value
                elif role == 'primary':
                    primary['retrievedOn'] = value
                else:
                    retailer['retrievedOn'] = value
                with self.subTest(role=role, value=value), self.assertRaises(ValueError):
                    builder.build(source, **changed)

    def test_historical_calendar_rule_does_not_change_the_existing_active_date_path(self):
        source, _, kwargs = missing_fixture(label='3.125 oz')
        expected, _ = builder.build(source, **kwargs)
        primary, retailer, photo = kwargs['source_reviews_manifest']['reviews'][0]['evidence']
        self.assertIs(primary['retiredNotice'], False)
        # This is the pre-existing shape-only behavior, deliberately scoped out
        # of this historical-package fix rather than silently changing it.
        primary['retrievedOn'] = retailer['retrievedOn'] = photo['inspectedOn'] = '2026-02-30'
        actual, _ = builder.build(source, **kwargs)
        self.assertEqual(actual, expected)

    def test_historical_proof_does_not_skip_independent_unit_original_or_photo_guards(self):
        source, _, kwargs = historical_fixture()
        mutations = [lambda k: k['source_reviews_manifest']['reviews'][0]['evidence'][0].update(observedUpc='00012345678905'),
                     lambda k: k['source_reviews_manifest']['reviews'][0]['evidence'][1].update(observedUpc='012345678912'),
                     lambda k: k['source_reviews_manifest']['reviews'][0]['evidence'][1].update(url='https://www.wellsfoodservice.com./unit'),
                     lambda k: k['source_reviews_manifest']['reviews'][0]['evidence'][1].update(retailUnitsPerPackage=24),
                     lambda k: k.update(image_manifest=None),
                     lambda k: k.update(private_direct_image_evidence_manifest=None),
                     lambda k: k['image_manifest']['images'][0]['verification'].update(professional=False),
                     lambda k: k['source_reviews_manifest']['reviews'][0].update(expectedProductId='p-' + '0' * 16)]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(kwargs); mutation(changed)
            with self.subTest(index=index), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs); review = changed['source_reviews_manifest']['reviews'][0]
        review['expectedImageSha256'] = review['evidence'][2]['sha256'] = '0' * 64
        review['evidence'][0]['historicalPackageReview']['sourceImageSha256'] = '0' * 64
        changed['image_manifest']['images'][0]['sha256'] = '0' * 64
        changed['private_direct_image_evidence_manifest']['entries'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Product image is empty or hash does not match'):
            builder.build(source, **changed)
        rows = list(csv.reader(io.StringIO(source.decode()))); rows[1][6] = '42'
        stream = io.StringIO(); csv.writer(stream).writerows(rows)
        with self.assertRaisesRegex(ValueError, 'Source metadata review identity mismatch'):
            builder.build(stream.getvalue().encode(), **kwargs)

    def test_historical_package_proof_stays_in_hash_bound_private_evidence(self):
        source, _, kwargs = historical_fixture()
        expected, _ = builder.build(source, **kwargs)
        projected = copy.deepcopy(kwargs); review = projected['source_reviews_manifest']['reviews'][0]
        evidence = review['evidence']; digest = builder.review_evidence_sha256(evidence)
        review['evidence'] = {'privateEvidenceSha256': digest}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            ledger = {'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND, 'records': {digest: evidence}}
            path.write_text(json.dumps(ledger))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                actual, _ = builder.build(source, **projected)
                self.assertEqual(actual, expected)
                self.assertNotIn('historicalPackageReview', json.dumps(actual))
                self.assertNotIn('historicalPackageReview', json.dumps(projected['source_reviews_manifest']))
                self.assertNotIn('https:', json.dumps(actual))
                ledger['records'][digest][0]['historicalPackageReview']['visibleNetContent'] = '4 oz'
                path.write_text(json.dumps(ledger))
                with self.assertRaisesRegex(ValueError, 'does not match its hash'): builder.build(source, **projected)

    def test_typed_gtin14_preserves_original_unit_codes_and_isolated_identity(self):
        for unit_upc in ('012345678905', '12345670', '1234567890128'):
            self.assertTrue(builder.valid_gtin(unit_upc))
            source = export(
                {'ItemName': 'Example G2 Soda', 'MAINUPC': unit_upc, 'DEPNAME': 'MISC',
                 'ItemPrice': '', 'TotalQty_Multi': ''},
                {'ItemName': 'Example G2 Soda 12oz', 'MAINUPC': '012345678912', 'DEPNAME': 'MISC',
                 'ItemPrice': '', 'TotalQty_Multi': ''})
            source, baseline, kwargs = typed_gtin14_fixture(source)
            original_kwargs = copy.deepcopy(kwargs)
            catalog, audit = builder.build(source, **kwargs)
            target = next(v for p in catalog['products'] for v in p['variants'] if v['sku'] == '1')
            expected = copy.deepcopy(baseline)
            before = next(v for p in expected['products'] for v in p['variants'] if v['sku'] == '1')
            before.update(label='16.9 fl oz', image=target['image'])
            self.assertEqual(catalog, expected)
            self.assertEqual(kwargs, original_kwargs)
            self.assertEqual(target['upc'], unit_upc)
            self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])
            self.assertEqual(audit['records'][0]['source']['MAINUPC'], unit_upc)
            evidence = kwargs['source_reviews_manifest']['reviews'][0]['evidence']
            self.assertEqual(evidence[0]['observedUpc'], unit_upc)
            self.assertEqual(evidence[1]['observedUnitGtin14'], unit_upc.zfill(14))
            self.assertNotIn('observedUpc', evidence[1])

    def test_typed_gtin14_rejects_case_codes_bad_checksums_and_formats(self):
        source, _, kwargs = typed_gtin14_fixture()
        case_gtin = '10012345678902'
        self.assertTrue(builder.valid_gtin(case_gtin))
        self.assertTrue(builder.valid_gtin('00012345678912'))
        bad_codes = [case_gtin, '00012345678912', '00012345678906', '0012345678905',
                     '000012345678905', ' 00012345678905', '00012345678905 ',
                     '000-12345678905', int('00012345678905'), None,
                     '０００１２３４５６７８９０５', '٠٠٠١٢٣٤٥٦٧٨٩٠٥']
        for value in bad_codes:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][1]['observedUnitGtin14'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'typed Product.gtin14'):
                builder.build(source, **changed)
        # An existing 14-digit source identity is outside this filler-only path.
        source14 = export({'ItemName': 'Example Soda', 'MAINUPC': '00012345678905',
                           'DEPNAME': 'MISC', 'ItemPrice': '', 'TotalQty_Multi': ''})
        source14, _, kwargs14 = typed_gtin14_fixture(source14)
        with self.assertRaisesRegex(ValueError, 'typed Product.gtin14'):
            builder.build(source14, **kwargs14)

    def test_typed_gtin14_requires_the_exact_retailer_schema_and_literal_manufacturer(self):
        source, _, kwargs = typed_gtin14_fixture()
        mutations = [
            lambda e: e[1].update(unitCodeSourceField='Product.gtin'),
            lambda e: e[1].update(unitCodeSourceField='Offer.gtin14'),
            lambda e: e[1].update(unitCodeSourceField=None),
            lambda e: e[1].update(kind='exact_upc_listing'),
            lambda e: e[1].update(kind='manufacturer'),
            lambda e: e[1].update(kind='other_unit_listing'),
            lambda e: e[1].update(observedUpc='012345678905'),
            lambda e: e[1].pop('observedUnitGtin14'),
            lambda e: e[0].update(observedUpc='00012345678905'),
            lambda e: e[0].update(observedUnitGtin14='00012345678905', unitCodeSourceField='Product.gtin14'),
            lambda e: e[0].update(kind='exact_unit_gtin14_listing'),
            lambda e: e.append(dict(e[1], kind='exact_upc_listing', observedUpc='012345678905')),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(kwargs)
            mutation(changed['source_reviews_manifest']['reviews'][0]['evidence'])
            with self.subTest(index=index), self.assertRaises(ValueError):
                builder.build(source, **changed)
        # Existing untyped aliases must still fail, even though the typed path succeeds.
        source, _, legacy = missing_fixture(label='16.9 fl oz')
        legacy['source_reviews_manifest']['reviews'][0]['evidence'][1]['observedUpc'] = '00012345678905'
        with self.assertRaisesRegex(ValueError, 'literal UPC'):
            builder.build(source, **legacy)

    def test_typed_gtin14_keeps_unit_independence_and_exact_photo_guards(self):
        source, _, kwargs = typed_gtin14_fixture()
        for key, value in [('retailUnitsPerPackage', 24), ('retailUnitsPerPackage', True),
                           ('quantityBasis', 'case_content'), ('retailUnitLabel', '24 fl oz'),
                           ('url', 'https://www.wellsfoodservice.com./synthetic-unit')]:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][1][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                builder.build(source, **changed)
        for key, value in [('expectedLabel', '24 fl oz'), ('sha256', '0' * 64)]:
            changed = copy.deepcopy(kwargs)
            changed['image_manifest']['images'][0][key] = value
            with self.subTest(image_key=key), self.assertRaisesRegex(ValueError, 'professional image binding'):
                builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        changed['image_manifest']['images'][0]['verification']['professional'] = False
        with self.assertRaisesRegex(ValueError, 'professional image binding'):
            builder.build(source, **changed)

    def test_typed_gtin14_works_through_private_evidence_projection(self):
        source, _, kwargs = typed_gtin14_fixture()
        expected, _ = builder.build(source, **kwargs)
        entry = kwargs['source_reviews_manifest']['reviews'][0]
        evidence = entry['evidence']
        digest = builder.review_evidence_sha256(evidence)
        entry['evidence'] = {'privateEvidenceSha256': digest}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND,
                                        'records': {digest: evidence}}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                actual, _ = builder.build(source, **kwargs)
        self.assertEqual(actual, expected)
        self.assertNotIn('observedUnitGtin14', json.dumps(actual))
        self.assertNotIn('https:', json.dumps(kwargs['source_reviews_manifest']))

    def test_late_fill_preserves_manufacturer_guard_and_all_other_metadata(self):
        source = export({'ItemName': 'Example Ice Cook', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905',
                         'ItemPrice': '', 'TotalQty_Multi': ''})
        correction = manufacturer_correction(source, 'Example Ice Cookie', 'Example Ice Cookie', 'Size not specified')
        names = {'schemaVersion': 1, 'corrections': [correction]}
        source, baseline, kwargs = missing_fixture(source, names=names)
        original = copy.deepcopy(kwargs)
        catalog, audit = builder.build(source, **kwargs)
        before = copy.deepcopy(baseline)
        variant = catalog['products'][0]['variants'][0]
        before['products'][0]['variants'][0].update(label='4.5 fl oz', image=variant['image'])
        self.assertEqual(catalog, before)
        self.assertEqual(kwargs, original)
        self.assertEqual(audit['records'][0]['nameCorrection']['expectedLabel'], 'Size not specified')
        self.assertEqual(audit['records'][0]['lateMeasurementResolution']['previousPublicIdentity'],
                         {'name': 'Example Ice Cookie', 'label': 'Size not specified'})
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example Ice Cook')
        self.assertEqual(builder.name_audit(catalog, audit)['summary']['exactIdentitiesResolved'], 0)
        wrong = copy.deepcopy(kwargs)
        wrong['name_manifest']['corrections'][0]['expectedLabel'] = '4.5 fl oz'
        with self.assertRaisesRegex(ValueError, 'Manufacturer name correction changed a guarded'):
            builder.build(source, **wrong)

    def test_final_unchanged_name_guard_runs_before_fill(self):
        source, _, _ = missing_fixture()
        correction = manufacturer_correction(source, 'Example Ice Cookie', 'Example Ice Cookie', 'Size not specified')
        verification = {key: correction[key] for key in ('sku', 'expectedUpc', 'expectedNameSha256',
                        'expectedLabel', 'verifiedOn', 'evidence', 'manufacturerRecord')}
        verification.update(expectedPublicName='Example Ice Cookie', verificationMethod='manufacturer_exact_upc_record',
                            verification={'brand': True, 'expression': True, 'upc': True, 'visual': False})
        source, _, kwargs = missing_fixture(source, label='2 L', names={'schemaVersion': 1, 'nameVerifications': [verification]})
        catalog, audit = builder.build(source, **kwargs)
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '2 L')
        self.assertEqual(audit['records'][0]['nameVerification']['expectedLabel'], 'Size not specified')
        self.assertIs(audit['records'][0]['nameVerification']['verification']['visual'], False)
        kwargs['name_manifest']['nameVerifications'][0]['expectedLabel'] = '2 L'
        with self.assertRaisesRegex(ValueError, 'Name verification does not match'):
            builder.build(source, **kwargs)

    def test_source_isolation_ids_and_duplicate_skus_are_preserved(self):
        for neighbor in [{'MAINUPC': '012345678912', 'ItemName': 'Example Ice Cookie 12oz'},
                         {'MAINUPC': '012345678905', 'ItemName': 'Example Ice Cookie'}]:
            source = export({'ItemName': 'Example Ice Cookie', 'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''},
                            dict(neighbor, ItemPrice='', TotalQty_Multi=''))
            source, baseline, kwargs = missing_fixture(source, label='3.25 oz')
            catalog, _ = builder.build(source, **kwargs)
            self.assertEqual([(p['id'], [v['sku'] for v in p['variants']]) for p in catalog['products']],
                             [(p['id'], [v['sku'] for v in p['variants']]) for p in baseline['products']])
            by_sku = {v['sku']: v for p in catalog['products'] for v in p['variants']}
            self.assertEqual(by_sku['1']['label'], '3.25 oz')
            self.assertEqual(by_sku['2'], next(v for p in baseline['products'] for v in p['variants'] if v['sku'] == '2'))

    def test_each_original_and_current_guard_fails_closed(self):
        source, _, kwargs = missing_fixture()
        changes = {'expectedUpc': '12345678905', 'expectedNameSha256': '0' * 64,
                   'expectedSourceRecordSha256': '0' * 64, 'expectedSourceRow': 2,
                   'expectedPublicName': 'Different Cookie', 'expectedLabel': 'Size needs confirmation',
                   'expectedCategory': 'drinks', 'expectedDepartment': 'Soda', 'expectedProductId': 'p-' + '0' * 16,
                   'expectedResultingGroupSkus': ['1', '2']}
        for key, value in changes.items():
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, **changed)
        # An unrelated private cell remains covered by the complete raw-row hash.
        changed_source = source.replace(b'Snacks,,,', b'Snacks,,1,', 1)
        self.assertNotEqual(changed_source, source)
        with self.assertRaisesRegex(ValueError, 'Source metadata review identity mismatch'):
            builder.build(changed_source, **kwargs)

    def test_case_gtin_unit_and_serving_quantity_tampering_are_rejected(self):
        source, _, kwargs = missing_fixture()
        case_gtin = '10012345678902'
        self.assertTrue(builder.valid_gtin(case_gtin))
        for position in (0, 1):
            for key, value in [('observedUpc', case_gtin), ('observedUpc', '00012345678905'),
                               ('retailUnitsPerPackage', 24), ('retailUnitsPerPackage', True),
                               ('quantityBasis', 'case_content'), ('quantityBasis', 'nutrition_serving'),
                               ('retailUnitLabel', '108 fl oz')]:
                changed = copy.deepcopy(kwargs)
                changed['source_reviews_manifest']['reviews'][0]['evidence'][position][key] = value
                with self.subTest(position=position, key=key, value=value), self.assertRaises(ValueError):
                    builder.build(source, **changed)

    def test_relabeling_manufacturer_evidence_does_not_make_it_independent(self):
        source, _, kwargs = missing_fixture()
        for publisher, primary_url, retailer_url in [
            ('Wells Enterprises', 'https://wellsfoodservice.com/synthetic-unit',
             'https://wellsfoodservice.com/synthetic-unit'),
            ('Wells Enterprises', 'https://wellsfoodservice.com/synthetic-unit',
             'https://www.wellsfoodservice.com/other-unit'),
            ('Wells Enterprises', 'https://wellsfoodservice.com/synthetic-unit',
             'https://shop.wellsfoodservice.com/other-unit'),
            ('Wells Enterprises', 'https://wellsfoodservice.com/synthetic-unit',
             'https://wellsfoodservice.com./synthetic-unit'),
            ('Wells Enterprises', 'https://wellsfoodservice.com/synthetic-unit',
             'https://www.wellsfoodservice.com./synthetic-unit'),
            ('Wells Enterprises', 'https://wellsfoodservice.com/synthetic-unit',
             'https://shop.wellsfoodservice.com./synthetic-unit'),
            ('Wells Enterprises', 'https://wellsfoodservice.com/synthetic-unit',
             'https://wellsfoodservice\u3002com/synthetic-unit'),
            ('Wells Enterprises', 'https://wellsfoodservice.com/synthetic-unit',
             'https://wellsfoodservice%2ecom/synthetic-unit'),
            ('PepsiCo', 'https://smartlabel.pepsico.info/synthetic-unit',
             'https://www.pepsicopartners.com/synthetic-unit'),
        ]:
            changed = copy.deepcopy(kwargs)
            evidence = changed['source_reviews_manifest']['reviews'][0]['evidence']
            evidence[0].update(publisher=publisher, url=primary_url, publisherEvidenceUrl=primary_url)
            evidence[1]['url'] = retailer_url
            with self.subTest(retailer_url=retailer_url), self.assertRaisesRegex(ValueError, 'independent retailer'):
                builder.build(source, **changed)

    def test_canonical_units_and_unsafe_measurements(self):
        for label in ['4 fl oz', '4.5 fl oz', '3.25 oz', '4.125 oz', '2 L']:
            source, _, kwargs = missing_fixture(label=label)
            catalog, _ = builder.build(source, **kwargs)
            self.assertEqual(catalog['products'][0]['variants'][0]['label'], label)
        for label in ['0 oz', '-1 oz', '0 fl oz', '4.50 fl oz', '2L', '2 L · bottles', '24-pack · 4 fl oz',
                      '2 L · 4 oz', '2 L 2 L', '12 sq ft', 'size unknown']:
            source, _, kwargs = missing_fixture(label=label)
            with self.subTest(label=label), self.assertRaises(ValueError):
                builder.build(source, **kwargs)

    def test_original_measures_case_metadata_and_nonproducts_cannot_be_filled(self):
        source, _, kwargs = missing_fixture()
        for fields in [{'ItemName': 'Example Ice Cookie 2 3/4 oz'},
                       {'ItemName': 'Example Ice Cookie 1L', 'PackName': '33.8 oz'},
                       *({'ItemName': 'Example Ice Cookie ' + pack} for pack in (
                           'case of 24', '2DZ', '2 dozen', '2 doz.', '24/CASE', '24 per case',
                           'case qty: 24', '6-count', 'pack of 24', '2x12', '24 pcs',
                           'multipack', 'multi-pack', 'multi pack')),
                       {'PackName': '2DZ'}, {'DEPNAME': 'Tax'}]:
            changed_source = export(dict({'ItemName': 'Example Ice Cookie', 'DEPNAME': 'Snacks',
                                          'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''}, **fields))
            changed = copy.deepcopy(kwargs)
            raw = list(csv.reader(io.StringIO(changed_source.decode())))[1]
            review = changed['source_reviews_manifest']['reviews'][0]
            review['expectedNameSha256'] = hashlib.sha256(raw[2].encode()).hexdigest()
            review['expectedSourceRecordSha256'] = hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, 'genuinely absent'):
                builder.build(changed_source, **changed)

    def test_late_pack_hint_guard_preserves_brand_numbers(self):
        source = export({'ItemName': 'Example G2 1800 Cookie', 'DEPNAME': 'Snacks',
                         'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
        source, baseline, kwargs = missing_fixture(source)
        catalog, audit = builder.build(source, **kwargs)
        self.assertEqual(catalog['products'][0]['name'], 'Example G2 1800 Cookie')
        self.assertEqual(catalog['products'][0]['id'], baseline['products'][0]['id'])
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example G2 1800 Cookie')

    def test_only_its_matching_professional_inspected_photo_can_be_bound(self):
        source, _, kwargs = missing_fixture()
        mutations = [lambda k: k.update(image_manifest=None),
                     lambda k: k['image_manifest']['images'].append(copy.deepcopy(k['image_manifest']['images'][0])),
                     lambda k: k['image_manifest']['images'][0].update(expectedName='Different Cookie'),
                     lambda k: k['image_manifest']['images'][0].update(expectedLabel='4 oz'),
                     lambda k: k['image_manifest']['images'][0].update(sha256='0' * 64),
                     lambda k: k['image_manifest']['images'][0]['verification'].update(professional=False),
                     lambda k: k.update(private_direct_image_evidence_manifest=None),
                     lambda k: k['source_reviews_manifest']['reviews'][0]['evidence'][2].update(sha256='0' * 64),
                     lambda k: k['source_reviews_manifest']['reviews'][0]['evidence'][2].update(inspectionReceiptSha256=''),
                     lambda k: k['source_reviews_manifest']['reviews'][0]['evidence'][2].update(url='https://example.test/other.jpg')]
        for mutation in mutations:
            changed = copy.deepcopy(kwargs)
            mutation(changed)
            with self.subTest(mutation=mutations.index(mutation)), self.assertRaises(ValueError):
                builder.build(source, **changed)
        # All metadata can agree on a forged hash; local bytes must still fail.
        changed = copy.deepcopy(kwargs)
        review = changed['source_reviews_manifest']['reviews'][0]
        review['expectedImageSha256'] = '0' * 64
        review['evidence'][2]['sha256'] = '0' * 64
        changed['image_manifest']['images'][0]['sha256'] = '0' * 64
        changed['private_direct_image_evidence_manifest']['entries'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Product image is empty or hash does not match'):
            builder.build(source, **changed)

    def test_unknown_fields_and_unowned_primary_sources_are_rejected(self):
        source, _, kwargs = missing_fixture()
        for key, value in [('verifiedName', 'Renamed'), ('foodSizeResolution', {}), ('identityResolution', {}),
                           ('groupWithPriorExpression', True), ('sourceImageUrl', 'https://example.test/image.jpg')]:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, **changed)
        for key, value in [('url', 'https://unowned.example.test/unit'), ('retiredNotice', True)]:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, **changed)

    def test_external_projection_preserves_guards_and_keeps_origin_private(self):
        source, _, kwargs = missing_fixture()
        expected, _ = builder.build(source, **kwargs)
        projected = copy.deepcopy(kwargs)
        entry = projected['source_reviews_manifest']['reviews'][0]
        evidence = entry['evidence']
        digest = builder.review_evidence_sha256(evidence)
        entry['evidence'] = {'privateEvidenceSha256': digest}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            ledger = {'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND, 'records': {digest: evidence}}
            path.write_text(json.dumps(ledger))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                actual, _ = builder.build(source, **projected)
                self.assertEqual(actual, expected)
                ledger['records'][digest][0]['retailUnitLabel'] = '9 oz'
                path.write_text(json.dumps(ledger))
                with self.assertRaisesRegex(ValueError, 'does not match its hash'):
                    builder.build(source, **projected)
        self.assertNotIn('https:', json.dumps(projected['source_reviews_manifest']))
        self.assertNotIn('sourceImageUrl', json.dumps(actual))
        self.assertNotIn('retailer.example', json.dumps(actual))
