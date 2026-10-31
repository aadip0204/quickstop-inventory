#!/usr/bin/env python3
"""Verify served release bytes and retirement of legacy data after Pages publishes.

This supplements (does not replace) browser interaction and responsive checks.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
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


def validate_public_image_privacy(catalog, manifest):
    """Require every public product image to use the source-free projection."""
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('images'), list):
        raise AssertionError('Public image manifest is invalid')
    manifest_direct = {}
    for entry in manifest['images']:
        sku = entry.get('sku')
        if set(entry) != DIRECT_PUBLIC_MANIFEST_FIELDS:
            raise AssertionError(f'Public image manifest exposes provenance or unsupported fields: {sku}')
        if (entry.get('usageBasis') != 'user_authorized_direct_reference'
                or entry.get('attributionRequired') is not False):
            raise AssertionError(f'Public image manifest has an unsupported usage policy: {sku}')
        if sku in manifest_direct:
            raise AssertionError(f'Duplicate public image manifest SKU: {sku}')
        manifest_direct[sku] = entry
    catalog_direct = {}
    for product in catalog['products']:
        for variant in product['variants']:
            image = variant.get('image')
            if not image:
                continue
            sku = variant['sku']
            if set(image) != DIRECT_PUBLIC_IMAGE_FIELDS:
                raise AssertionError(f'Public catalog image exposes provenance or unsupported fields: {sku}')
            if (image.get('usageBasis') != 'user_authorized_direct_reference'
                    or image.get('attributionRequired') is not False):
                raise AssertionError(f'Public catalog image has an unsupported usage policy: {sku}')
            if sku in catalog_direct:
                raise AssertionError(f'Duplicate public catalog image SKU: {sku}')
            catalog_direct[sku] = image
    if set(catalog_direct) != set(manifest_direct):
        raise AssertionError('Public catalog and manifest image assignments differ')
    return len(catalog_direct)


def verify(base_url, root):
    checks = []
    catalog = json.loads((root / "catalog.json").read_text())
    manifest = json.loads((root / "data/image-manifest.json").read_text())
    direct_count = validate_public_image_privacy(catalog, manifest)
    if (root / 'image-credits.html').exists():
        raise AssertionError('Deprecated public image-credits page is still present in the checkout')
    photo_paths = sorted({variant["image"]["src"]
                          for product in catalog["products"]
                          for variant in product["variants"] if variant["image"]})
    def check_asset(name):
        expected = (root / name).read_bytes()
        actual = fetch(urllib.parse.urljoin(base_url, name))
        digest = hashlib.sha256(actual).hexdigest()
        if actual != expected:
            raise AssertionError(f"Live {name} does not match the tested checkout")
        return {"path": name, "matchesCheckout": True, "sha256": digest}

    # Keep every byte check while bounding both network and memory use. Results
    # retain their original order so release audits remain easy to compare.
    asset_names = ["index.html", "style.css", "script.js", "catalog.json", "data/image-manifest.json", "images/logo.png", *photo_paths]
    executor = ThreadPoolExecutor(max_workers=4)
    try:
        checks.extend(executor.map(check_asset, asset_names))
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    for name in ("products.js", "products.json", "products_grouped_filtered.js", "image-credits.html"):
        try:
            fetch(urllib.parse.urljoin(base_url, name))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            checks.append({"path": name, "status": 404})
        else:
            raise AssertionError(f"Retired public artifact is still served: {name}")
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
