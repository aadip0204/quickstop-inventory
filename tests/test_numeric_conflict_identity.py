"""Synthetic numeric-conflict identity tests; no real source rows or product assets."""
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

from test_catalog import builder, export as source_export
from test_name_only_restoration import project_evidence

MAIN, OTHER = '012345678905', '012345678912'
TITLE = 'Example Vodka 80 Proof'
FRONT = b'synthetic selected-front fixture bytes'
FRONT_HASH = hashlib.sha256(FRONT).hexdigest()


def export(*records):
    defaults = dict(MAINUPC=MAIN, ItemName=OTHER, PackName='375 ml Original T',
                    DEPNAME='Vodka', ItemPrice='7.31', TotalQty_Multi='-4')
    return source_export(*(dict(defaults, **record) for record in records))


def fixture(source=None):
    source = source or export({})
    raw = list(csv.reader(io.StringIO(source.decode())))[1]
    selected, alternate = raw[1], raw[2]
    def unit(kind, code, content, url, asset, digest):
        return dict(kind=kind, url=url, supports=f'Literal UPC {code}: {TITLE}; unit record.',
                    observedUpc=code, observedName=TITLE, observedNetContent=content, observedProof='80',
                    quantityBasis='per_retail_unit', codeSourceField='product.upc', unitImageUrl=asset,
                    sourceSha256=digest * 64, retrievedOn='2024-02-28')
    producer = 'https://producer.example.test/selected-unit'
    listing = 'https://authority.other.test/selected-unit'
    front_url = 'https://authority.other.test/front.jpg'
    rear_url = 'https://producer.example.test/rear.jpg'
    evidence = [dict(kind='numeric_source_conflict', url=producer,
                     supports='Original main code and declared pack select one unit; different numeric name code is not an alias.',
                     expectedNumericItemName=alternate, expectedPackName=raw[3], alternateLabel='200 mL'),
                unit('manufacturer', selected, '375 ml', producer, rear_url, 'a'),
                unit('exact_upc_listing', selected, '.375L', listing, front_url, 'b'),
                unit('conflicting_numeric_unit_record', alternate, '200 ml',
                     'https://producer.example.test/alternate-unit', 'https://producer.example.test/alternate.jpg', 'c'),
                dict(kind='inspected_package_photo', url=front_url, supports='Whole selected front inspected; source-based unit.',
                     imageSha256=FRONT_HASH, productUrl=listing, associatedUpc=selected, associatedLabel='375 mL',
                     associationBasis='exact_selected_unit_source_record', inspectionReceiptSha256='d'*64,
                     inspectedOn='2024-02-29', wholePackage=True, professional=True),
                dict(kind='inspected_rear_package_photo', url=rear_url, supports='Separate whole rear printed code and volume inspected.',
                     imageSha256='e'*64, productUrl=producer, printedUpc=selected, printedNetContent='375 ML',
                     inspectionReceiptSha256='d'*64, inspectedOn='2024-02-29', wholePackage=True)]
    review = dict(sku=raw[0], expectedUpc=selected, expectedNameSha256=hashlib.sha256(raw[2].encode()).hexdigest(),
                  expectedSourceRecordSha256=builder.review_evidence_sha256(raw), expectedSourceRow=1,
                  action='resolve_verified_identity', verificationMethod=builder.NUMERIC_CONFLICT_IDENTITY_METHOD,
                  expectedDepartment=raw[4],
                  selectionBasis='original_main_upc_and_declared_pack',
                  conflictDisposition='different_unit_same_expression_not_alias',
                  expectedResultingGroupSkus=[raw[0]], expectedImageSha256=FRONT_HASH,
                  verifiedName=TITLE, verifiedLabel='375 mL', reviewedOn='2024-02-29', reason='Explicit original-code and declared-pack selection.',
                  verification=dict.fromkeys(['brand','expression','size','packaging','upc','visual'], True), evidence=evidence)
    image = dict(sku=raw[0], expectedUpc=selected, expectedName=TITLE, expectedLabel='375 mL',
                 src='images/products/synthetic-front.jpg', alt='Synthetic selected unit', sha256=FRONT_HASH,
                 verifiedOn='2024-02-29', verification=dict.fromkeys(['brand','expression','size','packaging','visual','professional'], True),
                 usageBasis='user_authorized_direct_reference', attributionRequired=False)
    private = dict(image, sourcePage=listing, sourceImageUrl=front_url)
    return source, dict(source_reviews_manifest={'schemaVersion':1, 'reviews':[review]},
                        image_manifest={'schemaVersion':1, 'images':[image]},
                        private_direct_image_evidence_manifest={'schemaVersion':1,
                            'kind':builder.PRIVATE_DIRECT_IMAGE_EVIDENCE_KIND, 'entries':[private]})


def review(kwargs): return kwargs['source_reviews_manifest']['reviews'][0]
def proof(kwargs, kind): return next(item for item in review(kwargs)['evidence'] if item['kind'] == kind)
def conflict(kwargs): return proof(kwargs, 'numeric_source_conflict')
def variants(catalog): return {v['sku']:(p,v) for p in catalog['products'] for v in p['variants']}


def retitle(kwargs, title):
    prior = review(kwargs)['verifiedName']; review(kwargs)['verifiedName'] = title
    for kind in ('manufacturer','exact_upc_listing','conflicting_numeric_unit_record'):
        item = proof(kwargs,kind); item['observedName'] = title; item['supports'] = item['supports'].replace(prior,title)
    kwargs['image_manifest']['images'][0]['expectedName'] = title
    kwargs['private_direct_image_evidence_manifest']['entries'][0]['expectedName'] = title


class NumericConflictIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'images/products').mkdir(parents=True)
        (self.root/'images/products/synthetic-front.jpg').write_bytes(FRONT)
        self.root_patch = patch.object(builder, 'ROOT', self.root); self.root_patch.start(); self.addCleanup(self.root_patch.stop)

    def test_selected_unit_preserves_raw_volume_and_isolates_same_expression_sibling(self):
        source = export({}, dict(ItemName=TITLE, PackName='375 ml'))
        source, kwargs = fixture(source); original = copy.deepcopy(kwargs)
        baseline, _ = builder.build(source)
        catalog, audit = builder.build(source, **kwargs)
        p, v = variants(catalog)['1']
        self.assertEqual([x['sku'] for x in p['variants']], ['1'])
        self.assertEqual(v['upc'], MAIN); self.assertEqual(v['label'], '375 mL')
        self.assertEqual(v['image']['src'], 'images/products/synthetic-front.jpg')
        self.assertEqual(variants(catalog)['2'], variants(baseline)['2'])
        self.assertNotEqual(p['id'], variants(catalog)['2'][0]['id'])
        raw = list(csv.reader(io.StringIO(source.decode())))[1]
        self.assertEqual(audit['records'][0]['rawFields'], raw)
        self.assertEqual(audit['records'][0]['source']['ItemName'], OTHER)
        self.assertEqual(audit['records'][0]['source']['PackName'], raw[3])
        self.assertEqual(audit['records'][0]['numericIdentityConflict'], dict(
            originalMainUpc=MAIN, originalNumericItemName=OTHER, originalPackName=raw[3],
            alternateLabel='200 mL', selectionBasis='original_main_upc_and_declared_pack',
            conflictDisposition='different_unit_same_expression_not_alias'))
        self.assertNotIn(OTHER, json.dumps(catalog)); self.assertEqual(kwargs, original)
        source, kwargs = fixture(export(dict(PackName='375 ml Liquor T')))
        catalog, _ = builder.build(source, **kwargs)
        self.assertEqual(variants(catalog)['1'][1]['label'], '375 mL')
        for brand in ['Quartet','Pintastic','Singlehanded','Thirteenfold','Bottlebrush']:
            source, kwargs = fixture(); title = 'Example '+brand+' Vodka 80 Proof'; retitle(kwargs,title)
            with self.subTest(brand=brand):
                catalog,_ = builder.build(source,**kwargs)
                self.assertEqual(variants(catalog)['1'][0]['name'],title)
                self.assertEqual(variants(catalog)['1'][1]['label'],'375 mL')

    def test_exact_source_guards_cannot_be_changed_or_recomputed_to_select_another_pack(self):
        source, kwargs = fixture()
        for key, value in [('expectedSourceRow',2),('expectedSourceRow',True),('expectedNameSha256','f'*64),
                           ('expectedSourceRecordSha256','f'*64),('expectedPackName','200 ml'),
                           ('expectedDepartment','Rum'),('expectedNumericItemName',MAIN),('expectedUpc',OTHER)]:
            bad = copy.deepcopy(kwargs)
            (conflict(bad) if key in {'expectedNumericItemName','expectedPackName'} else review(bad))[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source, **bad)
        with self.assertRaises(ValueError): builder.build(source.replace(b'7.31',b'8.31'), **kwargs)

    def test_codes_are_distinct_literal_valid_units_never_padded_aliases_or_shortcuts(self):
        for main, alternate in [(MAIN,MAIN),(MAIN,MAIN.zfill(14)),(MAIN,'57'),
                                 ('012345678906',OTHER),(MAIN,'012345678913'),(' '+MAIN,OTHER),
                                 ('000000000000',OTHER),(MAIN,'Example Vodka')]:
            source, kwargs = fixture(export(dict(MAINUPC=main,ItemName=alternate)))
            with self.subTest(main=main,alternate=alternate), self.assertRaises(ValueError): builder.build(source, **kwargs)
        source, kwargs = fixture(); proof(kwargs,'manufacturer')['observedUpc']=MAIN.zfill(14)
        with self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_one_original_positive_volume_without_count_or_container_is_required(self):
        for pack in ['', '0 ml', '375 g', '375 ml 200 ml', '375 ml 375 ml', '2-pack 375 ml',
                     'CS24 375 ml','375 ml 24CS','2DZ 375 ml','375 ml two-pack','375 ml twopack',
                     '375 ml bottles','375 ml 2x6','375 ml case',
                     '375 ml thirteen bottles Original','375 ml two large bottles Original',
                     '375 ml two decorated imported cans Original','375 ml thirteenbottles Original',
                     '375 ml TwoLargeBottles Original','375 ml twolargebottles Original',
                     '375 ml CS','375 ml DZ','375 ml large jug Original']:
            source, kwargs = fixture(export(dict(PackName=pack)))
            with self.subTest(pack=pack), self.assertRaises(ValueError): builder.build(source, **kwargs)

    def test_selected_label_cannot_override_the_original_or_copy_the_alternate(self):
        source, kwargs = fixture()
        for key, value in [('verifiedLabel','200 mL'),('verifiedLabel','0.375 L'),('alternateLabel','375 mL'),
                           ('selectionBasis','numeric_item_name'),('conflictDisposition','same_unit_alias'),
                           ('expectedResultingGroupSkus',['1','2'])]:
            bad = copy.deepcopy(kwargs)
            (conflict(bad) if key=='alternateLabel' else review(bad))[key]=value
            with self.subTest(key=key,value=value), self.assertRaises(ValueError): builder.build(source, **bad)
        for claim in ['Quart Bottle','Two Bottles','QuartBottle','quartbottle','TWOBOTTLES',
                      'TwoLargeBottles','twolargebottles']:
            bad = copy.deepcopy(kwargs); title = 'Example Vodka '+claim+' 80 Proof'; retitle(bad,title)
            with self.subTest(title=title), self.assertRaises(ValueError): builder.build(source,**bad)

    def test_each_selected_and_alternate_unit_needs_its_own_exact_proof(self):
        source, kwargs = fixture()
        for kind in ['manufacturer','exact_upc_listing','conflicting_numeric_unit_record']:
            for key, value in [('observedUpc',MAIN.zfill(14)),('observedNetContent','1 L'),
                               ('observedProof','100'),('quantityBasis','shipping_case'),
                               ('retrievedOn','2024-02-30'),('sourceSha256','bad'),('codeSourceField','')]:
                bad=copy.deepcopy(kwargs); proof(bad,kind)[key]=value
                with self.subTest(kind=kind,key=key), self.assertRaises(ValueError): builder.build(source,**bad)
        for kind in ['manufacturer','conflicting_numeric_unit_record']:
            bad=copy.deepcopy(kwargs); proof(bad,kind)['observedName']='Example Spiced Vodka 80 Proof'
            with self.subTest(kind=kind), self.assertRaises(ValueError): builder.build(source,**bad)
        for kind in ['conflicting_numeric_unit_record','inspected_rear_package_photo']:
            bad=copy.deepcopy(kwargs); review(bad)['evidence']=[x for x in review(bad)['evidence'] if x['kind']!=kind]
            with self.subTest(missing=kind), self.assertRaises(ValueError): builder.build(source,**bad)

    def test_independent_selected_unit_record_cannot_be_a_producer_host_alias(self):
        source, kwargs = fixture()
        for alias in ['https://producer.example.test./listing','https://www.producer.example.test/listing',
                      'https://shop.producer.example.test/listing','https://producer.example.test\u3002/listing']:
            bad=copy.deepcopy(kwargs); proof(bad,'exact_upc_listing')['url']=alias
            with self.subTest(alias=alias), self.assertRaises(ValueError): builder.build(source,**bad)
        bad=copy.deepcopy(kwargs); proof(bad,'conflicting_numeric_unit_record')['sourceSha256']='a'*64
        with self.assertRaises(ValueError): builder.build(source,**bad)

    def test_front_and_rear_have_distinct_roles_and_exact_unit_associations(self):
        source, kwargs = fixture()
        for kind, key, value in [('inspected_package_photo','associatedUpc',OTHER),
                ('inspected_package_photo','associatedLabel','200 mL'),
                ('inspected_package_photo','associationBasis','barcode_read_from_front'),
                ('inspected_package_photo','url','https://producer.example.test/alternate.jpg'),
                ('inspected_package_photo','professional',False),('inspected_package_photo','wholePackage',False),
                ('inspected_package_photo','inspectionReceiptSha256',''),
                ('inspected_rear_package_photo','printedUpc',OTHER),
                ('inspected_rear_package_photo','printedNetContent','200 ML'),
                ('inspected_rear_package_photo','imageSha256',FRONT_HASH)]:
            bad=copy.deepcopy(kwargs); proof(bad,kind)[key]=value
            with self.subTest(kind=kind,key=key), self.assertRaises(ValueError): builder.build(source,**bad)

    def test_exact_professional_front_is_required_not_a_sibling_photo_or_unmatched_hash(self):
        source, kwargs=fixture()
        for key, value in [('image_manifest',None),('private_direct_image_evidence_manifest',None)]:
            bad=copy.deepcopy(kwargs); bad[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source,**bad)
        for key, value in [('expectedUpc',OTHER),('expectedLabel','200 mL'),('expectedName','Other Vodka'),('sha256','f'*64)]:
            bad=copy.deepcopy(kwargs)
            for item in [bad['image_manifest']['images'][0],bad['private_direct_image_evidence_manifest']['entries'][0]]: item[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): builder.build(source,**bad)
        bad=copy.deepcopy(kwargs)
        for item in [bad['image_manifest']['images'][0],bad['private_direct_image_evidence_manifest']['entries'][0]]: item['verification']['professional']=False
        with self.assertRaises(ValueError): builder.build(source,**bad)
        bad=copy.deepcopy(kwargs); bad['private_direct_image_evidence_manifest']['entries'][0]['sourcePage']='https://authority.other.test/alternate-unit'
        with self.assertRaises(ValueError): builder.build(source,**bad)

    def test_overlapping_public_title_and_identity_overrides_are_forbidden(self):
        source, kwargs=fixture(); raw=list(csv.reader(io.StringIO(source.decode())))[1]
        name={'sku':'1','expectedUpc':MAIN,'expectedNameSha256':hashlib.sha256(OTHER.encode()).hexdigest()}
        bad=copy.deepcopy(kwargs); bad['name_manifest']={'schemaVersion':1,'identityReviews':[dict(name,action='quarantine',reason='Conflict',evidence=[{'url':'https://example.test/conflict'}])]}
        with self.assertRaises(ValueError): builder.build(source,**bad)
        bad=copy.deepcopy(kwargs); bad['public_name_reviews_manifest']={'schemaVersion':1,'reviews':[dict(
            sku='1',expectedUpc=MAIN,expectedSourceRow=1,expectedRawFieldsSha256=builder.review_evidence_sha256(raw),
            expectedPublicName=TITLE,expectedLabel='375 mL',expectedCategory='spirits',expectedDepartment='Vodka',
            proposedPublicTitle='Other Vodka',reason='Separate title.',evidence=[{'kind':'retailer_exact_upc_name',
            'url':'https://example.test/unit','supports':f'Literal {MAIN} Other Vodka.'}])]}
        with self.assertRaises(ValueError): builder.build(source,**bad)

    def test_legacy_full_identity_and_numeric_name_only_do_not_gain_this_opt_in(self):
        source, kwargs=fixture(); legacy=copy.deepcopy(review(kwargs))
        for key in ['verificationMethod','expectedSourceRow','expectedDepartment',
                    'selectionBasis','conflictDisposition','expectedImageSha256','expectedResultingGroupSkus']:
            legacy.pop(key)
        with self.assertRaises(ValueError): builder.build(source,source_reviews_manifest={'schemaVersion':1,'reviews':[legacy]})
        bad=copy.deepcopy(kwargs); review(bad)['verificationMethod']=builder.NUMERIC_NAME_RESTORATION_METHOD
        review(bad)['action']='restore_verified_numeric_name'
        with self.assertRaises(ValueError): builder.build(source,**bad)
        source, kwargs=fixture(export(dict(ItemName=TITLE)))
        with self.assertRaises(ValueError): builder.build(source,**kwargs)

    def test_private_projection_preserves_all_typed_conflict_evidence(self):
        source, kwargs=fixture(); original=copy.deepcopy(kwargs)
        ledger={'schemaVersion':1,'kind':builder.PRIVATE_REVIEW_EVIDENCE_KIND,'records':{}}
        kwargs['source_reviews_manifest']=project_evidence(kwargs['source_reviews_manifest'],ledger['records'])
        self.assertNotIn('https://',json.dumps(kwargs['source_reviews_manifest']))
        self.assertNotIn(OTHER,json.dumps(kwargs['source_reviews_manifest']))
        self.assertNotIn('375 ml Original T',json.dumps(kwargs['source_reviews_manifest']))
        path=self.root/'ledger.json'; path.write_text(json.dumps(ledger))
        with patch.dict(os.environ,QUICKSTOP_PRIVATE_REVIEW_EVIDENCE=str(path)):
            catalog,audit=builder.build(source,**kwargs)
        self.assertEqual(audit['records'][0]['sourceReview'],review(original))
        self.assertEqual(variants(catalog)['1'][1]['label'],'375 mL')


if __name__=='__main__': unittest.main()
