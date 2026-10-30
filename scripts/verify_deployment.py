#!/usr/bin/env python3
"""Verify served release bytes and retirement of legacy data after Pages publishes.

This supplements (does not replace) browser interaction and responsive checks.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


DIRECT_PUBLIC_IMAGE_FIELDS = {'src', 'alt', 'usageBasis', 'attributionRequired'}
DIRECT_PUBLIC_MANIFEST_FIELDS = {
    'sku', 'expectedUpc', 'expectedName', 'expectedLabel', 'src', 'alt',
    'sha256', 'verifiedOn', 'verification', 'usageBasis', 'attributionRequired',
}


def fetch(url):
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def validate_direct_reference_privacy(catalog, manifest):
    """Require direct-reference provenance to stay outside public artifacts."""
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('images'), list):
        raise AssertionError('Public image manifest is invalid')
    manifest_direct = {}
    for entry in manifest['images']:
        if entry.get('usageBasis') != 'user_authorized_direct_reference':
            continue
        sku = entry.get('sku')
        if set(entry) != DIRECT_PUBLIC_MANIFEST_FIELDS:
            raise AssertionError(f'Public direct image manifest exposes provenance or unsupported fields: {sku}')
        if sku in manifest_direct:
            raise AssertionError(f'Duplicate public direct image manifest SKU: {sku}')
        manifest_direct[sku] = entry
    catalog_direct = {}
    for product in catalog['products']:
        for variant in product['variants']:
            image = variant.get('image')
            if not image or image.get('usageBasis') != 'user_authorized_direct_reference':
                continue
            sku = variant['sku']
            if set(image) != DIRECT_PUBLIC_IMAGE_FIELDS:
                raise AssertionError(f'Public catalog direct image exposes provenance or unsupported fields: {sku}')
            if sku in catalog_direct:
                raise AssertionError(f'Duplicate public direct catalog image SKU: {sku}')
            catalog_direct[sku] = image
    if set(catalog_direct) != set(manifest_direct):
        raise AssertionError('Public direct catalog and manifest assignments differ')
    return len(catalog_direct)


def verify(base_url, root):
    checks = []
    catalog = json.loads((root / "catalog.json").read_text())
    manifest = json.loads((root / "data/image-manifest.json").read_text())
    direct_count = validate_direct_reference_privacy(catalog, manifest)
    photo_paths = sorted({variant["image"]["src"]
                          for product in catalog["products"]
                          for variant in product["variants"] if variant["image"]})
    for name in ["index.html", "style.css", "script.js", "catalog.json", "data/image-manifest.json", "image-credits.html", "images/logo.png", *photo_paths]:
        expected = (root / name).read_bytes()
        actual = fetch(urllib.parse.urljoin(base_url, name))
        digest = hashlib.sha256(actual).hexdigest()
        if actual != expected:
            raise AssertionError(f"Live {name} does not match the tested checkout")
        checks.append({"path": name, "matchesCheckout": True, "sha256": digest})
    for name in ("products.js", "products.json", "products_grouped_filtered.js"):
        try:
            fetch(urllib.parse.urljoin(base_url, name))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            checks.append({"path": name, "status": 404})
        else:
            raise AssertionError(f"Legacy data is still publicly served: {name}")
    return {"url": base_url, "assetChecks": checks, "liveCatalogCounts": catalog["counts"],
            "verifiedPhotoAssetsServed": len(photo_paths),
            "directReferenceImagesWithoutPublicProvenance": direct_count,
            "browserChecksStillRequired": ["search", "category navigation", "variant selection", "image loading", "responsive layout", "console"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Published site URL, including the project path")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(verify(args.url.rstrip("/") + "/", args.root), indent=2))
