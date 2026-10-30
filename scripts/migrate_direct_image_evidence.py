#!/usr/bin/env python3
"""Move direct-reference image provenance out of the public manifest.

The source URLs that support user-authorized direct product references are
private review evidence. This one-time-safe migration writes an external
evidence manifest, then removes only those URL fields from direct entries in
the public manifest. Licensed image records are deliberately left untouched.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_catalog import (
    DIRECT_REFERENCE_PUBLIC_FIELDS,
    PRIVATE_DIRECT_IMAGE_EVIDENCE_FIELDS,
    PRIVATE_DIRECT_IMAGE_EVIDENCE_KIND,
    ROOT,
    safe_private_path,
)


def migrate(manifest_path: Path, private_output: Path) -> tuple[int, int]:
    manifest_path = manifest_path.resolve()
    if manifest_path != ROOT / 'data/image-manifest.json':
        raise ValueError('Only the repository public image manifest may be migrated')
    private_output = safe_private_path(private_output)
    if private_output.exists():
        raise FileExistsError('Refusing to overwrite an existing private evidence file')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('images'), list):
        raise ValueError('Invalid public image manifest schema')
    private_entries = []
    licensed_count = 0
    for entry in manifest['images']:
        if not isinstance(entry, dict):
            raise ValueError('Public image manifest entries must be objects')
        if entry.get('usageBasis') != 'user_authorized_direct_reference':
            licensed_count += 1
            continue
        if set(entry) != PRIVATE_DIRECT_IMAGE_EVIDENCE_FIELDS:
            raise ValueError('Direct-reference entry is not in the expected pre-migration shape: '
                             + str(entry.get('sku')))
        private_entries.append(dict(entry))
        entry.pop('sourcePage')
        entry.pop('sourceImageUrl')
        if set(entry) != DIRECT_REFERENCE_PUBLIC_FIELDS:
            raise ValueError('Direct-reference public projection has an unexpected shape: '
                             + str(entry.get('sku')))
    if not private_entries:
        raise ValueError('No direct-reference entries found to migrate')
    private_output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = {
        'schemaVersion': 1,
        'kind': PRIVATE_DIRECT_IMAGE_EVIDENCE_KIND,
        'entries': private_entries,
    }
    private_output.write_text(json.dumps(private, ensure_ascii=False, indent=2) + '\n')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return len(private_entries), licensed_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-output', required=True, type=Path,
                        help='New evidence manifest outside the public repository')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data/image-manifest.json')
    args = parser.parse_args()
    direct_count, licensed_count = migrate(args.manifest, args.private_output)
    print(json.dumps({'directEvidenceEntries': direct_count,
                      'licensedEntriesUnchanged': licensed_count}))


if __name__ == '__main__':
    main()
