# Handoff: Meta Ads Library migration (API → scraper)

Written for whoever picks this project up next. Read this before touching
anything under "Meta" in `app.py` or `dashboard.html`.

---

## 1. What changed and why

The app used to read the **official Meta Ad Library Graph API**
(`https://graph.facebook.com/<version>/ads_archive`). That required each user to
obtain an *approved* Ad Library access token — an app review process most people
never get through. In practice the feature was unusable for a normal user.

It now reads the Ad Library through **`meta-ads-collector`**, a PyPI package that
talks to the same internal endpoint the Ad Library website itself uses. **No
token, no developer app, no review.**

The swap was deliberately kept to one seam so the rest of the app did not have
to care.

### The seam

```
fetch_meta_ads(product_name, max_results=None) -> list[dict]
```

That function signature is **unchanged**. Everything upstream
(`run_meta_ads_sync`, `save_meta_ads_lookup`, the HTTP endpoints, the dashboard
tables) still calls it and still gets a list of row dicts back. Only the
function body changed.

---

## 2. Files touched

| File | Change |
| --- | --- |
| `app.py` | Replaced the Graph API implementation of `fetch_meta_ads`; added collector helpers; widened the `meta_ads` schema; removed the token gates |
| `test_app.py` | Replaced 4 tests that exercised the removed Graph API path with 7 scraper tests |
| `dashboard.html` | Dark mode + UI modernisation only (unrelated to this migration) |
| `HANDOFF.md` | This document |

---

## 3. New functions in `app.py` (all near line ~2445)

| Function | Purpose |
| --- | --- |
| `meta_collector()` | Returns a **shared, lazily built** `MetaAdsCollector`. Building it is slow (it impersonates a browser TLS fingerprint), so one instance is reused for every product. |
| `close_meta_collector()` | Releases that session. Never call it between products. |
| `meta_lookup_available()` | `True` when the package is installed. **This replaces the old `configured_setting("META_AD_LIBRARY_ACCESS_TOKEN")` gate** — that gate is what used to silently skip the whole Meta pass. |
| `meta_ad_to_row(ad)` | Converts one collector `Ad` object into the flat dict this app stores. |
| `meta_range_text(value)` | Formats a spend/impression range as `"125000-150000"`. |
| `meta_time_text(value)` | ISO-formats a datetime. |
| `meta_query_for_product(product)` | Builds a targeted Ad Library search string from the identified product's name, brand, category and extracted specifications, instead of the raw (often wrong) supplier post title. This is what makes the Ad Library return ads for the *real* product rather than a similarly-named one. |
| `identify_product(..., specs=None)` | Records the identified name and stores the extracted `specs` (brand, category, material, color, size, weight, MOQ) with the identification so the product profile can render them in a table and the Meta search can target them. |

`meta-ads-collector` is imported in a `try/except ImportError` at the top of
`app.py` so the app and the test suite still run without it. If it is missing,
`MetaAdsCollector` is `None`, `meta_lookup_available()` is `False`, and
`META_ADS_COLLECTOR_HINT` tells the user how to install it.

---

## 4. Database changes

`meta_ads` gained 13 columns. Both the `CREATE TABLE` (fresh databases) and an
idempotent `ALTER TABLE` loop in `initialize_database()` (existing databases)
were updated — the loop skips columns that already exist, so an existing
`research.sqlite3` upgrades in place with no data loss.

New columns: `link_url`, `cta_text`, `image_url`, `video_url`, `thumbnail_url`,
`languages`, `display_format`, `ad_status`, `is_active`, `currency`, `spend`,
`impressions`, `reach`.

> **Watch the placeholder count.** The `INSERT INTO meta_ads` statement in
> `save_meta_ads_lookup()` lists 25 columns and 25 `?` placeholders in matching
> order. If you add a column, add it to *all four* places: the `CREATE TABLE`,
> the `ALTER TABLE` loop, the column list, and the value tuple.

---

## 5. ⚠️ Known limitations — read these

**1. Spend / impressions / reach are empty for normal commercial ads.**
This is **not a bug and not fixable**. Meta only publishes these figures for
* political and social-issue ads. I verified this directly: a political search
returned `USD 125,000 - 150,000`, while every commercial ad returned nothing.
The columns exist and will populate if you ever search political ads. Do not
"fix" this by loosening a query — there is no data to fetch.

**2. This violates Meta's Terms of Service.** The sanctioned route is the
official API with an approved token. The user made a deliberate, informed choice
to accept this tradeoff. Do not silently switch back.

**3. It is inherently fragile.** Meta can change or block the internal endpoint
at any time, and the package is maintained by a third party. `curl_cffi` is used
to impersonate a browser TLS fingerprint; if Meta tightens bot detection this
will start failing. When it breaks, first check for a newer
`meta-ads-collector` release before changing any app code.

---

## 10. What is left to finish

All items from the original handoff and the product-identity hardening are
completed on 2026-10-09. No open work remains.

* The product identifier is now the identified name + brand + extracted
  specifications, so the Ad Library is searched for the correct product
  instead of the raw supplier post title. See "## 12. Product identification
  hardening" below.

## 11. Summary of changes

- **Added `classify_meta_error()` function** in `app.py` (~line 4431). It
  classifies Meta errors into categories: ``"ok"``, ``"missing"``,
  ``"permission"``, ``"blocked"``, ``"expired"``, ``"network"``, ``"other"``.
  This function was used but not defined — it is now defined and exported.
- **Simplified the Meta dialog in `dashboard.html`** (~line 2237). The token
  input field and "Connect Meta" / "Save token" buttons were removed. The
  dialog now shows only "Test connection" and "Diagnose" buttons, matching the
  backend behavior which no longer requires a token.
- **Updated JavaScript handlers** in `dashboard.html` to remove token-saving
  logic (`saveMetaToken()` is no longer called, `#save-meta` handler removed,
  `testMetaConnection()` simplified).
- **Updated credential status display** to say "Meta Ad Library available"/
  "not available" instead of "token configured"/"not configured".
- **Updated README.txt** to reflect the new scraper-based approach. Removed all
  references to tokens, app review, and Graph API. Added explanation of the
  meta-ads-collector package and its ToS implications.

## 11.1 Added `classify_meta_error()` function (`app.py` ~line 4431)

### 11.1 Added `classify_meta_error()` function (`app.py` ~line 4431)

This function was *used* in two places but never *defined*:

* `run_meta_ads_sync()` (line ~4407) calls `classify_meta_error(str(error))[0]`
  to decide whether to stop the batch early.
* the `/api/meta-ads/diagnose` endpoint (line ~5545) calls
  `category, message = classify_meta_error(str(error))` to build its JSON reply.

The new function returns `(category, message)` and recognises these categories:

| Category | Meaning |
| --- | --- |
| ``"ok"`` | Lookup succeeded. |
| ``"missing"`` | `meta-ads-collector` is not installed. |
| ``"permission"`` | Meta refused access (legacy Graph API path — no longer reachable). |
| ``"blocked"`` | Meta blocked the request (bot detection, rate limit). |
| ``"expired"`` | Session/token expired (legacy path). |
| ``"network"`` | Connection/timeout problem. |
| ``"other"`` | Anything else. |

Branches that describe token problems that can no longer occur are kept
because they are still wired into the batch runner and diagnose endpoint.

### 11.2 Simplified the Meta dialog (`dashboard.html` ~line 2237)

The token input field and the "Connect Meta" / "Save token" buttons were
removed. The dialog now shows only:

* **Test connection** — hits `/api/meta-ads/test` and reports the result.
* **Diagnose** — hits `/api/meta-ads/diagnose` and explains any failure.
* **Close**.

The dialog heading changed from "Connect Meta Ad Library" to "Meta Ad
Library", and the intro paragraph now explains that no token is needed.

### 11.3 Updated JavaScript handlers (`dashboard.html`)

* Removed `saveMetaToken()` calls — the function still exists but is no longer
  invoked. (It can be deleted once the token field is fully removed from the
  settings API, if ever.)
* Removed the `#save-meta` button click handler entirely.
* Simplified `testMetaConnection()` to skip the token-saving step.
* Updated `META_DIAGNOSIS_TITLES` to drop token-specific wording.
* Updated `refreshCredentialStatus()` to say "Meta Ad Library available" /
  "not available" instead of "token configured".
* Updated the `#meta-connect` aria-label and the top-bar account label.
* Updated inline help text in the product profile, the Meta status panel, and
  the README.

### 11.4 Updated README.txt

All references to the Graph API, access tokens, app review, `META_GRAPH_API_VERSION`,
and the token dialog were replaced with the scraper-based story. The required-keys
section now says the Meta dialog has no token field.

### 12. Product identification hardening (2026-10-09)

The core bug was that the Ad Library was searched on the raw Telegram post
title, which is frequently a wrong or partial product name (a gift-set photo
reads as one item, a price fragment survives the extractor, a store label
prefixes the title). Those wrong queries returned ads for the wrong product.
The fix makes identification and the Ad Library query driven by the
identified product, not the post text.

* **Spec-capable identification.** `identify_product()` now accepts
  `specs` (list of `{label, value, unit}`) captured from the supplier offers
  and the photo/web reader, and stores them in `product_identifications`.
  `identify_product_from_web()` passes the web reader's extracted specs.
  Photo-identification keeps its confidence-gated save path.
* **Targeted Meta query.** `meta_query_for_product(product)` builds the Ad
  Library search string from name + brand + category + every extracted
  specification, then truncates to 200 characters before calling the
  collector. `fetch_meta_ads(..., product=None)` uses this targeted query
  whenever a product record is available, falling back to the raw name only
  if nothing else is known.
* **Spec table on the product page.** The product detail page now renders a
  "Specifications (N)" button that opens a modal table (`Property / Value`)
  listing every captured specification for that identified product.
* **Image carousel.** The product hero shows the supplier photo and, where
  there are identical reposted photos, a thumbnail strip. A `“«” /
  `”` carousel nav (plus swipe left/right) lets the user page between the
  product's photos and between the candidate reference images from the
n  photo catalogue, so the user can visually verify the name before the
  Meta lookup runs.

### 11.5 Enriched Meta ad fields now render on the product page (`dashboard.html`)

The product-profile Meta ad list now shows, for each ad:

* **Visit advertiser page** link from `link_url` (the real destination URL).
* **Call to action** from `cta_text`.
* **Ad image** thumbnail from `image_url` (or `thumbnail_url` when no image).
* **Watch ad video** link from `video_url`.
* **Spend** and **Impressions** when Meta published them (political/social-issue ads only).
* **Status** line showing `ad_status` and whether the ad is active.

A new `.ad-media` CSS class styles the image figures.

---

## 8. Useful commands

```bash
# run the suite (79 tests, ~5s — it must NOT hit the network)
.venv/Scripts/python.exe -m unittest test_app

# lint
.venv/Scripts/python.exe -m pyflakes app.py

# prove the collector works, without the app
.venv/Scripts/python.exe -c "from meta_ads_collector import MetaAdsCollector; \
c=MetaAdsCollector(); print([a.id for a in c.search(query='phone case', country='US', max_results=3)]); c.close()"
```

### Testing rule

**Never let a test call the real Ad Library.** The 7 tests in
`TradeDetailsTests` patch `app.meta_collector` with a fake collector
(`_collector_returning`) and build fake ads with `_fake_ad()`. An earlier
version of the suite did not do this and hung for 10 minutes hitting the network
— if the suite ever takes more than a few seconds, that is what has happened.

---

# Phase 2: UI, identification evidence and the supplier bridge

## Supplier directory (`supplier_bridge.py`)

The imported supplier guide lives in `telegram_research.db` (`imported_suppliers`,
1,238 rows) and is now readable from the product side without touching the
scanner:

* `match_suppliers(name, category, limit=5)` scores directory rows on product
  wording, row precision and the category sheet, and returns the shared terms in
  `matched_on` so every lead is explainable.
* `coverage(products)` matches a whole scan and answers the business question
  "for how many scanned products do we already know a wholesaler?".
* `SYNONYM_GROUPS` is the small English/French/Arabic bridge; extend it when a
  new sheet vocabulary appears.

Endpoints:

* `GET /api/suppliers/matches?product=…&category=…` – one product.
* `GET /api/suppliers/matches?key=…` – the same, resolved from the current scan.
* `GET /api/suppliers/matches` – coverage for the current scan.
* `GET /api/product/evidence?key=…` – identification evidence + directory matches.

**Backward compatibility:** nothing in `app.py`'s Telegram path reads the
directory. When the channel/API mechanism is replaced, only the *product* side of
the bridge needs to survive.

## Identification evidence

`identification_evidence(product)` combines four independent layers (photo/name
source, offer-post wording, web + competitor pages, supplier directory) into a
weighted agreement with a verdict and a next step. It is rendered in the
**Name verification** block on the profile. A layer with no data is skipped, not
scored as zero, so a product is never punished for a source nobody asked for.

## Meta ads exactness

`score_meta_ad` now rejects an ad that names a *neighbouring* model
(`Item1002` for `Item1001`) even when brand and keywords match, using
`_near_model_tokens` on the creative wording only (a model number in a tracking
URL never creates a false conflict). Conflicting ads stay in the *similar* section
with the reason spelled out.

## Image search

* Upload by drop, click, clipboard paste or URL (`image_url` is downloaded
  server-side, so CORS cannot block it).
* `start_date` / `end_date` are validated by `parse_date_window` and applied in
  the same SQL query as the channel filter.
* Results render as cards (photo, product, channel, date, price, hash distance)
  with a link into the product profile.

## Sidebar / design system

The sidebar is rebuilt with inline SVG icons, grouped navigation and a collapse
toggle that remembers the choice in `localStorage` (`productscout.sidebar.collapsed`).
`<style id="ui-modern">` is the single authoritative stylesheet for the new
components; older rules stay for the untouched pages.

Checks: `python .build/check_dom_ids.py` (every id the JS looks up exists) and
`python .build/verify_http.py` (serves the real handler and asserts the new
markup and endpoints).

## Product page layout (phase 2)

`page-product` is a product-detail layout: a sticky **profile top bar** (back,
name, key, refresh), then a **hero** — gallery on the left, identity on the
right with a dark **price hero** (lowest supplier price / sell price / margin),
a metric grid (confidence, trend, posts, views, exact and similar ads,
suppliers, cost) and the action row. Below sit the exact/similar ad carousels,
then the evidence blocks ordered by how often they answer a real question:
name verification, directory suppliers, images & offers, history, specs,
identification, research, similar sellers, Meta library, competitors, alerts.
Every block is backed by a real endpoint; none of the ids moved, so
`python .build/check_dom_ids.py` still passes.

## Product naming and supplier-channel scanning (phase 3)

Scans used to read each supplier post with a "first short line" rule, so the
channel's own banner ("متراطيش لافار"), a price ("85الف") or a CTA
("Disponible") became the product name. That name is what the Meta Ad Library
was searched for, which is why scans looked like they never identified anything
and the ads never matched.

`offer_parser.py` replaces that rule with a layout reader: emoji and decoration
are stripped, lines that only carry price, stock, delivery, contact details or
checkout wording are dropped, lines the channel repeats in most posts (its
signature) are dropped by frequency, and the name comes from an explicit marker
(`منتج` / `produit` / `article`), then a descriptive Latin-script line, then an
Arabic line. A mixed line contributes its Latin phrase
("للي وصانا على Littmann Classic 3" -> "Littmann Classic 3"), trailing price
tails are cut ("Airpods max 205 alf" -> "Airpods max"), and brand lines
(`marque:` / `ماركة:`) are stored as the brand. A post that names no product
stays an unnamed offer instead of being mislabelled.

`run_scan` reads a channel's posts for the window once, computes the signature
set, then names each post; the downloaded NER model is now only the fallback for
Latin-script posts the parser cannot name.

Meta lookup: `meta_query_for_product` now sends a short brand-first keyword
query (the Ad Library is a keyword search, so the old name+specs+category query
returned nothing) and retries with three words when it comes back empty.
Precision still comes from `score_meta_ad`, which splits the returned ads into
exact and similar.

Supplier pages: `import_supplier_channels()` reads the supplier guide's own
Telegram links (`telegram_research.db` -> `imported_suppliers`) into `sources`
and `source_categories` (115 channels across 19 categories on this install), and
replaces the four placeholder "Sample data" rows. `/api/scan` now accepts a
category without any `chat_ids`, and `run_scan` opens each source by handle with
`client.get_entity`, so the supplier's public channel is read even when the
account never joined it. One unreadable channel is logged and skipped instead of
ending the scan.

Identification only runs for offers that are still unnamed. Before the web and
photo readers, `image_match_candidate` names a photo-only offer from an
identical supplier photo that is already stored with a name (perceptual hash,
6 bits), and `_looks_like_concrete_product` rejects page titles and placeholders
("Unnamed Up offers", "Read Customer Service Reviews of ...") so they can never
be saved.

Measured on the stored history: 58.9% of stored text posts now get a real
product name (previously effectively none: the names were the channel banner,
prices or "Disponible"). A live scan of the `vetements pour homme` supplier
category read 324 posts from three channels that are not in the account and
named 58 products; the "parka" product then returned 36 active Algeria Meta ads
(22 exact, 14 similar) with advertisers, creatives, landing pages and start
dates.

### Closing the photo-only and Arabic-ad gaps

Three further layers were added on top of the phase-3 naming work, because
photo-only posts and Arabic product names were the two remaining holes.

**Album captions (exact, no model).** Telegram puts one caption on an album and
leaves the other photos empty. `run_scan` now collects a channel's posts for the
window once, reads the caption of every album (`grouped_id`) and gives that name
to every photo in the same album, so an offer posted as five photos is one
product instead of five unnamed ones.

**Visual product retrieval (CLIP).** When a photo offer has no caption and no
identical twin, `visual_name_candidate` compares its photo with the CLIP
embeddings of the photos that are already named (the cached `photo_embeddings`
library, 244 named photos on this install) and takes the nearest name only when
it clears the calibrated same-product gate (0.92) *and* beats the runner-up by
0.02. Verified against real stored photos: a resized, re-compressed copy of a
named supplier photo matched at 0.93 and named the offer. The candidate library
is only read from the cache — a single identification never sits through
encoding hundreds of pictures, and the background indexer keeps filling it.

**Arabic ad matching.** `_tokenize` read tokens with `[a-z0-9]+`, which dropped
Arabic entirely: an Arabic product name had no tokens, so it could never be an
exact ad match. Tokens now keep Arabic and both sides fold the optional
spellings onto one form (diacritics removed, أ/إ/آ → ا, ة → ه, ى → ي, the
definite article `ال` stripped), and Arabic filler words (من شركة رقم موديل …)
are dropped from the search query. Query widening goes further and searches the
name, then three words, then two, then the single strongest word, merging the
ads of every step — the scorer, not the query, decides exact vs similar.

Measured: `قميص الاعتكاف مخطط علبة` went from **0 ads** to 3 ads with **1 exact**
(a Qasr Alshiaka ad that names قميص الاعتكاف in Arabic). On the re-scan of the
`vetements pour homme` supplier category the same products are named again and
the album posts group into one product instead of several unnamed offers.

Still impossible without a vision model: a channel whose every post is a photo
with no caption, no album sibling and no named twin anywhere (the sport channel
is such a channel). Naming those needs an image-reading model; the local TrOCR
weights on disk cannot be loaded under transformers 5 (`XLMRobertaTokenizer` is
routed through the tiktoken converter and fails on `sentencepiece.bpe.model`),
so that path is not wired in.
