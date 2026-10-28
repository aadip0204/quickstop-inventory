# Repair status

Catalog repairs are implemented and tested. The first repair release was verified on the actual GitHub Pages site on October 27, 2026. Complete professional image coverage and external verification of every product name remain unfinished.

## Inventory and names

- All 9,881 original rows and distinct SKUs are preserved in private reconciliation outside this repository.
- Store reports add 67 merchandise SKUs and four explicitly excluded POS controls. Eight exact-SKU report descriptions repair unquoted CSV commas: five merchandise listings are restored and three lottery rows receive explicit exclusion. All 9,881 original rows remain, including 12 absent from later reports. The combined reconciliation covers 9,952 records. New report records do not supply UPCs or photos and stay isolated pending identity review.
- The current catalog contains 9,689 variants in 7,998 conservative product groups.
- 107 rows require identity/name review; 156 explicit nonproducts are excluded with private reasons. No source SKU is silently discarded. A guarded review restored 153 named merchandise rows by removing explicit price annotations or withholding an ambiguous size; these SKUs remain isolated and cannot inherit photos.
- 797 exact, source-hash/UPC-guarded spelling, spacing or name clarifications are applied. Every original name remains private and unchanged. All rows were scanned for defined risks. Only 501 complete names have external identity verification; the other corrections have narrower documented scopes. There are 83 separate unchanged-name verification records, which do not count as corrections. Twenty-three uncertain identities were resolved using manufacturer, exact-UPC and inspected-package evidence. The final eight spelling flags received explicit review: seven scoped repairs and the intentional manufacturer spelling “Blue Razberry,” which remains unchanged.
- October report evidence distinguishes glass and PET containers for two exact Dimitri Gin SKUs without changing their original volumes or UPCs. Clipped identifiers in the narrow reports are not guessed into catalog joins.
- Ambiguous sizes display a confirmation label. Differing flavors, strength, age, packaging and unknown identities are not guessed into one product.

## Images

Only exact SKU/UPC, expression, size and packaging matches with documented reuse terms and a completed professional presentation review may enter the public manifest. Each selected variant has its own image; changing size cannot inherit a sibling photo.

Consumer snapshots were removed after the owner clarified that images must be professional. Clean studio packshots on plain backgrounds are required. No generated replacements or generic brand photos are used. 146 variants currently have approved professional images; 9,543 still lack one. `data-summary.json` tracks these counts. Missing images are deliberately absent. Private research queues cover all 9,952 reconciled source rows and distinguish candidates, actual visual reviews, rejected images and approved assignments. Some licensed originals have limited resolution; none were synthetically enlarged or altered.

Professional image sourcing is ongoing. Manufacturer/distributor feeds or studio photography are still needed for items that cannot be matched to a suitable licensed image. Unreviewed bulk barcode matches are not treated as approvals.

## Implementation and checks

- Warm, responsive retail layout with the supplied logo, seven main categories and all 29 original departments.
- Product cards show no photo-source lines. Required attribution and licenses remain on a separate Image credits page linked once in the footer and regenerated with the catalog.
- Search covers every retained SKU/UPC, product name and size; pagination limits rendering to 36 cards without limiting the catalog.
- No prices or stock quantities are emitted by the public data builder. Current legacy price-bearing files and unverified old photographs are removed. Older Git history is unchanged.
- Loading, retry, empty results, keyboard navigation, selected sizes and filter URL state are implemented.
- 64 Python tests and eight frontend tests pass, including original-source conservation, safe grouping, price removal, guarded restoration and image validation. The new identity checks reject absent, whitespace, descriptive and padded UPCs while accepting valid EAN-8 identifiers. Browser checks cover every category/department, size-specific photographs, narrow mobile layouts, pagination and error recovery.
- Local HTTP asset hashes and all approved image responses are checked using `scripts/verify_deployment.py`. Browser checks remain required after publication.

## Deployment

The repository's existing GitHub Pages publishing configuration is preserved. No force push or history rewrite is authorized.

GitHub access was connected on October 27, 2026, and the authentication blocker is resolved. PR #1 was merged through the existing publishing workflow. Pages run 40460698603 succeeded, and the actual deployed HTML, CSS, script, catalog, logo and all 86 photographs in that first release matched the tested files. All three retired price-bearing data URLs returned 404. Live category, department, search, pagination and size-photo switching checks passed.

PR #2 added 40 approved photographs, 153 restored merchandise rows and further guarded name corrections. Pages run 40464647575 succeeded and the actual release assets were verified.

PR #3 integrated 72 merchandise listings (67 new SKUs and five CSV repairs), 25 additional scoped name corrections, two container-material clarifications and store contact information supported by the supplied reports. Pages run 40519389671 succeeded; the actual live assets and browser behavior were verified.

PR #4 added 104 guarded name corrections, including 28 full names checked against the exact approved package photographs and primary brand evidence, and the exact Breyers Vanilla Chocolate Strawberry 1.5 qt photograph. Pages run 40524050182 succeeded; all deployed assets and live browser behavior were verified.

PR #5 checked 101 additional complete names and added seven exact photographs while withdrawing the ambiguous Slim Jim photo. Pages run 40530323627 succeeded; the actual live catalog, all 133 eligible photographs, retired URLs and browser behavior were verified.

PR #6 checked 100 additional complete names, added six exact professional photographs and quarantined two source name/UPC conflicts without losing their private source records. Pages run 40556908741 succeeded; actual live assets and browser behavior were verified.

PR #7 checked 72 additional complete names, added five exact professional photographs and corrected 17 shopping categories with exact source guards. Pages run 40561508723 succeeded; actual live assets and browser behavior were verified.

PR #8 checked 71 additional complete names, added the exact Calypso Strawberry Lemonade 16 oz photograph, recognized 19 explicit bottle abbreviations and corrected six AriZona shopping categories. Pages run 40565687698 succeeded; actual deployed assets and browser behavior were verified. Reese's and Cretors photos remain withheld because competing package evidence does not resolve the unknown source weights.

PR #9 added 79 complete name checks from captured exact-UPC manufacturer records and corrected five Kool-Aid shopping categories. Pages run 40569821731 succeeded; the actual catalog, all 145 approved image files and live browser behavior were verified. Manufacturer-record name checks remain separate from visual verification and never infer package sizes or current stock.

The current catalog adds 36 manufacturer-record name checks: 23 guarded corrections and 13 unchanged-name verifications. Nine Crush records use PepsiCo's exact unit-UPC product catalog, supported by its 2025 Form 10-K description of licensed manufacturing and distribution. Three exact Crush SKUs move into Drinks & mixers while retaining their original MISC department.

One previously unspecified-size Sweet Baby Ray's record is resolved to Hickory & Brown Sugar Barbecue Sauce 18 oz using manufacturer package evidence and two exact-UPC retailer listings. Its new licensed professional bottle photo is the inspected original, with attribution preserved. This adds one complete name verification and one photo; it does not infer a size for any other SKU.

All 9,952 source records and all 9,689 public SKU/UPC pairs remain intact. The other 9,688 complete variant objects and all 145 prior image files are unchanged. Full name and photo coverage remain unfinished; detailed source reviews and dated deployment proofs stay outside the public repository.

PR #10 was verified through Pages run 40630916330, including all 146 image files and all 37 newly verified product cases. The subsequent card-display change moves source lines to a separate credits page without changing catalog entries or image files.

After authenticated publication, run `python3 scripts/verify_deployment.py https://aadip0204.github.io/quickstop-inventory/` against the exact tested checkout and repeat browser checks before declaring the release live.
