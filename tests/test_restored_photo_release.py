"""Synthetic fixtures: exact restored photos never authorize source/group repairs."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_catalog import builder, export, restoration_review


def approve(proof):
    proof['approval'] = dict(verdict=builder.RESTORED_PHOTO_RELEASE_VERDICT,
        reviewedReleaseSha256=builder.review_evidence_sha256({k: v for k, v in proof.items() if k != 'approval'}),
        reviewer='Synthetic independent test reviewer', reviewedOn='2026-11-03')
    return proof


class RestoredPhotoReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / 'public'
        (self.root / 'images/products').mkdir(parents=True)
        self.root_patch = patch.object(builder, 'ROOT', self.root)
        self.root_patch.start(); self.addCleanup(self.root_patch.stop)
        self.ledger_path = self.folder / 'ledger.private.json'
        self.environment = patch.dict(os.environ, QUICKSTOP_PRIVATE_REVIEW_EVIDENCE=str(self.ledger_path))
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.source = export({'MAINUPC': '070655910115', 'ItemName': 'Example Mixer 1.75Ltr1.75 Ltr', 'DEPNAME': 'N/A Mix'})
        self.source_reviews = restoration_review(self.source, 'Example Mixer 1.75L', uncertain=False)
        raw_name_hash = self.source_reviews['reviews'][0]['expectedNameSha256']
        self.names = {'schemaVersion': 1, 'corrections': [dict(sku='1', expectedUpc='070655910115',
            expectedNameSha256=raw_name_hash, replaceTextFrom='Example Mixer', replaceTextTo='Example Brand Mixer',
            scope='product_name_spelling', evidence=[dict(kind='manufacturer', url='https://example.test/product')])]}
        self.before, self.before_audit = builder.build(self.source, name_manifest=self.names, source_reviews_manifest=self.source_reviews)
        self.product = self.before['products'][0]
        self.record = self.before_audit['records'][0]
        self.asset = self.root / 'images/products/sku-1.png'
        # Fixed synthetic 2x2 white RGB PNG: public tests need only the standard library.
        self.asset.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAE0lEQVR4nGP8//8/AwMDEwMYAAAkBgMBXaJOiAAAAABJRU5ErkJggg=='))
        digest = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        self.image = dict(sku='1', src='images/products/sku-1.png', alt='Synthetic test package',
            expectedUpc='070655910115', expectedName=self.product['name'], expectedLabel='1.75 L',
            sha256=digest, verifiedOn='2026-11-03', usageBasis='user_authorized_direct_reference',
            attributionRequired=False, verification={key: True for key in ('brand', 'expression', 'size', 'packaging', 'visual', 'professional')})
        self.private_entry = dict(self.image, sourcePage='https://retailer.example/mixer', sourceImageUrl='https://retailer.example/mixer.png')
        self.private_images = dict(schemaVersion=1, kind=builder.PRIVATE_DIRECT_IMAGE_EVIDENCE_KIND, entries=[self.private_entry])
        source_fields = dict(url='https://records.example/sheet', sourceRole='Third-party hosted branded sell-sheet',
            recordSha256='a' * 64, recordPointer='page 1 exact product row', sourceName='Example Brand Mixer',
            sourceUnit='1.75L', matchedPublicName=self.product['name'], matchedLabel='1.75 L')
        self.proof = dict(kind=builder.RESTORED_PHOTO_RELEASE_METHOD, sku='1', expectedUpc='070655910115',
            expectedSourceRow=1, expectedRawFieldsSha256=builder.review_evidence_sha256(self.record['rawFields']),
            expectedNameSha256=raw_name_hash, expectedSourceReviewSha256=builder.review_evidence_sha256(self.record['sourceReview']),
            expectedNameCorrectionSha256=builder.review_evidence_sha256(self.record['nameCorrection']),
            expectedPublicName=self.product['name'], expectedLabel='1.75 L', expectedCategory='drinks', expectedDepartment='N/A Mix',
            expectedProductId=self.product['id'], expectedGroupSkus=['1'], expectedImageSrc=self.image['src'], expectedImageSha256=digest,
            expectedPrivateImageEvidenceSha256=builder.review_evidence_sha256(self.private_entry),
            codeUnitEvidence=dict(source_fields, printedCode='0 70655 91011 5', codeNormalization='ascii_spaces_only', literalConsumerCode='070655910115'),
            selectedImageEvidence=dict(source_fields, url=self.private_entry['sourcePage'], sourceRole='Own selected retailer product',
                recordPointer='selected Product object', sourceImageUrl=self.private_entry['sourceImageUrl'], publishedConsumerCode=None),
            nativeQa=dict(photoQaSha256='b' * 64, imageSha256=digest, format='PNG', width=2, height=2,
                **{key: True for key in ('brand', 'expression', 'size', 'packaging', 'professional',
                    'actualFullOriginalViewedIndependently', 'strictDecodePassed', 'nativeBytesUnchanged')}))
        approve(self.proof)

    def manifest(self, proofs=None):
        proofs = [self.proof] if proofs is None else proofs
        records, releases = {}, []
        for proof in proofs:
            evidence = [copy.deepcopy(proof)]; digest = builder.review_evidence_sha256(evidence)
            records[digest] = evidence; releases.append({'sku': proof['sku'], 'evidence': {'privateEvidenceSha256': digest}})
        self.ledger_path.write_text(json.dumps(dict(schemaVersion=1, kind=builder.PRIVATE_REVIEW_EVIDENCE_KIND, records=records)))
        return dict(schemaVersion=1, images=[copy.deepcopy(self.image)], restoredPhotoReleases=releases)

    def run_build(self, manifest=None, **overrides):
        args = dict(image_manifest=self.manifest() if manifest is None else manifest,
            name_manifest=self.names, source_reviews_manifest=self.source_reviews,
            private_direct_image_evidence_manifest=self.private_images)
        args.update(overrides)
        return builder.build(self.source, **args)

    def test_only_exact_image_is_added_and_restoration_and_isolated_id_remain(self):
        after, audit = self.run_build()
        restored = copy.deepcopy(after)
        self.assertEqual(restored['products'][0]['variants'][0]['image']['src'], self.image['src'])
        restored['products'][0]['variants'][0]['image'] = None
        self.assertEqual(restored, self.before)
        proof = audit['records'][0].pop('restoredPhotoRelease')
        self.assertEqual(proof, self.proof)
        self.assertEqual(audit['records'], self.before_audit['records'])
        self.assertEqual(audit['sourceDispositions'], self.before_audit['sourceDispositions'])
        self.assertEqual(audit['verifiedImages'], 1)

    def test_absent_optional_list_is_noop_and_does_not_unblock_photo(self):
        products = copy.deepcopy(self.before['products'])
        records = {'1': copy.deepcopy(self.record)}
        blocks = {'1'}
        builder.apply_restored_photo_releases(products, records,
            dict(schemaVersion=1, images=[]), None, blocks)
        self.assertEqual(products, self.before['products'])
        self.assertEqual(records, {'1': self.record})
        self.assertEqual(blocks, {'1'})
        self.assertNotIn('restoredPhotoRelease', self.record)
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            self.run_build(dict(schemaVersion=1, images=[self.image]))

    def test_empty_release_list_does_not_unblock_normal_photo(self):
        with self.assertRaisesRegex(ValueError, 'Image blocked'):
            self.run_build(self.manifest([]))

    def test_public_manifest_contains_only_sku_and_private_hash(self):
        manifest = self.manifest()
        self.assertEqual(set(manifest['restoredPhotoReleases'][0]), {'sku', 'evidence'})
        public = json.dumps(manifest)
        for value in ('https://', str(self.folder), 'sourceRole', 'printedCode', 'approval'):
            self.assertNotIn(value, public)
        manifest['restoredPhotoReleases'][0]['evidence'] = [self.proof]
        with self.assertRaisesRegex(ValueError, 'private hash projections'):
            self.run_build(manifest)

    def test_missing_tampered_or_nested_private_ledger_fails(self):
        manifest = self.manifest()
        for change in ('missing', 'tamper', 'nested'):
            self.manifest()
            if change == 'missing': self.ledger_path.unlink()
            else:
                ledger = json.loads(self.ledger_path.read_text()); record = next(iter(ledger['records'].values()))
                record[0]['expectedLabel'] = '1 L'
                if change == 'nested': record[0]['extra'] = {'privateEvidenceSha256': 'a' * 64}
                self.ledger_path.write_text(json.dumps(ledger))
            with self.subTest(change=change), self.assertRaises(ValueError): self.run_build(manifest)

    def test_unapproved_or_changed_scope_is_rejected(self):
        for key, value in [('verdict', 'PENDING'), ('reviewedReleaseSha256', 'c' * 64), ('reviewer', ''), ('reviewedOn', '2026-02-31')]:
            proof = copy.deepcopy(self.proof); proof['approval'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.run_build(self.manifest([proof]))
        proof = copy.deepcopy(self.proof); proof['expectedLabel'] = '1 L'
        with self.assertRaises(ValueError): self.run_build(self.manifest([proof]))

    def test_all_raw_current_review_group_and_image_guards_fail_closed(self):
        changes = {'expectedUpc': '070655910054', 'expectedSourceRow': 2, 'expectedRawFieldsSha256': 'a' * 64,
            'expectedNameSha256': 'a' * 64, 'expectedSourceReviewSha256': 'a' * 64,
            'expectedNameCorrectionSha256': 'a' * 64, 'expectedPublicName': 'Different Mixer',
            'expectedLabel': '1 L', 'expectedCategory': 'food', 'expectedDepartment': 'Juice',
            'expectedProductId': 'p-' + 'a' * 16, 'expectedGroupSkus': ['1', '2'],
            'expectedImageSrc': 'images/products/sku-1.webp', 'expectedImageSha256': 'a' * 64,
            'expectedPrivateImageEvidenceSha256': 'a' * 64}
        for key, value in changes.items():
            proof = approve(dict(copy.deepcopy(self.proof), **{key: value}))
            with self.subTest(key=key), self.assertRaises(ValueError): self.run_build(self.manifest([proof]))

    def test_unknown_fields_wrong_types_or_duplicate_release_are_rejected(self):
        changes = [('extra', True), ('expectedSourceRow', True), ('expectedGroupSkus', '1'), ('expectedUpc', 70655910115)]
        for key, value in changes:
            proof = approve(dict(copy.deepcopy(self.proof), **{key: value}))
            with self.subTest(key=key), self.assertRaises(ValueError): self.run_build(self.manifest([proof]))
        with self.assertRaises(ValueError): self.run_build(self.manifest([self.proof, self.proof]))
        for value in (None, {}, '1'):
            manifest = self.manifest(); manifest['restoredPhotoReleases'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): self.run_build(manifest)

    def test_evidence_cannot_launder_other_code_unit_or_image(self):
        changes = [('codeUnitEvidence', 'printedCode', '0 70655 91005 4'),
            ('codeUnitEvidence', 'codeNormalization', 'zero_padding'), ('codeUnitEvidence', 'sourceUnit', '1L'),
            ('selectedImageEvidence', 'publishedConsumerCode', '070655910054'),
            ('selectedImageEvidence', 'sourceImageUrl', 'https://retailer.example/other.png'),
            ('selectedImageEvidence', 'url', 'https://retailer.example/other'),
            ('nativeQa', 'actualFullOriginalViewedIndependently', 1), ('nativeQa', 'professional', False),
            ('nativeQa', 'width', True), ('nativeQa', 'imageSha256', 'a' * 64)]
        for section, key, value in changes:
            proof = copy.deepcopy(self.proof); proof[section][key] = value; approve(proof)
            with self.subTest(section=section, key=key), self.assertRaises(ValueError): self.run_build(self.manifest([proof]))

    def test_ordinary_private_provenance_and_local_byte_checks_still_run(self):
        with self.assertRaises(ValueError): self.run_build(private_direct_image_evidence_manifest=None)
        self.asset.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash does not match'): self.run_build()

    def test_ordinary_exact_image_quality_checks_still_run(self):
        self.image['verification']['professional'] = False
        self.private_entry['verification']['professional'] = False
        self.proof['expectedPrivateImageEvidenceSha256'] = builder.review_evidence_sha256(self.private_entry); approve(self.proof)
        with self.assertRaisesRegex(ValueError, 'every exact-match'): self.run_build()

    def test_route_is_generic_and_does_not_depend_on_reason_text(self):
        source = export({'SKU': '777', 'MAINUPC': '070655910054',
            'ItemName': 'Other Mixer 2.5Ltr2.5 Ltr', 'DEPNAME': 'N/A Mix'})
        reviews = restoration_review(source, 'Other Mixer 2.5L', uncertain=False)
        reviews['reviews'][0]['reason'] = 'Unrelated review wording; no special release keyword.'
        before, audit = builder.build(source, source_reviews_manifest=reviews)
        product, record = before['products'][0], audit['records'][0]
        proof = copy.deepcopy(self.proof)
        proof.update(sku='777', expectedUpc='070655910054', expectedSourceRow=1,
            expectedRawFieldsSha256=builder.review_evidence_sha256(record['rawFields']),
            expectedNameSha256=hashlib.sha256(record['rawFields'][2].encode()).hexdigest(),
            expectedSourceReviewSha256=builder.review_evidence_sha256(record['sourceReview']),
            expectedNameCorrectionSha256=builder.review_evidence_sha256(None), expectedPublicName=product['name'],
            expectedLabel='2.5 L', expectedProductId=product['id'], expectedGroupSkus=['777'],
            expectedImageSrc='images/products/sku-777.png')
        for key in ('codeUnitEvidence', 'selectedImageEvidence'):
            proof[key].update(sourceName=product['name'], sourceUnit='2.5L',
                matchedPublicName=product['name'], matchedLabel='2.5 L')
        proof['codeUnitEvidence'].update(printedCode='070655910054', codeNormalization='literal', literalConsumerCode='070655910054')
        image = dict(self.image, sku='777', src=proof['expectedImageSrc'], expectedUpc=proof['expectedUpc'],
            expectedName=product['name'], expectedLabel='2.5 L')
        private = dict(image, sourcePage=self.private_entry['sourcePage'], sourceImageUrl=self.private_entry['sourceImageUrl'])
        proof['expectedPrivateImageEvidenceSha256'] = builder.review_evidence_sha256(private); approve(proof)
        (self.root / image['src']).write_bytes(self.asset.read_bytes())
        manifest = self.manifest([proof]); manifest['images'] = [image]
        after, result = builder.build(source, source_reviews_manifest=reviews, image_manifest=manifest,
            private_direct_image_evidence_manifest=dict(self.private_images, entries=[private]))
        self.assertEqual(after['products'][0]['id'], product['id'])
        self.assertEqual(result['records'][0]['sourceReview'], record['sourceReview'])
        self.assertEqual(after['products'][0]['variants'][0]['image']['src'], image['src'])

    def test_fluid_ounce_source_evidence_cannot_match_legacy_plain_ounce_label(self):
        for section in ('codeUnitEvidence', 'selectedImageEvidence'):
            proof = copy.deepcopy(self.proof)
            proof['expectedLabel'] = '16 oz'
            for key in ('codeUnitEvidence', 'selectedImageEvidence'):
                proof[key].update(sourceUnit='16 oz', matchedLabel='16 oz')
            proof[section]['sourceUnit'] = '16 fl oz'
            approve(proof)
            with self.subTest(section=section), self.assertRaisesRegex(ValueError, 'source unit'):
                builder.validated_restored_photo_release_proof(proof, '1')

    def test_fluid_ounce_restoration_is_rejected_even_with_fresh_exact_guards(self):
        for raw_name in ('Example Mixer 16 fl oz 16 oz', 'Example Mixer 16 fl oz 16 fl oz'):
            record, product = copy.deepcopy(self.record), copy.deepcopy(self.product)
            record['rawFields'][2] = record['source']['ItemName'] = raw_name
            record.pop('nameCorrection')
            review = record['sourceReview']
            review.update(sanitizedSourceName='Example Mixer 16 oz',
                expectedNameSha256=hashlib.sha256(raw_name.encode()).hexdigest(),
                expectedSourceRecordSha256=builder.review_evidence_sha256(record['rawFields']))
            product['name'] = 'Example Mixer'
            product['variants'][0]['label'] = '16 oz'
            product['id'] = 'p-' + hashlib.sha256('\0'.join((product['category'], product['department'],
                product['name'].casefold(), 'restored-source-sku', '1')).encode()).hexdigest()[:16]
            record['productId'] = product['id']
            proof = copy.deepcopy(self.proof)
            proof.update(expectedRawFieldsSha256=builder.review_evidence_sha256(record['rawFields']),
                expectedNameSha256=review['expectedNameSha256'],
                expectedSourceReviewSha256=builder.review_evidence_sha256(review),
                expectedNameCorrectionSha256=builder.review_evidence_sha256(None),
                expectedPublicName=product['name'], expectedLabel='16 oz', expectedProductId=product['id'])
            with self.subTest(raw_name=raw_name), self.assertRaisesRegex(ValueError, 'duplicated equal'):
                builder.validate_restored_photo_context(proof, record, product)

    def test_joined_fluid_ounce_is_rejected_by_local_context_and_existing_restoration_gate(self):
        # Defense in depth: this example already fails the earlier restoration gate.
        # The local context guard must still inspect the units MEASURE_RE consumed.
        for fluid_unit in ('fl oz', 'FL.OZ.', 'fl. oz.'):
            raw_name = f'Example Mixer 16 {fluid_unit}Blue16 oz'
            record, product = copy.deepcopy(self.record), copy.deepcopy(self.product)
            record['rawFields'][2] = record['source']['ItemName'] = raw_name
            record.pop('nameCorrection')
            review = record['sourceReview']
            review.update(sanitizedSourceName='Example Mixer Blue 16 oz',
                expectedNameSha256=hashlib.sha256(raw_name.encode()).hexdigest(),
                expectedSourceRecordSha256=builder.review_evidence_sha256(record['rawFields']))
            product['name'] = 'Example Mixer Blue'
            product['variants'][0]['label'] = '16 oz'
            product['id'] = 'p-' + hashlib.sha256('\0'.join((product['category'], product['department'],
                product['name'].casefold(), 'restored-source-sku', '1')).encode()).hexdigest()[:16]
            record['productId'] = product['id']
            proof = copy.deepcopy(self.proof)
            proof.update(expectedRawFieldsSha256=builder.review_evidence_sha256(record['rawFields']),
                expectedNameSha256=review['expectedNameSha256'],
                expectedSourceReviewSha256=builder.review_evidence_sha256(review),
                expectedNameCorrectionSha256=builder.review_evidence_sha256(None),
                expectedPublicName=product['name'], expectedLabel='16 oz', expectedProductId=product['id'])
            with self.subTest(fluid_unit=fluid_unit):
                with self.assertRaisesRegex(ValueError, 'Restoration is not eligible'):
                    builder.restored_description(record['source'], review)
                with self.assertRaisesRegex(ValueError, 'duplicated equal'):
                    builder.validate_restored_photo_context(proof, record, product)

    def test_context_rejects_ambiguous_restoration_new_overrides_and_nonisolated_group(self):
        for change in ('unknown', 'different_unit', 'delete_word', 'pack', 'report', 'override', 'ordinary_id', 'sibling', 'already_pictured'):
            record, product = copy.deepcopy(self.record), copy.deepcopy(self.product)
            if change == 'unknown': record['sourceReview']['forceSizeNeedsConfirmation'] = True
            elif change == 'different_unit': record['source']['ItemName'] = 'Example Mixer 1L 1.75L'
            elif change == 'delete_word': record['sourceReview']['sanitizedSourceName'] = 'Mixer 1.75L'
            elif change == 'pack': record['source']['PackName'] = '2pk'
            elif change == 'report': record['sourceOrigin'] = 'report'
            elif change == 'override': record['publicNameCorrection'] = {}
            elif change == 'ordinary_id': product['id'] = 'p-' + 'a' * 16
            elif change == 'sibling': product['variants'].append(dict(product['variants'][0], sku='2'))
            else: product['variants'][0]['image'] = {'src': 'old.png'}
            with self.subTest(change=change), self.assertRaises(ValueError): builder.validate_restored_photo_context(self.proof, record, product)

    def test_release_does_not_unblock_another_restored_sku(self):
        source = export({'MAINUPC': '070655910115', 'ItemName': 'Example Mixer 1.75Ltr1.75 Ltr', 'DEPNAME': 'N/A Mix'},
            {'MAINUPC': '070655910054', 'ItemName': 'Other Mixer 1.75Ltr1.75 Ltr', 'DEPNAME': 'N/A Mix'})
        reviews = copy.deepcopy(self.source_reviews); reviews['reviews'] += restoration_review(source, 'Other Mixer 1.75L', uncertain=False, row=2)['reviews']
        catalog, _ = builder.build(source, source_reviews_manifest=reviews, name_manifest=self.names)
        other = next(p for p in catalog['products'] if p['variants'][0]['sku'] == '2')
        manifest = self.manifest(); second = dict(copy.deepcopy(self.image), sku='2', expectedUpc='070655910054', expectedName=other['name'])
        manifest['images'].append(second); private = copy.deepcopy(self.private_images)
        private['entries'].append(dict(second, sourcePage='https://retailer.example/other', sourceImageUrl='https://retailer.example/other.png'))
        with self.assertRaisesRegex(ValueError, 'Image blocked.*2'):
            builder.build(source, image_manifest=manifest, source_reviews_manifest=reviews, name_manifest=self.names,
                private_direct_image_evidence_manifest=private)


if __name__ == '__main__':
    unittest.main()
