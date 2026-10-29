#!/usr/bin/env python3
"""Build the public, price-free catalog from an explicitly supplied PRIVATE export.

The input and detailed reconciliation must remain outside this repository. No
quantities, source prices, or unfiltered source fields enter browser assets.
Run: python3 scripts/build_catalog.py --input /private/Inventory.csv \
        --private-output /private/reconciliation
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from html import escape
import io
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_NAME_REVIEWS = ROOT / 'data/public-name-reviews.json'
EXPECTED_HEADERS = ['SKU', 'MAINUPC', 'ItemName', 'PackName', 'DEPNAME',
                    'ItemPrice', 'TotalQty_Multi', 'ItemTypeDesc']
BASELINE_HASH = '822ee8b3b623548db0f2ea531aa10c340b160a47afc0d9e88398311ed7fa6882'

CATEGORY_DEPARTMENTS = {
    'spirits': ('Spirits', ['Vodka', 'Tequila', 'Whiskey', 'Rum', 'Cognac', 'Brandy', 'Gin', 'Cordials']),
    'wine': ('Wine', ['Standard', 'Vintage', 'Sparklin', 'wine']),
    'beer': ('Beer', ['Domestic', 'Imports', 'Micro', 'MALT BEVERAGE']),
    'drinks': ('Drinks & mixers', ['Ready To Drink', 'Coolers', 'Soda', 'Juice', 'N/A Mix', 'Water']),
    'food': ('Snacks & groceries', ['Snacks', 'Candy', 'GROCERY']),
    'essentials': ('Everyday essentials', ['MISC', 'Tobacco']),
    'other': ('More in store', ['ShortCut', '']),
}
DEPARTMENT_LABELS = {'Standard': 'Standard wine', 'Vintage': 'Vintage wine',
                     'Sparklin': 'Sparkling wine', 'wine': 'Other wine',
                     'Domestic': 'Domestic beer', 'Imports': 'Imported beer',
                     'Micro': 'Craft beer', 'MALT BEVERAGE': 'Malt beverages',
                     'N/A Mix': 'Non-alcoholic mixers', 'MISC': 'General merchandise',
                     'GROCERY': 'Groceries', 'ShortCut': 'Other listed items',
                     '': 'Unassigned department'}

# Deliberately recognize measurements and packs, never arbitrary numeric tokens.
# Unambiguous multi-letter units can be joined to the following expression in
# the source export. Single letters require a full word boundary: 100 GRAND
# and 1800 LIME are brands/flavors, never 100 grams or 1800 liters.
MEASURE_RE = re.compile(
    r'(?<![\d./])(?P<number>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?|\.\d+)\s*'
    r'(?P<unit>fl\.?\s*oz\.?|millilit(?:er|re)s?|ml|lit(?:er|re)s?|lit\b|ltr?s?\b|'
    r'l\b|ounces?|oz\.?|kilograms?|kg|grams?|g\b|'
    r'pounds?|lbs?\b|quarts?|qts?\b|gallons?|gals?\b|sq\.?\s*ft|ft\b|m\b)', re.I)
PACK_RE = re.compile(r'(?<![\d.])(?P<number>\d+)\s*(?:packs?|pks?|count|ct)'
                     r'(?=$|\b|\d|(?-i:[A-Z]))', re.I)
CONTAINER_RE = re.compile(r'\b(cans?|bottles?|btls?)\s*$', re.I)
STALE_STATUS_RE = re.compile(r'OUT\s+OF\s+STOCK|UPC\s+NOT\s+IN\s+USE', re.I)
PRICE_RE = re.compile(r'\$|\b\d+\s*for\s*\$?\s*\d|\b\d+(?:\.\d+)?\s*cents?\b|\b\d+\s*c\b', re.I)
BARE_DECIMAL_RE = re.compile(r'(?<![\d.])(?:\d+\.\d+|\.\d+)(?![\d.])')
DISTINGUISHING_NUMBER_RE = re.compile(r'\bNo\.\s*\d+(?:\.\d+)?\b|'
                                    r'\d+\.\d+\s*(?:%|ABV\b|A/C\b|PROOF\b)', re.I)
JOB_LINE_RE = re.compile(r'^JOB (?:1\.0|1\.25|1\.5) Rolling Papers$', re.I)
JOINED_UNIT_RE = re.compile(r'(?<![\d.])(?P<number>\d+\.\d+)\s*(?P<unit>[LG])'
                            r'(?=(?:Pinot|Chardonnay|Crisp|Merlot|Sunset|Lemon|Raspberry|'
                            r'Watermelon|Moscato|Sangria|White|Regular|Pink|Premium)\b)', re.I)
CURRENCY = r'(?:\$\s*\d*(?:\.\d+)?|\d+(?:\.\d+)?\s*\$)'
OFFER_RE = re.compile(r'\d+\s*for\s*(?:' + CURRENCY + r'|\d+(?:\.\d+)?\s*(?:cents?|c)\b)', re.I)
CURRENCY_RE = re.compile(CURRENCY + r'\s*(?:off\b|cents?\b)?', re.I)
CENT_RE = re.compile(r'(?<!\w)(?:\d+(?:\.\d+)?|\.\d+)\s*cents?\b\s*(?:off\b)?', re.I)
EXPLICIT_UTILITIES = {
    'age check 18', 'age check 21', 'deposite', 'deposit', 'discount',
    'cook county tax', 'lotto sale machine', 'machine lotto', 'misc ebt',
    'misc taxed item', 'misc taxed items',
}
REPORT_POS_CONTROLS = {'-2000': 'Card Void Adjustment', '-3000': 'COUPON',
                       '0': 'Scan Group', '-1000': 'Store Credit'}

# Reviewed publisher-owned record hosts. PepsiCo's own sitemap also delegates
# individual records to Scanbuy; that host requires a PepsiCo ownership URL.
MANUFACTURER_NAME_HOSTS = {
    'PepsiCo': {'smartlabel.pepsico.info', 'www.pepsicoproductfacts.com',
                'pepsicoproductfacts.com', 'www.pepsicopartners.com', 'pepsicopartners.com'},
    'Wells Enterprises': {'wellsfoodservice.com', 'www.wellsfoodservice.com'},
    'Conagra Brands': {'smartlabel.conagra.com'},
    'Kraft Heinz': {'www.kraftheinz.com', 'kraftheinz.com'},
}
MANUFACTURER_NAME_SCOPE = 'manufacturer_exact_upc_name_identity'
NAME_STRENGTH_RE = re.compile(r'\d+(?:\.\d+)?\s*(?:%\s*(?:ABV\b|A/C\b)?|ABV\b|A/C\b|PROOF\b)', re.I)


def clean_space(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip()


def decimal_text(value: str) -> str:
    return format(parse_number(value).normalize(), 'f')


def parse_number(value: str) -> Decimal:
    if '/' not in value:
        return Decimal(value)
    parts = value.split()
    whole = Decimal(parts[0]) if len(parts) == 2 else Decimal(0)
    numerator, denominator = parts[-1].split('/')
    return whole + Decimal(numerator) / Decimal(denominator)


def measurement(number: str, raw_unit: str) -> tuple[str, str, Decimal]:
    unit = re.sub(r'[\s.]', '', raw_unit.lower())
    number_dec = parse_number(number)
    if unit.startswith(('ml', 'millilit')):
        return f'{decimal_text(number)} mL', 'volume-mL', number_dec
    if unit in ('l', 'lit', 'lt', 'lts', 'ltr', 'ltrs') or unit.startswith(('liter', 'litre')):
        return f'{decimal_text(number)} L', 'volume-mL', number_dec * 1000
    if unit.startswith(('oz', 'ounce', 'floz')):
        return f'{decimal_text(number)} oz', 'ounce', number_dec
    if unit.startswith(('kg', 'kilogram')):
        return f'{decimal_text(number)} kg', 'mass-g', number_dec * 1000
    if unit == 'g' or unit.startswith('gram'):
        return f'{decimal_text(number)} g', 'mass-g', number_dec
    if unit.startswith(('lb', 'pound')):
        return f'{decimal_text(number)} lb', 'pound', number_dec
    if unit.startswith(('qt', 'quart')):
        return f'{decimal_text(number)} qt', 'quart', number_dec
    if unit.startswith(('gal', 'gallon')):
        return f'{decimal_text(number)} gal', 'gallon', number_dec
    if unit == 'sqft':
        return f'{decimal_text(number)} sq ft', 'area-sq-ft', number_dec
    return f'{decimal_text(number)} {unit}', 'length-' + unit, number_dec


def extract_dimensions(text: str) -> dict:
    measures = []
    packs = []

    def take_measure(match):
        value = measurement(match['number'], match['unit'])
        if value not in measures:
            measures.append(value)
        return ' '

    remaining = MEASURE_RE.sub(take_measure, text)

    def take_pack(match):
        number = int(match['number'])
        if number not in packs:
            packs.append(number)
        return ' '

    remaining = PACK_RE.sub(take_pack, remaining)
    container = ''
    container_token = ''
    match = CONTAINER_RE.search(remaining)
    if match:
        container_token = match[1]
        container = 'cans' if match[1].lower().startswith('can') else 'bottles'
        remaining = remaining[:match.start()]
    return {'text': clean_space(remaining), 'measures': measures,
            'packs': packs, 'container': container, 'containerToken': container_token}


def numeric_signature(parts: dict) -> set:
    return {(dimension, value) for _, dimension, value in parts['measures']}


def has_ambiguous_decimal(text: str) -> bool:
    # These are literal product expressions/strength statements, not inferred
    # package measurements. Preserve them in the grouping identity and title.
    if JOB_LINE_RE.fullmatch(text):
        return False
    return bool(BARE_DECIMAL_RE.search(DISTINGUISHING_NUMBER_RE.sub(' ', text)))


def separate_corroborated_joined_unit(title: str, pack_parts: dict) -> tuple[str, bool]:
    signature = numeric_signature(pack_parts)
    changed = False

    def separate(match):
        nonlocal changed
        _, dimension, value = measurement(match['number'], match['unit'])
        if (dimension, value) in signature:
            changed = True
            return match.group() + ' '
        return match.group()

    return JOINED_UNIT_RE.sub(separate, title), changed


def strip_price_annotations(text: str) -> tuple[str, bool]:
    original = text
    text = OFFER_RE.sub(' ', text)
    text = CURRENCY_RE.sub(' ', text)
    text = CENT_RE.sub(' ', text)
    return clean_space(text), text != original


def identifiable_name(name: str) -> bool:
    if not re.search(r'[A-Za-z]', name):
        return False
    # Generic POS buttons do not identify a specific product, even after a
    # currency annotation has been removed.
    reduced = re.sub(r'[^a-z ]', ' ', name.casefold())
    words = set(reduced.split())
    generic = {'for', 'candy', 'juice', 'soda', 'snack', 'snacks', 'beer',
               'cans', 'can', 'bottle', 'bottles', 'cigar', 'cigars', 'cent',
               'cents', 'pack', 'pk', 'what', 'kind', 'size', 'blue', 'orange',
               'strawberry', 'frutti', 'punch', 'lemonade', 'pop'}
    return bool(words - generic)


def category_for(department: str) -> str:
    for category, (_, departments) in CATEGORY_DEPARTMENTS.items():
        if department in departments:
            return category
    return 'other'


def classify_record(record: dict) -> tuple[str, str, dict | None, list[str]]:
    title = clean_space(record['ItemName'])
    pack = clean_space(record['PackName'])
    department = clean_space(record['DEPNAME'])
    upc = record['MAINUPC'].strip()
    flags = []
    if department == 'Tax':
        return 'excluded_nonproduct', 'Explicit Tax department', None, flags
    if department == 'Instant':
        return 'excluded_nonproduct', 'Explicit lottery/Instant department', None, flags
    if title.casefold() in EXPLICIT_UTILITIES or re.fullmatch(r'COUNTY TAX FOR .*', title, re.I):
        return 'excluded_nonproduct', 'Explicit POS utility or tax record', None, flags
    if re.match(r'^test [12]\b', title, re.I) and re.fullmatch(r'test [12]', upc, re.I):
        return 'excluded_nonproduct', 'Test name corroborated by test UPC', None, flags
    if title.casefold().startswith('machine lotto') and department == 'ShortCut':
        return 'excluded_nonproduct', 'Explicit lottery machine record', None, flags
    placeholder = clean_space(re.sub(r'[^a-z0-9 ]', ' ', title.casefold()))
    if placeholder in {'your description', 'new item', 'new product', 'unknown',
                       'unknown item', 'unknown product', 'unspecified',
                       'enter description', 'item description'}:
        return 'needs_review', 'Placeholder description needs a product name', None, flags

    original_title = title
    title = STALE_STATUS_RE.sub(' ', title)
    if title != original_title:
        flags.append('Stale availability/POS status removed; current stock is unknown')
    title, stripped_price = strip_price_annotations(title)
    if stripped_price:
        flags.append('Explicit currency/price annotation removed from title')
    if PRICE_RE.search(title):
        return 'needs_review', 'Unresolved price/promotion text in title', None, flags
    if PRICE_RE.search(pack):
        return 'needs_review', 'Price-bearing or ambiguous pack field', None, flags
    if PRICE_RE.search(upc) or BARE_DECIMAL_RE.fullmatch(upc):
        flags.append('Non-barcode price-bearing UPC withheld from public data')
        upc = ''
    # Retain legitimate UPCs as exact strings, including leading zeroes.
    # Descriptive POS codes remain private rather than becoming public search text.
    if upc and not re.fullmatch(r'\d{6,14}', upc):
        flags.append('Nonstandard/descriptive UPC retained privately')
        upc = ''

    pack_parts = extract_dimensions(pack)
    title, separated_join = separate_corroborated_joined_unit(title, pack_parts)
    if separated_join:
        flags.append('Joined decimal unit separated using matching PackName measurement and intact descriptor')
    title_parts = extract_dimensions(title)
    name = title_parts['text']
    # A unit/pack removal can reveal a token boundary in malformed joined text.
    name, second_price_removal = strip_price_annotations(name)
    if second_price_removal:
        flags.append('Explicit currency/price annotation removed after unit parsing')
    if PRICE_RE.search(name):
        return 'needs_review', 'Unresolved price/promotion text after unit parsing', None, flags
    if has_ambiguous_decimal(name):
        return 'needs_review', 'Unlabelled decimal may be a price or unconfirmed measure', None, flags
    if not identifiable_name(name):
        return 'needs_review', 'Needs a specific product name; source record retained', None, flags
    if re.search(r'what\s*(?:size|kind)', name, re.I):
        return 'needs_review', 'Explicitly unidentified product/size prompt in source name', None, flags
    if re.search(r'\?\s*$', name, re.I):
        flags.append('Original product description needs confirmation')

    title_measures = numeric_signature(title_parts)
    pack_measures = numeric_signature(pack_parts)
    size_conflict = bool(title_measures and pack_measures and not pack_measures.issubset(title_measures))
    count_conflict = bool(title_parts['packs'] and pack_parts['packs'] and
                          set(title_parts['packs']) != set(pack_parts['packs']))
    container_conflict = bool(title_parts['container'] and pack_parts['container'] and
                              title_parts['container'] != pack_parts['container'])
    multiple_conflicting_sizes = any(
        len({value for _, dim, value in parts['measures'] if dim == dimension}) > 1
        for parts in (title_parts, pack_parts)
        for dimension in {dim for _, dim, _ in parts['measures']})
    multiple_conflicting_packs = len(title_parts['packs']) > 1 or len(pack_parts['packs']) > 1
    # Conflicting fields stay in the master record; do not select either value.
    if size_conflict or count_conflict or container_conflict or multiple_conflicting_sizes or multiple_conflicting_packs:
        flags.append('Title and PackName size/pack conflict; neither value selected')
        label = 'Size needs confirmation'
        # Keep newly recognized abbreviations recoverable in the public name
        # when the conflicting variant label cannot carry package details.
        if title_parts['containerToken'].casefold() in {'btl', 'btls'}:
            name = clean_space(name + ' ' + title_parts['containerToken'])
    else:
        measures = title_parts['measures'] or pack_parts['measures']
        packs = title_parts['packs'] or pack_parts['packs']
        pieces = [f'{number}-pack' for number in packs]
        pieces.extend(label for label, _, _ in measures)
        container = title_parts['container'] or pack_parts['container']
        if container:
            pieces.append(container)
        # Only explicit Single metadata becomes a single, never assume it from
        # absence of a pack count.
        if not packs and re.match(r'^single\b', pack, re.I):
            pieces.insert(0, 'Single')
        label = ' · '.join(pieces) or 'Size not specified'

    public = {'name': clean_space(name), 'category': category_for(department),
              'department': department, 'variant': {
                  'sku': record['SKU'], 'upc': upc, 'label': label,
                  'sourceRow': record['sourceRow'], 'image': None}}
    return 'retained_variant', 'Named merchandise retained', public, flags


def is_direct_reference_image(image: dict, sku: str) -> bool:
    """Distinguish a user-authorized reference use from a claimed image license."""
    if 'usageBasis' not in image:
        if 'attributionRequired' in image:
            raise ValueError(f'Image attribution policy requires an explicit usage basis: {sku}')
        return False
    if image['usageBasis'] != 'user_authorized_direct_reference':
        raise ValueError(f'Unsupported image usage basis: {sku}')
    if image.get('attributionRequired') is not False:
        raise ValueError(f'Direct reference image requires explicit attribution policy: {sku}')
    if any(field in image for field in ('license', 'licenseUrl', 'attribution')):
        raise ValueError(f'Direct reference image must not invent license or attribution metadata: {sku}')
    return True


def apply_verified_images(products: list, image_manifest: dict | None, blocked_skus: set | None = None) -> int:
    if not image_manifest:
        return 0
    if image_manifest.get('schemaVersion') != 1 or not isinstance(image_manifest.get('images'), list):
        raise ValueError('Invalid image manifest schema')
    variants = {v['sku']: (p, v) for p in products for v in p['variants']}
    seen = set()
    image_root = (ROOT / 'images/products').resolve()
    verified_fields = {'brand', 'expression', 'size', 'packaging', 'visual', 'professional'}
    for entry in image_manifest['images']:
        sku = entry.get('sku')
        if sku in (blocked_skus or set()):
            raise ValueError(f'Image blocked while source size is under explicit review: {sku}')
        if sku in seen or sku not in variants:
            raise ValueError(f'Image manifest SKU is duplicate or absent from public catalog: {sku!r}')
        seen.add(sku)
        product, variant = variants[sku]
        expected = (entry.get('expectedUpc'), entry.get('expectedName'), entry.get('expectedLabel'))
        actual = (variant['upc'], product['name'], variant['label'])
        if expected != actual:
            raise ValueError(f'Image identity mismatch for SKU {sku}: expected metadata does not match catalog')
        verification = entry.get('verification', {})
        if set(verification) != verified_fields or any(v is not True for v in verification.values()):
            raise ValueError(f'Image must have every exact-match, visual and professional-quality check verified: {sku}')
        src = entry.get('src', '')
        if not isinstance(src, str) or not re.fullmatch(r'images/products/[A-Za-z0-9_./-]+', src):
            raise ValueError(f'Image must use a local images/products path: {sku}')
        image_path = (ROOT / src).resolve()
        if image_root not in image_path.parents or not image_path.is_file():
            raise ValueError(f'Missing or unsafe product image path: {sku}')
        data = image_path.read_bytes()
        if not data or hashlib.sha256(data).hexdigest() != entry.get('sha256'):
            raise ValueError(f'Product image is empty or hash does not match: {sku}')
        direct_reference = is_direct_reference_image(entry, sku)
        if direct_reference:
            required = {'sku', 'expectedUpc', 'expectedName', 'expectedLabel', 'src', 'alt',
                        'sourcePage', 'sourceImageUrl', 'sha256', 'verifiedOn', 'verification',
                        'usageBasis', 'attributionRequired'}
            if set(entry) != required:
                raise ValueError(f'Direct reference image has missing, unsupported or private fields: {sku}')
            if (not re.fullmatch(r'\d{6,14}', variant['upc'])
                    or variant['label'] in {'Size not specified', 'Size needs confirmation'}):
                raise ValueError(f'Direct reference image requires a known exact UPC and size: {sku}')
        required_text = ('alt', 'sourcePage', 'sourceImageUrl', 'verifiedOn')
        if not direct_reference:
            required_text += ('license', 'licenseUrl', 'attribution')
        for field in required_text:
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f'Image manifest requires {field}: {sku}')
        for field in ('sourcePage', 'sourceImageUrl') + (() if direct_reference else ('licenseUrl',)):
            if not re.fullmatch(r'https?://[^\s]+', entry[field]):
                raise ValueError(f'Invalid image evidence/license URL: {sku}')
        if PRICE_RE.search(entry['alt']):
            raise ValueError(f'Image alt text must not contain prices: {sku}')
        if direct_reference:
            if not safe_review_text(entry['alt']) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['verifiedOn']):
                raise ValueError(f'Direct reference image requires safe alt text and review date: {sku}')
            public_fields = ('src', 'alt', 'sourcePage', 'usageBasis', 'attributionRequired')
        else:
            public_fields = ('src', 'alt', 'sourcePage', 'license', 'licenseUrl', 'attribution')
        variant['image'] = {field: entry[field] for field in public_fields}
    return len(seen)


def safe_review_text(value: object) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and not PRICE_RE.search(value)
            and not re.search(r'[$€£]|(?:^|\s)(?:/Users/|/home/|file:)', value))


def review_url_host(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'https://[^\s]+', value):
        return ''
    parsed = urlparse(value)
    try:
        if parsed.username or parsed.password or parsed.port is not None:
            return ''
    except ValueError:
        return ''
    return parsed.hostname or ''


def valid_gtin(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r'(?:\d{8}|\d{12}|\d{13}|\d{14})', value):
        return False
    total = sum(int(digit) * (3 if index % 2 == 0 else 1)
                for index, digit in enumerate(reversed(value[:-1])))
    return (10 - total % 10) % 10 == int(value[-1])


def validate_manufacturer_name_record(entry: dict) -> None:
    """Validate a name-only review without inferring a visual/package check."""
    sku = entry['sku']
    record = entry.get('manufacturerRecord')
    required = {'publisher', 'productUrl', 'publisherEvidenceUrl', 'observedUpc',
                'observedName', 'retrievedOn', 'sourceSha256', 'retiredNotice'}
    if not isinstance(record, dict) or set(record) != required:
        raise ValueError(f'Manufacturer name review requires a complete source record: {sku}')
    publisher = record['publisher']
    if not isinstance(publisher, str) or publisher not in MANUFACTURER_NAME_HOSTS:
        raise ValueError(f'Manufacturer name review requires a reviewed publisher: {sku}')
    owned_hosts = MANUFACTURER_NAME_HOSTS[publisher]
    product_host = review_url_host(record['productUrl'])
    ownership_host = review_url_host(record['publisherEvidenceUrl'])
    delegated = publisher == 'PepsiCo' and product_host == 'smartlabel.myproduct.info'
    if ownership_host not in owned_hosts or (product_host not in owned_hosts and not delegated):
        raise ValueError(f'Manufacturer name source ownership is not established: {sku}')
    if delegated:
        path_gtins = re.findall(r'(?<!\d)\d{12}(?!\d)', urlparse(record['productUrl']).path)
        if (record['publisherEvidenceUrl'] != 'https://smartlabel.pepsico.info/sitemap.xml'
                or len(path_gtins) != 1
                or path_gtins[0].zfill(14) != str(record['observedUpc']).zfill(14)):
            raise ValueError(f'Delegated manufacturer record requires its exact sitemap and unit GTIN path: {sku}')
    if (not valid_gtin(entry['expectedUpc']) or not valid_gtin(record['observedUpc'])
            or entry['expectedUpc'].zfill(14) != record['observedUpc'].zfill(14)):
        raise ValueError(f'Manufacturer name source must state the exact unit GTIN: {sku}')
    if (not safe_review_text(record['observedName'])
            or not isinstance(record['sourceSha256'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', record['sourceSha256'])
            or not isinstance(record['retrievedOn'], str)
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', record['retrievedOn'])
            or type(record['retiredNotice']) is not bool):
        raise ValueError(f'Manufacturer name record requires safe, dated source evidence: {sku}')
    evidence = entry.get('evidence')
    if (not isinstance(evidence, list) or not evidence
            or any(not isinstance(item, dict) or not {'kind', 'url'}.issubset(item)
                   or set(item) - {'kind', 'url', 'supports'}
                   or not safe_review_text(item['kind']) or not review_url_host(item['url'])
                   or ('supports' in item and not safe_review_text(item['supports']))
                   for item in evidence)
            or not any(item['kind'] == 'manufacturer_exact_upc_record'
                       and item['url'] == record['productUrl'] for item in evidence)):
        raise ValueError(f'Manufacturer name review requires its exact source URL: {sku}')


def validate_manufacturer_name_correction(entry: dict) -> None:
    required = {'sku', 'expectedUpc', 'expectedNameSha256', 'replaceTextFrom',
                'replaceTextTo', 'scope', 'verifiedOn', 'evidence',
                'manufacturerRecord', 'expectedLabel', 'verifiedPublicName'}
    sku = entry['sku']
    if set(entry) != required or any(not safe_review_text(entry.get(field)) for field in
                                   ('replaceTextFrom', 'replaceTextTo', 'expectedLabel', 'verifiedPublicName')):
        raise ValueError(f'Manufacturer correction requires guarded name-only fields: {sku}')
    if (hashlib.sha256(entry['replaceTextFrom'].encode()).hexdigest() != entry['expectedNameSha256']
            or not isinstance(entry['verifiedOn'], str)
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['verifiedOn'])):
        raise ValueError(f'Manufacturer correction requires the complete original name: {sku}')
    before = extract_dimensions(entry['replaceTextFrom'])
    after = extract_dimensions(entry['replaceTextTo'])
    validate_manufacturer_name_record(entry)
    before_numbers = Counter(re.findall(r'\d+(?:\.\d+)?', before['text']))
    after_numbers = Counter(re.findall(r'\d+(?:\.\d+)?', after['text']))
    observed_numbers = Counter(re.findall(r'\d+(?:\.\d+)?',
        extract_dimensions(entry['manufacturerRecord']['observedName'])['text']))
    strength_before = Counter(clean_space(match.group()).casefold() for match in NAME_STRENGTH_RE.finditer(before['text']))
    strength_after = Counter(clean_space(match.group()).casefold() for match in NAME_STRENGTH_RE.finditer(after['text']))
    measures_before = Counter(clean_space(match.group()).casefold() for match in MEASURE_RE.finditer(entry['replaceTextFrom']))
    measures_after = Counter(clean_space(match.group()).casefold() for match in MEASURE_RE.finditer(entry['replaceTextTo']))
    if (measures_before != measures_after or before['measures'] != after['measures'] or strength_before != strength_after
            or before['packs'] != after['packs'] or before['container'] != after['container']
            or before_numbers - after_numbers
            or (after_numbers - before_numbers) - observed_numbers):
        raise ValueError(f'Manufacturer name correction cannot change size, pack or distinguishing numbers: {sku}')


def validated_name_overrides(manifest: dict | None) -> tuple[dict, dict]:
    if not manifest:
        return {}, {}
    if manifest.get('schemaVersion') != 1:
        raise ValueError('Invalid name correction manifest schema')
    mappings = []
    for field in ('corrections', 'identityReviews'):
        result = {}
        for entry in manifest.get(field, []):
            sku = entry.get('sku')
            if not isinstance(sku, str) or sku in result:
                raise ValueError('Name manifest has missing/duplicate SKU')
            if not re.fullmatch(r'[a-f0-9]{64}', entry.get('expectedNameSha256', '')) or not isinstance(entry.get('expectedUpc'), str):
                raise ValueError(f'Name manifest requires original identity fields: {sku}')
            if not entry.get('evidence') or not all(re.match(r'^https?://', e.get('url', '')) for e in entry['evidence']):
                raise ValueError(f'Name manifest requires inspectable evidence: {sku}')
            if field == 'identityReviews' and entry.get('action', 'quarantine') not in {'quarantine', 'retain_flagged'}:
                raise ValueError(f'Unsupported identity review action: {sku}')
            if field == 'corrections':
                if not all(isinstance(entry.get(k), str) and entry[k] and not PRICE_RE.search(entry[k])
                           for k in ('replaceTextFrom', 'replaceTextTo')):
                    raise ValueError(f'Invalid name correction: {sku}')
                if entry.get('scope') == MANUFACTURER_NAME_SCOPE:
                    validate_manufacturer_name_correction(entry)
                elif 'manufacturerRecord' in entry:
                    raise ValueError(f'Manufacturer name records require their explicit verification scope: {sku}')
            result[sku] = entry
        mappings.append(result)
    return tuple(mappings)


def validated_category_reviews(manifest: dict | None) -> dict:
    """Accept explicit source- and identity-guarded shopping-category reviews."""
    if manifest is None:
        return {}
    if (not isinstance(manifest, dict) or set(manifest) != {'schemaVersion', 'reviews'}
            or manifest['schemaVersion'] != 1 or not isinstance(manifest['reviews'], list)):
        raise ValueError('Invalid category review manifest schema')
    required = {'sku', 'expectedUpc', 'expectedNameSha256', 'expectedSourceRecordSha256',
                'expectedDepartment', 'expectedPublicName', 'expectedLabel',
                'expectedCategory', 'category', 'reviewedOn', 'reason', 'evidence'}
    reviews = {}
    for entry in manifest['reviews']:
        if not isinstance(entry, dict) or set(entry) != required:
            raise ValueError('Category review contains missing, unsupported or private fields')
        sku = entry['sku']
        if not isinstance(sku, str) or not re.fullmatch(r'\d+', sku) or sku in reviews:
            raise ValueError('Category reviews require unique string SKUs')
        if (not isinstance(entry['expectedUpc'], str)
                or not re.fullmatch(r'\d{6,14}', entry['expectedUpc'])
                or any(not isinstance(entry[key], str) or not re.fullmatch(r'[a-f0-9]{64}', entry[key])
                       for key in ('expectedNameSha256', 'expectedSourceRecordSha256'))):
            raise ValueError(f'Category review requires exact source identity guards: {sku}')
        if (not isinstance(entry['expectedCategory'], str) or not isinstance(entry['category'], str)
                or entry['expectedCategory'] not in CATEGORY_DEPARTMENTS
                or entry['category'] not in CATEGORY_DEPARTMENTS
                or entry['expectedCategory'] == entry['category']):
            raise ValueError(f'Category review requires a distinct valid shopping category: {sku}')
        for field in ('expectedDepartment', 'expectedPublicName', 'expectedLabel', 'reason'):
            value = entry[field]
            if (not isinstance(value, str) or (field != 'expectedDepartment' and not value.strip())
                    or PRICE_RE.search(value)
                    or re.search(r'[$€£]|(?:^|\s)(?:/Users/|/home/|file:)', value)):
                raise ValueError(f'Category review requires safe public metadata: {sku}')
        if not isinstance(entry['reviewedOn'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['reviewedOn']):
            raise ValueError(f'Category review requires a review date: {sku}')
        evidence = entry['evidence']
        if (not isinstance(evidence, list) or not evidence
                or any(not isinstance(item, dict) or not {'kind', 'url'}.issubset(item)
                       or set(item) - {'kind', 'url', 'supports'}
                       or not isinstance(item['kind'], str) or not item['kind'].strip()
                       or not isinstance(item['url'], str) or not re.fullmatch(r'https://[^\s]+', item['url'])
                       or ('supports' in item and (not isinstance(item['supports'], str)
                           or PRICE_RE.search(item['supports'])
                           or re.search(r'[$€£]|(?:^|\s)(?:/Users/|/home/|file:)', item['supports'])))
                       for item in evidence)):
            raise ValueError(f'Category review requires inspectable public evidence: {sku}')
        reviews[sku] = entry
    return reviews


def apply_category_review(public: dict | None, source: dict, raw_fields: list,
                          review: dict) -> None:
    """Change only this variant's shopping category, before grouping."""
    sku = source['SKU']
    source_identity = (source['MAINUPC'], hashlib.sha256(source['ItemName'].encode()).hexdigest(),
                       hashlib.sha256(json.dumps(raw_fields, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
                       source['DEPNAME'])
    expected_source = tuple(review[key] for key in ('expectedUpc', 'expectedNameSha256',
                                                    'expectedSourceRecordSha256', 'expectedDepartment'))
    if source_identity != expected_source:
        raise ValueError(f'Category review source identity mismatch: {sku}')
    if not public:
        raise ValueError(f'Category review requires retained merchandise: {sku}')
    public_identity = (public['variant']['upc'], public['name'], public['variant']['label'],
                       public['department'], public['category'])
    expected_public = tuple(review[key] for key in ('expectedUpc', 'expectedPublicName',
                                                    'expectedLabel', 'expectedDepartment', 'expectedCategory'))
    if public_identity != expected_public:
        raise ValueError(f'Category review public identity mismatch: {sku}')
    public['category'] = review['category']


def apply_public_name_review(public: dict | None, source: dict, raw_fields: list,
                             review: dict) -> None:
    """Replace only the parsed product title after exact source/public guards."""
    sku = source['SKU']
    if not public:
        raise ValueError(f'Public-name review requires retained source merchandise: {sku}')
    source_hash = hashlib.sha256(json.dumps(
        raw_fields, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    expected = (review['sku'], review['expectedUpc'], review['expectedSourceRow'],
                review['expectedRawFieldsSha256'], review['expectedPublicName'], review['expectedLabel'],
                review['expectedCategory'], review['expectedDepartment'])
    current = (sku, public['variant']['upc'], source['sourceRow'], source_hash,
               public['name'], public['variant']['label'], public['category'], public['department'])
    if current != expected:
        raise ValueError(f'Public-name review current identity mismatch: {sku}')
    before_variant = dict(public['variant'])
    before_category = public['category']
    before_department = public['department']
    public['name'] = review['proposedPublicTitle']
    if (public['variant'] != before_variant or public['category'] != before_category
            or public['department'] != before_department):
        raise ValueError(f'Public-name review changed guarded variant metadata: {sku}')


def validated_public_name_reviews(manifest: dict | None) -> dict:
    """Accept exact-source, parsed-variant guarded title-only corrections."""
    if manifest is None:
        return {}
    if (not isinstance(manifest, dict) or set(manifest) != {'schemaVersion', 'reviews'}
            or manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('reviews'), list)):
        raise ValueError('Invalid guarded public-name review manifest schema')
    reviews = {}
    required = {'sku', 'expectedUpc', 'expectedSourceRow', 'expectedRawFieldsSha256',
                'expectedPublicName', 'expectedLabel', 'expectedCategory', 'expectedDepartment',
                'proposedPublicTitle', 'reason', 'evidence'}
    for entry in manifest['reviews']:
        if not isinstance(entry, dict) or set(entry) != required:
            raise ValueError('Public-name review contains unsupported or private fields')
        sku = entry.get('sku')
        if not isinstance(sku, str) or not re.fullmatch(r'\d+', sku) or sku in reviews:
            raise ValueError('Public-name reviews require unique string SKUs')
        if (not isinstance(entry['expectedUpc'], str) or not valid_gtin(entry['expectedUpc'])
                or type(entry['expectedSourceRow']) is not int or entry['expectedSourceRow'] < 1
                or not isinstance(entry['expectedRawFieldsSha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', entry['expectedRawFieldsSha256'])):
            raise ValueError(f'Public-name review requires exact source guards: {sku}')
        public_fields = ('expectedPublicName', 'expectedLabel', 'expectedCategory')
        if any(not safe_review_text(entry[field]) for field in public_fields):
            raise ValueError(f'Public-name review contains unsafe parsed public metadata: {sku}')
        if (not isinstance(entry['expectedDepartment'], str)
                or (entry['expectedDepartment'] and not safe_review_text(entry['expectedDepartment']))):
            raise ValueError(f'Public-name review contains unsafe parsed department metadata: {sku}')
        title = entry['proposedPublicTitle']
        if not safe_review_text(title) or not identifiable_name(title):
            raise ValueError(f'Public-name review requires a safe, specific title: {sku}')
        if not safe_review_text(entry['reason']):
            raise ValueError(f'Public-name review requires a safe explanation: {sku}')
        evidence = entry.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f'Public-name review requires inspectable evidence: {sku}')
        exact_upc_evidence = False
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {'kind', 'url', 'supports'}:
                raise ValueError(f'Public-name evidence contains unsupported or missing fields: {sku}')
            if (not safe_review_text(item['kind']) or not review_url_host(item['url'])
                    or not safe_review_text(item['supports'])):
                raise ValueError(f'Public-name evidence must use a safe, inspectable HTTPS source: {sku}')
            exact_upc_evidence |= entry['expectedUpc'] in item['supports'] or entry['expectedUpc'] in item['url']
        if not exact_upc_evidence:
            raise ValueError(f'Public-name evidence must explicitly identify the guarded UPC: {sku}')
        reviews[sku] = entry
    return reviews


def validated_repair_image_review(review: object, sku: str) -> None:
    required = {'expectedName', 'expectedLabel', 'expectedImageSha256', 'verification', 'evidence'}
    if not isinstance(review, dict) or set(review) != required:
        raise ValueError(f'Repaired image review contains missing, unsupported or private fields: {sku}')
    for field in ('expectedName', 'expectedLabel'):
        value = review[field]
        if (not isinstance(value, str) or not value.strip() or PRICE_RE.search(value)
                or re.search(r'[$€£]|(?:^|\s)(?:/Users/|/home/|file:)', value)):
            raise ValueError(f'Repaired image review requires safe public identity fields: {sku}')
    if (not isinstance(review['expectedImageSha256'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', review['expectedImageSha256'])):
        raise ValueError(f'Repaired image review requires an exact image hash: {sku}')
    checks = review['verification']
    if (not isinstance(checks, dict)
            or set(checks) != {'brand', 'expression', 'size', 'packaging', 'upc', 'visual'}
            or any(value is not True for value in checks.values())):
        raise ValueError(f'Repaired image review requires every identity check: {sku}')
    evidence = review['evidence']
    kinds = {'manufacturer', 'exact_upc_listing', 'inspected_package_photo'}
    if (not isinstance(evidence, list) or not evidence
            or any(not isinstance(item, dict) or not {'kind', 'url'}.issubset(item)
                   or set(item) - {'kind', 'url', 'supports'} or item['kind'] not in kinds
                   or not isinstance(item['url'], str) or not re.fullmatch(r'https?://[^\s]+', item['url'])
                   or ('supports' in item and (not isinstance(item['supports'], str)
                       or PRICE_RE.search(item['supports'])
                       or re.search(r'[$€£]|(?:^|\s)(?:/Users/|/home/|file:)', item['supports'])))
                   for item in evidence)
            or {item['kind'] for item in evidence} != kinds):
        raise ValueError(f'Repaired image review requires manufacturer, UPC and inspected-photo URLs: {sku}')


def validated_name_verifications(manifest: dict | None) -> dict:
    """Keep an unchanged-name review separate from an actual correction."""
    entries = (manifest or {}).get('nameVerifications', [])
    if not isinstance(entries, list):
        raise ValueError('Name verifications must be a list')
    result = {}
    required = {'sku', 'expectedUpc', 'expectedNameSha256', 'expectedPublicName',
                'expectedLabel', 'verifiedOn', 'verification', 'evidence'}
    kinds = {'manufacturer', 'exact_upc_listing', 'inspected_package_photo'}
    for entry in entries:
        manufacturer_method = (isinstance(entry, dict)
                               and entry.get('verificationMethod') == 'manufacturer_exact_upc_record')
        entry_fields = required | {'verificationMethod', 'manufacturerRecord'} if manufacturer_method else required
        if not isinstance(entry, dict) or set(entry) != entry_fields:
            raise ValueError('Name verification contains missing, unsupported or private fields')
        sku = entry['sku']
        if not isinstance(sku, str) or not sku or sku in result:
            raise ValueError('Name verification requires a unique SKU')
        if (not isinstance(entry['expectedNameSha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', entry['expectedNameSha256'])
                or not isinstance(entry['expectedUpc'], str) or not entry['expectedUpc']):
            raise ValueError(f'Name verification requires exact source identity guards: {sku}')
        for field in ('expectedUpc', 'expectedPublicName', 'expectedLabel'):
            value = entry[field]
            if (not isinstance(value, str) or not value.strip() or PRICE_RE.search(value)
                    or re.search(r'[$€£]|(?:^|\s)(?:/Users/|/home/|file:)', value)):
                raise ValueError(f'Name verification requires safe public identity: {sku}')
        if not isinstance(entry['verifiedOn'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['verifiedOn']):
            raise ValueError(f'Name verification requires a review date: {sku}')
        checks = entry['verification']
        if (not isinstance(checks, dict) or set(checks) != {'brand', 'expression', 'upc', 'visual'}
                or any(checks[key] is not True for key in ('brand', 'expression', 'upc'))
                or checks['visual'] is not (False if manufacturer_method else True)):
            raise ValueError(f'Name verification requires every name identity check: {sku}')
        if manufacturer_method:
            validate_manufacturer_name_record(entry)
            result[sku] = entry
            continue
        evidence = entry['evidence']
        if (not isinstance(evidence, list) or not evidence
                or any(not isinstance(item, dict) or not {'kind', 'url'}.issubset(item)
                       or set(item) - {'kind', 'url', 'supports'} or item['kind'] not in kinds
                       or not isinstance(item['url'], str) or not re.fullmatch(r'https?://[^\s]+', item['url'])
                       or ('supports' in item and (not isinstance(item['supports'], str)
                           or PRICE_RE.search(item['supports'])
                           or re.search(r'[$€£]|(?:^|\s)(?:/Users/|/home/|file:)', item['supports'])))
                       for item in evidence)
                or {item['kind'] for item in evidence} != kinds):
            raise ValueError(f'Name verification requires manufacturer, UPC and inspected-photo evidence: {sku}')
        result[sku] = entry
    return result


def validated_source_reviews(manifest: dict | None) -> dict:
    if not manifest:
        return {}
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('reviews'), list):
        raise ValueError('Invalid source review manifest schema')
    result = {}
    for entry in manifest['reviews']:
        sku = entry.get('sku')
        if not isinstance(sku, str) or sku in result or entry.get('action') not in {
                'size_needs_confirmation', 'restore_named_merchandise', 'resolve_verified_identity',
                'repair_unquoted_name_commas', 'clarify_container_material'}:
            raise ValueError('Source review requires unique SKU and a supported action')
        if not isinstance(entry.get('expectedUpc'), str) or not all(
                re.fullmatch(r'[a-f0-9]{64}', entry.get(key, ''))
                for key in ('expectedNameSha256', 'expectedSourceRecordSha256')):
            raise ValueError(f'Source review requires exact original identity guards: {sku}')
        if PRICE_RE.search(entry['expectedUpc']) or BARE_DECIMAL_RE.fullmatch(entry['expectedUpc']):
            raise ValueError(f'Source review must not publish a price-bearing UPC: {sku}')
        if not isinstance(entry.get('reason'), str) or not entry['reason'].strip():
            raise ValueError(f'Source review requires a reason: {sku}')
        allowed = {'sku', 'expectedUpc', 'expectedNameSha256', 'expectedSourceRecordSha256',
                   'action', 'reason', 'reviewedOn'}
        if entry['action'] in {'size_needs_confirmation', 'restore_named_merchandise'}:
            allowed.add('identityResolution')
            if 'identityResolution' in entry:
                resolution = entry['identityResolution']
                fields = {'expectedName', 'expectedLabel', 'verifiedName', 'verifiedLabel',
                          'verification', 'evidence', 'reason'}
                if not isinstance(resolution, dict) or set(resolution) not in (fields, fields | {'groupWithPriorExpression'}):
                    raise ValueError(f'Identity resolution contains missing, unsupported or private fields: {sku}')
                if 'groupWithPriorExpression' in resolution and (
                        resolution['groupWithPriorExpression'] is not True
                        or entry['action'] != 'size_needs_confirmation'
                        or resolution['expectedName'] != resolution['verifiedName']):
                    raise ValueError(f'Grouping requires the unchanged prior product expression: {sku}')
                for field in ('expectedName', 'expectedLabel', 'verifiedName', 'verifiedLabel', 'reason'):
                    value = resolution[field]
                    if (not isinstance(value, str) or not value.strip() or PRICE_RE.search(value)
                            or re.search(r'[$€£]|(?:^|\s)(?:/Users/|/home/|file:)', value)):
                        raise ValueError(f'Identity resolution requires safe previous identity and reason: {sku}')
                if not resolution['expectedLabel'].startswith('Size needs confirmation'):
                    raise ValueError(f'Identity resolution requires an explicitly unresolved source size: {sku}')
                kinds = {'manufacturer', 'exact_upc_listing', 'inspected_package_photo'}
                evidence = resolution['evidence']
                if (not isinstance(evidence, list) or not evidence
                        or any(not isinstance(item, dict) or set(item) != {'kind', 'url', 'supports'}
                               or item['kind'] not in kinds or not isinstance(item['url'], str)
                               or not re.fullmatch(r'https?://[^\s]+', item['url'])
                               or not isinstance(item['supports'], str) or PRICE_RE.search(item['supports'])
                               or re.search(r'[$€£]|(?:^|\s)(?:/Users/|/home/|file:)', item['supports'])
                               for item in evidence)):
                    raise ValueError(f'Identity resolution requires public evidence only: {sku}')
                # Reuse the existing strict identity schema without broadening
                # its separate unspecified-size-only application path.
                check = {key: entry[key] for key in ('sku', 'expectedUpc', 'expectedNameSha256',
                                                    'expectedSourceRecordSha256', 'reason')}
                check.update(action='resolve_verified_identity', **{
                    key: resolution[key] for key in ('verifiedName', 'verifiedLabel', 'verification', 'evidence')})
                validated_source_reviews({'schemaVersion': 1, 'reviews': [check]})
        if entry['action'] == 'clarify_container_material':
            allowed |= {'containerMaterial', 'reportEvidence'}
            if entry.get('containerMaterial') not in {'glass', 'PET'}:
                raise ValueError(f'Container material must be glass or PET: {sku}')
            validated_report_evidence(entry.get('reportEvidence'), sku)
        if entry['action'] == 'repair_unquoted_name_commas':
            allowed |= {'expectedRejoinedNameSha256', 'reportEvidence', 'imageIdentityReview'}
            if not re.fullmatch(r'[a-f0-9]{64}', entry.get('expectedRejoinedNameSha256', '')):
                raise ValueError(f'CSV repair requires an exact rejoined name guard: {sku}')
            validated_report_evidence(entry.get('reportEvidence'), sku)
            if 'imageIdentityReview' in entry:
                validated_repair_image_review(entry['imageIdentityReview'], sku)
        if entry['action'] == 'restore_named_merchandise':
            allowed |= {'sanitizedSourceName', 'forceSizeNeedsConfirmation'}
            name = entry.get('sanitizedSourceName')
            if (not isinstance(name, str) or not name.strip() or PRICE_RE.search(name)
                    or re.search(r'[$€£]', name)
                    or not isinstance(entry.get('forceSizeNeedsConfirmation'), bool)):
                raise ValueError(f'Restoration requires a price-free reviewed description: {sku}')
        if entry['action'] == 'resolve_verified_identity':
            allowed |= {'verifiedName', 'verifiedLabel', 'verification', 'evidence'}
            name, label = entry.get('verifiedName'), entry.get('verifiedLabel')
            if (not isinstance(name, str) or not identifiable_name(name) or PRICE_RE.search(name)
                    or has_ambiguous_decimal(name) or not isinstance(label, str)):
                raise ValueError(f'Verified identity requires safe name and size fields: {sku}')
            dimensions = extract_dimensions(label)
            if (dimensions['text'] or len(dimensions['measures']) != 1
                    or label != dimensions['measures'][0][0]):
                raise ValueError(f'Verified identity requires one explicit canonical measurement: {sku}')
            checks = entry.get('verification', {})
            required_checks = {'brand', 'expression', 'size', 'packaging', 'upc', 'visual'}
            if set(checks) != required_checks or any(value is not True for value in checks.values()):
                raise ValueError(f'Verified identity requires all exact identity checks: {sku}')
            evidence = entry.get('evidence', [])
            if (not isinstance(evidence, list) or
                    not {'manufacturer', 'exact_upc_listing', 'inspected_package_photo'}.issubset(
                        {item.get('kind') for item in evidence if isinstance(item, dict)}) or
                    not all(isinstance(item, dict) and re.match(r'^https?://', item.get('url', ''))
                            and isinstance(item.get('supports'), str) and item['supports'].strip()
                            for item in evidence)):
                raise ValueError(f'Verified identity requires primary and inspected-package evidence: {sku}')
        if set(entry) - allowed:
            raise ValueError(f'Source review contains unsupported or private fields: {sku}')
        if PRICE_RE.search(entry['reason']) or re.search(r'[$€£]', entry['reason']):
            raise ValueError(f'Source review reason must not expose price annotations: {sku}')
        result[sku] = entry
    return result


def validated_report_evidence(evidence: object, sku: str) -> None:
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(f'Report metadata requires report evidence: {sku}')
    for item in evidence:
        if (not isinstance(item, dict) or set(item) != {'reportSha256', 'page'}
                or not isinstance(item['reportSha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', item['reportSha256'])
                or type(item['page']) is not int or item['page'] < 1):
            raise ValueError(f'Invalid report hash/page evidence: {sku}')


def validated_report_additions(manifest: dict | None) -> list[dict]:
    if manifest is None:
        return []
    if (not isinstance(manifest, dict) or set(manifest) != {'schemaVersion', 'records'}
            or manifest['schemaVersion'] != 1 or not isinstance(manifest['records'], list)):
        raise ValueError('Invalid report additions manifest schema')
    required = {'sku', 'name', 'pack', 'department', 'kind', 'sourceNameSha256',
                'evidence', 'reviewedOn', 'reason'}
    seen = set()
    for entry in manifest['records']:
        if (not isinstance(entry, dict) or not required.issubset(entry)
                or set(entry) - required - {'forceSizeNeedsConfirmation'}):
            raise ValueError('Report additions contain missing, unsupported or private fields')
        sku = entry['sku']
        if not isinstance(sku, str) or not re.fullmatch(r'-?\d+', sku) or sku in seen:
            raise ValueError('Report additions require unique string SKUs')
        seen.add(sku)
        if entry['kind'] not in {'merchandise', 'pos_control'}:
            raise ValueError(f'Invalid report record kind: {sku}')
        for field in ('name', 'pack', 'department', 'reason'):
            value = entry[field]
            if not isinstance(value, str) or PRICE_RE.search(value) or re.search(r'[$€£]', value):
                raise ValueError(f'Report {field} must be price-free text: {sku}')
            if field in {'department', 'reason'} and has_ambiguous_decimal(value):
                raise ValueError(f'Report {field} contains an ambiguous amount: {sku}')
        if not entry['name'].strip() or not entry['reason'].strip():
            raise ValueError(f'Report metadata requires a name and reason: {sku}')
        if (not isinstance(entry['sourceNameSha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', entry['sourceNameSha256'])):
            raise ValueError(f'Report metadata requires a source name hash: {sku}')
        if (not isinstance(entry['reviewedOn'], str)
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['reviewedOn'])):
            raise ValueError(f'Report metadata requires a review date: {sku}')
        if type(entry.get('forceSizeNeedsConfirmation', False)) is not bool:
            raise ValueError(f'Report size confirmation must be boolean: {sku}')
        validated_report_evidence(entry['evidence'], sku)
        if entry['kind'] == 'pos_control':
            if REPORT_POS_CONTROLS.get(sku) != entry['name'] or entry['pack']:
                raise ValueError(f'Report POS control is not an approved exact pair: {sku}')
        elif int(sku) <= 0 or sku in REPORT_POS_CONTROLS:
            raise ValueError(f'Report merchandise cannot use a POS control code: {sku}')
        # A reviewed measurement can contain a decimal. A bare decimal in any
        # public description is still an unresolved amount, never a safe price.
        for field in ('name', 'pack'):
            if has_ambiguous_decimal(extract_dimensions(entry[field])['text']):
                raise ValueError(f'Report {field} contains an ambiguous amount: {sku}')
    return manifest['records']


def repair_unquoted_name_commas(fields: list[str], review: dict) -> list[str]:
    """Rejoin only individually corroborated CSV title fragments."""
    sku = fields[0] if fields else ''
    if len(fields) <= len(EXPECTED_HEADERS):
        raise ValueError(f'CSV comma repair requires extra source fields: {sku}')
    raw_hash = hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if (fields[1], hashlib.sha256(fields[2].encode()).hexdigest(), raw_hash) != (
            review['expectedUpc'], review['expectedNameSha256'], review['expectedSourceRecordSha256']):
        raise ValueError(f'Source metadata review identity mismatch for SKU {sku}')
    recovered = fields[:2] + [','.join(fields[2:-5])] + fields[-5:]
    if (len(recovered) != len(EXPECTED_HEADERS)
            or hashlib.sha256(recovered[2].encode()).hexdigest() != review['expectedRejoinedNameSha256']):
        raise ValueError(f'Rejoined CSV name does not match reviewed report identity: {sku}')
    return recovered


def restored_description(source: dict, review: dict) -> str:
    """Allow a reviewed deletion of ambiguous metadata, never invented identity."""
    disposition, reason, _, _ = classify_record(source)
    eligible = {'Unlabelled decimal may be a price or unconfirmed measure',
                'Unresolved price/promotion text in title',
                'Unresolved price/promotion text after unit parsing'}
    if disposition != 'needs_review' or reason not in eligible:
        raise ValueError(f'Restoration is not eligible for the original source: {source["SKU"]}')
    original = iter(re.sub(r'\s', '', source['ItemName']))
    sanitized = review['sanitizedSourceName']
    if not all(any(candidate == character for candidate in original)
               for character in re.sub(r'\s', '', sanitized)):
        raise ValueError(f'Restoration must only remove source text: {source["SKU"]}')
    return sanitized


def uncertain_size_label(source: dict) -> str:
    """Keep independently stated, nonconflicting pack/container facts."""
    title = extract_dimensions(source['ItemName'])
    pack = extract_dimensions(source['PackName'])
    packs = set(title['packs']) | set(pack['packs'])
    containers = {value for value in (title['container'], pack['container']) if value}
    pieces = [f'{next(iter(packs))}-pack'] if len(packs) == 1 else []
    if not packs and re.match(r'^single\b', source['PackName'], re.I):
        pieces.append('Single')
    pieces.append('Size needs confirmation')
    if len(containers) == 1:
        pieces.append(next(iter(containers)))
    return ' · '.join(pieces)


def isolate_unspecified_variants(groups: dict, records: list, flag_reasons: Counter) -> list:
    products = []
    by_sku = {record['sku']: record for record in records}
    for product in groups.values():
        variants = product['variants']
        unknown = [v for v in variants if v['label'] == 'Size not specified']
        identities = {v['upc'] or v['sku'] for v in variants}
        if unknown and len(identities) > 1:
            specified = [v for v in variants if v['label'] != 'Size not specified']
            if specified:
                products.append({**product, 'variants': specified})
            separated = {}
            for variant in unknown:
                identity = variant['upc'] or variant['sku']
                identifier = 'p-' + hashlib.sha256((product['id'] + '\0unconfirmed\0' + identity).encode()).hexdigest()[:16]
                separated.setdefault(identity, {**product, 'id': identifier, 'variants': []})['variants'].append(variant)
                record = by_sku[variant['sku']]
                flag = 'Same title has distinct UPCs without size/pack metadata; expression identity unresolved and grouping isolated'
                record['flags'].append(flag)
                record['productId'] = identifier
                flag_reasons[flag] += 1
            products.extend(separated.values())
        else:
            products.append(product)
    return products


def build(source_bytes: bytes, source_date: str = '2026-02', image_manifest: dict | None = None,
          name_manifest: dict | None = None, source_reviews_manifest: dict | None = None,
          report_additions_manifest: dict | None = None,
          category_reviews_manifest: dict | None = None,
          public_name_reviews_manifest: dict | None = None) -> tuple[dict, dict]:
    reader = csv.reader(io.StringIO(source_bytes.decode('utf-8-sig'), newline=''))
    headers = next(reader)
    if headers != EXPECTED_HEADERS:
        raise ValueError(f'Unexpected CSV schema: {headers!r}')
    groups = {}
    corrections, identity_reviews = validated_name_overrides(name_manifest)
    name_verifications = validated_name_verifications(name_manifest)
    source_reviews = validated_source_reviews(source_reviews_manifest)
    report_additions = validated_report_additions(report_additions_manifest)
    category_reviews = validated_category_reviews(category_reviews_manifest)
    public_name_reviews = validated_public_name_reviews(public_name_reviews_manifest)
    applied_category_reviews = set()
    applied_public_name_reviews = set()
    master = []
    dispositions = Counter()
    flag_reasons = Counter()
    seen_skus = set()
    for row_number, fields in enumerate(reader, 1):
        sku = fields[0] if fields else ''
        if not sku or sku in seen_skus:
            raise ValueError(f'Missing or duplicate SKU at source row {row_number}: {sku!r}')
        seen_skus.add(sku)
        record = {'sourceRow': row_number, 'csvLine': reader.line_num,
                  'sku': sku, 'rawFields': fields}
        review = source_reviews.get(sku)
        repairing = bool(review and review['action'] == 'repair_unquoted_name_commas')
        recovered_fields = repair_unquoted_name_commas(fields, review) if repairing else fields
        if len(recovered_fields) != len(headers):
            if review:
                raise ValueError(f'Source review cannot apply to an unrepaired malformed record: {sku}')
            record.update(disposition='needs_review',
                          reason=f'Malformed CSV record: expected 8 columns, received {len(fields)}; no inferred repair',
                          flags=[])
        else:
            source = dict(zip(headers, recovered_fields))
            source['sourceRow'] = row_number
            if repairing:
                record['reconstructedFields'] = recovered_fields
                record['originalMalformedNameFragments'] = fields[2:-5]
            adjusted_source = dict(source)
            override_flags = []
            restoring = bool(review and review['action'] == 'restore_named_merchandise')
            resolving = bool(review and review['action'] == 'resolve_verified_identity')
            if review:
                raw_hash = hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
                if not repairing and (source['MAINUPC'], hashlib.sha256(source['ItemName'].encode()).hexdigest(), raw_hash) != (
                        review['expectedUpc'], review['expectedNameSha256'], review['expectedSourceRecordSha256']):
                    raise ValueError(f'Source metadata review identity mismatch for SKU {sku}')
                record['sourceReview'] = review
                if restoring:
                    adjusted_source['ItemName'] = restored_description(source, review)
            for overrides in (corrections, identity_reviews, name_verifications):
                override = overrides.get(sku)
                if override and (override['expectedNameSha256'], override['expectedUpc']) != (
                        hashlib.sha256(source['ItemName'].encode()).hexdigest(), source['MAINUPC']):
                    raise ValueError(f'Name override identity mismatch for SKU {sku}')
            correction = corrections.get(sku)
            manufacturer_baseline = None
            if correction and correction.get('scope') == MANUFACTURER_NAME_SCOPE:
                manufacturer_baseline = classify_record(adjusted_source)[2]
                if not manufacturer_baseline:
                    raise ValueError(f'Manufacturer name-only review cannot restore unidentified merchandise: {sku}')
            if correction:
                if source['ItemName'].count(correction['replaceTextFrom']) != 1:
                    raise ValueError(f'Spelling fragment does not match exactly once: {sku}')
                if adjusted_source['ItemName'].count(correction['replaceTextFrom']) != 1:
                    raise ValueError(f'Spelling correction and restoration must agree for SKU {sku}')
                adjusted_source['ItemName'] = adjusted_source['ItemName'].replace(correction['replaceTextFrom'], correction['replaceTextTo'], 1)
                record['nameCorrection'] = correction
                record['correctedSourceName'] = source['ItemName'].replace(correction['replaceTextFrom'], correction['replaceTextTo'], 1)
                override_flags.append('Evidence-backed spelling/spacing correction applied; source spelling preserved privately')
            if sku in identity_reviews:
                identity_review = identity_reviews[sku]
                record['identityReview'] = identity_review
                if identity_review.get('action') == 'retain_flagged':
                    disposition, reason, public, flags = classify_record(adjusted_source)
                    if not public:
                        raise ValueError(f'Flagged identity requires a retainable original SKU: {sku}')
                    public['name'] += ' (identity needs confirmation)'
                    flags.append('Exact UPC and source title conflict; listing isolated pending identity confirmation')
                else:
                    disposition, reason, public, flags = 'needs_review', identity_review['reason'], None, []
            else:
                disposition, reason, public, flags = classify_record(adjusted_source)
            if manufacturer_baseline and (not public or public['variant'] != manufacturer_baseline['variant']):
                raise ValueError(f'Manufacturer name-only review changed the original variant metadata: {sku}')
            if review:
                if restoring:
                    if not public:
                        raise ValueError(f'Reviewed restoration did not produce safe merchandise: {sku}')
                    record['sanitizedSourceName'] = adjusted_source['ItemName']
                    flags.append('Guarded named merchandise restored; original metadata preserved privately')
                    flags.append('Restored SKU grouping isolated; image requires its own exact identity review')
                    if review['forceSizeNeedsConfirmation']:
                        public['variant']['label'] = uncertain_size_label(adjusted_source)
                elif resolving:
                    if not public or public['variant']['label'] != 'Size not specified':
                        raise ValueError(f'Verified identity cannot silently replace a stated source size: {sku}')
                    if not public['variant']['upc'] or public['variant']['upc'] != review['expectedUpc']:
                        raise ValueError(f'Verified identity requires a retained public UPC matching the source guard: {sku}')
                    record['previousPublicIdentity'] = {'name': public['name'], 'label': public['variant']['label']}
                    public['name'] = review['verifiedName']
                    public['variant']['label'] = review['verifiedLabel']
                    flags.append('Exact SKU identity resolved with manufacturer, UPC listing and inspected package evidence')
                elif repairing:
                    if public:
                        # Size removal can leave a dangling comma from the
                        # corroborated CSV title; only normalize punctuation.
                        public['name'] = re.sub(r'\s*,\s*', ', ', public['name']).strip(' ,')
                    flags.append('Guarded unquoted CSV name commas repaired using exact SKU report evidence; raw fields preserved privately')
                    flags.append('Repaired SKU grouping isolated; image requires its own exact identity review')
                elif review['action'] == 'clarify_container_material':
                    has_measure = any(extract_dimensions(adjusted_source[field])['measures']
                                      for field in ('ItemName', 'PackName'))
                    if not public or public['variant']['label'].startswith('Size ') or not has_measure:
                        raise ValueError(f'Container clarification requires a retained variant with a confirmed source size: {sku}')
                    label = public['variant']['label']
                    if re.search(r'\b(?:cans|kegs|glass|PET)\b', label, re.I):
                        raise ValueError(f'Container clarification conflicts with an existing container: {sku}')
                    public['variant']['label'] = re.sub(r'(?:^| · )bottles$', '', label) + ' · ' + review['containerMaterial'] + ' bottles'
                    flags.append('Guarded report evidence clarifies container material; original source metadata preserved privately')
                else:
                    flags.append('Explicit source-size review: ' + review['reason'])
                if public and review['action'] == 'size_needs_confirmation':
                    public['variant']['label'] = 'Size needs confirmation'
                resolution = review.get('identityResolution')
                if resolution:
                    if (not public
                            or (public['name'], public['variant']['label'])
                            != (resolution['expectedName'], resolution['expectedLabel'])):
                        raise ValueError(f'Identity resolution previous catalog identity mismatch: {sku}')
                    if not public['variant']['upc'] or public['variant']['upc'] != review['expectedUpc']:
                        raise ValueError(f'Identity resolution requires a retained public UPC matching the source guard: {sku}')
                    record['previousPublicIdentity'] = {'name': public['name'], 'label': public['variant']['label']}
                    public['name'] = resolution['verifiedName']
                    public['variant']['label'] = resolution['verifiedLabel']
                    flags.append('Previously unresolved source identity explicitly reconciled using manufacturer, exact UPC and inspected-package evidence; original fields and prior review preserved')
            if correction and correction.get('scope') == MANUFACTURER_NAME_SCOPE:
                if (not public or (public['name'], public['variant']['label'], public['variant']['upc']) !=
                        (correction['verifiedPublicName'], correction['expectedLabel'], correction['expectedUpc'])):
                    raise ValueError(f'Manufacturer name correction changed a guarded variant identity: {sku}')
                flags.append('Manufacturer exact-UPC record checked the name only; this check does not verify size, photography or current stock')
            category_review = category_reviews.get(sku)
            if category_review:
                apply_category_review(public, source, fields, category_review)
                record['categoryReview'] = category_review
                applied_category_reviews.add(sku)
                flags.append('Guarded shopping category corrected; original department preserved')
            public_name_review = public_name_reviews.get(sku)
            if public_name_review:
                if sku in identity_reviews:
                    raise ValueError(f'Public-name review cannot override an unresolved identity conflict: {sku}')
                apply_public_name_review(public, source, fields, public_name_review)
                record['publicNameCorrection'] = public_name_review
                applied_public_name_reviews.add(sku)
                flags.append('Guarded exact-UPC public title correction applied; original source name and variant metadata preserved')
            flags = override_flags + flags
            record.update(source=source, disposition=disposition, reason=reason, flags=flags)
            flag_reasons.update(flags)
            if public:
                # Exact normalized expression and department only. No fuzzy
                # matching, synonym mapping, or deletion of brand/age/proof.
                key = (public['category'], public['department'], public['name'].casefold())
                identity_resolution = review.get('identityResolution') if review else None
                if restoring or resolving or repairing or (sku in identity_reviews) or (identity_resolution and not identity_resolution.get('groupWithPriorExpression')):
                    key += ('restored-source-sku', sku)
                # Exact source title says regular Silver, but the first-100 image
                # audit found UPC-specific evidence of an Artist Series package.
                # Isolate this source SKU without inventing a new product name.
                if sku == '109' and source['MAINUPC'] == '811538010412':
                    key += ('identity-review-sku-109',)
                    identity_flag = 'UPC-specific image research suggests Artist Series packaging; grouping isolated pending identity confirmation'
                    record['flags'].append(identity_flag)
                    flag_reasons[identity_flag] += 1
                identifier = 'p-' + hashlib.sha256('\0'.join(key).encode()).hexdigest()[:16]
                product = groups.setdefault(key, {'id': identifier, 'name': public['name'],
                    'category': public['category'], 'department': public['department'], 'variants': []})
                product['variants'].append(public['variant'])
                record['productId'] = identifier
                record['publicVariantSku'] = sku
        dispositions[record['disposition']] += 1
        master.append(record)

    if set(source_reviews) - seen_skus:
        raise ValueError('Source review contains SKUs absent from the original source export')
    if set(category_reviews) != applied_category_reviews:
        raise ValueError('Category review requires an applicable original-source merchandise SKU')
    if set(public_name_reviews) != applied_public_name_reviews:
        raise ValueError('Public-name review requires an applicable original-source merchandise SKU')
    original_rows = len(master)
    original_dispositions = dict(dispositions)
    report_dispositions = Counter()
    for offset, entry in enumerate(report_additions, 1):
        sku = entry['sku']
        if sku in seen_skus:
            raise ValueError(f'Report addition collides with an existing source SKU: {sku}')
        seen_skus.add(sku)
        row_number = original_rows + offset
        source = {'SKU': sku, 'MAINUPC': '', 'ItemName': entry['name'],
                  'PackName': entry['pack'], 'DEPNAME': entry['department'],
                  'sourceRow': row_number}
        adjusted_source = dict(source)
        correction = corrections.get(sku)
        override_flags = []
        if sku in identity_reviews:
            raise ValueError(f'Report-origin identity reviews are not supported: {sku}')
        if correction:
            if (correction['expectedUpc'], correction['expectedNameSha256']) != ('', entry['sourceNameSha256']):
                raise ValueError(f'Report name override identity mismatch for SKU {sku}')
            if entry['kind'] != 'merchandise':
                raise ValueError(f'Report POS control cannot receive a name correction: {sku}')
            if source['ItemName'].count(correction['replaceTextFrom']) != 1:
                raise ValueError(f'Spelling fragment does not match exactly once: {sku}')
            adjusted_source['ItemName'] = source['ItemName'].replace(
                correction['replaceTextFrom'], correction['replaceTextTo'], 1)
            override_flags.append('Evidence-backed spelling/spacing correction applied; source spelling preserved privately')
        if entry['kind'] == 'pos_control':
            disposition, reason, public, flags = ('excluded_nonproduct',
                'Explicit reviewed report POS control', None, [])
        else:
            disposition, reason, public, flags = classify_record(adjusted_source)
            if public and entry.get('forceSizeNeedsConfirmation', False):
                public['variant']['label'] = uncertain_size_label(adjusted_source)
        flags = override_flags + flags
        flags.append('Report-origin SKU retained separately; UPC unknown and image blocked pending exact review')
        record = {'sourceRow': row_number, 'sku': sku, 'sourceOrigin': 'report',
                  'rawFields': [], 'source': source, 'reportReferences': entry['evidence'],
                  'reportSourceNameSha256': entry['sourceNameSha256'],
                  'reviewedReportMetadata': entry, 'disposition': disposition,
                  'reason': reason, 'flags': flags}
        if correction:
            record['nameCorrection'] = correction
            record['correctedSourceName'] = adjusted_source['ItemName']
        if public:
            key = (public['category'], public['department'], public['name'].casefold(), 'report-source-sku', sku)
            identifier = 'p-' + hashlib.sha256('\0'.join(key).encode()).hexdigest()[:16]
            groups[key] = {'id': identifier, 'name': public['name'],
                           'category': public['category'], 'department': public['department'],
                           'variants': [public['variant']]}
            record.update(productId=identifier, publicVariantSku=sku)
        flag_reasons.update(flags)
        dispositions[disposition] += 1
        report_dispositions[disposition] += 1
        master.append(record)
    if (set(corrections) | set(identity_reviews)) - seen_skus:
        raise ValueError('Name manifest contains SKUs absent from all reviewed sources')
    products = sorted(isolate_unspecified_variants(groups, master, flag_reasons),
                      key=lambda p: (p['name'].casefold(), p['id']))
    for product in products:
        product['variants'].sort(key=lambda v: v['sourceRow'])
    blocked_images = {sku for sku, review in source_reviews.items()
                      if review['action'] != 'resolve_verified_identity' and not review.get('identityResolution')}
    blocked_images.update(entry['sku'] for entry in report_additions)
    blocked_images.update(sku for sku, review in identity_reviews.items() if review.get('action') == 'retain_flagged')
    variants_by_sku = {v['sku']: (p, v) for p in products for v in p['variants']}
    records_by_sku = {record['sku']: record for record in master}
    for sku, verification in name_verifications.items():
        product, variant = variants_by_sku.get(sku, ({}, {}))
        record = records_by_sku.get(sku, {})
        if (not record.get('source') or record.get('sourceOrigin') == 'report'
                or (verification['expectedUpc'], verification['expectedPublicName'], verification['expectedLabel'])
                != (variant.get('upc'), product.get('name'), variant.get('label'))):
            raise ValueError(f'Name verification does not match a current original-source variant: {sku}')
        record['nameVerification'] = verification
    for sku, source_review in source_reviews.items():
        image_review = source_review.get('imageIdentityReview')
        if image_review is None:
            continue
        entries = image_manifest.get('images', []) if isinstance(image_manifest, dict) else []
        if not isinstance(entries, list):
            entries = []
        matches = [entry for entry in entries if isinstance(entry, dict) and entry.get('sku') == sku]
        if len(matches) != 1:
            raise ValueError(f'Repaired image review requires exactly one corresponding image: {sku}')
        product, variant = variants_by_sku.get(sku, ({}, {}))
        if (not variant.get('upc') or variant.get('label', '').startswith('Size ')
                or not extract_dimensions(variant.get('label', ''))['measures']):
            raise ValueError(f'Repaired image review requires retained identity with known UPC and size: {sku}')
        expected = (source_review['expectedUpc'], image_review['expectedName'], image_review['expectedLabel'])
        if expected != (variant['upc'], product['name'], variant['label']):
            raise ValueError(f'Repaired image review identity mismatch for SKU {sku}')
        entry = matches[0]
        if (entry.get('expectedUpc'), entry.get('expectedName'), entry.get('expectedLabel'), entry.get('sha256')) != (
                *expected, image_review['expectedImageSha256']):
            raise ValueError(f'Repaired image manifest does not match exact review: {sku}')
        blocked_images.remove(sku)
    verified_images = apply_verified_images(products, image_manifest, blocked_images)
    categories = []
    for category_id, (label, departments) in CATEGORY_DEPARTMENTS.items():
        # A reviewed item can shop in a different category while retaining its
        # original department. Every actual pair must remain filterable.
        populated = {p['department'] for p in products if p['category'] == category_id}
        category_departments = [d for d in departments if d in populated]
        category_departments += sorted(populated - set(category_departments))
        if category_departments:
            categories.append({'id': category_id, 'label': label, 'departments': [
                {'id': d, 'label': DEPARTMENT_LABELS.get(d, d), 'sourceLabel': d}
                for d in category_departments]})
    flagged_retained = sum(r['disposition'] == 'retained_variant' and bool(r['flags']) for r in master)
    counts = {'sourceRows': len(master), 'sourceSkus': len(seen_skus),
              'originalSourceRows': original_rows, 'reportSourceRows': len(report_additions),
              'publicVariants': dispositions['retained_variant'], 'products': len(products),
              'needsReview': dispositions['needs_review'],
              'excludedNonproducts': dispositions['excluded_nonproduct'],
              'flaggedPublicVariants': flagged_retained}
    catalog = {'schemaVersion': 1, 'sourceDate': source_date, 'counts': counts,
               'categories': categories, 'products': products}
    audit = {'sourceSha256': hashlib.sha256(source_bytes).hexdigest(), 'counts': counts,
             'sourceDispositions': dict(dispositions), 'reviewFlagReasons': dict(flag_reasons),
             'originalSourceDispositions': original_dispositions,
             'reportSourceDispositions': dict(report_dispositions),
             'verifiedImages': verified_images, 'nameCorrections': len(corrections),
             'categoryReviews': len(applied_category_reviews),
             'publicNameCorrections': len(applied_public_name_reviews), 'records': master}
    assert sum(dispositions.values()) == len(master)
    return catalog, audit


def name_audit(catalog: dict, audit: dict) -> dict:
    """Scan every source row; an automated scan is NOT external verification."""
    by_sku = {v['sku']: p['name'] for p in catalog['products'] for v in p['variants']}
    candidates = {
        'chocolte': 'chocolate', 'strawbery': 'strawberry', 'pineaple': 'pineapple',
        'rasberry': 'raspberry', 'rosted': 'roasted', 'margatita': 'margarita',
        'fettu8ccini': 'fettuccini', 'feista': 'fiesta', 'chiken': 'chicken',
        'razberry': 'raspberry', 'unfiltred': 'unfiltered', 'sliver': 'silver',
        'jalapen': 'jalapeno', 'choclate': 'chocolate', 'strawbary': 'strawberry',
        'coocnut': 'coconut', 'vokda': 'vodka', 'orginal': 'original',
        'passionnfruit': 'passionfruit', 'punh': 'punch', 'chesse': 'cheese',
        'cheedar': 'cheddar', 'fuegd': 'fuego', 'ertra': 'extra', 'un sweet': 'unsweet',
    }
    rows = []
    for record in audit['records']:
        source = record.get('source', {})
        original = source.get('ItemName')
        if original is None:
            original = record['rawFields'][2] if len(record['rawFields']) > 2 else ''
        scanned_name = record.get('sanitizedSourceName', record.get('correctedSourceName', original))
        issues = []
        suggestions = []
        for typo, suggested in candidates.items():
            if re.search(r'(?<![A-Za-z])' + re.escape(typo) + r'(?![A-Za-z])', scanned_name, re.I):
                issues.append('Possible typo requires product-specific verification: ' + typo)
                suggestions.append({'from': typo, 'to': suggested, 'status': 'candidate_only_not_applied'})
        if re.search(r'[A-Za-z]\d+[A-Za-z]', scanned_name) and not re.search(r'\b7UP\b', scanned_name, re.I):
            issues.append('Joined letters/numbers checked; may be a measurement or distinguishing brand token')
        if re.search(r'[a-z][A-Z]', scanned_name):
            issues.append('Joined words or mixed casing checked; no automatic title rewriting')
        if re.search(r'\b(?:vod|marg|pomeg|frt|strw|rasp|blkbry|reg|mosc)\b', scanned_name, re.I):
            issues.append('Abbreviated expression requires confirmation before expansion')
        if re.search(r'MIKE N LIKE|TAYLORS CANDY|NOW\s*&\s*LATER', scanned_name, re.I):
            issues.append('Brand-level description may omit flavor; different UPCs must not imply matching products')
        if 'BELVEDERE INTENSE VOD100' in scanned_name.upper():
            issues.append('Source combines Intense/Unfiltered/100 proof wording; exact expression unresolved')
        issues.extend(record['flags'])
        if record['disposition'] != 'retained_variant':
            issues.append(record['reason'])
        correction = record.get('nameCorrection')
        public_name_correction = record.get('publicNameCorrection')
        verification = record.get('nameVerification')
        evidence = (correction['evidence'] if correction else
                    public_name_correction['evidence'] if public_name_correction else
                    record.get('identityReview', {}).get('evidence', []))
        if record.get('identityReview') and (correction or public_name_correction):
            evidence = evidence + record['identityReview']['evidence']
        if verification:
            evidence = evidence + verification['evidence']
        resolution = record.get('sourceReview', {})
        identity_resolution = resolution.get('identityResolution')
        resolved_identity = resolution.get('action') == 'resolve_verified_identity' or bool(identity_resolution)
        if resolved_identity:
            evidence = evidence + (identity_resolution or resolution)['evidence']
        outcome = ('evidence_backed_identity_resolved' if resolved_identity else
                   'identity_conflict_needs_review' if record.get('identityReview') else
                   'evidence_backed_public_title_correction' if public_name_correction else
                   'evidence_backed_correction_applied' if correction else
                   'evidence_backed_name_verified_unchanged' if verification else
                   'scanned_not_externally_verified')
        rows.append({'sourceRow': record['sourceRow'], 'sku': record['sku'],
                     'originalName': original, 'correctedSourceName': record.get('correctedSourceName', original),
                     'publicName': by_sku.get(record['sku']), 'publicStatus': record['disposition'],
                     'issues': issues, 'suggestedCorrections': suggestions, 'evidence': evidence,
                     'outcome': outcome,
                     'verifiedScope': 'product_name_identity_and_variant' if resolved_identity else
                        'exact_upc_public_title_identity' if public_name_correction else
                        MANUFACTURER_NAME_SCOPE if verification and verification.get('verificationMethod') == 'manufacturer_exact_upc_record' else
                        'exact_upc_name_identity' if verification else
                        (correction['scope'] if correction else None)})
    return {'schemaVersion': 1, 'sourceSha256': audit['sourceSha256'],
            'summary': {'sourceRowsScanned': len(rows), 'rowsWithIssues': sum(bool(r['issues']) for r in rows),
                        'evidenceBackedCorrections': sum(bool(r.get('nameCorrection')) for r in audit['records']),
                        'publicTitleCorrections': sum(bool(r.get('publicNameCorrection')) for r in audit['records']),
                        'nameVerificationRecords': sum(bool(r.get('nameVerification')) for r in audit['records']),
                        'identityConflicts': sum(bool(r.get('identityReview')) for r in audit['records']),
                        'exactIdentitiesResolved': sum(r['outcome'] == 'evidence_backed_identity_resolved' for r in rows),
                        'fullyVerifiedNames': sum(r['verifiedScope'] in {'product_name_spelling', 'exact_upc_name_identity', 'product_name_identity_and_variant', MANUFACTURER_NAME_SCOPE, 'exact_upc_public_title_identity'} and r['outcome'] != 'identity_conflict_needs_review' for r in rows),
                        'manufacturerRecordVerifiedNames': sum(r['verifiedScope'] == MANUFACTURER_NAME_SCOPE for r in rows),
                        'scopeNote': 'All rows scanned for specified risks. Only listed spelling corrections, public-title corrections, unchanged-name verifications and exact identity resolutions were externally checked within their stated scopes; public-title corrections preserve source and variant metadata. Remaining names are not verified. Manufacturer-record name checks are counted separately and do not verify photographs, package sizes or current stock.'},
            'records': rows}


def render_image_credits(catalog: dict) -> str:
    """Keep required per-photo attribution on a separate public page."""
    items = []
    for product in catalog['products']:
        for variant in product['variants']:
            image = variant.get('image')
            if not image:
                continue
            direct_reference = is_direct_reference_image(image, variant['sku'])
            for key in ('sourcePage',) + (() if direct_reference else ('licenseUrl',)):
                if not re.fullmatch(r'https?://[^\s]+', image[key]):
                    raise ValueError(f'Invalid image credit link for SKU {variant["sku"]}')
            if not re.fullmatch(r'images/products/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)', image['src']):
                raise ValueError(f'Invalid credited image path for SKU {variant["sku"]}')
            if direct_reference:
                continue
            title = escape(product['name'] + ' — ' + variant['label'])
            items.append(
                f'<li id="sku-{escape(variant["sku"])}" data-sku="{escape(variant["sku"])}">'
                f'<h2>{title}</h2><p>{escape(image["attribution"])}</p>'
                f'<p><a href="{escape(image["src"])}">Photograph</a> · '
                f'<a href="{escape(image["sourcePage"])}" rel="noopener noreferrer">Source</a> · '
                f'<a href="{escape(image["licenseUrl"])}" rel="noopener noreferrer">{escape(image["license"])}</a>'
                '</p></li>')
    return '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#181b19">
  <title>Image credits — Quick Stop Food &amp; Liquor</title>
  <link rel="icon" href="images/logo.png" type="image/png">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <a class="skip-link" href="#credits">Skip to image credits</a>
  <header class="site-header"><div class="header-inner shell">
    <a class="brand" href="./" aria-label="Quick Stop Food &amp; Liquor home">
      <img id="logo" src="images/logo.png" alt="Quick Stop Food &amp; Liquor" width="108" height="108">
      <span class="brand-copy"><strong>QUICK STOP</strong><span>FOOD &amp; LIQUOR</span></span>
    </a><a class="header-link" href="./">Browse the catalog <span aria-hidden="true">↗</span></a>
  </div></header>
  <main id="credits" class="credits-page shell" tabindex="-1">
    <h1>Image credits</h1>
    <p class="credits-intro">Credits and license details for the product photographs used in this catalog.</p>
    <ul class="credits-list">
''' + '\n'.join(items) + '''
    </ul>
    <a href="./">Back to catalog</a>
  </main>
</body>
</html>
'''


def safe_private_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError('Private input and output must be OUTSIDE the public repository')
    return path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--private-output', required=True, type=Path)
    parser.add_argument('--source-date', default='2026-02')
    parser.add_argument('--output', type=Path, default=ROOT / 'catalog.json')
    parser.add_argument('--images', type=Path, default=ROOT / 'data/image-manifest.json')
    parser.add_argument('--names', type=Path, default=ROOT / 'data/name-corrections.json')
    parser.add_argument('--source-reviews', type=Path, default=ROOT / 'data/source-reviews.json')
    parser.add_argument('--report-additions', type=Path, default=ROOT / 'data/report-additions.json')
    parser.add_argument('--category-reviews', type=Path, default=ROOT / 'data/category-reviews.json')
    parser.add_argument('--public-name-reviews', type=Path, default=DEFAULT_PUBLIC_NAME_REVIEWS,
                        help='Privacy-safe exact-UPC title review manifest (default: data/public-name-reviews.json)')
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    source_path = safe_private_path(args.input)
    private_output = safe_private_path(args.private_output)
    source_bytes = source_path.read_bytes()
    image_manifest = json.loads(args.images.read_text()) if args.images.exists() else None
    name_manifest = json.loads(args.names.read_text()) if args.names.exists() else None
    source_reviews = json.loads(args.source_reviews.read_text()) if args.source_reviews.exists() else None
    report_additions = json.loads(args.report_additions.read_text()) if args.report_additions.exists() else None
    category_reviews = json.loads(args.category_reviews.read_text()) if args.category_reviews.exists() else None
    public_name_reviews = (json.loads(args.public_name_reviews.read_text())
                           if args.public_name_reviews.exists() else None)
    catalog, audit = build(source_bytes, args.source_date, image_manifest, name_manifest, source_reviews,
                           report_additions, category_reviews, public_name_reviews)
    private_output.mkdir(parents=True, exist_ok=True, mode=0o700)
    (private_output / 'master-reconciliation.json').write_text(json.dumps(audit, indent=2) + '\n')
    (private_output / 'review-queue.json').write_text(json.dumps([
        r for r in audit['records'] if r['disposition'] == 'needs_review' or r['flags']], indent=2) + '\n')
    names = name_audit(catalog, audit)
    (private_output / 'all-names-audit.json').write_text(json.dumps(names, indent=2) + '\n')
    (private_output / 'name-audit-summary.json').write_text(json.dumps(names['summary'], indent=2) + '\n')
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, separators=(',', ':')) + '\n')
    (args.output.parent / 'image-credits.html').write_text(render_image_credits(catalog))
    summary = {'schemaVersion': 1, 'sourceDate': args.source_date,
               'sourceSha256': audit['sourceSha256'], 'counts': catalog['counts'],
               'sourceDispositions': audit['sourceDispositions'],
               'categoryReviews': audit['categoryReviews'],
               'publicNameCorrections': audit['publicNameCorrections'],
               'nameAudit': names['summary'],
               'imageStatus': {'verifiedVariants': audit['verifiedImages'],
                               'unverifiedVariants': catalog['counts']['publicVariants'] - audit['verifiedImages']}}
    (args.output.parent / 'data-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    if source_path.read_bytes() != source_bytes:
        raise RuntimeError('Source CSV changed while building')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
