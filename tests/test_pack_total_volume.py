"""A reviewed retail-pack total never silently becomes each container's volume."""
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

from test_catalog import (builder, export, public_name_review,
                          direct_reference_image_fixture, direct_reference_evidence_fixture)


def pack_fixture(source=None, verified='4-pack · 250 mL · cartons', titles=None):
    # Synthetic identities reuse unchanged checked local image bytes only for
    # integrity tests. They do not approve or stage any real merchandise.
    source = source or export({'MAINUPC': '012345678905', 'ItemName': 'Example Cocktail 4PK',
                               'PackName': '1 Liter Wine Tax', 'DEPNAME': 'Ready To Drink'})
    options = {'public_name_reviews_manifest': titles} if titles else {}
    baseline, _ = builder.build(source, **options)
    product, variant = next((p, v) for p in baseline['products'] for v in p['variants'] if v['sku'] == '1')
    raw = list(csv.reader(io.StringIO(source.decode())))[1]
    raw_hash = hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    image = direct_reference_image_fixture(source)
    image.update(expectedName=product['name'], expectedLabel=verified)
    private = direct_reference_evidence_fixture(image)
    parsed = builder.parse_reviewed_pack_volume(verified, require_container=True)
    authority = {
        'kind': 'authority_exact_upc_per_container_volume',
        'url': 'https://www.mainespirits.com/synthetic-price-book.pdf',
        'supports': 'Synthetic authority record states per-container size; retail count is not stated.',
        'publisher': 'Maine Spirits', 'publisherRole': 'government_authority',
        'observedUpc': raw[1], 'observedNetContent': parsed['measure'].replace(' ', '').upper(),
        'perContainerLabel': parsed['measure'], 'quantityBasis': 'per_container_volume',
        'retailPackCount': None, 'sourceSha256': 'a' * 64,
        'documentDate': '2026-02-01', 'page': 25, 'retrievedOn': '2026-11-01',
    }
    receipt = {
        'sku': '1', 'upc': raw[1], 'publicName': product['name'],
        'expectedLabel': variant['label'], 'verifiedLabel': verified,
        'productId': product['id'], 'groupSkus': [v['sku'] for v in product['variants']],
        'sourceRow': 1, 'rawFieldsSha256': raw_hash, 'src': image['src'],
        'sha256': image['sha256'], 'originalSha256': image['sha256'],
        'publicImageSha256': builder.review_evidence_sha256(image),
        'sourceImageUrl': private['entries'][0]['sourceImageUrl'],
        'visiblePackText': str(parsed['pack']) + 'X' + authority['observedNetContent'],
        'container': parsed['container'], 'quantityBasis': 'printed_retail_pack_per_container',
        'authoritySourceSha256': authority['sourceSha256'],
        'verification': copy.deepcopy(image['verification']), 'inspectedOn': '2026-11-01',
    }
    photo = {'kind': 'inspected_package_photo', 'url': receipt['sourceImageUrl'],
             'supports': 'Synthetic inspected retail package explicitly prints count times each volume.',
             'sha256': image['sha256'], 'inspectedOn': '2026-11-01',
             'inspectionReceipt': receipt,
             'inspectionReceiptSha256': builder.review_evidence_sha256(receipt)}
    entry = {
        'sku': '1', 'expectedUpc': raw[1],
        'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
        'expectedSourceRecordSha256': raw_hash, 'expectedSourceRow': 1,
        'action': 'resolve_verified_pack_total_volume', 'reviewedOn': '2026-11-01',
        'expectedPublicName': product['name'], 'expectedLabel': variant['label'],
        'expectedCategory': product['category'], 'expectedDepartment': product['department'],
        'expectedProductId': product['id'], 'expectedResultingGroupSkus': receipt['groupSkus'],
        'verifiedLabel': verified, 'expectedImageSha256': image['sha256'],
        'verification': dict.fromkeys(('brand', 'expression', 'size', 'packaging', 'upc', 'visual'), True),
        'evidence': [authority, photo],
        'reason': 'Clarify a reviewed total as each container volume; preserve the original retail count.',
    }
    options.update(source_reviews_manifest={'schemaVersion': 1, 'reviews': [entry]},
                   image_manifest={'schemaVersion': 1, 'images': [image]},
                   private_direct_image_evidence_manifest=private)
    return source, baseline, options


def review(options):
    return options['source_reviews_manifest']['reviews'][0]


def receipt(options):
    return review(options)['evidence'][1]['inspectionReceipt']


def rehash_inspection(options):
    photo = review(options)['evidence'][1]
    photo['inspectionReceiptSha256'] = builder.review_evidence_sha256(photo['inspectionReceipt'])


class PackTotalVolumeTests(unittest.TestCase):
    def test_changes_only_reviewed_label_and_image_preserving_all_siblings_and_source(self):
        source = export(
            {'MAINUPC': '012345678905', 'ItemName': 'Example Cocktail 4PK',
             'PackName': '1 Liter Wine Tax', 'DEPNAME': 'Ready To Drink'},
            {'MAINUPC': '012345678912', 'ItemName': 'Example Cocktail 2PK',
             'PackName': '1 Liter Wine Tax', 'DEPNAME': 'Ready To Drink'},
            {'ItemName': 'Unrelated Beverage 750ml', 'DEPNAME': 'Ready To Drink'})
        source, baseline, options = pack_fixture(source)
        before = copy.deepcopy(options)
        catalog, audit = builder.build(source, **options)
        expected = copy.deepcopy(baseline)
        variant = next(v for p in expected['products'] for v in p['variants'] if v['sku'] == '1')
        actual = next(v for p in catalog['products'] for v in p['variants'] if v['sku'] == '1')
        variant.update(label='4-pack · 250 mL · cartons', image=actual['image'])
        self.assertEqual(catalog, expected)
        self.assertEqual(options, before)
        self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])
        provenance = audit['records'][0]['packTotalVolumeResolution']
        self.assertEqual(provenance['previousPublicIdentity'], {'name': 'Example Cocktail', 'label': '4-pack · 1 L'})
        self.assertIsNone(provenance['authorityRetailPackCount'])
        self.assertEqual(provenance['retailCountBasis'], 'original_source_and_inspected_package')
        self.assertEqual(provenance['authoritySourceSha256'], 'a' * 64)

    def test_exact_decimal_arithmetic_across_liters_and_milliliters(self):
        source = export({'MAINUPC': '012345678905', 'ItemName': 'Example Cocktail 3PK',
                         'PackName': '0.3 Liter Wine Tax', 'DEPNAME': 'Ready To Drink'})
        source, _, options = pack_fixture(source, '3-pack · 100 mL · cartons')
        catalog, _ = builder.build(source, **options)
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '3-pack · 100 mL · cartons')
        options['source_reviews_manifest']['reviews'][0]['evidence'][0]['url'] = 'https://mainespirits.com/synthetic-price-book.pdf'
        builder.build(source, **options)

    def test_existing_title_guards_run_before_this_label_only_action(self):
        source, _, _ = pack_fixture()
        titles = public_name_review(source, 'Example Reviewed Cocktail')
        source, baseline, options = pack_fixture(source, titles=titles)
        catalog, _ = builder.build(source, **options)
        self.assertEqual(catalog['products'][0]['name'], baseline['products'][0]['name'])
        changed = copy.deepcopy(options)
        changed['public_name_reviews_manifest']['reviews'][0]['proposedPublicTitle'] = 'Different Cocktail'
        with self.assertRaisesRegex(ValueError, 'current source/public identity mismatch'):
            builder.build(source, **changed)

    def test_existing_name_verification_keeps_its_old_label_guard_before_late_repair(self):
        source, _, options = pack_fixture()
        r = review(options)
        verification = {key: r[key] for key in (
            'sku', 'expectedUpc', 'expectedNameSha256', 'expectedPublicName', 'expectedLabel')}
        verification.update(
            verifiedOn='2026-11-01',
            verification=dict.fromkeys(('brand', 'expression', 'upc', 'visual'), True),
            evidence=[{'kind': kind, 'url': 'https://example.test/' + kind}
                      for kind in ('manufacturer', 'exact_upc_listing', 'inspected_package_photo')])
        options['name_manifest'] = {'schemaVersion': 1, 'nameVerifications': [verification]}
        catalog, audit = builder.build(source, **options)
        self.assertEqual(audit['records'][0]['nameVerification']['expectedLabel'], '4-pack · 1 L')
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '4-pack · 250 mL · cartons')
        changed = copy.deepcopy(options)
        changed['name_manifest']['nameVerifications'][0]['expectedLabel'] = '4-pack · 250 mL · cartons'
        with self.assertRaisesRegex(ValueError, 'Name verification does not match'):
            builder.build(source, **changed)

    def test_all_original_current_group_and_image_guards_fail_closed(self):
        source, _, options = pack_fixture()
        changes = {
            'expectedUpc': '012345678912', 'expectedNameSha256': '0' * 64,
            'expectedSourceRecordSha256': '0' * 64, 'expectedSourceRow': 2,
            'expectedPublicName': 'Different Cocktail', 'expectedLabel': '4-pack · 2 L',
            'expectedCategory': 'wine', 'expectedDepartment': 'MISC',
            'expectedProductId': 'p-' + '0' * 16, 'expectedResultingGroupSkus': ['1', '2'],
            'expectedImageSha256': '0' * 64,
        }
        for key, value in changes.items():
            changed = copy.deepcopy(options); review(changed)[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, **changed)
        changed = copy.deepcopy(options); review(changed)['verification']['upc'] = False
        with self.assertRaises(ValueError): builder.build(source, **changed)
        rows = list(csv.reader(io.StringIO(source.decode()))); rows[1][6] = '8'
        stream = io.StringIO(); csv.writer(stream).writerows(rows)
        with self.assertRaisesRegex(ValueError, 'Source metadata review identity mismatch'):
            builder.build(stream.getvalue().encode(), **options)

    def test_bad_labels_count_changes_and_nonvolume_units_are_not_corrections(self):
        source, _, options = pack_fixture()
        labels = ['4-pack · 300 mL · cartons', '5-pack · 200 mL · cartons',
                  '4-pack · 250 mL', '4-pack · 250 oz · cartons',
                  '4-pack · 250 g · cartons', '4-pack · 0 mL · cartons',
                  '4-pack · -250 mL · cartons', '4-pack · 250.0 mL · cartons',
                  '4-pack · NaN L · cartons', '4-pack · 250 mL · cases']
        for label in labels:
            changed = copy.deepcopy(options); review(changed)['verifiedLabel'] = label
            with self.subTest(label=label), self.assertRaises(ValueError): builder.build(source, **changed)
        source_case = export({'MAINUPC': '012345678905', 'ItemName': 'Example Cocktail case 4PK',
                              'PackName': '1 Liter Wine Tax', 'DEPNAME': 'Ready To Drink'})
        source_case, _, changed = pack_fixture(source_case)
        with self.assertRaisesRegex(ValueError, 'original retail pack and total volume'):
            builder.build(source_case, **changed)

    def test_additional_case_and_dozen_shorthand_cannot_be_erased_by_retail_photo(self):
        for hint in ('CS24', '24CS', '2DZ', '2 dozen', 'CS-24', '24-CS', '2x6', 'x24'):
            source = export({'MAINUPC': '012345678905',
                             'ItemName': 'Example Cocktail ' + hint + ' 4PK',
                             'PackName': '1 Liter Wine Tax', 'DEPNAME': 'Ready To Drink'})
            source, _, options = pack_fixture(source)
            with self.subTest(hint=hint), self.assertRaisesRegex(ValueError, 'original retail pack and total volume'):
                builder.build(source, **options)
        for name in ('Example CSBlue Cocktail', 'Example Adzuki Cocktail'):
            source = export({'MAINUPC': '012345678905', 'ItemName': name + ' 4PK',
                             'PackName': '1 Liter Wine Tax', 'DEPNAME': 'Ready To Drink'})
            source, _, options = pack_fixture(source)
            catalog, _ = builder.build(source, **options)
            self.assertEqual(catalog['products'][0]['name'], name)

    def test_authority_is_literal_per_container_and_never_supplies_retail_count(self):
        source, _, options = pack_fixture()
        changes = {
            'url': 'https://retailer.example.test/price-book.pdf',
            'publisher': 'Some Retailer', 'publisherRole': 'manufacturer',
            'observedUpc': '012345678912', 'observedNetContent': '1L',
            'perContainerLabel': '1 L', 'quantityBasis': 'commercial_case_volume',
            'retailPackCount': 4, 'sourceSha256': '0' * 64, 'page': True,
            'documentDate': '2026-02-30', 'retrievedOn': '2025-01-01',
        }
        for key, value in changes.items():
            changed = copy.deepcopy(options); review(changed)['evidence'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)
        for key in review(options)['evidence'][0]:
            changed = copy.deepcopy(options); del review(changed)['evidence'][0][key]
            with self.subTest(missing=key), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(options); review(changed)['evidence'].append(copy.deepcopy(review(changed)['evidence'][0]))
        with self.assertRaises(ValueError): builder.build(source, **changed)

    def test_printed_pack_receipt_is_complete_bound_and_rejects_case_count_basis(self):
        source, _, options = pack_fixture()
        changes = {'visiblePackText': '4X300ML', 'container': 'bottles',
                   'quantityBasis': 'commercial_case', 'authoritySourceSha256': '0' * 64,
                   'upc': '012345678912', 'rawFieldsSha256': '0' * 64,
                   'sourceRow': True, 'groupSkus': ['1', '2'], 'originalSha256': '0' * 64,
                   'inspectedOn': '2027-01-01'}
        for key, value in changes.items():
            changed = copy.deepcopy(options); receipt(changed)[key] = value; rehash_inspection(changed)
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(options); receipt(changed)['verification']['professional'] = False; rehash_inspection(changed)
        with self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(options); receipt(changed)['visiblePackText'] = '4X0.25L'
        # Numerically equivalent print is acceptable only with its new receipt hash.
        with self.assertRaisesRegex(ValueError, 'hash-bound printed retail'):
            builder.build(source, **changed)
        rehash_inspection(changed); builder.build(source, **changed)

    def test_private_direct_provenance_and_final_image_are_mandatory(self):
        source, _, options = pack_fixture()
        for key in ['image_manifest', 'private_direct_image_evidence_manifest']:
            changed = copy.deepcopy(options); del changed[key]
            with self.subTest(missing=key), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(options)
        changed['private_direct_image_evidence_manifest']['entries'][0]['sourceImageUrl'] = 'https://example.test/other.jpg'
        with self.assertRaisesRegex(ValueError, 'match private provenance'):
            builder.build(source, **changed)
        changed = copy.deepcopy(options); changed['image_manifest']['images'].append(copy.deepcopy(changed['image_manifest']['images'][0]))
        with self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(options); image = changed['image_manifest']['images'][0]; image['usageBasis'] = 'not_direct_reference'
        receipt(changed)['publicImageSha256'] = builder.review_evidence_sha256(image); rehash_inspection(changed)
        with self.assertRaisesRegex(ValueError, 'Reviewed photo does not match'):
            builder.build(source, **changed)

    def test_asset_bytes_are_still_verified_after_all_declared_hashes_agree(self):
        source, _, options = pack_fixture(); image = options['image_manifest']['images'][0]
        image['sha256'] = '0' * 64; review(options)['expectedImageSha256'] = '0' * 64
        review(options)['evidence'][1]['sha256'] = '0' * 64
        receipt(options).update(sha256='0' * 64, originalSha256='0' * 64,
                               publicImageSha256=builder.review_evidence_sha256(image))
        rehash_inspection(options); options['private_direct_image_evidence_manifest']['entries'][0]['sha256'] = '0' * 64
        with self.assertRaises(ValueError): builder.build(source, **options)

    def test_private_projection_keeps_typed_source_and_image_urls_out_of_public_guards(self):
        source, baseline, options = pack_fixture()
        expected, _ = builder.build(source, **options)
        evidence = review(options)['evidence']; digest = builder.review_evidence_sha256(evidence)
        projected = copy.deepcopy(options); review(projected)['evidence'] = {'privateEvidenceSha256': digest}
        with tempfile.TemporaryDirectory() as folder:
            ledger = Path(folder) / 'ledger.json'
            ledger.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND,
                                          'records': {digest: evidence}}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(ledger)}):
                actual, _ = builder.build(source, **projected)
                self.assertEqual(actual, expected)
                public_guard = projected['source_reviews_manifest']
                self.assertNotIn('https://', json.dumps(public_guard))
                changed = copy.deepcopy(projected); review(changed)['expectedSourceRow'] = 2
                with self.assertRaises(ValueError): builder.build(source, **changed)
        self.assertEqual(baseline['products'][0]['variants'][0]['label'], '4-pack · 1 L')

    def test_legacy_incomplete_pack_action_and_global_parser_are_not_broadened(self):
        source, baseline, options = pack_fixture()
        old = copy.deepcopy(options); r = review(old); r['action'] = 'resolve_verified_pack_identity'
        del r['expectedProductId']
        with self.assertRaisesRegex(ValueError, 'only enrich'):
            builder.build(source, **old)
        self.assertIsNone(builder.parse_verified_pack_container_label('4-pack · 250 mL · cartons'))
        unchanged, _ = builder.build(source)
        self.assertEqual(unchanged, baseline)
