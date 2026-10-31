#!/usr/bin/env python3
"""Keep review guards public and move their evidence arrays to a private ledger."""
import argparse
import json
import os
from pathlib import Path
import build_catalog as builder

FILES = ('source-reviews.json', 'name-corrections.json',
         'category-reviews.json', 'public-name-reviews.json')


def project_evidence(node, records):
    """Return public hash references and append their immutable private records."""
    if isinstance(node, list):
        return [project_evidence(item, records) for item in node]
    if not isinstance(node, dict):
        return node
    result = {}
    for key, value in node.items():
        if key == 'evidence' and isinstance(value, list) and value:
            digest = builder.review_evidence_sha256(value)
            if digest in records and records[digest] != value:
                raise ValueError('Refuse to replace different private evidence')
            records[digest] = value
            result[key] = {'privateEvidenceSha256': digest}
        else:
            result[key] = project_evidence(value, records)
    return result


def atomic_json(path, value, private=False):
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    if private:
        temporary.chmod(0o600)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-evidence', type=Path, required=True)
    parser.add_argument('--private-backups', type=Path, required=True)
    args = parser.parse_args()
    destination = builder.safe_private_path(args.private_evidence)
    backups = builder.safe_private_path(args.private_backups)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    ledger = (json.loads(destination.read_text()) if destination.exists() else
              {'schemaVersion': 1, 'kind': builder.PRIVATE_REVIEW_EVIDENCE_KIND, 'records': {}})
    if (set(ledger) != {'schemaVersion', 'kind', 'records'} or ledger['schemaVersion'] != 1
            or ledger['kind'] != builder.PRIVATE_REVIEW_EVIDENCE_KIND
            or not isinstance(ledger['records'], dict)):
        raise ValueError('Invalid existing private review evidence ledger')
    for digest, record in ledger['records'].items():
        if not isinstance(record, list) or not record or builder.review_evidence_sha256(record) != digest:
            raise ValueError('Existing private evidence has a hash mismatch')

    projected, originals = {}, {}
    for name in FILES:
        path = builder.ROOT / 'data' / name
        raw = path.read_bytes()
        originals[name] = json.loads(raw)
        projected[name] = project_evidence(originals[name], ledger['records'])
        backup = backups / name
        if backup.exists() and backup.read_bytes() != raw:
            raise ValueError('Refuse to replace a different original backup')
        if not backup.exists():
            backup.write_bytes(raw)
            backup.chmod(0o600)
    # Persist and verify private originals before replacing any public file.
    atomic_json(destination, ledger, private=True)
    os.environ['QUICKSTOP_PRIVATE_REVIEW_EVIDENCE'] = str(destination)
    for name in FILES:
        if builder.expand_private_review_evidence(projected[name]) != builder.expand_private_review_evidence(originals[name]):
            raise ValueError('Projection changes review semantics')
    for name in FILES:
        atomic_json(builder.ROOT / 'data' / name, projected[name])
    print(json.dumps({'projectedFiles': len(FILES), 'privateEvidenceRecords': len(ledger['records'])}))


if __name__ == '__main__':
    main()
