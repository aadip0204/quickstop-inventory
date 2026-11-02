"""A separate, late unit clarification never converts mass or rewrites input."""
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

from test_catalog import (builder, export, manufacturer_correction, public_name_review,
                          direct_reference_image_fixture, direct_reference_evidence_fixture, category_review)


def liquid_fixture(source=None, names=None, titles=None, fluid_label='12.5 fl oz'):
    # Synthetic identities only. Reusing approved local bytes exercises file
    # integrity; none of this evidence approves merchandise or writes assets.
    source = source or export({'ItemName': 'Example Cleaner 12.5oz', 'DEPNAME': 'MISC',
                               'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
    baseline, _ = builder.build(source, name_manifest=names, public_name_reviews_manifest=titles)
    product, variant = next((p, v) for p in baseline['products'] for v in p['variants'] if v['sku'] == '1')
    raw = list(csv.reader(io.StringIO(source.decode())))[1]
    image = direct_reference_image_fixture(source)
    image.update(expectedName=product['name'], expectedLabel=fluid_label)
    private = direct_reference_evidence_fixture(image)
    receipt = {'sku': '1', 'upc': raw[1], 'publicName': product['name'], 'label': fluid_label,
               'src': image['src'], 'sha256': image['sha256'], 'originalSha256': image['sha256'],
               'publicImageSha256': builder.review_evidence_sha256(image),
               'sourceImageUrl': private['entries'][0]['sourceImageUrl'],
               'visibleNetContent': fluid_label, 'container': 'bottle',
               'verification': copy.deepcopy(image['verification']), 'inspectedOn': '2026-11-01'}
    listings = [
        {'kind': 'exact_upc_liquid_unit_listing', 'url': host + '/unit/' + raw[1],
         'supports': 'Synthetic literal unit code and explicit net fluid content of one bottle.',
         'observedUpc': raw[1], 'observedNetContent': fluid_label, 'retailUnitLabel': fluid_label,
         'quantityBasis': 'retail_net_content', 'retailUnitsPerPackage': 1, 'container': 'bottle',
         'sourceSha256': digit * 64, 'retrievedOn': '2026-11-01'}
        for host, digit in [('https://retailer.example.test', 'a'), ('https://shop.other.test', 'b')]]
    photo = {'kind': 'inspected_package_photo', 'url': receipt['sourceImageUrl'],
             'supports': 'Manual synthetic inspection; exact net fluid content is visibly readable.',
             'sha256': image['sha256'], 'inspectedOn': receipt['inspectedOn'],
             'inspectionReceipt': receipt, 'inspectionReceiptSha256': builder.review_evidence_sha256(receipt)}
    review = {'sku': '1', 'expectedUpc': raw[1],
              'expectedNameSha256': hashlib.sha256(raw[2].encode()).hexdigest(),
              'expectedSourceRecordSha256': hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
              'action': 'resolve_verified_ambiguous_ounce_volume', 'expectedSourceRow': 1,
              'expectedPublicName': product['name'], 'expectedLabel': variant['label'],
              'expectedCategory': product['category'], 'expectedDepartment': product['department'],
              'expectedProductId': product['id'], 'expectedResultingGroupSkus': ['1'],
              'verifiedLabel': fluid_label, 'expectedImageSha256': image['sha256'],
              'verification': dict.fromkeys(('brand', 'expression', 'size', 'packaging', 'upc', 'visual'), True),
              'evidence': listings + [photo], 'reviewedOn': '2026-11-01',
              'reason': 'Clarify only the ambiguous ounce unit; preserve its original numeric quantity.'}
    kwargs = {'source_reviews_manifest': {'schemaVersion': 1, 'reviews': [review]},
              'image_manifest': {'schemaVersion': 1, 'images': [image]},
              'private_direct_image_evidence_manifest': private}
    if names:
        kwargs['name_manifest'] = names
    if titles:
        kwargs['public_name_reviews_manifest'] = titles
    return source, baseline, kwargs


def review(kwargs):
    return kwargs['source_reviews_manifest']['reviews'][0]


def food_liquid_fixture(source=None, names=None, titles=None):
    source = source or export({'ItemName': 'Example Original Hot Sauce 6OZ', 'DEPNAME': 'Snacks',
                               'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
    source, baseline, kwargs = liquid_fixture(source, names, titles, fluid_label='6 fl oz')
    review(kwargs)['verificationMethod'] = builder.FOOD_FLUID_OUNCE_METHOD
    return source, baseline, kwargs


class AmbiguousLiquidOunceTests(unittest.TestCase):
    def test_food_opt_in_changes_only_reviewed_bottle_unit_not_neighbor_or_raw(self):
        source = export({'ItemName': 'Example Original Hot Sauce 6OZ', 'DEPNAME': 'Snacks',
                         'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''},
                        {'ItemName': 'Neighbor Hot Sauce 6oz', 'DEPNAME': 'Snacks',
                         'MAINUPC': '012345678912', 'ItemPrice': '', 'TotalQty_Multi': ''})
        source, baseline, kwargs = food_liquid_fixture(source)
        original = copy.deepcopy(kwargs)
        catalog, audit = builder.build(source, **kwargs)
        expected = copy.deepcopy(baseline)
        actual = next(v for p in catalog['products'] for v in p['variants'] if v['sku'] == '1')
        next(v for p in expected['products'] for v in p['variants'] if v['sku'] == '1').update(label='6 fl oz', image=actual['image'])
        self.assertEqual(catalog, expected)
        self.assertEqual(kwargs, original)
        self.assertEqual(next(v for p in catalog['products'] for v in p['variants'] if v['sku'] == '2')['label'], '6 oz')
        self.assertEqual([r['rawFields'] for r in audit['records']], list(csv.reader(io.StringIO(source.decode())))[1:])
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example Original Hot Sauce 6OZ')
        self.assertEqual(audit['records'][0]['ambiguousLiquidOunceResolution']['previousPublicIdentity'],
                         {'name': 'Example Original Hot Sauce', 'label': '6 oz'})

    def test_food_requires_exact_opt_in_and_cannot_opt_other_categories_in(self):
        source, _, kwargs = food_liquid_fixture()
        changed = copy.deepcopy(kwargs); review(changed).pop('verificationMethod')
        with self.assertRaises(ValueError): builder.build(source, **changed)
        for method in ['other', '', None, True, [], {}]:
            changed = copy.deepcopy(kwargs); review(changed)['verificationMethod'] = method
            with self.subTest(method=method), self.assertRaises(ValueError): builder.build(source, **changed)
        for department in ['MISC', 'Vodka', 'Soda']:
            source = export({'ItemName': 'Example Original Hot Sauce 6OZ', 'DEPNAME': department,
                             'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
            source, _, changed = food_liquid_fixture(source)
            with self.subTest(department=department), self.assertRaises(ValueError): builder.build(source, **changed)
        # Existing essentials reviews still reject an unknown added method.
        source, _, legacy = liquid_fixture()
        review(legacy)['verificationMethod'] = 'other'
        with self.assertRaises(ValueError): builder.build(source, **legacy)

    def test_food_requires_one_explicit_fluid_listing_but_preserves_other_bare_fact(self):
        source, _, kwargs = food_liquid_fixture()
        expected, _ = builder.build(source, **kwargs)
        for ambiguous_index in [0, 1]:
            changed = copy.deepcopy(kwargs)
            listing = review(changed)['evidence'][ambiguous_index]
            listing.update(kind='exact_upc_ambiguous_ounce_listing', observedNetContent='6 oz', retailUnitLabel='6 oz',
                           supports='Synthetic exact literal code in product name; this separate supplier record describes a six-ounce bottle and case context.')
            catalog, audit = builder.build(source, **changed)
            self.assertEqual(catalog, expected)
            self.assertEqual(audit['records'][0]['sourceReview']['evidence'][ambiguous_index]['observedNetContent'], '6 oz')
        changed = copy.deepcopy(kwargs)
        for listing in review(changed)['evidence'][:2]:
            listing.update(kind='exact_upc_ambiguous_ounce_listing', observedNetContent='6 oz', retailUnitLabel='6 oz')
        with self.assertRaisesRegex(ValueError, 'at least one explicit-fluid'): builder.build(source, **changed)
        # An explicit kind cannot turn a bare-ounce fact into fluid evidence.
        changed = copy.deepcopy(kwargs)
        review(changed)['evidence'][0].update(observedNetContent='6 oz', retailUnitLabel='6 oz')
        with self.assertRaises(ValueError): builder.build(source, **changed)

    def test_food_mode_keeps_all_literal_unit_photo_and_identity_guards(self):
        source, _, kwargs = food_liquid_fixture()
        mutations = [
            lambda k: review(k).update(expectedUpc='00012345678905'),
            lambda k: review(k).update(expectedSourceRow=2),
            lambda k: review(k).update(expectedSourceRecordSha256='0' * 64),
            lambda k: review(k).update(expectedNameSha256='0' * 64),
            lambda k: review(k).update(expectedProductId='p-' + '0' * 16),
            lambda k: review(k).update(expectedResultingGroupSkus=['1', '2']),
            lambda k: review(k).update(verifiedLabel='7 fl oz'),
            lambda k: review(k)['evidence'][0].update(retailUnitsPerPackage=24),
            lambda k: review(k)['evidence'][0].update(retailUnitsPerPackage=True),
            lambda k: review(k)['evidence'][0].update(quantityBasis='case_content'),
            lambda k: review(k)['evidence'][0].update(container='case'),
            lambda k: review(k)['evidence'][0].update(unitCodeSourceField='aapiData.upc'),
            lambda k: review(k)['evidence'][1].update(url=review(k)['evidence'][0]['url']),
            lambda k: k['image_manifest']['images'][0].update(alt='Changed after manual inspection'),
            lambda k: k['image_manifest']['images'][0]['verification'].update(professional=False),
            lambda k: k['private_direct_image_evidence_manifest']['entries'][0].update(sourceImageUrl='https://other.test/photo.jpg'),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(kwargs); mutation(changed)
            with self.subTest(index=index), self.assertRaises(ValueError): builder.build(source, **changed)

    def test_food_mode_rejects_raw_mass_counts_pack_and_multiple_measurements(self):
        for name in ['Example Sauce NET WT6OZ', 'Example Sauce 6oz weight', 'Example Sauce 6oz w/w',
                     'Example Sauce 6fl oz', 'Example Sauce 6oz 2oz', 'Example Sauce 6oz 2x12',
                     'Example Sauce 6oz CS24', 'Example Sauce 6oz 2bottles', 'Example Sauce 6oz dry',
                     'Example Sauce 6oz multipack']:
            source = export({'ItemName': name, 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905',
                             'ItemPrice': '', 'TotalQty_Multi': ''})
            source, _, kwargs = food_liquid_fixture(source)
            with self.subTest(name=name), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source = export({'ItemName': 'Example Sauce 6OZ', 'PackName': 'single', 'DEPNAME': 'Snacks',
                         'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
        source, _, kwargs = food_liquid_fixture(source)
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_food_mode_rejects_category_overrides_report_and_identity_conflicts(self):
        source, baseline, kwargs = food_liquid_fixture()
        changed = copy.deepcopy(kwargs)
        changed['category_reviews_manifest'] = {'schemaVersion': 1, 'reviews': [category_review(source, 'drinks')]}
        with self.assertRaisesRegex(ValueError, 'bare original ounce hint'): builder.build(source, **changed)
        _, audit = builder.build(source)
        validated = builder.validated_source_reviews(kwargs['source_reviews_manifest'])
        for mode in ['report', 'identity', 'nonproduct', 'category_override', 'wrong_review_category']:
            records = {r['sku']: copy.deepcopy(r) for r in audit['records']}
            scoped_reviews = copy.deepcopy(validated)
            identities = {}
            if mode == 'report': records['1']['sourceOrigin'] = 'report'
            if mode == 'nonproduct': records['1']['disposition'] = 'excluded_nonproduct'
            if mode == 'identity': identities['1'] = {'action': 'retain_flagged'}
            if mode == 'category_override': records['1']['categoryReview'] = {'category': 'food'}
            if mode == 'wrong_review_category': scoped_reviews['1']['expectedCategory'] = 'essentials'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                builder.apply_ambiguous_liquid_ounces(copy.deepcopy(baseline['products']), records, scoped_reviews,
                    kwargs['image_manifest'], kwargs['private_direct_image_evidence_manifest'], {'1'}, identities)
        # A reviewed category must not disguise the original essentials record.
        source = export({'ItemName': 'Example Sauce 6OZ', 'DEPNAME': 'MISC', 'MAINUPC': '012345678905',
                         'ItemPrice': '', 'TotalQty_Multi': ''})
        source, _, changed = food_liquid_fixture(source)
        review(changed)['expectedCategory'] = 'food'
        changed['category_reviews_manifest'] = {'schemaVersion': 1, 'reviews': [category_review(source, 'food')]}
        with self.assertRaisesRegex(ValueError, 'bare original ounce hint'): builder.build(source, **changed)

    def test_food_final_title_and_original_name_verification_precede_unit_change(self):
        source, _, _ = food_liquid_fixture()
        titles = public_name_review(source, 'Example Original Hot Pepper Sauce')
        source, _, kwargs = food_liquid_fixture(source, titles=titles)
        catalog, audit = builder.build(source, **kwargs)
        self.assertEqual(catalog['products'][0]['name'], 'Example Original Hot Pepper Sauce')
        self.assertEqual(audit['records'][0]['publicNameCorrection']['expectedLabel'], '6 oz')
        changed = copy.deepcopy(kwargs)
        changed['public_name_reviews_manifest']['reviews'][0]['expectedLabel'] = '6 fl oz'
        with self.assertRaises(ValueError): builder.build(source, **changed)
        correction = manufacturer_correction(source, 'Example Original Hot Sauce 6OZ', 'Example Original Hot Sauce', '6 oz')
        verification = {key: correction[key] for key in ('sku', 'expectedUpc', 'expectedNameSha256',
                        'expectedLabel', 'verifiedOn', 'evidence', 'manufacturerRecord')}
        verification.update(expectedPublicName='Example Original Hot Sauce', verificationMethod='manufacturer_exact_upc_record',
                            verification={'brand': True, 'expression': True, 'upc': True, 'visual': False})
        source, _, kwargs = food_liquid_fixture(source, names={'schemaVersion': 1, 'nameVerifications': [verification]})
        catalog, audit = builder.build(source, **kwargs)
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '6 fl oz')
        self.assertEqual(audit['records'][0]['nameVerification']['expectedLabel'], '6 oz')
        kwargs['name_manifest']['nameVerifications'][0]['expectedLabel'] = '6 fl oz'
        with self.assertRaisesRegex(ValueError, 'Name verification does not match'): builder.build(source, **kwargs)

    def test_food_method_preserves_private_evidence_projection(self):
        source, _, kwargs = food_liquid_fixture()
        expected, _ = builder.build(source, **kwargs)
        evidence = review(kwargs)['evidence']; digest = builder.review_evidence_sha256(evidence)
        review(kwargs)['evidence'] = {'privateEvidenceSha256': digest}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND,
                                        'records': {digest: evidence}}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                actual, _ = builder.build(source, **kwargs)
        self.assertEqual(actual, expected)
        self.assertNotIn('https:', json.dumps(kwargs['source_reviews_manifest']))
        self.assertNotIn('inspectionReceipt', json.dumps(actual))

    def test_positive_changes_only_label_and_image_and_preserves_source(self):
        source, baseline, kwargs = liquid_fixture()
        original = copy.deepcopy(kwargs)
        catalog, audit = builder.build(source, **kwargs)
        expected = copy.deepcopy(baseline)
        expected['products'][0]['variants'][0].update(
            label='12.5 fl oz', image=catalog['products'][0]['variants'][0]['image'])
        self.assertEqual(catalog, expected)
        self.assertEqual(kwargs, original)
        self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])
        self.assertEqual(audit['records'][0]['source']['ItemName'], 'Example Cleaner 12.5oz')
        self.assertEqual(audit['records'][0]['ambiguousLiquidOunceResolution']['previousPublicIdentity'],
                         {'name': 'Example Cleaner', 'label': '12.5 oz'})

    def test_every_original_and_current_guard_rejects_tampering(self):
        source, _, kwargs = liquid_fixture()
        changes = {'expectedUpc': '012345678912', 'expectedSourceRow': 2,
                   'expectedNameSha256': '0' * 64, 'expectedSourceRecordSha256': '0' * 64,
                   'expectedPublicName': 'Other Cleaner', 'expectedLabel': '24 oz',
                   'expectedCategory': 'food', 'expectedDepartment': 'Tobacco',
                   'expectedProductId': 'p-' + '0' * 16, 'expectedResultingGroupSkus': ['1', '2'],
                   'verifiedLabel': '24 fl oz'}
        for key, value in changes.items():
            changed = copy.deepcopy(kwargs)
            review(changed)[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, **changed)
        rows = list(csv.reader(io.StringIO(source.decode())))
        rows[1][6] = '8'
        stream = io.StringIO(); csv.writer(stream).writerows(rows)
        with self.assertRaisesRegex(ValueError, 'Source metadata review identity mismatch'):
            builder.build(stream.getvalue().encode(), **kwargs)

    def test_two_ambiguous_or_mixed_listings_preserve_their_actual_ounce_facts(self):
        source, _, kwargs = liquid_fixture()
        expected, _ = builder.build(source, **kwargs)
        for indices in [(0, 1), (0,), (1,)]:
            changed = copy.deepcopy(kwargs)
            for index in indices:
                listing = review(changed)['evidence'][index]
                listing.update(kind='exact_upc_ambiguous_ounce_listing',
                               observedNetContent='12.5 oz', retailUnitLabel='12.5 oz')
            original = copy.deepcopy(changed)
            with self.subTest(indices=indices):
                actual, audit = builder.build(source, **changed)
                self.assertEqual(actual, expected)
                self.assertEqual(changed, original)
                for index in indices:
                    self.assertEqual(audit['records'][0]['sourceReview']['evidence'][index]['observedNetContent'], '12.5 oz')
                self.assertEqual(review(changed)['evidence'][2]['inspectionReceipt']['visibleNetContent'], '12.5 fl oz')

    def test_listing_kinds_cannot_relabel_ambiguous_facts_as_explicit_fluid_units(self):
        source, _, kwargs = liquid_fixture()
        for kind, label in [('exact_upc_liquid_unit_listing', '12.5 oz'),
                            ('exact_upc_ambiguous_ounce_listing', '12.5 fl oz'),
                            ('exact_upc_ambiguous_ounce_listing', '12 oz')]:
            changed = copy.deepcopy(kwargs)
            review(changed)['evidence'][0].update(kind=kind, observedNetContent=label, retailUnitLabel=label)
            with self.subTest(kind=kind, label=label), self.assertRaises(ValueError):
                builder.build(source, **changed)
        changed = copy.deepcopy(kwargs); review(changed)['evidence'][0]['kind'] = []
        with self.assertRaises(ValueError): builder.build(source, **changed)

    def test_literal_codes_checksums_and_numeric_quantities_are_not_normalized(self):
        source, _, kwargs = liquid_fixture()
        for code in ['00012345678905', '10012345678902', '012345678906', 12345678905, None]:
            changed = copy.deepcopy(kwargs)
            review(changed)['evidence'][0]['observedUpc'] = code
            with self.subTest(code=code), self.assertRaises(ValueError):
                builder.build(source, **changed)
        for label in ['12.50 fl oz', '12.5 oz', '12 fl oz', '355 mL', '0 fl oz']:
            changed = copy.deepcopy(kwargs); review(changed)['verifiedLabel'] = label
            with self.subTest(label=label), self.assertRaises(ValueError):
                builder.build(source, **changed)
        changed = copy.deepcopy(kwargs); review(changed)['expectedUpc'] = '012345678906'
        with self.assertRaises(ValueError):
            builder.build(source, **changed)

    def test_explicit_fluid_weight_other_units_and_pack_metadata_reject_even_with_fresh_guards(self):
        bad_names = ['Example Cleaner 12.5 fl oz', 'Example Cleaner 12.5 ounces',
                     'Example Cleaner NET WT 12.5oz', 'Example Cleaner NETWT12.5oz',
                     'Example Cleaner weight12.5oz', 'Example Cleaner 12.5oz 2L',
                     'Example Cleaner 12.5oz 12.5oz', 'Example Cleaner 12.5oz 6pk',
                     'Example Cleaner 12.5oz case24', 'Example Cleaner 12.5oz 2DZ',
                     'Example Cleaner 12.5oz 2x12', 'Example Cleaner 12.5oz multipack',
                     'Example Cleaner -12.5oz', 'Example Cleaner - 12.5oz',
                     'Example Cleaner −12.5oz', 'Example Cleaner fl12.5oz',
                     'Example Cleaner 12.5oz 2btl', 'Example Cleaner 12.5oz 2bottles',
                     'Example Cleaner 12.5oz bottles', 'Example Cleaner 12.5oz dry']
        for name in bad_names:
            source = export({'ItemName': name, 'DEPNAME': 'MISC', 'MAINUPC': '012345678905',
                             'ItemPrice': '', 'TotalQty_Multi': ''})
            source, _, kwargs = liquid_fixture(source)
            with self.subTest(name=name), self.assertRaises(ValueError):
                builder.build(source, **kwargs)
        for pack in ['12.5oz', 'single', 'bottle', ' ', '24pk']:
            source = export({'ItemName': 'Example Cleaner 12.5oz', 'PackName': pack,
                             'DEPNAME': 'MISC', 'MAINUPC': '012345678905',
                             'ItemPrice': '', 'TotalQty_Multi': ''})
            source, _, kwargs = liquid_fixture(source)
            with self.subTest(pack=pack), self.assertRaises(ValueError):
                builder.build(source, **kwargs)

    def test_both_listings_require_explicit_unit_net_content_and_strict_single_bottle(self):
        source, _, kwargs = liquid_fixture()
        for index in [0, 1]:
            for key, value in [('observedNetContent', '12.5 oz'), ('observedNetContent', '1 serving'),
                               ('retailUnitLabel', '12 oz'), ('quantityBasis', 'case_content'),
                               ('retailUnitsPerPackage', 24), ('retailUnitsPerPackage', True),
                               ('retailUnitsPerPackage', 1.0), ('container', 'case'),
                               ('sourceSha256', 'x'), ('retrievedOn', None),
                               ('kind', 'exact_unit_gtin14_listing')]:
                changed = copy.deepcopy(kwargs); review(changed)['evidence'][index][key] = value
                with self.subTest(index=index, key=key, value=value), self.assertRaises(ValueError):
                    builder.build(source, **changed)

    def test_case_and_package_shorthand_reject_with_fresh_complete_guards(self):
        for hint in ['CS24', 'CS 24', 'cs qty:24', 'CS:24', 'CS=24', 'CS #24', 'CS-24',
                     'CS [24]', 'CS(24)', '[24cs]', '24CS', '24 CS', '24/CS',
                     '2pkg', '2pkgs', '2 pkg', '2-pkgs', '2:pkg', '2=pkgs', '[2pkg]',
                     'pkg2', 'pkgs 2', 'pkg:2', 'pkgs=2', 'pkg #2', 'pkg-2', 'pkg[2]']:
            source = export({'ItemName': 'Example Cleaner 12.5oz ' + hint, 'DEPNAME': 'MISC',
                             'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
            source, _, kwargs = liquid_fixture(source)
            with self.subTest(hint=hint), self.assertRaisesRegex(ValueError, 'bare original ounce hint'):
                builder.build(source, **kwargs)

    def test_multiplicity_markers_reject_only_in_context_of_package_counts(self):
        for hint in ['x24', '×24', 'x 24', '× 24', 'x:24', 'x#24', '[x24]', '24x',
                     '24×', '24 x 12.5oz', '24x12.5oz', '24×12.5oz']:
            name = ('Example Cleaner ' + hint if 'oz' in hint else 'Example Cleaner 12.5oz ' + hint)
            source = export({'ItemName': name, 'DEPNAME': 'MISC', 'MAINUPC': '012345678905',
                             'ItemPrice': '', 'TotalQty_Multi': ''})
            source, _, kwargs = liquid_fixture(source)
            with self.subTest(hint=hint), self.assertRaises(ValueError):
                builder.build(source, **kwargs)
        source = export({'ItemName': 'Example Cleaner 12.5oz×24', 'DEPNAME': 'MISC',
                         'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
        source, _, kwargs = liquid_fixture(source)
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_weight_ratio_and_joined_unit_conflicts_reject_with_fresh_complete_guards(self):
        for suffix in [' w/w', ' W / W', ' wt/wt', ' weight/weight',
                       ' w / weight', 'weight', 'fl', '.weight', '.fl', '_fl']:
            source = export({'ItemName': 'Example Cleaner 12.5oz' + suffix, 'DEPNAME': 'MISC',
                             'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
            source, _, kwargs = liquid_fixture(source)
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, 'bare original ounce hint'):
                builder.build(source, **kwargs)

    def test_conflict_abbreviations_do_not_match_incidental_brand_letters(self):
        for brand in ['CSPro', 'PKGWorks', 'WWW', 'Weightless', 'Floral', 'X24Cleaner', '409', 'CS']:
            source = export({'ItemName': brand + ' Cleaner 12.5oz', 'DEPNAME': 'MISC',
                             'MAINUPC': '012345678905', 'ItemPrice': '', 'TotalQty_Multi': ''})
            source, baseline, kwargs = liquid_fixture(source)
            with self.subTest(brand=brand):
                catalog, audit = builder.build(source, **kwargs)
                self.assertEqual(catalog['products'][0]['name'], baseline['products'][0]['name'])
                self.assertEqual(catalog['products'][0]['id'], baseline['products'][0]['id'])
                self.assertEqual(catalog['products'][0]['variants'][0]['label'], '12.5 fl oz')
                self.assertEqual(audit['records'][0]['source']['ItemName'], brand + ' Cleaner 12.5oz')

    def test_two_sources_cannot_be_duplicate_aliases_siblings_or_copied_responses(self):
        source, _, kwargs = liquid_fixture()
        for url in ['https://retailer.example.test/other', 'https://www.retailer.example.test./unit',
                    'https://other.example.test/unit', 'https://shop.retailer.example.test/unit',
                    'https://retailer.example.test。/unit', 'https://retailer%2eexample.test/unit',
                    'http://independent.other.test/unit', 'https://user:secret@other.test/unit',
                    'https://other.test:443/unit']:
            changed = copy.deepcopy(kwargs); review(changed)['evidence'][1]['url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        review(changed)['evidence'][1]['sourceSha256'] = review(changed)['evidence'][0]['sourceSha256']
        with self.assertRaisesRegex(ValueError, 'independent unit sources'):
            builder.build(source, **changed)

    def test_readable_photo_receipt_content_hash_and_original_are_bound(self):
        source, _, kwargs = liquid_fixture()
        for key, value in [('sku', '2'), ('upc', '012345678912'), ('publicName', 'Other Cleaner'),
                           ('label', '24 fl oz'), ('src', 'images/products/other.jpg'),
                           ('sha256', '0' * 64), ('originalSha256', '0' * 64), ('publicImageSha256', '0' * 64),
                           ('sourceImageUrl', 'https://other.test/photo.jpg'),
                           ('visibleNetContent', 'not readable'), ('container', 'can')]:
            changed = copy.deepcopy(kwargs)
            photo = review(changed)['evidence'][2]; photo['inspectionReceipt'][key] = value
            # Rehashing a tampered receipt must not bypass the field bindings.
            photo['inspectionReceiptSha256'] = builder.review_evidence_sha256(photo['inspectionReceipt'])
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, **changed)
        for key, value in [('inspectionReceiptSha256', '0' * 64), ('sha256', '0' * 64),
                           ('inspectionReceipt', None)]:
            changed = copy.deepcopy(kwargs); review(changed)['evidence'][2][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.build(source, **changed)
        changed = copy.deepcopy(kwargs); review(changed)['evidence'].pop()
        with self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs); photo = review(changed)['evidence'][2]
        photo['inspectionReceipt']['verification']['visual'] = False
        photo['inspectionReceiptSha256'] = builder.review_evidence_sha256(photo['inspectionReceipt'])
        with self.assertRaises(ValueError): builder.build(source, **changed)

    def test_professional_per_sku_manifest_provenance_and_actual_bytes_still_validate(self):
        source, _, kwargs = liquid_fixture()
        for key, value in [('sku', '2'), ('expectedLabel', '12.5 oz'), ('expectedName', 'Other Cleaner'),
                           ('alt', 'Changed uninspected caption'),
                           ('sha256', '0' * 64)]:
            changed = copy.deepcopy(kwargs); changed['image_manifest']['images'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **changed)
        for key in ['image_manifest', 'private_direct_image_evidence_manifest']:
            changed = copy.deepcopy(kwargs); changed[key] = None
            with self.subTest(missing=key), self.assertRaises(ValueError): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        changed['private_direct_image_evidence_manifest']['entries'][0]['sourceImageUrl'] = 'https://other.test/photo.jpg'
        with self.assertRaisesRegex(ValueError, 'private provenance'): builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        changed['image_manifest']['images'][0]['verification']['professional'] = False
        with self.assertRaises(ValueError): builder.build(source, **changed)
        # Forge every declared hash and recompute the receipt: actual local
        # bytes must still reject this apparently self-consistent tampering.
        changed = copy.deepcopy(kwargs); r = review(changed); photo = r['evidence'][2]
        r['expectedImageSha256'] = photo['sha256'] = '0' * 64
        photo['inspectionReceipt']['sha256'] = photo['inspectionReceipt']['originalSha256'] = '0' * 64
        changed['image_manifest']['images'][0]['sha256'] = '0' * 64
        changed['private_direct_image_evidence_manifest']['entries'][0]['sha256'] = '0' * 64
        photo['inspectionReceipt']['publicImageSha256'] = builder.review_evidence_sha256(changed['image_manifest']['images'][0])
        photo['inspectionReceiptSha256'] = builder.review_evidence_sha256(photo['inspectionReceipt'])
        with self.assertRaisesRegex(ValueError, 'hash does not match'): builder.build(source, **changed)

    def test_singleton_scope_and_existing_isolation_do_not_change_groups(self):
        source = export({'ItemName': 'Example Cleaner 12.5oz', 'DEPNAME': 'MISC', 'MAINUPC': '012345678905'},
                        {'ItemName': 'Example Cleaner', 'DEPNAME': 'MISC', 'MAINUPC': '012345678912'})
        source, baseline, kwargs = liquid_fixture(source)
        catalog, _ = builder.build(source, **kwargs)
        self.assertEqual([(p['id'], p['name'], [v['sku'] for v in p['variants']]) for p in catalog['products']],
                         [(p['id'], p['name'], [v['sku'] for v in p['variants']]) for p in baseline['products']])
        source = export({'ItemName': 'Example Cleaner 12.5oz', 'DEPNAME': 'MISC', 'MAINUPC': '012345678905'},
                        {'ItemName': 'Example Cleaner 24oz', 'DEPNAME': 'MISC', 'MAINUPC': '012345678912'})
        source, _, kwargs = liquid_fixture(source)
        with self.assertRaisesRegex(ValueError, 'unchanged singleton'): builder.build(source, **kwargs)

    def test_original_manufacturer_and_final_name_guards_run_before_unit_change(self):
        source = export({'ItemName': 'Example Cleener 12.5oz', 'DEPNAME': 'MISC', 'MAINUPC': '012345678905'})
        correction = manufacturer_correction(source, 'Example Cleaner 12.5oz', 'Example Cleaner', '12.5 oz')
        verification = {key: correction[key] for key in ('sku', 'expectedUpc', 'expectedNameSha256',
                        'expectedLabel', 'verifiedOn', 'evidence', 'manufacturerRecord')}
        verification.update(expectedPublicName='Example Cleaner', verificationMethod='manufacturer_exact_upc_record',
                            verification={'brand': True, 'expression': True, 'upc': True, 'visual': False})
        names = {'schemaVersion': 1, 'corrections': [correction], 'nameVerifications': [verification]}
        source, _, kwargs = liquid_fixture(source, names)
        catalog, audit = builder.build(source, **kwargs)
        self.assertEqual(catalog['products'][0]['variants'][0]['label'], '12.5 fl oz')
        self.assertEqual(audit['records'][0]['nameCorrection']['expectedLabel'], '12.5 oz')
        self.assertEqual(audit['records'][0]['nameVerification']['expectedLabel'], '12.5 oz')
        for collection, message in [('corrections', 'Manufacturer name correction changed a guarded'),
                                    ('nameVerifications', 'Name verification does not match')]:
            changed = copy.deepcopy(kwargs); changed['name_manifest'][collection][0]['expectedLabel'] = '12.5 fl oz'
            with self.subTest(collection=collection), self.assertRaisesRegex(ValueError, message):
                builder.build(source, **changed)

    def test_prior_guarded_title_runs_on_old_label_then_final_name_id_and_photo_bind(self):
        source = export({'ItemName': 'Example Cleaner 12.5oz', 'DEPNAME': 'MISC', 'MAINUPC': '012345678905'},
                        {'ItemName': 'Unrelated Soap 5oz', 'DEPNAME': 'MISC', 'MAINUPC': '012345678912'})
        raw_baseline, _ = builder.build(source)
        titles = public_name_review(source, 'Example Lavender Multi-Purpose Cleaner')
        source, title_baseline, kwargs = liquid_fixture(source, titles=titles)
        catalog, audit = builder.build(source, **kwargs)
        old_target = next(p for p in raw_baseline['products'] if p['variants'][0]['sku'] == '1')
        target = next(p for p in catalog['products'] if p['variants'][0]['sku'] == '1')
        self.assertEqual(target['name'], 'Example Lavender Multi-Purpose Cleaner')
        self.assertNotEqual(target['id'], old_target['id'])
        self.assertEqual(target['id'], review(kwargs)['expectedProductId'])
        self.assertEqual(target['variants'][0]['label'], '12.5 fl oz')
        expected = copy.deepcopy(title_baseline)
        next(v for p in expected['products'] for v in p['variants'] if v['sku'] == '1').update(
            label='12.5 fl oz', image=target['variants'][0]['image'])
        self.assertEqual(catalog, expected)
        self.assertEqual(next(p for p in catalog['products'] if p['variants'][0]['sku'] == '2'),
                         next(p for p in raw_baseline['products'] if p['variants'][0]['sku'] == '2'))
        self.assertEqual(audit['records'][0]['rawFields'], list(csv.reader(io.StringIO(source.decode())))[1])
        self.assertEqual(audit['records'][0]['publicNameCorrection']['expectedLabel'], '12.5 oz')
        self.assertEqual([v['sku'] for v in target['variants']], ['1'])
        changed = copy.deepcopy(kwargs); review(changed)['expectedProductId'] = old_target['id']
        with self.assertRaisesRegex(ValueError, 'current source/public identity mismatch'):
            builder.build(source, **changed)
        changed = copy.deepcopy(kwargs)
        changed['public_name_reviews_manifest']['reviews'][0]['expectedLabel'] = '12.5 fl oz'
        with self.assertRaises(ValueError): builder.build(source, **changed)

    def test_report_nonproduct_identity_and_nonessentials_cannot_be_promoted(self):
        source, baseline, kwargs = liquid_fixture()
        _, audit = builder.build(source)
        validated = builder.validated_source_reviews(kwargs['source_reviews_manifest'])
        for mutation in ['report', 'nonproduct', 'identity']:
            records = {r['sku']: copy.deepcopy(r) for r in audit['records']}
            identities = {}
            if mutation == 'report': records['1']['sourceOrigin'] = 'report'
            if mutation == 'nonproduct': records['1']['disposition'] = 'excluded_nonproduct'
            if mutation == 'identity': identities['1'] = {'action': 'retain_flagged'}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                builder.apply_ambiguous_liquid_ounces(copy.deepcopy(baseline['products']), records, validated,
                    kwargs['image_manifest'], kwargs['private_direct_image_evidence_manifest'], {'1'}, identities)
        source = export({'ItemName': 'Example Cleaner 12.5oz', 'DEPNAME': 'Snacks', 'MAINUPC': '012345678905'})
        source, _, kwargs = liquid_fixture(source)
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_private_evidence_projection_and_unknown_fields_fail_closed(self):
        source, _, kwargs = liquid_fixture()
        expected, _ = builder.build(source, **kwargs)
        evidence = review(kwargs)['evidence']; digest = builder.review_evidence_sha256(evidence)
        review(kwargs)['evidence'] = {'privateEvidenceSha256': digest}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND,
                                        'records': {digest: evidence}}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}):
                actual, _ = builder.build(source, **kwargs)
                self.assertEqual(actual, expected)
                self.assertNotIn('https:', json.dumps(actual))
                self.assertNotIn('inspectionReceipt', json.dumps(kwargs['source_reviews_manifest']))
            path.write_text(json.dumps({'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND,
                                        'records': {digest: evidence[:-1]}}))
            with patch.dict(os.environ, {'QUICKSTOP_PRIVATE_REVIEW_EVIDENCE': str(path)}), self.assertRaises(ValueError):
                builder.build(source, **kwargs)
        source, _, kwargs = liquid_fixture()
        for target in [review(kwargs), review(kwargs)['evidence'][0], review(kwargs)['evidence'][2]['inspectionReceipt']]:
            target['unexpected'] = 'unapproved'
            with self.assertRaises(ValueError): builder.build(source, **kwargs)
            target.pop('unexpected')


if __name__ == '__main__':
    unittest.main()
