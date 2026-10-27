"""Behavioral regression tests for safe catalog import, grouping and reconciliation."""
import csv
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('catalog_builder', ROOT / 'scripts/build_catalog.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def export(*records):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(builder.EXPECTED_HEADERS)
    for index, record in enumerate(records, 1):
        values = {'SKU': str(index), 'MAINUPC': '001234567890', 'ItemName': '',
                  'PackName': '', 'DEPNAME': 'Tequila', 'ItemPrice': '19.99',
                  'TotalQty_Multi': '0', 'ItemTypeDesc': ''}
        values.update(record)
        writer.writerow([values[field] for field in builder.EXPECTED_HEADERS])
    return stream.getvalue().encode()


def restoration_review(source, sanitized, uncertain=True, row=1):
    raw = list(csv.reader(io.StringIO(source.decode())))[row]
    return {'schemaVersion': 1, 'reviews': [{
        'sku': raw[0], 'expectedUpc': raw[1],
        'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
        'expectedSourceRecordSha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
        'action': 'restore_named_merchandise', 'sanitizedSourceName': sanitized,
        'forceSizeNeedsConfirmation': uncertain, 'reason': 'Reviewed ambiguous metadata removal',
    }]}


def comma_repair_review(source, row=1):
    raw = list(csv.reader(io.StringIO(source.decode())))[row]
    return {'schemaVersion': 1, 'reviews': [{
        'sku': raw[0], 'expectedUpc': raw[1],
        'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
        'expectedSourceRecordSha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
        'expectedRejoinedNameSha256': hashlib.sha256(','.join(raw[2:-5]).encode()).hexdigest(),
        'action': 'repair_unquoted_name_commas', 'reason': 'Exact SKU report corroborates comma fragments',
        'reportEvidence': [{'reportSha256': 'a' * 64, 'page': 54}],
    }]}


def report_manifest(*entries):
    result = []
    for i, fields in enumerate(entries, 1):
        entry = {'sku': str(1000 + i), 'name': 'Example Juice 12oz', 'pack': '',
                 'department': 'Juice', 'kind': 'merchandise',
                 'sourceNameSha256': hashlib.sha256(b'Example Juice 12oz').hexdigest(),
                 'evidence': [{'reportSha256': 'b' * 64, 'page': 1}],
                 'reviewedOn': '2026-10-27', 'reason': 'Exact report SKU and description reviewed'}
        entry.update(fields)
        result.append(entry)
    return {'schemaVersion': 1, 'records': result}


def repaired_image_fixture(source):
    review = comma_repair_review(source)
    catalog, _ = builder.build(source, source_reviews_manifest=review)
    sku = review['reviews'][0]['sku']
    product, variant = next((p, v) for p in catalog['products'] for v in p['variants'] if v['sku'] == sku)
    # Reuse a local approved asset to test integrity checks; the synthetic test
    # metadata is never written to the public catalog or approval manifests.
    image = copy.deepcopy(json.loads((ROOT / 'data/image-manifest.json').read_text())['images'][0])
    image.update(sku=sku, expectedUpc=variant['upc'], expectedName=product['name'], expectedLabel=variant['label'])
    review['reviews'][0]['imageIdentityReview'] = {
        'expectedName': product['name'], 'expectedLabel': variant['label'],
        'expectedImageSha256': image['sha256'],
        'verification': {key: True for key in ('brand', 'expression', 'size', 'packaging', 'upc', 'visual')},
        'evidence': [{'kind': kind, 'url': 'https://example.test/' + kind}
                     for kind in ('manufacturer', 'exact_upc_listing', 'inspected_package_photo')],
    }
    return review, {'schemaVersion': 1, 'images': [image]}


class ImportTests(unittest.TestCase):
    def test_exact_grouping_preserves_brand_flavor_strength_and_age(self):
        catalog, _ = builder.build(export(
            {'ItemName': '1800 Coconut 1 Liter', 'SKU': '001'},
            {'ItemName': '1800 Coconut 750ml', 'SKU': '002'},
            {'ItemName': '1800 Silver 750ml'},
            {'ItemName': '1800 Silver 100 P 1L'},
            {'ItemName': '818 Silver 750ml'},
            {'ItemName': '42 Below Vodka 750ml'},
            {'ItemName': 'Chivas Regal 12 Year 750ml'},
            {'ItemName': 'Chivas Regal 18 Year 750ml'},
            {'ItemName': '99 Bananas 50ml'},
            {'ItemName': '360 Vodka 750ml'},
            {'ItemName': '7UP 2L'},
        ))
        self.assertEqual(catalog['counts']['products'], 10)
        coconut = next(p for p in catalog['products'] if p['name'] == '1800 Coconut')
        self.assertEqual([v['sku'] for v in coconut['variants']], ['001', '002'])
        self.assertEqual(coconut['variants'][0]['upc'], '001234567890')
        names = {p['name'] for p in catalog['products']}
        self.assertIn('1800 Silver 100 P', names)
        self.assertIn('Chivas Regal 12 Year', names)
        self.assertIn('42 Below Vodka', names)
        self.assertIn('7UP', names)

    def test_cans_bottles_and_multipacks_remain_distinct_variants(self):
        catalog, _ = builder.build(export(
            {'ItemName': 'Example Lager 12pk 12ozCans', 'DEPNAME': 'Domestic'},
            {'ItemName': 'Example Lager 6pk 12ozBottles', 'DEPNAME': 'Domestic'},
            {'ItemName': 'Example Lager 16ozCan', 'PackName': 'Single Beer Tax', 'DEPNAME': 'Domestic'},
        ))
        self.assertEqual(catalog['counts']['products'], 1)
        labels = [v['label'] for v in catalog['products'][0]['variants']]
        self.assertEqual(labels, ['12-pack · 12 oz · cans', '6-pack · 12 oz · bottles', 'Single · 16 oz · cans'])

    def test_measurements_do_not_consume_numeric_brands_and_flavors(self):
        names = ['100 GRAND', '360 LEMON', '99 GRAPES', '1800 LIME']
        catalog, _ = builder.build(export(*[{'ItemName': name} for name in names]))
        self.assertEqual({p['name'] for p in catalog['products']}, set(names))
        self.assertTrue(all(p['variants'][0]['label'] == 'Size not specified' for p in catalog['products']))

    def test_long_units_and_fractions_are_not_short_prefixes(self):
        catalog, _ = builder.build(export(
            {'ItemName': 'DOMINO SUGAR 4LB', 'DEPNAME': 'GROCERY'},
            {'ItemName': 'ARIZONA ICED TEA 1GAL', 'DEPNAME': 'Juice'},
            {'ItemName': 'Cheetos puffs 3 3/8OZflamin hot', 'DEPNAME': 'Snacks'},
        ))
        labels = {p['name']: p['variants'][0]['label'] for p in catalog['products']}
        self.assertEqual(labels['DOMINO SUGAR'], '4 lb')
        self.assertEqual(labels['ARIZONA ICED TEA'], '1 gal')
        self.assertEqual(labels['Cheetos puffs flamin hot'], '3.375 oz')

    def test_pack_field_container_and_conflicting_title_dimensions(self):
        catalog, _ = builder.build(export(
            {'ItemName': 'Example Lager 12oz', 'PackName': '6pk Bottles', 'DEPNAME': 'Domestic'},
            {'ItemName': 'Example Vodka 750ml 1L'},
        ))
        labels = {p['name']: p['variants'][0]['label'] for p in catalog['products']}
        self.assertEqual(labels['Example Lager'], '6-pack · 12 oz · bottles')
        self.assertEqual(labels['Example Vodka'], 'Size needs confirmation')

    def test_explicitly_unidentified_shortcuts_are_private(self):
        catalog, _ = builder.build(export(
            {'ItemName': '12pk BeerWhat Kind?', 'DEPNAME': 'ShortCut'},
            {'ItemName': '***** your description ******', 'DEPNAME': 'Snacks'},
            {'ItemName': 'NEW ITEM', 'DEPNAME': 'MISC'},
            {'ItemName': '7UP ?', 'DEPNAME': 'Soda'},
        ))
        self.assertEqual(catalog['counts']['needsReview'], 3)
        self.assertEqual(catalog['counts']['publicVariants'], 1)
        self.assertEqual(catalog['products'][0]['name'], '7UP ?')

    def test_known_packaging_identity_conflict_keeps_skus_separate(self):
        catalog, audit = builder.build(export(
            {'SKU': '109', 'MAINUPC': '811538010412', 'ItemName': '1800 Silver 750ml'},
            {'SKU': '108', 'MAINUPC': '811538010300', 'ItemName': '1800 Silver 750ml'},
        ))
        self.assertEqual(catalog['counts']['products'], 2)
        self.assertEqual({p['name'] for p in catalog['products']}, {'1800 Silver'})
        self.assertIn('UPC-specific image research', audit['records'][0]['flags'][0])

    def test_conflicts_retain_sku_without_selecting_false_size(self):
        catalog, audit = builder.build(export(
            {'ItemName': '1800 Mango Margarita 1.75L', 'PackName': '1.5 Liter Wine '},
        ))
        self.assertEqual(catalog['products'][0]['name'], '1800 Mango Margarita')
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], 'Size needs confirmation')
        self.assertEqual(catalog['counts']['publicVariants'], 1)
        self.assertEqual(catalog['counts']['flaggedPublicVariants'], 1)
        self.assertEqual(audit['records'][0]['source']['PackName'], '1.5 Liter Wine ')

    def test_normalizes_equivalent_units(self):
        catalog, _ = builder.build(export({'ItemName': 'Example Vodka 1 Liter', 'PackName': '1000 ml Liquor T'}))
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '1 L')

    def test_decimal_product_expressions_and_strength_are_not_prices(self):
        catalog, _ = builder.build(export(
            {'ItemName': 'JOB 1.25 Rolling Papers', 'DEPNAME': 'Tobacco'},
            {'ItemName': 'JOB 1.5 Rolling Papers', 'DEPNAME': 'Tobacco'},
            {'ItemName': 'Spiced Jack No.94 Rum 750ml', 'DEPNAME': 'Rum'},
            {'ItemName': 'Heineken 0.0 A/C 12oz', 'DEPNAME': 'Imports'},
            {'ItemName': 'Example 40.5% ABV 750ml'},
        ))
        names = {p['name'] for p in catalog['products']}
        self.assertEqual(len(names), 5)
        self.assertIn('JOB 1.25 Rolling Papers', names)
        self.assertIn('Spiced Jack No.94 Rum', names)
        self.assertIn('Heineken 0.0 A/C', names)
        self.assertIn('Example 40.5% ABV', names)

    def test_joined_decimal_units_require_independent_corroboration(self):
        catalog, _ = builder.build(export(
            {'ItemName': 'Arbor Mist 1.5LPinot Grigio', 'PackName': '1.5 Liter Wine'},
            {'ItemName': 'Example 1.5 LIME', 'PackName': '1.5 Liter Wine'},
            {'ItemName': 'Arbor Mist 1.5LPinot Grigio', 'PackName': ''},
        ))
        self.assertEqual(catalog['counts']['publicVariants'], 1)
        self.assertEqual(catalog['counts']['needsReview'], 2)
        self.assertEqual(catalog['products'][0]['name'], 'Arbor Mist Pinot Grigio')
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '1.5 L')

    def test_unverified_or_wrong_identity_images_fail_closed(self):
        source = export({'ItemName': 'Example 750ml'})
        for manifest in (
            {'schemaVersion': 1, 'images': [{'sku': '999'}]},
            {'schemaVersion': 1, 'images': [{'sku': '1', 'expectedName': 'Different product'}]},
            {'schemaVersion': 1, 'images': [{'sku': '1', 'expectedName': 'Example',
               'expectedUpc': '001234567890', 'expectedLabel': '750 mL', 'verification': {'brand': True}}]},
        ):
            with self.assertRaises(ValueError):
                builder.build(source, image_manifest=manifest)

    def test_exact_match_without_professional_quality_review_is_rejected(self):
        source = export({'ItemName': 'Example 750ml'})
        manifest = {'schemaVersion': 1, 'images': [{'sku': '1', 'expectedName': 'Example',
                    'expectedUpc': '001234567890', 'expectedLabel': '750 mL',
                    'verification': dict.fromkeys(['brand', 'expression', 'size', 'packaging', 'visual'], True)}]}
        with self.assertRaisesRegex(ValueError, 'professional-quality'):
            builder.build(source, image_manifest=manifest)

    def test_no_silent_row_loss_and_no_price_or_quantity_export(self):
        catalog, audit = builder.build(export(
            {'ItemName': '$ 0.50', 'MAINUPC': '$ 0.50', 'DEPNAME': 'Snacks'},
            {'ItemName': 'Cook County', 'DEPNAME': 'Tax'},
            {'ItemName': 'Coffee Original 12oz', 'DEPNAME': 'Snacks'},
            {'ItemName': 'Lottery Pick Energy Drink 8.4oz', 'DEPNAME': 'Soda'},
            {'ItemName': 'test 1 single pack?', 'MAINUPC': 'test 1', 'DEPNAME': 'Imports'},
            {'ItemName': 'RC COLA 2L $1.49', 'DEPNAME': 'Soda'},
            {'ItemName': 'Acme Snack 1.99', 'DEPNAME': 'Snacks'},
        ))
        self.assertEqual(catalog['counts']['sourceRows'], 7)
        self.assertEqual(catalog['counts']['publicVariants'], 3)
        self.assertEqual(catalog['counts']['needsReview'], 2)
        self.assertEqual(catalog['counts']['excludedNonproducts'], 2)
        self.assertEqual(len(audit['records']), 7)
        self.assertEqual(audit['records'][0]['source']['ItemName'], '$ 0.50')
        public_json = json.dumps(catalog)
        for forbidden in ['ItemPrice', 'TotalQty', '$', '19.99', '1.49']:
            self.assertNotIn(forbidden, public_json)

    def test_malformed_csv_quarantined_without_losing_raw_fields(self):
        original = export({'ItemName': 'Example'})
        malformed = original + b'99,000123,Name,with,comma,,Snacks,4.99,0,\n'
        catalog, audit = builder.build(malformed)
        self.assertEqual(catalog['counts']['sourceRows'], 2)
        self.assertEqual(catalog['counts']['needsReview'], 1)
        self.assertEqual(len(audit['records'][1]['rawFields']), 10)
        self.assertIn('Malformed', audit['records'][1]['reason'])

    def test_guarded_csv_comma_repair_preserves_original_fields_and_sku(self):
        source = export() + b'002,000123456789,Example Mango,Lime 12oz,,Juice,4.99,-2,\n'
        raw = list(csv.reader(io.StringIO(source.decode())))[1]
        review = comma_repair_review(source)
        catalog, audit = builder.build(source, source_reviews_manifest=review)
        self.assertEqual(catalog['counts']['publicVariants'], 1)
        variant = catalog['products'][0]['variants'][0]
        self.assertEqual(variant['sku'], '002')
        self.assertEqual(variant['upc'], '000123456789')
        self.assertEqual(variant['label'], '12 oz')
        self.assertEqual(audit['records'][0]['sourceRow'], 1)
        self.assertEqual(audit['records'][0]['rawFields'], raw)
        self.assertEqual(audit['records'][0]['reconstructedFields'][:2], raw[:2])
        self.assertEqual(audit['records'][0]['reconstructedFields'][-5:], raw[-5:])
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example Mango,Lime 12oz')
        self.assertNotIn('4.99', json.dumps(catalog))
        self.assertNotIn('TotalQty', json.dumps(catalog))
        self.assertEqual(builder.build(source)[0]['counts']['needsReview'], 1)

    def test_csv_repair_rejects_stale_source_name_or_report_guards(self):
        source = export() + b'002,000123456789,Example Mango,Lime 12oz,,Juice,4.99,-2,\n'
        review = comma_repair_review(source)
        for changed in (source.replace(b'Mango', b'Peach'), source.replace(b'4.99', b'5.99'),
                        source.replace(b'000123456789', b'000123456788')):
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                builder.build(changed, source_reviews_manifest=review)
        wrong = copy.deepcopy(review)
        wrong['reviews'][0]['expectedRejoinedNameSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Rejoined CSV name'):
            builder.build(source, source_reviews_manifest=wrong)
        wrong['reviews'][0]['reportEvidence'][0]['page'] = 0
        with self.assertRaisesRegex(ValueError, 'report hash/page'):
            builder.build(source, source_reviews_manifest=wrong)
        with self.assertRaisesRegex(ValueError, 'extra source fields'):
            builder.build(export({'SKU': '002', 'ItemName': 'Ordinary item'}), source_reviews_manifest=review)

    def test_csv_repair_does_not_invent_barcode_pack_or_lottery_product(self):
        source = export() + b'9239,n080660958811,Victoria 12 Oz, 12 Pk,6pk Beer Tax,Imports,0.00,0,\n'
        review = comma_repair_review(source)
        catalog, audit = builder.build(source, source_reviews_manifest=review)
        variant = catalog['products'][0]['variants'][0]
        self.assertEqual(variant['upc'], '')
        self.assertEqual(variant['label'], 'Size needs confirmation')
        self.assertEqual(catalog['products'][0]['name'], 'Victoria')
        self.assertEqual(audit['records'][0]['source']['MAINUPC'], 'n080660958811')
        lottery = export() + b'29,653443117721,$1,000 A Week For Life $2$2,,Instant,2.00,0,\n'
        catalog, _ = builder.build(lottery, source_reviews_manifest=comma_repair_review(lottery))
        self.assertEqual(catalog['counts']['excludedNonproducts'], 1)
        self.assertEqual(catalog['products'], [])
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            builder.build(source, source_reviews_manifest=review,
                          image_manifest={'schemaVersion': 1, 'images': [{'sku': '9239'}]})

    def test_repaired_image_review_allows_only_its_exact_sku(self):
        source = export() + b'002,000123456789,Example Mango,Lime 12oz,,Juice,4.99,-2,\n'
        source += b'003,000123456789,Example Mango,Lime 12oz,,Juice,4.99,-2,\n'
        review, images = repaired_image_fixture(source)
        review['reviews'].extend(comma_repair_review(source, row=2)['reviews'])
        catalog, audit = builder.build(source, source_reviews_manifest=review, image_manifest=images)
        variants = {v['sku']: v for p in catalog['products'] for v in p['variants']}
        self.assertIsNotNone(variants['002']['image'])
        self.assertIsNone(variants['003']['image'])
        self.assertEqual(len(catalog['products']), 2)
        self.assertEqual(audit['verifiedImages'], 1)
        self.assertEqual([r['rawFields'] for r in audit['records']], list(csv.reader(io.StringIO(source.decode())))[1:])
        sibling = copy.deepcopy(images['images'][0])
        sibling['sku'] = '003'
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            builder.build(source, source_reviews_manifest=review,
                          image_manifest={'schemaVersion': 1, 'images': images['images'] + [sibling]})
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            builder.build(source.replace(b'4.99', b'5.99', 1), source_reviews_manifest=review, image_manifest=images)

    def test_repaired_image_review_rejects_stale_identity_hash_and_missing_image(self):
        source = export() + b'002,000123456789,Example Mango,Lime 12oz,,Juice,4.99,-2,\n'
        review, images = repaired_image_fixture(source)
        for field, value in [('expectedName', 'Other Flavor'), ('expectedLabel', '16 oz'),
                             ('expectedImageSha256', '0' * 64)]:
            wrong = copy.deepcopy(review)
            wrong['reviews'][0]['imageIdentityReview'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity mismatch|does not match exact review'):
                builder.build(source, source_reviews_manifest=wrong, image_manifest=images)
        for field, value in [('expectedUpc', '000123456788'), ('expectedName', 'Other Flavor'),
                             ('expectedLabel', '16 oz'), ('sha256', '0' * 64)]:
            wrong = copy.deepcopy(images)
            wrong['images'][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'does not match exact review'):
                builder.build(source, source_reviews_manifest=review, image_manifest=wrong)
        for absent in (None, {'schemaVersion': 1, 'images': []}, {'schemaVersion': 1, 'images': images['images'] * 2}):
            with self.assertRaisesRegex(ValueError, 'exactly one corresponding image'):
                builder.build(source, source_reviews_manifest=review, image_manifest=absent)
        wrong_review, wrong_image = copy.deepcopy(review), copy.deepcopy(images)
        wrong_review['reviews'][0]['imageIdentityReview']['expectedImageSha256'] = '0' * 64
        wrong_image['images'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'hash does not match'):
            builder.build(source, source_reviews_manifest=wrong_review, image_manifest=wrong_image)
        for field, value in [('professional', False), ('visual', False)]:
            wrong = copy.deepcopy(images)
            wrong['images'][0]['verification'][field] = value
            with self.assertRaisesRegex(ValueError, 'professional-quality'):
                builder.build(source, source_reviews_manifest=review, image_manifest=wrong)
        wrong = copy.deepcopy(images)
        wrong['images'][0]['license'] = ''
        with self.assertRaisesRegex(ValueError, 'requires license'):
            builder.build(source, source_reviews_manifest=review, image_manifest=wrong)

    def test_repaired_image_review_rejects_private_data_incomplete_evidence_and_unknown_size(self):
        source = export() + b'002,000123456789,Example Mango,Lime 12oz,,Juice,4.99,-2,\n'
        review, images = repaired_image_fixture(source)
        for key in ('brand', 'expression', 'size', 'packaging', 'upc', 'visual'):
            wrong = copy.deepcopy(review)
            wrong['reviews'][0]['imageIdentityReview']['verification'][key] = False
            with self.assertRaisesRegex(ValueError, 'every identity check'):
                builder.build(source, source_reviews_manifest=wrong, image_manifest=images)
        mutations = [lambda x: x.update(rawPath='/Users/private/source.csv'),
                     lambda x: x.update(expectedName='Example $4.99'),
                     lambda x: x['evidence'].pop(),
                     lambda x: x['evidence'][0].update(url='file:///Users/private/photo.jpg')]
        for mutate in mutations:
            wrong = copy.deepcopy(review)
            mutate(wrong['reviews'][0]['imageIdentityReview'])
            with self.assertRaises(ValueError):
                builder.build(source, source_reviews_manifest=wrong, image_manifest=images)
        wrong = copy.deepcopy(review)
        wrong['reviews'][0]['action'] = 'size_needs_confirmation'
        for field in ('reportEvidence', 'expectedRejoinedNameSha256'):
            wrong['reviews'][0].pop(field)
        with self.assertRaisesRegex(ValueError, 'unsupported or private fields'):
            builder.build(source, source_reviews_manifest=wrong, image_manifest=images)
        for title in (b'Example Mango,Lime', b'Example Mango,Lime 12oz 16oz'):
            uncertain = export() + b'002,000123456789,' + title + b',,Juice,4.99,-2,\n'
            unknown_review, unknown_image = repaired_image_fixture(uncertain)
            with self.assertRaisesRegex(ValueError, 'known UPC and size'):
                builder.build(uncertain, source_reviews_manifest=unknown_review, image_manifest=unknown_image)

    def test_report_additions_preserve_original_rows_and_never_merge_or_infer_upc(self):
        source = export({'ItemName': 'Example Juice 12oz', 'DEPNAME': 'Juice'})
        base, base_audit = builder.build(source)
        reports = report_manifest({'sku': '01001'}, {'sku': '1002'})
        catalog, audit = builder.build(source, report_additions_manifest=reports)
        self.assertEqual(catalog['counts']['originalSourceRows'], 1)
        self.assertEqual(catalog['counts']['reportSourceRows'], 2)
        self.assertEqual(catalog['counts']['sourceRows'], 3)
        self.assertEqual(catalog['counts']['products'], 3)
        self.assertEqual(audit['records'][:1], base_audit['records'])
        self.assertEqual([r['sourceRow'] for r in audit['records']], [1, 2, 3])
        self.assertEqual([r['sourceOrigin'] for r in audit['records'][1:]], ['report', 'report'])
        variants = {v['sku']: v for p in catalog['products'] for v in p['variants']}
        for sku in ('01001', '1002'):
            self.assertEqual(variants[sku]['upc'], '')
            self.assertIsNone(variants[sku]['image'])
        self.assertEqual(audit['originalSourceDispositions'], {'retained_variant': 1})
        self.assertEqual(audit['reportSourceDispositions'], {'retained_variant': 2})
        self.assertEqual(builder.build(source)[0], base)
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            builder.build(source, report_additions_manifest=reports,
                          image_manifest={'schemaVersion': 1, 'images': [{'sku': '01001'}]})

    def test_report_metadata_rejects_prices_private_fields_and_bad_evidence(self):
        cases = [{'name': 'Example $1.99'}, {'name': 'Example 1.99'}, {'pack': '2 for 99c'},
                 {'department': '$4'}, {'reason': 'cost 1.99'}, {'upc': '001234567890'},
                 {'cost': '1.50'}, {'sourceNameSha256': 'invalid'},
                 {'evidence': []}, {'evidence': [{'reportSha256': 'a' * 64, 'page': True}]},
                 {'forceSizeNeedsConfirmation': 'yes'}]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                builder.build(export(), report_additions_manifest=report_manifest(fields))
        with self.assertRaisesRegex(ValueError, 'collides'):
            builder.build(export({'SKU': '1001', 'ItemName': 'Existing'}),
                          report_additions_manifest=report_manifest({}))
        with self.assertRaisesRegex(ValueError, 'unique string SKUs'):
            builder.build(export(), report_additions_manifest=report_manifest({'sku': '1001'}, {'sku': '1001'}))

    def test_report_controls_are_exact_and_unknown_size_remains_unknown(self):
        entries = [dict(sku=sku, name=name, kind='pos_control')
                   for sku, name in builder.REPORT_POS_CONTROLS.items()]
        entries.append({'sku': '1005', 'name': 'Example Berry', 'forceSizeNeedsConfirmation': True})
        catalog, _ = builder.build(export(), report_additions_manifest=report_manifest(*entries))
        self.assertEqual(catalog['counts']['excludedNonproducts'], 4)
        self.assertEqual(catalog['counts']['publicVariants'], 1)
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], 'Size needs confirmation')
        for fields in ({'sku': '-2000', 'name': 'Other adjustment', 'kind': 'pos_control'},
                       {'sku': '1006', 'name': 'Card Void Adjustment', 'kind': 'pos_control'},
                       {'sku': '0', 'name': 'Scan Group', 'kind': 'merchandise'}):
            with self.assertRaises(ValueError):
                builder.build(export(), report_additions_manifest=report_manifest(fields))

    def test_report_name_correction_uses_original_report_hash_without_inventing_upc(self):
        reports = report_manifest({'name': 'Example PINEAPLE 12oz',
                                   'sourceNameSha256': hashlib.sha256(b'Original private report title').hexdigest()})
        correction = {'sku': '1001', 'expectedUpc': '',
                      'expectedNameSha256': reports['records'][0]['sourceNameSha256'],
                      'replaceTextFrom': 'PINEAPLE', 'replaceTextTo': 'PINEAPPLE',
                      'evidence': [{'url': 'https://manufacturer.example/flavors'}]}
        names = {'schemaVersion': 1, 'corrections': [correction]}
        catalog, audit = builder.build(export(), report_additions_manifest=reports, name_manifest=names)
        self.assertEqual(catalog['products'][0]['name'], 'Example PINEAPPLE')
        self.assertEqual(catalog['products'][0]['variants'][0]['upc'], '')
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '12 oz')
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example PINEAPLE 12oz')
        self.assertEqual(audit['records'][0]['correctedSourceName'], 'Example PINEAPPLE 12oz')
        self.assertEqual(audit['records'][0]['nameCorrection'], correction)
        for field, value in [('expectedUpc', '001234567890'), ('expectedNameSha256', '0' * 64)]:
            wrong = copy.deepcopy(names)
            wrong['corrections'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'Report name override identity mismatch'):
                builder.build(export(), report_additions_manifest=reports, name_manifest=wrong)
        wrong = copy.deepcopy(names)
        wrong['corrections'][0]['replaceTextFrom'] = 'MISSING'
        with self.assertRaisesRegex(ValueError, 'fragment does not match exactly once'):
            builder.build(export(), report_additions_manifest=reports, name_manifest=wrong)
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            builder.build(export(), report_additions_manifest=reports, name_manifest=names,
                          image_manifest={'schemaVersion': 1, 'images': [{'sku': '1001'}]})
        with self.assertRaisesRegex(ValueError, 'Report-origin identity reviews are not supported'):
            builder.build(export(), report_additions_manifest=reports,
                          name_manifest={'schemaVersion': 1, 'identityReviews': [correction]})
        with self.assertRaisesRegex(ValueError, 'absent from all reviewed sources'):
            builder.build(export(), name_manifest=names)

    def test_guarded_container_material_preserves_identity_and_size(self):
        source = export({'SKU': '2846', 'MAINUPC': '001234567890',
                         'ItemName': 'Example Vodka 1.75L bottles', 'DEPNAME': 'Vodka'})
        raw = list(csv.reader(io.StringIO(source.decode())))[1]
        review = {'schemaVersion': 1, 'reviews': [{
            'sku': '2846', 'expectedUpc': raw[1],
            'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
            'expectedSourceRecordSha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
            'action': 'clarify_container_material', 'containerMaterial': 'PET',
            'reason': 'Exact SKU report explicitly identifies container material',
            'reportEvidence': [{'reportSha256': 'c' * 64, 'page': 20}]}]}
        base, _ = builder.build(source)
        catalog, audit = builder.build(source, source_reviews_manifest=review)
        variant = catalog['products'][0]['variants'][0]
        self.assertEqual(catalog['products'][0]['name'], base['products'][0]['name'])
        self.assertEqual(variant['label'], '1.75 L · PET bottles')
        self.assertEqual(variant['upc'], raw[1])
        self.assertEqual(audit['records'][0]['rawFields'], raw)
        changed = copy.deepcopy(review)
        changed['reviews'][0]['containerMaterial'] = 'glass'
        self.assertEqual(builder.build(source, source_reviews_manifest=changed)[0]['products'][0]['variants'][0]['label'], '1.75 L · glass bottles')
        changed['reviews'][0]['containerMaterial'] = 'aluminum'
        with self.assertRaisesRegex(ValueError, 'material must be glass or PET'):
            builder.build(source, source_reviews_manifest=changed)
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            builder.build(source.replace(b'1.75L', b'1.00L'), source_reviews_manifest=review)
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            builder.build(source, source_reviews_manifest=review,
                          image_manifest={'schemaVersion': 1, 'images': [{'sku': '2846'}]})
        for title in ('Example Vodka', 'Example Vodka 4pk', 'Example Vodka 1.75L 750ml', 'Example Vodka 1.75L cans'):
            uncertain = export({'SKU': '2846', 'MAINUPC': raw[1], 'ItemName': title, 'DEPNAME': 'Vodka'})
            fields = list(csv.reader(io.StringIO(uncertain.decode())))[1]
            changed = copy.deepcopy(review)
            changed['reviews'][0]['expectedNameSha256'] = hashlib.sha256(title.encode()).hexdigest()
            changed['reviews'][0]['expectedSourceRecordSha256'] = hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'Container clarification'):
                builder.build(uncertain, source_reviews_manifest=changed)

    def test_duplicate_names_and_upcs_keep_every_sku(self):
        catalog, _ = builder.build(export({'ItemName': 'Example Vodka 750ml'}, {'ItemName': 'Example Vodka 750ml'}))
        self.assertEqual(catalog['counts']['products'], 1)
        self.assertEqual(len(catalog['products'][0]['variants']), 2)

    def test_unspecified_expression_with_different_upcs_is_not_merged(self):
        catalog, audit = builder.build(export(
            {'ItemName': 'TAYLORS CANDY', 'MAINUPC': '000111222333', 'DEPNAME': 'Candy'},
            {'ItemName': 'TAYLORS CANDY', 'MAINUPC': '000111222334', 'DEPNAME': 'Candy'},
            {'ItemName': 'TAYLORS CANDY', 'MAINUPC': '000111222333', 'DEPNAME': 'Candy'},
        ))
        self.assertEqual(catalog['counts']['products'], 2)
        self.assertEqual(catalog['counts']['publicVariants'], 3)
        self.assertEqual(sorted(len(p['variants']) for p in catalog['products']), [1, 2])
        self.assertTrue(all('grouping isolated' in r['flags'][-1] for r in audit['records']))

    def test_name_corrections_are_exact_and_source_spelling_stays_private(self):
        source = export({'ItemName': 'Johny Walker Black Label 375ml'})
        override = {'schemaVersion': 1, 'corrections': [{
            'sku': '1', 'expectedNameSha256': hashlib.sha256(b'Johny Walker Black Label 375ml').hexdigest(),
            'expectedUpc': '001234567890', 'replaceTextFrom': 'Johny Walker', 'replaceTextTo': 'Johnnie Walker',
            'scope': 'brand_spelling_only', 'evidence': [{'url': 'https://www.johnniewalker.com/'}],
        }]}
        catalog, audit = builder.build(source, name_manifest=override)
        self.assertEqual(catalog['products'][0]['name'], 'Johnnie Walker Black Label')
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Johny Walker Black Label 375ml')
        names = builder.name_audit(catalog, audit)
        self.assertEqual(names['summary']['sourceRowsScanned'], 1)
        self.assertEqual(names['summary']['evidenceBackedCorrections'], 1)
        self.assertEqual(names['summary']['fullyVerifiedNames'], 0)
        with self.assertRaises(ValueError):
            builder.build(export({'ItemName': 'Different'}), name_manifest=override)

    def test_name_queue_does_not_reopen_a_corrected_typo(self):
        original = 'Example rosted peanuts 4oz'
        names = {'schemaVersion': 1, 'corrections': [{
            'sku': '1', 'expectedUpc': '001234567890',
            'expectedNameSha256': hashlib.sha256(original.encode()).hexdigest(),
            'replaceTextFrom': 'rosted', 'replaceTextTo': 'roasted',
            'scope': 'ordinary_word_spelling_only',
            'evidence': [{'url': 'https://dictionary.cambridge.org/us/dictionary/english/roasted'}],
        }]}
        catalog, audit = builder.build(export({'ItemName': original}), name_manifest=names)
        queue = builder.name_audit(catalog, audit)
        self.assertEqual(queue['records'][0]['originalName'], original)
        self.assertEqual(queue['records'][0]['suggestedCorrections'], [])
        self.assertEqual(queue['summary']['evidenceBackedCorrections'], 1)
        self.assertEqual(queue['summary']['fullyVerifiedNames'], 0)

    def test_unchanged_name_verification_preserves_catalog_and_is_not_a_correction(self):
        source = export({'ItemName': 'Example Juice 750ml'})
        verification = {
            'sku': '1', 'expectedUpc': '001234567890',
            'expectedNameSha256': hashlib.sha256(b'Example Juice 750ml').hexdigest(),
            'expectedPublicName': 'Example Juice', 'expectedLabel': '750 mL',
            'verifiedOn': '2026-10-27',
            'verification': dict.fromkeys(('brand', 'expression', 'upc', 'visual'), True),
            'evidence': [{'kind': kind, 'url': 'https://example.test/' + kind}
                         for kind in ('manufacturer', 'exact_upc_listing', 'inspected_package_photo')],
        }
        manifest = {'schemaVersion': 1, 'nameVerifications': [verification]}
        catalog, audit = builder.build(source, name_manifest=manifest)
        baseline, _ = builder.build(source)
        self.assertEqual(catalog, baseline)
        summary = builder.name_audit(catalog, audit)['summary']
        self.assertEqual(summary['evidenceBackedCorrections'], 0)
        self.assertEqual(summary['nameVerificationRecords'], 1)
        self.assertEqual(summary['fullyVerifiedNames'], 1)
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example Juice 750ml')
        for key, value in [('expectedUpc', 'other'), ('expectedNameSha256', '0' * 64),
                           ('expectedPublicName', 'Other Juice'), ('expectedLabel', '1 L'), ('sku', '2')]:
            wrong = copy.deepcopy(manifest)
            wrong['nameVerifications'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, name_manifest=wrong)
        duplicate = copy.deepcopy(manifest)
        duplicate['nameVerifications'] *= 2
        with self.assertRaisesRegex(ValueError, 'unique SKU'):
            builder.build(source, name_manifest=duplicate)
        for mutate in [lambda x: x.update(rawPath='/Users/private/file.csv'),
                       lambda x: x.update(expectedPublicName='Example $4.99'),
                       lambda x: x['verification'].update(visual=False),
                       lambda x: x['evidence'].pop(),
                       lambda x: x['evidence'][0].update(url='file:///private/photo.jpg')]:
            wrong = copy.deepcopy(manifest)
            mutate(wrong['nameVerifications'][0])
            with self.assertRaises(ValueError):
                builder.build(source, name_manifest=wrong)

    def test_guarded_source_review_suppresses_suspect_size_without_guessing(self):
        source = export({'ItemName': 'DORITOS Spicy 1/8Oz', 'SKU': '2950', 'MAINUPC': '028400420754', 'DEPNAME': 'Snacks'})
        raw = list(csv.reader(io.StringIO(source.decode())))[1]
        review = {'schemaVersion': 1, 'reviews': [{
            'sku': '2950', 'expectedUpc': raw[1], 'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
            'expectedSourceRecordSha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
            'action': 'size_needs_confirmation', 'reason': 'Suspected incomplete source measurement',
        }]}
        catalog, audit = builder.build(source, source_reviews_manifest=review)
        variant = catalog['products'][0]['variants'][0]
        self.assertEqual(variant['label'], 'Size needs confirmation')
        self.assertIsNone(variant['image'])
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'DORITOS Spicy 1/8Oz')
        with self.assertRaises(ValueError):
            builder.build(source.replace(b'1/8Oz', b'1/4Oz'), source_reviews_manifest=review)
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            builder.build(source, source_reviews_manifest=review, image_manifest={'schemaVersion': 1, 'images': [{'sku': '2950'}]})

    def test_restoration_preserves_source_and_confirmed_pack_without_guessing_size(self):
        original = 'Example Berry 6pk 11.2Bottles'
        source = export({'ItemName': original, 'PackName': '6pk Beer Tax'})
        review = restoration_review(source, 'Example Berry 6pk Bottles')
        catalog, audit = builder.build(source, source_reviews_manifest=review)
        product = catalog['products'][0]
        self.assertEqual(product['name'], 'Example Berry')
        self.assertEqual(product['variants'][0]['label'], '6-pack · Size needs confirmation · bottles')
        self.assertEqual(audit['records'][0]['source']['ItemName'], original)
        self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])
        # The record guard covers private metadata too, including changes outside the title.
        for changed in (source.replace(b'11.2', b'12.2'), source.replace(b'19.99', b'20.99'),
                        source.replace(b'001234567890', b'001234567891')):
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                builder.build(changed, source_reviews_manifest=review)

    def test_restored_sku_is_isolated_and_never_inherits_an_image(self):
        source = export({'ItemName': 'Example Berry 1.5'}, {'ItemName': 'Example Berry 750ml'})
        review = restoration_review(source, 'Example Berry')
        catalog, _ = builder.build(source, source_reviews_manifest=review)
        self.assertEqual(catalog['counts']['publicVariants'], 2)
        self.assertEqual(catalog['counts']['products'], 2)
        self.assertTrue(all(v['image'] is None for p in catalog['products'] for v in p['variants']))
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            builder.build(source, source_reviews_manifest=review,
                          image_manifest={'schemaVersion': 1, 'images': [{'sku': '1'}]})

    def test_restoration_applies_guarded_spelling_to_the_reviewed_description(self):
        original = 'Johny Walker Black Label 1.75'
        source = export({'ItemName': original})
        review = restoration_review(source, 'Johny Walker Black Label')
        names = {'schemaVersion': 1, 'corrections': [{
            'sku': '1', 'expectedUpc': '001234567890',
            'expectedNameSha256': hashlib.sha256(original.encode()).hexdigest(),
            'replaceTextFrom': 'Johny Walker', 'replaceTextTo': 'Johnnie Walker',
            'scope': 'brand_spelling_only', 'evidence': [{'url': 'https://www.johnniewalker.com/'}],
        }]}
        catalog, audit = builder.build(source, name_manifest=names, source_reviews_manifest=review)
        self.assertEqual(catalog['products'][0]['name'], 'Johnnie Walker Black Label')
        self.assertEqual(audit['records'][0]['source']['ItemName'], original)
        self.assertEqual(audit['records'][0]['correctedSourceName'], 'Johnnie Walker Black Label 1.75')
        self.assertEqual(audit['records'][0]['sanitizedSourceName'], 'Johnnie Walker Black Label')

    def test_price_only_or_invented_restoration_cannot_bypass_quarantine(self):
        for original, sanitized in (('$ 0.50', 'Example Candy'), ('0.50 juice', 'juice'),
                                    ('Example Berry 1.5', 'Different Brand Berry')):
            source = export({'ItemName': original})
            with self.assertRaises(ValueError):
                builder.build(source, source_reviews_manifest=restoration_review(source, sanitized))
        catalog, _ = builder.build(export({'ItemName': '$ 0.50'}, {'ItemName': 'NEW ITEM'}))
        self.assertEqual(catalog['counts']['needsReview'], 2)

    def test_restoration_rejects_remaining_price_text_and_private_manifest_fields(self):
        source = export({'ItemName': 'Example Berry 2 for 1.99'})
        for sanitized in ('Example Berry $1.99', 'Example Berry 1.99', 'Example Berry 2 for 1.99'):
            with self.assertRaises(ValueError):
                builder.build(source, source_reviews_manifest=restoration_review(source, sanitized))
        review = restoration_review(source, 'Example Berry', uncertain=False)
        review['reviews'][0]['rawFields'] = ['private data']
        with self.assertRaisesRegex(ValueError, 'private fields'):
            builder.build(source, source_reviews_manifest=review)

    def test_clear_price_promotion_removal_keeps_independent_size(self):
        source = export({'ItemName': 'Example Berry 16oz 2 for 1.99'})
        catalog, _ = builder.build(source, source_reviews_manifest=restoration_review(source, 'Example Berry 16oz', uncertain=False))
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '16 oz')

    def test_evidence_backed_identity_resolution_is_exact_and_cannot_override_stated_size(self):
        source = export({'SKU': '7630', 'MAINUPC': '040000001607', 'ItemName': 'SKITTLES'},
                        {'SKU': '2', 'MAINUPC': '040000001607', 'ItemName': 'SKITTLES'})
        actual = json.loads((ROOT / 'data/source-reviews.json').read_text())
        entry = dict(next(r for r in actual['reviews'] if r['sku'] == '7630'))
        raw = list(csv.reader(io.StringIO(source.decode())))[1]
        entry['expectedSourceRecordSha256'] = hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        review = {'schemaVersion': 1, 'reviews': [entry]}
        catalog, audit = builder.build(source, source_reviews_manifest=review)
        by_sku = {v['sku']: (p, v) for p in catalog['products'] for v in p['variants']}
        self.assertEqual(by_sku['7630'][0]['name'], 'Skittles Original')
        self.assertEqual(by_sku['7630'][1]['label'], '2.17 oz')
        self.assertEqual(by_sku['2'][0]['name'], 'SKITTLES')
        self.assertEqual(by_sku['2'][1]['label'], 'Size not specified')
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'SKITTLES')
        self.assertIsNone(by_sku['7630'][1]['image'])
        names = builder.name_audit(catalog, audit)
        self.assertEqual(names['summary']['exactIdentitiesResolved'], 1)
        self.assertEqual(names['summary']['fullyVerifiedNames'], 1)
        self.assertEqual(names['summary']['evidenceBackedCorrections'], 0)
        self.assertTrue(all(isinstance(r['outcome'], str) for r in names['records']))
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            builder.build(source.replace(b'040000001607', b'040000001608'), source_reviews_manifest=review)
        with self.assertRaisesRegex(ValueError, 'Image identity mismatch'):
            builder.build(source, source_reviews_manifest=review,
                          image_manifest={'schemaVersion': 1, 'images': [{'sku': '7630', 'expectedName': 'Skittles Wild Berry'}]})
        for key in ('visual', 'size', 'packaging', 'upc'):
            incomplete = json.loads(json.dumps(review))
            incomplete['reviews'][0]['verification'][key] = False
            with self.assertRaisesRegex(ValueError, 'identity checks'):
                builder.build(source, source_reviews_manifest=incomplete)
        stated = export({'SKU': '7630', 'MAINUPC': '040000001607', 'ItemName': 'SKITTLES 3oz'})
        raw = list(csv.reader(io.StringIO(stated.decode())))[1]
        entry['expectedNameSha256'] = hashlib.sha256(raw[2].encode()).hexdigest()
        entry['expectedSourceRecordSha256'] = hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'stated source size'):
            builder.build(stated, source_reviews_manifest=review)

    def test_packaging_spelling_correction_revalidates_image_identity(self):
        original_name = 'Example BOTTEL 12oz'
        source = export({'ItemName': original_name})
        correction = {'schemaVersion': 1, 'corrections': [{
            'sku': '1', 'expectedUpc': '001234567890',
            'expectedNameSha256': hashlib.sha256(original_name.encode()).hexdigest(),
            'replaceTextFrom': 'BOTTEL', 'replaceTextTo': 'BOTTLE',
            'scope': 'ordinary_word_spelling_only',
            'evidence': [{'url': 'https://dictionary.cambridge.org/us/dictionary/english/bottle'}],
        }]}
        catalog, audit = builder.build(source, name_manifest=correction)
        product = catalog['products'][0]
        self.assertEqual(product['name'], 'Example')
        self.assertEqual(product['variants'][0]['label'], '12 oz · bottles')
        self.assertEqual(audit['records'][0]['source']['ItemName'], original_name)
        self.assertEqual(builder.name_audit(catalog, audit)['summary']['fullyVerifiedNames'], 0)
        old_photo_identity = {'schemaVersion': 1, 'images': [{
            'sku': '1', 'expectedUpc': '001234567890',
            'expectedName': 'Example BOTTEL', 'expectedLabel': '12 oz',
        }]}
        with self.assertRaisesRegex(ValueError, 'Image identity mismatch'):
            builder.build(source, name_manifest=correction, image_manifest=old_photo_identity)

    def test_explicit_resolution_of_prior_uncertain_identity_keeps_source_and_isolation(self):
        source = export({'ItemName': 'Example Berry 1.75mL', 'PackName': '1.75 L'},
                        {'ItemName': 'Example Berry 1.75L'})
        review = restoration_review(source, 'Example Berry')
        review['reviews'][0]['action'] = 'size_needs_confirmation'
        review['reviews'][0].pop('sanitizedSourceName')
        review['reviews'][0].pop('forceSizeNeedsConfirmation')
        resolution = {
            'expectedName': 'Example Berry', 'expectedLabel': 'Size needs confirmation',
            'verifiedName': 'Example Berry', 'verifiedLabel': '1.75 L',
            'verification': dict.fromkeys(('brand', 'expression', 'size', 'packaging', 'upc', 'visual'), True),
            'reason': 'Exact source UPC and inspected package resolve the conflicting title unit; original fields remain private.',
            'evidence': [{'kind': kind, 'url': 'https://example.test/' + kind, 'supports': 'Exact consumer identity reviewed'}
                         for kind in ('manufacturer', 'exact_upc_listing', 'inspected_package_photo')],
        }
        review['reviews'][0]['identityResolution'] = resolution
        catalog, audit = builder.build(source, source_reviews_manifest=review)
        self.assertEqual(len(catalog['products']), 2)
        by_sku = {v['sku']: v for p in catalog['products'] for v in p['variants']}
        self.assertEqual(by_sku['1']['label'], '1.75 L')
        self.assertEqual(audit['records'][0]['previousPublicIdentity']['label'], 'Size needs confirmation')
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example Berry 1.75mL')
        self.assertEqual(audit['records'][0]['source']['PackName'], '1.75 L')
        self.assertEqual(builder.name_audit(catalog, audit)['summary']['exactIdentitiesResolved'], 1)
        for key, value in [('expectedName', 'Other Berry'), ('expectedLabel', 'Size not specified'),
                           ('verifiedLabel', '1.75'), ('verifiedName', 'Example $4.99')]:
            wrong = copy.deepcopy(review)
            wrong['reviews'][0]['identityResolution'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, source_reviews_manifest=wrong)
        for mutate in [lambda x: x['verification'].update(size=False),
                       lambda x: x['evidence'].pop(),
                       lambda x: x.update(privatePath='/Users/private/source.csv'),
                       lambda x: x['evidence'][0].update(supports='Source $4.99'),
                       lambda x: x['evidence'][0].update(url='file:///private/source')]:
            wrong = copy.deepcopy(review)
            mutate(wrong['reviews'][0]['identityResolution'])
            with self.assertRaises(ValueError):
                builder.build(source, source_reviews_manifest=wrong)
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            builder.build(source.replace(b'19.99', b'29.99', 1), source_reviews_manifest=review)
        # Identity evidence permits only the reviewed image mapping and does
        # not weaken the existing professional or byte-integrity requirements.
        image = copy.deepcopy(json.loads((ROOT / 'data/image-manifest.json').read_text())['images'][0])
        image.update(sku='1', expectedUpc='001234567890', expectedName='Example Berry', expectedLabel='1.75 L')
        catalog, _ = builder.build(source, source_reviews_manifest=review, image_manifest={'schemaVersion': 1, 'images': [image]})
        by_sku = {v['sku']: v for p in catalog['products'] for v in p['variants']}
        self.assertIsNotNone(by_sku['1']['image'])
        self.assertIsNone(by_sku['2']['image'])
        for key, value in [('expectedLabel', '750 mL'), ('sha256', '0' * 64)]:
            wrong = copy.deepcopy(image)
            wrong[key] = value
            with self.assertRaises(ValueError):
                builder.build(source, source_reviews_manifest=review, image_manifest={'schemaVersion': 1, 'images': [wrong]})
        image['verification']['professional'] = False
        with self.assertRaises(ValueError):
            builder.build(source, source_reviews_manifest=review, image_manifest={'schemaVersion': 1, 'images': [image]})

    def test_identity_resolutions_require_retained_upc_matching_source_guard(self):
        for action in ('resolve_verified_identity', 'size_needs_confirmation', 'restore_named_merchandise'):
            for upc in ('', '   ', 'not-a-barcode', ' 001234567890 ', '78938793'):
                fields = {'ItemName': 'Example Berry', 'MAINUPC': upc}
                if action == 'size_needs_confirmation':
                    fields.update(ItemName='Example Berry 1.75mL', PackName='1.75 L')
                elif action == 'restore_named_merchandise':
                    fields['ItemName'] = 'Example Berry 1.75'
                source = export(fields)
                review = restoration_review(source, 'Example Berry')
                entry = review['reviews'][0]
                entry['action'] = action
                if action != 'restore_named_merchandise':
                    entry.pop('sanitizedSourceName')
                    entry.pop('forceSizeNeedsConfirmation')
                identity = {
                    'verifiedName': 'Example Berry', 'verifiedLabel': '1.75 L',
                    'verification': dict.fromkeys(('brand', 'expression', 'size', 'packaging', 'upc', 'visual'), True),
                    'evidence': [{'kind': kind, 'url': 'https://example.test/' + kind,
                                  'supports': 'Exact consumer identity reviewed'}
                                 for kind in ('manufacturer', 'exact_upc_listing', 'inspected_package_photo')],
                }
                if action == 'resolve_verified_identity':
                    entry.update(identity)
                else:
                    entry['identityResolution'] = {
                        **identity, 'expectedName': 'Example Berry', 'expectedLabel': 'Size needs confirmation',
                        'reason': 'Exact source barcode and inspected package resolve this identity.',
                    }
                with self.subTest(action=action, upc=upc):
                    if upc == '78938793':
                        # A legitimate eight-digit consumer barcode remains exact.
                        catalog, _ = builder.build(source, source_reviews_manifest=review)
                        self.assertEqual(catalog['products'][0]['variants'][0]['upc'], upc)
                        continue
                    # An invalid raw code must not become a verified identity or
                    # authorize a photo after classification withholds that code.
                    image = copy.deepcopy(json.loads((ROOT / 'data/image-manifest.json').read_text())['images'][0])
                    retained_upc = upc.strip() if upc.strip().isdigit() else ''
                    image.update(sku='1', expectedUpc=retained_upc, expectedName='Example Berry', expectedLabel='1.75 L')
                    for images in (None, {'schemaVersion': 1, 'images': [image]}):
                        with self.assertRaisesRegex(ValueError, 'retained public UPC matching the source guard'):
                            builder.build(source, source_reviews_manifest=review, image_manifest=images)

    def test_real_merchandise_in_shortcut_is_retained(self):
        catalog, _ = builder.build(export({'ItemName': 'ALKA SELTZER REG/COLD', 'DEPNAME': 'ShortCut'}))
        self.assertEqual(catalog['counts']['publicVariants'], 1)

    def test_private_input_inside_repo_rejected(self):
        with self.assertRaises(ValueError):
            builder.safe_private_path(ROOT / 'private-input' / 'Inventory.csv')


class GeneratedCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / 'catalog.json').read_text())

    def test_all_public_skus_unique_and_counted(self):
        variants = [v for p in self.catalog['products'] for v in p['variants']]
        self.assertEqual(len(variants), self.catalog['counts']['publicVariants'])
        self.assertEqual(len({v['sku'] for v in variants}), len(variants))
        self.assertEqual(len({v['sourceRow'] for v in variants}), len(variants))
        self.assertEqual(len(self.catalog['products']), self.catalog['counts']['products'])
        c = self.catalog['counts']
        self.assertEqual(c['sourceRows'], c['publicVariants'] + c['needsReview'] + c['excludedNonproducts'])
        self.assertEqual(c.get('originalSourceRows', c['sourceRows']), 9881)
        self.assertEqual(c['sourceRows'], c.get('originalSourceRows', c['sourceRows']) + c.get('reportSourceRows', 0))

    def test_strict_public_allowlist(self):
        departments = {d['id'] for c in self.catalog['categories'] for d in c['departments']}
        for product in self.catalog['products']:
            self.assertEqual(set(product), {'id', 'name', 'category', 'department', 'variants'})
            self.assertIn(product['department'], departments)
            self.assertFalse(builder.PRICE_RE.search(product['name']))
            self.assertFalse(builder.has_ambiguous_decimal(product['name']))
            for variant in product['variants']:
                self.assertEqual(set(variant), {'sku', 'upc', 'label', 'sourceRow', 'image'})
                self.assertIsInstance(variant['sku'], str)
                self.assertIsInstance(variant['upc'], str)
                self.assertNotIn('$', variant['label'])
                if variant['image'] is not None:
                    self.assertEqual(set(variant['image']), {'src', 'alt', 'sourcePage', 'license', 'licenseUrl', 'attribution'})

    @unittest.skipUnless(os.environ.get('QUICKSTOP_PRIVATE_INPUT'), 'Set QUICKSTOP_PRIVATE_INPUT for immutable source reconciliation check')
    def test_private_original_reconciliation(self):
        source = Path(os.environ['QUICKSTOP_PRIVATE_INPUT']).read_bytes()
        manifest_path = ROOT / 'data/image-manifest.json'
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        names_path = ROOT / 'data/name-corrections.json'
        names = json.loads(names_path.read_text()) if names_path.exists() else None
        reviews_path = ROOT / 'data/source-reviews.json'
        reviews = json.loads(reviews_path.read_text()) if reviews_path.exists() else None
        reports_path = ROOT / 'data/report-additions.json'
        reports = json.loads(reports_path.read_text()) if reports_path.exists() else None
        catalog, audit = builder.build(source, image_manifest=manifest, name_manifest=names,
                                       source_reviews_manifest=reviews, report_additions_manifest=reports)
        self.assertEqual(audit['sourceSha256'], builder.BASELINE_HASH)
        self.assertEqual(catalog, self.catalog)
        self.assertEqual(catalog['counts']['originalSourceRows'], 9881)
        self.assertEqual(len(audit['records']), 9881 + catalog['counts']['reportSourceRows'])
        originals = audit['records'][:9881]
        self.assertEqual({r['sourceRow'] for r in originals}, set(range(1, 9882)))
        self.assertEqual([r['rawFields'] for r in originals], list(csv.reader(io.StringIO(source.decode('utf-8-sig'))))[1:])


if __name__ == '__main__':
    unittest.main()
