# Repair status

Catalog repairs are implemented and tested. The first repair release was verified on the actual GitHub Pages site on October 27, 2026. Complete professional image coverage and external verification of every product name remain unfinished.

## Inventory and names

- All 9,881 original rows and distinct SKUs are preserved in private reconciliation outside this repository.
- Store reports add 67 merchandise SKUs and four explicitly excluded POS controls. Eight exact-SKU report descriptions repair unquoted CSV commas: five merchandise listings are restored and three lottery rows receive explicit exclusion. All 9,881 original rows remain, including 12 absent from later reports. The combined reconciliation covers 9,952 records. New report records do not supply UPCs or photos and stay isolated pending identity review.
- The current catalog contains 9,689 variants in 7,998 conservative product groups.
- 107 rows require identity/name review; 156 explicit nonproducts are excluded with private reasons. No source SKU is silently discarded. A guarded review restored 153 named merchandise rows by removing explicit price annotations or withholding an ambiguous size; these SKUs remain isolated and cannot inherit photos.
- 933 exact, source-hash/UPC-guarded spelling, spacing or name clarifications are applied. Every original name remains private and unchanged. All rows were scanned for defined risks. Only 624 complete names have external identity verification; the other corrections have narrower documented scopes. There are 84 separate name-verification records, which do not count as corrections. Seventy uncertain identities were resolved using manufacturer, exact-UPC and inspected-package evidence. The final eight spelling flags received explicit review: seven scoped repairs and the intentional manufacturer spelling “Blue Razberry,” which remains unchanged.
- October report evidence distinguishes glass and PET containers for two exact Dimitri Gin SKUs without changing their original volumes or UPCs. Clipped identifiers in the narrow reports are not guessed into catalog joins.
- Ambiguous sizes display a confirmation label. Differing flavors, strength, age, packaging and unknown identities are not guessed into one product.

## Images

Only exact SKU/UPC, expression, size and packaging matches with a completed professional presentation review may enter the public manifest. Licensed photographs retain their license records. At the owner’s explicit request, direct internet product references use a separate approval basis without asserting copyright-holder permission or a license. Each selected variant has its own image; changing size cannot inherit a sibling photo.

Consumer snapshots were removed after the owner clarified that images must be professional. Clean studio packshots on plain backgrounds are required. No generated replacements or generic brand photos are used. 495 variants currently have approved professional images; 9,194 still lack one. `data-summary.json` tracks these counts. Missing images are deliberately absent. Private research queues cover all 9,952 reconciled source rows and distinguish candidates, actual visual reviews, rejected images and approved assignments. Some licensed originals have limited resolution; none were synthetically enlarged or altered.

Professional image sourcing is ongoing. Direct manufacturer and retailer research continues for items without an exact professional photo. Unreviewed bulk barcode matches are not treated as approvals.

## Implementation and checks

- Warm, responsive retail layout with the supplied logo, seven main categories and all 29 original departments.
- Product cards show no photo-source lines. Required attribution and licenses remain on a separate Image credits page linked once in the footer and regenerated with the catalog.
- Search covers every retained SKU/UPC, product name and size. Exact SKU matches appear first, followed by exact UPC matches, while partial results remain available; pagination limits rendering to 36 cards without limiting the catalog.
- No prices or stock quantities are emitted by the public data builder. Current legacy price-bearing files and unverified old photographs are removed. Older Git history is unchanged.
- Loading, retry, empty results, keyboard navigation, selected sizes and filter URL state are implemented.
- 70 Python tests and eleven frontend tests pass, including original-source conservation, safe grouping, price removal, guarded restoration and image validation. The new identity checks reject absent, whitespace, descriptive and padded UPCs while accepting valid EAN-8 identifiers. Browser checks cover every category/department, size-specific photographs, narrow mobile layouts, pagination and error recovery.
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

PR #10 added 36 manufacturer-record name checks, three Crush shopping-category fixes and the exact Sweet Baby Ray's Hickory & Brown Sugar Barbecue Sauce 18 oz photograph and identity resolution. Pages run 40630916330 succeeded, including all 146 image files and 37 product cases.

PR #11 removed source lines from product cards and preserved all required photo credits on a separate footer-linked page. Pages run 40632590107 succeeded; live cards, all 146 credit records and responsive layouts were verified. Catalog and image bytes were unchanged.

The current consolidated catalog adds 74 complete manufacturer-record name checks: 73 corrections (six replace earlier narrow corrections) and one unchanged-name verification. All 181 source identities matching the examined official PepsiCo sitemap are accounted for; unsupported brand/flavor/line conflicts remain held. This bounded publisher review does not verify all other products.

Fifty-three exact source-guarded category corrections move 25 beverages into Drinks & mixers and 28 foods into Snacks & groceries. Original departments remain accessible, including MISC. Category-only checks do not claim complete name, size or photograph verification.

All 9,952 original/report source records remain unchanged. All 9,689 public variants retain their original SKU/UPC identifiers. Fifteen new exact photographs are assigned; all 146 existing image objects and files remain unchanged. Three guarded size reviews replace misleading fractional displays with “Size needs confirmation” for SKUs 1377, 2016 and 2537. Exact manufacturer evidence separates a Limon Cheetos expression and consolidates matching Doritos Nacho Cheese and Munchies Cheese Fix size variants; unverified neighboring expressions remain separate. The direct-photo review also adds 14 guarded name normalizations; ten increase full-name verification coverage and four update already verified identities. All five listed 1800 Coconut sizes have separate exact photographs. Full name and photo coverage remain unfinished. Detailed source reviews and dated deployment proofs stay outside the public repository.

After authenticated publication, run `python3 scripts/verify_deployment.py https://aadip0204.github.io/quickstop-inventory/` against the exact tested checkout and repeat browser checks before declaring the release live.

The current photo batch adds 73 exact SKU, size and packaging matches from directly reviewed retailer packshots. These are tied to their individual variants; source text stays off product cards. All prior source records, names, categories, sizes and existing image assignments are unchanged. Full photo and name verification remain unfinished.

The latest batch adds 49 exact product photographs. Twenty guarded name corrections restore the apostrophe in Jack Daniel's; one of these also completes the truncated word “lemonade.” Search accepts the brand with or without an apostrophe. These scoped spelling changes do not claim full product-name verification.

The latest cereal and snack batch adds seven individually checked product photographs and five guarded name corrections. Two previously unspecified Pringles can sizes were resolved against exact UPC and manufacturer evidence. Two other conflicting sizes remain marked for confirmation; no photo is assigned to either.

A further 28 Gatorade bottle photos are matched to individual UPC, flavor and size using manufacturer records and inspected packaging. Multipack images and conflicting identities remain withheld.

The latest snack and drink batch adds 21 exact, individually inspected package images and seven source-guarded name corrections. Source UPCs, sizes and original records are retained.

Thirteen previously unspecified snack sizes are resolved using exact UPC manufacturer records and inspected product packaging, with a separate image assigned to each variant. Original private source values remain unchanged.

The latest batch adds 20 exact snack photos, resolves 13 previously unspecified package sizes, and flags three conflicting sizes for confirmation. The original inventory records remain unchanged.

The latest batch adds seven matching snack images, resolves six previously unspecified sizes, corrects a product name, and flags four conflicting sizes for confirmation. Source records are retained.

Nine exact soda photos are matched to their UPC and individual bottle size. A conflicting Sprite source size and pack description is marked for confirmation.

Six matching candy package photos were added, four previously unspecified sizes were resolved, and two product names were clarified using exact UPC evidence.

Nine Brisk one-liter variants were verified against exact manufacturer UPC records and inspected packshots. Their unchanged product expressions remain grouped with their previously matching size variants.

Four additional Brisk lemon, pineapple passionfruit, and strawberry melon bottle variants were checked against exact manufacturer UPC records and inspected packshots.

The 15.5 oz Tostitos mild chunky salsa jar was verified against its exact UPC and a price-free retail product image.

Cheetos Puffs and Frito-Lay salted in-shell peanuts names were checked against exact manufacturer UPC records. The Cheetos size group remains intact.

Four 7UP and A&W bottles were matched to UPC-specific retailer records and visually inspected for flavor and package size.

Three additional 20 oz 7UP and A&W bottles were verified against exact UPC retailer records and inspected package photos.

The tropical 7UP 12 oz single can and 12-pack have separate exact-UPC package images.

Two one-liter bottles and a 16 oz 7UP can were matched to exact UPC product records and inspected packshots.

Four Cheetos bag variants were checked against exact manufacturer UPC records and inspected packshots.

Two Cheetos Flamin’ Hot names were corrected from manufacturer records, and the exact 3.25 oz Crunchy bag photo was added.

Sixteen additional snack packages were matched to exact manufacturer UPC, flavor, weight and inspected price-free packshots.

Twelve photographed snack names were reconciled against exact manufacturer UPC records; preexisting grouped variants remain separately traceable by SKU.

Three additional Chester’s and Smartfood snack bags were matched to exact manufacturer UPCs and inspected price-free package images.

Two Chester’s Flamin’ Hot Fries sizes and Smartfood White Cheddar Popcorn received exact-UPC name corrections.

One conflicting Doritos source title and Smartfood UPC remains visible as an isolated, explicitly unconfirmed listing; no product photo is assigned pending resolution.

Thirteen additional drink bottles and cans were matched to exact manufacturer UPC, flavor, size and inspected price-free packshots.

Four Dole lemonade bottle names were standardized with exact UPC and package evidence while preserving size groups.

Two Gatorade bottles and a Mountain Dew 12-pack received exact-UPC, individually inspected photos.

Two additional 7UP and A&W Cream Soda 12-packs received exact-UPC, individually inspected package photos.

One Gatorade Flow Kiwi Strawberry 28 oz bottle received a verified flavor correction and matching package photo. The product evidence includes a retired manufacturer label, so it does not establish current availability.

Four Doritos Crunch Mix and Crunch Nuts names were clarified using exact UPC evidence; the manufacturer package images display prices and were not added.

Three UPC-matched Canada Dry, Sunkist and Vernors 12-pack photographs were added after individual package inspection.

Three individually inspected Squirt, Sunkist Grape and Sunkist Strawberry 12-pack photographs were matched to their UPCs.

One Sunkist Orange 20 oz bottle received an exact-UPC name clarification and an individually inspected photo.

One Tahitian Treat Fruit Punch 20 oz bottle received an exact-UPC and individually inspected photo.

Three Canada Dry Ginger Ale and Tonic Water bottle photos were matched to their UPC and package sizes.

The 2 L Tahitian Treat Fruit Punch entry received an exact-UPC name correction and inspected package photo; its 20 oz variant retains its own image.

Four Dr Pepper and Sunkist 2-liter bottles received individually inspected UPC-matched photos.

Diet Rite Pure Zero Cola, Sunkist Strawberry and Sun Drop Citrus 2-liter bottles received inspected UPC-matched photos.

A Canada Dry Fruit Splash Cherry Ginger Ale 2 L bottle received an exact-UPC name correction and inspected package photo.

Schweppes Club Soda and Tonic Water 1-liter bottles received individually inspected UPC-matched photos.

A Schweppes Ginger Ale 2-liter bottle received an inspected UPC-matched photo.

The 1800 Silver 750 mL artist-series entry was clarified by exact UPC; its specific bottle artwork remains unconfirmed and no photo was assigned.

Pepsi and Diet Pepsi 12-packs received UPC-matched, individually inspected package photos.

Three 99 Brand 750 mL bottle photos were matched by UPC and inspected; Blackberries spelling was corrected.

99 Watermelons and 99 Peaches 750 mL bottles received UPC-matched, inspected photos.

Amarula Cream Liqueur and Alizé Red Passion 750 mL bottles received UPC-matched photos; the Amarula name was clarified.
