"""Behavioral regression tests for safe catalog import, grouping and reconciliation."""
import csv
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
        self.assertEqual(c['sourceRows'], 9881)

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
        catalog, audit = builder.build(source, image_manifest=manifest, name_manifest=names, source_reviews_manifest=reviews)
        self.assertEqual(audit['sourceSha256'], builder.BASELINE_HASH)
        self.assertEqual(catalog, self.catalog)
        self.assertEqual(len(audit['records']), 9881)
        self.assertEqual({r['sourceRow'] for r in audit['records']}, set(range(1, 9882)))


if __name__ == '__main__':
    unittest.main()
