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
import io
import json
import os
import re
from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_NAME_REVIEWS = ROOT / 'data/public-name-reviews.json'
SOURCE_RECORD_NAME_IDENTITY = 'original_source_record_name_only'
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
# These are intentionally separate from the source parser.  The source export
# uses forms such as ``6pk``; a reviewed public label uses the canonical
# human-readable form below.  Do not broaden PACK_RE just to parse a reviewed
# label, because that would change how every source row is classified.
INCOMPLETE_PACK_CONTAINER_LABEL_RE = re.compile(
    r'^(?P<pack>[1-9]\d*)-pack(?: · (?P<container>cans|bottles))?$')
VERIFIED_PACK_CONTAINER_LABEL_RE = re.compile(
    r'^(?P<pack>[1-9]\d*)-pack · (?P<measure>.+) · (?P<container>cans|bottles)$')
CANONICAL_OUNCE_LABEL_RE = re.compile(r'^(?P<number>(?:0|[1-9]\d*)(?:\.\d+)?) oz$')
CANONICAL_FLUID_OUNCE_LABEL_RE = re.compile(r'^(?P<number>(?:0|[1-9]\d*)(?:\.\d+)?) fl oz$')
FLUID_OUNCE_RE = re.compile(
    r'(?<![\d./])(?P<number>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?|\.\d+)\s*'
    r'fl\.?\s*oz\.?(?![A-Za-z])', re.I)
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
    'The Coca-Cola Company': {'smartlabel.coca-colaproductfacts.com'},
}
MANUFACTURER_NAME_SCOPE = 'manufacturer_exact_upc_name_identity'
NAME_STRENGTH_RE = re.compile(r'\d+(?:\.\d+)?\s*(?:%\s*(?:ABV\b|A/C\b)?|ABV\b|A/C\b|PROOF\b)', re.I)

PRIVATE_REVIEW_EVIDENCE_KIND = 'quickstop-private-review-evidence'
FOOD_FLUID_OUNCE_METHOD = 'food_single_bottle_fluid_ounce'
PRINTED_FOOD_UNIT_METHOD = 'printed_barcode_food_net_content'
NAME_ONLY_RESTORATION_METHOD = 'exact_upc_guarded_name_restoration'
NUMERIC_NAME_RESTORATION_METHOD = 'exact_literal_gtin_numeric_name_only'
NUMERIC_CONFLICT_IDENTITY_METHOD = 'main_gtin_declared_pack_numeric_conflict'

# This authority's .com host is explicit evidence, not a domain-role guess.
# Additional authorities require their own reviewed publisher/host binding.
PACK_TOTAL_VOLUME_AUTHORITIES = {
    'Maine Spirits': {'mainespirits.com', 'www.mainespirits.com'},
}


def review_evidence_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def expand_private_review_evidence(value: object) -> object:
    """Restore hash-bound review evidence before applying the original validators.

    Public review guards remain inspectable; origin records live outside the
    repository. A reference never skips UPC, source, scope or image validation.
    """
    ledger = None

    def expand(node):
        nonlocal ledger
        if isinstance(node, list):
            return [expand(item) for item in node]
        if not isinstance(node, dict):
            return node
        if 'privateEvidenceSha256' not in node:
            return {key: expand(item) for key, item in node.items()}
        digest = node.get('privateEvidenceSha256')
        if (set(node) != {'privateEvidenceSha256'} or not isinstance(digest, str)
                or not re.fullmatch(r'[a-f0-9]{64}', digest)):
            raise ValueError('Invalid private review evidence reference')
        if ledger is None:
            filename = os.environ.get('QUICKSTOP_PRIVATE_REVIEW_EVIDENCE')
            if not filename:
                raise ValueError('Private review references require QUICKSTOP_PRIVATE_REVIEW_EVIDENCE')
            path = safe_private_path(Path(filename))
            try:
                ledger = json.loads(path.read_text())
            except (OSError, ValueError) as error:
                raise ValueError('Private review evidence cannot be read') from error
            if (not isinstance(ledger, dict) or set(ledger) != {'schemaVersion', 'kind', 'records'}
                    or ledger.get('schemaVersion') != 1
                    or ledger.get('kind') != PRIVATE_REVIEW_EVIDENCE_KIND
                    or not isinstance(ledger.get('records'), dict)):
                raise ValueError('Invalid private review evidence ledger')
        record = ledger['records'].get(digest)
        if not isinstance(record, list) or not record or review_evidence_sha256(record) != digest:
            raise ValueError('Private review evidence is missing or does not match its hash')
        # Evidence records are plain JSON, never another layer of references.
        if 'privateEvidenceSha256' in json.dumps(record):
            raise ValueError('Nested private review evidence references are forbidden')
        return json.loads(json.dumps(record))

    return expand(value)


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


def canonical_ounce_label_value(label: object, fluid: bool = False) -> Decimal | None:
    """Return one strictly canonical ounce label's positive numeric value.

    This deliberately does not use ``measurement`` for fluid ounces.  The
    catalog's source parser keeps legacy ``fl oz`` output as ``oz`` today;
    reviewed fluid labels must therefore be validated without changing global
    source parsing or silently reclassifying unrelated variants.
    """
    match = (CANONICAL_FLUID_OUNCE_LABEL_RE if fluid else CANONICAL_OUNCE_LABEL_RE).fullmatch(
        label if isinstance(label, str) else '')
    if not match:
        return None
    value = parse_number(match['number'])
    if value <= 0 or decimal_text(match['number']) != match['number']:
        return None
    return value


def parse_incomplete_pack_container_label(label: object) -> dict | None:
    match = INCOMPLETE_PACK_CONTAINER_LABEL_RE.fullmatch(label if isinstance(label, str) else '')
    if not match:
        return None
    return {'pack': int(match['pack']), 'container': match['container'] or ''}


def parse_verified_pack_container_label(label: object) -> dict | None:
    """Parse exactly ``N-pack · measurement · cans|bottles`` review labels."""
    match = VERIFIED_PACK_CONTAINER_LABEL_RE.fullmatch(label if isinstance(label, str) else '')
    if not match:
        return None
    measure = match['measure']
    dimensions = extract_dimensions(measure)
    if (dimensions['text'] or dimensions['packs'] or dimensions['container']
            or len(dimensions['measures']) != 1
            or measure != dimensions['measures'][0][0]):
        return None
    return {'pack': int(match['pack']), 'measure': dimensions['measures'][0],
            'container': match['container']}


def parse_reviewed_pack_volume(label: object, require_container: bool = False) -> dict | None:
    """Parse this review's L/mL labels without broadening source parsing."""
    match = re.fullmatch(
        r'(?P<pack>[1-9]\d*)-pack · (?P<number>(?:0|[1-9]\d*)(?:\.\d+)?) '
        r'(?P<unit>mL|L)(?: · (?P<container>cans|bottles|cartons))?',
        label if isinstance(label, str) else '')
    if not match or (require_container and not match['container']):
        return None
    value = Decimal(match['number'])
    if value <= 0 or decimal_text(match['number']) != match['number']:
        return None
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits) + 4)
        milliliters = value * (1000 if match['unit'] == 'L' else 1)
    return {'pack': int(match['pack']), 'mL': milliliters,
            'measure': match['number'] + ' ' + match['unit'],
            'container': match['container'] or ''}


def reviewed_volume_milliliters(text: object) -> Decimal | None:
    """Preserve a source/printed L or mL spelling, comparing only its volume."""
    match = re.fullmatch(r'(?P<number>(?:0|[1-9]\d*)(?:\.\d+)?)\s*(?P<unit>mL|L)',
                         text if isinstance(text, str) else '', re.I)
    if not match:
        return None
    value = Decimal(match['number'])
    if value <= 0:
        return None
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits) + 4)
        return value * (1000 if match['unit'].lower() == 'l' else 1)


def explicit_source_fluid_ounce_value(source: dict) -> Decimal | None:
    """Return the one explicit liquid-ounce value stated in raw source fields."""
    values = []
    for field in ('ItemName', 'PackName'):
        value = source.get(field, '')
        if not isinstance(value, str):
            return None
        values.extend(parse_number(match['number']) for match in FLUID_OUNCE_RE.finditer(value))
    if len(values) != 1 or values[0] <= 0:
        return None
    return values[0]


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


def classify_record(record: dict, *, defer_specific_name: bool = False) -> tuple[str, str, dict | None, list[str]]:
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
    # The build may defer only this check for a separately validated, coupled
    # name-only restoration/title review. Its guarded intermediate is never
    # published; all other classification checks retain their normal order.
    if not identifiable_name(name) and not defer_specific_name:
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
    """Require the one public photo policy: private-backed direct reference."""
    if image['usageBasis'] != 'user_authorized_direct_reference':
        raise ValueError(f'Unsupported image usage basis: {sku}')
    if image.get('attributionRequired') is not False:
        raise ValueError(f'Direct reference image requires explicit attribution policy: {sku}')
    if any(field in image for field in ('sourcePage', 'sourceImageUrl', 'license', 'licenseUrl', 'attribution')):
        raise ValueError(f'Public image record must not contain provenance or attribution metadata: {sku}')
    return True


DIRECT_REFERENCE_PUBLIC_FIELDS = frozenset({
    'sku', 'expectedUpc', 'expectedName', 'expectedLabel', 'src', 'alt',
    'sha256', 'verifiedOn', 'verification', 'usageBasis', 'attributionRequired',
})
PRIVATE_DIRECT_IMAGE_EVIDENCE_FIELDS = frozenset({
    *DIRECT_REFERENCE_PUBLIC_FIELDS, 'sourcePage', 'sourceImageUrl',
})
PRIVATE_DIRECT_IMAGE_EVIDENCE_KIND = 'quickstop-direct-reference-image-evidence'


def validate_private_direct_image_evidence(image_manifest: dict | None,
                                           evidence_manifest: dict | None) -> dict:
    """Bind public direct-image assignments to private provenance evidence.

    The public manifest deliberately contains only the SKU identity, local asset
    and quality guards required to publish a card. Source URLs for
    user-authorized direct references belong in an explicitly supplied private
    input, never in a browser-delivered catalog or manifest.
    """
    entries = image_manifest.get('images', []) if isinstance(image_manifest, dict) else []
    if not isinstance(entries, list):
        return {}
    direct_entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Image manifest entries must be objects')
        sku = entry.get('sku')
        if set(entry) != DIRECT_REFERENCE_PUBLIC_FIELDS:
            raise ValueError(
                'Public image manifest must not contain provenance URLs or unsupported fields: '
                + str(sku))
        direct_entries.append(entry)
    if not direct_entries:
        if evidence_manifest is not None:
            if (not isinstance(evidence_manifest, dict)
                    or evidence_manifest.get('schemaVersion') != 1
                    or evidence_manifest.get('kind') != PRIVATE_DIRECT_IMAGE_EVIDENCE_KIND
                    or evidence_manifest.get('entries') != []):
                raise ValueError('Private direct-image evidence is stale or not applicable')
        return {}
    if evidence_manifest is None:
        raise ValueError('Direct-reference image assignments require a private evidence manifest')
    if (not isinstance(evidence_manifest, dict)
            or set(evidence_manifest) != {'schemaVersion', 'kind', 'entries'}
            or evidence_manifest.get('schemaVersion') != 1
            or evidence_manifest.get('kind') != PRIVATE_DIRECT_IMAGE_EVIDENCE_KIND
            or not isinstance(evidence_manifest.get('entries'), list)):
        raise ValueError('Invalid private direct-image evidence manifest')
    evidence_by_sku = {}
    for evidence in evidence_manifest['entries']:
        if not isinstance(evidence, dict) or set(evidence) != PRIVATE_DIRECT_IMAGE_EVIDENCE_FIELDS:
            raise ValueError('Private direct-image evidence has missing or unsupported fields')
        sku = evidence.get('sku')
        if not isinstance(sku, str) or sku in evidence_by_sku:
            raise ValueError('Private direct-image evidence SKU is missing or duplicate')
        for field in ('sourcePage', 'sourceImageUrl'):
            if not isinstance(evidence.get(field), str) or not re.fullmatch(r'https?://[^\s]+', evidence[field]):
                raise ValueError(f'Private direct-image evidence requires a valid {field}: {sku}')
        evidence_by_sku[sku] = evidence
    direct_by_sku = {entry.get('sku'): entry for entry in direct_entries}
    if set(direct_by_sku) != set(evidence_by_sku) or len(direct_by_sku) != len(direct_entries):
        raise ValueError('Private direct-image evidence does not exactly cover public assignments')
    for sku, entry in direct_by_sku.items():
        evidence = evidence_by_sku[sku]
        if any(evidence[field] != entry[field] for field in DIRECT_REFERENCE_PUBLIC_FIELDS):
            raise ValueError(f'Private direct-image evidence does not match public assignment: {sku}')
    return evidence_by_sku


def apply_verified_images(products: list, image_manifest: dict | None, blocked_skus: set | None = None,
                          private_direct_image_evidence: dict | None = None) -> int:
    if not image_manifest:
        return 0
    if image_manifest.get('schemaVersion') != 1 or not isinstance(image_manifest.get('images'), list):
        raise ValueError('Invalid image manifest schema')
    variants = {v['sku']: (p, v) for p in products for v in p['variants']}
    # A source-review block takes precedence over every later image-field
    # validation. This makes a held SKU impossible to publish even if a
    # malformed or stale image record is supplied for it.
    for entry in image_manifest['images']:
        if isinstance(entry, dict) and entry.get('sku') in (blocked_skus or set()):
            raise ValueError(f'Image blocked while source size is under explicit review: {entry["sku"]}')
    direct_evidence = validate_private_direct_image_evidence(
        image_manifest, private_direct_image_evidence)
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
        is_direct_reference_image(entry, sku)
        if sku not in direct_evidence:
            raise ValueError(f'Direct reference image lacks private provenance evidence: {sku}')
        if (not re.fullmatch(r'\d{6,14}', variant['upc'])
                or variant['label'] in {'Size not specified', 'Size needs confirmation'}):
            raise ValueError(f'Direct reference image requires a known exact UPC and size: {sku}')
        for field in ('alt', 'verifiedOn'):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f'Image manifest requires {field}: {sku}')
        if PRICE_RE.search(entry['alt']):
            raise ValueError(f'Image alt text must not contain prices: {sku}')
        if not safe_review_text(entry['alt']) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['verifiedOn']):
            raise ValueError(f'Direct reference image requires safe alt text and review date: {sku}')
        public_fields = ('src', 'alt', 'usageBasis', 'attributionRequired')
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
    manifest = expand_private_review_evidence(manifest)
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
    manifest = expand_private_review_evidence(manifest)
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


def original_source_record_public_identity(source: dict, raw_fields: list) -> dict:
    """Recompute a retained non-GTIN record without interpreting its store code."""
    if (not isinstance(raw_fields, list) or len(raw_fields) != len(EXPECTED_HEADERS)
            or any(not isinstance(value, str) for value in raw_fields)
            or not isinstance(source, dict)
            or any(source.get(key) != value for key, value in zip(EXPECTED_HEADERS, raw_fields))
            or type(source.get('sourceRow')) is not int or source['sourceRow'] < 1):
        raise ValueError('Source-record name review requires consistent original source fields')
    identifier = source['MAINUPC']
    numeric = bool(re.fullmatch(r'[0-9]{6,14}', identifier))
    if (not identifier or identifier != identifier.strip() or valid_gtin(identifier)
            or (numeric and not identifier.strip('0'))
            or (not numeric and (not re.search(r'[A-Za-z]', identifier)
                                 or not safe_review_text(identifier)))):
        raise ValueError('Source-record name review requires a literal non-GTIN inventory identifier')
    disposition, _, public, _ = classify_record(source)
    if (disposition != 'retained_variant' or not public
            or public['variant']['upc'] != (identifier if numeric else '')):
        raise ValueError('Source-record name review requires unchanged retained source merchandise')
    return public


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
    if review.get('identityBasis') == SOURCE_RECORD_NAME_IDENTITY:
        original = original_source_record_public_identity(source, raw_fields)
        if public != original:
            raise ValueError(f'Source-record name review requires the original public projection: {sku}')
    before_variant = dict(public['variant'])
    before_category = public['category']
    before_department = public['department']
    public['name'] = review['proposedPublicTitle']
    if (public['variant'] != before_variant or public['category'] != before_category
            or public['department'] != before_department):
        raise ValueError(f'Public-name review changed guarded variant metadata: {sku}')


def apply_verified_pack_identity(public: dict | None, source: dict, review: dict) -> None:
    """Enrich only a reviewed existing pack label with size/container facts."""
    sku = source['SKU']
    if not public:
        raise ValueError(f'Pack identity requires retained source merchandise: {sku}')
    expected = (review['sku'], review['expectedUpc'], review['expectedSourceRow'],
                review['expectedPublicName'], review['expectedLabel'],
                review['expectedCategory'], review['expectedDepartment'])
    current = (sku, public['variant']['upc'], source['sourceRow'], public['name'],
               public['variant']['label'], public['category'], public['department'])
    if current != expected:
        raise ValueError(f'Pack identity current public identity mismatch: {sku}')
    prior = parse_incomplete_pack_container_label(review['expectedLabel'])
    verified = parse_verified_pack_container_label(review['verifiedLabel'])
    if (not prior or not verified or prior['pack'] != verified['pack']
            or (prior['container'] and prior['container'] != verified['container'])):
        raise ValueError(f'Pack identity cannot replace stated pack/container facts: {sku}')
    before_name = public['name']
    before_category = public['category']
    before_department = public['department']
    before_variant = dict(public['variant'])
    public['variant']['label'] = review['verifiedLabel']
    if (public['name'] != before_name or public['category'] != before_category
            or public['department'] != before_department
            or {key: value for key, value in public['variant'].items() if key != 'label'}
            != {key: value for key, value in before_variant.items() if key != 'label'}):
        raise ValueError(f'Pack identity changed guarded variant metadata: {sku}')


def apply_refined_fluid_ounce_label(public: dict | None, source: dict, review: dict) -> None:
    """Refine one raw, explicit liquid-volume label without changing parsing."""
    sku = source['SKU']
    if not public:
        raise ValueError(f'Fluid-ounce identity requires retained source merchandise: {sku}')
    expected = (review['sku'], review['expectedUpc'], review['expectedSourceRow'],
                review['expectedPublicName'], review['expectedPublicLabel'],
                review['expectedCategory'], review['expectedDepartment'])
    current = (sku, public['variant']['upc'], source['sourceRow'], public['name'],
               public['variant']['label'], public['category'], public['department'])
    if current != expected:
        raise ValueError(f'Fluid-ounce identity current public identity mismatch: {sku}')
    source_value = explicit_source_fluid_ounce_value(source)
    verified_value = canonical_ounce_label_value(review['verifiedLabel'], fluid=True)
    if source_value is None or source_value != verified_value:
        raise ValueError(f'Fluid-ounce identity requires one matching explicit source liquid volume: {sku}')
    before_name = public['name']
    before_category = public['category']
    before_department = public['department']
    before_variant = dict(public['variant'])
    public['variant']['label'] = review['verifiedLabel']
    if (public['name'] != before_name or public['category'] != before_category
            or public['department'] != before_department
            or {key: value for key, value in public['variant'].items() if key != 'label'}
            != {key: value for key, value in before_variant.items() if key != 'label'}):
        raise ValueError(f'Fluid-ounce identity changed guarded variant metadata: {sku}')


def apply_verified_food_size(public: dict | None, source: dict, review: dict) -> None:
    """Resolve one explicitly-held food size without changing any other identity.

    This is deliberately narrower than the generic identity resolver.  It can
    run only after an existing ``size_needs_confirmation`` review has withheld
    a conflicting source size, and it preserves the final name, SKU, UPC,
    category, department, and every variant field except the reviewed weight.
    """
    sku = source['SKU']
    resolution = review['foodSizeResolution']
    if not public:
        raise ValueError(f'Food-size identity requires retained source merchandise: {sku}')
    expected = (review['sku'], review['expectedUpc'], resolution['expectedSourceRow'],
                resolution['expectedPublicName'], resolution['expectedPublicLabel'],
                resolution['expectedCategory'], resolution['expectedDepartment'])
    current = (sku, public['variant']['upc'], source['sourceRow'], public['name'],
               public['variant']['label'], public['category'], public['department'])
    if current != expected:
        raise ValueError(f'Food-size identity current public identity mismatch: {sku}')
    if canonical_ounce_label_value(resolution['verifiedLabel']) is None:
        raise ValueError(f'Food-size identity requires one canonical non-fluid-ounce label: {sku}')
    before_name = public['name']
    before_category = public['category']
    before_department = public['department']
    before_variant = dict(public['variant'])
    public['variant']['label'] = resolution['verifiedLabel']
    if (public['name'] != before_name or public['category'] != before_category
            or public['department'] != before_department
            or {key: value for key, value in public['variant'].items() if key != 'label'}
            != {key: value for key, value in before_variant.items() if key != 'label'}):
        raise ValueError(f'Food-size identity changed guarded variant metadata: {sku}')


def validate_reviewed_source_photo_bindings(products: list, source_reviews: dict,
                                            image_manifest: dict | None,
                                            blocked_images: set) -> dict:
    """Require scoped variant-label reviews to bind one private-backed photo.

    These review actions intentionally avoid public photo source URLs.  This
    gate couples their public hash/identity guard to exactly one direct-image
    assignment; ``apply_verified_images`` immediately afterwards verifies the
    corresponding private provenance record before any card receives it.
    """
    variants_by_sku = {variant['sku']: (product, variant)
                       for product in products for variant in product['variants']}
    entries = image_manifest.get('images', []) if isinstance(image_manifest, dict) else []
    if not isinstance(entries, list):
        entries = []
    bound_actions = {'resolve_verified_pack_identity', 'refine_verified_fluid_ounce_label',
                     'resolve_verified_pack_total_volume'}
    for sku, review in source_reviews.items():
        food_resolution = review.get('foodSizeResolution')
        if review['action'] not in bound_actions and food_resolution is None:
            continue
        product, variant = variants_by_sku.get(sku, ({}, {}))
        if not variant:
            raise ValueError(f'Reviewed variant is absent after grouping: {sku}')
        scoped_review = food_resolution or review
        expected_label = scoped_review['verifiedLabel']
        # The fluid action runs after a title review, so its expected public
        # name is already final.  The pack action uses the pre-title expected
        # name only for its application guard; its asset must bind the final
        # grouped product title. Food-size resolutions forbid a title change,
        # so their final title must still equal the guarded public title.
        final_identity = (review['expectedUpc'], product['name'], variant['label'])
        if (final_identity[0] != review['expectedUpc'] or final_identity[2] != expected_label
                or (review['action'] in {'refine_verified_fluid_ounce_label',
                                         'resolve_verified_pack_total_volume'}
                    and product['name'] != review['expectedPublicName'])
                or (food_resolution is not None
                    and product['name'] != food_resolution['expectedPublicName'])):
            raise ValueError(f'Reviewed photo final identity mismatch: {sku}')
        matches = [entry for entry in entries if isinstance(entry, dict) and entry.get('sku') == sku]
        if len(matches) != 1:
            raise ValueError(f'Reviewed photo requires exactly one corresponding image: {sku}')
        entry = matches[0]
        expected_image = (*final_identity, scoped_review['expectedImageSha256'])
        actual_image = (entry.get('expectedUpc'), entry.get('expectedName'),
                        entry.get('expectedLabel'), entry.get('sha256'))
        if (actual_image != expected_image
                or entry.get('usageBasis') != 'user_authorized_direct_reference'
                or entry.get('attributionRequired') is not False):
            raise ValueError(f'Reviewed photo does not match exact label/image guard: {sku}')
        blocked_images.discard(sku)
    return variants_by_sku


def validated_public_name_reviews(manifest: dict | None) -> dict:
    """Accept exact-source, parsed-variant guarded title-only corrections."""
    manifest = expand_private_review_evidence(manifest)
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
        if (not isinstance(entry, dict)
                or set(entry) not in (required, required | {'identityBasis'})):
            raise ValueError('Public-name review contains unsupported or private fields')
        source_record = 'identityBasis' in entry
        if source_record and entry['identityBasis'] != SOURCE_RECORD_NAME_IDENTITY:
            raise ValueError('Public-name review contains an unsupported identity basis')
        sku = entry.get('sku')
        if not isinstance(sku, str) or not re.fullmatch(r'\d+', sku) or sku in reviews:
            raise ValueError('Public-name reviews require unique string SKUs')
        code = entry['expectedUpc']
        valid_code = (isinstance(code, str) and not valid_gtin(code)
                      and (code == '' or bool(re.fullmatch(r'[0-9]{6,14}', code))
                           and bool(code.strip('0')))) if source_record else valid_gtin(code)
        if (not isinstance(code, str) or not valid_code
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
            evidence_fields = {'kind', 'url', 'supports'} | ({'sourceRecord'} if source_record else set())
            if not isinstance(item, dict) or set(item) != evidence_fields:
                raise ValueError(f'Public-name evidence contains unsupported or missing fields: {sku}')
            if (not safe_review_text(item['kind']) or not review_url_host(item['url'])
                    or not safe_review_text(item['supports'])):
                raise ValueError(f'Public-name evidence must use a safe, inspectable HTTPS source: {sku}')
            if source_record:
                identity = {'identifierNamespace': 'original_inventory_MAINUPC',
                            'barcodeMatchClaimed': False, 'sku': sku,
                            'sourceRow': entry['expectedSourceRow'],
                            'rawFieldsSha256': entry['expectedRawFieldsSha256'],
                            'publicUpc': code, 'expectedPublicName': entry['expectedPublicName'],
                            'proposedPublicTitle': title}
                if (item['kind'] != SOURCE_RECORD_NAME_IDENTITY
                        or not isinstance(item['sourceRecord'], dict)
                        or item['sourceRecord'] != identity
                        or type(item['sourceRecord'].get('barcodeMatchClaimed')) is not bool
                        or type(item['sourceRecord'].get('sourceRow')) is not int):
                    raise ValueError(f'Source-record name evidence must bind the exact reviewed identity: {sku}')
            else:
                exact_upc_evidence |= entry['expectedUpc'] in item['supports'] or entry['expectedUpc'] in item['url']
        if not source_record and not exact_upc_evidence:
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


def validate_reviewed_identity_checks(checks: object, sku: str, context: str) -> None:
    required = {'brand', 'expression', 'size', 'packaging', 'upc', 'visual'}
    if (not isinstance(checks, dict) or set(checks) != required
            or any(value is not True for value in checks.values())):
        raise ValueError(f'{context} requires every exact identity check: {sku}')


def validate_public_identity_evidence(evidence: object, sku: str, expected_upc: str,
                                      context: str) -> None:
    """Validate non-photo public evidence for a privacy-preserving review.

    Direct-image page and file URLs live only in the required private evidence
    manifest.  The public review can still require manufacturer and exact-UPC
    product records, while the later image binding proves the approved local
    bytes have matching private provenance.
    """
    required_kinds = {'manufacturer', 'exact_upc_listing'}
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(f'{context} requires public manufacturer and UPC evidence: {sku}')
    seen_kinds = set()
    exact_upc = False
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {'kind', 'url', 'supports'}:
            raise ValueError(f'{context} evidence contains unsupported or private fields: {sku}')
        if (item['kind'] not in required_kinds or not review_url_host(item['url'])
                or not safe_review_text(item['supports'])):
            raise ValueError(f'{context} evidence requires safe public HTTPS records: {sku}')
        seen_kinds.add(item['kind'])
        exact_upc |= item['kind'] == 'exact_upc_listing' and (
            expected_upc in item['url'] or expected_upc in item['supports'])
    if seen_kinds != required_kinds or not exact_upc:
        raise ValueError(f'{context} requires exact UPC evidence: {sku}')


def validate_direct_image_guard(entry: dict, sku: str, context: str) -> None:
    image_hash = entry.get('expectedImageSha256')
    if not isinstance(image_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', image_hash):
        raise ValueError(f'{context} requires an exact approved image hash: {sku}')


def validate_pack_total_volume_evidence(review: dict) -> None:
    """Keep authority per-container facts separate from printed retail count."""
    sku = review['sku']
    prior = parse_reviewed_pack_volume(review['expectedLabel'])
    verified = parse_reviewed_pack_volume(review['verifiedLabel'], require_container=True)
    evidence = review.get('evidence')
    kinds = {'authority_exact_upc_per_container_volume', 'inspected_package_photo'}
    if (not isinstance(evidence, list) or len(evidence) != 2
            or any(not isinstance(item, dict) or not isinstance(item.get('kind'), str)
                   for item in evidence)
            or {item['kind'] for item in evidence} != kinds):
        raise ValueError(f'Pack-total volume requires authority unit and inspected retail-pack evidence: {sku}')
    authority = next(item for item in evidence if item['kind'] != 'inspected_package_photo')
    photo = next(item for item in evidence if item['kind'] == 'inspected_package_photo')

    def parsed_date(value):
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    review_date = parsed_date(review['reviewedOn'])
    retrieved = parsed_date(authority.get('retrievedOn'))
    issued = parsed_date(authority.get('documentDate'))
    inspected = parsed_date(photo.get('inspectedOn'))
    fields = {'kind', 'url', 'supports', 'publisher', 'publisherRole', 'observedUpc',
              'observedNetContent', 'perContainerLabel', 'quantityBasis', 'retailPackCount',
              'sourceSha256', 'documentDate', 'page', 'retrievedOn'}
    if (set(authority) != fields or not review_url_host(authority.get('url'))
            or not safe_review_text(authority.get('supports'))
            or not safe_review_text(authority.get('publisher'))
            or authority['publisherRole'] != 'government_authority'
            or review_url_host(authority['url']) not in PACK_TOTAL_VOLUME_AUTHORITIES.get(
                authority['publisher'], set())
            or authority['observedUpc'] != review['expectedUpc']
            or authority['perContainerLabel'] != verified['measure']
            or reviewed_volume_milliliters(authority['observedNetContent']) != verified['mL']
            or authority['quantityBasis'] != 'per_container_volume'
            # This record supplies no retail count: never manufacture four
            # from a price-book row, commercial case field or shipping unit.
            or authority['retailPackCount'] is not None
            or not isinstance(authority['sourceSha256'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', authority['sourceSha256'])
            or type(authority['page']) is not int or authority['page'] < 1
            or not all((issued, retrieved, inspected, review_date))
            or not issued <= retrieved <= review_date or inspected > review_date):
        raise ValueError(f'Pack-total volume requires an honest exact-UPC authority per-container record: {sku}')
    fields = {'kind', 'url', 'supports', 'sha256', 'inspectedOn',
              'inspectionReceipt', 'inspectionReceiptSha256'}
    if (set(photo) != fields or not review_url_host(photo.get('url'))
            or not safe_review_text(photo.get('supports'))
            or photo['sha256'] != review['expectedImageSha256']):
        raise ValueError(f'Pack-total volume requires the exact inspected original photo: {sku}')
    receipt = photo['inspectionReceipt']
    fields = {'sku', 'upc', 'publicName', 'expectedLabel', 'verifiedLabel', 'productId',
              'groupSkus', 'sourceRow', 'rawFieldsSha256', 'src', 'sha256', 'originalSha256',
              'publicImageSha256', 'sourceImageUrl', 'visiblePackText', 'container',
              'quantityBasis', 'authoritySourceSha256', 'verification', 'inspectedOn'}
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise ValueError(f'Pack-total volume requires a complete printed-pack inspection receipt: {sku}')
    expected = (sku, review['expectedUpc'], review['expectedPublicName'], review['expectedLabel'],
                review['verifiedLabel'], review['expectedProductId'], review['expectedResultingGroupSkus'],
                review['expectedSourceRow'], review['expectedSourceRecordSha256'],
                review['expectedImageSha256'], review['expectedImageSha256'], photo['url'],
                verified['container'], 'printed_retail_pack_per_container',
                authority['sourceSha256'], photo['inspectedOn'])
    actual = tuple(receipt[key] for key in (
        'sku', 'upc', 'publicName', 'expectedLabel', 'verifiedLabel', 'productId', 'groupSkus',
        'sourceRow', 'rawFieldsSha256', 'sha256', 'originalSha256', 'sourceImageUrl',
        'container', 'quantityBasis', 'authoritySourceSha256', 'inspectedOn'))
    printed = re.fullmatch(r'(?P<pack>[1-9]\d*)\s*[x×]\s*(?P<volume>(?:0|[1-9]\d*)'
                           r'(?:\.\d+)?\s*(?:mL|L))',
                           receipt['visiblePackText'] if isinstance(receipt['visiblePackText'], str) else '', re.I)
    checks = receipt['verification']
    if (actual != expected or type(receipt['sourceRow']) is not int
            or not printed or int(printed['pack']) != prior['pack']
            or reviewed_volume_milliliters(printed['volume']) != verified['mL']
            or not isinstance(receipt['src'], str)
            or not re.fullmatch(r'images/products/[A-Za-z0-9_./-]+', receipt['src'])
            or not isinstance(receipt['publicImageSha256'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', receipt['publicImageSha256'])
            or not isinstance(checks, dict)
            or set(checks) != {'brand', 'expression', 'size', 'packaging', 'visual', 'professional'}
            or any(value is not True for value in checks.values())
            or photo['inspectionReceiptSha256'] != review_evidence_sha256(receipt)):
        raise ValueError(f'Pack-total volume requires hash-bound printed retail count and per-container volume: {sku}')


def apply_verified_pack_total_volumes(products: list, records_by_sku: dict, source_reviews: dict,
                                      image_manifest: dict | None, private_image_evidence: dict | None,
                                      identity_reviews: dict) -> None:
    """Clarify only a reviewed total-volume label, after final name/group guards."""
    variants = {v['sku']: (p, v) for p in products for v in p['variants']}
    for sku, review in source_reviews.items():
        if review['action'] != 'resolve_verified_pack_total_volume':
            continue
        product, variant = variants.get(sku, ({}, {}))
        record = records_by_sku.get(sku, {})
        source = record.get('source', {})
        raw_public = classify_record(source)[2] if source else None
        prior = parse_reviewed_pack_volume(review['expectedLabel'])
        parts = [extract_dimensions(source.get(field, '')) for field in ('ItemName', 'PackName')]
        counts = {count for part in parts for count in part['packs']}
        measures = {(dimension, value) for part in parts for _, dimension, value in part['measures']}
        containers = {part['container'] for part in parts if part['container']}
        verified = parse_reviewed_pack_volume(review['verifiedLabel'], require_container=True)
        case_hint = re.search(
            r'\b(?:cases?|shipping|wholesale|pallets?|displays?|trays?|cs)\b|'
            r'(?<!\w)(?:cs|cases?|dz|doz(?:en)?s?)\s*[:#-]?\s*\d+(?![\w.])|'
            r'(?<![\w.])\d+\s*(?:[-/]\s*|per\s+)?(?:cs|cases?|dz|doz(?:en)?s?)(?!\w)|'
            r'(?<![\w.])\d+\s*[x×]\s*\d+(?![\w.])|(?<!\w)[x×]\s*\d+(?![\w.])',
            source.get('ItemName', '') + ' ' + source.get('PackName', ''), re.I)
        if (record.get('disposition') != 'retained_variant' or record.get('sourceOrigin') == 'report'
                or sku in identity_reviews or not raw_public or case_hint
                or raw_public['variant']['label'] != review['expectedLabel']
                or counts != {prior['pack']} or measures != {('volume-mL', prior['mL'])}
                or (containers and containers != {verified['container']})):
            raise ValueError(f'Pack-total volume requires one matching original retail pack and total volume: {sku}')
        raw_hash = hashlib.sha256(json.dumps(record['rawFields'], ensure_ascii=False,
                                            separators=(',', ':')).encode()).hexdigest()
        current = (source.get('SKU'), source.get('MAINUPC'), record.get('sourceRow'),
                   hashlib.sha256(source.get('ItemName', '').encode()).hexdigest(), raw_hash,
                   variant.get('upc'), product.get('name'), variant.get('label'),
                   product.get('category'), product.get('department'), product.get('id'))
        expected = (sku, review['expectedUpc'], review['expectedSourceRow'],
                    review['expectedNameSha256'], review['expectedSourceRecordSha256'],
                    review['expectedUpc'], review['expectedPublicName'], review['expectedLabel'],
                    review['expectedCategory'], review['expectedDepartment'], review['expectedProductId'])
        if current != expected or source.get('DEPNAME') != review['expectedDepartment']:
            raise ValueError(f'Pack-total volume current source/public identity mismatch: {sku}')
        if [v['sku'] for v in product['variants']] != review['expectedResultingGroupSkus']:
            raise ValueError(f'Pack-total volume final SKU-group mismatch: {sku}')
        entries = image_manifest.get('images', []) if isinstance(image_manifest, dict) else []
        matches = [e for e in entries if isinstance(e, dict) and e.get('sku') == sku] if isinstance(entries, list) else []
        inspection = next(e for e in review['evidence'] if e['kind'] == 'inspected_package_photo')
        receipt = inspection['inspectionReceipt']
        if (len(matches) != 1
                or (matches[0].get('expectedUpc'), matches[0].get('expectedName'), matches[0].get('expectedLabel'),
                    matches[0].get('sha256'), matches[0].get('src'), matches[0].get('verification'))
                != (review['expectedUpc'], product['name'], review['verifiedLabel'],
                    review['expectedImageSha256'], receipt['src'], receipt['verification'])
                or review_evidence_sha256(matches[0]) != receipt['publicImageSha256']):
            raise ValueError(f'Pack-total volume requires its exact professional direct-image binding: {sku}')
        private = private_image_evidence.get('entries', []) if isinstance(private_image_evidence, dict) else []
        private_matches = [e for e in private if isinstance(e, dict) and e.get('sku') == sku] if isinstance(private, list) else []
        if len(private_matches) != 1 or private_matches[0].get('sourceImageUrl') != inspection['url']:
            raise ValueError(f'Pack-total volume inspected photo must match private provenance: {sku}')
        record['packTotalVolumeResolution'] = {
            'previousPublicIdentity': {'name': product['name'], 'label': variant['label']},
            'verifiedLabel': review['verifiedLabel'], 'expectedImageSha256': review['expectedImageSha256'],
            'authoritySourceSha256': receipt['authoritySourceSha256'],
            'inspectionReceiptSha256': inspection['inspectionReceiptSha256'],
            'retailCountBasis': 'original_source_and_inspected_package',
            'authorityRetailPackCount': None,
        }
        variant['label'] = review['verifiedLabel']


def validate_ambiguous_liquid_ounce_evidence(review: dict) -> None:
    """Require two literal unit records and a content-hashed manual receipt.

    This separate action does not interpret bare ounces globally. Its receipt
    is structured private evidence, not an unchecked digest of an absent file.
    The first version accepts only unchanged original single-bottle photos.
    """
    sku = review['sku']
    evidence = review.get('evidence')
    listing_kinds = {'exact_upc_liquid_unit_listing', 'exact_upc_ambiguous_ounce_listing'}
    if (not isinstance(evidence, list) or len(evidence) != 3
            or any(not isinstance(item, dict) or not isinstance(item.get('kind'), str) for item in evidence)):
        raise ValueError(f'Ambiguous liquid ounces require two unit listings and inspected-photo evidence: {sku}')
    listings = [item for item in evidence if item.get('kind') in listing_kinds]
    photos = [item for item in evidence if item.get('kind') == 'inspected_package_photo']
    if len(listings) != 2 or len(photos) != 1:
        raise ValueError(f'Ambiguous liquid ounces require two unit listings and inspected-photo evidence: {sku}')
    if (review.get('verificationMethod') == FOOD_FLUID_OUNCE_METHOD
            and not any(item['kind'] == 'exact_upc_liquid_unit_listing' for item in listings)):
        raise ValueError(f'Food fluid ounces require at least one explicit-fluid unit listing: {sku}')
    fields = {'kind', 'url', 'supports', 'observedUpc', 'observedNetContent',
              'retailUnitLabel', 'quantityBasis', 'retailUnitsPerPackage',
              'container', 'sourceSha256', 'retrievedOn'}
    hosts = []
    for item in listings:
        # An ambiguous listing preserves its literal canonical bare-ounce fact;
        # only the inspected package establishes the physical fluid unit.
        observed_label = (review['expectedLabel'] if item['kind'] == 'exact_upc_ambiguous_ounce_listing'
                          else review['verifiedLabel'])
        if (set(item) != fields or not safe_review_text(item['supports'])
                or item['observedUpc'] != review['expectedUpc']
                or item['observedNetContent'] != observed_label
                or item['retailUnitLabel'] != observed_label
                or item['quantityBasis'] != 'retail_net_content'
                or type(item['retailUnitsPerPackage']) is not int
                or item['retailUnitsPerPackage'] != 1 or item['container'] != 'bottle'
                or not isinstance(item['sourceSha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', item['sourceSha256'])
                or not isinstance(item['retrievedOn'], str)
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', item['retrievedOn'])):
            raise ValueError(f'Ambiguous liquid ounces require literal UPC and honest typed single-bottle net content: {sku}')
        try:
            host = review_url_host(item['url']).encode('idna').decode('ascii').rstrip('.').lower()
        except UnicodeError as error:
            raise ValueError(f'Ambiguous liquid ounces require independent DNS sources: {sku}') from error
        if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}', host):
            raise ValueError(f'Ambiguous liquid ounces require independent DNS sources: {sku}')
        hosts.append(host.removeprefix('www.'))
    # Conservatively reject shared two-label DNS suffixes, including sibling
    # subdomains. Some distinct public-suffix domains may need a later review;
    # this bounded path must not treat aliases as independent corroboration.
    if (hosts[0].split('.')[-2:] == hosts[1].split('.')[-2:]
            or listings[0]['sourceSha256'] == listings[1]['sourceSha256']):
        raise ValueError(f'Ambiguous liquid ounces require independent unit sources: {sku}')
    photo = photos[0]
    required = {'kind', 'url', 'supports', 'sha256', 'inspectedOn',
                'inspectionReceipt', 'inspectionReceiptSha256'}
    if (set(photo) != required or not review_url_host(photo['url'])
            or not safe_review_text(photo['supports'])
            or photo['sha256'] != review['expectedImageSha256']
            or not isinstance(photo['inspectedOn'], str)
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', photo['inspectedOn'])):
        raise ValueError(f'Ambiguous liquid ounces require inspected original-photo evidence: {sku}')
    receipt = photo['inspectionReceipt']
    receipt_fields = {'sku', 'upc', 'publicName', 'label', 'src', 'sha256', 'publicImageSha256',
                      'originalSha256', 'sourceImageUrl', 'visibleNetContent',
                      'container', 'verification', 'inspectedOn'}
    if (not isinstance(receipt, dict) or set(receipt) != receipt_fields
            or (receipt['sku'], receipt['upc'], receipt['publicName'], receipt['label'],
                receipt['sha256'], receipt['originalSha256'], receipt['sourceImageUrl'],
                receipt['visibleNetContent'], receipt['container'], receipt['inspectedOn'])
            != (sku, review['expectedUpc'], review['expectedPublicName'], review['verifiedLabel'],
                review['expectedImageSha256'], review['expectedImageSha256'], photo['url'],
                review['verifiedLabel'], 'bottle', photo['inspectedOn'])
            or not isinstance(receipt['src'], str)
            or not re.fullmatch(r'images/products/[A-Za-z0-9_./-]+', receipt['src'])
            or not isinstance(receipt['publicImageSha256'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', receipt['publicImageSha256'])
            or not isinstance(receipt['verification'], dict)
            or set(receipt['verification']) != {'brand', 'expression', 'size', 'packaging', 'visual', 'professional'}
            or any(value is not True for value in receipt['verification'].values())
            or photo['inspectionReceiptSha256'] != review_evidence_sha256(receipt)):
        raise ValueError(f'Ambiguous liquid ounces require a hash-bound readable-unit inspection receipt: {sku}')


def apply_ambiguous_liquid_ounces(products: list, records_by_sku: dict, source_reviews: dict,
                                 image_manifest: dict | None, private_image_evidence: dict | None,
                                 blocked_images: set, identity_reviews: dict) -> None:
    """Clarify one original bottle's unit; food requires its explicit opt-in."""
    variants = {v['sku']: (p, v) for p in products for v in p['variants']}
    for sku, review in source_reviews.items():
        if review['action'] != 'resolve_verified_ambiguous_ounce_volume':
            continue
        product, variant = variants.get(sku, ({}, {}))
        record = records_by_sku.get(sku, {})
        source = record.get('source', {})
        food_method = review.get('verificationMethod') == FOOD_FLUID_OUNCE_METHOD
        category = 'food' if food_method else 'essentials'
        raw_public = classify_record(source)[2] if source else None
        raw_name = source.get('ItemName', '')
        measures = list(MEASURE_RE.finditer(raw_name))
        # Count nouns may precede/follow a count, with customary punctuation.
        # Multiplicity markers require token/count context, so X24Cleaner,
        # CSPro and incidental brand numbers are not treated as package facts.
        count = r'[0-9]+(?:\.[0-9]+)?'
        count_noun = (r'(?:cases?|cs|packs?|pkgs?|pks?|counts?|ct|units?|pcs?|pieces?|'
                      r'bottles?|btls?|cans?|cartons?|boxes?|bundles?|sets?|pairs?|doz(?:en)?s?|dz)')
        punctuation = r'(?:[:=#/-]|\[|\()'
        count_hint = re.search(
            rf'(?<!\w){count_noun}\s*(?:(?:of|qty|count)\s*)?{punctuation}?\s*{count}(?![\w.])|'
            rf'(?<![\w.]){count}\s*(?:{punctuation}\s*|per\s+)?{count_noun}(?!\w)|'
            rf'(?<!\w)[x×]\s*{punctuation}?\s*{count}(?![\w.])|'
            rf'×\s*{count}(?![\w.])|'
            rf'(?<![\w.]){count}\s*[x×](?!\w)|'
            rf'(?<![\w.]){count}\s*[x×]\s*(?=[0-9])', raw_name, re.I)
        conflicts = re.search(
            r'(?<![a-z])(?:net[\s.-]*(?:wt|weight)|by\s*weight|mass|weight|wt|dry|powder|solid|negative|fluid|fl\.?)(?![a-z])|'
            r'(?<![a-z])(?:weight|wt|w)\s*/\s*(?:weight|wt|w)(?![a-z])|'
            r'\b(?:cases?|packs?|pkgs?|pks?|multi[-\s]*packs?|counts?|ct|cans?|bottles?|btls?|'
            r'boxes?|cartons?|bundles?|sets?|pairs?|doz(?:en)?s?|dz|units?|pcs?|pieces?)\b|'
            r'\b\d+\s*[x×]\s*\d+\b', raw_name, re.I)
        if (record.get('disposition') != 'retained_variant' or record.get('sourceOrigin') == 'report'
                or sku in identity_reviews or not raw_public or raw_public['category'] != category
                or review.get('expectedCategory') != category
                or ('verificationMethod' in review and not food_method)
                or (food_method and record.get('categoryReview') is not None)
                or source.get('PackName') != '' or conflicts or count_hint or len(measures) != 1
                or measures[0]['unit'].lower().rstrip('.') != 'oz'
                # The legacy parser accepts oz prefixes. This action requires
                # a complete bare unit, never joined ozweight/ozfl text.
                or re.match(r'\w', raw_name[measures[0].end():])
                or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', measures[0]['number'])
                or re.search(r'[-+−＋]\s*$', raw_name[:measures[0].start()])
                or parse_number(measures[0]['number']) != canonical_ounce_label_value(review['expectedLabel'])
                or raw_public['variant']['label'] != review['expectedLabel']):
            raise ValueError(f'Ambiguous liquid ounces require one bare original ounce hint without conflicting metadata: {sku}')
        raw_hash = hashlib.sha256(json.dumps(record['rawFields'], ensure_ascii=False,
                                            separators=(',', ':')).encode()).hexdigest()
        current = (source.get('SKU'), source.get('MAINUPC'), record.get('sourceRow'),
                   hashlib.sha256(raw_name.encode()).hexdigest(), raw_hash,
                   variant.get('upc'), product.get('name'), variant.get('label'),
                   product.get('category'), product.get('department'), product.get('id'))
        expected = (sku, review['expectedUpc'], review['expectedSourceRow'],
                    review['expectedNameSha256'], review['expectedSourceRecordSha256'],
                    review['expectedUpc'], review['expectedPublicName'], review['expectedLabel'],
                    category, review['expectedDepartment'], review['expectedProductId'])
        if current != expected or source.get('DEPNAME') != review['expectedDepartment']:
            raise ValueError(f'Ambiguous liquid ounces current source/public identity mismatch: {sku}')
        if [v['sku'] for v in product['variants']] != review['expectedResultingGroupSkus']:
            raise ValueError(f'Ambiguous liquid ounces require the unchanged singleton SKU group: {sku}')
        entries = image_manifest.get('images', []) if isinstance(image_manifest, dict) else []
        matches = [e for e in entries if isinstance(e, dict) and e.get('sku') == sku] if isinstance(entries, list) else []
        if len(matches) != 1:
            raise ValueError(f'Ambiguous liquid ounces require exactly one inspected original image: {sku}')
        image = matches[0]
        inspection = next(e for e in review['evidence'] if e['kind'] == 'inspected_package_photo')
        receipt = inspection['inspectionReceipt']
        if ((image.get('expectedUpc'), image.get('expectedName'), image.get('expectedLabel'),
             image.get('sha256'), image.get('src'), image.get('verification'))
                != (review['expectedUpc'], product['name'], review['verifiedLabel'],
                    review['expectedImageSha256'], receipt['src'], receipt['verification'])
                or review_evidence_sha256(image) != receipt['publicImageSha256']):
            raise ValueError(f'Ambiguous liquid ounces require their exact professional image binding: {sku}')
        private_entries = private_image_evidence.get('entries', []) if isinstance(private_image_evidence, dict) else []
        private_matches = [e for e in private_entries if isinstance(e, dict) and e.get('sku') == sku] if isinstance(private_entries, list) else []
        if len(private_matches) != 1 or private_matches[0].get('sourceImageUrl') != inspection['url']:
            raise ValueError(f'Ambiguous liquid ounces inspected photo must match private provenance: {sku}')
        record['ambiguousLiquidOunceResolution'] = {
            'previousPublicIdentity': {'name': product['name'], 'label': variant['label']},
            'verifiedLabel': review['verifiedLabel'], 'expectedImageSha256': review['expectedImageSha256'],
        }
        variant['label'] = review['verifiedLabel']
        blocked_images.discard(sku)


def missing_measurement_capture(item: dict, content_type: str, sku: str) -> str:
    """Read only a retained, complete body; a digest alone is not a capture."""
    capture = item.get('capture')
    if (not isinstance(capture, dict)
            or set(capture) != {'status', 'complete', 'contentType', 'responseBody'}
            or type(capture['status']) is not int or capture['status'] != 200
            or capture['complete'] is not True or capture['contentType'] != content_type
            or not isinstance(capture['responseBody'], str) or not capture['responseBody']
            or not isinstance(item.get('sourceSha256'), str)
            or hashlib.sha256(capture['responseBody'].encode('utf-8')).hexdigest() != item['sourceSha256']):
        raise ValueError(f'USDA missing measurement requires a complete hash-bound source capture: {sku}')
    return capture['responseBody']


def go_upc_package_capture_fields(body: str, sku: str) -> tuple[list, list, list]:
    """Read live fields from the one retained Go-UPC product container only."""
    from html.parser import HTMLParser

    class ProductCaptureParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.root = {'tag': '', 'attrs': {}, 'children': [], 'ignored': False}
            self.stack = [self.root]
            self.malformed = False

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if len(attributes) != len(attrs):
                self.malformed = True
            hidden = ('hidden' in attributes or attributes.get('aria-hidden') == 'true'
                      or re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', attributes.get('style') or '', re.I))
            node = {'tag': tag, 'attrs': attributes, 'children': [],
                    'ignored': bool(self.stack[-1]['ignored'] or hidden
                                    or tag in {'script', 'style', 'template', 'noscript'})}
            self.stack[-1]['children'].append(node)
            if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                           'link', 'meta', 'param', 'source', 'track', 'wbr'}:
                self.stack.append(node)

        def handle_endtag(self, tag):
            if len(self.stack) > 1 and self.stack[-1]['tag'] == tag:
                self.stack.pop()
            else:
                self.malformed = True

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if self.stack[-1]['tag'] == tag:
                self.stack.pop()

        def handle_data(self, data):
            self.stack[-1]['children'].append(data)

    def walk(node):
        if node['ignored']:
            return
        yield node
        for child in node['children']:
            if isinstance(child, dict):
                yield from walk(child)

    def text(node):
        return clean_space(' '.join(child if isinstance(child, str) else text(child)
                                   for child in node['children']
                                   if isinstance(child, str) or not child['ignored']))

    def has_class(node, name):
        return name in (node['attrs'].get('class') or '').split()

    parser = ProductCaptureParser()
    parser.feed(body)
    parser.close()
    containers = [node for node in walk(parser.root)
                  if node['tag'] == 'div' and node['attrs'].get('id') == 'resultPageContainer']
    if parser.malformed or len(parser.stack) != 1 or len(containers) != 1:
        raise ValueError(f'USDA missing measurement requires one live Go-UPC product container: {sku}')
    columns = [node for node in containers[0]['children']
               if isinstance(node, dict) and not node['ignored'] and node['tag'] == 'div'
               and has_class(node, 'two-column-container')]
    left = [node for column in columns for node in column['children']
            if isinstance(node, dict) and not node['ignored'] and node['tag'] == 'div'
            and has_class(node, 'left-column')]
    titles = [node for node in walk(containers[0]) if node['tag'] == 'h1' and has_class(node, 'product-name')]
    if (len(columns) != 1 or len(left) != 1 or len(titles) != 1 or not safe_review_text(text(titles[0]))
            or not any(node is titles[0] for node in walk(left[0]))):
        raise ValueError(f'USDA missing measurement requires one scoped Go-UPC product listing: {sku}')
    upcs, sizes, images = [], [], []
    for node in walk(left[0]):
        if node['tag'] == 'table' and has_class(node, 'table'):
            for row in walk(node):
                cells = [child for child in row['children'] if isinstance(child, dict)
                         and not child['ignored'] and child['tag'] == 'td'] if row['tag'] == 'tr' else []
                if len(cells) == 2 and has_class(cells[0], 'metadata-label') and text(cells[0]) == 'UPC':
                    upcs.append(text(cells[1]))
        if node['tag'] == 'li':
            labels = [child for child in node['children'] if isinstance(child, dict)
                      and not child['ignored'] and child['tag'] == 'span' and has_class(child, 'metadata-label')]
            if len(labels) == 1 and text(labels[0]) == 'Package Size, FL OZ:':
                if any(isinstance(child, dict) and not child['ignored'] and child is not labels[0]
                       for child in node['children']):
                    raise ValueError(f'USDA missing measurement rejects unsupported live package-size content: {sku}')
                sizes.append(clean_space(' '.join(child for child in node['children'] if isinstance(child, str))))
        if node['tag'] == 'figure' and has_class(node, 'product-image'):
            images.extend(child['attrs'].get('src') for child in node['children']
                          if isinstance(child, dict) and not child['ignored'] and child['tag'] == 'img')
    return upcs, sizes, images


def validate_usda_missing_measurement_evidence(review: dict) -> None:
    """Accept one historical USDA branded fluid-unit record, not a producer claim.

    This intentionally narrow path uses packageWeight only. Nutrition servings,
    case quantities, availability and the identity of the data submitter cannot
    establish retail quantity or manufacturer ownership here.
    """
    sku = review['sku']
    evidence = review.get('evidence')
    kinds = {'usda_branded_food_record', 'secondary_exact_upc_package_listing', 'inspected_package_photo'}
    if (not isinstance(evidence, list) or len(evidence) != 3
            or any(not isinstance(item, dict) or not isinstance(item.get('kind'), str) for item in evidence)
            or {item['kind'] for item in evidence} != kinds):
        raise ValueError(f'USDA missing measurement requires government, secondary and inspected-photo evidence: {sku}')
    primary = next(item for item in evidence if item['kind'] == 'usda_branded_food_record')
    secondary = next(item for item in evidence if item['kind'] == 'secondary_exact_upc_package_listing')
    photo = next(item for item in evidence if item['kind'] == 'inspected_package_photo')
    common = {'kind', 'url', 'supports', 'sourceSha256', 'retrievedOn', 'capture'}
    if (set(primary) != common | {'publisher', 'quantitySourceField', 'quantityBasis', 'retailUnitLabel'}
            or primary.get('publisher') != 'USDA FoodData Central'
            or primary.get('quantitySourceField') != 'packageWeight'
            or primary.get('quantityBasis') != 'branded_record_package_net_content'
            or primary.get('retailUnitLabel') != review['verifiedLabel']
            or not safe_review_text(primary.get('supports'))):
        raise ValueError(f'USDA missing measurement requires honest typed packageWeight evidence: {sku}')
    body = missing_measurement_capture(primary, 'application/json', sku)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate captured record field')
            result[key] = value
        return result

    try:
        record = json.loads(body, object_pairs_hook=unique_object,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, TypeError) as error:
        raise ValueError(f'USDA missing measurement requires an unambiguous captured JSON record: {sku}') from error
    if not isinstance(record, dict):
        raise ValueError(f'USDA missing measurement requires one captured Branded record: {sku}')
    fdc_id = record.get('fdcId')
    parsed = urlparse(primary['url']) if isinstance(primary.get('url'), str) else None
    if (not parsed or review_url_host(primary['url']) != 'api.nal.usda.gov'
            or type(fdc_id) is not int or fdc_id < 1
            or parsed.path != f'/fdc/v1/food/{fdc_id}'
            or parsed.query not in {'', 'api_key=DEMO_KEY'} or parsed.params or parsed.fragment
            or record.get('dataType') != 'Branded' or record.get('foodClass') != 'Branded'
            or not isinstance(record.get('gtinUpc'), str)
            or not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})', record['gtinUpc'])
            or not valid_gtin(record['gtinUpc']) or record['gtinUpc'] != review['expectedUpc']
            or record.get('dataSource') != 'LI' or record.get('marketCountry') != 'United States'
            or any(not safe_review_text(record.get(key)) for key in ('description', 'brandName', 'brandOwner'))
            or record.get('discontinuedDate') != ''):
        raise ValueError(f'USDA missing measurement requires the exact literal GTIN and official Branded record URL: {sku}')
    # Preserve all historical fields in the private body, without treating an
    # empty discontinuedDate as an assertion about current inventory.
    try:
        for key in ('publicationDate', 'modifiedDate', 'availableDate'):
            value = record.get(key)
            if not isinstance(value, str) or not re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}', value):
                raise ValueError('Missing branded record date')
            month, day, year = map(int, value.split('/'))
            date(year, month, day)
        for value in (review.get('reviewedOn'), primary.get('retrievedOn'), secondary.get('retrievedOn'), photo.get('inspectedOn')):
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError('Missing evidence date')
            date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f'USDA missing measurement requires valid retained historical and review dates: {sku}') from error
    number = r'(?:0|[1-9]\d*)(?:\.\d+)?'
    package = re.fullmatch(rf'(?P<oz>{number}) fl oz/(?P<ml>{number}) mL',
                           record.get('packageWeight') if isinstance(record.get('packageWeight'), str) else '')
    verified = canonical_ounce_label_value(review['verifiedLabel'], fluid=True)
    if not package or verified is None or Decimal(package['oz']) != verified:
        raise ValueError(f'USDA missing measurement requires explicit packageWeight fluid net content: {sku}')
    with localcontext() as context:
        context.prec = max(28, len(package['oz']) + len(package['ml']) + 20)
        expected_ml = (verified * Decimal('29.5735295625')).quantize(Decimal('1'))
    if Decimal(package['ml']) <= 0 or Decimal(package['ml']) != expected_ml:
        raise ValueError(f'USDA missing measurement rejects contradictory packageWeight units: {sku}')

    secondary_fields = common | {'publisher', 'sourceRole', 'observedUpc', 'quantitySourceField',
                                  'packageSize', 'packageSizeUnit', 'sourceImageUrl'}
    if (set(secondary) != secondary_fields or secondary.get('publisher') != 'Go-UPC'
            or secondary.get('sourceRole') != 'secondary_barcode_aggregator'
            or secondary.get('observedUpc') != review['expectedUpc']
            or secondary.get('quantitySourceField') != 'Package Size, FL OZ'
            or secondary.get('packageSizeUnit') != 'FL OZ'
            or not isinstance(secondary.get('packageSize'), str)
            or not re.fullmatch(number, secondary['packageSize'])
            or Decimal(secondary['packageSize']) != verified
            or not safe_review_text(secondary.get('supports'))
            or secondary.get('url') != 'https://go-upc.com/search?q=' + review['expectedUpc']
            or not review_url_host(secondary.get('sourceImageUrl'))):
        raise ValueError(f'USDA missing measurement requires independent literal Go-UPC package-size evidence: {sku}')
    secondary_body = missing_measurement_capture(secondary, 'text/html', sku)
    # These are the literal retained Go-UPC fields. Do not normalize its EAN,
    # substitute Weight LB, or invent an absent UnitsInPackage declaration.
    captured_upcs, captured_sizes, captured_images = go_upc_package_capture_fields(secondary_body, sku)
    if (captured_upcs != [review['expectedUpc']] or captured_sizes != [secondary['packageSize']]
            or secondary['sourceImageUrl'] not in captured_images):
        raise ValueError(f'USDA missing measurement secondary capture lacks exact code, package size or image association: {sku}')

    photo_fields = {'kind', 'url', 'supports', 'sha256', 'inspectedOn', 'inspectionReceipt', 'inspectionReceiptSha256'}
    if (set(photo) != photo_fields or photo.get('url') != secondary['sourceImageUrl']
            or photo.get('sha256') != review['expectedImageSha256']
            or not safe_review_text(photo.get('supports'))):
        raise ValueError(f'USDA missing measurement requires the original inspected package photo: {sku}')
    receipt = photo.get('inspectionReceipt')
    fields = {'scope', 'sku', 'upc', 'publicName', 'expectedLabel', 'verifiedLabel', 'productId',
              'groupSkus', 'sourceRow', 'rawFieldsSha256', 'rawNameSha256', 'src', 'sha256',
              'originalSha256', 'sourceImageUrl', 'publicImageSha256', 'visibleContainerCount',
              'countBasis', 'netContentBasis', 'visibleNetContent', 'authoritySourceSha256',
              'secondarySourceSha256', 'verification', 'inspectedOn'}
    expected = {'scope': 'original_inventory_unit', 'sku': sku, 'upc': review['expectedUpc'],
                'publicName': review['expectedPublicName'], 'expectedLabel': review['expectedLabel'],
                'verifiedLabel': review['verifiedLabel'], 'productId': review['expectedProductId'],
                'groupSkus': review['expectedResultingGroupSkus'], 'sourceRow': review['expectedSourceRow'],
                'rawFieldsSha256': review['expectedSourceRecordSha256'], 'rawNameSha256': review['expectedNameSha256'],
                'sha256': review['expectedImageSha256'], 'originalSha256': review['expectedImageSha256'],
                'sourceImageUrl': photo['url'], 'countBasis': 'manual_visible_packaging',
                'netContentBasis': 'usda_packageWeight_and_independent_secondary', 'visibleNetContent': None,
                'authoritySourceSha256': primary['sourceSha256'], 'secondarySourceSha256': secondary['sourceSha256'],
                'inspectedOn': photo['inspectedOn']}
    if (not isinstance(receipt, dict) or set(receipt) != fields
            or any(receipt.get(key) != value for key, value in expected.items())
            or type(receipt.get('sourceRow')) is not int
            or type(receipt.get('visibleContainerCount')) is not int or receipt['visibleContainerCount'] != 1
            or not isinstance(receipt.get('src'), str)
            or not re.fullmatch(r'images/products/[A-Za-z0-9_./-]+', receipt['src'])
            or not isinstance(receipt.get('publicImageSha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', receipt['publicImageSha256'])
            or not isinstance(receipt.get('verification'), dict)
            or set(receipt['verification']) != {'brand', 'expression', 'size', 'packaging', 'visual', 'professional'}
            or any(value is not True for value in receipt['verification'].values())
            or photo.get('inspectionReceiptSha256') != review_evidence_sha256(receipt)):
        raise ValueError(f'USDA missing measurement requires a hash-bound professional manual packaging receipt: {sku}')


def captured_mercaso_food_product(body: str, sku: str) -> dict:
    """Parse only the live own-product Next data, never recommendations."""
    from html.parser import HTMLParser

    class NextDataParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.stack, self.scripts, self.current = [], [], None
            self.invalid = False

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            ignored = (any(item[1] for item in self.stack) or tag in {'template', 'noscript', 'style'}
                       or 'hidden' in attributes or attributes.get('aria-hidden') == 'true')
            if tag == 'script' and attributes.get('id') == '__NEXT_DATA__' and not ignored:
                if (len(attributes) != len(attrs) or attributes.get('type') != 'application/json'
                        or 'src' in attributes or self.current is not None
                        or not any(item[0] == 'html' for item in self.stack)
                        or not any(item[0] in {'body', 'head'} for item in self.stack)):
                    self.invalid = True
                self.current = []
            if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
                           'meta', 'param', 'source', 'track', 'wbr'}:
                self.stack.append((tag, bool(ignored)))

        def handle_endtag(self, tag):
            if tag == 'script' and self.current is not None:
                self.scripts.append(''.join(self.current))
                self.current = None
            if self.stack and self.stack[-1][0] == tag:
                self.stack.pop()
            else:
                self.invalid = True

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if self.stack and self.stack[-1][0] == tag:
                self.handle_endtag(tag)

        def handle_data(self, value):
            if self.current is not None:
                self.current.append(value)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate own-product JSON field')
            result[key] = value
        return result

    parser = NextDataParser()
    parser.feed(body)
    parser.close()
    try:
        if parser.invalid or parser.stack or parser.current is not None or len(parser.scripts) != 1:
            raise ValueError('Expected one live own-product Next data script')
        data = json.loads(parser.scripts[0], object_pairs_hook=unique_object, parse_float=Decimal,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        product = data['props']['pageProps']['product']
        if not isinstance(product, dict):
            raise ValueError('Expected one own product object')
        return product
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f'Printed food unit requires one unambiguous props.pageProps.product capture: {sku}') from error


def validate_printed_food_unit_evidence(review: dict) -> None:
    """Use an actual printed barcode/net label, with honest distributor context."""
    sku = review['sku']
    evidence = review.get('evidence')
    kinds = {'distributor_exact_food_unit_record', 'inspected_package_photo'}
    if (not isinstance(evidence, list) or len(evidence) != 2
            or any(not isinstance(item, dict) or not isinstance(item.get('kind'), str) for item in evidence)
            or {item['kind'] for item in evidence} != kinds):
        raise ValueError(f'Printed food unit requires distributor and printed-package evidence: {sku}')
    source = next(item for item in evidence if item['kind'] == 'distributor_exact_food_unit_record')
    photo = next(item for item in evidence if item['kind'] == 'inspected_package_photo')
    fields = {'kind', 'url', 'supports', 'publisher', 'sourceRole', 'sourceSha256', 'retrievedOn', 'capture'}
    capture = source.get('capture')
    if (set(source) != fields or source.get('publisher') != 'Mercaso'
            or source.get('sourceRole') != 'direct_seller_wholesaler'
            or not safe_review_text(source.get('supports')) or not isinstance(capture, dict)
            or set(capture) != {'status', 'complete', 'contentType', 'responseUrl', 'responseBody'}
            or type(capture['status']) is not int or capture['status'] != 200 or capture['complete'] is not True
            or not isinstance(capture['contentType'], str) or capture['contentType'].split(';')[0].strip().lower() != 'text/html'
            or capture['responseUrl'] != source.get('url')
            or not isinstance(capture['responseBody'], str) or not capture['responseBody']
            or not isinstance(source.get('sourceSha256'), str)
            or hashlib.sha256(capture['responseBody'].encode()).hexdigest() != source['sourceSha256']):
        raise ValueError(f'Printed food unit requires a complete hash-bound distributor HTML capture: {sku}')
    product = captured_mercaso_food_product(capture['responseBody'], sku)
    parsed = urlparse(source['url']) if isinstance(source.get('url'), str) else None
    slug = product.get('slug')
    tags, variants = product.get('tags'), product.get('variants')
    size, unit = product.get('itemSize'), product.get('unitOfMeasure')
    individuals = [v for v in variants if isinstance(v, dict) and v.get('name') == 'Individual'] if isinstance(variants, list) else []
    images = product.get('images')
    image_url = images[0].get('url') if isinstance(images, list) and images and isinstance(images[0], dict) else None
    if (not parsed or review_url_host(source['url']) != 'shop.mercaso.com'
            or parsed.netloc != 'shop.mercaso.com' or parsed.params or parsed.query or parsed.fragment
            or not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug)
            or not re.fullmatch(r'(?:/collections/[a-z0-9]+(?:-[a-z0-9]+)*)?/products/' + re.escape(slug), parsed.path)
            or product.get('path') != '/' + slug
            or not isinstance(product.get('id'), str) or not re.fullmatch(r'gid://shopify/Product/[1-9][0-9]*', product['id'])
            or not safe_review_text(product.get('name'))
            or not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags)
            or tags.count('UPC-Each: ' + review['expectedUpc']) != 1
            or type(size) not in (int, Decimal) or size <= 0
            or not isinstance(unit, str) or unit not in {'oz', 'g', 'kg', 'lb', 'mL', 'L', 'fl oz'}
            or decimal_text(str(size)) + ' ' + unit != review['verifiedLabel']
            or len(individuals) != 1 or 'barcode' not in individuals[0] or individuals[0]['barcode'] is not None
            or not isinstance(individuals[0].get('options'), list)
            or not any(isinstance(option, dict) and option.get('displayName') == 'title'
                       and option.get('values') == [{'label': 'Individual'}] for option in individuals[0]['options'])
            or not review_url_host(image_url) or photo.get('url') != image_url):
        raise ValueError(f'Printed food unit requires literal own UPC-Each, size, Individual option and feature image: {sku}')
    photo_fields = {'kind', 'url', 'supports', 'sha256', 'inspectedOn', 'inspectionReceipt', 'inspectionReceiptSha256'}
    if (set(photo) != photo_fields or not safe_review_text(photo.get('supports'))
            or photo.get('sha256') != review['expectedImageSha256']):
        raise ValueError(f'Printed food unit requires the unchanged inspected package original: {sku}')
    receipt = photo.get('inspectionReceipt')
    expected = {'scope': 'original_inventory_unit', 'sku': sku, 'upc': review['expectedUpc'],
        'publicName': review['expectedPublicName'], 'expectedLabel': review['expectedLabel'], 'verifiedLabel': review['verifiedLabel'],
        'productId': review['expectedProductId'], 'groupSkus': review['expectedResultingGroupSkus'], 'sourceRow': review['expectedSourceRow'],
        'rawFieldsSha256': review['expectedSourceRecordSha256'], 'rawNameSha256': review['expectedNameSha256'],
        'sha256': review['expectedImageSha256'], 'originalSha256': review['expectedImageSha256'], 'sourceImageUrl': image_url,
        'visibleUpc': review['expectedUpc'], 'visibleNetContent': review['verifiedLabel'],
        'countBasis': 'manual_visible_packaging', 'codeBasis': 'printed_package_barcode',
        'netContentBasis': 'printed_package_net_content', 'distributorSourceSha256': source['sourceSha256'],
        'inspectedOn': photo['inspectedOn']}
    receipt_fields = set(expected) | {'src', 'publicImageSha256', 'visibleContainerCount', 'verification',
                                     'rootInspectionReceiptSha256', 'independentInspectionReceiptSha256'}
    if (not isinstance(receipt, dict) or set(receipt) != receipt_fields
            or any(receipt.get(key) != value for key, value in expected.items())
            or type(receipt.get('sourceRow')) is not int
            or type(receipt.get('visibleContainerCount')) is not int or receipt['visibleContainerCount'] != 1
            or not isinstance(receipt.get('src'), str) or not re.fullmatch(r'images/products/[A-Za-z0-9_./-]+', receipt['src'])
            or any(not isinstance(receipt.get(key), str) or not re.fullmatch(r'[a-f0-9]{64}', receipt[key])
                   for key in ['publicImageSha256', 'rootInspectionReceiptSha256', 'independentInspectionReceiptSha256'])
            or receipt['rootInspectionReceiptSha256'] == receipt['independentInspectionReceiptSha256']
            or not isinstance(receipt.get('verification'), dict)
            or set(receipt['verification']) != {'brand', 'expression', 'size', 'packaging', 'visual', 'professional'}
            or any(value is not True for value in receipt['verification'].values())
            or photo.get('inspectionReceiptSha256') != review_evidence_sha256(receipt)):
        raise ValueError(f'Printed food unit requires hash-bound visible barcode/net and independent manual receipt identifiers: {sku}')
    try:
        for value in [review.get('reviewedOn'), source['retrievedOn'], photo['inspectedOn']]:
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError('Invalid inspection date')
            date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f'Printed food unit requires real capture and inspection dates: {sku}') from error


def validate_missing_measurement_evidence(review: dict) -> None:
    """Bind a missing unit measurement to primary/unit records and inspected bytes."""
    if review.get('verificationMethod') == 'usda_branded_food_package_weight':
        validate_usda_missing_measurement_evidence(review)
        return
    if review.get('verificationMethod') == PRINTED_FOOD_UNIT_METHOD:
        validate_printed_food_unit_evidence(review)
        return
    sku = review['sku']
    evidence = review.get('evidence')
    kinds = {'manufacturer', 'exact_upc_listing', 'inspected_package_photo'}
    typed_kinds = {'manufacturer', 'exact_unit_gtin14_listing', 'inspected_package_photo'}
    retailer_kinds = {'exact_upc_listing', 'exact_unit_gtin14_listing'}
    if (not isinstance(evidence, list) or len(evidence) != 3
            or any(not isinstance(item, dict) or not isinstance(item.get('kind'), str) for item in evidence)
            or {item.get('kind') for item in evidence} not in (kinds, typed_kinds)):
        raise ValueError(f'Missing measurement requires primary, retail-unit and inspected-photo evidence: {sku}')
    unit_fields = {'kind', 'url', 'supports', 'observedUpc', 'retailUnitLabel',
                   'quantityBasis', 'retailUnitsPerPackage', 'sourceSha256', 'retrievedOn'}
    for item in evidence:
        fields = ({'kind', 'url', 'supports', 'sha256', 'inspectedOn', 'inspectionReceiptSha256'}
                  if item['kind'] == 'inspected_package_photo' else unit_fields)
        typed_unit = item['kind'] == 'exact_unit_gtin14_listing'
        if typed_unit:
            fields = (fields - {'observedUpc'}) | {'observedUnitGtin14', 'unitCodeSourceField'}
        if item['kind'] == 'manufacturer':
            fields = fields | {'publisher', 'publisherEvidenceUrl', 'observedName', 'retiredNotice'}
            if item.get('retiredNotice') is True:
                fields = fields | {'historicalPackageReview'}
        if (set(item) != fields or not review_url_host(item['url'])
                or not safe_review_text(item['supports'])):
            raise ValueError(f'Missing measurement evidence contains unsupported or unsafe fields: {sku}')
        if item['kind'] == 'inspected_package_photo':
            # The receipt digest identifies manual review; its contents are not
            # loaded here. The image hash binds inspection to the assigned bytes.
            if (item['sha256'] != review['expectedImageSha256']
                    or not isinstance(item['inspectedOn'], str)
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', item['inspectedOn'])
                    or not isinstance(item['inspectionReceiptSha256'], str)
                    or not re.fullmatch(r'[a-f0-9]{64}', item['inspectionReceiptSha256'])):
                raise ValueError(f'Missing measurement requires inspected-photo identity and a manual receipt identifier: {sku}')
            continue
        if typed_unit:
            # GS1 filler zeroes represent the same shorter unit code. This is
            # evidence-only: never strip/recalculate a code or change any UPC.
            expected_upc = review['expectedUpc']
            observed = item['observedUnitGtin14']
            if (not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12}|[0-9]{13})', expected_upc)
                    or not valid_gtin(expected_upc)
                    or not isinstance(observed, str) or not re.fullmatch(r'[0-9]{14}', observed)
                    or not valid_gtin(observed) or observed != expected_upc.zfill(14)
                    or item['unitCodeSourceField'] != 'Product.gtin14'):
                raise ValueError(f'Missing measurement requires exact typed Product.gtin14 unit evidence: {sku}')
        if ((not typed_unit and item['observedUpc'] != review['expectedUpc'])
                or item['retailUnitLabel'] != review['verifiedLabel']
                or item['quantityBasis'] != 'retail_net_content'
                or type(item['retailUnitsPerPackage']) is not int or item['retailUnitsPerPackage'] != 1
                or not isinstance(item['sourceSha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', item['sourceSha256'])
                or not isinstance(item['retrievedOn'], str)
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', item['retrievedOn'])):
            raise ValueError(f'Missing measurement requires the literal UPC and single retail-unit quantity: {sku}')
        if item['kind'] == 'manufacturer':
            if item['retiredNotice'] is True:
                # Historical unit evidence is not a current-stock or package-
                # generation claim. Retain the explicit retirement notice and
                # bind this additional manual proof to the exact primary
                # capture, visible net content and inspected original bytes.
                photo = next(e for e in evidence if e['kind'] == 'inspected_package_photo')
                historical = item['historicalPackageReview']
                historical_fields = {'scope', 'producerProductPageUrl', 'producerResponseSha256',
                                     'sourceImageUrl', 'sourceImageSha256', 'visibleNetContent',
                                     'retailUnitsPerPackage', 'manufacturerPrimaryImageConfirmed',
                                     'inspectedOn', 'inspectionReceiptSha256'}
                expected = {'scope': 'original_inventory_unit',
                            'producerProductPageUrl': item['url'],
                            'producerResponseSha256': item['sourceSha256'],
                            'sourceImageUrl': photo.get('url'),
                            'sourceImageSha256': review['expectedImageSha256'],
                            'visibleNetContent': review['verifiedLabel'],
                            'inspectedOn': photo.get('inspectedOn'),
                            'inspectionReceiptSha256': photo.get('inspectionReceiptSha256')}
                if (not isinstance(historical, dict) or set(historical) != historical_fields
                        or any(not isinstance(historical[key], str) or historical[key] != value
                               for key, value in expected.items())
                        or type(historical['retailUnitsPerPackage']) is not int
                        or historical['retailUnitsPerPackage'] != 1
                        or historical['manufacturerPrimaryImageConfirmed'] is not True):
                    raise ValueError(f'Missing measurement requires exact historical original-package proof: {sku}')
                # Scope real calendar checks to this historical route. Active
                # evidence retains its existing shape-only date behavior.
                from datetime import date
                retailer = next(e for e in evidence if e['kind'] in retailer_kinds)
                try:
                    for value in (historical['inspectedOn'], item['retrievedOn'], retailer.get('retrievedOn')):
                        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                            raise ValueError('Expected a YYYY-MM-DD historical evidence date')
                        date.fromisoformat(value)
                except ValueError as error:
                    raise ValueError(f'Missing measurement historical evidence requires valid calendar dates: {sku}') from error
            elif item['retiredNotice'] is not False:
                raise ValueError(f'Missing measurement requires a boolean manufacturer retirement notice: {sku}')
            validate_manufacturer_name_record({
                'sku': sku, 'expectedUpc': review['expectedUpc'],
                'manufacturerRecord': {key: item[key] for key in (
                    'publisher', 'publisherEvidenceUrl', 'observedUpc', 'observedName',
                    'sourceSha256', 'retrievedOn', 'retiredNotice')} | {'productUrl': item['url']},
                'evidence': [{'kind': 'manufacturer_exact_upc_record', 'url': item['url'],
                              'supports': item['supports']}],
            })
    primary = next(item for item in evidence if item['kind'] == 'manufacturer')
    retailer = next(item for item in evidence if item['kind'] in retailer_kinds)
    primary_hosts = MANUFACTURER_NAME_HOSTS[primary['publisher']] | {
        review_url_host(primary['url']), review_url_host(primary['publisherEvidenceUrl'])}
    try:
        retailer_host = review_url_host(retailer['url']).encode('idna').decode('ascii').rstrip('.').lower()
    except UnicodeError as error:
        raise ValueError(f'Missing measurement requires an independent retailer DNS host: {sku}') from error
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', retailer_host):
        raise ValueError(f'Missing measurement requires an independent retailer DNS host: {sku}')
    retailer_host = retailer_host.removeprefix('www.')
    if any(retailer_host == host.rstrip('.').removeprefix('www.')
           or retailer_host.endswith('.' + host.rstrip('.').removeprefix('www.')) for host in primary_hosts):
        raise ValueError(f'Missing measurement requires an independent retailer source: {sku}')


def printed_food_count_hint(text: str) -> re.Match | None:
    """Require no original packaging/container context in printed-food mode.

    Any standalone container word remains held for a separate pack review,
    regardless of intervening words or whether a count can be parsed.
    """
    numeric = r'[0-9]+(?:\.[0-9]+)?'
    separator = r'[\s\-\u2010-\u2015\u2212]'
    # Retain cardinal/scale recognition for joined forms. This never assigns
    # a quantity or promotes a description of several containers as one unit.
    word = (r'(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
            r'thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|'
            r'twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|'
            r'hundreds?|thousands?|millions?|billions?|trillions?|dozens?|'
            r'single|pair|couple|twin|double|triple|multi(?:ple)?|half|quarter|several|few)')
    written = rf'{word}(?:{separator}*(?:(?:and|a|of){separator}*)?{word})*'
    count = rf'(?:{numeric}|{written})'
    noun = (r'(?:cases?|cs|packs?|packages?|pkgs?|pks?|counts?|ct|units?|pcs?|pieces?|'
            r'bottles?|btls?|cans?|cartons?|box(?:es)?|bundles?|sets?|pairs?|doz(?:en)?s?|dz|'
            r'jars?|tubs?|pouch(?:es)?|packets?|bars?|rolls?|bags?|trays?|tins?|cups?|containers?|'
            r'tubes?|sachets?|sleeves?|sticks?)')
    punctuation = r'(?:[:=#/]|\[|\()'
    gap = rf'{separator}*'
    return re.search(
        rf'\b{noun}\b|'
        r'\b(?:cases?|cs|shipping|wholesale|pallets?|displays?|trays?|packs?|pkgs?|pks?|'
        r'cartons?|boxes?|bundles?|counts?|ct|units?|pcs?|pieces?|doz(?:en)?s?|dz)\b|'
        rf'(?<!\w){noun}{gap}(?:(?:of|qty|count){gap})?{punctuation}?{gap}{count}(?![\w.])|'
        rf'(?<![\w.]){count}{gap}(?:{punctuation}{gap}|(?:of|per){gap})?{noun}(?!\w)|'
        rf'(?<!\w)[x×]{gap}{punctuation}?{gap}{numeric}(?![\w.])|'
        rf'×{gap}{numeric}(?![\w.])|(?<![\w.]){numeric}{gap}[x×](?!\w)|'
        rf'(?<![\w.]){numeric}{gap}[x×]{gap}(?=[0-9])|'
        rf'(?<![\w.]){count}{gap}[x×]{gap}{noun}(?!\w)', text, re.I)


def apply_missing_measurements(products: list, records_by_sku: dict, source_reviews: dict,
                               image_manifest: dict | None, private_image_evidence: dict | None,
                               blocked_images: set, identity_reviews: dict) -> None:
    """Fill only absent measurements after original name guards and isolation.

    Legacy source/photo gates keep their existing order. This separate late
    action never rebuilds grouping or promotes a source/report review record.
    """
    variants = {v['sku']: (p, v) for p in products for v in p['variants']}
    for sku, review in source_reviews.items():
        if review['action'] != 'resolve_verified_missing_measurement':
            continue
        product, variant = variants.get(sku, ({}, {}))
        record = records_by_sku.get(sku, {})
        source = record.get('source', {})
        raw_public = classify_record(source)[2] if source else None
        raw_parts = [extract_dimensions(source.get(field, '')) for field in ('ItemName', 'PackName')]
        usda_method = review.get('verificationMethod') == 'usda_branded_food_package_weight'
        printed_method = review.get('verificationMethod') == PRINTED_FOOD_UNIT_METHOD
        # Fail closed on explicit pack counts the legacy dimension parser does
        # not recognize. This applies only to this single-unit late action;
        # neither source text nor global parsing/brand numbers are changed.
        pack_hint = re.search(
            r'\b(?:cases?|packs?|cartons?|boxes?|bundles?)\s*'
            r'(?:(?:of|qty|count)\s*[:=]?\s*)?\d+\b|'
            r'(?<![\d.])\d+\s*(?:[-/]\s*|per\s+)?'
            r'(?:doz(?:en)?s?|dz|cases?|cs|packs?|pks?|counts?|ct|units?|pcs?|pieces?|cartons?|boxes?|bundles?)\b|'
            r'\b(?:dozens?|multi[-\s]*packs?)\b|\b\d+\s*[x×]\s*\d+\b',
            source.get('ItemName', ''), re.I)
        if (record.get('disposition') != 'retained_variant' or record.get('sourceOrigin') == 'report'
                or sku in identity_reviews or not raw_public
                or pack_hint
                or clean_space(source.get('PackName', ''))
                or raw_public['variant']['label'] != 'Size not specified'
                or any(part['measures'] or part['packs'] or part['container'] for part in raw_parts)
                or (printed_method and (raw_public['category'] != 'food' or review.get('expectedCategory') != 'food'
                                        or source.get('PackName') != '' or record.get('categoryReview') is not None
                                        or printed_food_count_hint(source.get('ItemName', ''))))
                or (usda_method and re.search(
                    r'\b(?:cases?|cs|shipping|wholesale|pallets?|displays?|trays?|packs?|pkgs?|pks?|'
                    r'cartons?|boxes?|bundles?|counts?|ct|units?|pcs?|pieces?|doz(?:en)?s?|dz)\b|'
                    r'(?<!\w)[x×]\s*\d|\d\s*[x×](?!\w)', source.get('ItemName', ''), re.I))):
            raise ValueError(f'Missing measurement requires genuinely absent original size/pack metadata: {sku}')
        current = (source.get('SKU'), source.get('MAINUPC'), record.get('sourceRow'),
                   variant.get('upc'), product.get('name'), variant.get('label'),
                   product.get('category'), product.get('department'), product.get('id'))
        expected = (sku, review['expectedUpc'], review['expectedSourceRow'], review['expectedUpc'],
                    review['expectedPublicName'], review['expectedLabel'], review['expectedCategory'],
                    review['expectedDepartment'], review['expectedProductId'])
        if current != expected:
            raise ValueError(f'Missing measurement current source/public identity mismatch: {sku}')
        if [v['sku'] for v in product['variants']] != review['expectedResultingGroupSkus']:
            raise ValueError(f'Missing measurement final SKU-group mismatch: {sku}')
        entries = image_manifest.get('images', []) if isinstance(image_manifest, dict) else []
        if not isinstance(entries, list):
            entries = []
        matches = [entry for entry in entries if isinstance(entry, dict) and entry.get('sku') == sku]
        if len(matches) != 1:
            raise ValueError(f'Missing measurement requires exactly one corresponding image: {sku}')
        image = matches[0]
        checks = image.get('verification', {})
        if ((image.get('expectedUpc'), image.get('expectedName'), image.get('expectedLabel'), image.get('sha256'))
                != (review['expectedUpc'], product['name'], review['verifiedLabel'], review['expectedImageSha256'])
                or not isinstance(checks, dict)
                or set(checks) != {'brand', 'expression', 'size', 'packaging', 'visual', 'professional'}
                or any(value is not True for value in checks.values())):
            raise ValueError(f'Missing measurement requires its exact professional image binding: {sku}')
        private_entries = (private_image_evidence.get('entries', [])
                           if isinstance(private_image_evidence, dict) else [])
        if not isinstance(private_entries, list):
            private_entries = []
        private_matches = [entry for entry in private_entries
                           if isinstance(entry, dict) and entry.get('sku') == sku]
        inspection = next(item for item in review['evidence'] if item['kind'] == 'inspected_package_photo')
        if len(private_matches) != 1 or private_matches[0].get('sourceImageUrl') != inspection['url']:
            raise ValueError(f'Missing measurement inspected photo must match its private provenance: {sku}')
        if usda_method or printed_method:
            receipt = inspection['inspectionReceipt']
            if (image.get('src') != receipt['src'] or checks != receipt['verification']
                    or review_evidence_sha256(image) != receipt['publicImageSha256']):
                context = 'Printed food unit' if printed_method else 'USDA missing measurement'
                raise ValueError(f'{context} requires its complete inspected public-image binding: {sku}')
        # Every earlier guard has now seen its original label. Only this field
        # changes; final image assignment still verifies private coverage/bytes.
        record['lateMeasurementResolution'] = {
            'previousPublicIdentity': {'name': product['name'], 'label': variant['label']},
            'verifiedLabel': review['verifiedLabel'], 'expectedImageSha256': review['expectedImageSha256'],
        }
        variant['label'] = review['verifiedLabel']
        blocked_images.discard(sku)


def validated_name_verifications(manifest: dict | None) -> dict:
    """Keep an unchanged-name review separate from an actual correction."""
    manifest = expand_private_review_evidence(manifest)
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
    manifest = expand_private_review_evidence(manifest)
    if not manifest:
        return {}
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('reviews'), list):
        raise ValueError('Invalid source review manifest schema')
    result = {}
    for entry in manifest['reviews']:
        sku = entry.get('sku')
        if not isinstance(sku, str) or sku in result or entry.get('action') not in {
                'size_needs_confirmation', 'restore_named_merchandise', 'resolve_verified_identity',
                'repair_unquoted_name_commas', 'clarify_container_material',
                'resolve_verified_pack_identity', 'refine_verified_fluid_ounce_label',
                'resolve_verified_missing_measurement', 'resolve_verified_ambiguous_ounce_volume',
                'resolve_verified_pack_total_volume', 'restore_verified_numeric_name'}:
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
        if entry['action'] == 'restore_verified_numeric_name':
            allowed |= {'verificationMethod', 'expectedSourceRow'}
            if (set(entry) != allowed or not re.fullmatch(r'[1-9][0-9]*', sku)
                    or entry.get('verificationMethod') != NUMERIC_NAME_RESTORATION_METHOD
                    or not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})', entry['expectedUpc'])
                    or not valid_gtin(entry['expectedUpc']) or not entry['expectedUpc'].strip('0')
                    or type(entry.get('expectedSourceRow')) is not int or entry['expectedSourceRow'] < 1
                    or not safe_review_text(entry['reason'])):
                raise ValueError(f'Numeric name restoration requires literal GTIN, exact row and name-only method: {sku}')
            try:
                if not isinstance(entry.get('reviewedOn'), str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['reviewedOn']):
                    raise ValueError('Missing review date')
                date.fromisoformat(entry['reviewedOn'])
            except (TypeError, ValueError) as error:
                raise ValueError(f'Numeric name restoration requires a real review date: {sku}') from error
        if entry['action'] == 'resolve_verified_pack_total_volume':
            allowed |= {'expectedSourceRow', 'expectedPublicName', 'expectedLabel',
                        'expectedCategory', 'expectedDepartment', 'expectedProductId',
                        'expectedResultingGroupSkus', 'verifiedLabel', 'expectedImageSha256',
                        'verification', 'evidence'}
            prior = parse_reviewed_pack_volume(entry.get('expectedLabel'))
            verified = parse_reviewed_pack_volume(entry.get('verifiedLabel'), require_container=True)
            if (set(entry) != allowed or not re.fullmatch(r'[1-9]\d*', sku)
                    or not re.fullmatch(r'(?:\d{8}|\d{12}|\d{13}|\d{14})', entry['expectedUpc'])
                    or not valid_gtin(entry['expectedUpc'])
                    or not prior or not verified or prior['pack'] != verified['pack']
                    or (prior['container'] and prior['container'] != verified['container'])
                    or type(entry.get('expectedSourceRow')) is not int or entry['expectedSourceRow'] < 1
                    or not re.fullmatch(r'p-[a-f0-9]{16}', str(entry.get('expectedProductId')))
                    or not all(safe_review_text(entry.get(key)) for key in (
                        'expectedPublicName', 'expectedCategory', 'verifiedLabel', 'reason'))
                    or not isinstance(entry.get('expectedDepartment'), str)
                    or (entry['expectedDepartment'] and not safe_review_text(entry['expectedDepartment']))
                    or not isinstance(entry.get('reviewedOn'), str)
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['reviewedOn'])):
                raise ValueError(f'Pack-total volume requires exact current identity and canonical retail-pack labels: {sku}')
            with localcontext() as context:
                context.prec = max(28, len(verified['mL'].as_tuple().digits) + len(str(prior['pack'])) + 4,
                                   len(prior['mL'].as_tuple().digits) + 4)
                if prior['mL'] != Decimal(prior['pack']) * verified['mL']:
                    raise ValueError(f'Pack-total volume must equal retail count times per-container volume: {sku}')
            group = entry.get('expectedResultingGroupSkus')
            if (not isinstance(group, list) or not group or sku not in group
                    or any(not isinstance(value, str) or not re.fullmatch(r'\d+', value) for value in group)
                    or len(set(group)) != len(group)):
                raise ValueError(f'Pack-total volume requires an exact final SKU group: {sku}')
            validate_direct_image_guard(entry, sku, 'Pack-total volume')
            validate_reviewed_identity_checks(entry.get('verification'), sku, 'Pack-total volume')
            validate_pack_total_volume_evidence(entry)
        if entry['action'] == 'resolve_verified_ambiguous_ounce_volume':
            allowed |= {'expectedSourceRow', 'expectedPublicName', 'expectedLabel',
                        'expectedCategory', 'expectedDepartment', 'expectedProductId',
                        'expectedResultingGroupSkus', 'verifiedLabel', 'expectedImageSha256',
                        'verification', 'evidence'}
            food_method = entry.get('verificationMethod') == FOOD_FLUID_OUNCE_METHOD
            if food_method:
                allowed.add('verificationMethod')
            category = 'food' if food_method else 'essentials'
            prior = canonical_ounce_label_value(entry.get('expectedLabel'))
            verified = canonical_ounce_label_value(entry.get('verifiedLabel'), fluid=True)
            if (set(entry) != allowed or not re.fullmatch(r'[1-9][0-9]*', sku)
                    or not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})', entry['expectedUpc'])
                    or not valid_gtin(entry['expectedUpc']) or prior is None or verified != prior
                    or entry.get('verifiedLabel') != entry.get('expectedLabel', '')[:-3] + ' fl oz'
                    or entry.get('expectedCategory') != category
                    or type(entry.get('expectedSourceRow')) is not int or entry['expectedSourceRow'] < 1
                    or not re.fullmatch(r'p-[a-f0-9]{16}', str(entry.get('expectedProductId')))
                    or entry.get('expectedResultingGroupSkus') != [sku]
                    or not all(safe_review_text(entry.get(key)) for key in ('expectedPublicName', 'expectedDepartment'))
                    or not isinstance(entry.get('reviewedOn'), str)
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['reviewedOn'])):
                raise ValueError(f'Ambiguous liquid ounces require exact singleton {category} identity and unchanged numeric quantity: {sku}')
            validate_direct_image_guard(entry, sku, 'Ambiguous liquid ounces')
            validate_reviewed_identity_checks(entry.get('verification'), sku, 'Ambiguous liquid ounces')
            validate_ambiguous_liquid_ounce_evidence(entry)
        if entry['action'] == 'resolve_verified_missing_measurement':
            allowed |= {'expectedSourceRow', 'expectedPublicName', 'expectedLabel',
                        'expectedCategory', 'expectedDepartment', 'expectedProductId',
                        'expectedResultingGroupSkus', 'verifiedLabel', 'expectedImageSha256',
                        'verification', 'evidence'}
            usda_method = entry.get('verificationMethod') == 'usda_branded_food_package_weight'
            printed_method = entry.get('verificationMethod') == PRINTED_FOOD_UNIT_METHOD
            if usda_method or printed_method:
                allowed.add('verificationMethod')
            if (set(entry) != allowed or not valid_gtin(entry['expectedUpc'])
                    or type(entry['expectedSourceRow']) is not int or entry['expectedSourceRow'] < 1
                    or entry['expectedLabel'] != 'Size not specified'
                    or not isinstance(entry['reviewedOn'], str)
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['reviewedOn'])
                    or not re.fullmatch(r'p-[a-f0-9]{16}', str(entry['expectedProductId']))
                    or any(not safe_review_text(entry[key]) for key in (
                        'expectedPublicName', 'expectedCategory', 'expectedDepartment', 'verifiedLabel'))):
                raise ValueError(f'Missing measurement requires exact original/current identity guards: {sku}')
            label = entry['verifiedLabel']
            dimensions = extract_dimensions(label)
            fluid = canonical_ounce_label_value(label, fluid=True)
            if (fluid is None and (dimensions['text'] or dimensions['packs'] or dimensions['container']
                    or len(dimensions['measures']) != 1
                    or label != dimensions['measures'][0][0] or dimensions['measures'][0][2] <= 0
                    or dimensions['measures'][0][1] not in {'volume-mL', 'mass-g', 'ounce', 'pound', 'quart', 'gallon'})):
                raise ValueError(f'Missing measurement requires one positive canonical retail measurement: {sku}')
            group = entry['expectedResultingGroupSkus']
            if (not isinstance(group, list) or not group or sku not in group
                    or any(not isinstance(value, str) or not re.fullmatch(r'\d+', value) for value in group)
                    or len(set(group)) != len(group)):
                raise ValueError(f'Missing measurement requires its unchanged exact SKU group: {sku}')
            if usda_method and (entry['expectedCategory'] != 'food' or group != [sku]):
                raise ValueError(f'USDA missing measurement requires one unchanged original food SKU: {sku}')
            if printed_method and (entry['expectedCategory'] != 'food' or group != [sku]
                    or not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})', entry['expectedUpc'])):
                raise ValueError(f'Printed food unit requires one unchanged original food SKU and literal GTIN: {sku}')
            validate_direct_image_guard(entry, sku, 'Missing measurement')
            validate_reviewed_identity_checks(entry['verification'], sku, 'Missing measurement')
            validate_missing_measurement_evidence(entry)
        if (entry['action'] == 'size_needs_confirmation'
                and 'foodSizeResolution' in entry and 'identityResolution' in entry):
            raise ValueError(f'Food-size resolution cannot coexist with a generic identity resolution: {sku}')
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
        if entry['action'] == 'size_needs_confirmation' and 'foodSizeResolution' in entry:
            resolution = entry['foodSizeResolution']
            allowed.add('foodSizeResolution')
            required = {'expectedSourceRow', 'expectedPublicName', 'expectedPublicLabel',
                        'expectedCategory', 'expectedDepartment', 'verifiedLabel',
                        'expectedImageSha256', 'verification', 'evidence',
                        'expectedResultingGroupSkus', 'reason'}
            if not isinstance(resolution, dict) or set(resolution) != required:
                raise ValueError(f'Food-size resolution contains missing or unsupported fields: {sku}')
            if (not valid_gtin(entry['expectedUpc'])
                    or type(resolution['expectedSourceRow']) is not int
                    or resolution['expectedSourceRow'] < 1
                    or not isinstance(entry.get('reviewedOn'), str)
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['reviewedOn'])):
                raise ValueError(f'Food-size resolution requires exact UPC, source-row and review-date guards: {sku}')
            if (not safe_review_text(resolution['expectedPublicName'])
                    or resolution['expectedPublicLabel'] != 'Size needs confirmation'
                    or resolution['expectedCategory'] != 'food'
                    or resolution['expectedDepartment'] != 'Snacks'
                    or not safe_review_text(resolution['reason'])):
                raise ValueError(f'Food-size resolution requires the held food/Snacks public identity: {sku}')
            if canonical_ounce_label_value(resolution['verifiedLabel']) is None:
                raise ValueError(f'Food-size resolution requires one canonical non-fluid-ounce label: {sku}')
            validate_direct_image_guard(resolution, sku, 'Food-size resolution')
            validate_reviewed_identity_checks(resolution['verification'], sku, 'Food-size resolution')
            validate_public_identity_evidence(resolution['evidence'], sku, entry['expectedUpc'],
                                              'Food-size resolution')
            expected_group = resolution['expectedResultingGroupSkus']
            if (not isinstance(expected_group, list) or not expected_group
                    or sku not in expected_group or len(set(expected_group)) != len(expected_group)
                    or any(not isinstance(value, str) or not re.fullmatch(r'\d+', value)
                           for value in expected_group)):
                raise ValueError(f'Food-size resolution requires an exact final SKU group: {sku}')
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
            if 'verificationMethod' in entry:
                allowed.add('verificationMethod')
                if (entry['verificationMethod'] != NAME_ONLY_RESTORATION_METHOD
                        or entry['forceSizeNeedsConfirmation'] is not True
                        or 'identityResolution' in entry
                        or not re.fullmatch(r'[1-9][0-9]*', sku)
                        or not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})', entry['expectedUpc'])
                        or not valid_gtin(entry['expectedUpc'])):
                    raise ValueError(f'Name-only restoration requires its explicit method, literal GTIN and unresolved unit: {sku}')
                try:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry.get('reviewedOn', '')):
                        raise ValueError('Missing review date')
                    date.fromisoformat(entry['reviewedOn'])
                except (ValueError, TypeError) as error:
                    raise ValueError(f'Name-only restoration requires a real review date: {sku}') from error
        if entry['action'] == 'resolve_verified_identity':
            allowed |= {'verifiedName', 'verifiedLabel', 'verification', 'evidence'}
            if entry.get('verificationMethod') == NUMERIC_CONFLICT_IDENTITY_METHOD:
                allowed |= {'verificationMethod', 'expectedSourceRow', 'expectedDepartment', 'selectionBasis',
                            'conflictDisposition', 'expectedImageSha256',
                            'expectedResultingGroupSkus'}
                validate_numeric_conflict_identity_review(entry)
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
        if entry['action'] == 'resolve_verified_pack_identity':
            allowed |= {'expectedSourceRow', 'expectedPublicName', 'expectedLabel',
                        'expectedCategory', 'expectedDepartment', 'verifiedLabel',
                        'expectedImageSha256', 'verification', 'evidence',
                        'expectedResultingGroupSkus'}
            if (not valid_gtin(entry['expectedUpc'])
                    or type(entry.get('expectedSourceRow')) is not int
                    or entry['expectedSourceRow'] < 1
                    or not isinstance(entry.get('reviewedOn'), str)
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['reviewedOn'])):
                raise ValueError(f'Pack identity requires exact UPC, source-row and review-date guards: {sku}')
            for field in ('expectedPublicName', 'expectedLabel', 'expectedCategory', 'verifiedLabel'):
                if not safe_review_text(entry.get(field)):
                    raise ValueError(f'Pack identity requires safe public identity fields: {sku}')
            if (not isinstance(entry.get('expectedDepartment'), str)
                    or (entry['expectedDepartment'] and not safe_review_text(entry['expectedDepartment']))):
                raise ValueError(f'Pack identity requires a safe public department: {sku}')
            prior = parse_incomplete_pack_container_label(entry['expectedLabel'])
            verified = parse_verified_pack_container_label(entry['verifiedLabel'])
            if (not prior or not verified or prior['pack'] != verified['pack']
                    or (prior['container'] and prior['container'] != verified['container'])):
                raise ValueError(f'Pack identity must only enrich the existing pack/container facts: {sku}')
            validate_direct_image_guard(entry, sku, 'Pack identity')
            validate_reviewed_identity_checks(entry.get('verification'), sku, 'Pack identity')
            validate_public_identity_evidence(entry.get('evidence'), sku, entry['expectedUpc'],
                                              'Pack identity')
            expected_group = entry.get('expectedResultingGroupSkus')
            if (not isinstance(expected_group, list) or not expected_group
                    or sku not in expected_group or len(set(expected_group)) != len(expected_group)
                    or any(not isinstance(value, str) or not re.fullmatch(r'\d+', value)
                           for value in expected_group)):
                raise ValueError(f'Pack identity requires an exact final SKU group: {sku}')
        if entry['action'] == 'refine_verified_fluid_ounce_label':
            allowed |= {'expectedSourceRow', 'expectedPublicName', 'expectedPublicLabel',
                        'expectedCategory', 'expectedDepartment', 'verifiedLabel',
                        'expectedImageSha256', 'verification', 'evidence'}
            if (not valid_gtin(entry['expectedUpc'])
                    or type(entry.get('expectedSourceRow')) is not int
                    or entry['expectedSourceRow'] < 1
                    or not isinstance(entry.get('reviewedOn'), str)
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['reviewedOn'])):
                raise ValueError(f'Fluid-ounce identity requires exact UPC, source-row and review-date guards: {sku}')
            for field in ('expectedPublicName', 'expectedPublicLabel', 'expectedCategory', 'verifiedLabel'):
                if not safe_review_text(entry.get(field)):
                    raise ValueError(f'Fluid-ounce identity requires safe public identity fields: {sku}')
            if (not isinstance(entry.get('expectedDepartment'), str)
                    or (entry['expectedDepartment'] and not safe_review_text(entry['expectedDepartment']))):
                raise ValueError(f'Fluid-ounce identity requires a safe public department: {sku}')
            prior_value = canonical_ounce_label_value(entry['expectedPublicLabel'])
            verified_value = canonical_ounce_label_value(entry['verifiedLabel'], fluid=True)
            if prior_value is None or verified_value is None or prior_value != verified_value:
                raise ValueError(f'Fluid-ounce identity must preserve one canonical liquid volume: {sku}')
            validate_direct_image_guard(entry, sku, 'Fluid-ounce identity')
            validate_reviewed_identity_checks(entry.get('verification'), sku, 'Fluid-ounce identity')
            validate_public_identity_evidence(entry.get('evidence'), sku, entry['expectedUpc'],
                                              'Fluid-ounce identity')
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
    if review.get('verificationMethod') == NAME_ONLY_RESTORATION_METHOD:
        eligible.add('Needs a specific product name; source record retained')
    if disposition != 'needs_review' or reason not in eligible:
        raise ValueError(f'Restoration is not eligible for the original source: {source["SKU"]}')
    original = iter(re.sub(r'\s', '', source['ItemName']))
    sanitized = review['sanitizedSourceName']
    if not all(any(candidate == character for candidate in original)
               for character in re.sub(r'\s', '', sanitized)):
        raise ValueError(f'Restoration must only remove source text: {source["SKU"]}')
    return sanitized


def validate_guarded_name_restoration(source: dict, raw_fields: list, review: dict,
                                      title_review: dict | None) -> None:
    """Couple generic source cleanup to exact name proof, never unit proof."""
    sku, descriptor = source['SKU'], review['sanitizedSourceName']
    # This bounded route cannot delete a brand/flavor or interpret a source
    # pack. Only an edge ambiguous decimal/currency annotation may leave a plain,
    # generic merchandise noun; the entire descriptor is kept in its guard.
    generic = {'candy', 'juice', 'soda', 'snack', 'snacks', 'beer', 'cigar', 'cigars', 'pop'}
    amount = r'(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)'
    annotation = rf'(?:[0-9]+\.[0-9]+|\.[0-9]+|[$€£]\s*{amount}|{amount}\s*[$€£])'
    cleanup = rf'(?:{annotation}\s*)?{re.escape(descriptor)}(?:\s*{annotation})?'
    if (descriptor != clean_space(descriptor) or descriptor.casefold() not in generic
            or source['PackName'] != '' or category_for(clean_space(source['DEPNAME'])) == 'other'
            or not re.fullmatch(cleanup, clean_space(source['ItemName']))
            or clean_space(source['ItemName']) == descriptor):
        raise ValueError(f'Name-only restoration requires generic descriptor and edge-annotation cleanup only: {sku}')
    expected = (sku, source['MAINUPC'], source['sourceRow'], review_evidence_sha256(raw_fields),
                descriptor, 'Size needs confirmation', category_for(clean_space(source['DEPNAME'])),
                clean_space(source['DEPNAME']))
    if (not title_review or tuple(title_review[key] for key in (
            'sku', 'expectedUpc', 'expectedSourceRow', 'expectedRawFieldsSha256',
            'expectedPublicName', 'expectedLabel', 'expectedCategory', 'expectedDepartment')) != expected):
        raise ValueError(f'Name-only restoration requires its matching guarded public-name review: {sku}')
    validate_name_only_restored_title(title_review['proposedPublicTitle'], sku)


def numeric_conflict_details(review: dict) -> dict:
    """Keep the contradictory raw name/pack and alternate label private."""
    evidence = review.get('evidence')
    matches = [item for item in evidence if isinstance(item, dict) and item.get('kind') == 'numeric_source_conflict'] if isinstance(evidence, list) else []
    return matches[0] if len(matches) == 1 else {}


def validate_numeric_conflict_descriptor(text: str, sku: str) -> None:
    """This opt-in may not turn residual words into pack/container claims."""
    # Reuse the unchanged word/unit guard, also at camel-case boundaries.
    separated = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', text)
    if re.search(r'\b(?:cs|dz|jugs?|serves?)\b', separated, re.I):
        raise ValueError(f'Numeric conflict identity cannot add count, container or unit wording: {sku}')
    try:
        validate_name_only_restored_title(separated, sku)
    except ValueError as error:
        raise ValueError(f'Numeric conflict identity cannot add count, container or unit wording: {sku}') from error
    # Solid lower/upper-case compounds have no word boundary. A finite count
    # or measure prefix plus a container/serving suffix remains a claim even
    # when descriptive modifiers intervene (twolargebottles). Do not split
    # incidental brand words such as Quartet, Singlehanded or Bottlebrush.
    counts = (r'(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
              r'thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|'
              r'thirty|forty|fifty|sixty|seventy|eighty|ninety|dozens?|hundreds?|thousands?|'
              r'single|double|triple|twin|multi|multiple|variety)')
    measures = (r'(?:ounces?|pounds?|(?:kilo|milli|micro)?grams?|'
                r'(?:milli|centi|deci|kilo)?lit(?:er|re)s?|pints?|quarts?|gallons?)')
    containers = (r'(?:cases?|packs?|packages?|pkgs?|pks?|counts?|units?|pieces?|'
                  r'bottles?|btls?|cans?|cartons?|box(?:es)?|bundles?|jars?|jugs?|tubs?|'
                  r'pouch(?:es)?|packets?|bars?|rolls?|bags?|trays?|tins?|cups?|containers?|'
                  r'tubes?|sachets?|sleeves?|sticks?|sets?|pairs?|couples?|serv(?:e|ing)s?|size)')
    if re.search(rf'\b(?:(?:{counts}|{measures})[a-z]*{containers}|family[a-z]*(?:size|packs?))\b', text, re.I):
        raise ValueError(f'Numeric conflict identity cannot add joined count, container or unit wording: {sku}')


def validate_numeric_conflict_identity_review(review: dict) -> None:
    """Opt in only to a declared-volume selection between distinct literal units.

    Typed source facts and inspection receipt identifiers are manually reviewed
    evidence. This validator does not fetch source bodies or open receipt files.
    """
    sku = review['sku']
    def literal_gtin(value):
        return (isinstance(value, str) and re.fullmatch(r'(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})', value)
                and valid_gtin(value) and bool(value.strip('0')))
    def checked_date(value):
        try:
            return date.fromisoformat(value) if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else None
        except ValueError:
            return None
    def hash_value(value):
        return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value)
    def host(url):
        value = review_url_host(url)
        try:
            return value.encode('idna').decode('ascii').lower().rstrip('.').removeprefix('www.')
        except UnicodeError:
            return ''
    conflict = numeric_conflict_details(review)
    main, alternate = review.get('expectedUpc'), conflict.get('expectedNumericItemName')
    selected_volume = reviewed_volume_milliliters(review.get('verifiedLabel'))
    alternate_volume = reviewed_volume_milliliters(conflict.get('alternateLabel'))
    reviewed = checked_date(review.get('reviewedOn'))
    name = review.get('verifiedName', '')
    proof = re.search(r'\b([1-9][0-9]*(?:\.[0-9]+)?) Proof$', name) if isinstance(name, str) else None
    if (not re.fullmatch(r'[1-9][0-9]*', sku) or not literal_gtin(main) or not literal_gtin(alternate)
            or main.zfill(14) == alternate.zfill(14)
            or type(review.get('expectedSourceRow')) is not int or review['expectedSourceRow'] < 1
            or not reviewed or not safe_review_text(conflict.get('expectedPackName'))
            or not safe_review_text(review.get('expectedDepartment'))
            or category_for(review['expectedDepartment']) != 'spirits'
            or review.get('selectionBasis') != 'original_main_upc_and_declared_pack'
            or review.get('conflictDisposition') != 'different_unit_same_expression_not_alias'
            or review.get('expectedResultingGroupSkus') != [sku]
            or selected_volume is None or alternate_volume is None or selected_volume == alternate_volume
            or not proof or re.findall(r'[0-9]+(?:\.[0-9]+)?', name) != [proof[1]]):
        raise ValueError(f'Numeric conflict identity requires distinct literal units and explicit original-pack selection: {sku}')
    validate_direct_image_guard(review, sku, 'Numeric conflict identity')
    pack = conflict['expectedPackName']
    parts = extract_dimensions(pack)
    residual = MEASURE_RE.sub(' ', pack)
    validate_numeric_conflict_descriptor(residual, sku)
    if (len(list(MEASURE_RE.finditer(pack))) != 1 or len(parts['measures']) != 1
            or parts['measures'][0][1] != 'volume-mL' or parts['packs'] or parts['container']
            or parts['measures'][0][0] != review['verifiedLabel']):
        raise ValueError(f'Numeric conflict identity must preserve one original volume without a pack/count/container: {sku}')
    # The strength is an identity fact, never a second package measurement.
    title_parts = extract_dimensions(name)
    if title_parts['measures'] or title_parts['packs'] or title_parts['container']:
        raise ValueError(f'Numeric conflict identity title cannot add package facts: {sku}')
    validate_numeric_conflict_descriptor(name[:proof.start()].strip(), sku)
    evidence = review.get('evidence')
    kinds = {'numeric_source_conflict', 'manufacturer', 'exact_upc_listing', 'conflicting_numeric_unit_record',
             'inspected_package_photo', 'inspected_rear_package_photo'}
    if (not isinstance(evidence, list) or len(evidence) != len(kinds)
            or any(not isinstance(item, dict) for item in evidence)
            or {item.get('kind') for item in evidence} != kinds):
        raise ValueError(f'Numeric conflict identity requires selected, alternate, front and rear proof: {sku}')
    by_kind = {item['kind']: item for item in evidence}
    if (set(conflict) != {'kind', 'url', 'supports', 'expectedNumericItemName', 'expectedPackName', 'alternateLabel'}
            or conflict['url'] != by_kind['manufacturer'].get('url')
            or not safe_review_text(conflict.get('supports'))):
        raise ValueError(f'Numeric conflict identity requires its exact private raw conflict declaration: {sku}')
    unit_fields = {'kind', 'url', 'supports', 'observedUpc', 'observedName', 'observedNetContent',
                   'observedProof', 'quantityBasis', 'codeSourceField', 'unitImageUrl', 'sourceSha256', 'retrievedOn'}
    for kind in ('manufacturer', 'exact_upc_listing', 'conflicting_numeric_unit_record'):
        item = by_kind[kind]
        code = alternate if kind == 'conflicting_numeric_unit_record' else main
        volume = alternate_volume if kind == 'conflicting_numeric_unit_record' else selected_volume
        retrieved = checked_date(item.get('retrievedOn'))
        if (set(item) != unit_fields or not host(item.get('url')) or not safe_review_text(item.get('supports'))
                or not safe_review_text(item.get('observedName')) or not safe_review_text(item.get('codeSourceField'))
                or not host(item.get('unitImageUrl'))
                or item['observedUpc'] != code
                or reviewed_volume_milliliters('0' + item['observedNetContent'] if isinstance(item['observedNetContent'], str)
                                              and item['observedNetContent'].startswith('.') else item['observedNetContent']) != volume
                or item['observedProof'] != proof[1] or item['quantityBasis'] != 'per_retail_unit'
                or not hash_value(item['sourceSha256']) or not retrieved or retrieved > reviewed
                or (kind != 'exact_upc_listing' and item['observedName'] != name)
                or name not in item['supports']
                or not re.search(r'(?<![0-9])' + re.escape(code) + r'(?![0-9])', item['supports'])):
            raise ValueError(f'Numeric conflict identity unit record does not match its literal code/expression/volume/proof: {sku}')
    producer, listing, other = (by_kind[k] for k in ('manufacturer', 'exact_upc_listing', 'conflicting_numeric_unit_record'))
    producer_host, listing_host = host(producer['url']), host(listing['url'])
    if (producer_host == listing_host or producer_host.endswith('.' + listing_host)
            or listing_host.endswith('.' + producer_host) or host(other['url']) != producer_host
            or producer['url'] == other['url'] or producer['sourceSha256'] == other['sourceSha256']):
        raise ValueError(f'Numeric conflict identity requires independent selected-unit corroboration and a distinct producer alternate record: {sku}')
    front, rear = by_kind['inspected_package_photo'], by_kind['inspected_rear_package_photo']
    front_fields = {'kind', 'url', 'supports', 'imageSha256', 'productUrl', 'associatedUpc', 'associatedLabel',
                    'associationBasis', 'inspectionReceiptSha256', 'inspectedOn', 'wholePackage', 'professional'}
    rear_fields = {'kind', 'url', 'supports', 'imageSha256', 'productUrl', 'printedUpc', 'printedNetContent',
                   'inspectionReceiptSha256', 'inspectedOn', 'wholePackage'}
    if (set(front) != front_fields or set(rear) != rear_fields
            or front.get('imageSha256') != review['expectedImageSha256']
            or front.get('associatedUpc') != main or front.get('associatedLabel') != review['verifiedLabel']
            or front.get('associationBasis') != 'exact_selected_unit_source_record'
            or front.get('productUrl') != listing['url'] or front.get('professional') is not True
            or front.get('url') != listing['unitImageUrl']
            or rear.get('productUrl') != producer['url'] or rear.get('printedUpc') != main
            or rear.get('url') != producer['unitImageUrl']
            or reviewed_volume_milliliters(rear.get('printedNetContent')) != selected_volume
            or not hash_value(rear.get('imageSha256')) or rear['imageSha256'] == front['imageSha256']
            or other['unitImageUrl'] in {front.get('url'), rear.get('url')}):
        raise ValueError(f'Numeric conflict identity requires the exact selected front and separate printed-code rear: {sku}')
    for photo in (front, rear):
        inspected = checked_date(photo.get('inspectedOn'))
        if (not host(photo.get('url')) or not safe_review_text(photo.get('supports'))
                or not hash_value(photo.get('inspectionReceiptSha256')) or not inspected or inspected > reviewed
                or photo.get('wholePackage') is not True):
            raise ValueError(f'Numeric conflict identity requires dated manual whole-package inspection receipts: {sku}')


def validate_numeric_conflict_source(source: dict, raw_fields: list, review: dict) -> None:
    conflict = numeric_conflict_details(review)
    disposition, reason, _, _ = classify_record(source)
    if (disposition != 'needs_review' or reason != 'Needs a specific product name; source record retained'
            or len(raw_fields) != len(EXPECTED_HEADERS)
            or (source['MAINUPC'], source['ItemName'], source['PackName'], source['DEPNAME'], source['sourceRow'])
            != (review['expectedUpc'], conflict['expectedNumericItemName'], conflict['expectedPackName'],
                review['expectedDepartment'], review['expectedSourceRow'])):
        raise ValueError(f'Numeric conflict identity requires the exact original quarantined row and declared pack: {source["SKU"]}')


def validate_numeric_conflict_photos(products: list, source_reviews: dict, image_manifest: dict | None,
                                     private_evidence: dict | None, blocked_images: set) -> None:
    scoped = {sku: review for sku, review in source_reviews.items()
              if review.get('verificationMethod') == NUMERIC_CONFLICT_IDENTITY_METHOD}
    if not scoped:
        return
    private = validate_private_direct_image_evidence(image_manifest, private_evidence)
    for sku, review in scoped.items():
        matches = [p for p in products if any(v['sku'] == sku for v in p['variants'])]
        if (len(matches) != 1 or [v['sku'] for v in matches[0]['variants']] != [sku]
                or matches[0]['name'] != review['verifiedName']):
            raise ValueError(f'Numeric conflict identity must remain its exact isolated singleton: {sku}')
        front = next(e for e in review['evidence'] if e['kind'] == 'inspected_package_photo')
        if (sku not in private or private[sku]['sourcePage'] != front['productUrl']
                or private[sku]['sourceImageUrl'] != front['url']):
            raise ValueError(f'Numeric conflict photo requires its exact selected-unit private source association: {sku}')
    validate_reviewed_source_photo_bindings(products,
        {sku: dict(review, action='resolve_verified_pack_identity') for sku, review in scoped.items()},
        image_manifest, blocked_images)


def validate_numeric_name_restoration(source: dict, raw_fields: list, review: dict,
                                      title_review: dict | None) -> None:
    """Restore only an exact original numeric GTIN, leaving every unit unresolved."""
    sku, code = source['SKU'], source['MAINUPC']
    disposition, reason, _, _ = classify_record(source)
    if (disposition != 'needs_review'
            or reason != 'Needs a specific product name; source record retained'
            or source['ItemName'] != code or code != review['expectedUpc']
            or source['PackName'] != '' or len(raw_fields) != len(EXPECTED_HEADERS)
            or source['sourceRow'] != review['expectedSourceRow']
            or category_for(clean_space(source['DEPNAME'])) == 'other'):
        raise ValueError(f'Numeric name restoration requires an original matching numeric GTIN and empty pack: {sku}')
    expected = (sku, code, source['sourceRow'], review_evidence_sha256(raw_fields),
                code, 'Size needs confirmation', category_for(clean_space(source['DEPNAME'])),
                clean_space(source['DEPNAME']))
    if (not title_review or tuple(title_review[key] for key in (
            'sku', 'expectedUpc', 'expectedSourceRow', 'expectedRawFieldsSha256',
            'expectedPublicName', 'expectedLabel', 'expectedCategory', 'expectedDepartment')) != expected):
        raise ValueError(f'Numeric name restoration requires its matching guarded public-name review: {sku}')
    title = title_review['proposedPublicTitle']
    literal_code = re.compile(r'(?<![0-9])' + re.escape(code) + r'(?![0-9])')
    if not any(item['kind'] == 'retailer_exact_upc_name'
               and (literal_code.search(item['supports']) or literal_code.search(item['url']))
               and title in item['supports'] for item in title_review['evidence']):
        raise ValueError(f'Numeric name restoration requires explicit exact-UPC retailer name proof: {sku}')
    # A finite combination of known quantity/selling-unit words is still a
    # quantity when written as TwelvePack, singleserve or quartbottle. Match
    # whole compounds, not arbitrary brand substrings such as Quartet.
    counts = (r'(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
              r'thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|'
              r'thirty|forty|fifty|sixty|seventy|eighty|ninety|dozens?|hundreds?|thousands?|'
              r'single|double|triple|twin|multi|multiple|variety)')
    measures = (r'(?:ounces?|oz|(?:kilo|milli|micro)?grams?|kg|mg|mcg|g|pounds?|lbs?|'
                r'(?:milli|centi|deci|kilo)?lit(?:er|re)s?|ml|cl|dl|l|lit|ltr?s?|'
                r'pints?|pts?|quarts?|qts?|gallons?|gals?|feet|foot|ft|met(?:er|re)s?|m)')
    containers = (r'(?:cases?|packs?|packages?|pkgs?|pks?|counts?|ct|units?|pcs?|pieces?|'
                  r'bottles?|btls?|cans?|cartons?|box(?:es)?|bundles?|jars?|jugs?|tubs?|'
                  r'pouch(?:es)?|packets?|bars?|rolls?|bags?|trays?|tins?|cups?|containers?|'
                  r'tubes?|sachets?|sleeves?|sticks?|sets?|pairs?|couples?)')
    joined = (rf'\b(?:{counts}[-_\s]*(?:{measures}|{containers}|serv(?:e|ing)s?|size)|'
              rf'{measures}[-_\s]*{containers}|family[-_\s]*size)\b')
    if re.search(r'\b(?:proof|abv)\b', title, re.I) or re.search(joined, title, re.I):
        raise ValueError(f'Numeric name restoration cannot establish a quantity, container or strength: {sku}')
    validate_name_only_restored_title(title, sku)


def validate_name_only_restored_title(title: str, sku: str) -> None:
    """An exact name-only review cannot establish any quantity or selling unit."""
    parts = extract_dimensions(title)
    unit_or_container = re.search(
        r'\b(?:cases?|packs?|packages?|pkgs?|pks?|counts?|ct|units?|pcs?|pieces?|'
        r'bottles?|btls?|cans?|cartons?|box(?:es)?|bundles?|jars?|tubs?|pouch(?:es)?|'
        r'packets?|bars?|rolls?|bags?|trays?|tins?|cups?|containers?|tubes?|sachets?|'
        r'sleeves?|sticks?|sets?|pairs?|couples?|single|double|triple|twin|multi|multiple|zero|one|two|three|'
        r'four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|'
        r'sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|'
        r'eighty|ninety|dozens?|hundreds?|thousands?|'
        r'ounces?|oz|(?:kilo|milli|micro)?grams?|kg|mg|mcg|g|pounds?|lbs?|'
        r'(?:milli|centi|deci|kilo)?lit(?:er|re)s?|ml|cl|dl|l|lit|ltr?s?|fl(?:uid)?|'
        r'pints?|pts?|quarts?|qts?|gallons?|gals?|feet|foot|ft|met(?:er|re)s?|m|'
        r'servings?|size|net|wt)\b', title, re.I)
    if (re.search(r'\d', title) or unit_or_container
            or parts['measures'] or parts['packs'] or parts['container']):
        raise ValueError(f'Name-only restoration cannot establish a quantity, container or selling unit: {sku}')


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
          public_name_reviews_manifest: dict | None = None,
          private_direct_image_evidence_manifest: dict | None = None) -> tuple[dict, dict]:
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
    source_record_names = {sku for sku, review in public_name_reviews.items()
                           if review.get('identityBasis') == SOURCE_RECORD_NAME_IDENTITY}
    if source_record_names & (set(source_reviews) | set(corrections) | set(identity_reviews)
                              | set(name_verifications) | set(category_reviews)):
        raise ValueError('Source-record name-only review cannot coexist with source, name, identity or category overrides')
    guarded_restorations = {sku for sku, review in source_reviews.items()
                            if review.get('verificationMethod') == NAME_ONLY_RESTORATION_METHOD}
    numeric_restorations = {sku for sku, review in source_reviews.items()
                            if review['action'] == 'restore_verified_numeric_name'}
    numeric_conflicts = {sku for sku, review in source_reviews.items()
                         if review.get('verificationMethod') == NUMERIC_CONFLICT_IDENTITY_METHOD}
    if (guarded_restorations | numeric_restorations | numeric_conflicts) & (set(corrections) | set(identity_reviews) | set(name_verifications) | set(category_reviews)):
        raise ValueError('Name-only restoration cannot coexist with name, identity or category overrides')
    if numeric_conflicts & set(public_name_reviews):
        raise ValueError('Numeric conflict identity cannot coexist with a public-title override')
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
            guarded_restoring = sku in guarded_restorations
            numeric_restoring = sku in numeric_restorations
            resolving = bool(review and review['action'] == 'resolve_verified_identity')
            if review:
                raw_hash = hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
                if not repairing and (source['MAINUPC'], hashlib.sha256(source['ItemName'].encode()).hexdigest(), raw_hash) != (
                        review['expectedUpc'], review['expectedNameSha256'], review['expectedSourceRecordSha256']):
                    raise ValueError(f'Source metadata review identity mismatch for SKU {sku}')
                record['sourceReview'] = review
                if numeric_restoring:
                    validate_numeric_name_restoration(source, fields, review, public_name_reviews.get(sku))
                if sku in numeric_conflicts:
                    validate_numeric_conflict_source(source, fields, review)
                if restoring:
                    adjusted_source['ItemName'] = restored_description(source, review)
                    if guarded_restoring:
                        validate_guarded_name_restoration(source, fields, review, public_name_reviews.get(sku))
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
                disposition, reason, public, flags = classify_record(
                    adjusted_source, defer_specific_name=guarded_restoring or numeric_restoring or sku in numeric_conflicts)
            if manufacturer_baseline and (not public or public['variant'] != manufacturer_baseline['variant']):
                raise ValueError(f'Manufacturer name-only review changed the original variant metadata: {sku}')
            if review:
                if numeric_restoring:
                    if not public or public['name'] != source['MAINUPC']:
                        raise ValueError(f'Numeric name restoration changed its guarded numeric intermediate: {sku}')
                    public['variant']['label'] = 'Size needs confirmation'
                    flags.append('Guarded numeric merchandise name restored; source code and unknown unit preserved privately')
                    flags.append('Restored SKU grouping isolated; image requires its own exact identity review')
                elif restoring:
                    if not public:
                        raise ValueError(f'Reviewed restoration did not produce safe merchandise: {sku}')
                    record['sanitizedSourceName'] = adjusted_source['ItemName']
                    flags.append('Guarded named merchandise restored; original metadata preserved privately')
                    flags.append('Restored SKU grouping isolated; image requires its own exact identity review')
                    if review['forceSizeNeedsConfirmation']:
                        public['variant']['label'] = uncertain_size_label(adjusted_source)
                elif resolving:
                    if sku in numeric_conflicts:
                        if (not public or public['name'] != source['ItemName']
                                or public['variant']['label'] != review['verifiedLabel']):
                            raise ValueError(f'Numeric conflict identity must preserve its numeric intermediate and declared volume: {sku}')
                        record['numericIdentityConflict'] = {
                            'originalMainUpc': source['MAINUPC'], 'originalNumericItemName': source['ItemName'],
                            'originalPackName': source['PackName'], 'alternateLabel': numeric_conflict_details(review)['alternateLabel'],
                            'selectionBasis': review['selectionBasis'], 'conflictDisposition': review['conflictDisposition']}
                    elif not public or public['variant']['label'] != 'Size not specified':
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
                elif review['action'] in {'resolve_verified_pack_identity',
                                           'resolve_verified_pack_total_volume',
                                           'refine_verified_fluid_ounce_label',
                                           'resolve_verified_missing_measurement',
                                           'resolve_verified_ambiguous_ounce_volume'}:
                    # These actions deliberately run after any category/title
                    # review so their guarded current-public identity reflects
                    # the exact final product expression.
                    pass
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
            if review and review['action'] == 'resolve_verified_pack_identity':
                if not public:
                    raise ValueError(f'Pack identity requires retained source merchandise: {sku}')
                record['previousPublicIdentity'] = {
                    'name': public['name'], 'label': public['variant']['label']}
                apply_verified_pack_identity(public, source, review)
                flags.append('Exact UPC package identity enriches only the verified pack, size and container label')
            public_name_review = public_name_reviews.get(sku)
            if public_name_review:
                if sku in identity_reviews:
                    raise ValueError(f'Public-name review cannot override an unresolved identity conflict: {sku}')
                apply_public_name_review(public, source, fields, public_name_review)
                record['publicNameCorrection'] = public_name_review
                applied_public_name_reviews.add(sku)
                flags.append(('Guarded source-record public title correction applied; inventory identifier is not a verified barcode'
                              if public_name_review.get('identityBasis') == SOURCE_RECORD_NAME_IDENTITY else
                              'Guarded exact-UPC public title correction applied; original source name and variant metadata preserved'))
            if guarded_restoring or numeric_restoring:
                if (not public or sku not in applied_public_name_reviews
                        or public['name'] != public_name_review['proposedPublicTitle']
                        or public['variant']['label'] != 'Size needs confirmation'
                        or public['variant']['image'] is not None):
                    raise ValueError(f'Name-only restoration must finish its guarded name with unit and photo unresolved: {sku}')
                if numeric_restoring:
                    record['numericNameRestoration'] = {
                        'originalNumericName': source['ItemName'],
                        'verificationMethod': review['verificationMethod'],
                        'sellingUnitVerified': False, 'photoApproved': False}
                else:
                    record['nameOnlyRestoration'] = {'sanitizedDescriptor': review['sanitizedSourceName'],
                        'sellingUnitVerified': False, 'photoApproved': False}
            if review and review['action'] == 'refine_verified_fluid_ounce_label':
                if not public:
                    raise ValueError(f'Fluid-ounce identity requires retained source merchandise: {sku}')
                record['previousPublicIdentity'] = {
                    'name': public['name'], 'label': public['variant']['label']}
                apply_refined_fluid_ounce_label(public, source, review)
                flags.append('Exact raw source liquid volume refined from ounces to verified fluid ounces')
            if review and review.get('foodSizeResolution'):
                if not public:
                    raise ValueError(f'Food-size identity requires retained source merchandise: {sku}')
                record['previousPublicIdentity'] = {
                    'name': public['name'], 'label': public['variant']['label']}
                apply_verified_food_size(public, source, review)
                flags.append('Previously held food size resolved with guarded exact-UPC package evidence; original fields remain private')
            flags = override_flags + flags
            record.update(source=source, disposition=disposition, reason=reason, flags=flags)
            flag_reasons.update(flags)
            if public:
                # Exact normalized expression and department only. No fuzzy
                # matching, synonym mapping, or deletion of brand/age/proof.
                key = (public['category'], public['department'], public['name'].casefold())
                identity_resolution = review.get('identityResolution') if review else None
                if restoring or numeric_restoring or resolving or repairing or (sku in identity_reviews) or (identity_resolution and not identity_resolution.get('groupWithPriorExpression')):
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
                      if (review['action'] != 'resolve_verified_identity'
                          and not review.get('identityResolution')
                          and not review.get('foodSizeResolution'))}
    blocked_images.update(entry['sku'] for entry in report_additions)
    blocked_images.update(sku for sku, review in identity_reviews.items() if review.get('action') == 'retain_flagged')
    variants_by_sku = {v['sku']: (p, v) for p in products for v in p['variants']}
    for sku, review in source_reviews.items():
        food_resolution = review.get('foodSizeResolution')
        if review['action'] != 'resolve_verified_pack_identity' and food_resolution is None:
            continue
        product, variant = variants_by_sku.get(sku, ({}, {}))
        if not variant:
            raise ValueError(f'Reviewed size variant is absent after grouping: {sku}')
        actual_group = [member['sku'] for member in product['variants']]
        expected_group = (food_resolution or review)['expectedResultingGroupSkus']
        if actual_group != expected_group:
            context = 'Food-size identity' if food_resolution is not None else 'Pack identity'
            raise ValueError(f'{context} final SKU-group mismatch for SKU {sku}')
    # Keep every legacy photo/name gate on its original label. Only the new
    # total-volume action binds its photo after its separate late label repair.
    pack_total_reviews = {sku: review for sku, review in source_reviews.items()
                          if review['action'] == 'resolve_verified_pack_total_volume'}
    legacy_photo_reviews = {sku: review for sku, review in source_reviews.items()
                            if review['action'] != 'resolve_verified_pack_total_volume'}
    variants_by_sku = validate_reviewed_source_photo_bindings(
        products, legacy_photo_reviews, image_manifest, blocked_images)
    validate_numeric_conflict_photos(products, source_reviews, image_manifest,
                                     private_direct_image_evidence_manifest, blocked_images)
    records_by_sku = {record['sku']: record for record in master}
    for sku, verification in name_verifications.items():
        product, variant = variants_by_sku.get(sku, ({}, {}))
        record = records_by_sku.get(sku, {})
        if (not record.get('source') or record.get('sourceOrigin') == 'report'
                or (verification['expectedUpc'], verification['expectedPublicName'], verification['expectedLabel'])
                != (variant.get('upc'), product.get('name'), variant.get('label'))):
            raise ValueError(f'Name verification does not match a current original-source variant: {sku}')
        record['nameVerification'] = verification
    apply_verified_pack_total_volumes(products, records_by_sku, pack_total_reviews, image_manifest,
                                      private_direct_image_evidence_manifest, identity_reviews)
    validate_reviewed_source_photo_bindings(products, pack_total_reviews, image_manifest, blocked_images)
    apply_missing_measurements(products, records_by_sku, source_reviews, image_manifest,
                               private_direct_image_evidence_manifest, blocked_images, identity_reviews)
    apply_ambiguous_liquid_ounces(products, records_by_sku, source_reviews, image_manifest,
                                 private_direct_image_evidence_manifest, blocked_images, identity_reviews)
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
    verified_images = apply_verified_images(
        products, image_manifest, blocked_images, private_direct_image_evidence_manifest)
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
    parser.add_argument('--private-direct-image-evidence', type=Path,
                        help='Required outside-repository provenance manifest when direct-reference images exist')
    parser.add_argument('--private-review-evidence', type=Path,
                        help='Outside-repository evidence ledger for hash-referenced review records')
    parser.add_argument('--names', type=Path, default=ROOT / 'data/name-corrections.json')
    parser.add_argument('--source-reviews', type=Path, default=ROOT / 'data/source-reviews.json')
    parser.add_argument('--report-additions', type=Path, default=ROOT / 'data/report-additions.json')
    parser.add_argument('--category-reviews', type=Path, default=ROOT / 'data/category-reviews.json')
    parser.add_argument('--public-name-reviews', type=Path, default=DEFAULT_PUBLIC_NAME_REVIEWS,
                        help='Privacy-safe guarded title review manifest (default: data/public-name-reviews.json)')
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if args.private_review_evidence:
        os.environ['QUICKSTOP_PRIVATE_REVIEW_EVIDENCE'] = str(safe_private_path(args.private_review_evidence))
    source_path = safe_private_path(args.input)
    private_output = safe_private_path(args.private_output)
    private_direct_image_evidence = None
    if args.private_direct_image_evidence:
        evidence_path = safe_private_path(args.private_direct_image_evidence)
        private_direct_image_evidence = json.loads(evidence_path.read_text())
    source_bytes = source_path.read_bytes()
    image_manifest = json.loads(args.images.read_text()) if args.images.exists() else None
    name_manifest = json.loads(args.names.read_text()) if args.names.exists() else None
    source_reviews = json.loads(args.source_reviews.read_text()) if args.source_reviews.exists() else None
    report_additions = json.loads(args.report_additions.read_text()) if args.report_additions.exists() else None
    category_reviews = json.loads(args.category_reviews.read_text()) if args.category_reviews.exists() else None
    public_name_reviews = (json.loads(args.public_name_reviews.read_text())
                           if args.public_name_reviews.exists() else None)
    catalog, audit = build(source_bytes, args.source_date, image_manifest, name_manifest, source_reviews,
                           report_additions, category_reviews, public_name_reviews,
                           private_direct_image_evidence)
    private_output.mkdir(parents=True, exist_ok=True, mode=0o700)
    (private_output / 'master-reconciliation.json').write_text(json.dumps(audit, indent=2) + '\n')
    (private_output / 'review-queue.json').write_text(json.dumps([
        r for r in audit['records'] if r['disposition'] == 'needs_review' or r['flags']], indent=2) + '\n')
    names = name_audit(catalog, audit)
    (private_output / 'all-names-audit.json').write_text(json.dumps(names, indent=2) + '\n')
    (private_output / 'name-audit-summary.json').write_text(json.dumps(names['summary'], indent=2) + '\n')
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, separators=(',', ':')) + '\n')
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
