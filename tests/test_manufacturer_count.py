"""Count-only late fills bind rendered manufacturer facts and exact native bytes."""
import copy
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_catalog import builder, export, public_name_review
from test_missing_measurement import missing_fixture


def count_fixture(source=None, absorbency="Regular"):
    # Synthetic declarations exercise guard behavior; no real product approval.
    source = source or export({'ItemName': 'TAMPAX regular', 'DEPNAME': 'MISC',
                               'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
    source, baseline, kwargs = missing_fixture(source, label='10 count')
    review = kwargs['source_reviews_manifest']['reviews'][0]
    image = kwargs['image_manifest']['images'][0]
    product = next(p for p in baseline['products'] if any(v['sku'] == '1' for v in p['variants']))
    review.update(verificationMethod=builder.PG_CONTENTS_COUNT_METHOD,
                  expectedProductSha256=builder.review_evidence_sha256(product))
    gtin = review['expectedUpc'].zfill(14)
    literal = gtin[3:]
    url = f'https://smartlabel.pg.com/en-us/{gtin}.html'
    image_url = f'https://images.ctfassets.net/p9x6nkt33m4y/Abc123/{"a" * 32}/{gtin}'
    name = (f'Tampax Cardboard Tampons {absorbency} Absorbency, Anti-Slip Grip, '
            'LeakGuard Skirt, Unscented, 10 Count')
    footer = 'DISTR. BY PROCTER & GAMBLE, CINCINNATI, OHIO 45202'
    capture = {'url': url, 'title': name + ' - SmartLabel™',
        'text': '\n'.join(['Tampax', name, '10 CT', literal, footer]), 'links': [],
        'images': [{'currentSrc': image_url, 'src': image_url.removeprefix('https:'),
                    'complete': True, 'naturalWidth': 1000, 'naturalHeight': 1000}]}
    primary = {'kind': 'manufacturer_rendered_contents_count', 'url': url,
        'supports': 'Synthetic rendered single retail box, not a wholesale pack or literal valid body UPC.',
        'publisher': 'Procter & Gamble', 'publisherEvidenceUrl': 'https://smartlabel.org/product-search/',
        'retrievedOn': '2026-11-03', 'renderedCapture': capture,
        'renderedCaptureSha256': builder.review_evidence_sha256(capture),
        'observedName': name, 'displayedCodeLiteral': literal,
        'codeBasis': 'displayed_11_digits_omitting_gtin12_leading_zero',
        'contentsCount': 10, 'countText': '10 CT', 'quantityBasis': 'single_retail_box_contents',
        'retailUnitsPerPackage': 1, 'sourceImageUrl': image_url, 'publisherRoleLiteral': footer}
    receipt = {'scope': 'original_inventory_unit', 'sku': '1', 'upc': review['expectedUpc'],
        'publicName': review['expectedPublicName'], 'expectedLabel': review['expectedLabel'],
        'verifiedLabel': '10 count', 'productId': review['expectedProductId'],
        'productSha256': review['expectedProductSha256'], 'groupSkus': review['expectedResultingGroupSkus'],
        'sourceRow': review['expectedSourceRow'], 'rawFieldsSha256': review['expectedSourceRecordSha256'],
        'rawNameSha256': review['expectedNameSha256'], 'sha256': image['sha256'],
        'originalSha256': image['sha256'], 'sourceImageUrl': image_url,
        'producerProductPageUrl': url, 'renderedCaptureSha256': primary['renderedCaptureSha256'],
        'observedName': name, 'displayedCodeLiteral': literal, 'visibleContentsCount': 10,
        'retailUnitsPerPackage': 1, 'retailContainer': 'box', 'countBasis': 'printed_retail_contents_count',
        'manufacturerPrimaryImageConfirmed': True, 'inspectedOn': '2026-11-03',
        'src': image['src'], 'publicImageSha256': builder.review_evidence_sha256(image),
        'visibleCountText': '10 ' + absorbency.upper(), 'verification': copy.deepcopy(image['verification']),
        'nativeImage': {'format': 'JPEG', 'width': 1000, 'height': 1000, 'strictVerifyAndFreshDecode': True},
        'rootInspectionReceiptSha256': 'b' * 64, 'independentInspectionReceiptSha256': 'c' * 64}
    photo = {'kind': 'inspected_package_photo', 'url': image_url,
        'supports': 'Synthetic inspection; visible contents in one original retail box.',
        'sha256': image['sha256'], 'inspectedOn': '2026-11-03', 'inspectionReceipt': receipt,
        'inspectionReceiptSha256': builder.review_evidence_sha256(receipt)}
    review['evidence'] = [primary, photo]
    kwargs['private_direct_image_evidence_manifest']['entries'][0].update(sourcePage=url, sourceImageUrl=image_url)
    return source, baseline, kwargs


def reseal(kwargs):
    """Keep integrity digests valid while testing semantic contradiction guards."""
    primary, photo = kwargs['source_reviews_manifest']['reviews'][0]['evidence']
    primary['renderedCaptureSha256'] = builder.review_evidence_sha256(primary['renderedCapture'])
    photo['inspectionReceipt']['renderedCaptureSha256'] = primary['renderedCaptureSha256']
    photo['inspectionReceiptSha256'] = builder.review_evidence_sha256(photo['inspectionReceipt'])


def replace_own_expression(kwargs, name, visible):
    """Coherently replace declared source/photo facts, keeping target guards."""
    primary, photo = kwargs['source_reviews_manifest']['reviews'][0]['evidence']
    prior = primary['observedName']
    primary['observedName'] = photo['inspectionReceipt']['observedName'] = name
    primary['renderedCapture']['title'] = name + ' - SmartLabel™'
    primary['renderedCapture']['text'] = primary['renderedCapture']['text'].replace(prior, name)
    photo['inspectionReceipt']['visibleCountText'] = visible
    reseal(kwargs)


class ManufacturerCountTests(unittest.TestCase):
    def test_coherent_opposite_source_and_photo_cannot_override_original_expression(self):
        for current, opposite in [('Regular', 'Super'), ('Super', 'Regular')]:
            source = export({'ItemName': 'TAMPAX ' + current.upper(), 'DEPNAME': 'MISC',
                             'MAINUPC': '012345678905'})
            source, _, kwargs = count_fixture(source, absorbency=current)
            builder.build(source, **kwargs)
            original_guards = {k: copy.deepcopy(v) for k, v in kwargs['source_reviews_manifest']['reviews'][0].items()
                               if k != 'evidence'}
            name = (f'Tampax Cardboard Tampons {opposite} Absorbency, Anti-Slip Grip, '
                    'LeakGuard Skirt, Unscented, 10 Count')
            replace_own_expression(kwargs, name, '10 ' + opposite.upper())
            self.assertEqual({k: v for k, v in kwargs['source_reviews_manifest']['reviews'][0].items()
                              if k != 'evidence'}, original_guards)
            with self.subTest(current=current), self.assertRaisesRegex(ValueError, 'same supported Tampax brand and expression'):
                builder.build(source, **kwargs)

    def test_unsupported_original_or_current_family_expression_stays_held(self):
        for name in ('Other Regular', 'TAMPAX', 'TAMPAX Regular Super', 'TAMPAX SUPER PLUS',
                     'TAMPAX Regularity', 'TAMPAX REG', 'TAMPAX Pearl Regular', 'TAMPAX ſuper'):
            source = export({'ItemName': name, 'DEPNAME': 'MISC', 'MAINUPC': '012345678905'})
            source, _, kwargs = count_fixture(source)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'same supported Tampax brand and expression'):
                builder.build(source, **kwargs)

    def test_coherent_wrong_source_brand_family_or_plus_is_not_the_supported_expression(self):
        source, _, original = count_fixture()
        valid = original['source_reviews_manifest']['reviews'][0]['evidence'][0]['observedName']
        for name in (valid.replace('Tampax', 'Other'), valid.replace('Cardboard', 'Pearl'),
                     valid.replace('Regular', 'Super Plus'), valid.replace('Regular', 'Regular Super'),
                     valid.replace('Regular Absorbency, ', ''), valid.replace('Unscented', 'Scented')):
            kwargs = copy.deepcopy(original)
            replace_own_expression(kwargs, name, '10 SUPER' if 'Super Plus' in name else '10 REGULAR')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'same supported Tampax brand and expression'):
                builder.build(source, **kwargs)
        kwargs = copy.deepcopy(original)
        capture = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]['renderedCapture']
        capture['text'] = capture['text'].replace('Tampax\n', 'Other\n', 1)
        reseal(kwargs)
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_prior_title_override_cannot_hide_contrary_original_absorbency(self):
        for original, current in [('SUPER', 'regular'), ('regular', 'SUPER')]:
            source = export({'ItemName': 'TAMPAX ' + original, 'DEPNAME': 'MISC', 'MAINUPC': '012345678905'})
            source, _, kwargs = count_fixture(source, absorbency=current.title())
            names = public_name_review(source, 'TAMPAX ' + current)
            corrected, _ = builder.build(source, public_name_reviews_manifest=names)
            product = corrected['products'][0]
            review = kwargs['source_reviews_manifest']['reviews'][0]
            image = kwargs['image_manifest']['images'][0]
            receipt = review['evidence'][1]['inspectionReceipt']
            review.update(expectedPublicName=product['name'], expectedProductId=product['id'],
                          expectedProductSha256=builder.review_evidence_sha256(product))
            image['expectedName'] = product['name']
            kwargs['private_direct_image_evidence_manifest']['entries'][0]['expectedName'] = product['name']
            receipt.update(publicName=product['name'], productId=product['id'],
                           productSha256=review['expectedProductSha256'],
                           publicImageSha256=builder.review_evidence_sha256(image))
            reseal(kwargs)
            kwargs['public_name_reviews_manifest'] = names
            with self.subTest(original=original), self.assertRaisesRegex(ValueError, 'original Tampax expression unchanged'):
                builder.build(source, **kwargs)

    def test_count_fill_preserves_catalog_except_exact_target_label_and_photo(self):
        for upc in ('012345678905', '00012345678905'):
            source = export({'ItemName': 'TAMPAX regular', 'DEPNAME': 'MISC', 'MAINUPC': upc},
                            {'ItemName': 'Unrelated Product 4oz', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678912'})
            source, baseline, kwargs = count_fixture(source)
            original = copy.deepcopy(kwargs)
            catalog, audit = builder.build(source, **kwargs)
            expected = copy.deepcopy(baseline)
            actual_variant = next(v for p in catalog['products'] for v in p['variants'] if v['sku'] == '1')
            expected_variant = next(v for p in expected['products'] for v in p['variants'] if v['sku'] == '1')
            expected_variant.update(label='10 count', image=actual_variant['image'])
            with self.subTest(upc=upc):
                self.assertEqual(catalog, expected)
                self.assertEqual(kwargs, original)
                self.assertEqual(actual_variant['upc'], upc)
                self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])
                self.assertEqual(audit['records'][0]['source']['MAINUPC'], upc)
                self.assertEqual(audit['records'][0]['disposition'], 'retained_variant')

    def test_wrong_displayed_route_and_image_codes_are_rejected(self):
        for field, value in [('displayedCodeLiteral', '12345678912'),
                             ('displayedCodeLiteral', '012345678905'),
                             ('displayedCodeLiteral', '01234567890'),
                             ('codeBasis', 'valid_printed_upc'),
                             ('url', 'https://smartlabel.pg.com/en-us/00012345678912.html'),
                             ('sourceImageUrl', 'https://images.ctfassets.net/p9x6nkt33m4y/Abc123/' + 'a' * 32 + '/00012345678912')]:
            source, _, kwargs = count_fixture()
            kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                builder.build(source, **kwargs)
        for upc in ('112345678902', '012345678906'):
            source = export({'ItemName': 'TAMPAX regular', 'DEPNAME': 'MISC', 'MAINUPC': upc})
            source, _, kwargs = count_fixture(source)
            with self.subTest(originalUpc=upc), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_count_is_contents_of_one_box_not_a_case_or_pack(self):
        for field, value in [('contentsCount', 12), ('contentsCount', True), ('contentsCount', '10'),
                             ('countText', '10 PACK'), ('quantityBasis', 'wholesale_case'),
                             ('retailUnitsPerPackage', 10), ('retailUnitsPerPackage', True),
                             ('retailUnitsPerPackage', 1.0)]:
            source, _, kwargs = count_fixture()
            kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): builder.build(source, **kwargs)
        for label in ('10-pack', '10 CT', '0 count', '-1 count', '1.5 count', '10 oz'):
            source, _, kwargs = count_fixture()
            kwargs['source_reviews_manifest']['reviews'][0]['verifiedLabel'] = label
            with self.subTest(label=label), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_existing_source_quantity_or_pack_context_cannot_be_replaced(self):
        for field, value in [('ItemName', 'TAMPAX regular 10ct'), ('ItemName', 'TAMPAX regular 4oz'),
                             ('ItemName', 'TAMPAX regular ten boxes'), ('ItemName', 'TAMPAX regular 10'),
                             ('ItemName', 'TAMPAX regular carton'), ('PackName', ' '),
                             ('PackName', '10'), ('ItemTypeDesc', 'case')]:
            record = {'ItemName': 'TAMPAX regular', 'DEPNAME': 'MISC', 'MAINUPC': '012345678905'}
            record[field] = value
            source, _, kwargs = count_fixture(export(record))
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_source_and_full_current_product_guards_fail_closed(self):
        for key, value in [('expectedNameSha256', '0' * 64), ('expectedSourceRecordSha256', '0' * 64),
                           ('expectedSourceRow', 2), ('expectedPublicName', 'Other Regular'),
                           ('expectedLabel', '10 count'), ('expectedCategory', 'food'),
                           ('expectedDepartment', 'Snacks'), ('expectedProductId', 'p-' + '0' * 16),
                           ('expectedResultingGroupSkus', ['1', '2']), ('expectedProductSha256', '0' * 64)]:
            source, _, kwargs = count_fixture()
            kwargs['source_reviews_manifest']['reviews'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, _, kwargs = count_fixture()
        rows = list(csv.reader(io.StringIO(source.decode())))
        rows[1][6] = '123'
        stream = io.StringIO(); csv.writer(stream).writerows(rows)
        with self.assertRaisesRegex(ValueError, 'Source metadata review identity mismatch'):
            builder.build(stream.getvalue().encode(), **kwargs)
        # Keep receipt consistent so the actual current-product comparison runs.
        review = kwargs['source_reviews_manifest']['reviews'][0]
        review['expectedProductSha256'] = review['evidence'][1]['inspectionReceipt']['productSha256'] = '0' * 64
        reseal(kwargs)
        with self.assertRaisesRegex(ValueError, 'current full product mismatch'): builder.build(source, **kwargs)

    def test_rendered_source_conflicts_fail_even_with_recomputed_capture_hash(self):
        for old, new in [('10 CT', '12 CT'), ('12345678905', '12345678912'),
                         ('Regular Absorbency', 'Super Absorbency')]:
            source, _, kwargs = count_fixture()
            capture = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]['renderedCapture']
            capture['text'] = capture['text'].replace(old, new)
            reseal(kwargs)
            with self.subTest(old=old), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, _, kwargs = count_fixture()
        primary = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]
        primary['renderedCapture']['images'][0]['currentSrc'] += '-related'
        reseal(kwargs)
        with self.assertRaises(ValueError): builder.build(source, **kwargs)
        for conflicting_line in ('20 CT', '10 BOXES', '12345678912'):
            source, _, kwargs = count_fixture()
            capture = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]['renderedCapture']
            capture['text'] += '\n' + conflicting_line
            reseal(kwargs)
            with self.subTest(conflicting_line=conflicting_line), self.assertRaises(ValueError):
                builder.build(source, **kwargs)

    def test_unowned_routes_and_unemitted_image_hosts_are_rejected(self):
        source, _, original = count_fixture()
        primary = original['source_reviews_manifest']['reviews'][0]['evidence'][0]
        for field, value in [('publisher', 'Other'), ('url', primary['url'].replace('https:', 'http:')),
                             ('url', primary['url'].replace('pg.com/', 'pg.com.evil.test/')),
                             ('url', primary['url'] + '?x=1'), ('url', primary['url'] + '#x'),
                             ('publisherEvidenceUrl', 'https://smartlabel.org/brands/'),
                             ('publisherEvidenceUrl', 'https://smartlabel.org/product-search/?product=Tampax'),
                             ('sourceImageUrl', primary['sourceImageUrl'].replace('p9x6nkt33m4y', 'otherTenant'))]:
            kwargs = copy.deepcopy(original)
            kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_inspection_requires_visible_contents_native_decode_and_independent_qa(self):
        for field, value in [('visibleContentsCount', 12), ('visibleContentsCount', True),
                             ('visibleCountText', '10 BOXES'), ('visibleCountText', '10 SUPER'),
                             ('retailContainer', 'case'), ('retailUnitsPerPackage', 10),
                             ('manufacturerPrimaryImageConfirmed', 1), ('originalSha256', '0' * 64),
                             ('independentInspectionReceiptSha256', 'b' * 64),
                             ('nativeImage', {'format': 'JPEG', 'width': 900, 'height': 1000, 'strictVerifyAndFreshDecode': True})]:
            source, _, kwargs = count_fixture()
            receipt = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][1]['inspectionReceipt']
            receipt[field] = value; reseal(kwargs)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_native_assignment_and_private_source_remain_exact(self):
        for field, value in [('sourcePage', 'https://smartlabel.pg.com/en-us/00012345678912.html'),
                             ('sourceImageUrl', 'https://example.test/unrelated.jpg')]:
            source, _, kwargs = count_fixture()
            kwargs['private_direct_image_evidence_manifest']['entries'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, _, kwargs = count_fixture()
        review = kwargs['source_reviews_manifest']['reviews'][0]
        photo = review['evidence'][1]; receipt = photo['inspectionReceipt']
        image = kwargs['image_manifest']['images'][0]
        image['sha256'] = review['expectedImageSha256'] = photo['sha256'] = receipt['sha256'] = receipt['originalSha256'] = '0' * 64
        kwargs['private_direct_image_evidence_manifest']['entries'][0]['sha256'] = '0' * 64
        receipt['publicImageSha256'] = builder.review_evidence_sha256(image); reseal(kwargs)
        with self.assertRaisesRegex(ValueError, 'hash does not match'): builder.build(source, **kwargs)

    def test_new_method_does_not_relax_ordinary_measurement_or_schema(self):
        source, _, kwargs = missing_fixture(label='10 count')
        with self.assertRaisesRegex(ValueError, 'canonical retail measurement'): builder.build(source, **kwargs)
        for mutate in [lambda r: r['evidence'][0].update(observedUpc='012345678905'),
                       lambda r: r['evidence'][0]['renderedCapture'].update(hiddenState={}),
                       lambda r: r['evidence'][1]['inspectionReceipt'].update(visibleUpc='012345678905'),
                       lambda r: r['evidence'][0].update(retrievedOn='2026-02-30'),
                       lambda r: r.pop('expectedProductSha256')]:
            source, _, kwargs = count_fixture()
            mutate(kwargs['source_reviews_manifest']['reviews'][0])
            with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_count_evidence_roundtrips_through_existing_private_projection(self):
        source, _, kwargs = count_fixture()
        expected, _ = builder.build(source, **kwargs)
        review = kwargs['source_reviews_manifest']['reviews'][0]
        evidence = review['evidence']; digest = builder.review_evidence_sha256(evidence)
        review['evidence'] = {'privateEvidenceSha256': digest}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            ledger = {'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND, 'records': {digest: evidence}}
            path.write_text(json.dumps(ledger))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                actual, _ = builder.build(source, **kwargs)
                self.assertEqual(actual, expected)
                ledger['records'][digest][0]['contentsCount'] = 12
                path.write_text(json.dumps(ledger))
                with self.assertRaisesRegex(ValueError, 'does not match its hash'): builder.build(source, **kwargs)
        self.assertNotIn('https:', json.dumps(kwargs['source_reviews_manifest']))
        self.assertNotIn('displayedCodeLiteral', json.dumps(actual))
