# Repair status

Implementation is tested locally. GitHub publication is being prepared; complete professional image coverage and external verification of every product name remain unfinished.

## Inventory and names

- All 9,881 original rows and distinct SKUs are preserved in private reconciliation outside this repository.
- The public catalog contains 9,466 variants in 7,780 conservative product groups.
- 266 rows require identity/name review; 149 explicit nonproducts are excluded with private reasons. No source SKU is silently discarded.
- 287 exact, source-hash/UPC-guarded spelling or spacing corrections are applied. Every original name remains private and unchanged. All rows were scanned for defined risks; this is not a claim that every name was externally verified.
- Ambiguous sizes display a confirmation label. Differing flavors, strength, age, packaging and unknown identities are not guessed into one product.

## Images

Only exact SKU/UPC, expression, size and packaging matches with documented reuse terms and a completed professional presentation review may enter the public manifest. Each selected variant has its own image; changing size cannot inherit a sibling photo.

Consumer snapshots were removed after the owner clarified that images must be professional. Clean studio packshots on plain backgrounds are required. No generated replacements or generic brand photos are used. 86 variants currently have approved professional images; 9,380 still lack one. `data-summary.json` tracks these counts. Missing images are deliberately absent. Private research queues cover all 9,881 source rows and distinguish candidates, actual visual reviews, rejected images and approved assignments.

Professional image sourcing is ongoing. Manufacturer/distributor feeds or studio photography are still needed for items that cannot be matched to a suitable licensed image. Unreviewed bulk barcode matches are not treated as approvals.

## Implementation and checks

- Warm, responsive retail layout with the supplied logo, seven main categories and all 29 original departments.
- Search covers every retained SKU/UPC, product name and size; pagination limits rendering to 36 cards without limiting the catalog.
- No prices or stock quantities are emitted by the public data builder. Current legacy price-bearing files and unverified old photographs are removed. Older Git history is unchanged.
- Loading, retry, empty results, keyboard navigation, selected sizes and filter URL state are implemented.
- 25 Python tests and seven frontend tests pass, including original-source conservation, safe grouping, price removal and image validation. Browser checks cover every category/department, size-specific photographs, narrow mobile layouts, pagination and error recovery.
- Local HTTP asset hashes and all approved image responses are checked using `scripts/verify_deployment.py`. Browser checks remain required after publication.

## Deployment

The repository's existing GitHub Pages publishing configuration is preserved. No force push or history rewrite is authorized.

GitHub access was connected on October 27, 2026, and repository write permission is verified. The earlier authentication blocker is resolved. The last live check still showed the prior release and legacy data files; its `catalog.json` returned 404. Publication and live verification are pending. A local preview or commit must not be described as a live update.

After authenticated publication, run `python3 scripts/verify_deployment.py https://aadip0204.github.io/quickstop-inventory/` against the exact tested checkout and repeat browser checks before declaring the release live.
