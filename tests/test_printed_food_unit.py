"""Synthetic printed-code food proofs; no real product/capture/price approval."""
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from test_catalog import builder, export, public_name_review, category_review
from test_missing_measurement import missing_fixture


def document(data):
    return ('<!DOCTYPE html><html><head><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(data) + '</script></head><body><div id="__next">Synthetic test page</div></body></html>')


def sign(kwargs):
    review = kwargs['source_reviews_manifest']['reviews'][0]
    source, photo = review['evidence']
    photo['inspectionReceipt']['distributorSourceSha256'] = source['sourceSha256']
    photo['inspectionReceiptSha256'] = builder.review_evidence_sha256(photo['inspectionReceipt'])


def bind_body(kwargs, body):
    source = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]
    source['capture']['responseBody'] = body
    source['sourceSha256'] = hashlib.sha256(body.encode()).hexdigest()
    sign(kwargs)


def own_product(kwargs):
    body = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]['capture']['responseBody']
    return json.loads(re.search(r'<script[^>]*>(.*?)</script>', body).group(1))['props']['pageProps']['product']


def change_product(kwargs, mutation):
    product = own_product(kwargs)
    mutation(product)
    bind_body(kwargs, document({'props': {'pageProps': {'product': product}}}))


def printed_fixture(source=None, titles=None):
    source = source or export({'ItemName': 'Example Canned Food', 'DEPNAME': 'Snacks',
                               'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
    source, baseline, kwargs = missing_fixture(source, label='4.25 oz')
    review = kwargs['source_reviews_manifest']['reviews'][0]
    image = kwargs['image_manifest']['images'][0]
    private = kwargs['private_direct_image_evidence_manifest']['entries'][0]
    if titles:
        baseline, _ = builder.build(source, public_name_reviews_manifest=titles)
        product = next(p for p in baseline['products'] if any(v['sku'] == '1' for v in p['variants']))
        review.update(expectedPublicName=product['name'], expectedProductId=product['id'])
        image.update(expectedName=product['name'], alt=product['name'] + ' packaging')
        private.update({key: image[key] for key in builder.DIRECT_REFERENCE_PUBLIC_FIELDS})
        kwargs['public_name_reviews_manifest'] = titles
    review.update(verificationMethod=builder.PRINTED_FOOD_UNIT_METHOD, reviewedOn='2024-02-29')
    # Every identity, quantity, source URL, product object and price is synthetic.
    # Reused local image bytes test integrity only, not actual physical reading.
    product = {'id': 'gid://shopify/Product/12345', 'name': 'Example Canned Food',
               'path': '/synthetic-food-can', 'slug': 'synthetic-food-can',
               'itemSize': 4.25, 'unitOfMeasure': 'oz',
               'tags': ['UPC-Each: ' + review['expectedUpc'], 'UPC-Each: 012345678912',
                        'UPC-Case: 10012345678902'],
               'images': [{'url': private['sourceImageUrl']}],
               'variants': [{'name': 'Individual', 'barcode': None,
                             'options': [{'displayName': 'title', 'values': [{'label': 'Individual'}]}]},
                            {'name': 'Pack', 'barcode': '012345678905 012345678912 10012345678902'}],
               'packSize': {'value': '18', 'type': 'number_integer'}, 'productsInPackage': '18',
               'price': {'value': 17.13, 'currencyCode': 'USD'}}
    body = document({'props': {'pageProps': {'product': product}},
                     'recommendations': [{'tags': ['UPC-Each: 012345678905']}]})
    url = 'https://shop.mercaso.com/products/synthetic-food-can'
    listing = {'kind': 'distributor_exact_food_unit_record', 'url': url,
               'supports': 'Synthetic distributor own-product context; competing codes and case context remain explicit.',
               'publisher': 'Mercaso', 'sourceRole': 'direct_seller_wholesaler',
               'sourceSha256': hashlib.sha256(body.encode()).hexdigest(), 'retrievedOn': '2024-02-29',
               'capture': {'status': 200, 'complete': True, 'contentType': 'text/html; charset=utf-8',
                           'responseUrl': url, 'responseBody': body}}
    receipt = {'scope': 'original_inventory_unit', 'sku': '1', 'upc': review['expectedUpc'],
        'publicName': review['expectedPublicName'], 'expectedLabel': 'Size not specified', 'verifiedLabel': '4.25 oz',
        'productId': review['expectedProductId'], 'groupSkus': review['expectedResultingGroupSkus'], 'sourceRow': 1,
        'rawFieldsSha256': review['expectedSourceRecordSha256'], 'rawNameSha256': review['expectedNameSha256'],
        'src': image['src'], 'sha256': image['sha256'], 'originalSha256': image['sha256'],
        'sourceImageUrl': private['sourceImageUrl'], 'publicImageSha256': builder.review_evidence_sha256(image),
        'visibleUpc': review['expectedUpc'], 'visibleNetContent': '4.25 oz', 'visibleContainerCount': 1,
        'countBasis': 'manual_visible_packaging', 'codeBasis': 'printed_package_barcode',
        'netContentBasis': 'printed_package_net_content', 'distributorSourceSha256': listing['sourceSha256'],
        'verification': copy.deepcopy(image['verification']), 'inspectedOn': '2024-02-29',
        'rootInspectionReceiptSha256': 'd' * 64, 'independentInspectionReceiptSha256': 'e' * 64}
    photo = {'kind': 'inspected_package_photo', 'url': private['sourceImageUrl'],
             'supports': 'Synthetic manual printed-code and net-content receipt; not a real image approval.',
             'sha256': image['sha256'], 'inspectedOn': '2024-02-29', 'inspectionReceipt': receipt}
    review['evidence'] = [listing, photo]
    sign(kwargs)
    return source, baseline, kwargs


class PrintedFoodUnitTests(unittest.TestCase):
    def test_printed_food_fills_only_missing_unit_and_image_preserving_all_raw_and_neighbors(self):
        source = export({'ItemName': 'Example Canned Food', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905',
                         'ItemPrice': '', 'TotalQty_Multi': ''},
                        {'ItemName': 'Neighbor Canned Food 8oz', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678912',
                         'ItemPrice': '', 'TotalQty_Multi': ''})
        source, baseline, kwargs = printed_fixture(source)
        original = copy.deepcopy(kwargs)
        catalog, audit = builder.build(source, **kwargs)
        expected = copy.deepcopy(baseline)
        actual = next(v for p in catalog['products'] for v in p['variants'] if v['sku'] == '1')
        next(v for p in expected['products'] for v in p['variants'] if v['sku'] == '1').update(label='4.25 oz', image=actual['image'])
        self.assertEqual(catalog, expected)
        self.assertEqual(kwargs, original)
        self.assertEqual([r['rawFields'] for r in audit['records']], list(csv.reader(io.StringIO(source.decode())))[1:])
        self.assertEqual(own_product(kwargs)['productsInPackage'], '18')
        self.assertIn('UPC-Each: 012345678912', own_product(kwargs)['tags'])
        self.assertEqual(audit['records'][0]['lateMeasurementResolution']['previousPublicIdentity']['label'], 'Size not specified')

    def test_live_single_next_data_and_exact_own_product_scope_are_required(self):
        source, _, kwargs = printed_fixture()
        body = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]['capture']['responseBody']
        script = re.search(r'<script.*?</script>', body).group(0)
        bad_bodies = [
            '<html><body><!--' + script + '--></body></html>',
            '<html><body><template>' + script + '</template></body></html>',
            '<html><body><noscript>' + script + '</noscript></body></html>',
            body.replace('</head>', script + '</head>'),
            body.replace('id="__NEXT_DATA__"', 'id="recommendation-data"'),
            body.replace('type="application/json"', 'type="text/plain"'),
            body.replace('id="__NEXT_DATA__"', 'id="__NEXT_DATA__" id="__NEXT_DATA__"'),
            script,
            document({'props': {'pageProps': {'recommendations': [own_product(kwargs)]}}}),
            document({'props': {'product': own_product(kwargs)}}),
            body.replace('"itemSize": 4.25', '"itemSize": 4.25, "itemSize": 4.25'),
            body.replace('"itemSize": 4.25', '"itemSize": NaN'),
        ]
        for index, value in enumerate(bad_bodies):
            changed = copy.deepcopy(kwargs); bind_body(changed, value)
            with self.subTest(index=index), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_capture_and_publisher_roles_cannot_be_invented(self):
        source, _, kwargs = printed_fixture()
        for key, value in [('status', True), ('status', 404), ('complete', 1), ('complete', False),
                           ('contentType', 'application/json'), ('responseBody', ''), ('responseBody', '{}'),
                           ('responseUrl', 'https://shop.mercaso.com/products/different')]:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][0]['capture'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)
        for key, value in [('publisher', 'Manufacturer'), ('sourceRole', 'manufacturer'), ('kind', 'manufacturer'),
                           ('retailUnitsPerPackage', 1), ('sourceSha256', '0' * 64)]:
            changed = copy.deepcopy(kwargs)
            changed['source_reviews_manifest']['reviews'][0]['evidence'][0][key] = value
            sign(changed)
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_url_host_path_slug_and_types_match_actual_own_product(self):
        source, _, kwargs = printed_fixture()
        for url in ['http://shop.mercaso.com/products/synthetic-food-can',
                    'https://shop.mercaso.com.evil.test/products/synthetic-food-can',
                    'https://shop.mercaso.com./products/synthetic-food-can',
                    'https://shop.mercaso.com/products/wrong',
                    'https://shop.mercaso.com/products/synthetic-food-can;other',
                    'https://shop.mercaso.com/products/synthetic-food-can?other=1',
                    'https://user@shop.mercaso.com/products/synthetic-food-can',
                    'https://shop.mercaso.com:443/products/synthetic-food-can']:
            changed = copy.deepcopy(kwargs); item = changed['source_reviews_manifest']['reviews'][0]['evidence'][0]
            item['url'] = item['capture']['responseUrl'] = url
            with self.subTest(url=url), self.assertRaises(ValueError): builder.build(source, **changed)
        for key, value in [('slug', 'wrong'), ('slug', None), ('path', '/wrong'), ('id', 'merchant-12345'),
                           ('itemSize', '4.25'), ('itemSize', True), ('itemSize', 0), ('itemSize', -4.25),
                           ('itemSize', 4.26), ('unitOfMeasure', 'servings'), ('unitOfMeasure', []),
                           ('unitOfMeasure', 'fl oz')]:
            changed = copy.deepcopy(kwargs); change_product(changed, lambda product: product.update({key: value}))
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): builder.build(source, **changed)
        # Observed collection paths remain supported without changing own slug.
        changed = copy.deepcopy(kwargs); item = changed['source_reviews_manifest']['reviews'][0]['evidence'][0]
        item['url'] = item['capture']['responseUrl'] = 'https://shop.mercaso.com/collections/synthetic-food/products/synthetic-food-can'
        builder.build(source, **changed)

    def test_recommendation_alias_case_and_pack_barcodes_cannot_replace_exact_each_tag(self):
        source, _, kwargs = printed_fixture()
        for tags in [[], ['UPC-Each: 00012345678905'], ['UPC-Case: 012345678905'],
                     ['UPC-Each: 012345678912'], ['UPC-Each: 012345678905 012345678912'],
                     ['UPC-Each: 012345678905', 'UPC-Each: 012345678905']]:
            changed = copy.deepcopy(kwargs); change_product(changed, lambda product: product.update(tags=tags))
            with self.subTest(tags=tags), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        product = own_product(changed); product['tags'] = ['UPC-Each: 012345678912']
        bind_body(changed, document({'props': {'pageProps': {'product': product,
                   'sameBrandProducts': [{'tags': ['UPC-Each: 012345678905'], 'itemSize': 4.25, 'unitOfMeasure': 'oz'}]}}}))
        with self.assertRaises(ValueError): builder.build(source, **changed)

    def test_individual_option_and_own_first_image_are_not_case_or_gallery_inference(self):
        source, _, kwargs = printed_fixture()
        mutations = [lambda p: p.update(variants=p['variants'][1:]),
                     lambda p: p['variants'][0].update(barcode='012345678905'),
                     lambda p: p['variants'][0].pop('barcode'),
                     lambda p: p['variants'][0].update(options=[]),
                     lambda p: p['variants'].append(copy.deepcopy(p['variants'][0])),
                     lambda p: p.update(images=[{'url': 'https://example.test/wrong.jpg'}, p['images'][0]]),
                     lambda p: p.update(images=[])]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(kwargs); change_product(changed, mutation)
            with self.subTest(index=index), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs); change_product(changed, lambda p: p.update(productsInPackage='24', packSize={'value': '24'}))
        catalog, _ = builder.build(source, **changed)
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '4.25 oz')

    def test_printed_barcode_net_count_and_actual_manual_identifiers_are_bound(self):
        source, _, kwargs = printed_fixture()
        for key, value in [('visibleUpc', '012345678912'), ('visibleUpc', '00012345678905'),
                           ('visibleNetContent', '4.25 fl oz'), ('visibleNetContent', 'one serving'),
                           ('visibleContainerCount', True), ('visibleContainerCount', 18),
                           ('countBasis', 'productsInPackage'), ('codeBasis', 'variant.barcode'),
                           ('netContentBasis', 'nutrition_serving'), ('sourceRow', True),
                           ('rootInspectionReceiptSha256', ''), ('independentInspectionReceiptSha256', 'e' * 63),
                           ('rootInspectionReceiptSha256', 'e' * 64), ('rawFieldsSha256', '0' * 64),
                           ('rawNameSha256', '0' * 64), ('originalSha256', '0' * 64),
                           ('publicImageSha256', '0' * 64), ('scope', 'current_stock')]:
            changed = copy.deepcopy(kwargs); receipt = changed['source_reviews_manifest']['reviews'][0]['evidence'][1]['inspectionReceipt']
            receipt[key] = value; sign(changed)
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): builder.build(source, **changed)
        for key in ['rootInspectionReceiptSha256', 'independentInspectionReceiptSha256']:
            changed = copy.deepcopy(kwargs); changed['source_reviews_manifest']['reviews'][0]['evidence'][1]['inspectionReceipt'].pop(key); sign(changed)
            with self.subTest(missing=key), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_new_method_has_original_food_singleton_unknown_measurement_scope_only(self):
        for fields in [{'DEPNAME': 'MISC'}, {'DEPNAME': 'Vodka'}, {'ItemName': 'Example Canned Food 4oz'},
                       {'ItemName': 'Example Canned Food case'}, {'ItemName': 'Example Canned Food 12/CS'},
                       {'ItemName': 'Example Canned Food 2x12'}, {'PackName': ' '}, {'PackName': 'single'}]:
            source = export(dict({'ItemName': 'Example Canned Food', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905',
                                  'ItemPrice': '', 'TotalQty_Multi': ''}, **fields))
            source, _, kwargs = printed_fixture(source)
            with self.subTest(fields=fields), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, baseline, kwargs = printed_fixture()
        changed = copy.deepcopy(kwargs)
        changed['category_reviews_manifest'] = {'schemaVersion': 1, 'reviews': [category_review(source, 'drinks')]}
        with self.assertRaises(ValueError): builder.build(source, **changed)
        _, audit = builder.build(source)
        validated = builder.validated_source_reviews(kwargs['source_reviews_manifest'])
        for mode in ['report', 'identity', 'category']:
            records = {r['sku']: copy.deepcopy(r) for r in audit['records']}; identities = {}
            if mode == 'report': records['1']['sourceOrigin'] = 'report'
            if mode == 'identity': identities['1'] = {'action': 'retain_flagged'}
            if mode == 'category': records['1']['categoryReview'] = {'category': 'food'}
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                builder.apply_missing_measurements(copy.deepcopy(baseline['products']), records, validated,
                    kwargs['image_manifest'], kwargs['private_direct_image_evidence_manifest'], {'1'}, identities)

    def test_prior_title_guard_sees_unknown_label_before_late_fill(self):
        source, _, _ = printed_fixture()
        titles = public_name_review(source, 'Example Canned Food in Vegetable Oil')
        source, _, kwargs = printed_fixture(source, titles)
        catalog, audit = builder.build(source, **kwargs)
        self.assertEqual(catalog['products'][0]['name'], 'Example Canned Food in Vegetable Oil')
        self.assertEqual(audit['records'][0]['publicNameCorrection']['expectedLabel'], 'Size not specified')
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '4.25 oz')
        kwargs['public_name_reviews_manifest']['reviews'][0]['expectedLabel'] = '4.25 oz'
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_original_count_context_rejects_reviewed_repros_and_nonterminal_food_containers(self):
        # Full builds regenerate all row/name/public/receipt guards for each
        # original name, so rejection cannot be attributed to stale hashes.
        hints = ['2 cans in oil', '2 bottles Original', '2btls Original', 'CS24', 'CT12',
                 'btl2 Original', 'can2 Original', '2cans in oil', 'can:2 Original',
                 'CS24 Original', 'CT12 Original', '24CS Original', '12CT Original',
                 'btl #2 Original', '2/btl Original', '2 per can Original',
                 'pkg2 Original', '2pkgs Original', 'pack of 2 Original', 'case qty:24 Original',
                 'jar2 Original', '2jars in oil', 'tub2 Original', '2 tubs Original',
                 'pouch2 Original', '2pouches Original', 'packet2 Original', '2 packets Original',
                 'bar2 Original', '2 bars Original', 'roll2 Original', '2rolls Original',
                 'bag2 Original', 'tin2 Original', 'cup2 Original', '2 sachets Original',
                 'single bottle Original', 'two cans in oil', 'three jars Original',
                 '2xCans Original', '2×bottles Original', 'Twin Pack Original', 'TwinPack Original',
                 'twin cans Original', 'double jars Original', 'pair of jars Original',
                 'x24 Original', '24x Original', '×24 Original']
        for hint in hints:
            source = export({'ItemName': 'Example Food ' + hint, 'DEPNAME': 'Snacks',
                             'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
            source, _, kwargs = printed_fixture(source)
            with self.subTest(hint=hint), self.assertRaisesRegex(ValueError, 'genuinely absent original size/pack metadata'):
                builder.build(source, **kwargs)

    def test_original_incidental_brand_numbers_are_not_count_metadata(self):
        for name in ['X24Cleaner Food', '100Grand Food', '100 Grand Food',
                     'CSPro Food', 'PKGWorks Food', 'Jarlsberg Food', 'Rolland Food']:
            source = export({'ItemName': name, 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905',
                             'ItemPrice': '', 'TotalQty_Multi': ''})
            source, baseline, kwargs = printed_fixture(source)
            with self.subTest(name=name):
                catalog, audit = builder.build(source, **kwargs)
                self.assertEqual(catalog['products'][0]['name'], baseline['products'][0]['name'])
                self.assertEqual(catalog['products'][0]['id'], baseline['products'][0]['id'])
                self.assertEqual(catalog['products'][0]['variants'][0]['label'], '4.25 oz')
                self.assertEqual(audit['records'][0]['source']['ItemName'], name)

    def test_original_written_count_grammar_holds_cardinals_scales_and_compounds(self):
        # Exercise a grammar across count words, direction, joining and Unicode
        # hyphens; every build refreshes all source/name/inspection bindings.
        cardinal_words = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight',
                          'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen',
                          'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen',
                          'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy',
                          'eighty', 'ninety', 'dozen', 'hundred', 'thousand',
                          'million', 'billion', 'trillion']
        hints = [f'{word} cans in oil' for word in cardinal_words]
        hints += [f'bottles {word} Original' for word in cardinal_words]
        hints += [f'two{separator}can Original' for separator in ['-', '\u2010', '\u2011', '\u2012', '\u2013', '\u2014', '\u2015', '\u2212']]
        hints += ['thirteen bottles Original', 'two-bottle Original',
                  'twenty-one cans Original', 'twenty one cans Original',
                  'thirty-twocans Original', 'onehundredbottles Original',
                  'onehundredandtwentyonejars Original',
                  'one hundred and twenty-three pouches Original',
                  'two thousand four hundred cans Original',
                  'one-hundred-and-twenty-one-can Original', 'ninety-nine-bottle Original',
                  'hundreds of cans Original', 'thousands of bottles Original',
                  'bottle-thirty-two Original', 'btlonehundred Original',
                  'cans twenty-one Original', 'jar\u2011one\u2011hundred Original',
                  '2\u2011cans Original', 'btl\u201424 Original', 'CT\u221212 Original',
                  'thirty-two x cans Original', 'a dozen cans Original',
                  'half a dozen cans Original', 'a couple of cans Original']
        for hint in hints:
            with self.subTest(hint=hint):
                self.assertIsNotNone(builder.printed_food_count_hint('Example Food ' + hint))
                source = export({'ItemName': 'Example Food ' + hint, 'DEPNAME': 'Snacks',
                                 'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
                source, _, kwargs = printed_fixture(source)
                with self.assertRaisesRegex(ValueError, 'genuinely absent original size/pack metadata'):
                    builder.build(source, **kwargs)

    def test_original_container_context_holds_without_parsing_count_or_intervening_words(self):
        # This method deliberately holds even an unnumbered container title.
        # A single visible photo cannot prove such raw pack context absent.
        containers = ['can', 'cans', 'bottles', 'btls', 'jars', 'tubs', 'pouches',
                      'packets', 'bars', 'rolls', 'bags', 'trays', 'tins', 'cups',
                      'containers', 'tubes', 'sachets', 'sleeves', 'sticks',
                      'box', 'boxes', 'package', 'packages', 'cartons', 'pack',
                      'unit', 'count', 'piece']
        modifiers = ['large', 'assorted imported retail',
                     'arbitrary seasonal descriptive words with no bounded adjective list']
        hints = ['2 large cans in oil', 'two large cans in oil', '2 assorted jars Original']
        hints += [f'2 {modifier} {container} in oil'
                  for modifier in modifiers for container in containers]
        hints += [f'{container} described by arbitrary words then two Original'
                  for container in containers]
        hints += [f'Original {container} in oil' for container in containers]
        hints += ['100Grand Food jar', 'X24Cleaner Food bottle',
                  'two decorated extra family size bottles Original']
        for hint in hints:
            with self.subTest(hint=hint):
                self.assertIsNotNone(builder.printed_food_count_hint('Example Food ' + hint))
                source = export({'ItemName': 'Example Food ' + hint, 'DEPNAME': 'Snacks',
                                 'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
                source, _, kwargs = printed_fixture(source)
                with self.assertRaisesRegex(ValueError, 'genuinely absent original size/pack metadata'):
                    builder.build(source, **kwargs)

    def test_full_public_image_private_provenance_and_local_original_bytes_still_gate_fill(self):
        source, _, kwargs = printed_fixture()
        mutations = [lambda k: k['image_manifest']['images'][0].update(alt='Changed uninspected caption'),
                     lambda k: k['image_manifest']['images'][0].update(expectedLabel='4.25 fl oz'),
                     lambda k: k['image_manifest']['images'][0]['verification'].update(professional=False),
                     lambda k: k['private_direct_image_evidence_manifest']['entries'][0].update(sourceImageUrl='https://example.test/wrong.jpg'),
                     lambda k: k.update(image_manifest=None),
                     lambda k: k.update(private_direct_image_evidence_manifest=None)]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(kwargs); mutation(changed)
            with self.subTest(index=index), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs); r = changed['source_reviews_manifest']['reviews'][0]; photo = r['evidence'][1]
        r['expectedImageSha256'] = photo['sha256'] = '0' * 64
        for entry in [changed['image_manifest']['images'][0], changed['private_direct_image_evidence_manifest']['entries'][0]]:
            entry['sha256'] = '0' * 64
        photo['inspectionReceipt'].update(sha256='0' * 64, originalSha256='0' * 64,
            publicImageSha256=builder.review_evidence_sha256(changed['image_manifest']['images'][0]))
        sign(changed)
        with self.assertRaisesRegex(ValueError, 'hash does not match'): builder.build(source, **changed)

    def test_private_projection_retains_html_and_prices_privately_and_does_not_broaden_legacy(self):
        source, _, kwargs = printed_fixture()
        expected, _ = builder.build(source, **kwargs)
        entry = kwargs['source_reviews_manifest']['reviews'][0]
        evidence = entry['evidence']; digest = builder.review_evidence_sha256(evidence)
        entry['evidence'] = {'privateEvidenceSha256': digest}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND, 'records': {digest: evidence}}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                actual, _ = builder.build(source, **kwargs)
        self.assertEqual(actual, expected)
        for private in ['responseBody', '__NEXT_DATA__', 'currencyCode', '17.13', 'sourceImageUrl', 'https:']:
            self.assertNotIn(private, json.dumps(actual))
            self.assertNotIn(private, json.dumps(kwargs['source_reviews_manifest']))
        source, _, kwargs = printed_fixture()
        kwargs['source_reviews_manifest']['reviews'][0].pop('verificationMethod')
        with self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, _, kwargs = missing_fixture()
        kwargs['source_reviews_manifest']['reviews'][0]['verificationMethod'] = builder.PRINTED_FOOD_UNIT_METHOD
        with self.assertRaises(ValueError): builder.build(source, **kwargs)


if __name__ == '__main__':
    unittest.main()
