import sqlite3
import re
import asyncio
import tempfile
import time
import unittest
import zipfile
import os
import json
from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
from PIL import Image, ImageDraw
from app import (
    calculate_test_priority,
    clean_product_candidate,
    derive_offer_title,
    parse_listing_html,
    product_attributes_from_text,
    separate_description,
    is_arabic_dominant,
    normalize_product,
    normalize_supplier_name,
    telegram_engagement,
    telegram_message_url,
    parse_trade_details,
    image_dhash_bytes,
    validate_public_url,
)


class CancellableIterationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_interrupts_a_pending_telegram_message_fetch(self):
        class WaitingIterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.Future()

        cancel_event = asyncio.Event()

        async def consume():
            async for _ in app.cancellable_telegram_messages(
                WaitingIterator(), cancel_event
            ):
                self.fail("No message should be produced.")

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        cancel_event.set()
        await asyncio.wait_for(task, timeout=0.5)


@unittest.skipUnless(os.name == "nt", "Windows DPAPI is required")
class ProtectedCredentialTests(unittest.TestCase):
    def test_credentials_are_encrypted_and_only_returned_by_local_decryption(self):
        credentials = {
            "TELEGRAM_API_ID": "123456",
            "TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef",
        }
        protected = app._dpapi_transform(
            json.dumps(credentials).encode("utf-8"),
            protect=True,
        )

        self.assertEqual(
            json.loads(app._dpapi_transform(protected, protect=False)),
            credentials,
        )

    def test_credential_status_never_returns_secret_values(self):
        original_path = app.CREDENTIALS_PATH
        original_cache = app._credentials_cache
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                app.CREDENTIALS_PATH = Path(temporary_directory) / "credentials.dpapi"
                app._credentials_cache = {}
                app.save_local_credentials(
                    {
                        "TELEGRAM_API_ID": "123456",
                        "TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef",
                    }
                )
                status = app.credential_status()

                self.assertEqual(
                    status,
                    {"telegram_configured": True, "meta_configured": True},
                )
        finally:
            app.CREDENTIALS_PATH = original_path
            app._credentials_cache = original_cache

    def test_meta_lookup_is_available_when_collector_is_imported(self):
        # meta_lookup_available() checks whether the collector package is importable,
        # not whether a token is stored. On a machine with the package installed it
        # should return True.
        self.assertTrue(app.meta_lookup_available())

    def test_credentials_are_never_read_from_environment_variables(self):
        original_path = app.CREDENTIALS_PATH
        original_cache = app._credentials_cache
        environment = {
            "TELEGRAM_API_ID": "999999",
            "TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef",
        }
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                app.CREDENTIALS_PATH = Path(temporary_directory) / "credentials.dpapi"
                app._credentials_cache = {}
                app.save_local_credentials({})
                with patch.dict(os.environ, environment):
                    self.assertEqual(app.configured_setting("TELEGRAM_API_ID"), "")
                    self.assertEqual(
                        app.credential_status(),
                        {"telegram_configured": False, "meta_configured": True},
                    )
                app.update_local_credentials(
                    {
                        "telegram_api_id": environment["TELEGRAM_API_ID"],
                        "telegram_api_hash": environment["TELEGRAM_API_HASH"],
                    }
                )
                self.assertEqual(
                    app.credential_status()["telegram_configured"],
                    True,
                )
        finally:
            app.CREDENTIALS_PATH = original_path
            app._credentials_cache = original_cache


class TradeDetailsTests(unittest.TestCase):
    def test_extracts_price_minimum_order_and_dimensions(self):
        details = parse_trade_details(
            "Steel bottle USD 4.25 per piece MOQ: 12 pcs Size: 20 x 8 cm"
        )

        self.assertEqual(details["price"], 4.25)
        self.assertEqual(details["currency"], "USD")
        self.assertEqual(details["price_unit"], "piece")
        self.assertEqual(details["moq"], "12 pcs")
        self.assertEqual(details["size"], "20 x 8 cm")

    def test_extracts_symbol_currency(self):
        details = parse_trade_details("Sample product £19.99")

        self.assertEqual(details["price"], 19.99)
        self.assertEqual(details["currency"], "GBP")

    def test_extracts_algerian_dinar_and_french_offer_details(self):
        details = parse_trade_details(
            "Prix de gros: 1 200 DA, min commande: 10 pcs, Taille: 20 x 8 cm"
        )

        self.assertEqual(details["price"], 1200.0)
        self.assertEqual(details["currency"], "DZD")
        self.assertEqual(details["moq"], "10 pcs")
        self.assertEqual(details["size"], "20 x 8 cm")

    def test_parses_comma_grouped_thousands_in_supplier_price(self):
        details = parse_trade_details("Wholesale price 1,200 DA")

        self.assertEqual(details["price"], 1200.0)
        self.assertEqual(details["currency"], "DZD")

    def test_extracts_arabic_currency(self):
        details = parse_trade_details("السعر 1200 دج")

        self.assertEqual(details["price"], 1200.0)
        self.assertEqual(details["currency"], "DZD")

    def test_missing_offer_details_are_not_invented(self):
        self.assertEqual(
            parse_trade_details("A product with no price or order details"),
            {
                "price": None,
                "currency": None,
                "price_unit": None,
                "moq": None,
                "size": None,
            },
        )

    def test_product_normalization_groups_case_and_punctuation(self):
        self.assertEqual(normalize_product("Rose-Gold Bottle!"), "rose gold bottle")
        self.assertEqual(normalize_product("قارورة - حرارية"), "قارورة حرارية")

    def test_normalizes_supplier_display_variants(self):
        self.assertEqual(
            normalize_supplier_name("@Official Rose Gold Shop"),
            normalize_supplier_name("rose-gold"),
        )

    def test_arabic_is_not_sent_to_english_product_name_extractor(self):
        self.assertTrue(is_arabic_dominant("السعر 1200 دج"))
        self.assertFalse(is_arabic_dominant("Wholesale bottle USD 12"))

    def test_rejects_unreliable_model_fragments(self):
        self.assertIsNone(clean_product_candidate("##ul", 0.99))
        self.assertIsNone(clean_product_candidate("https : / / sa", 0.99))
        self.assertIsNone(clean_product_candidate("Wholesal", 0.89))
        self.assertEqual(clean_product_candidate("Steel Bottle", 0.91), "Steel Bottle")

    def test_extracts_product_weight_and_attributes(self):
        attributes = product_attributes_from_text(
            "Weight: 450 g\nMaterial: Stainless steel\nColor: Silver\nPack of 12 pcs"
        )

        self.assertEqual(attributes["weight"], ["450 g"])
        self.assertEqual(attributes["material"], ["Stainless steel"])
        self.assertEqual(attributes["color"], ["Silver"])
        self.assertEqual(attributes["package_quantity"], ["12 pcs"])

    def test_separates_title_from_description_without_dropping_post(self):
        title, inferred = derive_offer_title("LED Desk Lamp\nMaterial: aluminum\nWeight: 1 kg")

        self.assertEqual(title, "LED Desk Lamp")
        self.assertTrue(inferred)
        self.assertEqual(
            separate_description(title, "LED Desk Lamp\nMaterial: aluminum\nWeight: 1 kg"),
            "Material: aluminum\nWeight: 1 kg",
        )

    def test_builds_public_and_private_telegram_message_links(self):
        self.assertEqual(
            telegram_message_url(-1001234567890, "sample_channel", 42),
            "https://t.me/sample_channel/42",
        )
        self.assertEqual(
            telegram_message_url(-1001234567890, None, 42),
            "https://t.me/c/1234567890/42",
        )

    def test_reads_public_competitor_product_metadata(self):
        listing = parse_listing_html(
            '<meta property="og:title" content="Steel Bottle">'
            '<meta property="product:price:amount" content="19.99">'
            '<meta property="product:price:currency" content="USD">'
        )

        self.assertEqual(listing["title"], "Steel Bottle")
        self.assertEqual(listing["price"], 19.99)
        self.assertEqual(listing["currency"], "USD")

    def test_parses_competitor_prices_with_decimal_comma(self):
        listing = parse_listing_html(
            '<meta itemprop="price" content="19,99">'
            '<meta itemprop="priceCurrency" content="EUR">'
        )

        self.assertEqual(listing["price"], 19.99)
        self.assertEqual(listing["currency"], "EUR")

    def test_calculates_research_informed_test_priority(self):
        baseline, _, coverage = calculate_test_priority(60, {"trend_direction": "unknown"})
        informed, breakdown, coverage = calculate_test_priority(
            60,
            {
                "trend_direction": "rising",
                "active_ads": 2,
                "oldest_ad_days": 30,
                "social_views": 10000,
                "social_interactions": 800,
            },
        )

        self.assertEqual(baseline, 60)
        self.assertGreater(informed, 60)
        self.assertEqual(coverage, 3)
        self.assertIn("meta_ads", breakdown)
        self.assertNotIn("google_trends", breakdown)

    def test_blocks_non_public_competitor_urls(self):
        with self.assertRaises(ValueError):
            validate_public_url("http://127.0.0.1/private")
        with self.assertRaises(ValueError):
            validate_public_url("file:///C:/secret")

    def test_reads_telegram_reactions_replies_and_forwards(self):
        class Reaction:
            def __init__(self, count):
                self.count = count

        class Replies:
            replies = 7

        class Message:
            reactions = type("Reactions", (), {"results": [Reaction(80), Reaction(25)]})()
            replies = Replies()
            forwards = 9

        self.assertEqual(telegram_engagement(Message()), (105, 7, 9))

    def test_image_upload_hash_requires_a_real_supported_image(self):
        from app import image_upload_hash, image_upload_hash_and_chat_ids

        boundary = "test-boundary"
        image_file = BytesIO()
        image = Image.new("RGB", (120, 90), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 75, 70), fill="black")
        draw.ellipse((80, 20, 110, 50), fill="gray")
        image.save(image_file, format="PNG")
        image_bytes = image_file.getvalue()
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image"; filename="sample.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()

        self.assertEqual(
            image_upload_hash(f"multipart/form-data; boundary={boundary}", body),
            image_dhash_bytes(image_bytes),
        )

        scoped_body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="chat_ids"\r\n\r\n'
            "-1001234567890,-1001234567891\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image"; filename="sample.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()
        self.assertEqual(
            image_upload_hash_and_chat_ids(
                f"multipart/form-data; boundary={boundary}", scoped_body
            ),
            (
                image_dhash_bytes(image_bytes),
                [-1001234567890, -1001234567891],
            ),
        )

    def test_perceptual_image_hash_matches_resized_recompressed_copy(self):
        image = Image.new("RGB", (240, 160), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 15, 145, 130), fill="#18233a")
        draw.ellipse((155, 30, 220, 95), fill="#e28c24")
        original = BytesIO()
        image.save(original, format="PNG")
        modified = BytesIO()
        image.resize((117, 78)).save(modified, format="JPEG", quality=74)

        original_hash = int(image_dhash_bytes(original.getvalue()), 16)
        modified_hash = int(image_dhash_bytes(modified.getvalue()), 16)

        self.assertLessEqual((original_hash ^ modified_hash).bit_count(), 12)

    def test_image_index_status_recovers_paused_checkpoint_after_restart(self):
        temporary = tempfile.TemporaryDirectory()
        original_db = app.DB_PATH
        original_state = dict(app._image_index_state)
        original_chats = app._chat_entities
        app.DB_PATH = Path(temporary.name) / "research.sqlite3"
        app._image_index_state.update(status="idle")
        app._chat_entities = {123: object(), 456: object()}
        cutoff = (app.date.today() - app.timedelta(days=365)).isoformat()
        try:
            with closing(sqlite3.connect(app.DB_PATH)) as connection:
                connection.execute(
                    "CREATE TABLE telegram_image_index (image_hash TEXT)"
                )
                connection.execute(
                    """
                    CREATE TABLE telegram_image_index_state (
                        chat_id INTEGER, cutoff_date TEXT, status TEXT,
                        match_hash TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO telegram_image_index_state
                        (chat_id, cutoff_date, status)
                    VALUES (?, ?, ?)
                    """,
                    (123, cutoff, "paused"),
                )
                connection.execute(
                    """
                    INSERT INTO telegram_image_index_state
                        (chat_id, cutoff_date, status, match_hash)
                    VALUES (?, ?, ?, ?)
                    """,
                    (456, cutoff, "match_found", "0123456789abcdef"),
                )
                connection.commit()
            status = app.image_index_status()
            search_status = app.image_index_status("0123456789abcdef")
        finally:
            app.DB_PATH = original_db
            app._image_index_state.update(original_state)
            app._chat_entities = original_chats
            temporary.cleanup()

        self.assertEqual(status["status"], "paused")
        self.assertEqual(status["ready_chat_ids"], [])
        self.assertFalse(status["ready"])
        self.assertEqual(search_status["ready_chat_ids"], [456])

    def test_reverse_image_search_ranks_all_indexed_channel_images(self):
        first = Image.new("RGB", (100, 80), "white")
        ImageDraw.Draw(first).rectangle((8, 8, 70, 65), fill="black")
        first_bytes = BytesIO()
        first.save(first_bytes, format="PNG")
        matching = Image.new("RGB", (61, 49), "white")
        ImageDraw.Draw(matching).rectangle((5, 5, 43, 40), fill="black")
        matching_bytes = BytesIO()
        matching.save(matching_bytes, format="JPEG", quality=82)
        other = Image.new("RGB", (100, 80), "white")
        ImageDraw.Draw(other).ellipse((15, 10, 85, 70), fill="black")
        other_bytes = BytesIO()
        other.save(other_bytes, format="PNG")
        query_hash = image_dhash_bytes(first_bytes.getvalue())

        temporary = tempfile.TemporaryDirectory()
        original_db = app.DB_PATH
        app.DB_PATH = Path(temporary.name) / "research.sqlite3"
        try:
            with closing(sqlite3.connect(app.DB_PATH)) as connection:
                connection.execute(
                    """
                    CREATE TABLE telegram_image_index (
                        chat_id INTEGER, message_id INTEGER, chat_title TEXT,
                        product TEXT, supplier TEXT, price REAL, currency TEXT,
                        message_date TEXT, telegram_url TEXT, image_name TEXT,
                        image_hash TEXT, product_key TEXT DEFAULT ''
                    )
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO telegram_image_index (
                        chat_id, message_id, chat_title, product, supplier,
                        price, currency, message_date, telegram_url, image_name, image_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            -1001234567890, 101, "Channel A", "Steel Bottle",
                            "Supplier One", 12, "USD", "2026-10-02",
                            "https://t.me/sample_channel/101", "same-product.jpg",
                            image_dhash_bytes(matching_bytes.getvalue()),
                        ),
                        (
                            -1001234567890, 102, "Channel A", "Steel Bottle",
                            "Supplier Two", 11, "USD", "2026-10-03",
                            "https://t.me/sample_channel/102", "same-product-2.jpg",
                            image_dhash_bytes(matching_bytes.getvalue()),
                        ),
                        (
                            -1001234567891, 202, "Channel B", "Desk Lamp",
                            "Supplier Two", 20, "USD", "2026-10-01",
                            "https://t.me/another_channel/202", "other-product.jpg",
                            image_dhash_bytes(other_bytes.getvalue()),
                        ),
                    ],
                )
                connection.commit()

            matches = app.find_similar_telegram_images(query_hash)
            selected_matches = app.find_similar_telegram_images(
                query_hash, chat_ids=[-1001234567890]
            )
        finally:
            app.DB_PATH = original_db
            temporary.cleanup()

        self.assertEqual(matches[0]["product"], "Steel Bottle")
        self.assertEqual(selected_matches[0]["chat_id"], -1001234567890)
        self.assertEqual([match["product"] for match in selected_matches], ["Steel Bottle"])
        self.assertEqual(len(selected_matches), 1)
        self.assertEqual(selected_matches[0]["supplier"], "Supplier Two")
        self.assertLessEqual(matches[0]["difference_bits"], 12)
        self.assertNotIn("Desk Lamp", [match["product"] for match in matches])

    @staticmethod
    def _fake_ad(ad_id="ad-123", **overrides):
        """Build an object shaped like a meta-ads-collector Ad."""
        creative = SimpleNamespace(
            body="Ad copy",
            title="Product",
            caption="shop.example.com",
            link_url="https://shop.example.com/p/9",
            cta_text="Shop Now",
            image_url="https://cdn.example.com/i.jpg",
            video_url=None,
            thumbnail_url="https://cdn.example.com/t.jpg",
        )
        ad = SimpleNamespace(
            id=ad_id,
            page=SimpleNamespace(name="Example Store"),
            creatives=[creative],
            is_active=True,
            ad_status="ACTIVE",
            delivery_start_time=datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc),
            delivery_stop_time=None,
            snapshot_url=f"https://www.facebook.com/ads/archive/render_ad/?id={ad_id}",
            ad_snapshot_url=None,
            impressions=None,
            spend=None,
            reach=None,
            currency="",
            publisher_platforms=["FACEBOOK", "INSTAGRAM"],
            languages=["English"],
            display_format="IMAGE",
        )
        for name, value in overrides.items():
            setattr(ad, name, value)
        return ad

    @staticmethod
    def _collector_returning(ads, calls=None, error=None):
        """Stand in for MetaAdsCollector so no test touches the network."""
        class Collector:
            def search(self, **kwargs):
                if calls is not None:
                    calls.append(kwargs)
                if error is not None:
                    raise error
                yield from ads
        return Collector()

    def test_meta_ad_lookup_normalizes_collector_results(self):
        ads = [self._fake_ad()]
        with patch.object(app, "meta_collector", return_value=self._collector_returning(ads)):
            rows = app.fetch_meta_ads("Example Product")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["ad_id"], "ad-123")
        self.assertEqual(row["page_name"], "Example Store")
        self.assertEqual(row["platforms"], ["FACEBOOK", "INSTAGRAM"])
        self.assertEqual(row["creative_text"], "Ad copy")
        self.assertEqual(row["creative_title"], "Product")

    def test_meta_lookup_keeps_the_fields_the_collector_adds(self):
        """The scraper returns a real destination URL and media the API could not."""
        ads = [self._fake_ad()]
        with patch.object(app, "meta_collector", return_value=self._collector_returning(ads)):
            row = app.fetch_meta_ads("Example Product")[0]

        self.assertEqual(row["link_url"], "https://shop.example.com/p/9")
        self.assertEqual(row["cta_text"], "Shop Now")
        self.assertEqual(row["image_url"], "https://cdn.example.com/i.jpg")
        self.assertEqual(row["thumbnail_url"], "https://cdn.example.com/t.jpg")
        self.assertEqual(row["display_format"], "IMAGE")
        self.assertEqual(row["is_active"], 1)
        self.assertEqual(row["languages"], "English")

    def test_meta_lookup_real_destination_url_beats_the_display_address(self):
        """Competitor pages should be read from the ad's true landing URL."""
        ads = [self._fake_ad()]
        with patch.object(app, "meta_collector", return_value=self._collector_returning(ads)):
            urls = app.competitor_urls_from_ads(app.fetch_meta_ads("Example Product"))
        self.assertEqual(urls, ["https://shop.example.com/p/9"])

    def test_competitor_urls_fall_back_to_the_display_address(self):
        """An ad with no landing URL still yields the advertiser's page."""
        rows = [{"link_url": "", "link_caption": "shop.example.com"}]
        self.assertEqual(
            app.competitor_urls_from_ads(rows), ["https://shop.example.com/"]
        )

    def test_meta_range_text_formats_spend_and_blanks_commercial_ads(self):
        """Meta only publishes spend for political ads, so blanks are expected."""
        self.assertEqual(app.meta_range_text(None), "")
        self.assertEqual(
            app.meta_range_text(SimpleNamespace(lower_bound=None, upper_bound=None)), ""
        )
        self.assertEqual(
            app.meta_range_text(SimpleNamespace(lower_bound=125000, upper_bound=150000)),
            "125000-150000",
        )
        self.assertEqual(
            app.meta_range_text(SimpleNamespace(lower_bound=5000, upper_bound=5000)),
            "5000",
        )

    def test_meta_lookup_limits_results_and_sends_the_selected_country(self):
        calls = []
        ads = [self._fake_ad("ad-1"), self._fake_ad("ad-2"), self._fake_ad("ad-3")]
        with patch.object(
            app, "meta_collector", return_value=self._collector_returning(ads, calls)
        ):
            # The Ad Library country is now chosen on its own, not derived from
            # the research market, so the lookup must send the selected code.
            with patch.dict(app._meta_ad_settings, {"country": "DZ"}):
                rows = app.fetch_meta_ads("product", max_results=1)

        self.assertEqual([row["ad_id"] for row in rows], ["ad-1"])
        self.assertEqual(calls[0]["country"], "DZ")
        self.assertEqual(calls[0]["status"], "ACTIVE")
        self.assertEqual(calls[0]["max_results"], 1)

    def test_meta_lookup_returns_nothing_for_a_blank_query(self):
        collector = self._collector_returning([self._fake_ad()])
        with patch.object(app, "meta_collector", return_value=collector) as factory:
            self.assertEqual(app.fetch_meta_ads("   "), [])
        factory.assert_not_called()

    def test_meta_lookup_failures_surface_as_a_plain_runtime_error(self):
        error = app.MetaAdsError("Meta Ads rate limited this session")
        with patch.object(
            app, "meta_collector", return_value=self._collector_returning([], error=error)
        ):
            with self.assertRaises(RuntimeError) as raised:
                app.fetch_meta_ads("Example Product")

        message = str(raised.exception)
        self.assertIn("Meta Ads rate limited this session", message)


class ResultStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB_PATH
        self.original_scan_id = app._current_scan_id
        self.original_market = app._current_market
        self.original_latest_scan = app._latest_scan_info
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        app._current_scan_id = None
        app.initialize_database()
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """
                INSERT INTO scans (
                    scan_id, start_date, end_date, market, completed_at, status,
                    extractor_version
                ) VALUES ('test-scan', '2026-10-01', '2026-10-03', 'Algeria',
                          '2026-10-03T12:00:00+00:00', 'completed', 3)
                """
            )
            connection.execute(
                """
                INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text, price,
                    currency, price_unit, moq, size, views, forwards, image_name,
                    attributes_json, review_candidate, description_text, telegram_url
                ) VALUES (
                    'test-scan', -1001234567890, 42, 'Steel Bottle', 'steel bottle',
                    'Supplier One', 'Wholesale Chat', '2026-10-02T12:00:00+00:00',
                    'Steel Bottle\\nWeight: 450 g', 1200, 'DZD', 'piece', '10 pcs',
                    '20 x 8 cm', 500, 3, NULL, '{"weight":["450 g"]}', 0,
                    'Weight: 450 g', 'https://t.me/sample_channel/42'
                )
                """
            )
            connection.commit()
        app._current_scan_id = "test-scan"

    def tearDown(self):
        app.DB_PATH = self.original_db
        app._current_scan_id = self.original_scan_id
        app._current_market = self.original_market
        app._latest_scan_info = self.original_latest_scan
        self.temp_dir.cleanup()

    def test_result_api_data_keeps_description_specs_and_telegram_link(self):
        product = app.load_results()[0]

        self.assertEqual(product["product"], "Steel Bottle")
        self.assertEqual(product["description"], "Weight: 450 g")
        self.assertEqual(product["attributes"]["weight"], ["450 g"])
        self.assertEqual(
            product["offers"][0]["telegram_url"],
            "https://t.me/sample_channel/42",
        )
        self.assertEqual(product["offers"][0]["price"], 1200)

    def test_new_saved_scans_can_store_reusable_channel_criteria(self):
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(scans)")
            }

        self.assertIn("chat_ids_json", columns)
        self.assertIn("name", columns)

    def test_product_history_aggregates_unique_posts_by_post_date(self):
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """
                INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text, views,
                    forwards, reactions, comments
                ) VALUES (
                    'test-scan', -1001234567890, 43, 'Steel Bottle', 'steel bottle',
                    'Supplier Two', 'Wholesale Chat', '2026-10-03T12:00:00+00:00',
                    'Steel Bottle', 700, 4, 12, 2
                )
                """
            )
            connection.commit()

        history = app.load_product_history("steel bottle")

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["posts"], 1)
        self.assertEqual(history[1]["date"], "2026-10-03")
        self.assertEqual(history[1]["reactions"], 12)
        self.assertEqual(history[1]["forwards"], 4)

    def test_csv_export_includes_engagement_and_telegram_link(self):
        product = app.load_results()[0]
        product["total_reactions"] = 8
        product["total_comments"] = 2
        product["total_forwards"] = 3
        product["active_meta_ads"] = [{"ad_id": "ad-1", "page_name": "Example Store"}]
        product["meta_ads_lookup"] = {"status": "ok"}

        exported = app.export_products_csv([product])

        self.assertIn("Telegram reactions", exported)
        self.assertIn("Active Meta ads returned", exported)
        self.assertIn("Verified sales data", exported)
        self.assertIn("Example Store", exported)
        self.assertIn("Not provided by Meta Ad Library", exported)
        self.assertIn("Steel Bottle", exported)
        self.assertIn("https://t.me/sample_channel/42", exported)

    def test_meta_dashboard_total_deduplicates_ads_found_for_multiple_products(self):
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            for product_key in ("steel bottle", "steel flask"):
                connection.execute(
                    """
                    INSERT INTO meta_ad_lookups (
                        scan_id, product_key, status, ad_count, fetched_at
                    ) VALUES ('test-scan', ?, 'ok', 1, '2026-10-03T12:00:00+00:00')
                    """,
                    (product_key,),
                )
                connection.execute(
                    """
                    INSERT INTO meta_ads (
                        scan_id, product_key, ad_id, fetched_at
                    ) VALUES ('test-scan', ?, 'same-ad', '2026-10-03T12:00:00+00:00')
                    """,
                    (product_key,),
                )
            connection.commit()

        summary = app.meta_ads_summary("test-scan")

        self.assertEqual(summary["products_checked"], 2)
        self.assertEqual(summary["active_ads_found"], 1)


class MetaDiagnosisTests(unittest.TestCase):
    def test_competitor_urls_fall_back_to_the_display_address(self):
        ads = [
            {"link_caption": "shop.example.com/item/1"},
            {"link_caption": "https://other.example.org/product?id=7"},
            {"link_caption": "this is not an address"},
            {"link_caption": ""},
            {"link_caption": "shop.example.com/item/1"},
        ]

        self.assertEqual(
            app.competitor_urls_from_ads(ads),
            [
                "https://shop.example.com/item/1",
                "https://other.example.org/product?id=7",
            ],
        )
        self.assertEqual(
            app.competitor_urls_from_ads([{"link_caption": "x y"}]), []
        )

    def test_competitor_urls_prefer_the_real_landing_url(self):
        self.assertEqual(
            app.competitor_urls_from_ads([{"link_url": "https://shop.example.com/p/9", "link_caption": "shop.example.com"}]),
            ["https://shop.example.com/p/9"],
        )

    def test_competitor_urls_ignore_blank_rows(self):
        self.assertEqual(
            app.competitor_urls_from_ads([{}, {"link_url": ""}, {"link_caption": "  "}]), []
        )

    def test_fetch_meta_ads_returns_an_empty_list_for_a_blank_query(self):
        self.assertEqual(app.fetch_meta_ads("   "), [])

    def test_fetch_meta_ads_works_where_a_stub_collector_returns_no_results(self):
        fake = SimpleNamespace(search=lambda **kw: [])
        with patch("app.meta_collector", return_value=fake) as factory:
            rows = app.fetch_meta_ads("Example Product", max_results=3)

        self.assertEqual(len(rows), 0)
        factory.assert_called_once()


class ProductIdentificationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB_PATH
        self.original_scan_id = app._current_scan_id
        self.original_latest_scan = app._latest_scan_info
        self.original_image_dir = app.IMAGE_DIR
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        app.IMAGE_DIR = Path(self.temp_dir.name) / "images"
        app.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        app._current_scan_id = None
        app.initialize_database()
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """
                INSERT INTO scans (
                    scan_id, start_date, end_date, market, completed_at, status,
                    extractor_version
                ) VALUES ('test-scan', '2026-10-01', '2026-10-03', 'Algeria',
                          '2026-10-03T12:00:00+00:00', 'completed', 3)
                """
            )
            connection.execute(
                """
                INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text, review_candidate
                ) VALUES (
                    'test-scan', -1001234567890, 42, 'review offer',
                    'review offer', 'Supplier One', 'Wholesale Chat',
                    '2026-10-02T12:00:00+00:00', 'Steel Bottle USD 12', 1
                )
                """
            )
            connection.commit()
        app._current_scan_id = "test-scan"

    def tearDown(self):
        app.DB_PATH = self.original_db
        app.IMAGE_DIR = self.original_image_dir
        app._current_scan_id = self.original_scan_id
        app._latest_scan_info = self.original_latest_scan
        self.temp_dir.cleanup()

    def test_identifying_a_candidate_product_enables_scoring_and_ad_lookups(self):
        new_key = app.identify_product("test-scan", "review offer", "Steel Bottle")

        self.assertEqual(new_key, "steel bottle")
        product = app.load_results()[0]
        self.assertFalse(product["candidate_only"])
        self.assertEqual(product["product"], "Steel Bottle")
        self.assertIsNotNone(product["score"])

    def test_identify_rejects_unknown_products_and_blank_names(self):
        with self.assertRaises(LookupError):
            app.identify_product("test-scan", "missing product", "Steel Bottle")
        with self.assertRaises(ValueError):
            app.identify_product("test-scan", "review offer", "   ")

    def _store_photo(self, image_name="offer.jpg"):
        photo = Path(app.IMAGE_DIR) / image_name
        photo.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 32), "white").save(photo)
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                "UPDATE messages SET image_name = ? WHERE normalized_product = 'review offer'",
                (image_name,),
            )
            connection.commit()
        return photo

    def test_photo_preview_ranks_names_without_saving_them(self):
        self._store_photo()
        choices = [
            {"label": "steel bottle", "score": 0.91},
            {"label": "water bottle", "score": 0.07},
        ]
        with patch.object(app, "product_image_choices", return_value=choices) as ranked:
            preview = app.identify_product_from_image("test-scan", "review offer", save=False)

        ranked.assert_called_once()
        self.assertFalse(preview["saved"])
        self.assertEqual(preview["key"], "review offer")
        self.assertEqual(preview["name"], "steel bottle")
        self.assertAlmostEqual(preview["score"], 0.91)
        self.assertTrue(app.load_results()[0]["candidate_only"])

    def test_saving_a_photo_match_clears_the_review_flag(self):
        self._store_photo()
        choices = [{"label": "steel bottle", "score": 0.91}]
        with patch.object(app, "product_image_choices", return_value=choices):
            saved = app.identify_product_from_image("test-scan", "review offer")

        self.assertTrue(saved["saved"])
        self.assertEqual(saved["key"], "steel bottle")
        self.assertFalse(app.load_results()[0]["candidate_only"])

    def test_low_confidence_and_missing_photos_are_refused(self):
        with patch.object(
            app, "product_image_choices", return_value=[{"label": "hair dryer", "score": 0.12}]
        ):
            with self.assertRaises(LookupError):
                app.identify_product_from_image("test-scan", "review offer")
        self._store_photo()
        with patch.object(
            app, "product_image_choices", return_value=[{"label": "hair dryer", "score": 0.12}]
        ):
            with self.assertRaises(ValueError) as raised:
                app.identify_product_from_image("test-scan", "review offer")
        self.assertIn("hair dryer", str(raised.exception))
        self.assertTrue(app.load_results()[0]["candidate_only"])

    def _add_second_photo(self, image_name="offer-2.jpg"):
        Image.new("RGB", (48, 48), "black").save(Path(app.IMAGE_DIR) / image_name)
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text,
                    review_candidate, image_name
                ) VALUES ('test-scan', -1001234567890, 43, 'review offer',
                          'review offer', 'Supplier One', 'Wholesale Chat',
                          '2026-10-02T13:00:00+00:00', 'second photo of the offer',
                          1, ?)""",
                (image_name,),
            )
            connection.commit()

    def test_saving_a_name_needs_every_photo_of_the_offer_to_agree(self):
        self._store_photo()
        self._add_second_photo()
        # Both photos must individually clear the confident-save floor so the
        # per-photo winner can then be tested for cross-photo agreement.
        disagreeing = [
            [
                {"label": "hair dryer", "score": 0.95},
                {"label": "hair clipper", "score": 0.05},
            ],
            [
                {"label": "hair clipper", "score": 0.95},
                {"label": "hair dryer", "score": 0.05},
            ],
        ]
        with patch.object(app, "product_image_choices", side_effect=disagreeing):
            with self.assertRaises(ValueError) as raised:
                app.identify_product_from_image("test-scan", "review offer")
        self.assertIn("disagree", str(raised.exception))
        self.assertTrue(app.load_results()[0]["candidate_only"])

        # A preview stays clickable so the right name can still be picked.
        with patch.object(app, "product_image_choices", side_effect=disagreeing):
            preview = app.identify_product_from_image(
                "test-scan", "review offer", save=False
            )
        self.assertFalse(preview["saved"])
        self.assertEqual(
            {choice["label"] for choice in preview["choices"]},
            {"hair dryer", "hair clipper"},
        )

    def test_agreeing_photos_save_the_name_that_holds_across_them(self):
        self._store_photo()
        self._add_second_photo()
        agreeing = [
            [
                {"label": "hair dryer", "score": 0.8},
                {"label": "hair clipper", "score": 0.1},
            ],
            [
                {"label": "hair dryer", "score": 0.7},
                {"label": "hair clipper", "score": 0.2},
            ],
        ]
        with patch.object(app, "product_image_choices", side_effect=agreeing):
            saved = app.identify_product_from_image("test-scan", "review offer")

        self.assertTrue(saved["saved"])
        self.assertEqual(saved["name"], "hair dryer")
        self.assertAlmostEqual(saved["score"], 0.75)
        product = app.load_results()[0]
        self.assertFalse(product["candidate_only"])
        self.assertAlmostEqual(product["identification_confidence"], 0.75)

    def test_a_guess_that_barely_beats_the_runner_up_saves_nothing(self):
        self._store_photo()
        too_close = [
            [
                {"label": "hair dryer", "score": 0.55},
                {"label": "hair clipper", "score": 0.42},
            ]
        ]
        with patch.object(app, "product_image_choices", side_effect=too_close):
            with self.assertRaises(ValueError) as raised:
                app.identify_product_from_image("test-scan", "review offer")
        self.assertIn("too close", str(raised.exception))
        self.assertTrue(app.load_results()[0]["candidate_only"])


class AutomaticPhotoIdentificationTests(unittest.TestCase):
    """Naming products from their photos without being asked, after a scan."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB_PATH
        self.original_image_dir = app.IMAGE_DIR
        self.original_scan_id = app._current_scan_id
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        app.IMAGE_DIR = Path(self.temp_dir.name) / "images"
        app.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        app._current_scan_id = "test-scan"
        app.initialize_database()
        rows = []
        for index in range(3):
            image_name = f"offer-{index}.jpg"
            Image.new("RGB", (16, 16), "white").save(app.IMAGE_DIR / image_name)
            rows.append(
                (
                    "test-scan",
                    -1001234567890,
                    100 + index,
                    f"review offer {index}",
                    f"review offer {index}",
                    f"Supplier {index}",
                    "Wholesale Chat",
                    "2026-10-02T12:00:00+00:00",
                    "some offer text",
                    1,
                    image_name,
                )
            )
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """INSERT INTO scans (
                    scan_id, start_date, end_date, market, completed_at, status,
                    extractor_version
                ) VALUES ('test-scan', '2026-10-01', '2026-10-03', 'Algeria',
                          '2026-10-03T12:00:00+00:00', 'completed', 3)"""
            )
            connection.executemany(
                """INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text,
                    review_candidate, image_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            connection.commit()
        with app._photo_identify_lock:
            app._photo_identify_state.update(running=False, scan_id="test-scan", checked=0, total=0,
                identified=0, unreadable=0, message="",
            )
        # These classes exist to test the photo model. The duplicate-photo and
        # visual-nearest layers run before it in the app and have their own
        # tests, so they are switched off here to keep this class on the reader.
        for layer in (
            patch.object(app, "image_match_candidate", return_value=None),
            patch.object(app, "visual_name_candidate", return_value=None),
        ):
            layer.start()
            self.addCleanup(layer.stop)
        # The web step is disabled so no live search runs inside the suite.
        self.web_patcher = patch.object(
            app,
            "identify_product_from_web",
            side_effect=ValueError("Web research is disabled in the photo tests."),
        )
        self.web_patcher.start()
        self.addCleanup(self.web_patcher.stop)

    def tearDown(self):
        app.DB_PATH = self.original_db
        app.IMAGE_DIR = self.original_image_dir
        app._current_scan_id = self.original_scan_id
        with app._photo_identify_lock:
            app._photo_identify_state.update(running=False, scan_id=None)
        self.temp_dir.cleanup()

    def _choices(self, *labels):
        return [
            {"label": label, "score": score}
            for label, score in labels
        ]

    def test_pending_candidates_are_the_review_offers_that_have_a_photo(self):
        self.assertEqual(
            app.pending_photo_candidates("test-scan"),
            ["review offer 0", "review offer 1", "review offer 2"],
        )
        self.assertEqual(app.pending_photo_candidates(None), [])

    def test_the_sweep_names_every_candidate_without_being_asked(self):
        pending = app.pending_photo_candidates("test-scan")
        with patch.object(
            app, "product_image_choices", return_value=self._choices(("hair clipper", 0.85), ("comb", 0.1))
        ), patch.object(app, "start_meta_ads_sync") as meta_sync:
            app.run_photo_identification("test-scan", pending)

        self.assertEqual(app.pending_photo_candidates("test-scan"), [])
        products = app.load_results()
        self.assertEqual([item["key"] for item in products], ["hair clipper"])
        self.assertEqual(len(products[0]["offers"]), 3)
        self.assertTrue(products[0]["identified_from_photo"])
        self.assertAlmostEqual(products[0]["identification_confidence"], 0.85)
        self.assertEqual(
            products[0]["identification_alternatives"], [{"label": "comb", "score": 0.1}]
        )
        self.assertFalse(products[0]["candidate_only"])
        self.assertIsNotNone(products[0]["score"])
        meta_sync.assert_called_once()

    def test_a_weak_photo_match_is_left_as_a_review_candidate(self):
        """A 0.4 photo guess must not be saved as a confident product name.

        Saving it would clear the review flag and send the score, the Meta Ad
        Library lookup and the competitor checks after the wrong product.
        """
        pending = app.pending_photo_candidates("test-scan")
        with patch.object(
            app, "product_image_choices", return_value=self._choices(("hair clipper", 0.4), ("comb", 0.1))
        ), patch.object(app, "start_meta_ads_sync") as meta_sync:
            app.run_photo_identification("test-scan", pending)

        self.assertEqual(
            app.pending_photo_candidates("test-scan"),
            ["review offer 0", "review offer 1", "review offer 2"],
        )
        self.assertTrue(all(item["candidate_only"] for item in app.load_results()))
        meta_sync.assert_not_called()

    def test_an_unreadable_photo_stays_a_review_offer(self):
        with patch.object(
            app, "product_image_choices", return_value=self._choices(("hair clipper", 0.001))
        ), patch.object(app, "start_meta_ads_sync"):
            app.run_photo_identification("test-scan", ["review offer 0"])

        summary = app.photo_identification_summary("test-scan")
        self.assertEqual(summary["identified"], 0)
        self.assertEqual(summary["unreadable"], 1)
        self.assertEqual(summary["pending"], 3)
        self.assertTrue(app.load_results()[0]["candidate_only"])

    def test_photo_identification_is_skipped_when_the_model_is_missing(self):
        with patch.object(app, "photo_identification_installed", return_value=False):
            self.assertFalse(app.start_photo_identification("test-scan"))
        self.assertEqual(app.pending_photo_candidates("test-scan")[0], "review offer 0")

    def test_naming_one_product_from_its_photo_records_how_it_was_named(self):
        with patch.object(
            app, "product_image_choices", return_value=self._choices(("wrench set", 0.82), ("pliers", 0.2))
        ):
            result = app.auto_identify_product("test-scan", "review offer 1")

        self.assertTrue(result["saved"])
        product = next(
            item for item in app.load_results() if item["key"] == "wrench set"
        )
        self.assertEqual(product["identification_method"], "photo")
        self.assertEqual(product["previous_product_key"], "review offer 1")
        self.assertAlmostEqual(product["identification_confidence"], 0.82)

    def test_a_weak_photo_guess_stays_a_review_offer_instead_of_naming_it(self):
        with patch.object(
            app,
            "product_image_choices",
            return_value=self._choices(("hair dryer", 0.2), ("comb", 0.1)),
        ), patch.object(app, "start_meta_ads_sync"):
            result = app.auto_identify_product("test-scan", "review offer 0")

        self.assertIn("error", result)
        self.assertTrue(app.load_results()[0]["candidate_only"])

    def test_renaming_replaces_the_recorded_identification(self):
        app.identify_product(
            "test-scan", "review offer 0", "Hair Clipper", method="photo",
            confidence=0.4, alternatives=[{"label": "comb", "score": 0.1}],
        )
        app.identify_product("test-scan", "hair clipper", "Trimmer")

        product = app.load_results()[0]
        self.assertEqual(product["key"], "trimmer")
        self.assertEqual(product["identification_method"], "typed")
        self.assertIsNone(product["identification_confidence"])


class ScanFinishNamingTests(unittest.IsolatedAsyncioTestCase):
    """A finished scan names its own photo offers, with no further input."""

    async def test_a_finished_scan_names_its_photo_offers_by_itself(self):
        from datetime import date, datetime, timezone
        from types import SimpleNamespace

        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        originals = (app.DB_PATH, app.IMAGE_DIR, app._current_scan_id, app._chat_entities)
        app.DB_PATH = Path(temp_dir.name) / "research.sqlite3"
        app.IMAGE_DIR = Path(temp_dir.name) / "images"
        app.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        app._current_scan_id = None
        app._chat_entities = {-1001234567890: SimpleNamespace(title="Wholesale Chat")}
        app.initialize_database()

        message = SimpleNamespace(
            id=42,
            date=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
            message="منتج للبيع بالجملة",
            views=120,
            get_sender=self._sender,
            download_media=self._download,
        )

        def restore():
            app.DB_PATH, app.IMAGE_DIR = originals[0], originals[1]
            app._current_scan_id, app._chat_entities = originals[2], originals[3]
            with app._photo_identify_lock:
                app._photo_identify_state.update(running=False, scan_id=None)
            temp_dir.cleanup()

        self.addCleanup(restore)

        async def one_message(iterator, cancel_event):
            yield message

        async def no_entities(text):
            return [], {}

        async def extractor():
            return object()

        async def client():
            return SimpleNamespace(iter_messages=lambda *args, **kwargs: [])

        with patch.object(app, "cancellable_telegram_messages", one_message), patch.object(
            app, "extract_entities", no_entities
        ), patch.object(app, "get_product_extractor", extractor), patch.object(
            app, "require_authorized_client", client
        ), patch.object(app, "has_product_image", return_value=True), patch.object(
            app, "telegram_image_extension", return_value=".jpg"
        ), patch.object(
            app,
            "product_image_choices",
            return_value=[{"label": "wireless earbuds", "score": 0.71}, {"label": "power bank", "score": 0.2}],
        ), patch.object(app, "start_meta_ads_sync"), patch.object(
            app, "start_web_research", return_value=False
        ), patch.object(
            app,
            "identify_product_from_web",
            side_effect=ValueError("Web research is disabled in the tests."),
        ):
            await app.run_scan([-1001234567890], date(2026, 10, 1), date(2026, 10, 3))
            scan_id = app._current_scan_id
            self.assertTrue(scan_id)
            for _ in range(200):
                with app._photo_identify_lock:
                    if not app._photo_identify_state["running"]:
                        break
                await asyncio.sleep(0.05)

        self.assertEqual(app.pending_photo_candidates(scan_id), [])
        products = app.load_results()
        self.assertEqual([item["key"] for item in products], ["wireless earbuds"])
        self.assertTrue(products[0]["identified_from_photo"])
        self.assertAlmostEqual(products[0]["identification_confidence"], 0.71)

    @staticmethod
    async def _sender():
        from types import SimpleNamespace

        return SimpleNamespace(title="Supplier One", username=None, first_name=None)

    @staticmethod
    async def _download(file=None, **kwargs):
        Image.new("RGB", (24, 24), "white").save(file)


    def test_a_refused_app_stops_the_batch_instead_of_retrying_every_product(self):
        temp_dir = tempfile.TemporaryDirectory()
        original_db = app.DB_PATH
        app.DB_PATH = Path(temp_dir.name) / "research.sqlite3"
        app.initialize_database()
        self.addCleanup(lambda: (setattr(app, "DB_PATH", original_db), temp_dir.cleanup()))
        products = [
            {"key": f"product {index}", "product": f"Product {index}", "candidate_only": False}
            for index in range(3)
        ]
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            for product in products:
                connection.execute(
                    "INSERT INTO meta_ad_lookups (scan_id, product_key, status, fetched_at) VALUES (?, ?, 'failed', '')",
                    ("test-scan", product["key"]),
                )
            connection.commit()

        refused = RuntimeError(
            "Meta Ad Library request failed (HTTP 400): Application does not have permission for this action"
        )
        with patch.object(app, "fetch_meta_ads", side_effect=refused) as lookup:
            app.run_meta_ads_sync("test-scan", products)

        # Only the first product is attempted; the rest would be refused too.
        self.assertEqual(lookup.call_count, 1)
        with app._meta_ads_lock:
            message = app._meta_ads_state["message"]
            running = app._meta_ads_state["running"]
        self.assertIn("Meta refused access for this app or token", message)
        self.assertIn("2 product(s) were not searched", message)
        self.assertFalse(running)


class CompetitorAdFollowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB_PATH
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        app.initialize_database()
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """
                INSERT INTO meta_ads (
                    scan_id, product_key, ad_id, page_name, link_caption, fetched_at
                ) VALUES ('test-scan', 'steel bottle', 'ad-1', 'Example Store',
                          'shop.example.com/item/1', '2026-10-03T12:00:00+00:00')
                """
            )
            connection.execute(
                """
                INSERT INTO meta_ad_lookups (
                    scan_id, product_key, status, ad_count, fetched_at
                ) VALUES ('test-scan', 'steel bottle', 'ok', 1,
                          '2026-10-03T12:00:00+00:00')
                """
            )
            connection.commit()

    def tearDown(self):
        app.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    def test_follows_ad_link_to_read_and_store_a_competitor_price(self):
        page = {
            "url": "https://shop.example.com/item/1",
            "title": "Steel Bottle",
            "price": 19.99,
            "currency": "USD",
            "description": "Insulated bottle",
            "image": None,
        }
        with patch.object(app, "fetch_competitor_listing", return_value=page) as fetch:
            results = app.collect_competitor_prices_from_ads("test-scan", "steel bottle")

        self.assertEqual(fetch.call_args.args[0], "https://shop.example.com/item/1")
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[0]["price"], 19.99)
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            stored = connection.execute(
                "SELECT price, currency, status FROM competitor_listings"
            ).fetchall()
        self.assertEqual(stored, [(19.99, "USD", "ok")])

    def test_reports_why_prices_cannot_be_followed(self):
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute("DELETE FROM meta_ads")
            connection.execute(
                "UPDATE meta_ad_lookups SET status = 'failed', error_text = 'Meta said no'"
            )
            connection.commit()

        with self.assertRaisesRegex(LookupError, "Meta said no"):
            app.collect_competitor_prices_from_ads("test-scan", "steel bottle")

        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """
                INSERT INTO meta_ads (
                    scan_id, product_key, ad_id, page_name, link_caption, fetched_at
                ) VALUES ('test-scan', 'steel bottle', 'ad-2', 'Example Store', '',
                          '2026-10-03T12:00:00+00:00')
                """
            )
            connection.commit()

        with self.assertRaisesRegex(ValueError, "no competitor page to follow"):
            app.collect_competitor_prices_from_ads("test-scan", "steel bottle")


class SameProductSellerTests(unittest.TestCase):
    """The product page finds other sellers by the photo, Alibaba-style."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB_PATH
        self.original_image_dir = app.IMAGE_DIR
        self.original_scan_id = app._current_scan_id
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        app.IMAGE_DIR = Path(self.temp_dir.name) / "images"
        app.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        app._current_scan_id = "test-scan"
        app.initialize_database()

        # The product's own photo: a plain white square.
        Image.new("RGB", (64, 64), "white").save(app.IMAGE_DIR / "product.jpg")
        # Another channel reposted the very same file.
        (app.IMAGE_DIR / "twin.jpg").write_bytes(
            (app.IMAGE_DIR / "product.jpg").read_bytes()
        )
        # A different product photo: a left-to-right darkening gradient, whose
        # perceptual hash is nothing like the plain white product photo.
        gradient = Image.new("RGB", (64, 64))
        draw = ImageDraw.Draw(gradient)
        for x in range(64):
            shade = 255 - int(x * 255 / 63)
            draw.line([(x, 0), (x, 63)], fill=(shade, shade, shade))
        gradient.save(app.IMAGE_DIR / "different.jpg")
        # One more photo that only the embedding (not the hash) recognises as
        # the same product.
        checks = Image.new("RGB", (64, 64), "white")
        draw = ImageDraw.Draw(checks)
        for x in range(0, 64, 8):
            draw.rectangle([x, 0, x + 3, 63], fill="black")
        checks.save(app.IMAGE_DIR / "visual.jpg")
        # A category look-alike (same shape, different product) that the visual
        # model scores 0.85 — below the 0.90 gate, so it must never match.
        lookalike = Image.new("RGB", (64, 64), "white")
        draw = ImageDraw.Draw(lookalike)
        draw.ellipse([12, 12, 51, 51], fill="black")
        lookalike.save(app.IMAGE_DIR / "doubtful.jpg")

        rows = [
            ("test-scan", -100, 1, "Review Offer", "review offer",
             "Supplier One", "Main Chat", "2026-10-02T12:00:00+00:00",
             "offer text", 1, "product.jpg"),
            ("test-scan", -101, 2, "Other Offer", "other offer",
             "Supplier Two", "Twin Chat", "2026-10-03T12:00:00+00:00",
             "offer text", 0, "twin.jpg"),
            ("test-scan", -102, 3, "Different Stuff", "different stuff",
             "Supplier Three", "Other Chat", "2026-10-04T12:00:00+00:00",
             "offer text", 0, "different.jpg"),
            ("test-scan", -103, 4, "Unrelated Title", "unrelated title",
             "Supplier Four", "Visual Chat", "2026-10-05T12:00:00+00:00",
             "offer text", 0, "visual.jpg"),
            ("test-scan", -104, 5, "Lookalike Listing", "lookalike listing",
             "Supplier Five", "Similar Chat", "2026-10-06T12:00:00+00:00",
             "offer text", 0, "doubtful.jpg"),
        ]
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """INSERT INTO scans (
                    scan_id, start_date, end_date, market, completed_at, status,
                    extractor_version
                ) VALUES ('test-scan', '2026-10-01', '2026-10-03', 'Algeria',
                          '2026-10-03T12:00:00+00:00', 'completed', 3)"""
            )
            connection.executemany(
                """INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text,
                    review_candidate, image_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            # Cached embeddings for every candidate photo but the product's own,
            # which the call under test computes itself.
            for name, vector in (
                ("twin.jpg", [0.99, 0.01, 0.0, 0.0]),
                ("visual.jpg", [0.92, 0.392, 0.0, 0.0]),
                ("different.jpg", [0.0, 1.0, 0.0, 0.0]),
                ("doubtful.jpg", [0.85, 0.527, 0.0, 0.0]),
            ):
                connection.execute(
                    """INSERT INTO photo_embeddings (
                        image_name, model_version, embedding, created_at
                    ) VALUES (?, ?, ?, '2026-10-06T00:00:00+00:00')""",
                    (name, app.PHOTO_EMBEDDING_VERSION, app.pack_embedding(vector)),
                )
            connection.commit()
        with app._photo_embed_lock:
            app._photo_embed_state.update(
                running=False, checked=0, total=0, message=""
            )

    def tearDown(self):
        app.DB_PATH = self.original_db
        app.IMAGE_DIR = self.original_image_dir
        app._current_scan_id = self.original_scan_id
        with app._photo_embed_lock:
            app._photo_embed_state.update(running=False, checked=0, total=0, message="")
        self.temp_dir.cleanup()

    @staticmethod
    def _product_embedding(paths):
        # "visual.jpg" is a *different photo of the same product*: 0.92 cosine
        # (well above the 0.90 visual gate). "doubtful.jpg" is a category-lookalike
        # at 0.85 — exactly the band that used to leak wrong products in.
        vectors = {
            "product.jpg": [1.0, 0.0, 0.0, 0.0],
            "visual.jpg": [0.92, 0.392, 0.0, 0.0],
            "doubtful.jpg": [0.85, 0.527, 0.0, 0.0],
        }
        return [vectors.get(Path(path).name) for path in paths]

    def test_lookalike_photos_below_the_visual_gate_are_rejected(self):
        """A 0.85 CLIP score with a different title is a different product."""
        with patch.object(
            app, "encode_photo_embeddings", side_effect=self._product_embedding
        ), patch.object(app, "start_photo_embedding", return_value=False):
            result = app.same_product_sellers("test-scan", "review offer")

        matches = {match["chat_id"]: match for match in result["matches"]}
        self.assertNotIn(-104, matches)   # 0.85 lookalike, different title
        self.assertNotIn(-102, matches)   # unrelated photo
        self.assertIn(-101, matches)      # identical repost still matches
        self.assertIn(-103, matches)      # 0.92 true match still matches

    def test_sellers_are_found_by_photo_even_when_their_title_differs(self):
        with patch.object(
            app, "encode_photo_embeddings", side_effect=self._product_embedding
        ), patch.object(app, "start_photo_embedding", return_value=False):
            result = app.same_product_sellers("test-scan", "review offer")

        self.assertEqual(result["status"], "ready")
        matches = {match["chat_id"]: match for match in result["matches"]}
        # The reposted photo is a certain match, the differently-titled post
        # matches on similarity, and the unrelated photo matches nothing.
        self.assertIn(-101, matches)
        self.assertIn(-103, matches)
        self.assertNotIn(-102, matches)
        self.assertNotIn(-104, matches)  # 0.85 lookalike stays out
        # The product's own offer is already listed on the product page.
        self.assertNotIn(-100, matches)
        self.assertTrue(matches[-101]["identical"])
        self.assertFalse(matches[-103]["identical"])
        self.assertGreaterEqual(matches[-103]["similarity"], app.SAME_PRODUCT_MINIMUM_SCORE)
        self.assertFalse(matches[-103]["same_title"])
        self.assertEqual(result["identical"], 1)
        self.assertEqual(result["visual"], 1)

    def test_hash_matching_still_works_without_the_visual_model(self):
        with patch.object(
            app, "encode_photo_embeddings", side_effect=RuntimeError("model missing")
        ), patch.object(app, "start_photo_embedding", return_value=False):
            result = app.same_product_sellers("test-scan", "review offer")

        self.assertFalse(result["visual_available"])
        self.assertIn("model missing", result["reason"])
        matches = result["matches"]
        self.assertEqual([match["chat_id"] for match in matches], [-101])
        self.assertTrue(matches[0]["identical"])

    def test_the_page_reports_progress_while_photos_are_still_being_read(self):
        with patch.object(
            app, "encode_photo_embeddings", side_effect=self._product_embedding
        ), patch.object(app, "start_photo_embedding", return_value=True):
            with app._photo_embed_lock:
                app._photo_embed_state.update(
                    running=True, checked=3, total=10,
                    message="Reading 10 photos for image matching…",
                )
            result = app.same_product_sellers("test-scan", "review offer")

        self.assertEqual(result["status"], "indexing")
        self.assertTrue(result["running"])
        self.assertIn("Reading 10 photos", result["reason"])
        # Matches found so far are still returned while the index fills.
        self.assertTrue(result["matches"])


class WebResearchTests(unittest.TestCase):
    """Product names and details come from the live web, not from models."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB_PATH
        self.original_image_dir = app.IMAGE_DIR
        self.original_scan_id = app._current_scan_id
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        app.IMAGE_DIR = Path(self.temp_dir.name) / "images"
        app.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        app._current_scan_id = "test-scan"
        app.initialize_database()
        Image.new("RGB", (32, 32), "white").save(app.IMAGE_DIR / "offer.jpg")
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """INSERT INTO scans (
                    scan_id, start_date, end_date, market, completed_at, status,
                    extractor_version
                ) VALUES ('test-scan', '2026-10-01', '2026-10-03', 'Algeria',
                          '2026-10-03T12:00:00+00:00', 'completed', 3)"""
            )
            connection.execute(
                """INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text,
                    review_candidate, image_name
                ) VALUES ('test-scan', -1001234567890, 42, 'review offer',
                          'review offer', 'Supplier One', 'Wholesale Chat',
                          '2026-10-02T12:00:00+00:00',
                          'Steel bottle 500ml wholesale', 1, 'offer.jpg')"""
            )
            connection.commit()

    def tearDown(self):
        app.DB_PATH = self.original_db
        app.IMAGE_DIR = self.original_image_dir
        app._current_scan_id = self.original_scan_id
        self.temp_dir.cleanup()

    @staticmethod
    def _results():
        return [
            {
                "title": "Steel Water Bottle 500ml - Amazon.ae",
                "href": "https://www.amazon.ae/steel-bottle-500ml",
                "body": "Steel bottle 500ml double wall, BPA free lid.",
            },
            {
                "title": "500ml Stainless Steel Bottle | eBay",
                "href": "https://www.ebay.com/itm/steel-bottle",
                "body": "Wholesale steel bottle 500ml, ships worldwide.",
            },
            {
                "title": "Unrelated Page",
                "href": "https://example.com/other",
                "body": "Nothing about this product.",
            },
        ]

    @staticmethod
    def _detail(**overrides):
        detail = {
            "name": "Steel Water Bottle 500ml",
            "brand": "",
            "category": "",
            "description": "Steel bottle 500ml double wall, BPA free lid.",
            "specs": [],
            "sources": [
                {
                    "title": "Steel Water Bottle 500ml",
                    "url": "https://www.amazon.ae/steel-bottle-500ml",
                    "domain": "amazon.ae",
                }
            ],
            "confidence": 0.8,
        }
        detail.update(overrides)
        return detail

    def test_web_research_builds_a_name_description_and_source_pages(self):
        with patch.object(
            app, "web_search_text", return_value=self._results()
        ) as search:
            detail = app.web_product_research("steel bottle 500ml wholesale")

        search.assert_called_once()
        self.assertEqual(detail["name"], "Steel Water Bottle 500ml")
        self.assertIn("double wall", detail["description"])
        self.assertEqual(len(detail["sources"]), 3)
        self.assertEqual(detail["sources"][0]["domain"], "amazon.ae")
        self.assertGreaterEqual(detail["confidence"], app.WEB_RESEARCH_MINIMUM_CONFIDENCE)

    def test_supplier_post_noise_is_stripped_out_of_the_search_query(self):
        noisy = "🏃 متراطيش لافار 🔊 hair clipper Disponible 📞 0555 12 34 56"
        candidates = app.web_query_candidates(noisy, "hair clipper")

        self.assertTrue(candidates)
        # The emoji and punctuation never reach the search engine.
        self.assertFalse(any(re.search(r"[^\w\s\u0600-\u06FF]", c) for c in candidates))
        # The supplier catchphrases are dropped, the product name is kept.
        self.assertIn("hair clipper", candidates)
        self.assertNotIn("متراطيش", " ".join(candidates))
        self.assertEqual(len(candidates), len(set(c.casefold() for c in candidates)))
        # The cleanest query comes first so one search is usually enough.
        self.assertEqual(candidates[0], "hair clipper")

    def test_promo_filler_is_stripped_from_the_saved_product_name(self):
        self.assertTrue(
            app.strip_web_noise("Power Bank : تسوق اونلاين بأفضل الاسعار في السعودية")
            .strip()
            .startswith("Power Bank")
        )
        self.assertEqual(app.strip_web_noise("Steel Water Bottle 500ml"),
                         "Steel Water Bottle 500ml")

    def test_a_noisy_post_falls_back_to_the_next_searchable_query(self):
        attempts = []

        def flaky_search(query, limit=8):
            attempts.append(query)
            if "steel" not in query:
                raise RuntimeError("The web search failed (DDGSException): No results found.")
            return self._results()

        with patch.object(app, "web_search_text", side_effect=flaky_search):
            detail = app.web_product_research(
                "متراطيش لافار Disponible", fallback_query="steel bottle 500ml"
            )

        self.assertGreater(len(attempts), 1)
        self.assertEqual(detail["name"], "Steel Water Bottle 500ml")
        self.assertGreaterEqual(detail["confidence"], app.WEB_RESEARCH_MINIMUM_CONFIDENCE)

    def test_when_every_query_fails_the_last_error_is_reported(self):
        with patch.object(
            app,
            "web_search_text",
            side_effect=RuntimeError("The web search failed (DDGSException): No results found."),
        ):
            with self.assertRaises(RuntimeError) as raised:
                app.web_product_research("متراطيش لافار", fallback_query="hair clipper")

        self.assertIn("No results found", str(raised.exception))

    def test_a_web_name_above_the_auto_save_floor_is_saved(self):
        # A web name at 0.9 confidence is above the 0.85 auto-save floor, so the
        # product is named and saved without hand-confirmation.
        with patch.object(
            app,
            "web_product_research",
            return_value=self._detail(confidence=0.9),
        ):
            result = app.identify_product_from_web("test-scan", "review offer")

        self.assertTrue(result["saved"])
        product = app.load_results()[0]
        self.assertFalse(product["candidate_only"])
        self.assertEqual(product["identification_method"], "web")
        self.assertAlmostEqual(product["identification_confidence"], 0.9)
        details = app.load_product_details("test-scan", "steel water bottle 500ml")
        self.assertAlmostEqual(details["confidence"], 0.9)

    def test_a_photo_named_candidate_is_queued_for_web_confirmation(self):
        # The photo read is only 0.4, below the 0.5 photo save floor, so the
        # name stays a review candidate and is queued for the web instead.
        app.identify_product(
            "test-scan", "review offer", "Hair Clipper", method="photo", confidence=0.4
        )
        self.assertEqual(app.pending_web_research("test-scan"), ["hair clipper"])

        # The full auto-identify chain prefers the web and falls back to photo.
        with patch.object(
            app,
            "identify_product_from_web",
            side_effect=ValueError("The web returned no results."),
        ), patch.object(
            app,
            "product_image_choices",
            return_value=[{"label": "hair clipper", "score": 0.9}],
        ):
            result = app.auto_identify_product("test-scan", "hair clipper")

        self.assertTrue(result["saved"])
        self.assertIn("Web research could not name this offer", result["note"])
        # A 0.9 photo save clears the per-message review candidate, so the row is
        # no longer candidate_only. The pending-web queue still lists this product
        # because it is keyed on the original normalized message product.
        self.assertFalse(app.load_results()[0]["candidate_only"])
        self.assertEqual(app.pending_web_research("test-scan"), ["hair clipper"])
        self.assertEqual(app.pending_photo_candidates("test-scan"), [])

    def test_weak_web_agreement_is_not_saved(self):
        with patch.object(
            app,
            "web_product_research",
            return_value=self._detail(confidence=0.4),
        ):
            with self.assertRaises(ValueError) as raised:
                app.identify_product_from_web("test-scan", "review offer")

        self.assertIn("40%", str(raised.exception))
        self.assertTrue(app.load_results()[0]["candidate_only"])

    def test_auto_identification_prefers_the_web_and_falls_back_to_the_photo(self):
        with patch.object(
            app,
            "identify_product_from_web",
            side_effect=ValueError("The web returned no results."),
        ), patch.object(
            app,
            "product_image_choices",
            return_value=[{"label": "hair clipper", "score": 0.9}],
        ):
            result = app.auto_identify_product("test-scan", "review offer")

        self.assertTrue(result["saved"])
        self.assertIn("Web research could not name this offer", result["note"])

    def test_a_failed_web_search_and_a_failed_photo_report_both_reasons(self):
        with patch.object(
            app, "identify_product_from_web", side_effect=ValueError("web down")
        ), patch.object(
            app,
            "identify_product_from_image",
            side_effect=ValueError("photo unreadable"),
        ):
            result = app.auto_identify_product("test-scan", "review offer")

        self.assertIn("web down", result["error"])
        self.assertIn("photo unreadable", result["error"])

    def test_photo_named_products_are_queued_for_web_confirmation(self):
        # The photo read is only 0.4, below the 0.5 photo save floor, so the
        # name stays a review candidate and is queued for the web instead.
        app.identify_product(
            "test-scan", "review offer", "Hair Clipper", method="photo", confidence=0.4
        )
        self.assertEqual(app.pending_web_research("test-scan"), ["hair clipper"])

        # The full auto-identify chain prefers the web and falls back to photo.
        with patch.object(
            app,
            "identify_product_from_web",
            side_effect=ValueError("The web returned no results."),
        ), patch.object(
            app,
            "product_image_choices",
            return_value=[{"label": "hair clipper", "score": 0.9}],
        ):
            result = app.auto_identify_product("test-scan", "hair clipper")

        self.assertTrue(result["saved"])
        self.assertIn("Web research could not name this offer", result["note"])
        # A 0.9 photo save clears the per-message review candidate, so the row is
        # no longer candidate_only. The pending-web queue still lists this product
        # because it is keyed on the original normalized message product.
        self.assertFalse(app.load_results()[0]["candidate_only"])
        self.assertEqual(app.pending_web_research("test-scan"), ["hair clipper"])


class ExactProductAdTests(unittest.TestCase):
    """Only ads that name the product itself may reach the exact ads section."""

    @staticmethod
    def _ad(ad_id="ad", **overrides):
        row = {
            "ad_id": ad_id,
            "page_name": "",
            "creative_title": "",
            "creative_text": "",
            "link_url": "",
            "link_caption": "",
            "snapshot_url": "",
        }
        row.update(overrides)
        return row

    def test_an_ad_that_names_the_product_is_an_exact_match(self):
        profile = app.product_match_profile("Item1001")

        match = app.score_meta_ad(profile, self._ad(creative_title="Item1001 official"))

        self.assertEqual(match["level"], "exact")

    def test_another_model_of_the_same_line_is_not_an_exact_match(self):
        profile = app.product_match_profile("Item1001")

        match = app.score_meta_ad(profile, self._ad(creative_title="Item1002 wholesale lot"))

        self.assertEqual(match["level"], "similar")
        self.assertTrue(
            any("different model" in reason.casefold() for reason in match["reasons"])
        )

    def test_a_variant_of_the_product_is_not_an_exact_match(self):
        profile = app.product_match_profile("Item1001")

        match = app.score_meta_ad(profile, self._ad(creative_title="Item1001 Plus"))

        self.assertEqual(match["level"], "similar")
        self.assertTrue(
            any("variant" in reason.casefold() for reason in match["reasons"])
        )

    def test_a_known_brand_must_appear_in_the_ad_for_an_exact_match(self):
        profile = app.product_match_profile("Item1001", attributes={"brand": ["Acme"]})

        unbadged = app.score_meta_ad(profile, self._ad(creative_title="Item1001"))
        badged = app.score_meta_ad(
            profile, self._ad(creative_title="Item1001", page_name="Acme Store")
        )

        self.assertEqual(unbadged["level"], "similar")
        self.assertEqual(badged["level"], "exact")

    def test_the_exact_section_never_contains_a_same_category_ad(self):
        profile = app.product_match_profile("Item1001")

        exact, similar = app.classify_product_ads(
            profile,
            [
                self._ad("exact-ad", creative_title="Item1001 genuine"),
                self._ad("competitor", creative_title="Item1002 best price"),
                self._ad("category", creative_title="Summer sale on sofas"),
            ],
        )

        self.assertEqual([ad["ad_id"] for ad in exact], ["exact-ad"])
        self.assertEqual(
            sorted(ad["ad_id"] for ad in similar), ["category", "competitor"]
        )
        self.assertTrue(
            all(ad["match"]["reasons"] for ad in exact + similar),
            "every ad must explain why it is in its section",
        )

    def test_a_repeated_ad_row_is_only_counted_once(self):
        profile = app.product_match_profile("Item1001")

        exact, similar = app.classify_product_ads(
            profile,
            [
                self._ad("same", creative_title="Item1001"),
                self._ad("same", creative_title="Item1001"),
                self._ad("other", creative_title="Item1002"),
            ],
        )

        self.assertEqual([ad["ad_id"] for ad in exact], ["same"])
        self.assertEqual([ad["ad_id"] for ad in similar], ["other"])


class ProductIntelligenceTests(unittest.TestCase):
    """The product page numbers must be traceable to a source, never invented."""

    def test_the_margin_uses_the_lowest_supplier_price_and_a_read_competitor_price(self):
        estimate = app.product_pricing_estimate(
            1200,
            "DZD",
            "piece",
            [
                {"price": 2000, "currency": "DZD", "status": "ok"},
                {"price": 2400, "currency": "DZD", "status": "ok"},
                {"price": 9999, "currency": "USD", "status": "ok"},
                {"price": None, "currency": "DZD", "status": "failed"},
            ],
        )

        self.assertEqual(estimate["estimated_cost"], 1200)
        self.assertEqual(estimate["estimated_selling_price"], 2200)
        self.assertEqual(estimate["profit_margin_percent"], 45.5)
        self.assertIn("median", estimate["basis"])

    def test_a_missing_competitor_price_reports_no_margin_instead_of_a_guess(self):
        estimate = app.product_pricing_estimate(1200, "DZD", "piece", [])

        self.assertIsNone(estimate["estimated_selling_price"])
        self.assertIsNone(estimate["profit_margin_percent"])
        self.assertIn("needs a comparable competitor price", estimate["basis"])

    def test_the_cheapest_priced_offer_is_the_cost_when_no_currency_was_detected(self):
        estimate = app.product_pricing_estimate(
            None,
            None,
            None,
            [],
            offers=[
                {"price": 1500, "currency": None, "price_unit": None},
                {"price": 900, "currency": None, "price_unit": None},
            ],
        )

        self.assertEqual(estimate["estimated_cost"], 900)
        self.assertIn("currency not detected", estimate["basis"])

    def test_the_trend_prefers_the_recorded_research_direction(self):
        trend = app.product_trend_estimate(
            {"trend_direction": "rising"}, age_days=30, period_days=30,
            oldest_ad_days=40, exact_ads=[{"ad_id": "1"}],
        )

        self.assertEqual(trend["trend_direction"], "rising")
        self.assertGreaterEqual(trend["trend_score"], 78)
        self.assertIn("Research trend: rising", trend["basis"])

    def test_the_gallery_lists_supplier_photos_first_and_never_repeats_one(self):
        gallery = app.product_gallery(
            [
                {"image": "/media/a.jpg", "supplier": "Supplier One"},
                {"image": "/media/a.jpg", "supplier": "Supplier One"},
            ],
            [
                {"page_name": "Ad Store", "image_url": "https://cdn.example.com/ad.jpg"},
            ],
            [{"title": "Competitor page", "image_url": "https://cdn.example.com/page.jpg"}],
        )

        self.assertEqual(
            [item["url"] for item in gallery],
            ["/media/a.jpg", "https://cdn.example.com/ad.jpg", "https://cdn.example.com/page.jpg"],
        )
        self.assertEqual(gallery[0]["source"], "supplier")

    def test_every_product_state_is_explicit(self):
        original = dict(app._identify_state)
        original_active = set(app._identify_state["active"])
        original_failed = dict(app._identify_state["failed"])

        def restore():
            app._identify_state.update(original)
            app._identify_state["active"] = original_active
            app._identify_state["failed"] = original_failed

        self.addCleanup(restore)
        with app._identify_lock:
            app._identify_state["scan_id"] = "scan-1"
            app._identify_state["active"] = {"review offer 1"}
            app._identify_state["failed"] = {"review offer 2": "the photo was unreadable"}

        named, _ = app.product_status_for("scan-1", "steel bottle", False, None)
        processing, _ = app.product_status_for("scan-1", "review offer 1", True, None)
        failed, reason = app.product_status_for("scan-1", "review offer 2", True, None)
        waiting, note = app.product_status_for("scan-1", "review offer 3", True, None)

        self.assertEqual(named, app.PRODUCT_STATUS_IDENTIFIED)
        self.assertEqual(processing, app.PRODUCT_STATUS_PROCESSING)
        self.assertEqual(failed, app.PRODUCT_STATUS_FAILED)
        self.assertEqual(reason, "the photo was unreadable")
        self.assertEqual(waiting, app.PRODUCT_STATUS_AWAITING_REVIEW)
        self.assertTrue(note)

    def test_the_status_counts_describe_every_row(self):
        counts = app.count_products_by_status(
            [
                {"status": app.PRODUCT_STATUS_IDENTIFIED},
                {"status": app.PRODUCT_STATUS_AWAITING_REVIEW},
                {"status": app.PRODUCT_STATUS_AWAITING_REVIEW},
                {"status": app.PRODUCT_STATUS_PROCESSING},
                {"status": app.PRODUCT_STATUS_FAILED},
            ]
        )

        self.assertEqual(counts["total"], 5)
        self.assertEqual(counts[app.PRODUCT_STATUS_IDENTIFIED], 1)
        self.assertEqual(counts[app.PRODUCT_STATUS_AWAITING_REVIEW], 2)
        self.assertEqual(counts[app.PRODUCT_STATUS_PROCESSING], 1)
        self.assertEqual(counts[app.PRODUCT_STATUS_FAILED], 1)


class ScanIntegrityTests(unittest.TestCase):
    """The scan record and the table rows must be checked against each other."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB_PATH
        self.original_scan_id = app._current_scan_id
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        app._current_scan_id = "integrity-scan"
        app.initialize_database()
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """
                INSERT INTO scans (
                    scan_id, start_date, end_date, market, completed_at, status,
                    scanned_messages, products_found, extractor_version
                ) VALUES ('integrity-scan', '2026-10-01', '2026-10-03', 'Algeria',
                          '2026-10-03T12:00:00+00:00', 'completed', 25, 1, 3)
                """
            )
            connection.execute(
                """
                INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text, review_candidate
                ) VALUES (
                    'integrity-scan', -1001234567890, 42, 'Steel Bottle', 'steel bottle',
                    'Supplier One', 'Wholesale Chat', '2026-10-02T12:00:00+00:00',
                    'Steel Bottle 1200 DZD', 0
                )
                """
            )
            connection.commit()

    def tearDown(self):
        app.DB_PATH = self.original_db
        app._current_scan_id = self.original_scan_id
        self.temp_dir.cleanup()

    def test_the_scan_record_is_compared_with_the_rows_that_are_shown(self):
        products = app.load_results()

        integrity = app.scan_integrity("integrity-scan", products, deep=True)

        self.assertEqual(integrity["returned"], 1)
        self.assertEqual(integrity["detected"], 1)
        self.assertEqual(integrity["recorded_in_scan"], 1)
        self.assertTrue(integrity["consistent"])

    def test_a_product_that_never_reached_the_table_is_reported(self):
        integrity = app.scan_integrity("integrity-scan", [], deep=True)

        self.assertFalse(integrity["consistent"])
        self.assertTrue(any("1 product" in issue for issue in integrity["issues"]))

    def test_an_older_scan_record_is_reported_but_not_called_a_mismatch(self):
        products = app.load_results()
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            # Older scans stored the number of flagged posts in this column.
            connection.execute(
                "UPDATE scans SET products_found = 4 WHERE scan_id = 'integrity-scan'"
            )
            connection.commit()

        integrity = app.scan_integrity("integrity-scan", products)

        self.assertTrue(integrity["consistent"])
        self.assertFalse(integrity["checked_from_messages"])
        self.assertEqual(integrity["recorded_in_scan"], 4)

    def test_the_finished_scan_verdict_is_stored_for_the_dashboard(self):
        app.verify_scan_integrity("integrity-scan")

        stored = app.last_integrity_report("integrity-scan")

        self.assertIsNotNone(stored)
        self.assertTrue(stored["checked_from_messages"])
        self.assertTrue(stored["consistent"])

    def test_a_running_scan_is_not_reported_as_a_mismatch(self):
        products = app.load_results()
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                "UPDATE scans SET status = 'running', products_found = 0 WHERE scan_id = 'integrity-scan'"
            )
            connection.commit()

        integrity = app.scan_integrity("integrity-scan", products)

        self.assertTrue(integrity["live"])
        self.assertTrue(integrity["consistent"])

    def test_the_deep_check_counts_the_products_straight_from_the_messages(self):
        integrity = app.scan_integrity("integrity-scan", app.load_results(), deep=True)

        self.assertEqual(integrity["detected"], 1)
        self.assertTrue(integrity["checked_from_messages"])


class ProgressiveIdentificationTests(unittest.TestCase):
    """Identification must run while the scan is still going, not only after it."""

    def setUp(self):
        self.original_state = dict(app._identify_state)
        self.original_active = set(app._identify_state["active"])
        self.original_failed = dict(app._identify_state["failed"])
        app._identify_state.update(
            running=False, scan_id=None, active=set(), done=set(), failed={},
            identified=0, queued=0, message="",
        )

    def tearDown(self):
        app._identify_state.update(self.original_state)
        app._identify_state["active"] = self.original_active
        app._identify_state["failed"] = self.original_failed

    def test_queued_products_are_identified_while_the_scan_runs(self):
        saved = {
            "saved": True,
            "key": "item1001",
            "name": "Item1001",
            "score": 0.9,
            "source": "web",
        }
        with patch.object(
            app, "identify_product_progressive", return_value=saved
        ) as identify, patch.object(app, "load_results", return_value=[]), patch.object(
            app, "start_meta_ads_sync"
        ):
            self.assertTrue(app.start_progressive_identification("scan-live", workers=1))
            self.assertEqual(app.enqueue_identification("scan-live", ["review offer 1"]), 1)
            for _ in range(2000):
                summary = app.progressive_identification_summary("scan-live")
                if summary["pending"] == 0 and summary["identified"] == 1:
                    break
                time.sleep(0.01)
            finished = app.finish_progressive_identification("scan-live", timeout=5)

        self.assertEqual(summary["identified"], 1)
        self.assertEqual(summary["pending"], 0)
        self.assertTrue(finished["drained"])
        identify.assert_called_once_with("scan-live", "review offer 1")

    def test_a_product_no_reader_can_name_is_reported_as_failed_with_a_reason(self):
        with patch.object(
            app,
            "identify_product_progressive",
            return_value={"saved": False, "error": "the photo was unreadable"},
        ), patch.object(app, "load_results", return_value=[]):
            app.start_progressive_identification("scan-live", workers=1)
            app.enqueue_identification("scan-live", ["review offer 9"])
            for _ in range(2000):
                summary = app.progressive_identification_summary("scan-live")
                if summary["pending"] == 0 and summary["failed"] == 1:
                    break
                time.sleep(0.01)
            status, reason = app.product_status_for("scan-live", "review offer 9", True, None)
            app.finish_progressive_identification("scan-live", timeout=5)

        self.assertEqual(summary["failed"], 1)
        self.assertEqual(status, app.PRODUCT_STATUS_FAILED)
        self.assertEqual(reason, "the photo was unreadable")

    def test_identification_is_not_queued_for_another_scan(self):
        app._identify_state["scan_id"] = "scan-live"

        self.assertEqual(app.enqueue_identification("another-scan", ["review offer 1"]), 0)


def _spreadsheet_column(index):
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def write_test_workbook(path, sheets):
    """Write a minimal xlsx (worksheet name -> rows) with no dependencies."""
    with zipfile.ZipFile(path, "w") as archive:
        names = "".join(
            f'<sheet name="{name}" sheetId="{position}"/>'
            for position, (name, _) in enumerate(sheets, start=1)
        )
        archive.writestr(
            "xl/workbook.xml",
            f'<?xml version="1.0"?><workbook><sheets>{names}</sheets></workbook>',
        )
        for position, (_, rows) in enumerate(sheets, start=1):
            parts = ['<?xml version="1.0"?><worksheet><sheetData>']
            for row_index, row in enumerate(rows, start=1):
                parts.append(f'<row r="{row_index}">')
                for column_index, value in enumerate(row):
                    reference = f"{_spreadsheet_column(column_index)}{row_index}"
                    parts.append(
                        f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'
                    )
                parts.append("</row>")
            parts.append("</sheetData></worksheet>")
            archive.writestr(f"xl/worksheets/sheet{position}.xml", "".join(parts))
    return path


class SourceCatalogueTests(unittest.TestCase):
    """The worksheet structure is what drives categories and scanning."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB_PATH
        self.original_entities = app._chat_entities
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        app.initialize_database()
        app._chat_entities = {
            -1001: SimpleNamespace(title="Electronics Deals DZ", username="electronicsdz"),
            -1002: SimpleNamespace(title="Beauty Wholesale", username="beautywholesale"),
        }
        self.folder = Path(self.temp_dir.name)

    def tearDown(self):
        app.DB_PATH = self.original_db
        app._chat_entities = self.original_entities
        self.temp_dir.cleanup()

    def _workbook(self, name="sources.xlsx"):
        return write_test_workbook(
            self.folder / name,
            [
                (
                    "Electronics",
                    [
                        ["Channel Name", "Username", "Language", "Country"],
                        ["Electronics Deals DZ", "@electronicsdz", "AR", "DZ"],
                        ["Happy Tech", "@happytech", "AR", "DZ"],
                    ],
                ),
                (
                    "Beauty",
                    [
                        ["Channel Name", "Username", "Language"],
                        ["Beauty Wholesale", "@beautywholesale", "FR"],
                    ],
                ),
            ],
        )

    def test_each_worksheet_becomes_its_own_category(self):
        summary = app.import_sources_from_workbook(self._workbook())

        catalogue = app.source_catalogue()
        names = [category["name"] for category in catalogue["categories"]]

        self.assertEqual(sorted(names), ["Beauty", "Electronics"])
        self.assertEqual(summary["sources_added"], 3)
        self.assertEqual(catalogue["totals"]["channels"], 3)
        electronics = next(c for c in catalogue["categories"] if c["name"] == "Electronics")
        self.assertEqual(electronics["channel_count"], 2)
        self.assertEqual(electronics["linked_channel_count"], 1)

    def test_a_worksheet_without_a_header_row_is_read_from_its_values(self):
        path = self.folder / "raw.xlsx"
        write_test_workbook(
            path,
            [("Fitness", [["Fit Gear", "@fitgear"], ["Gym Supplies", "@gymsupplies"]])],
        )

        app.import_sources_from_workbook(path)

        category = next(
            item for item in app.source_catalogue()["categories"] if item["name"] == "Fitness"
        )
        self.assertEqual(category["channel_count"], 2)
        self.assertEqual(
            sorted(channel["username"] for channel in category["channels"]),
            ["@fitgear", "@gymsupplies"],
        )

    def test_a_single_csv_export_uses_its_file_name_as_the_category(self):
        path = self.folder / "Home & Kitchen.csv"
        path.write_text(
            "Channel Name,Telegram,Country\nKitchen Gadgets,@kitchengadgets,DZ\n",
            encoding="utf-8",
        )

        summary = app.import_sources_from_workbook(path)

        self.assertEqual(summary["categories"], ["Home & Kitchen"])
        self.assertIn(
            "Home & Kitchen",
            [category["name"] for category in app.source_catalogue()["categories"]],
        )

    def test_re_importing_updates_sources_instead_of_duplicating_them(self):
        path = self._workbook()
        first = app.import_sources_from_workbook(path)

        second = app.import_sources_from_workbook(path)

        self.assertEqual(first["sources_added"], 3)
        self.assertEqual(second["sources_added"], 0)
        self.assertEqual(second["sources_updated"], 3)
        self.assertEqual(app.source_catalogue()["totals"]["channels"], 3)

    def test_a_category_scans_only_its_own_channels(self):
        app.import_sources_from_workbook(self._workbook())

        targets = app.resolve_scan_targets("Beauty")

        self.assertEqual(targets["chat_ids"], [-1002])
        self.assertEqual(targets["unavailable"], [])

    def test_a_channel_missing_from_this_telegram_account_is_reported(self):
        app.import_sources_from_workbook(self._workbook())

        targets = app.resolve_scan_targets("Electronics")

        self.assertEqual(targets["chat_ids"], [-1001])
        self.assertEqual(targets["unavailable"], ["Happy Tech"])

    def test_explicit_sources_can_be_scanned_without_a_category(self):
        app.import_sources_from_workbook(self._workbook())

        targets = app.resolve_scan_targets("", ["handle:beautywholesale"])

        self.assertEqual(targets["chat_ids"], [-1002])
        self.assertEqual(targets["sources_considered"], 1)

    def test_a_scan_can_record_the_category_it_came_from(self):
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(scans)")}

        self.assertIn("category", columns)

    def test_source_score_rewards_channels_that_name_their_products(self):
        named = app.compute_source_score(10, 9, 8)
        unnamed = app.compute_source_score(10, 0, 2)

        self.assertIsNotNone(named)
        self.assertGreater(named, unnamed)
        self.assertLessEqual(named, 100)
        self.assertIsNone(app.compute_source_score(0, 0, 0))

    def test_scan_history_stores_what_each_channel_contributed(self):
        app.import_sources_from_workbook(self._workbook())
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """
                INSERT INTO scans (
                    scan_id, start_date, end_date, market, completed_at, status,
                    extractor_version, category
                ) VALUES ('cat-scan', '2026-10-01', '2026-10-03', 'Algeria',
                          '2026-10-03T12:00:00+00:00', 'completed', 3, 'Electronics')
                """
            )
            connection.execute(
                """
                INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text, price,
                    currency, review_candidate
                ) VALUES (
                    'cat-scan', -1001, 1, 'Item1001', 'item1001',
                    'Supplier One', 'Electronics Deals DZ', '2026-10-02T12:00:00+00:00',
                    'Item1001 1200 DZD', 1200, 'DZD', 0
                )
                """
            )
            connection.commit()

        written = app.record_source_scan_history("cat-scan", [-1001], "Electronics")

        self.assertEqual(written, 1)
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            row = connection.execute(
                "SELECT products_found, products_identified, source_score, category FROM source_scan_history WHERE scan_id = 'cat-scan'"
            ).fetchone()
            source = connection.execute(
                "SELECT products_found, last_scanned_at, source_score FROM sources WHERE username = '@electronicsdz'"
            ).fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], 1)
        self.assertEqual(row[3], "Electronics")
        self.assertIsNotNone(row[2])
        self.assertEqual(source[0], 1)
        self.assertTrue(source[1])
        self.assertIsNotNone(source[2])


class OverviewIntelligenceTests(unittest.TestCase):
    """The dashboard overview must describe the products it is showing."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = app.DB_PATH
        self.original_entities = app._chat_entities
        self.original_current = app._current_scan_id
        self.original_latest = app._latest_scan_info
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        app.initialize_database()
        app._chat_entities = {
            -1001: SimpleNamespace(title="Electronics Deals DZ", username="electronicsdz"),
        }
        app._current_scan_id = None
        app._latest_scan_info = {}
        self.folder = Path(self.temp_dir.name)

    def tearDown(self):
        app.DB_PATH = self.original_db
        app._chat_entities = self.original_entities
        app._current_scan_id = self.original_current
        app._latest_scan_info = self.original_latest
        self.temp_dir.cleanup()

    def _scan(self, scan_id="overview-scan", category="", status="completed"):
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """
                INSERT INTO scans (
                    scan_id, start_date, end_date, market, completed_at, status,
                    extractor_version, category
                ) VALUES (?, '2026-10-01', '2026-10-16', 'Algeria',
                          '2026-10-16T12:00:00+00:00', ?, 3, ?)
                """,
                (scan_id, status, category),
            )
            connection.commit()
        return scan_id

    def _product(self, key, name, **overrides):
        product = {
            "key": key,
            "product": name,
            "status": app.PRODUCT_STATUS_IDENTIFIED,
            "status_label": "Identified",
            "candidate_only": False,
            "offers": [],
            "posts": 1,
            "sellers": 1,
            "max_views": 1000,
            "total_reactions": 10,
            "total_comments": 2,
            "total_forwards": 1,
            "trend": {"trend_score": 70, "basis": "Mentions are rising"},
            "pricing": {"estimated_cost": 1200, "currency": "DZD", "profit_margin_percent": 40},
            "ads_match_summary": {"exact": 3, "similar": 5},
            "gallery_count": 2,
            "opportunity": {
                "score": 80,
                "label": "HIGH OPPORTUNITY",
                "reasons": ["3 exact active ads", "two suppliers listing it"],
            },
            "growth_rate": 25.0,
        }
        product.update(overrides)
        return product

    def _offer(self, chat_id, supplier, date_text, views=100):
        return {
            "chat_id": chat_id,
            "supplier": supplier,
            "channel": "Electronics Deals DZ",
            "date": date_text,
            "views": views,
            "reactions": 5,
            "comments": 1,
            "forwards": 0,
            "price": 1200,
            "currency": "DZD",
            "message_id": 1,
        }

    def test_the_overview_counts_every_product_it_is_shown(self):
        products = [
            self._product("a", "Item1001"),
            self._product("b", "Item1002"),
            self._product(
                "c",
                "Item1003",
                status=app.PRODUCT_STATUS_AWAITING_REVIEW,
                candidate_only=True,
            ),
        ]

        overview = app.platform_overview(None, products)

        metrics = overview["metrics"]
        self.assertEqual(metrics["products_total"], 3)
        self.assertEqual(metrics["products_identified"], 2)
        self.assertEqual(metrics["review_needed"], 1)
        self.assertEqual(overview["empty_state"], "")
        self.assertEqual(len(overview["trending"]), 3)

    def test_trending_is_ranked_by_opportunity_and_says_why(self):
        weak = self._product("weak", "Item1001", opportunity={"score": 40, "label": "LOW", "reasons": ["thin demand"]})
        strong = self._product(
            "strong",
            "Item1002",
            opportunity={"score": 91, "label": "HIGH OPPORTUNITY", "reasons": ["7 exact active ads"]},
        )

        overview = app.platform_overview(None, [weak, strong], limit=1)

        self.assertEqual(len(overview["trending"]), 1)
        self.assertEqual(overview["trending"][0]["key"], "strong")
        self.assertIn("7 exact active ads", overview["trending"][0]["why"])

    def test_hot_products_are_grouped_by_their_channel_category(self):
        csv_path = self.folder / "Electronics.csv"
        csv_path.write_text(
            "Channel Name,Telegram\nElectronics Deals DZ,@electronicsdz\n",
            encoding="utf-8",
        )
        app.import_sources_from_workbook(csv_path)
        app.link_sources_to_chats()
        scan_id = self._scan(category="Electronics")

        overview = app.platform_overview(
            scan_id,
            [self._product("a", "Item1001", offers=[self._offer(-1001, "Supplier One", "2026-10-10T10:00:00+00:00")])],
        )

        self.assertEqual(overview["metrics"]["categories_active"], 1)
        self.assertEqual([group["category"] for group in overview["hot_by_category"]], ["Electronics"])
        self.assertEqual(overview["hot_by_category"][0]["products"][0]["key"], "a")

    def test_a_supplier_pushing_more_product_late_in_the_scan_shows_growth(self):
        scan_id = self._scan()
        offers = [
            self._offer(-1001, "Supplier One", "2026-10-02T10:00:00+00:00"),
            self._offer(-1001, "Supplier One", "2026-10-12T10:00:00+00:00"),
            self._offer(-1001, "Supplier One", "2026-10-14T10:00:00+00:00"),
            self._offer(-1001, "Supplier One", "2026-10-15T10:00:00+00:00"),
        ]

        overview = app.platform_overview(scan_id, [self._product("a", "Item1001", offers=offers)])

        top = overview["suppliers"]["top"][0]
        self.assertEqual(top["supplier"], "Supplier One")
        self.assertEqual(top["posts"], 4)
        self.assertEqual(top["growth_rate"], 200.0)
        self.assertTrue(top["is_new"])
        self.assertEqual(len(overview["suppliers"]["growing"]), 1)
        self.assertEqual(overview["metrics"]["suppliers_new"], 1)

    def test_the_scan_category_travels_with_the_overview(self):
        scan_id = self._scan(category="Beauty")

        overview = app.platform_overview(scan_id, [self._product("a", "Item1001")])

        self.assertEqual(overview["scan"]["scan_id"], scan_id)
        self.assertEqual(overview["scan"]["category"], "Beauty")
        self.assertEqual(overview["metrics"]["products_total"], 1)

    def test_an_empty_scan_explains_itself_instead_of_showing_blank_widgets(self):
        overview = app.platform_overview(None, [])

        self.assertEqual(overview["metrics"]["products_total"], 0)
        self.assertEqual(overview["trending"], [])
        self.assertEqual(overview["hot_by_category"], [])
        self.assertEqual(overview["suppliers"], {"top": [], "growing": [], "new": []})
        self.assertEqual(overview["sources"], [])
        self.assertTrue(overview["empty_state"])

    def test_the_overview_endpoint_reuses_a_payload_built_seconds_ago(self):
        calls = []
        original = app.platform_overview

        def counting(scan_id, products=None, limit=8):
            calls.append(scan_id)
            return original(scan_id, products, limit)

        app.platform_overview = counting
        app._overview_cache.update({"key": None, "at": 0.0, "payload": None})
        try:
            first = app._cached_overview(None)
            second = app._cached_overview(None)
        finally:
            app.platform_overview = original
            app._overview_cache.update({"key": None, "at": 0.0, "payload": None})

        self.assertEqual(len(calls), 1)
        self.assertIs(first, second)

    def test_a_product_without_ads_or_pricing_still_gets_scored(self):
        sparse = self._product(
            "sparse",
            "Item1001",
            trend={},
            pricing={},
            ads_match_summary={},
            opportunity=None,
            growth_rate=None,
        )
        sparse.pop("opportunity")

        overview = app.platform_overview(None, [sparse])

        row = overview["trending"][0]
        self.assertEqual(row["key"], "sparse")
        self.assertIsInstance(row["opportunity"], int)
        self.assertGreaterEqual(row["opportunity"], 0)
        self.assertLessEqual(row["opportunity"], 100)


class SupplierGuideImportTests(unittest.TestCase):
    """The guide is imported exactly as written, header row included."""

    def test_a_contact_link_header_still_becomes_the_header(self):
        from suppliers_import import dataset_from_rows

        # The Telegram-channel worksheet names its first column with a link,
        # not a shop name, and starts with an empty grid row.
        rows = [
            ["" for _ in range(4)],
            ["1", "رابط قناة التلغرام", "نوع المنتجات", "العمود 1"],
            ["2", "https://t.me/ElrayessShop", "متنوع", ""],
            ["3", "https://t.me/grosvip", "اكسسوار الهواتف", ""],
            ["4", "", "", ""],
        ]

        data = dataset_from_rows("Ch telegrame", rows, "preview:681208861", "681208861")

        self.assertEqual(data["header_row"], 2)
        self.assertEqual(data["headers"][0], "رابط قناة التلغرام")
        self.assertEqual(data["fields"], {"telegram": 0, "product_type": 1})
        # The header row must not be imported as if it were a supplier.
        self.assertEqual(len(data["records"]), 2)
        record = data["records"][0]
        self.assertEqual(record["supplier_name"], "https://t.me/ElrayessShop")
        self.assertEqual(record["telegram"], "https://t.me/ElrayessShop")
        self.assertEqual(record["product_type"], "متنوع")

    def test_the_shop_name_header_maps_every_contact_column(self):
        from suppliers_import import dataset_from_rows

        rows = [
            ["Nom De la Boutique", "Numéro De Téléphone", "Whatsapp", "Telegram", "type de produit"],
            ["Boutique A", "0550000001", "0550000001", "//", "Textile"],
            ["", "", "", "", ""],
        ]

        data = dataset_from_rows("TEXTILE", rows, "preview:831696121", "831696121")

        self.assertEqual(data["header_row"], 1)
        self.assertEqual(len(data["records"]), 1)
        record = data["records"][0]
        self.assertEqual(record["supplier_name"], "Boutique A")
        self.assertEqual(record["phone"], "0550000001")
        self.assertEqual(record["whatsapp"], "0550000001")
        self.assertEqual(record["product_type"], "Textile")


class SupplierBridgeTests(unittest.TestCase):
    """Directory matching is a lead generator, so it must explain itself."""

    ROWS = [
        {
            "id": 1,
            "category_sheet": "vetements pour femmes",
            "supplier_name": "Atelier Nour",
            "category": "vetements pour femmes",
            "product_type": "robes",
            "phone": "0550111222",
            "whatsapp": "",
            "telegram": "",
            "facebook": "",
            "city": "El Eulma",
            "notes": "",
        },
        {
            "id": 2,
            "category_sheet": "chaussures",
            "supplier_name": "Shoe House",
            "category": "chaussures",
            "product_type": "",
            "phone": "0660333444",
            "whatsapp": "",
            "telegram": "",
            "facebook": "",
            "city": "El Eulma",
            "notes": "",
        },
        {
            "id": 3,
            "category_sheet": "cuisine et menage",
            "supplier_name": "Top kitchen",
            "category": "cuisine et menage",
            "product_type": "ustensiles",
            "phone": "",
            "whatsapp": "",
            "telegram": "",
            "facebook": "",
            "city": "",
            "notes": "",
        },
    ]

    def test_english_product_meets_the_french_sheet(self):
        from supplier_bridge import match_suppliers

        matches = match_suppliers(
            "Women cotton dress", "vetements pour femmes", rows=self.ROWS
        )
        self.assertTrue(matches)
        self.assertEqual(matches[0]["supplier"], "Atelier Nour")
        self.assertIn("femme", matches[0]["matched_on"])
        self.assertGreater(matches[0]["score"], 0.3)
        self.assertEqual(matches[0]["phone"], "0550111222")

    def test_category_sheet_alone_is_a_usable_lead(self):
        from supplier_bridge import match_suppliers

        matches = match_suppliers("Wireless charger", "chaussures", rows=self.ROWS)
        self.assertEqual([item["supplier"] for item in matches], ["Shoe House"])
        self.assertEqual(matches[0]["matched_on"], ["chaussure"])

    def test_unrelated_product_returns_no_match(self):
        from supplier_bridge import match_suppliers

        self.assertEqual(
            match_suppliers("Steel water bottle", "", rows=self.ROWS), []
        )

    def test_coverage_counts_only_products_with_a_lead(self):
        from supplier_bridge import coverage

        payload = coverage(
            [
                {"key": "a", "product": "Women dress", "category": "vetements pour femmes"},
                {"key": "b", "product": "Steel water bottle", "category": ""},
            ],
            rows=self.ROWS,
        )
        self.assertEqual(payload["products_total"], 2)
        self.assertEqual(payload["products_matched"], 1)
        self.assertEqual(payload["directory_size"], 3)
        self.assertEqual(
            payload["products"]["a"]["matches"][0]["supplier"], "Atelier Nour"
        )
        self.assertEqual(payload["products"]["b"]["matches"], [])

    def test_plural_and_accent_folding_keep_words_apart(self):
        from supplier_bridge import expand, tokens

        self.assertEqual(tokens("Robes"), {"robe"})
        self.assertIn("robe", expand({"dress"}))


class ImageSearchDateWindowTests(unittest.TestCase):
    def test_date_window_is_validated_not_guessed(self):
        self.assertEqual(
            app.parse_date_window("2026-10-01", "2026-10-31"),
            ("2026-10-01", "2026-10-31"),
        )
        self.assertEqual(app.parse_date_window("", ""), (None, None))
        with self.assertRaises(ValueError):
            app.parse_date_window("2026-11-01", "2026-10-01")
        with self.assertRaises(ValueError):
            app.parse_date_window("10/01/2026", "")

    def test_multipart_request_reads_the_date_fields(self):
        boundary = "date-boundary"
        image_file = BytesIO()
        Image.new("RGB", (90, 60), "white").save(image_file, format="PNG")
        image_bytes = image_file.getvalue()
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="start_date"\r\n\r\n'
            "2026-10-01\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="end_date"\r\n\r\n'
            "2026-10-05\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="chat_ids"\r\n\r\n'
            "-1001234567890\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image"; filename="sample.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()

        parsed = app.image_search_request(
            f"multipart/form-data; boundary={boundary}", body
        )
        self.assertEqual(parsed["start_date"], "2026-10-01")
        self.assertEqual(parsed["end_date"], "2026-10-05")
        self.assertEqual(parsed["chat_ids"], [-1001234567890])
        self.assertEqual(parsed["image_hash"], image_dhash_bytes(image_bytes))

    def test_a_rejected_url_never_reaches_the_network(self):
        boundary = "url-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image_url"\r\n\r\n'
            "ftp://example.com/a.jpg\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        with self.assertRaises(ValueError):
            app.image_search_request(f"multipart/form-data; boundary={boundary}", body)

    def test_reverse_image_search_respects_the_date_window(self):
        image = Image.new("RGB", (100, 80), "white")
        ImageDraw.Draw(image).rectangle((8, 8, 70, 65), fill="black")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        query_hash = image_dhash_bytes(image_bytes)

        temporary = tempfile.TemporaryDirectory()
        original_db = app.DB_PATH
        app.DB_PATH = Path(temporary.name) / "research.sqlite3"
        try:
            with closing(sqlite3.connect(app.DB_PATH)) as connection:
                connection.execute(
                    """
                    CREATE TABLE telegram_image_index (
                        chat_id INTEGER, message_id INTEGER, chat_title TEXT,
                        product TEXT, supplier TEXT, price REAL, currency TEXT,
                        message_date TEXT, telegram_url TEXT, image_name TEXT,
                        image_hash TEXT, product_key TEXT DEFAULT ''
                    )
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO telegram_image_index (
                        chat_id, message_id, chat_title, product, supplier,
                        price, currency, message_date, telegram_url, image_name, image_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            -1001234567890, 1, "Channel A", "Old Post", "Supplier One",
                            10, "USD", "2026-09-01", "https://t.me/a/1",
                            "old.jpg", query_hash,
                        ),
                        (
                            -1001234567891, 2, "Channel B", "New Post", "Supplier Two",
                            12, "USD", "2026-10-04", "https://t.me/b/2",
                            "new.jpg", query_hash,
                        ),
                    ],
                )
                connection.commit()

            window = app.find_similar_telegram_images(
                query_hash, start_date="2026-10-01", end_date="2026-10-31"
            )
            everything = app.find_similar_telegram_images(query_hash)
        finally:
            app.DB_PATH = original_db
            temporary.cleanup()

        self.assertEqual([match["product"] for match in window], ["New Post"])
        self.assertEqual({match["product"] for match in everything}, {"Old Post", "New Post"})


class MetaAdExactnessTests(unittest.TestCase):
    """An ad for Item1002 may never be listed as an ad for Item1001."""

    @staticmethod
    def _ad(**fields):
        row = {
            "ad_id": fields.pop("ad_id", "ad-1"),
            "page_name": "Store",
            "creative_title": "",
            "creative_text": "",
            "link_caption": "",
            "link_url": "",
            "snapshot_url": "",
        }
        row.update(fields)
        return row

    def test_a_neighbouring_model_is_never_exact(self):
        profile = app.product_match_profile("Item1001")
        verdict = app.score_meta_ad(profile, self._ad(creative_text="Item1002 - free delivery"))
        self.assertEqual(verdict["level"], "similar")
        self.assertTrue(
            any("different model" in reason.lower() for reason in verdict["reasons"]),
            verdict["reasons"],
        )

    def test_the_same_model_stays_exact(self):
        profile = app.product_match_profile("Item1001")
        verdict = app.score_meta_ad(profile, self._ad(creative_text="Item1001 - free delivery"))
        self.assertEqual(verdict["level"], "exact")

    def test_a_model_in_a_link_does_not_create_a_false_conflict(self):
        profile = app.product_match_profile("Item1001")
        verdict = app.score_meta_ad(
            profile,
            self._ad(
                creative_text="Item1001 - free delivery",
                link_url="https://shop.example.com/track/1002?ref=feed",
            ),
        )
        self.assertEqual(verdict["level"], "exact")

    def test_classification_never_mixes_the_two_sections(self):
        profile = app.product_match_profile("Item1001")
        ads = [
            self._ad(ad_id="a", creative_text="Item1001 bluetooth speaker"),
            self._ad(ad_id="b", creative_text="Item1002 bluetooth speaker"),
        ]
        exact, similar = app.classify_product_ads(profile, ads)
        self.assertEqual([row["ad_id"] for row in exact], ["a"])
        self.assertEqual([row["ad_id"] for row in similar], ["b"])


class AdVisualGateTests(unittest.TestCase):
    """A creative that does not look like the product cannot be an exact ad."""

    @staticmethod
    def _ad(**fields):
        row = {
            "ad_id": fields.pop("ad_id", "ad-1"),
            "page_name": "Store",
            "creative_title": "",
            "creative_text": "",
            "link_caption": "",
            "link_url": "",
            "snapshot_url": "",
        }
        row.update(fields)
        return row

    def test_a_different_creative_demotes_a_textual_match(self):
        profile = app.product_match_profile("Item1001")
        verdict = app.score_meta_ad(
            profile,
            self._ad(creative_text="Item1001 bluetooth speaker"),
            visual_score=0.41,
        )
        self.assertEqual(verdict["level"], "similar")
        self.assertTrue(verdict["visual_checked"])
        self.assertIn("does not match this product", " ".join(verdict["reasons"]))

    def test_a_matching_creative_keeps_the_ad_exact(self):
        profile = app.product_match_profile("Item1001")
        verdict = app.score_meta_ad(
            profile,
            self._ad(creative_text="Item1001 bluetooth speaker"),
            visual_score=0.93,
        )
        self.assertEqual(verdict["level"], "exact")
        self.assertTrue(any("image matches" in reason for reason in verdict["reasons"]))

    def test_an_unchecked_creative_never_changes_the_verdict(self):
        profile = app.product_match_profile("Item1001")
        verdict = app.score_meta_ad(profile, self._ad(creative_text="Item1001 bluetooth speaker"))
        self.assertEqual(verdict["level"], "exact")
        self.assertIsNone(verdict["visual"])
        self.assertFalse(verdict["visual_checked"])

    def test_the_stored_score_on_the_row_is_used(self):
        profile = app.product_match_profile("Item1001")
        exact, similar = app.classify_product_ads(
            profile,
            [self._ad(ad_id="a", creative_text="Item1001 speaker", visual_score=0.2)],
        )
        self.assertEqual(exact, [])
        self.assertEqual([row["ad_id"] for row in similar], ["a"])

    def test_a_product_without_photos_reports_why_it_cannot_check(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "scan.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE messages (scan_id TEXT, normalized_product TEXT, image_name TEXT)"
                )
                connection.execute(
                    "CREATE TABLE meta_ads (scan_id TEXT, product_key TEXT, ad_id TEXT, image_url TEXT, thumbnail_url TEXT, visual_score REAL)"
                )
                connection.commit()
            result = app.verify_ad_visuals("scan", "key", database=database)
        self.assertEqual(result["checked"], 0)
        self.assertIn("no stored photos", result["reason"])


class IdentificationEvidenceTests(unittest.TestCase):
    def test_a_photo_name_corroborated_everywhere_is_high_agreement(self):
        product = {
            "key": "item1001",
            "product": "Item1001 bluetooth speaker",
            "description": "Item1001 bluetooth speaker, original, wholesale price",
            "identification_method": "photo",
            "identification_confidence": 0.82,
            "candidate_only": False,
            "market_research": {"trend_direction": "rising", "active_ads": 6, "notes": "seen on 2 pages"},
            "competitor_listings": [{"title": "Item1001 speaker"}, {"title": "Item1001"}],
            "directory_suppliers": [{"supplier": "Sound Shop", "score": 0.6}],
        }
        evidence = app.identification_evidence(product)
        self.assertEqual(evidence["verdict"], "High agreement")
        self.assertGreaterEqual(evidence["confidence"], 75)
        self.assertEqual(
            [layer["layer"] for layer in evidence["layers"]],
            ["photo", "post", "web", "directory"],
        )
        self.assertTrue(all(layer["ok"] for layer in evidence["layers"]))

    def test_an_unverified_title_is_flagged_for_review(self):
        product = {
            "key": "offer",
            "product": "منتج جديد",
            "description": "جملة",
            "identification_method": None,
            "identification_confidence": None,
            "candidate_only": True,
            "market_research": {},
            "competitor_listings": [],
            "directory_suppliers": [],
        }
        evidence = app.identification_evidence(product)
        self.assertEqual(evidence["verdict"], "Needs review")
        self.assertLess(evidence["confidence"], 50)
        self.assertFalse(evidence["layers"][0]["ok"])
        self.assertIn("not corroborated", evidence["next_step"])

    def test_missing_layers_are_skipped_not_scored_as_zero(self):
        product = {
            "key": "typed",
            "product": "Steel water bottle",
            "description": "Steel water bottle 500ml",
            "identification_method": "typed",
            "identification_confidence": None,
            "candidate_only": False,
            "market_research": {},
            "competitor_listings": [],
            "directory_suppliers": [],
        }
        evidence = app.identification_evidence(product)
        layers = {layer["layer"] for layer in evidence["layers"]}
        self.assertNotIn("directory", layers)
        self.assertIn("post", layers)
        self.assertGreater(evidence["confidence"], 0)


class ProductVisionTests(unittest.TestCase):
    """The multi-layer identifier, exercised without any model on disk."""

    def test_model_codes_ignore_prices_and_units(self):
        import product_vision as vision

        codes = vision.model_codes("Samsung SM-A546B 128GB - 25000 DA + 2 PCS")
        self.assertIn("SM-A546B", codes)
        self.assertNotIn("25000", codes)
        self.assertNotIn("PCS", codes)

    def test_packaging_signature_separates_pack_sizes(self):
        import product_vision as vision

        self.assertEqual(vision.packaging_signature("Cream 500ml"), frozenset({"500ml"}))
        self.assertNotEqual(
            vision.packaging_signature("2 pcs set"),
            vision.packaging_signature("1 pc"),
        )

    def test_brand_comes_from_packaging_text(self):
        import product_vision as vision

        self.assertEqual(vision.extract_brand("PHILIPS blender 1500W"), "philips")
        self.assertEqual(vision.extract_brand("no text at all"), "")

    def test_missing_layers_are_skipped_by_the_fusion(self):
        import product_vision as vision

        layers = [
            vision.Layer("visual", 1.0),
            vision.Layer("ocr", 0.0, available=False),
            vision.Layer("brand", 0.0, available=False),
        ]
        self.assertAlmostEqual(vision.fuse(layers), 1.0)
        self.assertEqual(vision.fuse([vision.Layer("ocr", 0.0, available=False)]), 0.0)

    def test_verdict_bands_are_ordered(self):
        import product_vision as vision

        self.assertEqual(vision.verdict_for(0.9), "exact")
        self.assertEqual(vision.verdict_for(0.6), "likely")
        self.assertEqual(vision.verdict_for(0.4), "possible")
        self.assertEqual(vision.verdict_for(0.1), "unknown")

    def test_conflicting_model_number_blocks_the_model_layer(self):
        import product_vision as vision

        candidate = {"name": "Samsung Galaxy A54 SM-A546B", "brand": "samsung"}
        layers = {layer.name: layer for layer in vision.compare_text(candidate, "Samsung SM-A545F 8/256")}
        self.assertEqual(layers["model"].score, 0.0)
        self.assertIn("Different model", layers["model"].detail)
        self.assertEqual(layers["brand"].score, 1.0)

    def test_matching_model_and_packaging_score_full_confidence(self):
        import product_vision as vision

        candidate = {
            "name": "Samsung Galaxy A54 SM-A546B 500ml",
            "brand": "samsung",
            "specs": "SM-A546B",
        }
        layers = {layer.name: layer for layer in vision.compare_text(candidate, "SAMSUNG SM-A546B 500ml")}
        self.assertEqual(layers["model"].score, 1.0)
        self.assertEqual(layers["packaging"].score, 1.0)

    def test_vector_index_returns_the_nearest_key(self):
        import product_vision as vision

        index = vision.VectorIndex()
        index.add(["dress", "phone", "blender"], [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]])
        hits = index.search([0.99, 0.01], limit=2)
        self.assertEqual(hits[0][0], "dress")
        self.assertGreater(hits[0][1], hits[1][1])

    def test_vector_index_skips_vectors_of_the_wrong_size(self):
        import product_vision as vision

        index = vision.VectorIndex()
        added = index.add(["good", "bad"], [[1.0, 0.0], [1.0, 0.0, 0.0]])
        self.assertEqual(added, 1)
        self.assertEqual(index.keys, ["good"])

    def test_text_only_identification_picks_the_best_candidate(self):
        import product_vision as vision

        candidates = {
            "a": {"name": "Ceramic coffee mug", "brand": "", "specs": ""},
            "b": {"name": "Philips blender 1500W", "brand": "philips", "specs": "HR2221"},
        }
        identity = vision.identify(
            None,
            candidates=candidates,
            read_ocr=False,
            ocr_text="PHILIPS HR2221 blender 1500W",
        )
        self.assertEqual(identity.product_key, "b")
        self.assertGreater(identity.confidence, vision.POSSIBLE_CONFIDENCE)
        self.assertEqual(identity.verdict, vision.verdict_for(identity.confidence))

    def test_identification_without_any_signal_is_unknown(self):
        import product_vision as vision

        identity = vision.identify(None, candidates={}, read_ocr=False, ocr_text="")
        self.assertEqual(identity.verdict, "unknown")
        self.assertEqual(identity.alternatives, [])

    def test_status_reports_installed_backends(self):
        import product_vision as vision

        status = vision.status()
        self.assertIn(status["visual_backend"], {"dinov2", "clip", "none"})
        self.assertIn(status["vector_backend"], {"faiss", "exact"})
        self.assertIn("ocr", status)


class VisionApiTests(unittest.TestCase):
    """The DB glue and request parsing behind /api/vision/identify."""

    def test_suppliers_are_split_into_three_tiers(self):
        import vision_api

        tiers = vision_api.rank_suppliers([
            {"supplier": "A", "score": 0.7},
            {"supplier": "B", "score": 0.5},
            {"supplier": "C", "score": 0.31},
        ])
        self.assertEqual([row["tier"] for row in tiers], ["exact", "high", "similar"])
        self.assertEqual(tiers[0]["tier_label"], "Exact supplier match")

    def test_supplier_tiers_are_sorted_by_confidence(self):
        import vision_api

        tiers = vision_api.rank_suppliers([
            {"supplier": "low", "score": 0.2},
            {"supplier": "high", "score": 0.9},
        ])
        self.assertEqual([row["supplier"] for row in tiers], ["high", "low"])

    def _multipart(self, parts):
        boundary = "----test"
        body = b""
        for name, value, filename in parts:
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'.encode()
            if filename:
                body += f'; filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode()
                body += value
            else:
                body += b"\r\n\r\n" + value.encode()
            body += b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        return f"multipart/form-data; boundary={boundary}", body

    def test_request_payload_reads_image_and_product_key(self):
        import vision_api

        content_type, body = self._multipart(
            [("image", b"\x89PNG fake", "photo.png"), ("product_key", "dress", None)]
        )
        payload = vision_api.request_payload(content_type, body)
        self.assertEqual(payload["image"], b"\x89PNG fake")
        self.assertEqual(payload["product_key"], "dress")

    def test_request_payload_rejects_an_empty_request(self):
        import vision_api

        content_type, body = self._multipart([("product_key", "dress", None)])
        with self.assertRaises(ValueError):
            vision_api.request_payload(content_type, body)

    def test_unreadable_bytes_are_rejected(self):
        import vision_api

        with self.assertRaises(ValueError):
            vision_api.identify_payload(b"definitely not an image")

    def test_identify_payload_reports_the_reading_without_a_scan(self):
        import io

        import vision_api

        buffer = io.BytesIO()
        Image.new("RGB", (200, 120), "white").save(buffer, format="PNG")
        payload = vision_api.identify_payload(buffer.getvalue())
        self.assertIn("status", payload)
        self.assertEqual(payload["indexed_products"], 0)
        self.assertEqual(payload["verdict"], "unknown")
        self.assertIn("elapsed_ms", payload)


class AppSettingsTests(unittest.TestCase):
    """The app settings that change how the collector behaves."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._original_path = app.APP_SETTINGS_PATH
        self._original_values = dict(app._app_settings)
        app.APP_SETTINGS_PATH = Path(self._directory.name) / "app_settings.json"
        app._app_settings.clear()
        app._app_settings.update(app.APP_SETTINGS_DEFAULTS)

        def restore():
            app.APP_SETTINGS_PATH = self._original_path
            app._app_settings.clear()
            app._app_settings.update(self._original_values)

        self.addCleanup(restore)

    def test_defaults_are_used_when_nothing_is_stored(self):
        self.assertEqual(app.load_app_settings(), app.APP_SETTINGS_DEFAULTS)

    def test_saved_settings_survive_a_reload(self):
        app.save_app_settings(
            {
                "market_country": "Algeria",
                "scan_lookback_days": 14,
                "scan_category": "Cat 1",
                "product_entity_label": "OFFER",
                "auto_meta_ads_lookup": False,
            }
        )
        reloaded = app.load_app_settings()
        self.assertEqual(reloaded["market_country"], "Algeria")
        self.assertEqual(reloaded["scan_lookback_days"], 14)
        self.assertEqual(reloaded["scan_category"], "Cat 1")
        self.assertEqual(reloaded["product_entity_label"], "OFFER")
        self.assertFalse(reloaded["auto_meta_ads_lookup"])
        self.assertTrue(reloaded["auto_web_research"])

    def test_invalid_values_are_rejected(self):
        for payload in (
            {"market_country": "Mars"},
            {"product_entity_label": "two words"},
            {"product_ner_model": ""},
            {"scan_lookback_days": 0},
            {"scan_lookback_days": 400},
            {"scan_lookback_days": "soon"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    app.save_app_settings(payload)

    def test_a_rejected_save_leaves_the_stored_settings_alone(self):
        app.save_app_settings({"scan_lookback_days": 5})
        with self.assertRaises(ValueError):
            app.save_app_settings({"scan_lookback_days": 5, "market_country": "Mars"})
        self.assertEqual(app.load_app_settings()["scan_lookback_days"], 5)
        self.assertEqual(app.load_app_settings()["market_country"], "United States")

    def test_app_setting_reads_the_current_values(self):
        app.save_app_settings({"market_country": "United Kingdom"})
        self.assertEqual(app.app_setting("market_country"), "United Kingdom")
        self.assertEqual(app.app_setting("not_a_setting", "fallback"), "fallback")


class AppSettingsEndpointTests(unittest.TestCase):
    """GET and POST /api/app-settings over real HTTP."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        import threading

        cls._server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls._server.daemon_threads = True
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        cls.base = "http://127.0.0.1:%d" % cls._server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()
        cls._thread.join(timeout=5)

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._original_path = app.APP_SETTINGS_PATH
        self._original_values = dict(app._app_settings)
        app.APP_SETTINGS_PATH = Path(self._directory.name) / "app_settings.json"
        app._app_settings.clear()
        app._app_settings.update(app.APP_SETTINGS_DEFAULTS)

        def restore():
            app.APP_SETTINGS_PATH = self._original_path
            app._app_settings.clear()
            app._app_settings.update(self._original_values)

        self.addCleanup(restore)

    def _get(self, path):
        from urllib.request import urlopen

        with urlopen(self.base + path, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def _post(self, path, payload):
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen

        request = Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_get_returns_the_settings_with_the_markets_and_categories(self):
        status, payload = self._get("/api/app-settings")
        self.assertEqual(status, 200)
        self.assertEqual(payload["settings"]["market_country"], "United States")
        self.assertIn("Algeria", payload["markets"])
        self.assertIsInstance(payload["categories"], list)

    def test_post_saves_the_settings_and_returns_them(self):
        status, payload = self._post("/api/app-settings", {"scan_lookback_days": 9})
        self.assertEqual(status, 200)
        self.assertTrue(payload["saved"])
        self.assertEqual(payload["settings"]["scan_lookback_days"], 9)

        status, payload = self._get("/api/app-settings")
        self.assertEqual(payload["settings"]["scan_lookback_days"], 9)

    def test_post_rejects_an_invalid_market(self):
        status, payload = self._post("/api/app-settings", {"market_country": "Mars"})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_post_can_reset_the_settings_to_the_defaults(self):
        self._post("/api/app-settings", {"scan_lookback_days": 30})
        status, payload = self._post("/api/app-settings", {"reset": True})
        self.assertEqual(status, 200)
        self.assertEqual(payload["settings"]["scan_lookback_days"], 2)
        self.assertEqual(payload["settings"], app.APP_SETTINGS_DEFAULTS)


class OfferNameParsingTests(unittest.TestCase):
    """An Algerian wholesale post must yield the product, not its decoration.

    These are real post shapes from the El Eulma supplier channels: the shop
    banner on the first line, the product after ``منتج``, then price and
    delivery. Naming them wrongly is what put nonsense into the Meta Ad Library
    query and returned ads for the wrong product.
    """

    SAFIR_POST = (
        "🏃 متراطيش لافار 🔊\n"
        "⭐ منتج tapis de bain et de douche antidérapant\n"
        "🤏 السعر: 630 دج\n"
        "👌 أسعار مغرية لأصحاب الكميات الكبيرة 👌\n"
        "🎖 السفير للجملة العلمة | Safir Gros 🎖\n"
        "🚚 التوصيل متوفر 69 ولاية"
    )

    def test_reads_the_product_after_the_arabic_marker(self):
        offer = app.offer_parser.parse_offer(self.SAFIR_POST)

        self.assertEqual(offer.name, "tapis de bain et de douche antidérapant")
        self.assertGreaterEqual(offer.confidence, 0.5)

    def test_never_names_a_post_after_the_shop_signature(self):
        posts = [
            self.SAFIR_POST,
            "🏃 متراطيش لافار 🔊\n⭐ منتج mini hachoir électrique\nالسعر 1150 دج",
            "🏃 متراطيش لافار 🔊\n⭐ منتج support de papier 2en1\nالسعر 1350 دج",
            "🏃 متراطيش لافار 🔊\n⭐ منتج organisateur de couverts\nالسعر 1250 دج",
        ]
        signature = app.offer_parser.signature_lines(posts)

        self.assertIn("متراطيش لافار", signature)
        offer = app.offer_parser.parse_offer(
            "🏃 متراطيش لافار 🔊\nCarcheur double batterie\nDisponible en gros",
            channel_title="MGR STOR GROS",
            signature=signature,
        )
        self.assertEqual(offer.name, "Carcheur double batterie")

    def test_a_signature_only_post_is_not_named_after_it(self):
        signature = app.offer_parser.signature_lines([
            "متراطيش لافار\nالسعر 100 دج",
            "متراطيش لافار\nالسعر 200 دج",
            "متراطيش لافار\nالسعر 300 دج",
            "متراطيش لافار\nالسعر 400 دج",
        ])

        offer = app.offer_parser.parse_offer(
            "متراطيش لافار\nالسعر 630 دج", signature=signature
        )

        self.assertFalse(offer.named)

    def test_price_and_boilerplate_posts_are_not_named(self):
        for text in (
            "85الف",
            "✅200 الف جملة",
            "Disponible",
            "Wining products Disponible",
            "Envoyez un message à Easy Shop sur WhatsApp. https://wa.me/213772521536",
            "منتج للبيع بالجملة",
        ):
            with self.subTest(text=text):
                self.assertFalse(app.offer_parser.parse_offer(text).named)

    def test_reads_a_latin_product_line_without_a_marker(self):
        offer = app.offer_parser.parse_offer(
            "Carcheur double batterie \n72 V \nDisponible en gros \n1400 pcs disponible"
        )

        self.assertEqual(offer.name, "Carcheur double batterie")

    def test_reads_a_short_arabic_product_name(self):
        offer = app.offer_parser.parse_offer("Disponible \nلابوبو")

        self.assertEqual(offer.name, "لابوبو")

    def test_keeps_the_brand_when_the_post_states_one(self):
        offer = app.offer_parser.parse_offer(
            "⭐ produit: mini hachoir électrique 250W\nmarque: ChainHunt\nprix 1150 دج"
        )

        self.assertEqual(offer.name, "mini hachoir électrique 250W")
        self.assertEqual(offer.brand, "ChainHunt")

    def test_derive_offer_title_uses_the_new_reader(self):
        title, inferred = derive_offer_title(self.SAFIR_POST)

        self.assertEqual(title, "tapis de bain et de douche antidérapant")
        self.assertTrue(inferred)
        title, inferred = derive_offer_title("Disponible")
        self.assertEqual(title, "Unidentified offer")
        self.assertTrue(inferred)


class IdentificationNameGuardTests(unittest.TestCase):
    def test_placeholder_and_page_titles_are_not_product_names(self):
        for name in (
            "Unnamed Up offers",
            "Review an Offer",
            "Read Customer Service Reviews of ...",
            "Page not found",
        ):
            with self.subTest(name=name):
                self.assertFalse(app._looks_like_concrete_product(name))

    def test_a_real_product_name_still_passes(self):
        for name in ("Wireless Earbuds", "Mini hachoir électrique", "Littmann Classic 3"):
            with self.subTest(name=name):
                self.assertTrue(app._looks_like_concrete_product(name))


class ImageMatchNamingTests(unittest.TestCase):
    """An identical supplier photo already names the product; reuse it."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp_dir.cleanup)
        self.original = app.DB_PATH
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        self.addCleanup(lambda: setattr(app, "DB_PATH", self.original))
        app.initialize_database()

    def _row(self, scan_id, message_id, product_key, image_hash, candidate, product=None):
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """
                INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text, image_hash,
                    review_candidate
                ) VALUES (?, -1001234567890, ?, ?, ?, 'Supplier', 'Chat',
                          '2026-10-01T00:00:00+00:00', '', ?, ?)
                """,
                (
                    scan_id,
                    message_id,
                    product or product_key,
                    product_key,
                    image_hash,
                    candidate,
                ),
            )
            connection.commit()

    def test_an_identical_photo_reuses_the_name_of_the_named_post(self):
        self._row("old", 1, "wireless earbuds", "0f1e2d3c4b5a6978", 0)
        self._row("new", 2, "review offer -1 2", "0f1e2d3c4b5a6978", 1)

        match = app.image_match_candidate("new", "review offer -1 2")

        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "wireless earbuds")
        self.assertGreaterEqual(match["score"], 0.85)

    def test_a_different_photo_is_not_matched(self):
        self._row("old", 1, "wireless earbuds", "0f1e2d3c4b5a6978", 0)
        self._row("new", 2, "review offer -1 2", "f0e1d2c3b4a59687", 1)

        self.assertIsNone(app.image_match_candidate("new", "review offer -1 2"))


class AlbumCaptionTests(unittest.IsolatedAsyncioTestCase):
    """A photo album carries one caption; every photo in it belongs to it."""

    async def test_photos_of_a_named_album_are_named_too(self):
        from datetime import date, datetime, timezone
        from types import SimpleNamespace

        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        originals = (app.DB_PATH, app.IMAGE_DIR, app._current_scan_id, app._chat_entities)
        app.DB_PATH = Path(temp_dir.name) / "research.sqlite3"
        app.IMAGE_DIR = Path(temp_dir.name) / "images"
        app.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        app._current_scan_id = None
        app._chat_entities = {-1001234567890: SimpleNamespace(title="Wholesale Chat")}
        app.initialize_database()

        def restore():
            app.DB_PATH, app.IMAGE_DIR = originals[0], originals[1]
            app._current_scan_id, app._chat_entities = originals[2], originals[3]
            temp_dir.cleanup()

        self.addCleanup(restore)

        async def sender():
            return SimpleNamespace(title="Supplier One", username=None, first_name=None)

        async def download(file=None, **kwargs):
            Image.new("RGB", (16, 16), "white").save(file)

        messages = [
            SimpleNamespace(
                id=1,
                date=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
                message="⭐ منتج tapis de bain antidérapant\nالسعر: 630 دج",
                views=10,
                grouped_id=777,
                get_sender=sender,
                download_media=download,
            ),
            SimpleNamespace(
                id=2,
                date=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
                message="",
                views=10,
                grouped_id=777,
                get_sender=sender,
                download_media=download,
            ),
        ]

        async def all_messages(iterator, cancel_event):
            for message in messages:
                yield message

        async def no_entities(text):
            return [], {}

        async def extractor():
            return object()

        async def client():
            return SimpleNamespace(iter_messages=lambda *args, **kwargs: [])

        with patch.object(app, "cancellable_telegram_messages", all_messages), patch.object(
            app, "extract_entities", no_entities
        ), patch.object(app, "get_product_extractor", extractor), patch.object(
            app, "require_authorized_client", client
        ), patch.object(app, "has_product_image", return_value=True), patch.object(
            app, "telegram_image_extension", return_value=".jpg"
        ), patch.object(app, "start_meta_ads_sync"), patch.object(
            app, "start_photo_identification", return_value=False
        ), patch.object(app, "start_web_research", return_value=False):
            await app.run_scan(
                [-1001234567890], date(2026, 10, 1), date(2026, 10, 3)
            )

        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            rows = connection.execute(
                "SELECT message_id, product, normalized_product, review_candidate "
                "FROM messages ORDER BY message_id"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [(row[1], row[2], row[3]) for row in rows],
            [
                ("tapis de bain antidérapant", "tapis de bain antidérapant", 0),
                ("tapis de bain antidérapant", "tapis de bain antidérapant", 0),
            ],
        )


class ArabicAdMatchingTests(unittest.TestCase):
    """Arabic products must be able to match Arabic ad copy."""

    def test_arabic_words_are_tokenized_and_folded(self):
        tokens = app._tokenize("قميص الاعتكاف")

        self.assertEqual(tokens, ["قميص", "اعتكاف"])
        # Every optional spelling of the same word folds onto one token.
        self.assertEqual(app._tokenize("إعتكاف"), app._tokenize("اعتكاف"))
        self.assertEqual(app._tokenize("طاقية"), app._tokenize("طاقيه"))

    def test_an_arabic_ad_names_the_arabic_product(self):
        profile = app.product_match_profile("قميص الاعتكاف")
        ad = {
            "creative_title": "قميص اعتكاف فاخر",
            "creative_text": "جديد قميص الإعتكاف بجودة عالية",
            "page_name": "متجر",
            "link_url": "https://example.com/p/1",
            "link_caption": "example.com",
            "snapshot_url": "",
        }

        match = app.score_meta_ad(profile, ad)

        self.assertEqual(match["level"], "exact")
        self.assertIn("Names this exact product", " ".join(match["reasons"]))

    def test_an_unrelated_arabic_ad_is_not_exact(self):
        profile = app.product_match_profile("قميص الاعتكاف")
        ad = {
            "creative_title": "حذاء رياضي",
            "creative_text": "احذية رجالية جديدة",
            "page_name": "متجر",
            "link_url": "https://example.com/p/2",
            "link_caption": "example.com",
            "snapshot_url": "",
        }

        self.assertEqual(app.score_meta_ad(profile, ad)["level"], "similar")


class VisualNamingTests(unittest.TestCase):
    """A photo-only offer is named from the visually nearest named photo."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp_dir.cleanup)
        self.original = (app.DB_PATH, app.IMAGE_DIR, app._current_scan_id)
        app.DB_PATH = Path(self.temp_dir.name) / "research.sqlite3"
        app.IMAGE_DIR = Path(self.temp_dir.name) / "images"
        app.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        app._current_scan_id = "s2"

        def restore():
            app.DB_PATH, app.IMAGE_DIR, app._current_scan_id = self.original

        self.addCleanup(restore)
        app.initialize_database()
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.execute(
                """INSERT INTO scans (
                    scan_id, start_date, end_date, market, completed_at, status,
                    extractor_version
                ) VALUES ('s2', '2026-10-01', '2026-10-03', 'Algeria',
                          '2026-10-03T12:00:00+00:00', 'completed', 3)"""
            )
            connection.commit()
        Image.new("RGB", (16, 16), "white").save(app.IMAGE_DIR / "query.jpg")
        for name in ("known-a.jpg", "known-b.jpg"):
            Image.new("RGB", (16, 16), "grey").save(app.IMAGE_DIR / name)
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.executemany(
                """
                INSERT INTO messages (
                    scan_id, chat_id, message_id, product, normalized_product,
                    supplier, chat_title, message_date, message_text, image_name,
                    review_candidate
                ) VALUES (?, -1, ?, ?, ?, 'S', 'C', '2026-10-01T00:00:00+00:00', '', ?, ?)
                """,
                [
                    ("s2", 1, "Unnamed offer", "review offer -1 1", "query.jpg", 1),
                    ("s1", 2, "Wireless Earbuds", "wireless earbuds", "known-a.jpg", 0),
                    ("s1", 3, "Steel Bottle", "steel bottle", "known-b.jpg", 0),
                ],
            )
            connection.commit()

    def _vectors(self, mapping):
        from array import array

        def fake(paths, compute=True):
            return {
                Path(path).name: array("f", mapping[Path(path).name])
                for path in paths
                if Path(path).name in mapping
            }

        return fake

    def test_a_close_visual_match_names_the_offer(self):
        vectors = self._vectors(
            {"query.jpg": [1.0, 0.0], "known-a.jpg": [0.99, 0.14], "known-b.jpg": [0.2, 0.98]}
        )
        with patch.object(app, "_photo_vectors", side_effect=vectors):
            match = app.visual_name_candidate("s2", "review offer -1 1")

        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "Wireless Earbuds")

    def test_a_same_category_lookalike_is_refused(self):
        vectors = self._vectors(
            {
                "query.jpg": [1.0, 0.0],
                "known-a.jpg": [0.80, 0.60],
                "known-b.jpg": [0.85, 0.53],
            }
        )
        with patch.object(app, "_photo_vectors", side_effect=vectors):
            self.assertIsNone(app.visual_name_candidate("s2", "review offer -1 1"))

    def test_two_equally_close_products_name_nothing(self):
        vectors = self._vectors(
            {"query.jpg": [1.0, 0.0], "known-a.jpg": [0.99, 0.10], "known-b.jpg": [0.99, 0.11]}
        )
        with patch.object(app, "_photo_vectors", side_effect=vectors):
            self.assertIsNone(app.visual_name_candidate("s2", "review offer -1 1"))

    def test_the_identification_saves_the_visual_name(self):
        vectors = self._vectors(
            {"query.jpg": [1.0, 0.0], "known-a.jpg": [0.99, 0.14], "known-b.jpg": [0.2, 0.98]}
        )
        with patch.object(app, "_photo_vectors", side_effect=vectors), patch.object(
            app, "identify_product_from_web", side_effect=ValueError("disabled")
        ):
            result = app.identify_product_progressive("s2", "review offer -1 1")

        self.assertTrue(result["saved"])
        self.assertEqual(result["key"], "wireless earbuds")
        product = app.load_results()[0]
        self.assertFalse(product["candidate_only"])
        self.assertTrue(product["identified_from_photo"])


class MetaQueryTests(unittest.TestCase):
    def test_widening_keeps_narrowing_the_query(self):
        attempts = app.meta_query_widening("عطر هيرش لهب")

        self.assertEqual(attempts[0], "عطر هيرش لهب")
        self.assertEqual(attempts[1], "عطر هيرش")
        self.assertEqual(attempts[2], "هيرش")
        # A repeated step is dropped instead of being searched twice.
        self.assertEqual(len(attempts), len(set(attempts)))

    def test_a_single_word_query_is_not_searched_four_times(self):
        self.assertEqual(app.meta_query_widening("parka"), ["parka"])

    def test_filler_words_are_dropped_from_the_query(self):
        query = app.meta_query_for_product(
            {"product": "متوفر عطر attacks من شركة assaf"}
        )

        self.assertNotIn("من", query.split())
        self.assertNotIn("شركة", query.split())
        self.assertIn("attacks", query)


    def test_query_is_short_and_starts_with_the_brand(self):
        query = app.meta_query_for_product(
            {
                "product": (
                    "Tapis de bain antidérapant grande taille ultra doux "
                    "premium quality 60x40 cm"
                ),
                "brand": "Safir",
                "specs": [{"brand": "Safir", "value": "60x40", "unit": "cm"}],
            }
        )

        self.assertTrue(query.startswith("Safir "))
        # The brand plus the first words of the name, with the long tail dropped:
        # a long keyword query is what made the Ad Library look empty.
        self.assertLessEqual(len(query.split()), app.META_QUERY_MAX_WORDS + 1)
        self.assertNotIn("quality", query)
        self.assertNotIn("specs", query)

    def test_query_does_not_repeat_the_brand_already_in_the_name(self):
        query = app.meta_query_for_product(
            {"product": "Safir tapis de bain", "brand": "Safir"}
        )

        self.assertEqual(query, "Safir tapis de bain")


class SupplierGuideSourceImportTests(unittest.TestCase):
    """The guide's suppliers become scannable channels, not just a contact list."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp_dir.cleanup)
        self.database = Path(self.temp_dir.name) / "research.sqlite3"
        self.directory = Path(self.temp_dir.name) / "telegram_research.db"
        self.originals = (app.DB_PATH, app.SUPPLIER_DIRECTORY_DB, app._chat_entities)
        app.DB_PATH = self.database
        app.SUPPLIER_DIRECTORY_DB = self.directory
        app._chat_entities = {}

        def restore():
            app.DB_PATH, app.SUPPLIER_DIRECTORY_DB, app._chat_entities = self.originals

        self.addCleanup(restore)
        app.initialize_database()
        with closing(sqlite3.connect(self.directory)) as connection:
            connection.execute(
                """
                CREATE TABLE imported_suppliers (
                    id INTEGER PRIMARY KEY, category_sheet TEXT, supplier_name TEXT,
                    category TEXT, phone TEXT, whatsapp TEXT, telegram TEXT,
                    facebook TEXT, delivery TEXT
                )
                """
            )
            connection.executemany(
                "INSERT INTO imported_suppliers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (1, "sport", "Nine sport", "Article de sports", "782595159", "//",
                     "https://t.me/eurlvistasport19", "//", "disponible"),
                    (2, "sport", "Mens wears", "ملابس", "550111222", "//",
                     "https://t.me/bfmenswear", "//", "disponible"),
                    (3, "TEXTILE", "ADEN Tex 16", "TEXTILE", "550999888", "//",
                     "https://www.instagram.com/aden.tex", "//", "//"),
                    (4, "sport", "Nine sport again", "Article de sports", "782595159", "//",
                     "https://t.me/eurlvistasport19", "//", "disponible"),
                ],
            )
            connection.commit()

    def test_imports_only_the_telegram_channels_and_keeps_the_categories(self):
        summary = app.import_supplier_channels()

        self.assertEqual(summary["sources_added"], 2)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT title, username, telegram_url, status FROM sources ORDER BY title"
                )
            ]
            categories = [
                row[0] for row in connection.execute("SELECT name FROM source_categories")
            ]
        self.assertEqual(
            [(row["title"], row["username"]) for row in rows],
            [("Mens wears", "bfmenswear"), ("Nine sport", "eurlvistasport19")],
        )
        self.assertTrue(all(row["status"] == "active" for row in rows))
        self.assertEqual(sorted(categories), ["sport"])

    def test_a_second_import_updates_instead_of_duplicating(self):
        app.import_supplier_channels()
        summary = app.import_supplier_channels()

        self.assertEqual(summary["sources_added"], 0)
        self.assertEqual(summary["sources_updated"], 2)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 2
            )

    def test_sample_channels_are_replaced_by_the_real_suppliers(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO sources (source_key, title, status, notes, imported_at) "
                "VALUES ('title:sample', 'Supplier 1 - Category_1_AlEulma', 'active', "
                "'Sample data', '2026-10-01T00:00:00+00:00')"
            )
            connection.commit()

        app.import_supplier_channels()

        with closing(sqlite3.connect(self.database)) as connection:
            titles = [
                row[0] for row in connection.execute("SELECT title FROM sources")
            ]
        self.assertNotIn("Supplier 1 - Category_1_AlEulma", titles)
        self.assertEqual(len(titles), 2)


if __name__ == "__main__":
    unittest.main()
