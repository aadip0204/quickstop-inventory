#!/usr/bin/env python3
"""Safely add direct-reference image approvals to public/private manifests.

Candidate records include provenance URLs, so they are deliberately read from
an external private file. The public manifest receives only its allowlisted
projection; the complete candidate is merged into the external evidence file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from build_catalog import (
    DIRECT_REFERENCE_PUBLIC_FIELDS,
    PRIVATE_DIRECT_IMAGE_EVIDENCE_FIELDS,
    PRIVATE_DIRECT_IMAGE_EVIDENCE_KIND,
    ROOT,
    safe_private_path,
    validate_private_direct_image_evidence,
)


def load_private_manifest(path: Path) -> dict:
    path = safe_private_path(path)
    return json.loads(path.read_text())


def validate_update_entries(updates: dict) -> list[dict]:
    if (not isinstance(updates, dict)
            or set(updates) != {'schemaVersion', 'kind', 'entries'}
            or updates.get('schemaVersion') != 1
            or updates.get('kind') != PRIVATE_DIRECT_IMAGE_EVIDENCE_KIND
            or not isinstance(updates.get('entries'), list)
            or not updates['entries']):
        raise ValueError('Invalid private direct-image update manifest')
    seen = set()
    for entry in updates['entries']:
        if not isinstance(entry, dict) or set(entry) != PRIVATE_DIRECT_IMAGE_EVIDENCE_FIELDS:
            raise ValueError('Private direct-image update has missing or unsupported fields')
        sku = entry.get('sku')
        if not isinstance(sku, str) or sku in seen:
            raise ValueError('Private direct-image update SKU is missing or duplicate')
        seen.add(sku)
        if entry.get('usageBasis') != 'user_authorized_direct_reference' or entry.get('attributionRequired') is not False:
            raise ValueError(f'Private direct-image update has an unsupported usage policy: {sku}')
        asset = (ROOT / entry['src']).resolve()
        if ROOT / 'images/products' not in asset.parents or not asset.is_file():
            raise ValueError(f'Private direct-image update has a missing or unsafe asset: {sku}')
        if hashlib.sha256(asset.read_bytes()).hexdigest() != entry.get('sha256'):
            raise ValueError(f'Private direct-image update asset hash mismatch: {sku}')
    return updates['entries']


def sync(manifest_path: Path, evidence_path: Path, update_path: Path,
         replace_licensed_skus: set[str], replace_direct_skus: set[str]) -> tuple[int, int, int]:
    manifest_path = manifest_path.resolve()
    if manifest_path != ROOT / 'data/image-manifest.json':
        raise ValueError('Only the repository public image manifest may be updated')
    evidence_path = safe_private_path(evidence_path)
    manifest = json.loads(manifest_path.read_text())
    evidence = load_private_manifest(evidence_path)
    updates = validate_update_entries(load_private_manifest(update_path))
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('images'), list):
        raise ValueError('Invalid public image manifest schema')
    # Validate existing bindings before changing anything.
    validate_private_direct_image_evidence(manifest, evidence)
    indexes = {entry.get('sku'): index for index, entry in enumerate(manifest['images'])}
    existing_evidence = {entry.get('sku'): entry for entry in evidence['entries']}
    if replace_licensed_skus & replace_direct_skus:
        raise ValueError('A SKU cannot be both a licensed and direct-image replacement')
    replaced_licensed = set()
    replaced_direct = set()
    for update in updates:
        sku = update['sku']
        public_entry = {field: update[field] for field in DIRECT_REFERENCE_PUBLIC_FIELDS}
        if sku in indexes:
            existing = manifest['images'][indexes[sku]]
            is_direct = existing.get('usageBasis') == 'user_authorized_direct_reference'
            if is_direct and sku not in replace_direct_skus:
                raise ValueError(f'Public direct image manifest SKU requires --replace-direct-sku: {sku}')
            if not is_direct and sku not in replace_licensed_skus:
                raise ValueError(f'Public image manifest SKU already exists: {sku}')
            manifest['images'][indexes[sku]] = public_entry
            if is_direct:
                replaced_direct.add(sku)
            else:
                replaced_licensed.add(sku)
        else:
            manifest['images'].append(public_entry)
        existing_evidence[sku] = update
    if replaced_licensed != replace_licensed_skus:
        raise ValueError('Requested licensed-image replacements do not exactly match public manifest collisions')
    if replaced_direct != replace_direct_skus:
        raise ValueError('Requested direct-image replacements do not exactly match public manifest collisions')
    evidence['entries'] = [entry for entry in evidence['entries'] if entry['sku'] not in {u['sku'] for u in updates}]
    evidence['entries'].extend(updates)
    validate_private_direct_image_evidence(manifest, evidence)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    return len(updates), len(replaced_licensed), len(replaced_direct)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-evidence', required=True, type=Path)
    parser.add_argument('--private-updates', required=True, type=Path)
    parser.add_argument('--replace-licensed-sku', action='append', default=[],
                        help='SKU whose existing licensed manifest entry this direct image may replace')
    parser.add_argument('--replace-direct-sku', action='append', default=[],
                        help='SKU whose existing direct manifest entry may receive a guarded metadata refresh')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data/image-manifest.json')
    args = parser.parse_args()
    added, licensed_replaced, direct_replaced = sync(
        args.manifest, args.private_evidence, args.private_updates,
        set(args.replace_licensed_sku), set(args.replace_direct_sku))
    print(json.dumps({
        'directImageUpdates': added,
        'licensedImageReplacements': licensed_replaced,
        'directImageMetadataReplacements': direct_replaced,
    }))


if __name__ == '__main__':
    main()
