"""Synthetic retailer names: exact own record + inspected bytes, no mutation."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_catalog import (builder, export, direct_reference_image_fixture,
                          direct_reference_evidence_fixture, public_name_review,
                          category_review)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def fixture(grouped=False):
    # All names, codes, source documents and URLs are synthetic. A checked
    # local asset supplies integrity-test bytes, not real product approval.
    rows = [{'ItemName': 'Example Vodka 750ml', 'DEPNAME': 'Vodka', 'MAINUPC': '012345678905'},
            {'ItemName': 'Example Vodka 375ml' if grouped else 'Neighbor Juice 1L',
             'DEPNAME': 'Vodka' if grouped else 'Juice', 'MAINUPC': '012345678912'}]
    source = export(*rows)
    image = direct_reference_image_fixture(source)
    private = direct_reference_evidence_fixture(image)
    url = 'https://saucey.com/spirits/vodka/example/VO-EXAMPLE-750ML'
    image_url = 'https://cdn.saucey.com/staging/products/VO-EXAMPLE-750ML/image_large.jpg'
    private['entries'][0].update(sourcePage=url, sourceImageUrl=image_url)
    kwargs = {'image_manifest': {'schemaVersion': 1, 'images': [image]},
              'private_direct_image_evidence_manifest': private}
    baseline, audit = builder.build(source, **kwargs)
    product = next(p for p in baseline['products'] if p['variants'][0]['sku'] == '1')
    variant = product['variants'][0]
    raw = audit['records'][0]['rawFields']
    selected = {'sku_id': 'VO-EXAMPLE-750ML', 'title': 'Example', 'subtitle': 'Vodka',
                'upc_code': raw[1], 'volume': '750ml Bottle', 'volume_label': '750ml Bottle',
                'image_large': image_url, 'image': image_url.replace('image_large', 'image'),
                'url': url.removeprefix('https://saucey.com'), 'additional_upcs': []}
    listing = {'kind': 'exact_upc_listing', 'url': url, 'supports': 'Synthetic selected retail name.',
               'publisher': 'Saucey', 'sourceRole': 'retailer', 'sourceSha256': '',
               'retrievedOn': '2026-11-04', 'capture': {'status': 200, 'complete': True,
                   'contentType': 'text/html; charset=utf-8', 'responseUrl': url, 'responseBody': ''}}
    guard = {'sku': '1', 'sourceRow': 1, 'rawFields': raw,
             'rawFieldsSha256': builder.review_evidence_sha256(raw), 'rawNameSha256': digest(raw[2]),
             'disposition': 'retained_variant', 'upc': raw[1], 'name': product['name'],
             'label': variant['label'], 'category': product['category'], 'department': product['department'],
             'productId': product['id'], 'groupSkus': [v['sku'] for v in product['variants']],
             'orderedProduct': copy.deepcopy(product), 'currentImage': copy.deepcopy(private['entries'][0]),
             'beforeNameVerificationStage': {'name': product['name'], 'label': variant['label'],
                                             'noLateIdentityOrLabelReview': True}}
    actual_source = {'publisher': 'Saucey', 'role': 'retailer',
                     'pointer': 'window.__PRELOADED_STATE__/product/product', 'sourcePage': url,
                     'decodedBodySha256': '', 'selectedFields': copy.deepcopy(selected)}
    facts = {'privateOnly': True, 'frozen': True, 'candidates': [{'sku': '1', 'currentGuard': guard,
             'actualSource': actual_source, 'sourceToImageBinding': {
                 'original': {'path': '/synthetic/native.jpg', 'sha256': image['sha256']},
                 'nativeOriginalEqualsCurrentImage': True, 'selectedImageLargeMatchesCanonicalUrl': True}}]}
    decision = {'sku': '1', 'verdict': 'PASS', 'currentName': product['name'],
                'currentLabel': variant['label'], 'literalOriginalUpc': raw[1],
                'orderedGroupSkus': guard['groupSkus'], 'actualSourceProof': {
                    'pointer': actual_source['pointer'], 'actualSelectedFields': copy.deepcopy(selected),
                    'decodedBodySha256': '', 'role': 'retailer',
                    'literalConsumerUpcNotMerchantId': True, 'fullStateReadAndFieldsMatched': True},
                'nativeImage': {'path': '/synthetic/native.jpg', 'sha256': image['sha256'],
                    'actualWholeNativeViewedIndependentlyThisPass': True, 'currentCanonicalBytesMatch': True,
                    'observations': 'Complete Example Vodka package and name read.'}}
    review = {'privateOnly': True, 'reviewer': 'Synthetic independent reviewer',
              'reviewedOn': '2026-11-03', 'packet': {'sha256': ''}, 'records': [decision]}
    photo = {'kind': 'inspected_package_photo', 'url': image_url, 'supports': 'Synthetic name inspection.',
             'sha256': image['sha256'], 'inspection': {}}
    entry = {'sku': '1', 'expectedUpc': raw[1], 'expectedNameSha256': digest(raw[2]),
             'expectedPublicName': product['name'], 'expectedLabel': variant['label'],
             'verifiedOn': '2026-11-03', 'verificationMethod': builder.RETAILER_NAME_METHOD,
             'verification': dict.fromkeys(('brand', 'expression', 'upc', 'visual'), True),
             'evidence': [listing, photo]}
    seal(entry, selected=selected, facts=facts, review=review)
    kwargs['name_manifest'] = {'schemaVersion': 1, 'nameVerifications': [entry]}
    return source, baseline, kwargs


def documents(entry):
    inspection = entry['evidence'][1]['inspection']
    return json.loads(inspection['factsBody']), json.loads(inspection['reviewBody'])


def seal(entry, selected=None, facts=None, review=None, body=None):
    listing, photo = entry['evidence']
    if facts is None:
        facts, review = documents(entry)
    if selected is not None or body is not None:
        if body is None:
            state = {'product': {'product': selected}, 'recommendations': []}
            body = '<html><body><script type="text/javascript">window.__PRELOADED_STATE__ = ' + json.dumps(state) + '</script></body></html>'
        listing['capture']['responseBody'] = body
        listing['sourceSha256'] = digest(body)
        facts['candidates'][0]['actualSource']['decodedBodySha256'] = listing['sourceSha256']
        review['records'][0]['actualSourceProof']['decodedBodySha256'] = listing['sourceSha256']
        if selected is not None:
            facts['candidates'][0]['actualSource']['selectedFields'] = copy.deepcopy(selected)
            review['records'][0]['actualSourceProof']['actualSelectedFields'] = copy.deepcopy(selected)
    facts_body = json.dumps(facts)
    review['packet']['sha256'] = digest(facts_body)
    review_body = json.dumps(review)
    photo['inspection'] = {'factsBody': facts_body, 'factsSha256': digest(facts_body),
                           'reviewBody': review_body, 'reviewSha256': digest(review_body)}


class RetailerNameVerificationTests(unittest.TestCase):
    def test_unchanged_name_preserves_entire_catalog_raw_neighbor_and_metrics(self):
        source, baseline, kwargs = fixture()
        before = copy.deepcopy(kwargs)
        catalog, audit = builder.build(source, **kwargs)
        prior_audit = builder.build(source, **{k: v for k, v in kwargs.items() if k != 'name_manifest'})[1]
        self.assertEqual(catalog, baseline)
        for actual, prior in zip(audit['records'], prior_audit['records']):
            self.assertEqual({k: v for k, v in actual.items() if k != 'nameVerification'}, prior)
        summary = builder.name_audit(catalog, audit)['summary']
        self.assertEqual(summary['nameVerificationRecords'], 1)
        self.assertEqual(summary['fullyVerifiedNames'], 1)
        self.assertEqual(summary['manufacturerRecordVerifiedNames'], 0)
        self.assertEqual(summary['evidenceBackedCorrections'], 0)
        self.assertEqual(kwargs, before)

    def test_existing_ordered_multivariant_group_is_preserved(self):
        source, baseline, kwargs = fixture(grouped=True)
        self.assertEqual([v['sku'] for v in baseline['products'][0]['variants']], ['1', '2'])
        self.assertEqual(builder.build(source, **kwargs)[0], baseline)

    def test_persisted_name_allows_only_independently_validated_sibling_photo_delta(self):
        source, baseline, kwargs = fixture(grouped=True)
        name_proof = copy.deepcopy(kwargs['name_manifest'])
        sibling = direct_reference_image_fixture(source, sku='2')
        kwargs['image_manifest']['images'].append(sibling)
        kwargs['private_direct_image_evidence_manifest']['entries'].extend(
            direct_reference_evidence_fixture(sibling)['entries'])
        catalog, audit = builder.build(source, **kwargs)
        expected = copy.deepcopy(baseline)
        expected['products'][0]['variants'][1]['image'] = {
            key: sibling[key] for key in ('src', 'alt', 'usageBasis', 'attributionRequired')}
        self.assertEqual(catalog, expected)
        self.assertEqual(kwargs['name_manifest'], name_proof)
        self.assertEqual(audit['records'][0]['nameVerification'], name_proof['nameVerifications'][0])
        self.assertEqual(builder.name_audit(catalog, audit)['summary']['fullyVerifiedNames'], 1)
        # Masking the sibling cell does not bypass its independent image gates.
        kwargs['private_direct_image_evidence_manifest']['entries'][-1]['sha256'] = 'a' * 64
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_requires_explicit_supported_method_and_closed_entry_schema(self):
        for mutate in (lambda e: e.pop('verificationMethod'),
                       lambda e: e.update(verificationMethod='other'),
                       lambda e: e.update(manufacturerRecord={}),
                       lambda e: e['verification'].update(visual=False)):
            source, _, kwargs = fixture()
            mutate(kwargs['name_manifest']['nameVerifications'][0])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_legacy_three_kind_requirement_is_unchanged(self):
        source, baseline, kwargs = fixture()
        e = kwargs['name_manifest']['nameVerifications'][0]
        e.pop('verificationMethod')
        e['evidence'] = [{'kind': kind, 'url': 'https://example.test/' + kind}
                         for kind in ('manufacturer', 'exact_upc_listing', 'inspected_package_photo')]
        self.assertEqual(builder.build(source, **kwargs)[0], baseline)
        e['evidence'].pop(0)
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_complete_capture_status_type_url_body_hash_and_dates_are_required(self):
        mutations = [lambda e: e['evidence'][0]['capture'].update(status=201),
                     lambda e: e['evidence'][0]['capture'].update(status=True),
                     lambda e: e['evidence'][0]['capture'].update(complete=False),
                     lambda e: e['evidence'][0]['capture'].update(contentType='application/json'),
                     lambda e: e['evidence'][0]['capture'].update(responseUrl='https://saucey.com/other'),
                     lambda e: e['evidence'][0]['capture'].update(responseBody='changed'),
                     lambda e: e['evidence'][0].update(retrievedOn='2026-99-99'),
                     lambda e: e.update(verifiedOn='2026-02-30')]
        for mutate in mutations:
            source, _, kwargs = fixture(); mutate(kwargs['name_manifest']['nameVerifications'][0])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_unsafe_source_hosts_params_credentials_and_manufacturer_role_reject(self):
        for url in ('http://saucey.com/spirits/vodka/example/VO-EXAMPLE-750ML',
                    'https://saucey.com.evil.test/spirits/vodka/example/VO-EXAMPLE-750ML',
                    'https://user@saucey.com/spirits/vodka/example/VO-EXAMPLE-750ML',
                    'https://saucey.com:443/spirits/vodka/example/VO-EXAMPLE-750ML',
                    'https://saucey.com/spirits/vodka/example/VO-EXAMPLE-750ML;other',
                    'https://saucey.com/spirits/vodka/example/VO-EXAMPLE-750ML?other'):
            source, _, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]
            e['evidence'][0]['url'] = e['evidence'][0]['capture']['responseUrl'] = url
            with self.subTest(url=url), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, _, kwargs = fixture()
        kwargs['name_manifest']['nameVerifications'][0]['evidence'][0]['sourceRole'] = 'manufacturer'
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_only_literal_own_upc_is_accepted_not_alias_case_or_merchant_id(self):
        for code in ('012345678912', '00012345678905', '', 12345678905, None):
            source, _, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]
            facts, _ = documents(e); selected = facts['candidates'][0]['actualSource']['selectedFields']
            selected.update(upc_code=code, additional_upcs=[e['expectedUpc']])
            seal(e, selected=selected)
            with self.subTest(code=code), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_correct_recommendation_cannot_substitute_for_wrong_selected_product(self):
        source, _, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]
        facts, _ = documents(e); selected = facts['candidates'][0]['actualSource']['selectedFields']
        wrong = dict(selected, upc_code='012345678912')
        body = '<html><body><script>window.__PRELOADED_STATE__ = ' + json.dumps(
            {'product': {'product': wrong}, 'recommendations': [selected]}) + '</script></body></html>'
        seal(e, body=body)
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_state_must_be_one_live_complete_own_assignment(self):
        for wrap in (lambda s: '<!--' + s + '-->', lambda s: '<template>' + s + '</template>',
                     lambda s: '<noscript>' + s + '</noscript>', lambda s: s + s,
                     lambda s: s.replace('window.__PRELOADED_STATE__', 'window.other'),
                     lambda s: s.replace('type="text/javascript"', 'type="application/json" type="text/javascript"'),
                     lambda s: s.replace('</script>', '; window.other = {};</script>'),
                     lambda s: s.replace('"product": {"product":', '"product": {"related":')):
            source, _, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]
            body = e['evidence'][0]['capture']['responseBody']
            seal(e, body=wrap(body))
            with self.subTest(wrap=wrap), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_duplicate_native_json_fields_reject(self):
        source, _, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]
        body = e['evidence'][0]['capture']['responseBody'].replace('"upc_code":', '"upc_code":"012345678912", "upc_code":')
        seal(e, body=body)
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_own_url_and_selected_image_cannot_be_substituted(self):
        for field, value in [('url', '/spirits/vodka/other/VO-EXAMPLE-750ML'),
                             ('sku_id', 'OTHER'), ('image_large', 'https://cdn.saucey.com/staging/products/OTHER/image_large.jpg')]:
            source, _, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]
            facts, _ = documents(e); selected = facts['candidates'][0]['actualSource']['selectedFields']
            selected[field] = value; seal(e, selected=selected)
            with self.subTest(field=field), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_inspection_review_and_facts_body_hashes_are_required(self):
        for field in ('factsBody', 'factsSha256', 'reviewBody', 'reviewSha256'):
            source, _, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]
            e['evidence'][1]['inspection'][field] = 'changed'
            with self.subTest(field=field), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, _, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]
        inspection = e['evidence'][1]['inspection']
        inspection['reviewBody'] = inspection['reviewBody'].replace('"verdict":', '"verdict":"HOLD", "verdict":')
        inspection['reviewSha256'] = digest(inspection['reviewBody'])
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_independent_positive_name_expression_and_original_bindings_are_required(self):
        for mutate in (lambda d: d.update(verdict='HOLD'), lambda d: d.update(sku='2'),
                       lambda d: d.update(currentName='Other Vodka'),
                       lambda d: d['actualSourceProof']['actualSelectedFields'].update(subtitle='Other flavor'),
                       lambda d: d['nativeImage'].update(actualWholeNativeViewedIndependentlyThisPass=False),
                       lambda d: d['nativeImage'].update(sha256='a' * 64),
                       lambda d: d['nativeImage'].update(currentCanonicalBytesMatch=False)):
            source, _, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]
            facts, review = documents(e); mutate(review['records'][0]); seal(e, facts=facts, review=review)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_original_row_raw_hash_and_ordered_product_are_checked_at_runtime(self):
        for mutate in (lambda g: g.update(sourceRow=2), lambda g: g.update(rawFieldsSha256='a' * 64),
                       lambda g: g.pop('orderedProduct'),
                       lambda g: g['rawFields'].__setitem__(3, 'Different private pack'),
                       lambda g: g.update(productId='p-' + 'a' * 16),
                       lambda g: g['orderedProduct'].update(id='p-' + 'a' * 16),
                       lambda g: g['orderedProduct'].update(department='Tequila'),
                       lambda g: g['orderedProduct']['variants'][0].update(image=None),
                       lambda g: g['orderedProduct']['variants'][1].update(label='1 L'),
                       lambda g: g['orderedProduct']['variants'][1].update(upc='012345678929'),
                       lambda g: g['orderedProduct']['variants'][1].update(sourceRow=99),
                       lambda g: g['orderedProduct']['variants'][1].pop('image'),
                       lambda g: g['orderedProduct']['variants'].reverse()):
            source, _, kwargs = fixture(grouped=True); e = kwargs['name_manifest']['nameVerifications'][0]
            facts, review = documents(e); mutate(facts['candidates'][0]['currentGuard']); seal(e, facts=facts, review=review)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_common_name_upc_hash_label_guards_and_duplicate_skus_remain(self):
        for key, value in [('expectedUpc', '012345678912'), ('expectedUpc', '123456'),
                           ('expectedNameSha256', 'a' * 64), ('expectedPublicName', 'Other Vodka'),
                           ('expectedLabel', '1 L'), ('sku', '2')]:
            source, _, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]; e[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, _, kwargs = fixture(); kwargs['name_manifest']['nameVerifications'] *= 2
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_existing_image_and_private_provenance_are_required_without_new_approval(self):
        for field in ('sourcePage', 'sourceImageUrl', 'sha256'):
            source, _, kwargs = fixture()
            kwargs['private_direct_image_evidence_manifest']['entries'][0][field] = 'changed'
            with self.subTest(field=field), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, _, kwargs = fixture(); kwargs.pop('image_manifest'); kwargs.pop('private_direct_image_evidence_manifest')
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_cannot_pair_with_title_category_or_source_unit_actions(self):
        source, _, kwargs = fixture()
        variants = [dict(public_name_reviews_manifest=public_name_review(source, 'Example Original Vodka')),
                    dict(category_reviews_manifest={'schemaVersion': 1, 'reviews': [category_review(source, 'essentials')]}),
                    dict(source_reviews_manifest={'schemaVersion': 1, 'reviews': [{
                        'sku': '1', 'expectedUpc': '012345678905', 'expectedNameSha256': digest('Example Vodka 750ml'),
                        'expectedSourceRecordSha256': 'a' * 64, 'action': 'size_needs_confirmation',
                        'reason': 'Synthetic size hold.'}]})]
        for extra in variants:
            with self.subTest(extra=next(iter(extra))), self.assertRaisesRegex(ValueError, 'cannot coexist'):
                builder.build(source, **kwargs, **extra)

    def test_projection_keeps_real_body_shapes_and_private_financial_fields_private(self):
        source, baseline, kwargs = fixture(); e = kwargs['name_manifest']['nameVerifications'][0]
        facts, review = documents(e); selected = facts['candidates'][0]['actualSource']['selectedFields']
        body = '<html><body><script>window.__PRELOADED_STATE__ = ' + json.dumps(
            {'product': {'product': dict(selected, price='synthetic-private-financial-field')}}) + '</script></body></html>'
        seal(e, body=body)
        spec = importlib.util.spec_from_file_location('retailer_name_projection', Path(builder.__file__).with_name('project_review_evidence.py'))
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'build_catalog': builder}): spec.loader.exec_module(module)
        ledger = {}; projected = module.project_evidence(kwargs['name_manifest'], ledger)
        public = json.dumps(projected)
        self.assertNotIn('responseBody', public)
        self.assertNotIn('synthetic-private-financial-field', public)
        self.assertNotIn('saucey.com', public)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'evidence.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND, 'records': ledger}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                kwargs['name_manifest'] = projected
                self.assertEqual(builder.build(source, **kwargs)[0], baseline)


if __name__ == '__main__':
    unittest.main()
