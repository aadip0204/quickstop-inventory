"""Government branded quantities keep their actual publisher and field basis."""
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

from test_catalog import builder, export, manufacturer_correction, public_name_review
from test_missing_measurement import missing_fixture


def bind_capture(item, body, content_type):
    item['capture'] = {'status': 200, 'complete': True, 'contentType': content_type, 'responseBody': body}
    item['sourceSha256'] = hashlib.sha256(body.encode()).hexdigest()


def sign_receipt(kwargs):
    review = kwargs['source_reviews_manifest']['reviews'][0]
    primary, secondary, photo = review['evidence']
    photo['inspectionReceipt']['authoritySourceSha256'] = primary['sourceSha256']
    photo['inspectionReceipt']['secondarySourceSha256'] = secondary['sourceSha256']
    photo['inspectionReceiptSha256'] = builder.review_evidence_sha256(photo['inspectionReceipt'])


def change_record(kwargs, **fields):
    primary = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]
    record = json.loads(primary['capture']['responseBody'])
    record.update(fields)
    bind_capture(primary, json.dumps(record), 'application/json')
    sign_receipt(kwargs)


def usda_fixture(source=None, names=None, titles=None):
    source, baseline, kwargs = missing_fixture(source, label='4 fl oz', names=names)
    review = kwargs['source_reviews_manifest']['reviews'][0]
    if titles:
        kwargs['public_name_reviews_manifest'] = titles
        baseline, _ = builder.build(source, name_manifest=names, public_name_reviews_manifest=titles)
        product = next(p for p in baseline['products'] if any(v['sku'] == '1' for v in p['variants']))
        review.update(expectedPublicName=product['name'], expectedProductId=product['id'])
        kwargs['image_manifest']['images'][0].update(expectedName=product['name'], alt=product['name'] + ' packaging')
        kwargs['private_direct_image_evidence_manifest']['entries'][0].update(
            {field: kwargs['image_manifest']['images'][0][field] for field in builder.DIRECT_REFERENCE_PUBLIC_FIELDS})
    review['verificationMethod'] = 'usda_branded_food_package_weight'
    image = kwargs['image_manifest']['images'][0]
    photo_url = kwargs['private_direct_image_evidence_manifest']['entries'][0]['sourceImageUrl']
    primary = {'kind': 'usda_branded_food_record', 'url': 'https://api.nal.usda.gov/fdc/v1/food/12345?api_key=DEMO_KEY',
               'supports': 'Synthetic USDA-published historical branded data; no current-stock claim.',
               'publisher': 'USDA FoodData Central', 'quantitySourceField': 'packageWeight',
               'quantityBasis': 'branded_record_package_net_content', 'retailUnitLabel': '4 fl oz',
               'retrievedOn': '2026-11-01'}
    record = {'fdcId': 12345, 'dataType': 'Branded', 'foodClass': 'Branded', 'gtinUpc': review['expectedUpc'],
              'description': 'ICE CREAM CONE', 'brandName': 'EXAMPLE', 'brandOwner': 'Example Submitter',
              'packageWeight': '4.0 fl oz/118 mL', 'dataSource': 'LI', 'marketCountry': 'United States',
              'publicationDate': '10/28/2021', 'modifiedDate': '7/28/2020', 'availableDate': '7/28/2020',
              'discontinuedDate': '', 'servingSize': 81.0, 'servingSizeUnit': 'g', 'householdServingFullText': '81 GRM'}
    bind_capture(primary, json.dumps(record), 'application/json')
    secondary = {'kind': 'secondary_exact_upc_package_listing',
                 'url': 'https://go-upc.com/search?q=' + review['expectedUpc'],
                 'supports': 'Synthetic secondary barcode aggregator package-size and original image association.',
                 'publisher': 'Go-UPC', 'sourceRole': 'secondary_barcode_aggregator',
                 'observedUpc': review['expectedUpc'], 'quantitySourceField': 'Package Size, FL OZ',
                 'packageSize': '4.0', 'packageSizeUnit': 'FL OZ', 'sourceImageUrl': photo_url,
                 'retrievedOn': '2026-11-01'}
    html = ('<main class="wrapper"><div id="resultPageContainer"><div class="two-column-container">'
            '<div class="left-column"><h1 class="product-name">Synthetic Ice Cream Cone</h1>'
            '<table class="table table-striped"><tr><td class="metadata-label">UPC</td><td>' + review['expectedUpc'] + '</td></tr></table>'
            '<li><span class="metadata-label">Package Size, FL OZ:</span> 4.0</li>'
            '<li><span class="metadata-label">Weight, LB:</span> 0.2</li>'
            '<figure class="product-image mobile"><img src="' + photo_url + '" alt="Synthetic fixture"/></figure>'
            '</div><div class="right-column"></div></div></div></main>')
    bind_capture(secondary, html, 'text/html')
    photo = {'kind': 'inspected_package_photo', 'url': photo_url,
             'supports': 'Synthetic manual packaging receipt for existing test bytes; net content is unreadable.',
             'sha256': image['sha256'], 'inspectedOn': '2026-11-01'}
    photo['inspectionReceipt'] = {
        'scope': 'original_inventory_unit', 'sku': '1', 'upc': review['expectedUpc'],
        'publicName': review['expectedPublicName'], 'expectedLabel': 'Size not specified', 'verifiedLabel': '4 fl oz',
        'productId': review['expectedProductId'], 'groupSkus': review['expectedResultingGroupSkus'],
        'sourceRow': 1, 'rawFieldsSha256': review['expectedSourceRecordSha256'],
        'rawNameSha256': review['expectedNameSha256'], 'src': image['src'], 'sha256': image['sha256'],
        'originalSha256': image['sha256'], 'sourceImageUrl': photo_url,
        'publicImageSha256': builder.review_evidence_sha256(image), 'visibleContainerCount': 1,
        'countBasis': 'manual_visible_packaging', 'netContentBasis': 'usda_packageWeight_and_independent_secondary',
        'visibleNetContent': None, 'verification': image['verification'], 'inspectedOn': '2026-11-01'}
    review['evidence'] = [primary, secondary, photo]
    sign_receipt(kwargs)
    return source, baseline, kwargs


class UsdaMeasurementTests(unittest.TestCase):
    def test_only_missing_quantity_and_matching_photo_change(self):
        source = export({'ItemName': 'Example Ice Cookie', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905',
                         'ItemPrice': '', 'TotalQty_Multi': ''},
                        {'ItemName': 'Example Ice Cookie 8oz', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678912',
                         'ItemPrice': '', 'TotalQty_Multi': ''})
        source, baseline, kwargs = usda_fixture(source)
        original = copy.deepcopy(kwargs)
        catalog, audit = builder.build(source, **kwargs)
        actual = next(v for p in catalog['products'] for v in p['variants'] if v['sku'] == '1')
        expected = copy.deepcopy(baseline)
        next(v for p in expected['products'] for v in p['variants'] if v['sku'] == '1').update(label='4 fl oz', image=actual['image'])
        self.assertEqual(catalog, expected)
        self.assertEqual(kwargs, original)
        self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])
        self.assertEqual(audit['records'][0]['source']['MAINUPC'], '012345678905')
        record = json.loads(audit['records'][0]['sourceReview']['evidence'][0]['capture']['responseBody'])
        self.assertEqual((record['dataSource'], record['publicationDate'], record['servingSize']), ('LI', '10/28/2021', 81.0))
        self.assertNotIn('retailUnitsPerPackage', json.dumps(kwargs['source_reviews_manifest']))
        self.assertEqual(audit['records'][0]['lateMeasurementResolution']['previousPublicIdentity']['label'], 'Size not specified')

    def test_final_public_title_is_guarded_before_late_quantity(self):
        source = export({'ItemName': 'Example coneice cream', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905',
                         'ItemPrice': '', 'TotalQty_Multi': ''})
        titles = public_name_review(source, 'Example Ice Cream Cone')
        source, baseline, kwargs = usda_fixture(source, titles=titles)
        catalog, audit = builder.build(source, **kwargs)
        self.assertEqual(catalog['products'][0]['name'], 'Example Ice Cream Cone')
        self.assertEqual(catalog['products'][0]['id'], baseline['products'][0]['id'])
        self.assertEqual(audit['records'][0]['publicNameCorrection']['expectedLabel'], 'Size not specified')
        changed = copy.deepcopy(kwargs)
        changed['public_name_reviews_manifest']['reviews'][0]['expectedLabel'] = '4 fl oz'
        with self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        changed['source_reviews_manifest']['reviews'][0]['expectedPublicName'] = 'Example coneice cream'
        with self.assertRaises(ValueError): builder.build(source, **changed)

    def test_original_name_correction_and_name_verification_keep_unknown_size(self):
        source = export({'ItemName': 'Example Ice Cook', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905',
                         'ItemPrice': '', 'TotalQty_Multi': ''})
        correction = manufacturer_correction(source, 'Example Ice Cookie', 'Example Ice Cookie', 'Size not specified')
        verification = {key: correction[key] for key in ('sku', 'expectedUpc', 'expectedNameSha256',
                        'expectedLabel', 'verifiedOn', 'evidence', 'manufacturerRecord')}
        verification.update(expectedPublicName='Example Ice Cookie', verificationMethod='manufacturer_exact_upc_record',
                            verification={'brand': True, 'expression': True, 'upc': True, 'visual': False})
        names = {'schemaVersion': 1, 'corrections': [correction], 'nameVerifications': [verification]}
        source, _, kwargs = usda_fixture(source, names=names)
        catalog, audit = builder.build(source, **kwargs)
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '4 fl oz')
        self.assertEqual(audit['records'][0]['nameVerification']['expectedLabel'], 'Size not specified')
        for collection in ('corrections', 'nameVerifications'):
            changed = copy.deepcopy(kwargs)
            changed['name_manifest'][collection][0]['expectedLabel'] = '4 fl oz'
            with self.subTest(collection=collection), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_record_codes_types_dates_and_urls_fail_closed(self):
        source, _, kwargs = usda_fixture()
        bad_fields = [('gtinUpc', ''), ('gtinUpc', None), ('gtinUpc', 12345678905),
                      ('gtinUpc', '00012345678905'), ('gtinUpc', '012345678912'), ('gtinUpc', '012345678906'),
                      ('fdcId', True), ('fdcId', '12345'), ('fdcId', 12346), ('dataType', 'Foundation'),
                      ('foodClass', 'Survey'), ('dataSource', None), ('dataSource', 'manufacturer'),
                      ('publicationDate', '2/30/2021'), ('modifiedDate', None), ('availableDate', '2021-10-01'),
                      ('discontinuedDate', '7/28/2020')]
        for key, value in bad_fields:
            changed = copy.deepcopy(kwargs); change_record(changed, **{key: value})
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): builder.build(source, **changed)
        for url in ('https://api.nal.usda.gov.evil.test/fdc/v1/food/12345',
                    'https://api.nal.usda.gov./fdc/v1/food/12345',
                    'https://api.nal.usda.gov/fdc/v1/food/54321',
                    'https://api.nal.usda.gov/fdc/v1/food/12345;unreviewed',
                    'https://api.nal.usda.gov/fdc/v1/food/12345?api_key=PRIVATE_KEY',
                    'https://fdc.nal.usda.gov/fdc/v1/food/12345',
                    'https://user@api.nal.usda.gov/fdc/v1/food/12345',
                    'http://api.nal.usda.gov/fdc/v1/food/12345'):
            changed = copy.deepcopy(kwargs); changed['source_reviews_manifest']['reviews'][0]['evidence'][0]['url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_package_weight_only_and_consistent_explicit_fluid_units(self):
        source, _, kwargs = usda_fixture()
        for value in (None, '', 4, '81 g', '4 oz/118 mL', '4.0 fl oz/119 mL', '4.0 fl oz/81 g',
                      '24 x 4.0 fl oz/118 mL', '4.0 fl oz/118 mL per serving', '-4.0 fl oz/118 mL'):
            changed = copy.deepcopy(kwargs); change_record(changed, packageWeight=value)
            with self.subTest(value=value), self.assertRaises(ValueError): builder.build(source, **changed)
        for field in ('servingSize', 'householdServingFullText', 'Weight, LB'):
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][0]['quantitySourceField'] = field
            with self.subTest(field=field), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        # Nutrition fields can differ without becoming net quantity.
        change_record(changed, servingSize=4, servingSizeUnit='fl oz', householdServingFullText='4 fl oz')
        actual, _ = builder.build(source, **changed)
        self.assertEqual(actual['products'][0]['variants'][0]['label'], '4 fl oz')

    def test_capture_bytes_complete_status_and_exact_schemas_are_required(self):
        source, _, kwargs = usda_fixture()
        for index in (0, 1):
            for key, value in [('status', True), ('status', 404), ('complete', 1), ('complete', False),
                               ('contentType', 'text/plain'), ('responseBody', ''), ('responseBody', '{}')]:
                changed = copy.deepcopy(kwargs)
                changed['source_reviews_manifest']['reviews'][0]['evidence'][index]['capture'][key] = value
                with self.subTest(index=index, key=key, value=value), self.assertRaises(ValueError): builder.build(source, **changed)
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][index]['retailUnitsPerPackage'] = 1
            with self.subTest(index=index), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        primary = changed['source_reviews_manifest']['reviews'][0]['evidence'][0]
        body = primary['capture']['responseBody']
        bind_capture(primary, body[:-1] + ', "gtinUpc": "012345678905"}', 'application/json')
        sign_receipt(changed)
        with self.assertRaisesRegex(ValueError, 'unambiguous captured JSON'): builder.build(source, **changed)

    def test_secondary_is_an_actual_independent_quantity_and_image_association(self):
        source, _, kwargs = usda_fixture()
        for key, value in [('sourceRole', 'manufacturer'), ('publisher', 'USDA FoodData Central'),
                           ('observedUpc', '00012345678905'), ('quantitySourceField', 'Weight, LB'),
                           ('packageSize', 4.0), ('packageSize', '24'), ('packageSizeUnit', 'OZ'),
                           ('sourceImageUrl', 'https://example.test/different.jpg'),
                           ('url', 'https://api.nal.usda.gov/fdc/v1/food/12345')]:
            changed = copy.deepcopy(kwargs); changed['source_reviews_manifest']['reviews'][0]['evidence'][1][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): builder.build(source, **changed)
        for old, new in [('>UPC<', '>EAN<'), ('Package Size, FL OZ:', 'Serving Size, FL OZ:'),
                         ('</span> 4.0', '</span> 24.0'), ('product-image mobile', 'unrelated-logo')]:
            changed = copy.deepcopy(kwargs)
            secondary = changed['source_reviews_manifest']['reviews'][0]['evidence'][1]
            bind_capture(secondary, secondary['capture']['responseBody'].replace(old, new), 'text/html')
            sign_receipt(changed)
            with self.subTest(old=old), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_secondary_fields_must_be_live_and_in_the_same_product_container(self):
        source, _, kwargs = usda_fixture()
        body = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][1]['capture']['responseBody']
        package = '<li><span class="metadata-label">Package Size, FL OZ:</span> 4.0</li>'
        row = '<tr><td class="metadata-label">UPC</td><td>012345678905</td></tr>'
        figure = '<figure class="product-image mobile"><img src="https://example.test/image/1.jpg" alt="Synthetic fixture"/></figure>'
        mutations = [
            '<html><body>No product data</body><!--' + body + '--></html>',
            *('<' + tag + '>' + body + '</' + tag + '>' for tag in ('script', 'style', 'template', 'noscript')),
            '<div hidden>' + body + '</div>',
            '<div style="display: none">' + body + '</div>',
            body.replace('id="resultPageContainer"', 'id="unrelatedPage"'),
            body + body,
            *(body.replace(fragment, '') + fragment for fragment in (package, row, figure)),
            body.replace(package, '').replace('<div class="right-column">', '<div class="right-column">' + package),
            body.replace(figure, '').replace('<div class="right-column">', '<div class="right-column">' + figure),
            body.replace('table table-striped', 'unrelated-table'),
            body.replace('</span> 4.0</li>', '</span> 4.0<span>24.0</span></li>'),
            body.replace('<div class="right-column">', '<div class="right-column"><h1 class="product-name">Other item</h1>'),
        ]
        for index, changed_body in enumerate(mutations):
            changed = copy.deepcopy(kwargs)
            bind_capture(changed['source_reviews_manifest']['reviews'][0]['evidence'][1], changed_body, 'text/html')
            sign_receipt(changed)
            with self.subTest(index=index), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_usda_action_review_date_must_be_a_real_calendar_date(self):
        source, _, kwargs = usda_fixture()
        for value in ('2026-99-99', '2026-02-30', '2025-02-29'):
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['reviewedOn'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'valid retained historical and review dates'):
                builder.build(source, **changed)

    def test_manual_receipt_cannot_invent_printed_quantity_or_metadata_count(self):
        source, _, kwargs = usda_fixture()
        for key, value in [('scope', 'current_stock'), ('visibleContainerCount', True), ('visibleContainerCount', 24),
                           ('countBasis', 'UnitsInPackage'), ('visibleNetContent', '4 fl oz'),
                           ('netContentBasis', 'servingSize'), ('productId', 'p-' + '0' * 16),
                           ('groupSkus', ['1', '2']), ('sourceRow', True), ('rawFieldsSha256', '0' * 64),
                           ('rawNameSha256', '0' * 64), ('originalSha256', '0' * 64),
                           ('publicImageSha256', '0' * 64), ('publicName', 'Different Cookie')]:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][2]['inspectionReceipt'][key] = value
            sign_receipt(changed)
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_public_image_complete_binding_private_provenance_and_bytes(self):
        source, _, kwargs = usda_fixture()
        for target, key, value in [('image', 'alt', 'Changed after manual inspection'),
                                   ('image', 'expectedName', 'Different Cookie'),
                                   ('image', 'expectedLabel', '4 oz'), ('image', 'sha256', '0' * 64),
                                   ('private', 'sourceImageUrl', 'https://example.test/other.jpg')]:
            changed = copy.deepcopy(kwargs)
            entry = (changed['image_manifest']['images'][0] if target == 'image'
                     else changed['private_direct_image_evidence_manifest']['entries'][0])
            entry[key] = value
            with self.subTest(target=target, key=key), self.assertRaises(ValueError): builder.build(source, **changed)
        for key in ('image_manifest', 'private_direct_image_evidence_manifest'):
            changed = copy.deepcopy(kwargs); changed[key] = None
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        image = changed['image_manifest']['images'][0]; image['sha256'] = '0' * 64
        changed['private_direct_image_evidence_manifest']['entries'][0]['sha256'] = '0' * 64
        review = changed['source_reviews_manifest']['reviews'][0]; review['expectedImageSha256'] = '0' * 64
        photo = review['evidence'][2]; photo['sha256'] = '0' * 64
        photo['inspectionReceipt'].update(sha256='0' * 64, originalSha256='0' * 64,
                                         publicImageSha256=builder.review_evidence_sha256(image))
        sign_receipt(changed)
        with self.assertRaisesRegex(ValueError, 'Product image is empty or hash does not match'): builder.build(source, **changed)

    def test_original_size_pack_metadata_and_identity_conflicts_cannot_be_reinterpreted(self):
        for suffix in (' 4oz', ' 2 3/4 oz', ' case', ' 24/CASE', ' 6-count', ' 2DZ', ' pack', ' 2x12'):
            source = export({'ItemName': 'Example Ice Cookie' + suffix, 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905',
                             'ItemPrice': '', 'TotalQty_Multi': ''})
            source, _, kwargs = usda_fixture(source)
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, 'genuinely absent'): builder.build(source, **kwargs)
        source, _, kwargs = usda_fixture()
        for key, value in [('expectedSourceRow', 2), ('expectedNameSha256', '0' * 64),
                           ('expectedSourceRecordSha256', '0' * 64), ('expectedProductId', 'p-' + '0' * 16),
                           ('expectedCategory', 'drinks'), ('expectedResultingGroupSkus', ['1', '2']),
                           ('expectedLabel', '4 oz')]:
            changed = copy.deepcopy(kwargs); changed['source_reviews_manifest']['reviews'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_late_application_rejects_report_origin_conflicts_and_stale_group(self):
        source, _, kwargs = usda_fixture()
        reviews = builder.validated_source_reviews(kwargs['source_reviews_manifest'])
        for mode in ('report', 'identity_conflict', 'different_group'):
            catalog, audit = builder.build(source)
            records = {record['sku']: record for record in audit['records']}
            identities = {}
            if mode == 'report':
                records['1']['sourceOrigin'] = 'report'
            elif mode == 'identity_conflict':
                identities['1'] = {'action': 'retain_flagged'}
            else:
                neighbor = copy.deepcopy(catalog['products'][0]['variants'][0]); neighbor['sku'] = '2'
                catalog['products'][0]['variants'].append(neighbor)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                builder.apply_missing_measurements(
                    catalog['products'], records, reviews, kwargs['image_manifest'],
                    kwargs['private_direct_image_evidence_manifest'], {'1'}, identities)

    def test_private_projection_and_legacy_route_remain_separate(self):
        source, _, kwargs = usda_fixture()
        expected, _ = builder.build(source, **kwargs)
        entry = kwargs['source_reviews_manifest']['reviews'][0]
        evidence = entry['evidence']; digest = builder.review_evidence_sha256(evidence)
        entry['evidence'] = {'privateEvidenceSha256': digest}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review-evidence.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND,
                                        'records': {digest: evidence}}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                actual, _ = builder.build(source, **kwargs)
        self.assertEqual(actual, expected)
        self.assertNotIn('https:', json.dumps(kwargs['source_reviews_manifest']))
        for private in ('responseBody', 'FoodData Central', 'Go-UPC', 'visibleContainerCount', 'sourceImageUrl'):
            self.assertNotIn(private, json.dumps(actual))
        source, _, legacy = missing_fixture()
        catalog, _ = builder.build(source, **legacy)
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '4.5 fl oz')
        legacy['source_reviews_manifest']['reviews'][0]['verificationMethod'] = 'usda_branded_food_package_weight'
        with self.assertRaises(ValueError): builder.build(source, **legacy)
        source, _, kwargs = usda_fixture()
        kwargs['source_reviews_manifest']['reviews'][0].pop('verificationMethod')
        with self.assertRaises(ValueError): builder.build(source, **kwargs)
