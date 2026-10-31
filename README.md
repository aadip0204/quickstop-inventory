# Quick Stop Food & Liquor

A static, searchable in-store catalog. The site does not offer checkout or report live stock. Its inventory baseline is the original February 2026 export, supplemented by individually reviewed store-report SKUs. Reports do not establish live availability.

## Local preview

Run `python3 -m http.server 8765 --bind 127.0.0.1` in this directory, then open http://127.0.0.1:8765. The site needs HTTP because it fetches `catalog.json`.

## Private inventory build

Keep the original CSV, private reports, direct-image provenance and review-evidence ledger **outside this repository**. Never upload the source export, raw backups, reconciliation records, `direct-reference-evidence-v1.json` or `review-evidence-v1.json`: they contain private commercial or research fields. The builder accepts explicit paths and emits a field-allowlisted public catalog. See `python3 scripts/build_catalog.py --help` for exact options.

Build-only review files retain source/UPC guards and content hashes. Their original evidence arrays, including photograph URLs, are resolved from the external review-evidence ledger before the existing strict validators run. Missing or altered evidence fails the build. After preparing new reviews, use `python3 scripts/project_review_evidence.py --private-evidence /private/quickstop/review-evidence-v1.json --private-backups /private/quickstop/review-backups/RELEASE` to preserve their exact original files privately and project the evidence references. These four review files are also excluded from Pages.

The original export is immutable. Its SHA-256 is `822ee8b3b623548db0f2ea531aa10c340b160a47afc0d9e88398311ed7fa6882`.

Reviewed, price-free report additions live in `data/report-additions.json`, with source-description hashes and PDF hash/page references. Raw reports and extracted rows stay private. Report SKUs have unknown UPCs, isolated product groups and no inherited images. Eight original malformed records are repaired only with exact original-field and report-description guards.

Each source row has a private disposition: retained variant, retained duplicate reference if applicable, needs review/name, or explicitly excluded nonproduct. Source SKUs and UPCs remain strings. All retained variants participate in search; pagination only bounds rendered cards. Metadata conflicts remain private and display a neutral confirmation label.

## Product identity and images

Grouping is conservative: only identical expressions after recognized size/pack extraction share a card. Brand numbers, ages, proof, vintage and flavor remain part of identity. Original departments are preserved under broader navigation categories. No fuzzy matching is used.

Explicit trailing BTL/BTLS markers become bottle labels alongside the original measures and pack counts. When size metadata conflicts, the abbreviation stays in the product name and the size remains unconfirmed. Reviewed shopping-category corrections apply only to guarded source SKUs, preserving their original departments.

Reviewed photographs are installed per SKU and shown only for the exact selected size. Public cards, catalog data, image manifests and deployed files contain no picture-source, credit or license links. Exact product identity, quality and origin records are held in an explicit private evidence manifest; the build fails closed without a matching private record for every public image. The owner authorized direct internet product references; that approval does not claim copyright-holder permission or a license. Photos remain absent where product identity or professional presentation is unresolved. The builder requires an explicit professional-quality review as well as exact identity checks; handheld and household snapshots are not approved for display. Existing photos from the earlier site were unverified and have been removed. The source-row image audit is private; aggregate status is recorded in `docs/repair-status.md`.

A CSV comma repair stays isolated from other products. Its image remains blocked unless its source review names the exact current product, size and image hash, confirms all six identity checks, and links manufacturer, exact-UPC and inspected-photo evidence. That exception requires a matching private-backed professional image record; it cannot approve another SKU or bypass file-hash validation.

A previously uncertain identity can be resolved only by a guarded review matching the original row, UPC, current name and confirmation label. Manufacturer, exact-UPC and inspected-package evidence must agree on the new identity and size. Resolved records stay isolated, require a valid retained UPC, and still need their own private-backed professional image approval. Unchanged names have separate verification records so reviewing a correct name does not inflate the correction count.

Names may also be checked against a captured manufacturer record that explicitly states the exact valid UPC and product expression. This separate name-only method records the publisher, source URL and captured-page hash; it preserves original sizes, packs, containers and strength statements. Delegated SmartLabel records require the manufacturer's exact sitemap reference and matching UPC in the product path. Historical retired records are marked as such and do not establish current availability. These checks do not approve photographs or resolve package-size conflicts, and their counts remain separate in the audit. A licensed manufacturer may provide the record when its manufacturing relationship is supported by primary evidence; this does not imply ownership of the brand or permission to reuse its photographs.

## Validation and deployment

Run the Python tests with `python3 -m unittest discover -s tests -v`, and the frontend tests with `node --test tests/catalog-ui.test.cjs`. For full-source reconciliation, set `QUICKSTOP_PRIVATE_INPUT` to the absolute CSV path, `QUICKSTOP_PRIVATE_DIRECT_IMAGE_EVIDENCE` to the external direct-image evidence manifest, and `QUICKSTOP_PRIVATE_REVIEW_EVIDENCE` to the external review-evidence ledger. The production builder accepts `--private-direct-image-evidence PATH` and `--private-review-evidence PATH`; both are required for the current catalog. Never place private inputs in CI.

The existing repository deploys GitHub Pages from its publishing branch. Preserve that configuration. Review and test a repair branch, push through authenticated GitHub access, and follow repository branch protections. Do not force-push or rewrite history. `_config.yml` excludes build tools, tests and maintainer reports from Pages.

After publication, check the actual live URL, new catalog counts/hash, searches, category navigation, logo, error-free rendering, and removal of all three legacy data URLs. A commit or successful upload alone is not a verified live deployment.

Historical price-bearing files remain in older Git commits. Deleting current files does not erase repository history, external copies, or caches. History rewriting requires separate explicit authorization.
