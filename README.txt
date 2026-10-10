WHOLESALE PRODUCT RESEARCH DASHBOARD

What it does
------------
This is a local dashboard for reviewing offers in Telegram channels and groups
your account can access. It lets you choose chats and a date range, then records
product names, available supplier details, Telegram reactions, replies, forwards,
message views, and product photos. The results are saved on this computer in
the data folder.

The Dashboard summarizes the selected scan's unique products, captured Telegram
views and engagement, returned Meta active ads, and highest evidence-based test
priorities. The metric cards and candidate chart open the relevant product
results when clicked. The score is a research aid, not a sales forecast. Use
the sidebar to switch between Overview, Research tools, Reports, and Settings;
each link explains what its window contains, and the sidebar can be collapsed
with its chevron. The card at the bottom of the sidebar shows whether your
Telegram account is signed in and whether Meta ad lookup is available. Research tools
has separate Telegram product scan and Image product search tabs. The
searchable, filterable, sortable product table is under Research tools >
Telegram product scan. Long post text never stretches a row: descriptions and
other long cells are clamped to a fixed number of lines with the full text on
hover, and long supplier, ad, and specification lists scroll inside their own
cell. Both result tables (product scan and image search) behave this way, so
rows stay a consistent height and one long Arabic post cannot push the rest of
the table off screen. Click a product name to open its dedicated detail page,
including its image, captured description, supplier comparison, competitor
listings, Telegram history, and available Meta ads.

Reports includes saved scans with actions to open a report, rename it, run a new
scan using the same date/channel settings, or delete that scan and its related
research. The saved-scan table can be searched and filtered by status.
Re-running starts from the beginning; it does not continue from a
message-level scan checkpoint. Running Telegram scans and image indexing can be
cancelled; the app stops at the next message boundary and saves its progress.
A cancelled image index can resume from its saved chat/message checkpoint.
Meta lookup batches can be cancelled and resumed from Reports.

The test-priority estimate starts with Telegram post frequency, distinct
supplier mentions, views, offer-detail completeness, and recency. When the
Meta Ad Library API is configured and returns eligible data, active ad count
and ad age are also used as research signals. This is not a prediction or
probability of sales.

The app saves the Telegram image when a post includes one, separates an offer
title from its remaining post description, and extracts details it can
recognize (for example price, currency, MOQ, size/dimensions, weight, material,
color, brand, model number, and pack quantity). Missing facts stay blank rather
than being guessed. Each offer links back to its Telegram post where Telegram
provides a usable link.

Open a product profile from its result row to compare supplier offers, see
Telegram engagement totals, and review a history chart. The profile page is
built as one summary block of grouped metric tiles followed by separate
sections: Telegram evidence, Meta Ad Library, and Retail comparison tiles at
the top, then Popularity history, Supplier comparison, Competitor retail
listings, Active Meta ads, and Engagement alerts as individual cards. Each tile
shows what was actually measured and says so plainly when a number is not
available ("Identify first", "Lookup failed", "Not reported by Meta") instead
of showing a zero. The chart groups
unique Telegram posts by their original post date; it is not a history of
changing engagement counts, ad duration, or sales. Posts with at least 100
Telegram reactions that are no more than 24 hours old when scanned appear in
the in-app Alerts list. The alert is detected only when a scan reads the post.
Use the Image search tab to choose one or more Telegram channels/groups and
upload a photo. Image search is independent from the product scan, uses its own
filterable match table, and stops searching an individual channel as soon as
it finds a sufficiently similar product image. The first search builds a local
image index for image posts from the last 12 months in selected channels; when
the uploaded image is recognized, that channel's search stops early. Channels
without a match are indexed through the end of the time window. Indexing runs
in the background and reports progress. Matching tolerates common
resizing and recompression. It does not search the public internet. Removing a
match from the table only hides it from the current results; Telegram/indexed
data is not deleted. Export CSV downloads the current product results.

When configured, the app automatically searches the Meta Ad Library for active
ads matching each identified product name and aggregates returned ads across
products. Review-candidate posts (titles the recognizer could not verify) are
marked "Identify first" and are deliberately skipped, and they no longer need
your attention to get a name. Products are named automatically from their
supplier photos: after every scan finishes, and again whenever results are
opened, the app runs a local image model over every unnamed offer that has a
photo and records the best-matching product name, so the score, the Meta Ad
Library lookup and competitor price checks run on its own. Opening an unnamed
product page names that product too, in about two seconds, so a name is never
missing while you are looking at it. The results table shows which products
were named this way, and the tools page reports how many are still waiting.

Every automatic name carries how it was reached: the product page shows the
match percentage, a badge saying the name came from the photo, and the
alternative names the model considered as one-click options. Use "Correct the
name" to type the real name (and "Update name" to save it) whenever the photo
was a gift set, several items at once, or otherwise unclear; renaming re-runs
the score, the Ad Library lookup and the competitor price checks. The photo
model runs on this computer only, nothing is uploaded. Offers with no stored
photo cannot be named this way and stay marked for review until you name
them by hand with "Identify product". The model can still be wrong on
multi-item or packed photos (a gift set can read as a single product), so the
automatic name is a starting point that you can correct, not a verified fact.
The scan table's Active Meta ads column says
whether a lookup is pending, failed, found ads, or returned no ads. Click that
status or a product name to inspect its product detail page and returned ad
records, advertiser, creative, platform, archive link, and campaign start date.
Advertiser counts are pages returned with ads, not verified sellers. The Ad
Library does not provide verified units sold, and its results do not prove
sales or product success. The app does not ask you to fill in ad counts or
Google Trends forms.

Telegram and Meta each have their own dialog in the top bar: "Connect Telegram"
and "Meta Ad Library". In the Meta dialog, "Test connection" makes one
small active-ad query and verifies that the Ad Library is reachable,
while "Diagnose" classifies a failure in plain language (missing package,
blocked by Meta, network problem, or other) and shows the error detail. No
access token is needed; the lookup uses the meta-ads-collector package, which
talks to the Ad Library's own internal endpoint. The UI confirms each action
with a result popup that is styled green for success and red or amber for
failures. A successful connection test does not guarantee that a product will
have matching ads.

When a product has returned ads, opening its profile automatically follows the
address each ad publishes and reads the price metadata from that advertiser
page, so competitor retail prices appear without pasting URLs. Sites that block
automatic inspection simply record the reason they could not be read; a public
product URL can still be submitted by hand.

Use the dashboard's "Connect Telegram" dialog to enter your Telegram API ID
and API hash. Telegram sign-in is separate: each person signs in to their own
Telegram account with their phone number and Telegram's code. The API ID/hash
identify the Telegram app integration, not the Telegram account. The Meta Ad
Library dialog has no token field — the lookup uses the meta-ads-collector
package, which talks to the Ad Library's own internal endpoint and needs no
developer app, access token, or app review.

The API encrypts these settings with Windows DPAPI for the current Windows
user and saves the encrypted file under that user's AppData folder. The actual
secret values are never sent back by the settings API. Telegram login codes
and two-step passwords are not stored by the settings form. Keep the Windows
account and Telegram session file private; malware or another person using
your Windows account can still access an app you are signed into.

The app listens only on this computer. The meta-ads-collector package runs on
this computer and is a third-party tool that may break if Meta changes its
internal endpoint. When it breaks, first check for a newer meta-ads-collector
release before changing any app code.

The meta-ads-collector scraper does not require an approved Ad Library API
token, but it does violate Meta's Terms of Service. The sanctioned route is the
official API with an approved token. Spend, impressions, and reach are only
published for political and social-issue ads, so those columns are empty for
ordinary commercial ads by design — not a bug. Public archive records do not
consistently include likes, comments, shares, or verified sales. Do not share
any access token in chat. Your Telegram API ID and hash are read only from the
dashboard dialogs where you type them; they are encrypted for your Windows user
and are never taken from Windows environment variables.

To compare competitor listing prices, paste a public product-page URL in a
product profile and choose "Fetch competitor price".
The app reads public page metadata only; some websites block this or do not
publish a machine-readable price. A fetched competitor price is separate from
the Telegram supplier price. Compare only when currency, quantity/unit,
shipping, and tax basis match.

Start the dashboard
-------------------
1. Open PowerShell in the project folder.
2. Make sure the dependencies are installed. Pillow is used for image
   matching and PyTorch is required by the product recognizer; without it a
   scan stops with "NameError: name 'torch' is not defined":

   & "D:\PYTHON INSTALLATION\python.exe" -m pip install Pillow
   & "D:\PYTHON INSTALLATION\python.exe" -m pip install --index-url https://download.pytorch.org/whl/cpu torch
   & "D:\PYTHON INSTALLATION\python.exe" -m pip install transformers huggingface_hub
   & "D:\PYTHON INSTALLATION\python.exe" -m pip install meta-ads-collector

   meta-ads-collector powers the Meta Ad Library lookup; without it the app
   runs normally but reports "Meta Ad Library not available".

   Identifying a product from its photo also needs the image model, downloaded
   once into the data folder (about 605 MB):

   & "D:\PYTHON INSTALLATION\python.exe" -c "from huggingface_hub import snapshot_download; snapshot_download('openai/clip-vit-base-patch32', local_dir='data/models/clip-vit-base-patch32')"

   Without it, everything else still works; only "Identify from photo" reports
   that photo identification is not installed.

3. Run:

   & "D:\PYTHON INSTALLATION\python.exe" "D:\TELEGRAM_AI\app.py"

4. The dashboard should open in your browser at http://127.0.0.1:8765.
5. Choose a date range and tick only the Telegram channels/groups you want in
   the "Choose the chats and dates to read" card. No chats are selected
   automatically. Choose a research market, then press "Scan selected chats"
   and read the confirmation before proceeding.
6. On first use, wait while the product recognizer downloads its model (about
   265 MB). The dashboard shows download progress; message progress starts
   after the model is ready. Once the download is complete, future scans reuse
   the saved local model.
7. Watch the current chat, messages checked, and results found in the progress
   message. Arabic-dominant posts are not sent to the English-oriented product
   recognizer. Posts with a photo or offer details can still appear as review
   candidates when a reliable product name cannot be identified.
8. Press "Stop scan" to stop after the current message; the app keeps and
   displays results collected so far.
9. Review the separate product title and description, image, supplier-listed
   price, and Telegram post link. Expand the details for MOQ, size, weight,
   materials, colors, and the complete post where available.
10. Click a product name to review its supplier and competitor comparisons,
   Telegram history, and any Meta Ad Library records returned automatically.
   The Meta overview shows totals aggregated across products. Offers that the
   text could not name are named automatically from their supplier photo after
   the scan and when you open them, so most products already have a score, an
   Ad Library lookup and competitor price checks. The product page shows the
   match percentage and the other names the photo reader considered: use
   "Correct the name" to fix a name the photo got wrong, then "Update name".
   The first photo read downloads a one-time image model into the data folder;
   later reads reuse it. Competitor prices
   are read automatically from the advertiser pages your returned ads link to;
   paste a public product-page URL only when you want to compare a retailer the
   ads do not point at.

Product identification and the Ad Library now work from the product's name,
brand, category and every extracted specification (material, colour,
size, weight, MOQ and so on) — not just the supplier post title. That is why
the returned ads describe the product you named instead of a similar-named
but different one. The product page shows a "Specifications (N)" button that
opens a table of every captured specification, and a photo carousel (»/«
arrows and swipe) so you can page through the product photos and any
reference photos before the lookup runs. The same spec table appears on the
web-research screen, where the photo and product recogniser also recordnbrand, category and material/colour/size/weight/MOQ details.
11. Use "Sign in with Telegram" or "Connect Meta" in the top bar (the Settings
   page has the same two buttons) to open the "Connect Telegram" and
   "Meta Ad Library" dialogs. The Telegram dialog saves your Telegram API
   ID/hash; press "Connect Telegram" to save the keys and jump to the
   sign-in step. The Meta dialog has no token field: press "Test connection"
   to query the Ad Library straight away. Both
   dialogs show their connection state as a coloured pill. The dashboard
   confirms whether each service is configured without displaying saved
   secrets, and every save, test, diagnosis, and sign-in step reports its result
   in a styled popup. For Telegram API credentials, visit my.telegram.org/apps.
   Do not paste keys into chat or share them.
12. If Telegram asks you to sign in, enter your phone number and then the login
   code in the "Connect Telegram" dialog. If you enabled Telegram two-step
   verification, enter that password in the dialog too; each step reports its
   result in a popup. Never send either to anyone.
13. To stop the app, return to PowerShell and press Ctrl+C.

The dashboard listens only on this computer. Keep the data folder, Telegram
session file, login code, and API credentials private.

Required keys
--------------
You always supply your own keys. Open "Sign in with Telegram" in the top bar to
enter the Telegram API ID and API hash from my.telegram.org/apps and to sign in;
open "Meta Ad Library" to test the Meta Ad Library connection. No token is
needed — the lookup uses the meta-ads-collector package, which talks to the
Ad Library's own internal endpoint. Install it with `pip install meta-ads-collector`.

Values are encrypted with Windows DPAPI for your user account. Leave a field
blank to keep the stored value. PRODUCT_NER_MODEL is optional; the default is
Atheer174/Products_NER2. TELEGRAM_SESSION is optional; the default session is
stored in your Windows user folder, outside this project.

If Telegram credentials have ever been shared publicly or in a chat, replace
them using Telegram's API development tools and enter the new values in the
Telegram dialog before signing in.

What it cannot tell you yet
---------------------------
The app cannot verify supplier reliability, shipping costs, product quality,
customer conversion, marketplace sales, customs/tax costs, or profit margins.
It removes Google Trends research and has no TikTok ad integration. Meta
coverage depends on API approval and Meta's market and ad eligibility rules;
ad duration is derived from the archive start date, and ad interactions are not
consistently supplied. Retail websites can block price fetching, and prices
may omit options, shipping, or tax. The text extractor may miss or misread
product details. A review-candidate title is unverified. Confirm product
specifications and every price with the supplier before buying. Product names
read from a supplier photo come from a general-purpose image model, not from the
post text; they are saved automatically and can be wrong, especially for packed
or multi-item photos, so check the badge and correct the name when it is. Telegram image
matching indexes accessible chats for the last 12 months on the first search;
it is not internet-wide reverse-image search. Push notifications and Excel
export are not available. Product consolidation uses normalized matching
names; confirm that grouped offers are actually the same product.
