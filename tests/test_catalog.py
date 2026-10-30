"""Behavioral regression tests for safe catalog import, grouping and reconciliation."""
import csv
import copy
import hashlib
from html.parser import HTMLParser
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


def direct_reference_image_fixture(source, sku='1'):
    catalog, _ = builder.build(source)
    product, variant = next((p, v) for p in catalog['products'] for v in p['variants'] if v['sku'] == sku)
    # Synthetic identity metadata reuses a checked local asset only inside tests.
    image = copy.deepcopy(json.loads((ROOT / 'data/image-manifest.json').read_text())['images'][0])
    for field in ('license', 'licenseUrl', 'attribution'):
        image.pop(field, None)
    image.update(sku=sku, expectedUpc=variant['upc'], expectedName=product['name'],
                 expectedLabel=variant['label'], alt=product['name'] + ' packaging',
                 usageBasis='user_authorized_direct_reference', attributionRequired=False)
    return image


def category_review(source, category='food', row=1, name_manifest=None):
    raw = list(csv.reader(io.StringIO(source.decode())))[row]
    catalog, _ = builder.build(source, name_manifest=name_manifest)
    product, variant = next((p, v) for p in catalog['products'] for v in p['variants'] if v['sku'] == raw[0])
    return {'sku': raw[0], 'expectedUpc': raw[1],
            'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
            'expectedSourceRecordSha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
            'expectedDepartment': raw[4], 'expectedPublicName': product['name'],
            'expectedLabel': variant['label'], 'expectedCategory': product['category'],
            'category': category, 'reviewedOn': '2026-10-27',
            'reason': 'Exact reviewed product belongs in this shopping category.',
            'evidence': [{'kind': 'manufacturer', 'url': 'https://example.test/reviewed-product'}]}


def public_name_review(source, proposed, row=1, category=None):
    raw = list(csv.reader(io.StringIO(source.decode())))[row]
    catalog, _ = builder.build(source)
    product, variant = next((p, v) for p in catalog['products'] for v in p['variants'] if v['sku'] == raw[0])
    review = {'sku': raw[0], 'expectedUpc': raw[1], 'expectedSourceRow': row,
              'expectedRawFieldsSha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
              'expectedPublicName': product['name'], 'expectedLabel': variant['label'],
              'expectedCategory': category or product['category'], 'expectedDepartment': product['department'],
              'proposedPublicTitle': proposed, 'reason': 'Exact UPC listing confirms the product title.',
              'evidence': [{'kind': 'exact_upc_listing', 'url': f'https://example.test/upc/{raw[1]}',
                            'supports': f'Exact UPC {raw[1]} identifies the reviewed expression.'}]}
    return {'schemaVersion': 1, 'reviews': [review]}


def manufacturer_correction(source, replacement, public_name, label):
    # Synthetic evidence exercises guards; it is never a real source approval
    # and is never written to any public manifest or product catalog.
    raw = list(csv.reader(io.StringIO(source.decode())))[1]
    url = 'https://smartlabel.pepsico.info/synthetic-unit-test'
    return {'sku': raw[0], 'expectedUpc': raw[1],
            'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
            'replaceTextFrom': raw[2], 'replaceTextTo': replacement,
            'scope': builder.MANUFACTURER_NAME_SCOPE, 'verifiedOn': '2026-10-27',
            'expectedLabel': label, 'verifiedPublicName': public_name,
            'manufacturerRecord': {
                'publisher': 'PepsiCo', 'productUrl': url, 'publisherEvidenceUrl': url,
                'observedUpc': raw[1], 'observedName': public_name,
                'retrievedOn': '2026-10-27', 'sourceSha256': hashlib.sha256(b'synthetic test evidence').hexdigest(),
                'retiredNotice': False,
            },
            'evidence': [{'kind': 'manufacturer_exact_upc_record', 'url': url,
                          'supports': 'Synthetic test fixture; no real product claim.'}]}


class ImportTests(unittest.TestCase):
    def test_guarded_public_name_correction_changes_only_the_reviewed_title(self):
        source = export(
            {'ItemName': 'Example Grape 12oz', 'SKU': '100', 'MAINUPC': '012345678905', 'DEPNAME': 'Juice'},
            {'ItemName': 'Example Grape 24oz', 'SKU': '101', 'MAINUPC': '012345678912', 'DEPNAME': 'Juice'},
        )
        baseline, _ = builder.build(source)
        reviews = public_name_review(source, 'Example Concord Grape')
        catalog, audit = builder.build(source, public_name_reviews_manifest=reviews)
        before = {v['sku']: (p, v) for p in baseline['products'] for v in p['variants']}
        after = {v['sku']: (p, v) for p in catalog['products'] for v in p['variants']}

        self.assertEqual(after['100'][0]['name'], 'Example Concord Grape')
        self.assertEqual(after['101'][0]['name'], before['101'][0]['name'])
        self.assertEqual(after['100'][1], before['100'][1])
        self.assertEqual(after['101'][1], before['101'][1])
        self.assertEqual(after['100'][0]['category'], before['100'][0]['category'])
        self.assertEqual(after['100'][0]['department'], before['100'][0]['department'])
        self.assertNotEqual(after['100'][0]['id'], after['101'][0]['id'])
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example Grape 12oz')
        self.assertEqual(audit['publicNameCorrections'], 1)
        self.assertEqual(builder.name_audit(catalog, audit)['summary']['publicTitleCorrections'], 1)

    def test_guarded_public_name_correction_runs_after_category_review(self):
        source = export({'ItemName': 'Example Crackers 4oz', 'DEPNAME': 'MISC',
                         'MAINUPC': '012345678905'})
        categories = {'schemaVersion': 1, 'reviews': [category_review(source, category='food')]}
        names = public_name_review(source, 'Example Savory Crackers', category='food')
        catalog, audit = builder.build(source, category_reviews_manifest=categories,
                                       public_name_reviews_manifest=names)
        product = catalog['products'][0]
        self.assertEqual(product['name'], 'Example Savory Crackers')
        self.assertEqual(product['category'], 'food')
        self.assertEqual(product['department'], 'MISC')
        self.assertEqual(product['variants'][0]['label'], '4 oz')
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example Crackers 4oz')

    def test_public_name_review_cannot_hide_an_identity_conflict(self):
        original = 'Example Grape 12oz'
        source = export({'ItemName': original, 'SKU': '100', 'MAINUPC': '012345678905', 'DEPNAME': 'Juice'})
        names = {'schemaVersion': 1, 'identityReviews': [{
            'sku': '100', 'expectedUpc': '012345678905',
            'expectedNameSha256': hashlib.sha256(original.encode()).hexdigest(),
            'action': 'retain_flagged', 'reason': 'UPC identifies a different product',
            'evidence': [{'url': 'https://example.test/conflict'}]}]}
        proposal = public_name_review(source, 'Example Concord Grape')
        proposal['reviews'][0]['expectedPublicName'] += ' (identity needs confirmation)'
        with self.assertRaisesRegex(ValueError, 'cannot override an unresolved identity conflict'):
            builder.build(source, name_manifest=names, public_name_reviews_manifest=proposal)

    def test_default_public_name_manifest_is_repository_data_file(self):
        args = builder.build_argument_parser().parse_args([
            '--input', '/tmp/private.csv', '--private-output', '/tmp/private-output'])
        self.assertEqual(args.public_name_reviews, ROOT / 'data/public-name-reviews.json')
        self.assertEqual(builder.DEFAULT_PUBLIC_NAME_REVIEWS, args.public_name_reviews)

    def test_guarded_public_name_correction_rejects_stale_guards_and_unsafe_proposals(self):
        source = export({'ItemName': 'Example Grape 12oz', 'SKU': '100',
                         'MAINUPC': '012345678905', 'DEPNAME': 'Juice'})
        proposal = public_name_review(source, 'Example Concord Grape')
        for field, value in [('expectedUpc', '012345678912'), ('expectedSourceRow', 2),
                             ('expectedPublicName', 'Stale Title'),
                             ('expectedRawFieldsSha256', '0' * 64),
                             ('expectedLabel', '13 oz'), ('expectedCategory', 'food'),
                             ('expectedDepartment', 'Soda')]:
            stale = copy.deepcopy(proposal)
            stale['reviews'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                builder.build(source, public_name_reviews_manifest=stale)

        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            builder.build(source.replace(b'19.99', b'29.99'), public_name_reviews_manifest=proposal)
        for mutate in [lambda x: x.update(proposedPublicTitle='Example Grape $4.99'),
                       lambda x: x.update(proposedPublicTitle='Juice'),
                       lambda x: x.update(evidence=[]),
                       lambda x: x['evidence'][0].update(url='file:///private/source'),
                       lambda x: x['evidence'][0].update(url='https://example.test/source',
                                                        supports='UPC omitted from evidence')]:
            wrong = copy.deepcopy(proposal)
            mutate(wrong['reviews'][0])
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                builder.build(source, public_name_reviews_manifest=wrong)

        missing = copy.deepcopy(proposal)
        missing['reviews'][0]['sku'] = '999'
        with self.assertRaisesRegex(ValueError, 'applicable original-source merchandise SKU'):
            builder.build(source, public_name_reviews_manifest=missing)

        for field, value in [('currentRawSourceName', 'private raw title'),
                             ('privatePath', '/Users/private/input.csv'),
                             ('catalogSha256Snapshot', 'a' * 64)]:
            private_field = copy.deepcopy(proposal)
            private_field['reviews'][0][field] = value
            with self.subTest(private_field=field), self.assertRaisesRegex(ValueError, 'private fields'):
                builder.build(source, public_name_reviews_manifest=private_field)

    def test_manufacturer_name_correction_preserves_size_source_and_absent_photo(self):
        source = export({'ItemName': 'Example G2 Lem 16oz bottles', 'MAINUPC': '012345678905'})
        entry = manufacturer_correction(source, 'Example G2 Lemon 16oz bottles', 'Example G2 Lemon', '16 oz · bottles')
        entry['manufacturerRecord']['observedUpc'] = '00012345678905'
        catalog, audit = builder.build(source, name_manifest={'schemaVersion': 1, 'corrections': [entry]})
        product = catalog['products'][0]
        self.assertEqual(product['name'], 'Example G2 Lemon')
        self.assertEqual(product['variants'][0]['label'], '16 oz · bottles')
        self.assertEqual(product['variants'][0]['upc'], '012345678905')
        self.assertIsNone(product['variants'][0]['image'])
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example G2 Lem 16oz bottles')
        names = builder.name_audit(catalog, audit)
        self.assertEqual(names['records'][0]['verifiedScope'], builder.MANUFACTURER_NAME_SCOPE)
        self.assertEqual(names['summary']['fullyVerifiedNames'], 1)
        self.assertEqual(names['summary']['manufacturerRecordVerifiedNames'], 1)

    def test_manufacturer_name_correction_cannot_change_units_packs_or_brand_numbers(self):
        source = export({'ItemName': 'Example G2 Lem 6pk 16oz bottles', 'MAINUPC': '012345678905'})
        entry = manufacturer_correction(source, 'Example G2 Lemon 6pk 16oz bottles', 'Example G2 Lemon', '6-pack · 16 oz · bottles')
        for replacement in ['Example Lemon 6pk 16oz bottles', 'Example G2 Lemon 12pk 16oz bottles',
                            'Example G2 Lemon 6pk 20oz bottles', 'Example G2 Lemon 6pk 16oz cans']:
            bad = copy.deepcopy(entry)
            bad['replaceTextTo'] = replacement
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                builder.build(source, name_manifest={'schemaVersion': 1, 'corrections': [bad]})
        for key, value in [('expectedLabel', '20 oz'), ('verifiedPublicName', 'Other'),
                           ('replaceTextFrom', 'Lem'), ('scope', 'brand_spelling_only')]:
            bad = copy.deepcopy(entry)
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, name_manifest={'schemaVersion': 1, 'corrections': [bad]})
        pint = export({'ItemName': 'Example Lem 1pint', 'MAINUPC': '012345678905'})
        bad = manufacturer_correction(pint, 'Example Lemon', 'Example Lemon', 'Size not specified')
        with self.assertRaises(ValueError):
            builder.build(pint, name_manifest={'schemaVersion': 1, 'corrections': [bad]})

    def test_manufacturer_name_record_rejects_unowned_sources_or_different_gtin(self):
        source = export({'ItemName': 'Example Lem 16oz', 'MAINUPC': '012345678905'})
        entry = manufacturer_correction(source, 'Example Lemon 16oz', 'Example Lemon', '16 oz')
        for field, value in [('publisher', 'Unreviewed vendor'),
                             ('productUrl', 'https://smartlabel.pepsico.info.evil.test/product'),
                             ('productUrl', 'https://user@smartlabel.pepsico.info/product'),
                             ('publisherEvidenceUrl', 'https://example.test/source'),
                             ('observedUpc', '112345678902'), ('observedUpc', '012345678906'),
                             ('observedUpc', '123456'), ('observedName', 'Product $4'),
                             ('observedName', 'See /Users/example/private.csv'),
                             ('sourceSha256', ''), ('retrievedOn', 'today'), ('retiredNotice', 'false')]:
            bad = copy.deepcopy(entry)
            bad['manufacturerRecord'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                builder.build(source, name_manifest={'schemaVersion': 1, 'corrections': [bad]})

    def test_manufacturer_expression_number_addition_requires_primary_name_evidence(self):
        source = export({'ItemName': 'Example Grape 16oz', 'MAINUPC': '012345678905'})
        entry = manufacturer_correction(source, 'Example G2 Grape 16oz', 'Example G2 Grape', '16 oz')
        catalog, _ = builder.build(source, name_manifest={'schemaVersion': 1, 'corrections': [entry]})
        self.assertEqual(catalog['products'][0]['name'], 'Example G2 Grape')
        entry['manufacturerRecord']['observedName'] = 'Example Grape'
        with self.assertRaises(ValueError):
            builder.build(source, name_manifest={'schemaVersion': 1, 'corrections': [entry]})

    def test_manufacturer_name_delegation_requires_owned_evidence_url(self):
        source = export({'ItemName': 'Example Lem 16oz', 'MAINUPC': '012345678905'})
        entry = manufacturer_correction(source, 'Example Lemon 16oz', 'Example Lemon', '16 oz')
        entry['manufacturerRecord']['productUrl'] = 'https://smartlabel.myproduct.info/012345678905-0001-en-US/index.html'
        entry['manufacturerRecord']['publisherEvidenceUrl'] = 'https://smartlabel.pepsico.info/sitemap.xml'
        entry['evidence'][0]['url'] = entry['manufacturerRecord']['productUrl']
        builder.build(source, name_manifest={'schemaVersion': 1, 'corrections': [entry]})
        for field, value in [('publisherEvidenceUrl', 'https://smartlabel.myproduct.info/sitemap.xml'),
                             ('publisherEvidenceUrl', 'https://smartlabel.pepsico.info/unrelated-page'),
                             ('publisherEvidenceUrl', 'https://smartlabel.pepsico.info/sitemap.xml?unrelated=1'),
                             ('productUrl', 'https://smartlabel.myproduct.info/112345678902-0001-en-US/index.html')]:
            bad = copy.deepcopy(entry)
            bad['manufacturerRecord'][field] = value
            bad['evidence'][0]['url'] = bad['manufacturerRecord']['productUrl']
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                builder.build(source, name_manifest={'schemaVersion': 1, 'corrections': [bad]})

    def test_manufacturer_name_only_cannot_change_strength_units_or_equivalent_volume_label(self):
        for original, replacement, public_name, label in [
            ('Example Lem 40% ABV 16oz', 'Example Lemon 40 Proof 16oz', 'Example Lemon 40 Proof', '16 oz'),
            ('Example Lem 1000mL', 'Example Lemon 1L', 'Example Lemon', '1 L'),
            ('Example Lem 16oz', 'Example Lemon 16 fl oz', 'Example Lemon', '16 oz'),
        ]:
            source = export({'ItemName': original, 'MAINUPC': '012345678905'})
            entry = manufacturer_correction(source, replacement, public_name, label)
            with self.subTest(original=original), self.assertRaises(ValueError):
                builder.build(source, name_manifest={'schemaVersion': 1, 'corrections': [entry]})

    def test_manufacturer_unchanged_name_is_explicitly_nonvisual_and_not_a_correction(self):
        source = export({'ItemName': 'Example Lemon 16oz', 'MAINUPC': '012345678905'})
        correction = manufacturer_correction(source, 'Example Lemon 16oz', 'Example Lemon', '16 oz')
        entry = {key: correction[key] for key in ('sku', 'expectedUpc', 'expectedNameSha256',
                 'expectedLabel', 'verifiedOn', 'evidence', 'manufacturerRecord')}
        entry.update(expectedPublicName='Example Lemon', verificationMethod='manufacturer_exact_upc_record',
                     verification={'brand': True, 'expression': True, 'upc': True, 'visual': False})
        entry['manufacturerRecord']['retiredNotice'] = True
        manifest = {'schemaVersion': 1, 'nameVerifications': [entry]}
        catalog, audit = builder.build(source, name_manifest=manifest)
        baseline, _ = builder.build(source)
        self.assertEqual(catalog, baseline)
        summary = builder.name_audit(catalog, audit)['summary']
        self.assertEqual(summary['evidenceBackedCorrections'], 0)
        self.assertEqual(summary['nameVerificationRecords'], 1)
        self.assertEqual(summary['manufacturerRecordVerifiedNames'], 1)
        for mutate in [lambda x: x['verification'].update(visual=True),
                       lambda x: x.pop('verificationMethod'),
                       lambda x: x.update(verificationMethod='other'),
                       lambda x: x.update(expectedLabel='20 oz'),
                       lambda x: x['evidence'][0].update(url='https://example.test/unrelated')]:
            wrong = copy.deepcopy(manifest)
            mutate(wrong['nameVerifications'][0])
            with self.assertRaises(ValueError):
                builder.build(source, name_manifest=wrong)

    def test_manufacturer_name_review_rejects_extra_private_or_visual_fields(self):
        source = export({'ItemName': 'Example Lem 16oz', 'MAINUPC': '012345678905'})
        entry = manufacturer_correction(source, 'Example Lemon 16oz', 'Example Lemon', '16 oz')
        for mutate in [lambda x: x.update(visual=True),
                       lambda x: x['manufacturerRecord'].update(localPath='/private/raw.html'),
                       lambda x: x['evidence'][0].update(supports='Source $2'),
                       lambda x: x['evidence'][0].update(supports='See /home/example/private.csv')]:
            bad = copy.deepcopy(entry)
            mutate(bad)
            with self.assertRaises(ValueError):
                builder.build(source, name_manifest={'schemaVersion': 1, 'corrections': [bad]})

    def test_terminal_bottle_abbreviations_preserve_variant_packages(self):
        catalog, _ = builder.build(export(
            {'ItemName': '1800 Coconut 750ML BTL', 'PackName': '750 ml'},
            {'ItemName': '1800 Coconut 50ML BTLS', 'PackName': '10pk'},
            {'ItemName': '1800 Silver 750ML BTL', 'PackName': '750 ml'},
            {'ItemName': 'Example BTL Edition 12oz'},
        ))
        variants = {v['sku']: (p['name'], v['label']) for p in catalog['products'] for v in p['variants']}
        self.assertEqual(variants['1'], ('1800 Coconut', '750 mL · bottles'))
        self.assertEqual(variants['2'], ('1800 Coconut', '10-pack · 50 mL · bottles'))
        self.assertEqual(variants['3'], ('1800 Silver', '750 mL · bottles'))
        self.assertEqual(variants['4'], ('Example BTL Edition', '12 oz'))
        self.assertEqual(catalog['counts']['publicVariants'], 4)
        self.assertEqual(catalog['counts']['products'], 3)

    def test_bottle_abbreviation_survives_unresolved_size_conflict(self):
        source = export({'ItemName': 'Example Sangria 355ML BTL', 'PackName': '375 ml'})
        catalog, audit = builder.build(source)
        product = catalog['products'][0]
        self.assertEqual(product['name'], 'Example Sangria BTL')
        self.assertEqual(product['variants'][0]['label'], 'Size needs confirmation')
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example Sangria 355ML BTL')
        self.assertEqual(audit['records'][0]['source']['PackName'], '375 ml')

    def test_category_override_splits_only_reviewed_sku_and_preserves_source_department(self):
        source = export({'ItemName': 'Example Crackers 4oz', 'DEPNAME': 'MISC'},
                        {'ItemName': 'Example Crackers 8oz', 'DEPNAME': 'MISC'},
                        {'ItemName': 'Other Pasta 12oz', 'DEPNAME': 'GROCERY'})
        baseline, _ = builder.build(source)
        manifest = {'schemaVersion': 1, 'reviews': [category_review(source)]}
        catalog, audit = builder.build(source, category_reviews_manifest=manifest)
        by_sku = {v['sku']: (p, v) for p in catalog['products'] for v in p['variants']}
        before = {v['sku']: (p, v) for p in baseline['products'] for v in p['variants']}
        self.assertEqual(by_sku['1'][0]['category'], 'food')
        self.assertEqual(by_sku['2'][0]['category'], 'essentials')
        self.assertEqual(by_sku['2'][0]['id'], before['2'][0]['id'])
        self.assertEqual(by_sku['3'][0], before['3'][0])
        self.assertEqual(catalog['counts']['publicVariants'], 3)
        self.assertEqual(catalog['counts']['products'], 3)
        for sku in by_sku:
            self.assertEqual(by_sku[sku][1], before[sku][1])
            self.assertEqual(by_sku[sku][0]['department'], before[sku][0]['department'])
        raw = list(csv.reader(io.StringIO(source.decode())))[1:]
        self.assertEqual([r['rawFields'] for r in audit['records']], raw)
        self.assertEqual(audit['records'][0]['source']['DEPNAME'], 'MISC')
        self.assertEqual(audit['categoryReviews'], 1)
        self.assertNotIn('categoryReview', audit['records'][1])
        pairs = {(c['id'], d['id']) for c in catalog['categories'] for d in c['departments']}
        self.assertEqual(pairs, {('food', 'MISC'), ('food', 'GROCERY'), ('essentials', 'MISC')})
        for category in catalog['categories']:
            for department in category['departments']:
                self.assertEqual(department['sourceLabel'], department['id'])

    def test_category_overrides_group_reviewed_siblings_without_merging_original_departments(self):
        source = export({'ItemName': 'Example Crackers 4oz', 'DEPNAME': 'MISC'},
                        {'ItemName': 'Example Crackers 8oz', 'DEPNAME': 'MISC'},
                        {'ItemName': 'Example Crackers 12oz', 'DEPNAME': 'Snacks'})
        manifest = {'schemaVersion': 1, 'reviews': [category_review(source, row=i) for i in (1, 2)]}
        catalog, _ = builder.build(source, category_reviews_manifest=manifest)
        self.assertEqual(catalog['counts']['products'], 2)
        moved = next(p for p in catalog['products'] if p['department'] == 'MISC')
        self.assertEqual([v['sku'] for v in moved['variants']], ['1', '2'])
        self.assertTrue(all(p['category'] == 'food' for p in catalog['products']))

    def test_category_reviews_reject_stale_source_public_identity_and_unretained_rows(self):
        source = export({'ItemName': 'Example Crackers 4oz', 'DEPNAME': 'MISC'})
        entry = category_review(source)
        for field, value in [('expectedUpc', '001234567891'), ('expectedNameSha256', 'a' * 64),
                             ('expectedSourceRecordSha256', 'b' * 64), ('expectedDepartment', 'Snacks'),
                             ('expectedPublicName', 'Different Crackers'), ('expectedLabel', '8 oz'),
                             ('expectedCategory', 'other')]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity mismatch'):
                builder.build(source, category_reviews_manifest={'schemaVersion': 1, 'reviews': [{**entry, field: value}]})
        with self.assertRaisesRegex(ValueError, 'source identity mismatch'):
            builder.build(source.replace(b'19.99', b'29.99'),
                          category_reviews_manifest={'schemaVersion': 1, 'reviews': [entry]})
        with self.assertRaisesRegex(ValueError, 'applicable original-source'):
            builder.build(source, category_reviews_manifest={'schemaVersion': 1, 'reviews': [{**entry, 'sku': '999'}]})
        name_manifest = {'schemaVersion': 1, 'identityReviews': [{
            'sku': entry['sku'], 'expectedUpc': entry['expectedUpc'],
            'expectedNameSha256': entry['expectedNameSha256'], 'reason': 'Unresolved exact identity',
            'evidence': [{'url': 'https://example.test/conflict'}]}]}
        with self.assertRaisesRegex(ValueError, 'retained merchandise'):
            builder.build(source, name_manifest=name_manifest,
                          category_reviews_manifest={'schemaVersion': 1, 'reviews': [entry]})

    def test_conflicting_identity_can_remain_visible_without_grouping_or_photo(self):
        source = export({'ItemName': 'Example Chips 3oz'}, {'ItemName': 'Example Chips 4oz'})
        entry = {'sku': '1', 'expectedUpc': '001234567890',
                 'expectedNameSha256': hashlib.sha256(b'Example Chips 3oz').hexdigest(),
                 'action': 'retain_flagged', 'reason': 'UPC identifies another product',
                 'evidence': [{'url': 'https://example.test/conflict'}]}
        catalog, audit = builder.build(source, name_manifest={'schemaVersion': 1, 'identityReviews': [entry]})
        self.assertEqual(catalog['counts']['publicVariants'], 2)
        self.assertEqual(len(catalog['products']), 2)
        flagged = next(p for p in catalog['products'] if p['variants'][0]['sku'] == '1')
        self.assertIn('identity needs confirmation', flagged['name'])
        self.assertIsNone(flagged['variants'][0]['image'])
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example Chips 3oz')
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            builder.build(source.replace(b'Example Chips 3oz', b'Example Chips 5oz'),
                          name_manifest={'schemaVersion': 1, 'identityReviews': [entry]})

    def test_identity_conflict_summary_is_not_masked_by_simultaneous_name_correction(self):
        original = 'Example Chips 3oz'
        source = export({'ItemName': original, 'SKU': '2705', 'MAINUPC': '012345678905'})
        name_hash = hashlib.sha256(original.encode()).hexdigest()
        names = {'schemaVersion': 1,
                 'corrections': [{'sku': '2705', 'expectedUpc': '012345678905',
                                  'expectedNameSha256': name_hash, 'replaceTextFrom': 'Example',
                                  'replaceTextTo': 'Example Brand', 'scope': 'product_name_spelling',
                                  'evidence': [{'url': 'https://example.test/name'}]}],
                 'identityReviews': [{'sku': '2705', 'expectedUpc': '012345678905',
                                      'expectedNameSha256': name_hash,
                                      'action': 'retain_flagged', 'reason': 'UPC identity remains unresolved',
                                      'evidence': [{'url': 'https://example.test/upc-conflict'}]}]}
        catalog, audit = builder.build(source, name_manifest=names)
        names_audit = builder.name_audit(catalog, audit)
        self.assertEqual(names_audit['records'][0]['outcome'], 'identity_conflict_needs_review')
        self.assertEqual(names_audit['records'][0]['correctedSourceName'], 'Example Brand Chips 3oz')
        self.assertEqual(names_audit['summary']['identityConflicts'], 1)
        self.assertEqual(names_audit['summary']['evidenceBackedCorrections'], 1)
        self.assertEqual(names_audit['summary']['fullyVerifiedNames'], 0)

    def test_category_review_checks_corrected_public_name_but_original_source_hash(self):
        source = export({'ItemName': 'Exampel Crackers 4oz', 'DEPNAME': 'MISC'})
        old_review = category_review(source)
        names = {'schemaVersion': 1, 'corrections': [{
            'sku': '1', 'expectedUpc': old_review['expectedUpc'],
            'expectedNameSha256': old_review['expectedNameSha256'],
            'replaceTextFrom': 'Exampel', 'replaceTextTo': 'Example',
            'evidence': [{'url': 'https://example.test/name'}]}]}
        current = category_review(source, name_manifest=names)
        catalog, _ = builder.build(source, name_manifest=names,
                                    category_reviews_manifest={'schemaVersion': 1, 'reviews': [current]})
        self.assertEqual(catalog['products'][0]['name'], 'Example Crackers')
        with self.assertRaisesRegex(ValueError, 'public identity mismatch'):
            builder.build(source, name_manifest=names,
                          category_reviews_manifest={'schemaVersion': 1, 'reviews': [old_review]})

    def test_category_review_schema_blocks_broad_rules_and_private_metadata(self):
        source = export({'ItemName': 'Example Crackers 4oz', 'DEPNAME': 'MISC'})
        entry = category_review(source)
        invalid = [dict(entry, category='unknown'), dict(entry, category=['food']),
                   dict(entry, category='essentials'), dict(entry, departmentRule='MISC'),
                   dict(entry, rawFields=['private']), dict(entry, reason='Example $2'),
                   dict(entry, reason='See /Users/example/private.csv'),
                   dict(entry, reviewedOn='today'), dict(entry, expectedUpc=''),
                   dict(entry, evidence=[{'kind': 'private', 'url': 'file:///private/source'}]),
                   dict(entry, evidence=[{'kind': 'manufacturer', 'url': 'https://example.test/ bad'}])]
        for value in ('Source $4.99', 'See /home/example/private.csv', 'file:///private/source'):
            candidate = copy.deepcopy(entry)
            candidate['evidence'][0]['supports'] = value
            invalid.append(candidate)
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                builder.build(source, category_reviews_manifest={'schemaVersion': 1, 'reviews': [candidate]})
        with self.assertRaisesRegex(ValueError, 'unique string SKUs'):
            builder.build(source, category_reviews_manifest={'schemaVersion': 1, 'reviews': [entry, entry]})

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

    def test_direct_reference_photo_has_no_invented_license_or_sibling_assignment(self):
        source = export({'ItemName': 'Example Berry 750ml'}, {'ItemName': 'Example Berry 375ml'})
        image = direct_reference_image_fixture(source)
        catalog, audit = builder.build(source, image_manifest={'schemaVersion': 1, 'images': [image]})
        variants = {v['sku']: v for p in catalog['products'] for v in p['variants']}
        self.assertEqual(audit['verifiedImages'], 1)
        self.assertEqual(set(variants['1']['image']),
                         {'src', 'alt', 'sourcePage', 'usageBasis', 'attributionRequired'})
        self.assertEqual(variants['1']['image']['usageBasis'], 'user_authorized_direct_reference')
        self.assertIs(variants['1']['image']['attributionRequired'], False)
        self.assertIsNone(variants['2']['image'])
        self.assertNotIn('data-sku="1"', builder.render_image_credits(catalog))

    def test_direct_reference_rights_basis_cannot_claim_license_or_suppress_legacy_credits(self):
        source = export({'ItemName': 'Example Berry 750ml'})
        image = direct_reference_image_fixture(source)
        for field, value in [('usageBasis', 'copyright_permission_granted'),
                             ('attributionRequired', True), ('attributionRequired', 0),
                             ('license', ''), ('license', 'CC BY 4.0'), ('licenseUrl', ''),
                             ('attribution', ''), ('privateRightsNotes', '/Users/private/source')]:
            changed = copy.deepcopy(image)
            changed[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                builder.build(source, image_manifest={'schemaVersion': 1, 'images': [changed]})
        for field in ('usageBasis', 'attributionRequired'):
            changed = copy.deepcopy(image)
            changed.pop(field)
            with self.subTest(missing=field), self.assertRaises(ValueError):
                builder.build(source, image_manifest={'schemaVersion': 1, 'images': [changed]})

    def test_direct_reference_photo_keeps_exact_identity_file_and_quality_guards(self):
        source = export({'ItemName': 'Example Berry 750ml'})
        image = direct_reference_image_fixture(source)
        for field, value in [('expectedUpc', '001234567891'), ('expectedName', 'Example Lime'),
                             ('expectedLabel', '375 mL'), ('sha256', '0' * 64),
                             ('src', 'images/products/../../catalog.json'),
                             ('sourcePage', 'javascript:alert(1)'),
                             ('sourceImageUrl', 'file:///private/photo.jpg'),
                             ('sourceImageUrl', 'https://example.test/photo extra'),
                             ('alt', 'Example $4.99'), ('verifiedOn', 'not a date')]:
            changed = copy.deepcopy(image)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                builder.build(source, image_manifest={'schemaVersion': 1, 'images': [changed]})
        for field in ('brand', 'expression', 'size', 'packaging', 'visual', 'professional'):
            for missing in (False, True):
                changed = copy.deepcopy(image)
                if missing:
                    changed['verification'].pop(field)
                else:
                    changed['verification'][field] = False
                with self.subTest(field=field, missing=missing), self.assertRaisesRegex(ValueError, 'professional-quality'):
                    builder.build(source, image_manifest={'schemaVersion': 1, 'images': [changed]})

    def test_direct_reference_photo_cannot_bypass_unknown_size_or_upc(self):
        for source in (export({'ItemName': 'Example Berry'}),
                       export({'ItemName': 'Example Berry 750ml', 'MAINUPC': 'unknown'})):
            image = direct_reference_image_fixture(source)
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'known exact UPC and size'):
                builder.build(source, image_manifest={'schemaVersion': 1, 'images': [image]})
        source = export({'ItemName': 'Example Berry 750ml 1L'})
        image = direct_reference_image_fixture(source)
        with self.assertRaisesRegex(ValueError, 'known exact UPC and size'):
            builder.build(source, image_manifest={'schemaVersion': 1, 'images': [image]})

    def test_direct_reference_photo_leaves_licensed_credit_entries_unchanged(self):
        source = export({'ItemName': 'Example Berry 750ml'}, {'ItemName': 'Other 375ml'})
        direct = direct_reference_image_fixture(source)
        licensed = copy.deepcopy(json.loads((ROOT / 'data/image-manifest.json').read_text())['images'][0])
        licensed.update(sku='2', expectedUpc='001234567890', expectedName='Other', expectedLabel='375 mL')
        before, _ = builder.build(source, image_manifest={'schemaVersion': 1, 'images': [licensed]})
        after, _ = builder.build(source, image_manifest={'schemaVersion': 1, 'images': [licensed, direct]})
        self.assertEqual(builder.render_image_credits(before), builder.render_image_credits(after))
        before_image = next(v['image'] for p in before['products'] for v in p['variants'] if v['sku'] == '2')
        after_image = next(v['image'] for p in after['products'] for v in p['variants'] if v['sku'] == '2')
        self.assertEqual(before_image, after_image)
        for field, value in [('usageBasis', 'user_authorized_direct_reference'), ('attributionRequired', False)]:
            invalid = copy.deepcopy(after)
            photo = next(v['image'] for p in invalid['products'] for v in p['variants'] if v['sku'] == '2')
            photo[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                builder.render_image_credits(invalid)

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

    def test_ambiguous_pack_fraction_reviews_preserve_source_without_guessing_measurements(self):
        fixtures = [('1377', 'BUD Black Crown 15/22 OzBottle', '15 PK CITY&COUN'),
                    ('2016', "CHESTER'S PUFFCORN 41/2 OZ", ''),
                    ('2537', 'CRACKER JACK ORG 41/2OZ', '')]
        for sku, title, pack in fixtures:
            with self.subTest(sku=sku):
                source = export({'SKU': sku, 'ItemName': title, 'PackName': pack})
                raw = list(csv.reader(io.StringIO(source.decode())))[1]
                review = {'schemaVersion': 1, 'reviews': [{
                    'sku': sku, 'expectedUpc': raw[1],
                    'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
                    'expectedSourceRecordSha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
                    'action': 'size_needs_confirmation',
                    'reason': 'Ambiguous source fraction and pack notation require exact package review',
                }]}
                catalog, audit = builder.build(source, source_reviews_manifest=review)
                variant = catalog['products'][0]['variants'][0]
                self.assertEqual(variant['label'], 'Size needs confirmation')
                self.assertEqual((variant['sku'], variant['upc']), (sku, raw[1]))
                self.assertEqual(audit['records'][0]['rawFields'], raw)
                self.assertEqual(audit['records'][0]['source']['ItemName'], title)
                self.assertEqual(audit['records'][0]['source']['PackName'], pack)
                self.assertIsNone(variant['image'])
                with self.assertRaisesRegex(ValueError, 'Image blocked'):
                    builder.build(source, source_reviews_manifest=review,
                                  image_manifest={'schemaVersion': 1, 'images': [{'sku': sku}]})

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
        grouped_review = copy.deepcopy(review)
        grouped_review['reviews'][0]['identityResolution']['groupWithPriorExpression'] = True
        grouped, _ = builder.build(source, source_reviews_manifest=grouped_review)
        self.assertEqual(len(grouped['products']), 1)
        self.assertEqual({v['sku'] for v in grouped['products'][0]['variants']}, {'1', '2'})
        for invalid in (False, 'true'):
            bad = copy.deepcopy(grouped_review)
            bad['reviews'][0]['identityResolution']['groupWithPriorExpression'] = invalid
            with self.assertRaisesRegex(ValueError, 'unchanged prior product expression'):
                builder.build(source, source_reviews_manifest=bad)
        bad = copy.deepcopy(grouped_review)
        bad['reviews'][0]['identityResolution']['verifiedName'] = 'Different Berry'
        with self.assertRaisesRegex(ValueError, 'unchanged prior product expression'):
            builder.build(source, source_reviews_manifest=bad)
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

    def test_separate_credits_cover_every_photo_requiring_attribution(self):
        class CreditsParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.records = {}
                self.current = None
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'li' and 'data-sku' in attrs:
                    self.current = {'links': [], 'text': []}
                    self.records[attrs['data-sku']] = self.current
                if self.current is not None and tag == 'a':
                    self.current['links'].append(attrs.get('href'))
            def handle_data(self, data):
                if self.current is not None:
                    self.current['text'].append(data)
            def handle_endtag(self, tag):
                if tag == 'li':
                    self.current = None
        parser = CreditsParser()
        parser.feed((ROOT / 'image-credits.html').read_text())
        photos = {v['sku']: (p, v) for p in self.catalog['products'] for v in p['variants']
                  if v['image'] and v['image'].get('attributionRequired', True)}
        self.assertEqual(set(parser.records), set(photos))
        for sku, (product, variant) in photos.items():
            record = parser.records[sku]
            image = variant['image']
            self.assertEqual(set(record['links']), {image['src'], image['sourcePage'], image['licenseUrl']})
            text = ''.join(record['text'])
            for expected in (product['name'], variant['label'], image['attribution'], image['license']):
                self.assertIn(expected, text)
        self.assertNotIn('href="image-credits.html"', (ROOT / 'index.html').read_text())

    def test_credit_metadata_is_escaped_and_unsafe_links_rejected(self):
        image = {'src': 'images/products/example.jpg', 'sourcePage': 'https://example.test/product',
                 'licenseUrl': 'https://example.test/license', 'license': 'Example license',
                 'attribution': '<img src=x onerror=alert(1)>'}
        catalog = {'products': [{'name': '</h2><script>alert(1)</script>',
                    'variants': [{'sku': '1', 'label': '12 oz', 'image': image}]}]}
        rendered = builder.render_image_credits(catalog)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('<img src=x', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        for field, value in [('sourcePage', 'javascript:alert(1)'),
                             ('licenseUrl', 'file:///private/record'),
                             ('src', 'images/products/../secret.jpg')]:
            changed = copy.deepcopy(catalog)
            changed['products'][0]['variants'][0]['image'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                builder.render_image_credits(changed)

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
                    image = variant['image']
                    if image.get('usageBasis') == 'user_authorized_direct_reference':
                        self.assertEqual(set(image), {'src', 'alt', 'sourcePage', 'usageBasis', 'attributionRequired'})
                        self.assertIs(image['attributionRequired'], False)
                    else:
                        self.assertEqual(set(image), {'src', 'alt', 'sourcePage', 'license', 'licenseUrl', 'attribution'})

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
        categories_path = ROOT / 'data/category-reviews.json'
        categories = json.loads(categories_path.read_text()) if categories_path.exists() else None
        public_names_path = ROOT / 'data/public-name-reviews.json'
        public_names = json.loads(public_names_path.read_text()) if public_names_path.exists() else None
        catalog, audit = builder.build(source, image_manifest=manifest, name_manifest=names,
                                       source_reviews_manifest=reviews, report_additions_manifest=reports,
                                       category_reviews_manifest=categories,
                                       public_name_reviews_manifest=public_names)
        self.assertEqual(audit['sourceSha256'], builder.BASELINE_HASH)
        self.assertEqual(catalog, self.catalog)
        self.assertEqual(catalog['counts']['originalSourceRows'], 9881)
        self.assertEqual(len(audit['records']), 9881 + catalog['counts']['reportSourceRows'])
        originals = audit['records'][:9881]
        self.assertEqual({r['sourceRow'] for r in originals}, set(range(1, 9882)))
        self.assertEqual([r['rawFields'] for r in originals], list(csv.reader(io.StringIO(source.decode('utf-8-sig'))))[1:])


if __name__ == '__main__':
    unittest.main()
