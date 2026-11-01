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


class MissingMeasurementTests(unittest.TestCase):
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
