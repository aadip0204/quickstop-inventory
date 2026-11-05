"""Entirely synthetic metadata/printed-mass proofs; no actual product admission."""
import copy
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_catalog import builder, export, ROOT
from test_missing_measurement import missing_fixture
from test_printed_food_unit import document


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def producer_body(title='Example Food', size='4.25', unit='oz', codes=None):
    codes = codes or ['opaque-publisher-code-27']
    url = 'https://www.delmonte.com/products/vegetables/example/example-food'
    image = 'https://www.delmonte.com/synthetic-example.jpg'
    power = {'node': '123', 'nodeTitle': title, 'pageUrl': url, 'imageUrl': image, 'upc': codes}
    catalog = {'productId': '123', 'productName': title, 'productPageURL': url,
               'productImageURL': image, 'upcs': codes}
    return ('<!DOCTYPE html><html><head><script type="application/json" data-drupal-selector="drupal-settings-json">'
        + json.dumps({'delmonte': {'powerReviews': power}}) + '</script><script>'
        + 'window.bvDCC = {catalogData: {locale: "en_US", catalogProducts: ['
        + json.dumps(catalog)[:-1] + ',}]}};</script></head><body>'
        + '<div id="block-delmonte-dm-system-main"><section class="banner product-page">'
        + '<h1>' + title + '</h1><span id="node-id" data-node-id="123"></span>'
        + '<h4>Sizes Available: ' + size + unit + '</h4></section>'
        + '<section class="product_ingredients"><div class="nutrition"><div class="nutrition-option">'
        + '<h4>Fresh Cut® ' + title + ', ' + size + ' ' + unit + '. Can</h4>'
        + '<h6>Serving Size <span>1/2 Cup</span></h6></div></div></section></div></body></html>')


def receipt_sign(kwargs):
    review = kwargs['source_reviews_manifest']['reviews'][0]
    source, producer, photo = review['evidence']
    receipt = photo['inspectionReceipt']
    receipt.update(distributorSourceSha256=source['sourceSha256'], producerSourceSha256=producer['sourceSha256'],
                   selectedProductSha256=source['selectedProductSha256'],
                   producerCodeAdjudicationSha256=builder.review_evidence_sha256(producer['codeAdjudication']))
    photo['inspectionReceiptSha256'] = builder.review_evidence_sha256(receipt)


def bind_distributor(kwargs, body):
    review = kwargs['source_reviews_manifest']['reviews'][0]
    source = review['evidence'][0]
    source['capture']['responseBody'] = body
    source['sourceSha256'] = digest(body)
    try:
        product = builder.captured_mercaso_food_product(body, '1')
        source['selectedProductSha256'] = builder.metadata_food_product_sha256(product)
    except ValueError:
        pass
    receipt_sign(kwargs)


def own_product(kwargs):
    return json.loads(kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]['capture']['responseBody']
        .split('<script id="__NEXT_DATA__" type="application/json">')[1].split('</script>')[0])['props']['pageProps']['product']


def change_product(kwargs, mutate):
    product = own_product(kwargs); mutate(product)
    bind_distributor(kwargs, document({'props': {'pageProps': {'product': product}}}))


def fixture(source=None, names=None):
    source = source or export({'ItemName': 'Del Monte Example Food', 'DEPNAME': 'Snacks',
                               'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''},
                             {'ItemName': 'Neighbor Food 8oz', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678912'})
    source, baseline, kwargs = missing_fixture(source, label='4.25 oz', names=names)
    review = kwargs['source_reviews_manifest']['reviews'][0]
    image = kwargs['image_manifest']['images'][0]
    private = kwargs['private_direct_image_evidence_manifest']['entries'][0]
    product = next(p for p in baseline['products'] if any(v['sku'] == '1' for v in p['variants']))
    review.update(verificationMethod=builder.METADATA_FOOD_UNIT_METHOD,
                  expectedProductSha256=builder.review_evidence_sha256(product))
    url = 'https://shop.mercaso.com/products/synthetic-food'
    image_url = 'https://cdn.example.test/synthetic-food.png'
    private.update(sourcePage=url, sourceImageUrl=image_url)
    native = {'id': 'gid://shopify/Product/123', 'name': review['expectedPublicName'],
              'productTitle': review['expectedPublicName'], 'oldTitle': review['expectedPublicName'] + ', 4.25 oz (18 Pack)',
              'slug': 'synthetic-food', 'path': '/synthetic-food', 'itemSize': 4.25, 'unitOfMeasure': 'oz',
              'tags': ['UPC-Case: 10012345678902', 'UPC-Each: ' + review['expectedUpc'],
                       'UPC-Each: 0012345678905', 'UPP:18', 'Size_4.25oz'],
              'variants': [{'name': 'Individual', 'barcode': None,
                            'options': [{'displayName': 'title', 'values': [{'label': 'Individual'}]}]},
                           {'name': 'Pack', 'barcode': 'synthetic-case-and-other-code'}],
              'images': [{'url': image_url}], 'packSize': {'value': '18', 'type': 'number_integer'},
              'productsInPackage': '18', 'price': {'value': 22.42}, 'availableForSale': True}
    body = document({'props': {'pageProps': {'product': native}}})
    source_evidence = {'kind': 'distributor_exact_consumer_metadata_record', 'url': url,
        'supports': 'Synthetic exact own typed metadata; wholesale case context is retained separately.',
        'publisher': 'Mercaso', 'sourceRole': 'direct_seller_wholesaler', 'sourceSha256': digest(body),
        'retrievedOn': '2026-11-04', 'capture': {'status': 200, 'complete': True,
            'contentType': 'text/html; charset=utf-8', 'responseUrl': url, 'responseBody': body},
        'consumerCodeBasis': 'own_product_typed_UPC_Each_tag', 'observedConsumerCode': review['expectedUpc'],
        'consumerTagIndex': 1, 'selectedProductSha256': builder.metadata_food_product_sha256(
            builder.captured_mercaso_food_product(body, '1')),
        'retailUnitsPerPackage': 1, 'retailUnitCountBasis': 'individual_option_and_manual_single_container'}
    body = producer_body()
    url = 'https://www.delmonte.com/products/vegetables/example/example-food'
    producer = {'kind': 'manufacturer_named_net_content_corroboration', 'url': url,
        'supports': 'General own named-product net weight only; no exact consumer code or generation association.',
        'publisher': 'Del Monte', 'sourceRole': 'manufacturer_name_and_net_content_corroboration_only',
        'sourceSha256': digest(body), 'retrievedOn': '2026-11-04', 'capture': {'status': 200,
            'complete': True, 'contentType': 'text/html; charset=UTF-8', 'responseUrl': url, 'responseBody': body},
        'corroborationScope': 'general_named_product_net_content_only', 'manufacturerConsumerCodeMatchClaimed': False,
        'publisherCodeFields': builder.captured_delmonte_mass_corroboration(body, url, '1')['publisherCodeFields'],
        'codeAdjudication': {'scope': 'own_node_codes_retained_without_cross_source_equivalence',
            'consumerCodeAssociation': 'unestablished_not_used_for_assignment',
            'assessment': 'Both native fields retained literally; exact consumer-code association unestablished. General name/net only.',
            'rootReceiptSha256': '1' * 64, 'independentReceiptSha256': '2' * 64}}
    _, prior_audit = builder.build(source, name_manifest=names)
    receipt = {'scope': 'original_inventory_unit', 'sku': '1', 'upc': review['expectedUpc'],
        'publicName': review['expectedPublicName'], 'expectedLabel': 'Size not specified', 'verifiedLabel': '4.25 oz',
        'productId': review['expectedProductId'], 'productSha256': review['expectedProductSha256'], 'groupSkus': ['1'],
        'sourceRow': 1, 'rawFieldsSha256': review['expectedSourceRecordSha256'], 'rawNameSha256': review['expectedNameSha256'],
        'sha256': image['sha256'], 'originalSha256': image['sha256'], 'sourceImageUrl': image_url,
        'visibleNetContent': '4.25 oz', 'visibleNetContentLiteral': 'NET WT 4.25 OZ (120g)',
        'countBasis': 'manual_visible_packaging', 'codeBasis': 'own_product_typed_consumer_metadata',
        'netContentBasis': 'printed_package_net_content', 'physicalBarcodeReadable': False,
        'manufacturerConsumerCodeMatchClaimed': False, 'exactConsumerMetadataCodeMatch': True,
        'retailerIdentityConflictObserved': False, 'rawIdentityConflictObserved': False, 'retailUnitConflictObserved': False,
        'distributorSourceSha256': source_evidence['sourceSha256'], 'producerSourceSha256': producer['sourceSha256'],
        'selectedProductSha256': source_evidence['selectedProductSha256'],
        'producerCodeAdjudicationSha256': builder.review_evidence_sha256(producer['codeAdjudication']),
        'inspectedOn': '2026-11-04', 'src': image['src'], 'publicImageSha256': builder.review_evidence_sha256(image),
        'visibleContainerCount': 1, 'verification': copy.deepcopy(image['verification']),
        'nameCorrectionSha256': builder.review_evidence_sha256(prior_audit['records'][0].get('nameCorrection')),
        'rootInspectionReceiptSha256': '3' * 64, 'independentInspectionReceiptSha256': '4' * 64}
    photo = {'kind': 'inspected_package_photo', 'url': image_url, 'supports': 'Synthetic whole single container and printed net inspection.',
             'sha256': image['sha256'], 'inspectedOn': '2026-11-04', 'inspectionReceipt': receipt,
             'inspectionReceiptSha256': builder.review_evidence_sha256(receipt)}
    review['evidence'] = [source_evidence, producer, photo]
    return source, baseline, kwargs


class MetadataFoodUnitTests(unittest.TestCase):
    def test_only_reviewed_missing_label_and_photo_change_with_all_raw_names_groups_preserved(self):
        source, baseline, kwargs = fixture(); original = copy.deepcopy(kwargs)
        catalog, audit = builder.build(source, **kwargs)
        actual = next(v for p in catalog['products'] for v in p['variants'] if v['sku'] == '1')
        expected = copy.deepcopy(baseline)
        next(v for p in expected['products'] for v in p['variants'] if v['sku'] == '1').update(label='4.25 oz', image=actual['image'])
        self.assertEqual(catalog, expected); self.assertEqual(kwargs, original)
        self.assertEqual([r['rawFields'] for r in audit['records']], [r['rawFields'] for r in builder.build(source)[1]['records']])
        self.assertIsNone(own_product(kwargs)['variants'][0]['barcode'])
        self.assertEqual(own_product(kwargs)['productsInPackage'], '18')
        self.assertEqual(kwargs['source_reviews_manifest']['reviews'][0]['evidence'][1]['codeAdjudication']['consumerCodeAssociation'], 'unestablished_not_used_for_assignment')

    def test_prior_name_correction_is_preserved_and_hash_bound(self):
        raw = 'Del Monte example food'; source = export({'ItemName': raw, 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905'})
        correction = {'sku': '1', 'expectedUpc': '012345678905', 'expectedNameSha256': digest(raw),
            'replaceTextFrom': raw, 'replaceTextTo': 'Del Monte Example Food', 'scope': 'exact_upc_name_identity',
            'verifiedOn': '2026-11-04', 'evidence': [{'kind': k, 'url': 'https://example.test/' + k}
                for k in ('manufacturer', 'exact_upc_listing', 'inspected_package_photo')]}
        names = {'schemaVersion': 1, 'corrections': [correction]}
        source, _, kwargs = fixture(source, names)
        _, audit = builder.build(source, **kwargs)
        self.assertEqual(audit['records'][0]['nameCorrection'], correction)
        kwargs['source_reviews_manifest']['reviews'][0]['evidence'][2]['inspectionReceipt']['nameCorrectionSha256'] = 'a' * 64
        receipt_sign(kwargs)
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_old_missing_unit_methods_do_not_accept_metadata_proof(self):
        for method in (None, builder.PRINTED_FOOD_UNIT_METHOD):
            source, _, kwargs = fixture(); r = kwargs['source_reviews_manifest']['reviews'][0]
            r.pop('expectedProductSha256')
            if method: r['verificationMethod'] = method
            else: r.pop('verificationMethod')
            with self.subTest(method=method), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_typed_code_is_literal_unique_and_never_case_alias_or_merchant_barcode(self):
        mutations = [lambda p: p['tags'].__setitem__(1, 'UPC-Case: 012345678905'),
            lambda p: p['tags'].__setitem__(1, 'UPC-Each: 0012345678905'),
            lambda p: p['tags'].append('UPC-Each: 012345678905'),
            lambda p: p['tags'].__setitem__(1, 'UPC-Each: 012345678912'),
            lambda p: p['tags'].__setitem__(1, 'merchant: 012345678905')]
        for mutation in mutations:
            source, _, kwargs = fixture(); change_product(kwargs, mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): builder.build(source, **kwargs)

        for field, value in [('consumerCodeBasis', 'merchant_sku'), ('consumerTagIndex', 0),
                             ('retailUnitsPerPackage', 18), ('retailUnitCountBasis', 'metadata_pack_count')]:
            source, _, kwargs = fixture(); kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_individual_must_be_unique_explicit_and_barcode_null(self):
        for mutation in [lambda p: p['variants'].__setitem__(0, dict(p['variants'][0], name='Pack')),
                         lambda p: p['variants'].append(copy.deepcopy(p['variants'][0])),
                         lambda p: p['variants'][0].update(barcode='012345678905'),
                         lambda p: p['variants'][0].update(options=[]),
                         lambda p: p['variants'][0]['options'][0].update(values=[{'label': 'Pack'}])]:
            source, _, kwargs = fixture(); change_product(kwargs, mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_net_mass_cannot_be_serving_zero_fluid_case_total_or_contradictory_vendor_size(self):
        for mutation in [lambda p: p.update(itemSize=0), lambda p: p.update(itemSize='4.25'),
                         lambda p: p.update(unitOfMeasure='fl oz'), lambda p: p.update(itemSize=76.5),
                         lambda p: p.update(oldTitle='Del Monte Example Food 3oz 18 Pack'),
                         lambda p: p['tags'].append('Size_3oz')]:
            source, _, kwargs = fixture(); change_product(kwargs, mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): builder.build(source, **kwargs)
        for label in ('0 oz', '4.250 oz', '4.25 fl oz', '18 count', '1 L'):
            source, _, kwargs = fixture(); kwargs['source_reviews_manifest']['reviews'][0]['verifiedLabel'] = label
            with self.subTest(label=label), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_same_product_feature_image_and_distributor_private_origin_are_required(self):
        for mutation in [lambda p: p['images'][0].update(url='https://cdn.example.test/other.png'),
                         lambda p: p.update(slug='other'), lambda p: p.update(path='/other'),
                         lambda p: p.update(name='Del Monte Different Food')]:
            source, _, kwargs = fixture(); change_product(kwargs, mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, _, kwargs = fixture(); kwargs['private_direct_image_evidence_manifest']['entries'][0]['sourcePage'] = 'https://shop.mercaso.com/products/other'
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_both_full_captures_and_safe_exact_hosts_are_required(self):
        for position in (0, 1):
            for field, value in [('status', 403), ('status', True), ('complete', False), ('contentType', 'application/json'),
                                 ('responseBody', 'changed'), ('responseUrl', 'https://example.test/other')]:
                source, _, kwargs = fixture(); kwargs['source_reviews_manifest']['reviews'][0]['evidence'][position]['capture'][field] = value
                with self.subTest(position=position, field=field), self.assertRaises(ValueError): builder.build(source, **kwargs)
            for url in ('https://evil.test/product', 'https://user@shop.mercaso.com/products/synthetic-food',
                        'https://www.delmonte.com:443/products/vegetables/example/example-food'):
                source, _, kwargs = fixture(); item = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][position]
                item['url'] = item['capture']['responseUrl'] = url
                with self.subTest(position=position, url=url), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_duplicate_unscoped_or_recommendation_only_distributor_json_rejects(self):
        for mutation in (lambda b: '<html><body><!--' + b + '--></body></html>',
                         lambda b: b.replace('id="__NEXT_DATA__"', 'id="other"'),
                         lambda b: b.replace('</head>', b.split('<head>')[1].split('</head>')[0] + '</head>'),
                         lambda b: b.replace('"product":', '"recommendation":'),
                         lambda b: b.replace('"itemSize":', '"itemSize": 2, "itemSize":')):
            source, _, kwargs = fixture(); bind_distributor(kwargs, mutation(kwargs['source_reviews_manifest']['reviews'][0]['evidence'][0]['capture']['responseBody']))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_producer_named_net_is_live_main_content_not_serving_recommendation_or_hidden(self):
        bodies = [producer_body(title='Other Food'), producer_body(size='3'),
                  producer_body().replace('Sizes Available: 4.25oz', 'Serving Size: 4.25oz'),
                  producer_body().replace('class="product_ingredients"', 'class="recommendations"'),
                  producer_body().replace('id="block-delmonte-dm-system-main"', 'id="recommendations"'),
                  '<html><body><!--' + producer_body() + '--></body></html>',
                  producer_body().replace('class="banner product-page"', 'class="other" class="banner product-page"'),
                  producer_body().replace('<h1>Example Food</h1>', '<h1>Example Food</h2>'),
                  producer_body().replace('type="application/json" data-drupal-selector', 'type="application/json" type="application/json" data-drupal-selector'),
                  producer_body().replace('<h4>Sizes Available: 4.25oz</h4>', '<h4>Sizes Available: 4.25oz<span> 3 oz</span></h4>')]
        for body in bodies:
            source, _, kwargs = fixture(); p = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][1]
            p['capture']['responseBody'] = body; p['sourceSha256'] = digest(body); receipt_sign(kwargs)
            with self.subTest(body=body[:50]), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_producer_active_assignment_and_exact_known_callback_remain_supported(self):
        callback = ('window.bvCallback = function (BV) { BV.pixel.trackEvent("CatalogUpdate", {'
                    "type: 'Product', locale: window.bvDCC.catalogData.locale, "
                    'catalogProducts: window.bvDCC.catalogData.catalogProducts}); };')
        for with_callback in (False, True):
            source, _, kwargs = fixture()
            producer = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][1]
            body = producer['capture']['responseBody']
            if with_callback:
                body = body.replace('}]}};</script>', '}]}};' + callback + '</script>')
            producer['capture']['responseBody'] = body; producer['sourceSha256'] = digest(body); receipt_sign(kwargs)
            catalog, _ = builder.build(source, **kwargs)
            variant = next(v for p in catalog['products'] for v in p['variants'] if v['sku'] == '1')
            self.assertEqual(variant['label'], '4.25 oz')
            self.assertIsNotNone(variant['image'])

    def test_commented_quoted_conditional_or_extra_js_is_not_active_producer_assignment(self):
        for wrap in (lambda js: '/*' + js + '*/', lambda js: '// ' + js,
                     lambda js: '`' + js + '`;', lambda js: json.dumps(js) + ';',
                     lambda js: 'if (false) {' + js + '}',
                     lambda js: 'window.unreviewed = 1;' + js,
                     lambda js: js + 'window.unreviewed = 1;',
                     lambda js: js + js):
            source, _, kwargs = fixture()
            producer = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][1]
            body = producer['capture']['responseBody']
            script = re.search(r'<script>(.*?)</script>', body, re.S).group(1)
            body = body.replace('<script>' + script + '</script>', '<script>' + wrap(script) + '</script>')
            producer['capture']['responseBody'] = body; producer['sourceSha256'] = digest(body); receipt_sign(kwargs)
            with self.subTest(wrap=wrap), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_raw_text_and_inert_html_cannot_supply_own_product_markup(self):
        wrappers = [(tag, lambda html, tag=tag: '<' + tag + '>' + html + '</' + tag + '>')
                    for tag in ('textarea', 'title', 'xmp', 'iframe', 'noembed', 'noframes', 'template', 'noscript')]
        wrappers += [('textarea-false-close', lambda html: '<textarea></body></html>' + html + '</textarea>'),
                     ('plaintext', lambda html: '<plaintext>' + html),
                     ('plaintext-false-close', lambda html: '<plaintext></plaintext>' + html),
                     ('textarea-self-close', lambda html: '<textarea/>' + html),
                     ('xmp-self-close', lambda html: '<xmp/>' + html),
                     ('script-self-close', lambda html: '<script/>' + html)]
        for label, wrap in wrappers:
            source, _, kwargs = fixture()
            producer = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][1]
            body = producer['capture']['responseBody']
            start = body.index('<div id="block-delmonte-dm-system-main">')
            end = body.index('</body>')
            body = body[:start] + wrap(body[start:end]) + body[end:]
            producer['capture']['responseBody'] = body; producer['sourceSha256'] = digest(body); receipt_sign(kwargs)
            with self.subTest(wrapper=label), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_both_producer_code_fields_and_unestablished_scoped_adjudication_are_retained(self):
        for mutate in [lambda p: p['publisherCodeFields'].pop('drupalSettings:/delmonte/powerReviews/upc'),
                       lambda p: p['publisherCodeFields'].__setitem__('window.bvDCC:/catalogData/catalogProducts/0/upcs', ['changed']),
                       lambda p: p.update(manufacturerConsumerCodeMatchClaimed=True),
                       lambda p: p['codeAdjudication'].update(consumerCodeAssociation='exact_inventory_upc_match'),
                       lambda p: p['codeAdjudication'].update(independentReceiptSha256='1' * 64),
                       lambda p: p.update(sourceRole='manufacturer_exact_consumer_gtin')]:
            source, _, kwargs = fixture(); mutate(kwargs['source_reviews_manifest']['reviews'][0]['evidence'][1]); receipt_sign(kwargs)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_physical_barcode_manufacturer_code_and_metadata_count_claims_cannot_be_invented(self):
        for key, value in [('physicalBarcodeReadable', True), ('physicalBarcodeReadable', 0),
                           ('exactConsumerMetadataCodeMatch', 1), ('manufacturerConsumerCodeMatchClaimed', True),
                           ('exactConsumerMetadataCodeMatch', False), ('visibleContainerCount', 18),
                           ('countBasis', 'metadata_pack_count'), ('visibleNetContentLiteral', 'SERVING 4.25 OZ'),
                           ('visibleNetContentLiteral', 'NET WT 3 OZ'), ('visibleNetContent', '3 oz'),
                           ('retailerIdentityConflictObserved', True), ('rawIdentityConflictObserved', True),
                           ('retailUnitConflictObserved', True), ('rootInspectionReceiptSha256', '4' * 64)]:
            source, _, kwargs = fixture(); kwargs['source_reviews_manifest']['reviews'][0]['evidence'][2]['inspectionReceipt'][key] = value; receipt_sign(kwargs)
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_full_product_raw_image_and_receipt_staleness_rejects(self):
        for key in ('expectedSourceRow', 'expectedNameSha256', 'expectedSourceRecordSha256', 'expectedProductSha256', 'expectedProductId', 'expectedUpc'):
            source, _, kwargs = fixture(); r = kwargs['source_reviews_manifest']['reviews'][0]
            r[key] = 99 if key == 'expectedSourceRow' else 'a' * 64
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **kwargs)
        for key in ('src', 'publicImageSha256', 'sourceImageUrl', 'selectedProductSha256'):
            source, _, kwargs = fixture(); kwargs['source_reviews_manifest']['reviews'][0]['evidence'][2]['inspectionReceipt'][key] = 'changed'
            photo = kwargs['source_reviews_manifest']['reviews'][0]['evidence'][2]
            photo['inspectionReceiptSha256'] = builder.review_evidence_sha256(photo['inspectionReceipt'])
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_original_pack_unit_container_and_count_context_remains_held(self):
        for raw in ('Del Monte Example Food 4oz', 'Del Monte Example Food two large cans',
                    'Del Monte Example Food2cans', 'Del Monte Example Food CS24', 'Del Monte Example Food box',
                    'Del Monte Example Food thirty-two jars'):
            source = export({'ItemName': raw, 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905'})
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                data, _, kwargs = fixture(source); builder.build(data, **kwargs)
        for pack in (' ', 'case', '2pk', '8oz'):
            source = export({'ItemName': 'Del Monte Example Food', 'PackName': pack, 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905'})
            with self.subTest(pack=pack), self.assertRaises(ValueError):
                data, _, kwargs = fixture(source); builder.build(data, **kwargs)

        for item_type in ('case', 'retail_item'):
            source = export({'ItemName': 'Del Monte Example Food', 'DEPNAME': 'Snacks',
                             'MAINUPC': '012345678905', 'ItemTypeDesc': item_type})
            data, _, kwargs = fixture(source)
            with self.subTest(item_type=item_type), self.assertRaises(ValueError): builder.build(data, **kwargs)

    def test_report_identity_category_prior_image_and_nonfood_cannot_fill(self):
        source, baseline, kwargs = fixture(); r = kwargs['source_reviews_manifest']['reviews'][0]
        _, audit = builder.build(source)
        for change in ('report', 'identity', 'category', 'image'):
            products = copy.deepcopy(baseline['products']); records = {v['sku']: copy.deepcopy(v) for v in audit['records']}; identities = {}
            if change == 'report': records['1']['sourceOrigin'] = 'report'
            if change == 'identity': identities['1'] = {}
            if change == 'category': records['1']['categoryReview'] = {}
            if change == 'image': products[0]['variants'][0]['image'] = {'src': 'previous'}
            with self.subTest(change=change), self.assertRaises(ValueError): builder.apply_missing_measurements(
                products, records, {'1': r}, kwargs['image_manifest'], kwargs['private_direct_image_evidence_manifest'], {'1'}, identities)
        for dept in ('MISC', 'Vodka'):
            data = export({'ItemName': 'Del Monte Example Food', 'DEPNAME': dept, 'MAINUPC': '012345678905'})
            data, _, changed = fixture(data)
            with self.subTest(dept=dept), self.assertRaises(ValueError): builder.build(data, **changed)

    def test_exact_singleton_and_exactly_one_public_private_image_are_required(self):
        for field in ('image_manifest', 'private_direct_image_evidence_manifest'):
            source, _, kwargs = fixture(); key = 'images' if field == 'image_manifest' else 'entries'
            kwargs[field][key] *= 2
            with self.subTest(field=field), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, _, kwargs = fixture(); kwargs['source_reviews_manifest']['reviews'][0]['expectedResultingGroupSkus'] = ['1', '2']
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_private_projection_hides_captures_codes_prices_and_stock_without_semantic_change(self):
        source, baseline, kwargs = fixture()
        spec = importlib.util.spec_from_file_location('metadata_food_projection', ROOT / 'scripts/project_review_evidence.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'build_catalog': builder}): spec.loader.exec_module(module)
        ledger = {}; projected = module.project_evidence(kwargs['source_reviews_manifest'], ledger)
        public = json.dumps(projected)
        for token in ('shop.mercaso.com', 'delmonte.com', 'responseBody', 'publisherCodeFields', 'availableForSale', '22.42'):
            self.assertNotIn(token, public)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger.json'; path.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND, 'records': ledger}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                direct = builder.build(source, **kwargs)
                kwargs['source_reviews_manifest'] = projected
                self.assertEqual(builder.build(source, **kwargs), direct)


if __name__ == '__main__':
    unittest.main()
