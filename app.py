import asyncio
import base64
import csv
import ctypes
import sys
from array import array
from ctypes import wintypes
from contextlib import closing
from email import policy
from email.parser import BytesParser
import hashlib
import http.client
import ipaddress
import io
import json
import math
import mimetypes
import os
import queue
import re
import socket
import sqlite3
import threading
import time
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse
from urllib.request import (
    HTTPDefaultErrorHandler,
    HTTPErrorProcessor,
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    UnknownHandler,
    urlopen,
)

if os.name == "nt":
    import winreg

# Avoid stalled Xet transfers in environments where the Hugging Face Xet
# transport cannot stream model files; ordinary Hub downloads remain available.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from telethon import TelegramClient, utils
from telethon.errors import FloodWaitError, RPCError, SessionPasswordNeededError
from transformers import pipeline

# meta-ads-collector is the optional dependency behind Meta ad lookup. It is
# imported defensively so the rest of the app still starts (and the test suite
# still runs) on a machine where it has not been installed.
try:
    from meta_ads_collector import MetaAdsError
    from meta_ads_collector import MetaAdsCollector
except ImportError:  # pragma: no cover - exercised only without the package
    MetaAdsCollector = None

    class MetaAdsError(Exception):
        """Stand-in used when meta-ads-collector is not installed."""
from PIL import Image, ImageOps, UnidentifiedImageError


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
IMAGE_DIR = DATA_DIR / "images"
MODEL_DIR = DATA_DIR / "models" / "Products_NER2"
DB_PATH = DATA_DIR / "research.sqlite3"
CREDENTIALS_PATH = Path(
    os.environ.get(
        "APPDATA",
        Path.home() / "AppData" / "Roaming",
    )
) / "WholesaleProductResearch" / "credentials.dpapi"
HOST = "127.0.0.1"
PORT = 8765
MODEL_DEFAULT = "Atheer174/Products_NER2"
MODEL_WEIGHT_SIZE = 265529381
LABEL_DEFAULT = "PRODUCT"

DATA_DIR.mkdir(exist_ok=True)
IMAGE_DIR.mkdir(exist_ok=True)

PRICE_RE = re.compile(
    r"(?:(?P<currency1>US\$|USD|DZD|DA|د\.ج|دج|CNY|RMB|EUR|GBP|CAD|AUD|[$€£¥])\s*"
    r"(?P<amount1>(?:\d{1,3}(?:[ \u00a0,]\d{3})+|\d+)(?:[.,]\d{1,2})?)|"
    r"(?P<amount2>(?:\d{1,3}(?:[ \u00a0,]\d{3})+|\d+)(?:[.,]\d{1,2})?)\s*"
    r"(?P<currency2>USD|DZD|DA|د\.ج|دج|CNY|RMB|EUR|GBP|CAD|AUD|元|[$€£¥]))",
    re.IGNORECASE,
)
MOQ_RE = re.compile(
    r"(?:MOQ|minimum\s+order(?:\s+quantity)?|min(?:imum)?\s+commande|"
    r"quantit[eé]\s+min(?:imum)?|qt[eé]\s+min)\s*[:：]?\s*"
    r"(?P<quantity>\d+(?:[.,]\d+)?)\s*(?P<unit>pcs?|pieces?|units?|sets?|箱|件)?",
    re.IGNORECASE,
)
PRODUCT_NAME_RE = re.compile(
    r"[^\W_]+(?:[ '\-/&][^\W_]+){0,5}\Z",
    re.UNICODE,
)
PRODUCT_NAME_STOPWORDS = {
    "available",
    "arrivage",
    "com",
    "electronics",
    "gros",
    "tension",
    "tensions",
    "product",
    "products",
    "supplier",
    "wholesale",
    "wholesal",
}
SIZE_RE = re.compile(
    r"(?:size|taille|尺寸)\s*[:：]?\s*(?P<size>"
    r"\d+(?:[.,]\d+)?\s*(?:cm|mm|m|inch|inches|in)?"
    r"(?:\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:cm|mm|m|inch|inches|in)?){0,2}"
    r"(?:\s*,?\s*(?:XS|S|M|L|XL|XXL|XXXL))?)",
    re.IGNORECASE,
)
DIMENSION_RE = re.compile(
    r"(?P<size>\d+(?:[.,]\d+)?\s*(?:cm|mm|m|inch|inches|in)"
    r"(?:\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:cm|mm|m|inch|inches|in)){1,2})",
    re.IGNORECASE,
)
PRICE_UNIT_RE = re.compile(
    r"\s*(?:/|per\s+)(?P<unit>pcs?|pieces?|units?|sets?|pairs?|kg|g|boxes?|packs?)\b",
    re.IGNORECASE,
)
WEIGHT_RE = re.compile(
    r"(?:(?:weight|poids|وزن)\s*[:：]?\s*)?(?P<value>\d+(?:[.,]\d+)?\s*"
    r"(?:kg|kgs|g|gr|grammes?|mg|lb|lbs|oz))",
    re.IGNORECASE,
)
PRODUCT_ATTRIBUTE_PATTERNS = {
    "material": re.compile(
        r"(?:material|mati[eè]re|mat[eé]riau|الخامة|المادة)\s*[:：]?\s*([^\n,;]{1,80})",
        re.IGNORECASE,
    ),
    "color": re.compile(
        r"(?:colou?r|couleur|اللون)\s*[:：]?\s*([^\n,;]{1,60})",
        re.IGNORECASE,
    ),
    "brand": re.compile(
        r"(?:brand|marque|العلامة)\s*[:：]?\s*([^\n,;]{1,60})",
        re.IGNORECASE,
    ),
    "model_number": re.compile(
        r"(?:model|model\s*(?:no|number)|mod[eè]le|موديل)\s*[:：#]?\s*([A-Z0-9_-]{2,40})",
        re.IGNORECASE,
    ),
    "package_quantity": re.compile(
        r"(?:pack(?:age)?\s*(?:of|contains?)|lot de|colis de|"
        r"عبوة\s*من)\s*(\d+\s*(?:pcs?|pieces?|units?|sets?|pairs?))",
        re.IGNORECASE,
    ),
    "volume": re.compile(
        r"(?:volume|contenance|السعة)\s*[:：]?\s*(\d+(?:[.,]\d+)?\s*(?:ml|cl|l|litres?|liters?))",
        re.IGNORECASE,
    ),
    "voltage": re.compile(
        r"(?:voltage|tension|الجهد)\s*[:：]?\s*(\d+(?:[.,]\d+)?\s*v(?:olts?)?)",
        re.IGNORECASE,
    ),
    "warranty": re.compile(
        r"(?:warranty|garantie|ضمان)\s*[:：]?\s*(\d+\s*(?:months?|mois|years?|ans?))",
        re.IGNORECASE,
    ),
    "origin_country": re.compile(
        r"(?:made\s+in|origin|origine|بلد\s*الصنع)\s*[:：]?\s*([^\n,;]{2,50})",
        re.IGNORECASE,
    ),
    "sku": re.compile(
        r"(?:SKU|reference|r[eé]f(?:erence)?|كود)\s*[:：#]?\s*([A-Z0-9_-]{2,40})",
        re.IGNORECASE,
    ),
}
MAX_LISTING_BYTES = 2 * 1024 * 1024
MAX_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024

_state_lock = threading.Lock()
_state = {
    "running": False,
    "message": "Ready.",
    "error": None,
    "needs_login": False,
    "login_stage": "phone",
    "scanned_messages": 0,
    "products_found": 0,
    "products_detected": 0,
    "chat_index": 0,
    "chat_total": 0,
    "current_chat": "",
    "cancel_requested": False,
    "job_kind": None,
}
_chat_entities = {}
_client = None
_extractor = None
_extractor_load_task = None
_current_scan_id = None
_current_market = "United States"
_latest_scan_info = None
_login_state = {"phone": None, "phone_code_hash": None}
_scan_cancel = threading.Event()
_image_index_cancel = threading.Event()
_meta_ads_cancel = threading.Event()
_photo_identify_cancel = threading.Event()
_active_job_future = None
_active_scan_id = None
_meta_ads_lock = threading.Lock()
_meta_ads_state = {
    "running": False,
    "scan_id": None,
    "checked": 0,
    "total": 0,
    "message": "Not started",
}
_photo_identify_lock = threading.Lock()
_photo_identify_state = {
    "running": False,
    "scan_id": None,
    "checked": 0,
    "total": 0,
    "identified": 0,
    "unreadable": 0,
    "message": "Not started",
}
_image_index_lock = threading.Lock()
_image_index_state = {
    "status": "idle",
    "indexed_photos": 0,
    "total_photos": 0,
    "indexed_chats": 0,
    "total_chats": 0,
    "message": "Image index has not been built yet.",
    "error": "",
}
_credentials_lock = threading.RLock()
_credentials_cache = None


class TelegramSignInRequired(Exception):
    pass


async def cancellable_telegram_messages(iterator, cancel_event):
    iterator = aiter(iterator)
    while not cancel_event.is_set():
        next_message = asyncio.create_task(anext(iterator))
        while not next_message.done() and not cancel_event.is_set():
            await asyncio.wait((next_message,), timeout=0.2)
        if cancel_event.is_set():
            next_message.cancel()
            try:
                await next_message
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            return
        try:
            yield next_message.result()
        except StopAsyncIteration:
            return


async def await_cancellable(awaitable, cancel_event):
    task = asyncio.create_task(awaitable)
    while not task.done() and not cancel_event.is_set():
        await asyncio.wait((task,), timeout=0.2)
    if cancel_event.is_set() and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return False, None
    return True, task.result()


async def cancellable_delay(cancel_event, seconds):
    deadline = asyncio.get_running_loop().time() + seconds
    while not cancel_event.is_set():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return True
        await asyncio.sleep(min(0.2, remaining))
    return False


def initialize_database():
    global _current_scan_id, _current_market, _latest_scan_info
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                scan_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                product TEXT NOT NULL,
                normalized_product TEXT NOT NULL,
                supplier TEXT NOT NULL,
                chat_title TEXT NOT NULL,
                message_date TEXT NOT NULL,
                message_text TEXT NOT NULL,
                price REAL,
                currency TEXT,
                price_unit TEXT,
                moq TEXT,
                size TEXT,
                views INTEGER,
                forwards INTEGER,
                image_name TEXT,
                attributes_json TEXT NOT NULL DEFAULT '{}',
                review_candidate INTEGER NOT NULL DEFAULT 0,
                description_text TEXT NOT NULL DEFAULT '',
                telegram_url TEXT,
                reactions INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                image_hash TEXT,
                UNIQUE(scan_id, chat_id, message_id, normalized_product)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS meta_ad_lookups (
                scan_id TEXT NOT NULL,
                product_key TEXT NOT NULL,
                status TEXT NOT NULL,
                ad_count INTEGER NOT NULL DEFAULT 0,
                error_text TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL,
                PRIMARY KEY(scan_id, product_key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_image_index (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                chat_title TEXT NOT NULL DEFAULT '',
                product TEXT NOT NULL DEFAULT '',
                product_key TEXT NOT NULL DEFAULT '',
                supplier TEXT NOT NULL DEFAULT '',
                price REAL,
                currency TEXT,
                message_date TEXT NOT NULL DEFAULT '',
                telegram_url TEXT,
                image_name TEXT NOT NULL,
                image_hash TEXT NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            )
            """
        )
        image_index_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(telegram_image_index)"
            ).fetchall()
        }
        if "product_key" not in image_index_columns:
            connection.execute(
                "ALTER TABLE telegram_image_index ADD COLUMN product_key TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS telegram_image_index_hash
            ON telegram_image_index(image_hash)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS photo_embeddings (
                image_name TEXT PRIMARY KEY,
                model_version TEXT NOT NULL,
                embedding BLOB NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS product_details (
                scan_id TEXT NOT NULL,
                product_key TEXT NOT NULL,
                source TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                brand TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                specs_json TEXT NOT NULL DEFAULT '[]',
                sources_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (scan_id, product_key)
            )
            """
        )
        detail_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(product_details)").fetchall()
        }
        if "sources_json" not in detail_columns:
            connection.execute(
                "ALTER TABLE product_details ADD COLUMN sources_json TEXT NOT NULL DEFAULT '[]'"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_image_index_state (
                chat_id INTEGER PRIMARY KEY,
                cutoff_date TEXT NOT NULL,
                last_message_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                match_hash TEXT NOT NULL DEFAULT '',
                indexed_images INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        image_state_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(telegram_image_index_state)"
            ).fetchall()
        }
        if "match_hash" not in image_state_columns:
            connection.execute(
                "ALTER TABLE telegram_image_index_state ADD COLUMN match_hash TEXT NOT NULL DEFAULT ''"
            )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "attributes_json" not in columns:
            connection.execute(
                "ALTER TABLE messages ADD COLUMN attributes_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "price_unit" not in columns:
            connection.execute("ALTER TABLE messages ADD COLUMN price_unit TEXT")
        if "review_candidate" not in columns:
            connection.execute(
                "ALTER TABLE messages ADD COLUMN review_candidate INTEGER NOT NULL DEFAULT 0"
            )
        if "description_text" not in columns:
            connection.execute(
                "ALTER TABLE messages ADD COLUMN description_text TEXT NOT NULL DEFAULT ''"
            )
        if "telegram_url" not in columns:
            connection.execute("ALTER TABLE messages ADD COLUMN telegram_url TEXT")
        for name, declaration in (
            ("reactions", "INTEGER NOT NULL DEFAULT 0"),
            ("comments", "INTEGER NOT NULL DEFAULT 0"),
            ("image_hash", "TEXT"),
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE messages ADD COLUMN {name} {declaration}")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
                scan_id TEXT PRIMARY KEY,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                market TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                chat_count INTEGER NOT NULL DEFAULT 0,
                scanned_messages INTEGER NOT NULL DEFAULT 0,
                products_found INTEGER NOT NULL DEFAULT 0,
                chat_titles_json TEXT NOT NULL DEFAULT '[]',
                extractor_version INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        scan_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(scans)").fetchall()
        }
        for name, declaration in (
            ("status", "TEXT NOT NULL DEFAULT 'completed'"),
            ("chat_count", "INTEGER NOT NULL DEFAULT 0"),
            ("scanned_messages", "INTEGER NOT NULL DEFAULT 0"),
            ("products_found", "INTEGER NOT NULL DEFAULT 0"),
            ("chat_titles_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("chat_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("name", "TEXT NOT NULL DEFAULT ''"),
            ("extractor_version", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in scan_columns:
                connection.execute(f"ALTER TABLE scans ADD COLUMN {name} {declaration}")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS product_identifications (
                scan_id TEXT NOT NULL,
                product_key TEXT NOT NULL,
                previous_key TEXT NOT NULL DEFAULT '',
                method TEXT NOT NULL DEFAULT 'typed',
                confidence REAL,
                alternatives_json TEXT NOT NULL DEFAULT '[]',
                specs_json TEXT NOT NULL DEFAULT '[]',
                identified_at TEXT NOT NULL,
                PRIMARY KEY(scan_id, product_key)
            )
            """
        )
        identification_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(product_identifications)").fetchall()
        }
        if "specs_json" not in identification_columns:
            connection.execute(
                "ALTER TABLE product_identifications ADD COLUMN specs_json TEXT NOT NULL DEFAULT '[]'"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS research_evidence (
                scan_id TEXT NOT NULL,
                product_key TEXT NOT NULL,
                trend_direction TEXT NOT NULL DEFAULT 'unknown',
                active_ads INTEGER,
                oldest_ad_days INTEGER,
                social_views INTEGER,
                social_interactions INTEGER,
                social_source_url TEXT,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(scan_id, product_key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS competitor_listings (
                listing_id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                product_key TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                price REAL,
                currency TEXT,
                description TEXT,
                image_url TEXT,
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL,
                error_text TEXT NOT NULL DEFAULT '',
                UNIQUE(scan_id, product_key, url)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS engagement_alerts (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                product_key TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                threshold INTEGER NOT NULL,
                observed INTEGER NOT NULL,
                message_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(scan_id, chat_id, message_id, alert_type, threshold)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS meta_ads (
                scan_id TEXT NOT NULL,
                product_key TEXT NOT NULL,
                ad_id TEXT NOT NULL,
                page_name TEXT NOT NULL DEFAULT '',
                creative_text TEXT NOT NULL DEFAULT '',
                creative_title TEXT NOT NULL DEFAULT '',
                delivery_start TEXT NOT NULL DEFAULT '',
                delivery_stop TEXT NOT NULL DEFAULT '',
                platforms_json TEXT NOT NULL DEFAULT '[]',
                snapshot_url TEXT NOT NULL DEFAULT '',
                link_caption TEXT NOT NULL DEFAULT '',
                link_url TEXT NOT NULL DEFAULT '',
                cta_text TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                video_url TEXT NOT NULL DEFAULT '',
                thumbnail_url TEXT NOT NULL DEFAULT '',
                languages TEXT NOT NULL DEFAULT '',
                display_format TEXT NOT NULL DEFAULT '',
                ad_status TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT '',
                spend TEXT NOT NULL DEFAULT '',
                impressions TEXT NOT NULL DEFAULT '',
                reach TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL,
                PRIMARY KEY(scan_id, product_key, ad_id)
            )
            """
        )
        meta_ad_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(meta_ads)").fetchall()
        }
        if "link_caption" not in meta_ad_columns:
            connection.execute(
                "ALTER TABLE meta_ads ADD COLUMN link_caption TEXT NOT NULL DEFAULT ''"
            )
        # Fields the meta-ads-collector scraper supplies that the old Ad
        # Library API could not. Added one at a time so an existing database
        # upgrades in place instead of needing to be rebuilt.
        for column, declaration in (
            ("link_url", "TEXT NOT NULL DEFAULT ''"),
            ("cta_text", "TEXT NOT NULL DEFAULT ''"),
            ("image_url", "TEXT NOT NULL DEFAULT ''"),
            ("video_url", "TEXT NOT NULL DEFAULT ''"),
            ("thumbnail_url", "TEXT NOT NULL DEFAULT ''"),
            ("languages", "TEXT NOT NULL DEFAULT ''"),
            ("display_format", "TEXT NOT NULL DEFAULT ''"),
            ("ad_status", "TEXT NOT NULL DEFAULT ''"),
            ("is_active", "INTEGER NOT NULL DEFAULT 0"),
            ("currency", "TEXT NOT NULL DEFAULT ''"),
            ("spend", "TEXT NOT NULL DEFAULT ''"),
            ("impressions", "TEXT NOT NULL DEFAULT ''"),
            ("reach", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in meta_ad_columns:
                connection.execute(
                    f"ALTER TABLE meta_ads ADD COLUMN {column} {declaration}"
                )
        connection.execute(
            """
            UPDATE scans SET status = 'cancelled', completed_at = ?
            WHERE status = 'running'
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        latest = connection.execute(
            "SELECT scan_id, market, extractor_version, status, start_date, end_date, "
            "chat_count, scanned_messages, products_found FROM scans WHERE status != 'running' "
            "ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        if latest:
            (
                scan_id,
                _current_market,
                extractor_version,
                scan_status,
                start_date,
                end_date,
                chat_count,
                scanned_messages,
                products_found,
            ) = latest
            _latest_scan_info = {
                "scan_id": scan_id,
                "market": _current_market,
                "extractor_version": extractor_version,
                "status": scan_status,
                "start_date": start_date,
                "end_date": end_date,
                "chat_count": chat_count,
                "scanned_messages": scanned_messages,
                "products_found": products_found,
            }
            if extractor_version < 2:
                _current_scan_id = None
            else:
                _current_scan_id = scan_id
        connection.commit()


def parse_trade_details(text):
    price_match = PRICE_RE.search(text)
    price = None
    currency = None
    price_unit = None
    if price_match:
        amount = price_match.group("amount1") or price_match.group("amount2")
        currency = price_match.group("currency1") or price_match.group("currency2")
        price = parse_numeric_amount(amount)
        currency = currency.upper()
        currency = {
            "$": "USD",
            "€": "EUR",
            "£": "GBP",
            "¥": "CNY",
            "元": "CNY",
            "DA": "DZD",
            "دج": "DZD",
            "د.ج": "DZD",
        }.get(currency, currency)
        unit_match = PRICE_UNIT_RE.match(text[price_match.end():])
        if unit_match:
            price_unit = unit_match.group("unit").casefold()

    moq_match = MOQ_RE.search(text)
    moq = None
    if moq_match:
        quantity = moq_match.group("quantity")
        unit = moq_match.group("unit") or "units"
        moq = f"{quantity} {unit}"

    size_match = SIZE_RE.search(text) or DIMENSION_RE.search(text)
    size = size_match.group("size").strip() if size_match else None
    return {
        "price": price,
        "currency": currency,
        "price_unit": price_unit,
        "moq": moq,
        "size": size,
    }


def parse_numeric_amount(amount):
    value = re.sub(r"[ \u00a0]", "", str(amount))
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        whole, separator, fraction = value.rpartition(",")
        value = whole + fraction if len(fraction) == 3 else whole + "." + fraction
    elif value.count(".") > 1:
        whole, separator, fraction = value.rpartition(".")
        value = whole.replace(".", "") + fraction if len(fraction) == 3 else value
    return float(value)


class ListingHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metadata = {}
        self.page_title = []
        self.json_ld = []
        self._in_title = False
        self._in_json_ld = False

    def handle_starttag(self, tag, attrs):
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "meta":
            key = (
                attributes.get("property")
                or attributes.get("name")
                or attributes.get("itemprop")
            ).casefold()
            if key and attributes.get("content"):
                self.metadata[key] = attributes["content"].strip()
        elif tag.casefold() == "title":
            self._in_title = True
        elif tag.casefold() == "script" and attributes.get("type", "").casefold() == "application/ld+json":
            self._in_json_ld = True

    def handle_endtag(self, tag):
        if tag.casefold() == "title":
            self._in_title = False
        elif tag.casefold() == "script":
            self._in_json_ld = False

    def handle_data(self, data):
        if self._in_title:
            self.page_title.append(data)
        if self._in_json_ld:
            self.json_ld.append(data)


def product_attributes_from_text(text, attributes=None):
    result = {key: list(values) for key, values in (attributes or {}).items()}
    weight_match = WEIGHT_RE.search(text)
    if weight_match:
        result.setdefault("weight", []).append(weight_match.group("value").strip())
    for key, pattern in PRODUCT_ATTRIBUTE_PATTERNS.items():
        match = pattern.search(text)
        if match:
            value = match.group(1).strip(" \t\r\n-–—")
            if value:
                result.setdefault(key, []).append(value)
    return {
        key: list(dict.fromkeys(value for value in values if value))
        for key, values in result.items()
    }


def derive_offer_title(text, extracted_title=None):
    if extracted_title and extracted_title != "Review offer (product name not identified)":
        return extracted_title, False
    for line in text.splitlines():
        candidate = re.sub(r"https?://\S+|www\.\S+", "", line).strip(" \t-–—|")
        if not candidate or len(candidate) > 100:
            continue
        if re.search(r"(?:wholesale|available|arrivage|prix|price|moq|minimum|"
                     r"السعر|الجملة|متوفر)", candidate, re.IGNORECASE):
            continue
        if PRICE_RE.search(candidate) or WEIGHT_RE.search(candidate):
            continue
        if any(character.isalpha() for character in candidate):
            return candidate, True
    return "Unidentified offer", True


def separate_description(title, text):
    description = text.strip()
    if title and title != "Unidentified offer":
        description = re.sub(re.escape(title), "", description, count=1, flags=re.IGNORECASE)
    return re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", description).strip(" \t\r\n-–—|")


def optional_nonnegative_int(value, label):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number or left blank.")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a whole number or left blank.") from error
    if number < 0 or number > 1_000_000_000:
        raise ValueError(f"{label} must be between 0 and 1,000,000,000.")
    return number


def telegram_message_url(chat_id, username, message_id):
    if username:
        return f"https://t.me/{username}/{message_id}"
    if chat_id <= -1_000_000_000_000:
        return f"https://t.me/c/{abs(chat_id) - 1_000_000_000_000}/{message_id}"
    return None


def resolve_public_ip(hostname):
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            ]
        except OSError as error:
            raise ValueError(f"Could not resolve competitor website: {error}") from error
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Competitor URLs must resolve only to public internet addresses.")
    return str(addresses[0])


def validate_public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a public http:// or https:// competitor product URL.")
    if parsed.username or parsed.password:
        raise ValueError("Competitor URLs cannot contain a username or password.")
    resolve_public_ip(parsed.hostname.rstrip("."))
    return url


class PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        address = resolve_public_ip(self.host)
        self.sock = socket.create_connection(
            (address, self.port), self.timeout, self.source_address
        )


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        address = resolve_public_ip(self.host)
        raw_socket = socket.create_connection(
            (address, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


class PublicHTTPHandler(HTTPHandler):
    def http_open(self, request):
        return self.do_open(PublicHTTPConnection, request)


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(
            PublicHTTPSConnection,
            request,
            context=self._context,
            check_hostname=self._check_hostname,
        )


class PublicOnlyRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        validate_public_url(urljoin(request.full_url, new_url))
        return super().redirect_request(
            request, response, code, message, headers, new_url
        )


def extract_json_ld_product(value):
    if isinstance(value, list):
        for entry in value:
            result = extract_json_ld_product(entry)
            if result:
                return result
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if any(str(item).casefold() == "product" for item in types):
            offers = value.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict):
                return {
                    "title": value.get("name"),
                    "description": value.get("description"),
                    "image": value.get("image"),
                    "price": offers.get("price") or offers.get("lowPrice"),
                    "currency": offers.get("priceCurrency"),
                }
        for child in value.values():
            result = extract_json_ld_product(child)
            if result:
                return result
    return None


def parse_listing_html(document):
    parser = ListingHTMLParser()
    parser.feed(document)
    meta = parser.metadata
    listing = {
        "title": meta.get("og:title") or meta.get("twitter:title") or "".join(parser.page_title).strip(),
        "description": meta.get("og:description") or meta.get("description"),
        "image": meta.get("og:image") or meta.get("twitter:image"),
        "price": meta.get("product:price:amount") or meta.get("og:price:amount") or meta.get("price"),
        "currency": meta.get("product:price:currency") or meta.get("og:price:currency") or meta.get("pricecurrency"),
    }
    for script in parser.json_ld:
        try:
            product = extract_json_ld_product(json.loads(script))
        except (ValueError, TypeError):
            continue
        if product:
            for key, value in product.items():
                if value and not listing.get(key):
                    listing[key] = value
            break
    try:
        listing["price"] = parse_numeric_amount(listing["price"]) if listing.get("price") is not None else None
    except ValueError:
        listing["price"] = None
    if listing.get("currency"):
        listing["currency"] = str(listing["currency"]).upper()
    return listing


def fetch_competitor_listing(url):
    validate_public_url(url)
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; WholesaleResearch/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    opener = OpenerDirector()
    for handler in (
        ProxyHandler({}),
        PublicHTTPHandler(),
        PublicHTTPSHandler(),
        PublicOnlyRedirectHandler(),
        HTTPDefaultErrorHandler(),
        HTTPErrorProcessor(),
        UnknownHandler(),
    ):
        opener.add_handler(handler)
    with opener.open(request, timeout=8) as response:
        content_type = response.headers.get_content_type()
        if content_type not in ("text/html", "application/xhtml+xml"):
            raise ValueError("That URL did not return a product web page.")
        document = response.read(MAX_LISTING_BYTES + 1)
        if len(document) > MAX_LISTING_BYTES:
            raise ValueError("The competitor product page is too large to inspect safely.")
        charset = response.headers.get_content_charset() or "utf-8"
        final_url = response.geturl()
    validate_public_url(final_url)
    listing = parse_listing_html(document.decode(charset, errors="replace"))
    listing["image"] = urljoin(final_url, listing["image"]) if listing.get("image") else None
    listing["url"] = final_url
    if not listing["title"] and listing["price"] is None:
        raise ValueError(
            "The page did not expose product title or price metadata. It may block automatic inspection."
        )
    return listing


def normalize_product(name):
    return " ".join(re.findall(r"[^\W_]+", name.casefold(), flags=re.UNICODE))


def normalize_supplier_name(name):
    cleaned = re.sub(r"^@", "", (name or "").strip()).casefold()
    cleaned = re.sub(r"\b(?:official|store|shop|supplier|wholesale)\b", " ", cleaned)
    normalized = " ".join(re.findall(r"[^\W_]+", cleaned, flags=re.UNICODE))
    return normalized or "unknown supplier"


def telegram_engagement(message):
    reaction_total = sum(
        max(0, int(getattr(reaction, "count", 0) or 0))
        for reaction in getattr(getattr(message, "reactions", None), "results", []) or []
    )
    replies = getattr(message, "replies", None)
    comments = max(0, int(getattr(replies, "replies", 0) or 0))
    return reaction_total, comments, max(0, int(getattr(message, "forwards", 0) or 0))


def image_dhash(image):
    if not isinstance(image, Image.Image):
        raise TypeError("An opened image is required.")
    image = ImageOps.exif_transpose(image)
    if image.width * image.height > 40_000_000:
        raise ValueError("Images must be smaller than 40 megapixels.")
    pixels = list(
        image.convert("L").resize((9, 8), Image.Resampling.LANCZOS).tobytes()
    )
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return f"{value:016x}"


def image_dhash_bytes(image_bytes):
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Images must be smaller than 40 megapixels.")
            image.load()
            return image_dhash(image)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("The uploaded file is not a readable image.") from error


def image_upload_hash_and_chat_ids(content_type, body):
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
        + body
    )
    image_hash = None
    chat_ids = []
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if name == "image" and part.get_filename():
            image = part.get_payload(decode=True) or b""
            if not image:
                raise ValueError("Choose a non-empty image file.")
            if len(image) > MAX_IMAGE_UPLOAD_BYTES:
                raise ValueError("Images must be smaller than 8 MB.")
            image_hash = image_dhash_bytes(image)
        elif name == "chat_ids":
            try:
                chat_ids.extend(int(value) for value in (part.get_content() or "").split(",") if value.strip())
            except ValueError as error:
                raise ValueError("Choose valid Telegram channels for image search.") from error
    if image_hash is None:
        raise ValueError("Choose an image file to search.")
    return image_hash, list(dict.fromkeys(chat_ids))


def image_upload_hash(content_type, body):
    image_hash, _ = image_upload_hash_and_chat_ids(content_type, body)
    return image_hash


def load_product_history(product_key):
    with closing(sqlite3.connect(DB_PATH)) as connection:
        rows = connection.execute(
            """
            SELECT chat_id, message_id, message_date, views, reactions, comments, forwards
            FROM messages WHERE normalized_product = ? AND message_date != ''
            ORDER BY message_date
            """,
            (product_key,),
        ).fetchall()
    unique_posts = {}
    for chat_id, message_id, posted_at, views, reactions, comments, forwards in rows:
        post_key = (chat_id, message_id)
        observation = unique_posts.get(post_key)
        if observation is None or (reactions or 0) > observation["reactions"]:
            unique_posts[post_key] = {
                "date": posted_at[:10],
                "views": views or 0,
                "reactions": reactions or 0,
                "comments": comments or 0,
                "forwards": forwards or 0,
            }
    daily = {}
    for observation in unique_posts.values():
        bucket = daily.setdefault(
            observation["date"],
            {"date": observation["date"], "posts": 0, "views": 0, "reactions": 0, "comments": 0, "forwards": 0},
        )
        bucket["posts"] += 1
        for metric in ("views", "reactions", "comments", "forwards"):
            bucket[metric] += observation[metric]
    return sorted(daily.values(), key=lambda bucket: bucket["date"])


def load_engagement_alerts(scan_id=None):
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        if scan_id:
            rows = connection.execute(
                """
                SELECT alert_id, product_key, observed, threshold, message_date,
                    created_at, chat_id, message_id,
                    (SELECT telegram_url FROM messages
                     WHERE messages.chat_id = engagement_alerts.chat_id
                       AND messages.message_id = engagement_alerts.message_id
                     ORDER BY rowid DESC LIMIT 1) AS telegram_url
                FROM engagement_alerts WHERE scan_id = ? ORDER BY observed DESC
                """,
                (scan_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT alert_id, product_key, observed, threshold, message_date,
                    created_at, chat_id, message_id,
                    (SELECT telegram_url FROM messages
                     WHERE messages.chat_id = engagement_alerts.chat_id
                       AND messages.message_id = engagement_alerts.message_id
                     ORDER BY rowid DESC LIMIT 1) AS telegram_url
                FROM engagement_alerts ORDER BY created_at DESC LIMIT 100
                """
            ).fetchall()
    return [dict(row) for row in rows]


def export_products_csv(products):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        (
            "Product",
            "Description",
            "Test priority",
            "Telegram score",
            "Posts",
            "Suppliers",
            "Telegram reactions",
            "Telegram comments",
            "Telegram forwards",
            "Listed supplier prices",
            "Competitor listings",
            "Meta lookup status",
            "Active Meta ads returned",
            "Advertisers with active ads",
            "Active advertisers",
            "Verified sales data",
            "Telegram post links",
        )
    )
    for product in products:
        supplier_prices = [
            f"{offer['supplier']}: {offer['currency'] or ''} {offer['price']}"
            for offer in product["offers"]
            if offer["price"] is not None
        ]
        competitor_listings = [
            f"{listing.get('title') or listing['url']}: {listing.get('currency') or ''} {listing['price']}"
            for listing in product["competitor_listings"]
            if listing["status"] == "ok" and listing["price"] is not None
        ]
        links = [
            offer["telegram_url"]
            for offer in product["offers"]
            if offer.get("telegram_url")
        ]
        meta_lookup = product.get("meta_ads_lookup")
        active_ads = product.get("active_meta_ads", [])
        advertisers = {
            ad.get("page_name") for ad in active_ads if ad.get("page_name")
        }
        writer.writerow(
            (
                product["product"],
                product["description"],
                product["score"] if product["score"] is not None else "",
                product["telegram_score"] if product["telegram_score"] is not None else "",
                product["posts"],
                product["sellers"],
                product["total_reactions"],
                product["total_comments"],
                product["total_forwards"],
                " | ".join(supplier_prices),
                " | ".join(competitor_listings),
                meta_lookup.get("status", "")
                if meta_lookup
                else "identify product first"
                if product["candidate_only"]
                else "not checked",
                len(active_ads),
                len(advertisers),
                " | ".join(sorted(advertisers)),
                "Not provided by Meta Ad Library",
                " | ".join(links),
            )
        )
    return "\ufeff" + output.getvalue()


def is_arabic_dominant(text):
    letters = [character for character in text if character.isalpha()]
    if not letters:
        return False
    arabic_letters = sum("\u0600" <= character <= "\u06ff" for character in letters)
    return arabic_letters / len(letters) >= 0.1


def clean_product_candidate(name, confidence):
    candidate = name.strip()
    if confidence < 0.85 or "##" in candidate or len(candidate) > 80:
        return None
    if any(marker in candidate.casefold() for marker in ("http", "www.", ".com", ".net")):
        return None
    candidate = candidate.strip(" \t\r\n,.;:!?-–—()[]{}")
    if not PRODUCT_NAME_RE.fullmatch(candidate):
        return None
    words = re.findall(r"[^\W_]+", candidate, flags=re.UNICODE)
    if not words or not any(len(word) >= 3 and not word.isdigit() for word in words):
        return None
    if any(word.casefold() in PRODUCT_NAME_STOPWORDS for word in words):
        return None
    return candidate


def _looks_like_concrete_product(name):
    """True when a web-given name reads like an actual product, not a tag or listicle.

    Web results for noisy supplier posts can be high-confidence and still wrong:
    stock-photo captions ("90,000+ Free Face Sheet Mask Images"), category pages,
    and listicles match the query tokens but are not the product. This blocks those
    from being auto-saved as the canonical name while still letting a real product
    title through.
    """
    name = " ".join(str(name or "").split())
    if not name:
        return False
    if re.search(
        r"\b(free|images|photos|pic|picture|stock|download|gallery|lots? of|wallpaper)\b",
        name,
        flags=re.I,
    ):
        return False
    words = re.findall(r"[^\W_]+", name, flags=re.UNICODE)
    content_words = [
        w for w in words if len(w) >= 3 and not w.isdigit() and w.casefold() not in _WEB_NOISE_WORDS
    ]
    return len(content_words) >= 2


def setting(name, default=""):
    value = os.environ.get(name, "").strip()
    if not value and os.name == "nt":
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            value = ""
        value = str(value).strip()
    return value or default


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _dpapi_transform(value, protect):
    if os.name != "nt":
        raise RuntimeError("Protected local credentials are supported only on Windows.")
    buffer = ctypes.create_string_buffer(value)
    source = _DataBlob(
        len(value),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    result = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if protect:
        transform = crypt32.CryptProtectData
        transform.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        transform.restype = wintypes.BOOL
        succeeded = transform(
            ctypes.byref(source),
            "Wholesale Product Research credentials",
            None,
            None,
            None,
            0x1,
            ctypes.byref(result),
        )
    else:
        description = wintypes.LPWSTR()
        transform = crypt32.CryptUnprotectData
        transform.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        transform.restype = wintypes.BOOL
        succeeded = transform(
            ctypes.byref(source),
            ctypes.byref(description),
            None,
            None,
            None,
            0x1,
            ctypes.byref(result),
        )
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))
    if not succeeded:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def load_local_credentials():
    global _credentials_cache
    with _credentials_lock:
        if _credentials_cache is None:
            if not CREDENTIALS_PATH.exists():
                _credentials_cache = {}
            else:
                try:
                    protected = CREDENTIALS_PATH.read_bytes()
                    decoded = _dpapi_transform(protected, protect=False)
                    credentials = json.loads(decoded.decode("utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise RuntimeError(
                        "Could not decrypt the local credentials. Delete the encrypted credentials file "
                        "from your Windows user profile, then enter them again."
                    ) from error
                if not isinstance(credentials, dict):
                    raise RuntimeError("The encrypted local credentials file is invalid.")
                _credentials_cache = credentials
        return dict(_credentials_cache)


def save_local_credentials(credentials):
    global _credentials_cache
    if os.name != "nt":
        raise RuntimeError("Protected local credentials are supported only on Windows.")
    encoded = json.dumps(credentials, ensure_ascii=True).encode("utf-8")
    protected = _dpapi_transform(encoded, protect=True)
    with _credentials_lock:
        CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = CREDENTIALS_PATH.with_suffix(".tmp")
        temporary_path.write_bytes(protected)
        temporary_path.replace(CREDENTIALS_PATH)
        _credentials_cache = dict(credentials)


def configured_setting(name, default=""):
    """Return a secret the user typed into the dashboard.

    Keys are only ever read from the encrypted local credentials file, so the
    user always supplies their own Telegram and Meta credentials.
    """
    credentials = load_local_credentials()
    return str(credentials.get(name, "")).strip() or default


def credential_status():
    api_id = configured_setting("TELEGRAM_API_ID")
    api_hash = configured_setting("TELEGRAM_API_HASH")
    return {
        "telegram_configured": bool(api_id and api_hash),
        "meta_configured": meta_lookup_available(),
    }


def update_local_credentials(body):
    with _credentials_lock:
        credentials = load_local_credentials()
        api_id = str(body.get("telegram_api_id", "")).strip()
        api_hash = str(body.get("telegram_api_hash", "")).strip()
        if api_id or api_hash:
            effective_id = api_id or configured_setting("TELEGRAM_API_ID")
            effective_hash = api_hash or configured_setting("TELEGRAM_API_HASH")
            if not effective_id.isdecimal() or int(effective_id) <= 0:
                raise ValueError("Telegram API ID must be a positive number.")
            if not re.fullmatch(r"[a-fA-F0-9]{32}", effective_hash):
                raise ValueError("Telegram API hash must contain 32 hexadecimal characters.")
            credentials["TELEGRAM_API_ID"] = effective_id
            credentials["TELEGRAM_API_HASH"] = effective_hash
        save_local_credentials(credentials)


def required_env(name):
    value = configured_setting(name)
    if not value:
        raise RuntimeError(
            f"{name} is not configured. Open the Telegram connection dialog and enter your own key."
        )
    return value


class AsyncRunner:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)


runner = AsyncRunner()


async def get_client():
    global _client
    if _client is None:
        api_id = int(required_env("TELEGRAM_API_ID"))
        api_hash = required_env("TELEGRAM_API_HASH")
        session_path = setting(
            "TELEGRAM_SESSION", str(Path.home() / "telegram_product_reader")
        )
        _client = TelegramClient(session_path, api_id, api_hash)
    await _client.connect()
    return _client


async def disconnect_telegram_client():
    global _client
    if _client is not None:
        client = _client
        await client.disconnect()
        _client = None


async def require_authorized_client():
    client = await get_client()
    if not await client.is_user_authorized():
        raise TelegramSignInRequired()
    return client


async def send_login_code(phone):
    client = await get_client()
    if await client.is_user_authorized():
        with _state_lock:
            _state.update(needs_login=False, login_stage="done", message="Telegram is already signed in.")
        return
    sent_code = await client.send_code_request(phone)
    _login_state.update(phone=phone, phone_code_hash=sent_code.phone_code_hash)
    with _state_lock:
        _state.update(
            needs_login=True,
            login_stage="code",
            message="Telegram sent a sign-in code. Enter it in this dashboard.",
        )


async def verify_login_code(code):
    if not _login_state["phone"] or not _login_state["phone_code_hash"]:
        raise RuntimeError("Request a Telegram sign-in code first.")
    client = await get_client()
    try:
        await client.sign_in(
            phone=_login_state["phone"],
            code=code,
            phone_code_hash=_login_state["phone_code_hash"],
        )
    except SessionPasswordNeededError:
        with _state_lock:
            _state.update(
                needs_login=True,
                login_stage="password",
                message="Telegram requires your two-step verification password.",
            )
        return
    _login_state.update(phone=None, phone_code_hash=None)
    with _state_lock:
        _state.update(needs_login=False, login_stage="done", message="Signed in to Telegram.")


async def verify_two_step_password(password):
    if not _login_state["phone"]:
        raise RuntimeError("Request a Telegram sign-in code first.")
    client = await get_client()
    await client.sign_in(password=password)
    _login_state.update(phone=None, phone_code_hash=None)
    with _state_lock:
        _state.update(needs_login=False, login_stage="done", message="Signed in to Telegram.")


async def get_product_extractor():
    global _extractor, _extractor_load_task
    if _extractor is None:
        if _extractor_load_task is None:
            model_name = setting("PRODUCT_NER_MODEL", MODEL_DEFAULT)
            weight_path = MODEL_DIR / "pytorch_model.bin"
            config_path = MODEL_DIR / "config.json"
            if (
                model_name == MODEL_DEFAULT
                and weight_path.is_file()
                and weight_path.stat().st_size == MODEL_WEIGHT_SIZE
                and config_path.is_file()
            ):
                model_name = str(MODEL_DIR)
            _extractor_load_task = asyncio.create_task(
                asyncio.to_thread(
                    pipeline,
                    "token-classification",
                    model=model_name,
                    aggregation_strategy="simple",
                )
            )
            _extractor_load_task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
        try:
            _extractor = await asyncio.shield(_extractor_load_task)
        except Exception:
            _extractor_load_task = None
            raise
    return _extractor


def get_model_download_status():
    model_name = setting("PRODUCT_NER_MODEL", MODEL_DEFAULT)
    if model_name != MODEL_DEFAULT:
        return {"model_download_bytes": None, "model_download_total": None}
    weight_path = MODEL_DIR / "pytorch_model.bin"
    downloaded = weight_path.stat().st_size if weight_path.is_file() else 0
    partial_path = MODEL_DIR / "pytorch_model.chunk"
    if partial_path.is_file():
        downloaded += partial_path.stat().st_size
    return {
        "model_download_bytes": min(downloaded, MODEL_WEIGHT_SIZE),
        "model_download_total": MODEL_WEIGHT_SIZE,
    }


def update_job(message):
    with _state_lock:
        _state["message"] = message


def start_job(message, coroutine_factory, kind=None):
    global _active_job_future
    with _state_lock:
        if _state["running"]:
            return False
        _state.update(
            running=True,
            message=message,
            error=None,
            job_kind=kind,
            cancel_requested=False,
        )

    async def run():
        global _active_scan_id
        try:
            await coroutine_factory()
        except asyncio.CancelledError:
            if kind == "scan" and _active_scan_id:
                with sqlite3.connect(DB_PATH) as connection:
                    connection.execute(
                        """
                        UPDATE scans SET completed_at = ?, status = 'cancelled',
                            scanned_messages = ?, products_found = ?
                        WHERE scan_id = ? AND status = 'running'
                        """,
                        (
                            datetime.now(timezone.utc).isoformat(),
                            _state["scanned_messages"],
                            _state["products_found"],
                            _active_scan_id,
                        ),
                    )
            with _state_lock:
                _state.update(
                    running=False,
                    job_kind=None,
                    cancel_requested=False,
                    message=(
                        "Scan stopped. Results collected so far are saved below."
                        if kind == "scan"
                        else "Operation cancelled."
                    ),
                )
            _active_scan_id = None
            raise
        except TelegramSignInRequired:
            with _state_lock:
                _state.update(
                    running=False,
                    needs_login=True,
                    login_stage="phone",
                    message="Sign in to Telegram in this dashboard to load your chats.",
                    error=None,
                    job_kind=None,
                )
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            if kind == "scan" and _active_scan_id:
                with sqlite3.connect(DB_PATH) as connection:
                    connection.execute(
                        """
                        UPDATE scans SET completed_at = ?, status = 'failed'
                        WHERE scan_id = ? AND status = 'running'
                        """,
                        (datetime.now(timezone.utc).isoformat(), _active_scan_id),
                    )
                _active_scan_id = None
            with _state_lock:
                _state.update(
                    running=False, message="The operation failed.", error=detail, job_kind=None
                )
            print(detail, flush=True)
        else:
            with _state_lock:
                stopped_image_search = (
                    kind == "image_index" and _image_index_cancel.is_set()
                )
                _state.update(
                    running=False,
                    job_kind=None,
                    cancel_requested=False,
                    message=(
                        "Image indexing stopped safely. Progress is saved; resume when ready."
                        if stopped_image_search
                        else _state["message"]
                    ),
                )

    future: Future = runner.submit(run())
    _active_job_future = future

    def failed_to_schedule(completed):
        if completed.cancelled():
            return
        exception = completed.exception()
        if exception:
            detail = f"{type(exception).__name__}: {exception}"
            with _state_lock:
                _state.update(
                    running=False, message="The operation failed.", error=detail, job_kind=None
                )
            print(detail, flush=True)

    future.add_done_callback(failed_to_schedule)
    return True


def get_chats():
    return sorted(
        [
            {
                "id": chat_id,
                "title": entity.title,
                "kind": "channel" if getattr(entity, "broadcast", False) else "group",
                "username": getattr(entity, "username", None),
            }
            for chat_id, entity in _chat_entities.items()
        ],
        key=lambda chat: chat["title"].casefold(),
    )


async def load_chats():
    global _chat_entities
    client = await require_authorized_client()
    entities = {}
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
            entities[utils.get_peer_id(entity)] = entity
        elif entity.__class__.__name__ == "Chat":
            entities[entity.id] = entity
    _chat_entities = entities
    update_job(f"Loaded {len(entities)} accessible channels and groups.")


def image_index_status(search_hash=None):
    cutoff_date = (date.today() - timedelta(days=365)).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as connection:
        total_photos = connection.execute(
            "SELECT COUNT(*) FROM telegram_image_index"
        ).fetchone()[0]
        indexed_chats = connection.execute(
            """
            SELECT COUNT(*) FROM telegram_image_index_state
            WHERE cutoff_date = ? AND status = 'complete'
            """,
            (cutoff_date,),
        ).fetchone()[0]
        paused_chats = connection.execute(
            """
            SELECT COUNT(*) FROM telegram_image_index_state
            WHERE cutoff_date = ? AND status = 'paused'
            """,
            (cutoff_date,),
        ).fetchone()[0]
        ready_chat_ids = [
            row[0]
            for row in connection.execute(
                """
                SELECT chat_id FROM telegram_image_index_state
                WHERE cutoff_date = ? AND (
                    status = 'complete' OR
                    (status = 'match_found' AND match_hash = ?)
                )
                """,
                (cutoff_date, search_hash or ""),
            ).fetchall()
        ]
    with _image_index_lock:
        state = dict(_image_index_state)
    if paused_chats and state["status"] in ("idle", "ready"):
        state.update(
            status="paused",
            message="Image indexing is paused. Your saved channel/message checkpoint can be resumed.",
        )
    reported_indexed_chats = (
        state["indexed_chats"]
        if state["total_chats"] and state["status"] in ("indexing", "paused", "ready")
        else indexed_chats
    )
    reported_total_chats = (
        state["total_chats"]
        if state["total_chats"] and state["status"] in ("indexing", "paused", "ready")
        else len(_chat_entities) or state["total_chats"]
    )
    return {
        **state,
        "indexed_photos": total_photos,
        "indexed_chats": reported_indexed_chats,
        "total_chats": reported_total_chats,
        "ready_chat_ids": ready_chat_ids,
        "cutoff_date": cutoff_date,
        "ready": bool(_chat_entities) and indexed_chats == len(_chat_entities),
    }


def find_similar_telegram_images(image_hash, limit=30, chat_ids=None):
    uploaded_hash = int(image_hash, 16)
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        selected_ids = list(dict.fromkeys(chat_ids or []))
        channel_filter = ""
        params = ()
        if selected_ids:
            channel_filter = f" WHERE chat_id IN ({','.join('?' for _ in selected_ids)})"
            params = tuple(selected_ids)
        candidates = connection.execute(
            f"""
            SELECT chat_id, product, supplier, chat_title, price, currency,
                message_date, telegram_url, image_name, image_hash, product_key
            FROM telegram_image_index{channel_filter}
            ORDER BY message_date DESC
            """,
            params,
        ).fetchall()
    matches_by_chat = {}
    for row in candidates:
        candidate = dict(row)
        if candidate["chat_id"] in matches_by_chat:
            continue
        distance = (uploaded_hash ^ int(candidate.pop("image_hash"), 16)).bit_count()
        if distance <= 12:
            candidate["difference_bits"] = distance
            matches_by_chat[candidate["chat_id"]] = candidate
            if len(matches_by_chat) >= limit:
                break
    return list(matches_by_chat.values())


def seed_saved_telegram_images():
    with closing(sqlite3.connect(DB_PATH)) as connection:
        rows = connection.execute(
            """
            SELECT current.chat_id, current.message_id, current.product,
                current.normalized_product, current.supplier, current.chat_title,
                current.price, current.currency, current.message_date,
                current.telegram_url, current.image_name
            FROM messages AS current
            WHERE current.image_name IS NOT NULL AND current.rowid = (
                SELECT MAX(previous.rowid) FROM messages AS previous
                WHERE previous.chat_id = current.chat_id
                  AND previous.message_id = current.message_id
                  AND previous.image_name IS NOT NULL
            )
            """
        ).fetchall()
        seeded = 0
        for row in rows:
            (
                chat_id, message_id, product, product_key, supplier, chat_title,
                price, currency, message_date, telegram_url, image_name,
            ) = row
            image_path = IMAGE_DIR / Path(image_name).name
            if not image_path.is_file():
                continue
            try:
                image_hash = image_dhash_bytes(image_path.read_bytes())
            except (OSError, ValueError) as error:
                print(f"Could not index saved Telegram image {image_name}: {error}", flush=True)
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO telegram_image_index (
                    chat_id, message_id, chat_title, product, product_key,
                    supplier, price, currency, message_date, telegram_url,
                    image_name, image_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id, message_id, chat_title,
                    product or "Photo offer (name not identified)",
                    product_key or normalize_product(product or ""),
                    supplier, price, currency, message_date,
                    telegram_url, image_name, image_hash,
                ),
            )
            seeded += connection.execute("SELECT changes()").fetchone()[0]
        connection.commit()
    return seeded


async def build_telegram_image_index(selected_chat_ids=None, search_hash=None):
    global _chat_entities
    _image_index_cancel.clear()
    client = await require_authorized_client()
    if not _chat_entities:
        await load_chats()
    cutoff = datetime.combine(
        date.today() - timedelta(days=365),
        datetime.min.time(),
        timezone.utc,
    )
    cutoff_date = cutoff.date().isoformat()
    if selected_chat_ids is None:
        chat_items = list(_chat_entities.items())
    else:
        missing_ids = set(selected_chat_ids) - set(_chat_entities)
        if missing_ids:
            raise RuntimeError("Refresh Telegram; one or more selected channels are no longer available.")
        chat_items = [
            (chat_id, _chat_entities[chat_id])
            for chat_id in selected_chat_ids
        ]
    seeded_photos = await asyncio.to_thread(seed_saved_telegram_images)
    with _image_index_lock:
        _image_index_state.update(
            status="indexing",
            indexed_photos=seeded_photos,
            total_photos=0,
            indexed_chats=0,
            total_chats=len(chat_items),
            message=f"Indexing Telegram images from the last year in {len(chat_items)} chats…",
            error="",
        )
    try:
        for chat_number, (chat_id, entity) in enumerate(chat_items, start=1):
            if _image_index_cancel.is_set():
                with _image_index_lock:
                    _image_index_state.update(
                        status="paused",
                        message="Image indexing stopped. Your progress is saved; search again to resume.",
                    )
                return
            with closing(sqlite3.connect(DB_PATH)) as connection:
                progress = connection.execute(
                    """
                    SELECT cutoff_date, last_message_id, status, match_hash
                    FROM telegram_image_index_state WHERE chat_id = ?
                    """,
                    (chat_id,),
                ).fetchone()
                if progress and progress[0] == cutoff_date and (
                    progress[2] == "complete"
                    or (progress[2] == "match_found" and progress[3] == search_hash)
                ):
                    with _image_index_lock:
                        _image_index_state["indexed_chats"] += 1
                    continue
                cursor_id = (
                    progress[1]
                    if progress and progress[0] == cutoff_date
                    else 0
                )
                connection.execute(
                    """
                    INSERT INTO telegram_image_index_state (
                        chat_id, cutoff_date, last_message_id, status,
                        match_hash, indexed_images, updated_at
                    ) VALUES (?, ?, ?, 'indexing', ?, 0, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        cutoff_date=excluded.cutoff_date,
                        last_message_id=excluded.last_message_id,
                        status='indexing',
                        match_hash=excluded.match_hash,
                        indexed_images=CASE
                            WHEN telegram_image_index_state.cutoff_date=excluded.cutoff_date
                            THEN telegram_image_index_state.indexed_images
                            ELSE 0
                        END,
                        updated_at=excluded.updated_at
                    """,
                    (
                        chat_id,
                        cutoff_date,
                        cursor_id,
                        search_hash or "",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                connection.commit()
                last_seen_id = cursor_id
                checked = 0
                indexed_in_chat = 0
                match_found = False
                with _image_index_lock:
                    _image_index_state["message"] = (
                        f"Indexing chat {chat_number} of {len(chat_items)}: {entity.title}"
                    )
                async for message in cancellable_telegram_messages(
                    client.iter_messages(
                        entity,
                        offset_date=datetime.now(timezone.utc) + timedelta(minutes=1),
                        max_id=cursor_id,
                    ),
                    _image_index_cancel,
                ):
                    if _image_index_cancel.is_set():
                        connection.execute(
                            """
                            UPDATE telegram_image_index_state SET last_message_id=?,
                                status='paused', indexed_images=indexed_images+?,
                                updated_at=? WHERE chat_id=?
                            """,
                            (
                                last_seen_id,
                                indexed_in_chat,
                                datetime.now(timezone.utc).isoformat(),
                                chat_id,
                            ),
                        )
                        connection.commit()
                        with _image_index_lock:
                            _image_index_state.update(
                                status="paused",
                                indexed_photos=_image_index_state["indexed_photos"] + indexed_in_chat,
                                message="Image indexing stopped. Your progress is saved; search again to resume.",
                            )
                        return
                    message_date = message.date.astimezone(timezone.utc) if message.date else None
                    if message_date and message_date < cutoff:
                        break
                    checked += 1
                    if has_product_image(message):
                        image_name = make_image_name(
                            chat_id,
                            message.id,
                            telegram_image_extension(message),
                        )
                        image_path = IMAGE_DIR / image_name
                        try:
                            if not image_path.is_file():
                                try:
                                    await message.download_media(file=str(image_path))
                                except FloodWaitError as error:
                                    if not await cancellable_delay(
                                        _image_index_cancel,
                                        error.seconds,
                                    ):
                                        break
                                    await message.download_media(file=str(image_path))
                            if image_path.is_file():
                                image_hash = image_dhash_bytes(image_path.read_bytes())
                                text = message.message or ""
                                existing = connection.execute(
                                    """
                                    SELECT product, normalized_product, supplier,
                                        price, currency, telegram_url
                                    FROM messages WHERE chat_id = ? AND message_id = ?
                                    ORDER BY rowid DESC LIMIT 1
                                    """,
                                    (chat_id, message.id),
                                ).fetchone()
                                details = parse_trade_details(text)
                                title, _ = derive_offer_title(
                                    text,
                                    existing[0] if existing else None,
                                )
                                try:
                                    sender = await message.get_sender()
                                except RPCError as error:
                                    print(
                                        f"Could not read Telegram sender for message {message.id}: {error}",
                                        flush=True,
                                    )
                                    sender = None
                                supplier = (
                                    (existing[2] if existing else None)
                                    or getattr(sender, "title", None)
                                    or getattr(sender, "username", None)
                                    or getattr(sender, "first_name", None)
                                    or entity.title
                                )
                                username = getattr(entity, "username", None)
                                connection.execute(
                                    """
                                    INSERT OR REPLACE INTO telegram_image_index (
                                        chat_id, message_id, chat_title, product,
                                        product_key, supplier,
                                        price, currency, message_date, telegram_url,
                                        image_name, image_hash
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        chat_id,
                                        message.id,
                                        entity.title,
                                        title if title != "Unidentified offer" else "Photo offer (name not identified)",
                                        existing[1] if existing and existing[1] else normalize_product(title),
                                        supplier,
                                        existing[3] if existing and existing[3] is not None else details["price"],
                                        existing[4] if existing and existing[4] else details["currency"],
                                        message_date.isoformat() if message_date else "",
                                        (existing[5] if existing else None)
                                        or telegram_message_url(chat_id, username, message.id),
                                        image_name,
                                        image_hash,
                                    ),
                                )
                                indexed_in_chat += 1
                                if (
                                    search_hash
                                    and (int(search_hash, 16) ^ int(image_hash, 16)).bit_count() <= 12
                                ):
                                    match_found = True
                        except (OSError, ValueError, RPCError) as error:
                            print(
                                f"Could not index Telegram image in {entity.title}, message {message.id}: {error}",
                                flush=True,
                            )
                    last_seen_id = message.id
                    if match_found:
                        break
                    if checked % 100 == 0:
                        connection.execute(
                            """
                            UPDATE telegram_image_index_state SET
                                last_message_id=?, indexed_images=?, updated_at=?
                            WHERE chat_id=?
                            """,
                            (
                                last_seen_id,
                                indexed_in_chat,
                                datetime.now(timezone.utc).isoformat(),
                                chat_id,
                            ),
                        )
                        connection.commit()
                        with _image_index_lock:
                            _image_index_state["indexed_photos"] += indexed_in_chat
                            _image_index_state["message"] = (
                                f"Indexing chat {chat_number} of {len(chat_items)}: "
                                f"{entity.title} · {checked:,} messages checked"
                            )
                        indexed_in_chat = 0
                    elif checked % 25 == 0:
                        with _image_index_lock:
                            _image_index_state["message"] = (
                                f"Indexing chat {chat_number} of {len(chat_items)}: "
                                f"{entity.title} · {checked:,} messages checked"
                            )
                if _image_index_cancel.is_set():
                    connection.execute(
                        """
                        UPDATE telegram_image_index_state SET last_message_id=?,
                            status='paused', indexed_images=indexed_images+?,
                            updated_at=? WHERE chat_id=?
                        """,
                        (
                            last_seen_id,
                            indexed_in_chat,
                            datetime.now(timezone.utc).isoformat(),
                            chat_id,
                        ),
                    )
                    connection.commit()
                    with _image_index_lock:
                        _image_index_state.update(
                            status="paused",
                            indexed_photos=_image_index_state["indexed_photos"] + indexed_in_chat,
                            message="Image indexing stopped. Your progress is saved; resume when ready.",
                        )
                    return
                connection.execute(
                    """
                    UPDATE telegram_image_index_state SET last_message_id=?,
                        status=?, match_hash=?, indexed_images=indexed_images+?,
                        updated_at=? WHERE chat_id=?
                    """,
                    (
                        last_seen_id,
                        "match_found" if match_found else "complete",
                        search_hash or "",
                        indexed_in_chat,
                        datetime.now(timezone.utc).isoformat(),
                        chat_id,
                    ),
                )
                connection.commit()
                with _image_index_lock:
                    _image_index_state["indexed_photos"] += indexed_in_chat
                    _image_index_state["indexed_chats"] += 1
        with _image_index_lock:
            _image_index_state.update(
                status="ready",
                message="Telegram photo index is ready. Image searches now use the local index.",
            )
    except Exception as error:
        with _image_index_lock:
            _image_index_state.update(
                status="failed",
                error=f"{type(error).__name__}: {error}",
                message="Telegram photo indexing did not finish.",
            )
        raise


async def extract_entities(text):
    if is_arabic_dominant(text):
        return [], {}
    extractor = await get_product_extractor()
    parts = []
    for paragraph in re.split(r"(?<=[\n.!?])\s+|\n+", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        words = paragraph.split()
        current = ""
        for word in words:
            if current and len(current) + len(word) + 1 > 1000:
                parts.append(current)
                current = ""
            current = f"{current} {word}".strip()
        if current:
            parts.append(current)
    entity_label = setting("PRODUCT_ENTITY_LABEL", LABEL_DEFAULT).casefold()
    products = []
    attributes = {}
    attribute_labels = {
        "trademark": "brand",
        "manufacturer": "manufacturer",
        "modelno": "model_number",
        "country": "origin_country",
        "hscode": "hs_code",
        "hscodeen": "hs_code_description",
    }
    for part in parts:
        entities = await asyncio.to_thread(extractor, part)
        for entity in entities:
            label = entity.get("entity_group", "").removeprefix("B-").casefold()
            word = entity.get("word", "").strip()
            if not word:
                continue
            if label == entity_label:
                candidate = clean_product_candidate(word, float(entity.get("score", 0)))
                if candidate:
                    products.append(candidate)
            elif label in attribute_labels:
                values = attributes.setdefault(attribute_labels[label], [])
                if word not in values:
                    values.append(word)
    unique_products = {}
    for name in products:
        unique_products.setdefault(normalize_product(name), name)
    return list(unique_products.values()), attributes


def make_image_name(chat_id, message_id, extension=".jpg"):
    digest = hashlib.sha256(f"{chat_id}:{message_id}".encode()).hexdigest()
    return f"{digest}{extension}"


def has_product_image(message):
    if message.photo:
        return True
    document = message.document
    return bool(document and (document.mime_type or "").startswith("image/"))


def telegram_image_extension(message):
    mime_type = getattr(getattr(message, "file", None), "mime_type", None)
    extension = mimetypes.guess_extension(mime_type or "") if mime_type else None
    return extension if extension in (".jpg", ".jpeg", ".png", ".webp", ".gif") else ".jpg"


async def run_scan(chat_ids, start_date, end_date):
    global _current_scan_id, _current_market, _active_scan_id
    client = await require_authorized_client()
    scan_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    _current_scan_id = scan_id
    _active_scan_id = None
    range_start = datetime.combine(start_date, datetime.min.time(), timezone.utc)
    range_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), timezone.utc)
    scanned = 0
    matched = 0
    seen_products = set()
    pending_identify = []
    with _state_lock:
        _state.update(
            scanned_messages=0,
            products_found=0,
            products_detected=0,
            chat_index=0,
            chat_total=len(chat_ids),
            current_chat="",
            cancel_requested=False,
        )

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO scans (
                scan_id, start_date, end_date, market, completed_at, status,
                chat_count, chat_titles_json, chat_ids_json, extractor_version
            ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, 3)
            """,
            (
                scan_id,
                start_date.isoformat(),
                end_date.isoformat(),
                setting("MARKET_COUNTRY", "United States"),
                datetime.now(timezone.utc).isoformat(),
                len(chat_ids),
                json.dumps([_chat_entities[chat_id].title for chat_id in chat_ids]),
                json.dumps(chat_ids),
            ),
        )
        connection.commit()
        _active_scan_id = scan_id
        was_cancelled = False
        # Identification starts with the scan, not after it: the pool is ready
        # before the first product is written, so every offer post can be named
        # the moment it is discovered.
        start_progressive_identification(scan_id)
        update_job(
            "Loading the downloaded product recognizer. Message-count progress starts when it is ready."
        )
        await get_product_extractor()
        for index, chat_id in enumerate(chat_ids, start=1):
            if _scan_cancel.is_set():
                was_cancelled = True
                break
            entity = _chat_entities.get(chat_id)
            if entity is None:
                continue
            with _state_lock:
                _state.update(
                    chat_index=index,
                    current_chat=entity.title,
                    message=f"Scanning chat {index} of {len(chat_ids)}: {entity.title}",
                )
            async for message in cancellable_telegram_messages(
                client.iter_messages(entity, offset_date=range_end),
                _scan_cancel,
            ):
                if _scan_cancel.is_set():
                    was_cancelled = True
                    break
                if message.date and message.date.replace(tzinfo=timezone.utc) < range_start:
                    break
                scanned += 1
                text = message.message or ""
                details = parse_trade_details(text)
                product_names, attributes = await extract_entities(text) if text else ([], {})
                has_image = has_product_image(message)
                review_candidate = not product_names and (
                    has_image
                    or details["price"] is not None
                    or details["moq"] is not None
                )
                if review_candidate:
                    title, _ = derive_offer_title(text)
                    product_names = [title]
                attributes = product_attributes_from_text(text, attributes)
                if product_names:
                    matched += 1
                    sender = await message.get_sender()
                    supplier = (
                        getattr(sender, "title", None)
                        or getattr(sender, "username", None)
                        or getattr(sender, "first_name", None)
                        or entity.title
                    )
                    image_name = None
                    image_hash = None
                    if has_image:
                        image_name = make_image_name(
                            chat_id,
                            message.id,
                            telegram_image_extension(message),
                        )
                        await message.download_media(file=str(IMAGE_DIR / image_name))
                        image_path = IMAGE_DIR / image_name
                        if image_path.is_file():
                            image_hash = image_dhash_bytes(image_path.read_bytes())
                    reaction_count, comment_count, forward_count = telegram_engagement(message)
                    message_date = (
                        message.date.astimezone(timezone.utc).isoformat()
                        if message.date
                        else ""
                    )
                    message_age = (
                        datetime.now(timezone.utc) - message.date.astimezone(timezone.utc)
                        if message.date
                        else None
                    )
                    for product_name in product_names:
                        normalized = normalize_product(product_name)
                        if review_candidate:
                            normalized = f"review offer {chat_id} {message.id}"
                        if not normalized:
                            continue
                        if normalized not in seen_products:
                            seen_products.add(normalized)
                            if review_candidate:
                                # Unnamed offer posts are the ones that need a
                                # reader; named ones are already identified.
                                pending_identify.append(normalized)
                        description = separate_description(product_name, text)
                        if (
                            reaction_count >= 100
                            and message_age is not None
                            and timedelta(0) <= message_age <= timedelta(hours=24)
                        ):
                            connection.execute(
                                """
                                INSERT OR IGNORE INTO engagement_alerts (
                                    scan_id, chat_id, message_id, product_key, alert_type,
                                    threshold, observed, message_date, created_at
                                ) VALUES (?, ?, ?, ?, 'telegram_reactions_24h', 100, ?, ?, ?)
                                """,
                                (
                                    scan_id,
                                    chat_id,
                                    message.id,
                                    normalized,
                                    reaction_count,
                                    message_date,
                                    datetime.now(timezone.utc).isoformat(),
                                ),
                            )
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO messages (
                                scan_id, chat_id, message_id, product, normalized_product,
                                supplier, chat_title, message_date, message_text, price,
                                currency, price_unit, moq, size, views, forwards, image_name,
                                attributes_json, review_candidate, description_text, telegram_url,
                                reactions, comments, image_hash
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                scan_id,
                                chat_id,
                                message.id,
                                product_name,
                                normalized,
                                supplier,
                                entity.title,
                                message_date,
                                text[:3000],
                                details["price"],
                                details["currency"],
                                details["price_unit"],
                                details["moq"],
                                details["size"],
                                message.views,
                                forward_count,
                                image_name,
                                json.dumps(attributes, ensure_ascii=False),
                                int(review_candidate),
                                description[:3000],
                                telegram_message_url(
                                    chat_id, getattr(entity, "username", None), message.id
                                ),
                                reaction_count,
                                comment_count,
                                image_hash,
                            ),
                        )
                        if image_name and image_hash:
                            connection.execute(
                                """
                                INSERT OR REPLACE INTO telegram_image_index (
                                    chat_id, message_id, chat_title, product,
                                    product_key, supplier,
                                    price, currency, message_date, telegram_url,
                                    image_name, image_hash
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    chat_id,
                                    message.id,
                                    entity.title,
                                    product_name,
                                    normalized,
                                    supplier,
                                    details["price"],
                                    details["currency"],
                                    message_date,
                                    telegram_message_url(
                                        chat_id,
                                        getattr(entity, "username", None),
                                        message.id,
                                    ),
                                    image_name,
                                    image_hash,
                                ),
                            )
                if scanned % 20 == 0:
                    connection.commit()
                    if pending_identify:
                        # Name what has already been found while the scan is
                        # still walking the channels: this is what makes the
                        # dashboard fill in progressively instead of at the end.
                        enqueue_identification(scan_id, pending_identify)
                        pending_identify = []
                    with _state_lock:
                        _state["products_detected"] = len(seen_products)
                with _state_lock:
                    _state.update(
                        scanned_messages=scanned,
                        products_found=matched,
                        message=(
                            f"Scanning chat {index} of {len(chat_ids)}: {entity.title} "
                            f"— checked {scanned} messages; offer posts flagged: {matched}."
                        ),
                    )
            if _scan_cancel.is_set():
                was_cancelled = True
            connection.commit()
            if pending_identify:
                enqueue_identification(scan_id, pending_identify)
                pending_identify = []
            with _state_lock:
                _state["products_detected"] = len(seen_products)
            if was_cancelled:
                break
        status = "cancelled" if was_cancelled else "completed"
        connection.execute(
            """
            UPDATE scans SET completed_at = ?, status = ?, scanned_messages = ?,
                products_found = (
                    SELECT COUNT(DISTINCT normalized_product) FROM messages
                    WHERE scan_id = ?
                )
            WHERE scan_id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                status,
                scanned,
                scan_id,
                scan_id,
            ),
        )
        connection.commit()
    _current_market = setting("MARKET_COUNTRY", "United States")
    update_job(
        f"Scan {'stopped' if was_cancelled else 'finished'}: checked {scanned} messages "
        f"and flagged {matched} possible offer posts for review."
    )
    # Give the identification pool a bounded window to finish the products it
    # already holds, then let the post-scan sweeps pick up anything that is
    # still unnamed. A slow reader can therefore delay a product, never drop it.
    drain = finish_progressive_identification(scan_id)
    start_photo_identification(scan_id)
    start_web_research(scan_id)
    # Validation check: every product the scan wrote must be in the table. The
    # verdict is stored so the dashboard can show the last verified result
    # instead of an unverified count.
    verify_scan_integrity(scan_id)
    with _state_lock:
        _state.update(
            scanned_messages=scanned,
            products_found=matched,
            products_detected=len(seen_products),
            cancel_requested=False,
        )
    _active_scan_id = None


def market_country_code(market):
    return {
        "United States": "US",
        "United Kingdom": "GB",
        "European Union": "DE",
        "Algeria": "DZ",
    }.get(market, "US")


# ---- Meta Ad Library country ------------------------------------------------
# The Ad Library only returns ads that ran in the country you ask for, so this is
# chosen separately from the research market: an Algerian supplier can be
# advertising to US buyers, and vice versa.
META_AD_COUNTRIES = (
    ("DZ", "Algeria"), ("EG", "Egypt"), ("MA", "Morocco"), ("TN", "Tunisia"),
    ("SA", "Saudi Arabia"), ("AE", "United Arab Emirates"), ("QA", "Qatar"),
    ("KW", "Kuwait"), ("BH", "Bahrain"), ("OM", "Oman"), ("JO", "Jordan"),
    ("LB", "Lebanon"), ("IQ", "Iraq"), ("TR", "Türkiye"),
    ("US", "United States"), ("CA", "Canada"), ("MX", "Mexico"),
    ("BR", "Brazil"), ("AR", "Argentina"), ("CL", "Chile"),
    ("GB", "United Kingdom"), ("IE", "Ireland"), ("DE", "Germany"),
    ("FR", "France"), ("ES", "Spain"), ("IT", "Italy"),
    ("NL", "Netherlands"), ("BE", "Belgium"), ("PT", "Portugal"),
    ("CH", "Switzerland"), ("AT", "Austria"), ("SE", "Sweden"),
    ("NO", "Norway"), ("DK", "Denmark"), ("FI", "Finland"),
    ("PL", "Poland"), ("RO", "Romania"), ("GR", "Greece"),
    ("CZ", "Czechia"), ("HU", "Hungary"), ("UA", "Ukraine"),
    ("RU", "Russia"), ("IN", "India"), ("PK", "Pakistan"),
    ("BD", "Bangladesh"), ("ID", "Indonesia"), ("MY", "Malaysia"),
    ("PH", "Philippines"), ("TH", "Thailand"), ("VN", "Vietnam"),
    ("SG", "Singapore"), ("JP", "Japan"), ("KR", "South Korea"),
    ("CN", "China"), ("HK", "Hong Kong"), ("AU", "Australia"),
    ("NZ", "New Zealand"), ("ZA", "South Africa"), ("NG", "Nigeria"),
    ("KE", "Kenya"), ("GH", "Ghana"), ("IL", "Israel"),
)
META_AD_COUNTRY_CODES = frozenset(code for code, _ in META_AD_COUNTRIES)
META_AD_SETTINGS_PATH = DATA_DIR / "meta_ad_settings.json"


def load_meta_ad_settings():
    """Country the Ad Library lookup asks for, remembered across restarts.

    Falls back to the research market's country the first time, so an existing
    setup keeps behaving the way it did before this setting existed.
    """
    default_country = market_country_code(setting("MARKET_COUNTRY", "United States"))
    try:
        data = json.loads(META_AD_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"country": default_country}
    country = str(data.get("country", "")).strip().upper()
    if country not in META_AD_COUNTRY_CODES:
        country = default_country
    return {"country": country}


def save_meta_ad_settings(country):
    country = str(country or "").strip().upper()
    if country not in META_AD_COUNTRY_CODES:
        raise ValueError("Choose one of the listed Ad Library countries.")
    _meta_ad_settings["country"] = country
    META_AD_SETTINGS_PATH.write_text(
        json.dumps({"country": country}), encoding="utf-8"
    )
    return country


_meta_ad_settings = load_meta_ad_settings()


META_ADS_COLLECTOR_HINT = (
    "Meta ad lookup requires the meta-ads-collector package. "
    "Install it with: pip install meta-ads-collector"
)

_meta_collector_lock = threading.Lock()
_meta_collectors = {}  # thread ident -> collector session


def meta_collector():
    """Return a MetaAdsCollector session for the calling thread.

    Building the client is the slow part: it impersonates a browser TLS
    fingerprint and warms up a session, so a thread keeps its own instance
    instead of rebuilding one per lookup. Instances are per-thread because one
    session is not safe to share across threads; that is also what lets a batch
    look several products up at the same time. The import is deferred so the
    rest of the app still starts when the optional dependency is absent.
    """
    if MetaAdsCollector is None:
        raise RuntimeError(META_ADS_COLLECTOR_HINT)
    ident = threading.get_ident()
    with _meta_collector_lock:
        collector = _meta_collectors.get(ident)
        if collector is None:
            collector = MetaAdsCollector()
            _meta_collectors[ident] = collector
        return collector


def _close_collectors(collectors):
    for collector in collectors:
        try:
            collector.close()
        except Exception:
            pass


def close_meta_collector():
    """Release every collector session that is still open."""
    with _meta_collector_lock:
        collectors = list(_meta_collectors.values())
        _meta_collectors.clear()
    _close_collectors(collectors)


def prune_meta_collectors():
    """Close sessions whose worker thread has finished.

    Batch workers each open a session; once the pool retires those threads the
    sessions are dead weight, so they are closed instead of accumulating.
    """
    alive = {thread.ident for thread in threading.enumerate()}
    with _meta_collector_lock:
        stale = [ident for ident in _meta_collectors if ident not in alive]
        collectors = [_meta_collectors.pop(ident) for ident in stale]
    _close_collectors(collectors)


def meta_time_text(value):
    """Render a datetime the collector returned as stored text."""
    if value is None:
        return ""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def meta_range_text(value):
    """Render a spend/impression/reach range object as stored text.

    Meta only publishes these for political and social-issue ads, so this
    returns an empty string for ordinary commercial ads by design.
    """
    if value is None:
        return ""
    lower = getattr(value, "lower_bound", None)
    upper = getattr(value, "upper_bound", None)
    if lower is None and upper is None:
        return ""
    if lower is not None and upper is not None and lower != upper:
        return f"{lower}-{upper}"
    return str(lower if lower is not None else upper)


def meta_ad_to_row(ad):
    """Convert one meta-ads-collector Ad into the row shape this app stores.

    Only documented collector fields are read, and every optional field
    degrades to an empty string so one odd ad cannot fail a whole lookup.
    """
    creatives = list(getattr(ad, "creatives", None) or [])
    bodies = [str(c.body).strip() for c in creatives if getattr(c, "body", None)]
    titles = [str(c.title).strip() for c in creatives if getattr(c, "title", None)]
    captions = [str(c.caption).strip() for c in creatives if getattr(c, "caption", None)]
    primary = creatives[0] if creatives else None
    page = getattr(ad, "page", None)
    link_url = ""
    for creative in creatives:
        candidate = str(getattr(creative, "link_url", "") or "").strip()
        if candidate:
            link_url = candidate
            break
    snapshot = getattr(ad, "snapshot_url", None) or getattr(ad, "ad_snapshot_url", None) or ""
    is_active = getattr(ad, "is_active", None)
    return {
        "ad_id": str(getattr(ad, "id", "") or ""),
        "page_name": str(getattr(page, "name", "") or "") if page else "",
        "creative_text": "\n".join(bodies),
        "creative_title": "\n".join(titles),
        "delivery_start": meta_time_text(getattr(ad, "delivery_start_time", None)),
        "delivery_stop": meta_time_text(getattr(ad, "delivery_stop_time", None)),
        "platforms": list(getattr(ad, "publisher_platforms", None) or []),
        "snapshot_url": str(snapshot),
        # The display address that older code used to guess the advertiser's
        # page; kept for compatibility alongside the real destination URL.
        "link_caption": next((value for value in captions if value), ""),
        "link_url": link_url,
        "cta_text": str(getattr(primary, "cta_text", "") or "") if primary else "",
        "image_url": str(getattr(primary, "image_url", "") or "") if primary else "",
        "video_url": str(getattr(primary, "video_url", "") or "") if primary else "",
        "thumbnail_url": str(getattr(primary, "thumbnail_url", "") or "") if primary else "",
        "languages": ", ".join(getattr(ad, "languages", None) or []),
        "display_format": str(getattr(ad, "display_format", "") or ""),
        "ad_status": str(getattr(ad, "ad_status", "") or ""),
        "is_active": 1 if is_active else 0,
        "currency": str(getattr(ad, "currency", "") or ""),
        "spend": meta_range_text(getattr(ad, "spend", None)),
        "impressions": meta_range_text(getattr(ad, "impressions", None)),
        "reach": meta_range_text(getattr(ad, "reach", None)),
    }


def meta_query_for_product(product):
    """Build a targeted Ad Library search string for an identified product.

    The raw Telegram post title is usually wrong or only a fragment of the
    real product (for example a gift-set photo reads as one item, a price
    fragment survives the extractor, or a multi-brand listing is labeled
    by the store). Searching the Ad Library on that raw text returns ads for
    the wrong product. The identified product name, brand and extracted
    specifications are much closer to the real product, so they become the
    search terms.

    Returns a single string ready to be truncated to 200 characters before
    it is sent to the collector.
    """
    parts = []
    name = str(product.get("product") or "").strip()
    if name:
        parts.append(name)
    for key in ("brand", "category", "type"):
        value = str(product.get(key) or "").strip()
        if value:
            parts.append(value)
    specs = product.get("specs") or []
    for spec in specs:
        brand = str(spec.get("brand") or "").strip()
        value = str(spec.get("value") or "").strip()
        unit = str(spec.get("unit") or "").strip()
        if brand and value:
            parts.append(f"{brand} {value} {unit}".strip())
        elif value:
            parts.append(value)
    for key in ("size", "weight", "moq"):
        value = str(product.get(key) or "").strip()
        if value:
            parts.append(f"{key} {value}")
    cleaned = [part for part in parts if part]
    if not cleaned:
        return str(product.get("product") or "").strip()
    return " ".join(cleaned)


def fetch_meta_ads(product_name, max_results=None, product=None):
    """Search the Meta Ad Library for active ads matching an identified product.

    Reads the Ad Library through the meta-ads-collector package, which talks to
    the Ad Library's own internal endpoint. No Meta developer app, access token
    or app review is involved, so the previous token requirement is gone.

    The Ad Library is searched with a targeted query built from the
    identified product's name, brand and specifications rather than the raw
    supplier post title, so the returned ads describe the product the user
    actually named, not the wrong product the post title described.
    """
    query = str(product_name or "").strip()
    if product is not None and query:
        targeted = meta_query_for_product(product)
        if targeted and targeted != query:
            query = targeted[:200]
    if not query:
        return []
    country = _meta_ad_settings["country"]
    collector = meta_collector()
    rows = []
    try:
        for ad in collector.search(
            query=query,
            country=country,
            status="ACTIVE",
            max_results=max_results,
        ):
            row = meta_ad_to_row(ad)
            if not row["ad_id"]:
                continue
            rows.append(row)
            if max_results and len(rows) >= max_results:
                break
    except MetaAdsError as error:
        raise RuntimeError(f"Meta Ad Library lookup failed: {error}") from error
    except (OSError, ValueError) as error:
        raise RuntimeError(
            f"Meta Ad Library lookup failed ({type(error).__name__})."
        ) from error
    return rows


AD_DESTINATION_LIMIT = 3


def competitor_urls_from_ads(ads, limit=AD_DESTINATION_LIMIT):
    """Read the advertiser page each Meta ad points at.

    The collector returns the ad's real destination URL, which is used first.
    When only the display address (link caption) is available it is used
    instead, because that is still enough to follow the ad back to the
    competitor's own product page.
    """
    urls = []
    for ad in ads:
        destination = (
            str(ad.get("link_url") or "").strip()
            or str(ad.get("link_caption") or "").strip()
        )
        if not destination or any(character.isspace() for character in destination):
            continue
        candidate = destination if "://" in destination else f"https://{destination.strip('/')}"
        parsed = urlparse(candidate)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            continue
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        if "." not in hostname:
            continue
        normalized = f"https://{hostname}{parsed.path or '/'}"
        if parsed.query:
            normalized = f"{normalized}?{parsed.query}"
        if normalized not in urls:
            urls.append(normalized)
        if len(urls) >= limit:
            break
    return urls




def save_competitor_listing(scan_id, product_key, url, listing=None, error_text=""):
    """Store one competitor page result (price found, or the reason it failed)."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as connection:
        if listing is None:
            connection.execute(
                """
                INSERT INTO competitor_listings (
                    scan_id, product_key, url, fetched_at, status, error_text
                ) VALUES (?, ?, ?, ?, 'failed', ?)
                ON CONFLICT(scan_id, product_key, url) DO UPDATE SET
                    fetched_at=excluded.fetched_at, status='failed',
                    error_text=excluded.error_text
                """,
                (scan_id, product_key, url, fetched_at, str(error_text)[:500]),
            )
        else:
            connection.execute(
                """
                INSERT INTO competitor_listings (
                    scan_id, product_key, url, title, price, currency,
                    description, image_url, fetched_at, status, error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', '')
                ON CONFLICT(scan_id, product_key, url) DO UPDATE SET
                    title=excluded.title, price=excluded.price,
                    currency=excluded.currency, description=excluded.description,
                    image_url=excluded.image_url, fetched_at=excluded.fetched_at,
                    status='ok', error_text=''
                """,
                (
                    scan_id,
                    product_key,
                    listing["url"],
                    str(listing.get("title") or "")[:300],
                    listing.get("price"),
                    listing.get("currency"),
                    str(listing.get("description") or "")[:2000],
                    listing.get("image"),
                    fetched_at,
                ),
            )
        connection.commit()
    return fetched_at


def collect_competitor_prices_from_ads(scan_id, product_key):
    """Follow the product's Meta ads to the advertiser pages and read prices."""
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        ads = [
            dict(row)
            for row in connection.execute(
                "SELECT ad_id, page_name, link_caption, snapshot_url "
                "FROM meta_ads WHERE scan_id = ? AND product_key = ?",
                (scan_id, product_key),
            )
        ]
        lookup = connection.execute(
            "SELECT status, error_text FROM meta_ad_lookups "
            "WHERE scan_id = ? AND product_key = ?",
            (scan_id, product_key),
        ).fetchone()
    if not ads:
        if lookup is None:
            raise LookupError(
                "The Meta ad lookup for this product has not run yet. Identify the "
                "product and let the ad lookup finish first."
            )
        if lookup["status"] == "failed":
            raise LookupError(
                f"Meta returned no ads for this product: {lookup['error_text']}"
            )
        raise LookupError(
            "Meta returned no active ads for this product, so there is no competitor ad to follow."
        )
    urls = competitor_urls_from_ads(ads)
    if not urls:
        raise ValueError(
            "The returned ads do not publish a destination address, so there is no competitor page to follow."
        )
    results = []
    for url in urls:
        try:
            listing = fetch_competitor_listing(url)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, LookupError) as error:
            message = (
                str(error)
                or f"{type(error).__name__}: competitor page could not be read."
            )
            save_competitor_listing(scan_id, product_key, url, error_text=message)
            results.append(
                {"url": url, "status": "failed", "error": str(message)[:300]}
            )
            continue
        save_competitor_listing(scan_id, product_key, listing["url"], listing=listing)
        results.append(
            {
                "url": listing["url"],
                "status": "ok",
                "title": listing.get("title"),
                "price": listing.get("price"),
                "currency": listing.get("currency"),
            }
        )
    return results


def identify_product(scan_id, product_key, name, method="typed", confidence=None, alternatives=None, specs=None):
    """Record the real product name for an unverified offer title.

    Identified products are scored and searched in the Meta Ad Library, so this
    is the switch that lets ad lookups run for review-candidate posts. The
    method and any photo confidence are stored so the product page can show how
    the name was arrived at and offer a correction. The extracted specifications
    are stored with the identification so the product profile can show what the
    product is and the Meta search is targeted at the right product.

    Args:
        specs: A list of {"label": ..., "value": ..., "unit": ...}? dicts captured
            for the product, as rendered from the supplier offers and the
            photo/web reader. If None, no specs are recorded for this
            identification.
    """
    identified_name = " ".join(str(name or "").split())
    if len(identified_name) > 200:
        raise ValueError("Product names must be 200 characters or fewer.")
    new_key = normalize_product(identified_name)
    if not new_key:
        raise ValueError("Enter the product name so it can be identified.")
    with closing(sqlite3.connect(DB_PATH)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM messages WHERE scan_id = ? AND normalized_product = ? LIMIT 1",
            (scan_id, product_key),
        ).fetchone()
        if not exists:
            raise LookupError("That product is not part of the current scan.")
        connection.execute(
            """
            UPDATE messages
            SET product = ?, normalized_product = ?, review_candidate = 0
            WHERE scan_id = ? AND normalized_product = ?
            """,
            (identified_name, new_key, scan_id, product_key),
        )
        connection.execute(
            "UPDATE telegram_image_index SET product = ?, product_key = ? WHERE product_key = ?",
            (identified_name, new_key, product_key),
        )
        connection.execute(
            """
            INSERT INTO product_identifications (
                scan_id, product_key, previous_key, method, confidence,
                alternatives_json, specs_json, identified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scan_id, product_key) DO UPDATE SET
                previous_key = excluded.previous_key,
                method = excluded.method,
                confidence = excluded.confidence,
                alternatives_json = excluded.alternatives_json,
                specs_json = excluded.specs_json,
                identified_at = excluded.identified_at
            """,
            (
                scan_id,
                new_key,
                product_key,
                method,
                confidence,
                json.dumps(alternatives or []),
                json.dumps(specs or []),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
    return new_key


# Photo-based identification -------------------------------------------------
# Telegram offer posts are frequently in a language the text recognizer cannot
# name, but almost all of them ship a supplier photo. These models turn that
# photo into a product name, which is exactly the key the Meta Ad Library
# lookup needs.
IMAGE_MATCHER_MODEL = "openai/clip-vit-base-patch32"
IMAGE_MATCHER_DIR = DATA_DIR / "models" / "clip-vit-base-patch32"
IMAGE_CLASSIFIER_MODEL = "microsoft/resnet-50"
IMAGE_CLASSIFIER_DIR = DATA_DIR / "models" / "resnet-50"

# Short, concrete product names. They are used both as the identified product
# name and as the Ad Library search term, so they stay two or three words.
PRODUCT_IMAGE_LABELS = (
    "wireless earbuds", "bluetooth speaker", "power bank", "usb charger",
    "led light bulb", "led strip light", "smart watch", "phone case",
    "hdmi cable", "extension cord", "power strip", "car charger",
    "solar panel", "electric kettle", "hair dryer", "electric razor",
    "electric toothbrush", "air fryer", "blender", "coffee maker",
    "rice cooker", "electric fan", "vacuum cleaner", "clothes iron",
    "water pump", "air compressor", "electric generator",
    "cordless drill", "wrench set", "screwdriver set", "socket set",
    "pliers", "tape measure", "paint brush", "hand tool kit",
    "stainless steel water bottle", "thermos flask", "kitchen knife",
    "dinnerware set", "food storage container", "cooler box",
    "picture frame", "wall clock", "alarm clock", "clothes hanger",
    "door lock", "shower head", "bath towel", "bed sheet", "floor carpet",
    "cushion pillow", "laundry basket", "mop and broom",
    "perfume bottle", "shampoo bottle", "soap bar", "lipstick",
    "makeup brush set", "sunscreen lotion", "hand cream", "nail polish",
    "razor blades", "sheet face mask", "massage gun", "hair clipper",
    "barber scissors", "tattoo machine", "manicure nail kit",
    "t-shirt", "hoodie", "sneakers", "leather belt", "wallet",
    "backpack", "baseball cap", "casual dress", "denim jeans",
    "wool socks", "winter scarf",
    "car wax", "car cover", "car vacuum", "motorcycle helmet",
    "bicycle lock", "bicycle pump", "car phone holder",
    "motorcycle gloves", "engine oil bottle",
    "yoga mat", "dumbbell set", "resistance band", "swimming goggles",
    "camping tent", "sleeping bag", "flashlight",
    "laptop stand", "usb hub", "wireless mouse", "mechanical keyboard",
    "printer paper", "spiral notebook", "stapler", "scissors",
    "marker pens", "packing tape", "document folder",
    "flower pot", "candle holder", "wall sticker", "garden watering can",
    "chicken feed bag", "seeds packet", "wheelbarrow", "sprayer bottle",
)

# Fallback vocabulary for plain classifiers (ImageNet labels -> product names).
IMAGE_NET_PRODUCT_NAMES = {
    "water bottle": "stainless steel water bottle",
    "beer bottle": "glass bottle",
    "wine bottle": "glass bottle",
    "coffee mug": "ceramic coffee mug",
    "cup": "plastic cup",
    "coffee pot": "coffee maker",
    "teapot": "tea pot",
    "washbasin": "wash basin",
    "toilet tissue": "toilet paper roll",
    "packet": "packaged product",
    "plastic bag": "plastic packaging bag",
    "shopping basket": "shopping basket",
    "mailbox": "mailbox",
    "backpack": "backpack",
    "handbag": "handbag",
    "suitcase": "suitcase",
    "wallet": "wallet",
    "sunglasses": "sunglasses",
    "shoe shop": "shoes",
    "sandal": "sandals",
    "clog": "shoes",
    "running shoe": "running shoes",
    "notebook": "spiral notebook",
    "book jacket": "notebook cover",
    "envelope": "document envelope",
    "packet of paper": "printer paper",
    "pencil box": "pencil case",
    "ballpoint": "ballpoint pens",
    "scissors": "scissors",
    "stapler": "stapler",
    "hole punch": "hole punch",
    "cellphone": "smartphone",
    "hand-held computer": "tablet",
    "laptop": "laptop",
    "monitor": "computer monitor",
    "screen": "computer monitor",
    "keyboard": "computer keyboard",
    "computer mouse": "computer mouse",
    "speaker": "bluetooth speaker",
    "microphone": "microphone",
    "headphone": "headphones",
    "CD player": "media player",
    "radio": "portable radio",
    "iPod": "mp3 player",
    "electric guitar": "electric guitar",
    "printer": "printer",
    "modem": "wifi router",
    "hair drier": "hair dryer",
    "electric razor": "electric shaver",
    "electric toothbrush": "electric toothbrush",
    "power plug": "wall charger",
    "power strip": "power strip",
    "light bulb": "led light bulb",
    "spotlight": "led spotlight",
    "lamp shade": "lamp shade",
    "ceiling fan": "ceiling fan",
    "electric fan": "electric fan",
    "kettle": "electric kettle",
    "waffle iron": "waffle maker",
    "toaster": "toaster",
    "microwave oven": "microwave oven",
    "slow cooker": "rice cooker",
    "coffee maker": "coffee maker",
    "stove": "gas stove",
    "vacuum cleaner": "vacuum cleaner",
    "iron": "clothes iron",
    "hair spray": "hair spray",
    "perfume": "perfume bottle",
    "sunscreen": "sunscreen lotion",
    "lotion bottle": "lotion bottle",
    "pill bottle": "pill bottle",
    "nail polish": "nail polish",
    "face powder": "face powder",
    "lipstick": "lipstick",
    "brush": "makeup brush",
    "packet of cigarettes": "cigarettes",
    "bottlecap": "bottle cap",
    "bucket": "plastic bucket",
    "wash bottle": "cleaning spray bottle",
    "dishwasher": "dishwasher",
    "refrigerator": "refrigerator",
    "crock pot": "slow cooker",
    "plate": "dinner plate",
    "soup bowl": "soup bowl",
    "mixing bowl": "mixing bowl",
    "measuring cup": "measuring jug",
    "wooden spoon": "kitchen utensil",
    "spatula": "kitchen spatula",
    "ladle": "kitchen ladle",
    "neck brace": "neck support",
    "crutch": "crutch",
    "barbershop": "barber chair",
    "notebook computer": "laptop",
    "envelope opener": "letter opener",
    "safety pin": "safety pins",
    "hook": "wall hook",
    "padlock": "padlock",
    "key": "door key",
    "bottlebrush": "bottle cleaning brush",
    "electric guitar": "electric guitar",
    "bicycle-built-for-two": "tandem bicycle",
    "mountain bike": "mountain bicycle",
    "bicycle": "bicycle",
    "unicycle": "unicycle",
    "motor scooter": "scooter",
    "moped": "moped",
    "go-kart": "go kart",
    "tractor": "tractor",
    "trailer truck": "truck",
    "car wheel": "car wheel",
    "grille": "car grille",
    "convertible": "convertible car",
    "car mirror": "car mirror",
    "convertible car": "convertible car",
    "gas pump": "fuel pump",
    "parking meter": "parking meter",
    "steel drum": "steel drum",
    "mailbox": "mailbox",
    "bucket": "plastic bucket",
    "dog": "dog",
    "cat": "cat",
}

_image_matcher = None
_image_matcher_kind = ""


_image_matcher_lock = threading.Lock()


def load_image_matcher():
    """Load the photo classifier used to name a product, once per process.

    CLIP is preferred because it scores a photo against our own product
    vocabulary. A plain ImageNet classifier is used when only that is present.
    """
    global _image_matcher, _image_matcher_kind
    if _image_matcher is not None:
        return _image_matcher, _image_matcher_kind
    with _image_matcher_lock:
        if _image_matcher is not None:
            return _image_matcher, _image_matcher_kind
        if (IMAGE_MATCHER_DIR / "config.json").is_file() and (
            IMAGE_MATCHER_DIR / "pytorch_model.bin"
        ).is_file():
            _image_matcher = pipeline(
                "zero-shot-image-classification", model=str(IMAGE_MATCHER_DIR)
            )
            _image_matcher_kind = "clip"
            return _image_matcher, _image_matcher_kind
        if (IMAGE_CLASSIFIER_DIR / "config.json").is_file() and (
            IMAGE_CLASSIFIER_DIR / "model.safetensors"
        ).is_file():
            _image_matcher = pipeline("image-classification", model=str(IMAGE_CLASSIFIER_DIR))
            _image_matcher_kind = "imagenet"
            return _image_matcher, _image_matcher_kind
        raise RuntimeError(
            "Photo identification is not installed. Download the photo model once with: "
            "python -c \"from huggingface_hub import snapshot_download; "
            "snapshot_download('openai/clip-vit-base-patch32', local_dir='data/models/clip-vit-base-patch32')\""
        )


def product_image_choices(image_path, limit=4):
    """Rank product names for one supplier photo."""
    matcher, kind = load_image_matcher()
    with Image.open(image_path) as handle:
        image = handle.convert("RGB")
        if kind == "clip":
            ranked = matcher(image, candidate_labels=list(PRODUCT_IMAGE_LABELS))
            choices = [
                {"label": str(item["label"]), "score": round(float(item["score"]), 3)}
                for item in sorted(ranked, key=lambda item: float(item["score"]), reverse=True)
            ]
        else:
            scored = matcher(image, top_k=40)
            merged = {}
            for item in scored:
                name = IMAGE_NET_PRODUCT_NAMES.get(str(item["label"]).strip().lower())
                if not name:
                    continue
                merged[name] = max(merged.get(name, 0.0), float(item["score"]))
            choices = [
                {"label": name, "score": round(score, 3)}
                for name, score in sorted(merged.items(), key=lambda pair: pair[1], reverse=True)
            ]
    if not choices:
        raise ValueError("The photo did not match any known product category.")
    return choices[:limit]


def product_photo_candidates(scan_id, product_key):
    with closing(sqlite3.connect(DB_PATH)) as connection:
        rows = connection.execute(
            """SELECT image_name FROM messages
               WHERE scan_id = ? AND normalized_product = ?
                 AND image_name IS NOT NULL AND image_name != ''""",
            (scan_id, product_key),
        ).fetchall()
    paths = []
    for (image_name,) in rows:
        path = IMAGE_DIR / Path(str(image_name)).name
        if path.is_file():
            paths.append(path)
    # The largest stored photo is usually the clearest view of the product.
    paths.sort(key=lambda item: item.stat().st_size, reverse=True)
    return paths


# Reading every photo of an offer keeps one odd picture from naming the whole
# product, and a save needs a winner that clearly beats the runner-up.
PHOTO_IDENTIFICATION_MAX_PHOTOS = 5
PHOTO_IDENTIFICATION_MIN_MARGIN = 0.15  # must clearly beat the runner-up


_PHOTO_SAVE_MINIMUM_SCORE = 0.50


def merge_photo_choices(per_photo):
    """Combine the names read from every photo of one offer.

    A label earns the mean of the scores it got across the offer's photos and
    zero for a photo that did not list it, so a name that only fits one of
    several pictures cannot win on the strength of that single picture. The
    returned counts say how often each label was a single photo's top guess,
    which is what decides whether the photos actually agree.
    """
    totals, tops = {}, {}
    for choices in per_photo:
        for item in choices:
            label = str(item["label"])
            totals[label] = totals.get(label, 0.0) + float(item["score"])
        if choices:
            leader = str(choices[0]["label"])
            tops[leader] = tops.get(leader, 0) + 1
    photos = max(1, len(per_photo))
    merged = sorted(
        (
            {"label": label, "score": round(total / photos, 3)}
            for label, total in totals.items()
        ),
        key=lambda item: (-item["score"], item["label"]),
    )
    return merged, tops


def identify_product_from_image(scan_id, product_key, minimum_score=0.50, save=True):
    """Name an unverified offer from its supplier photos.

    Every stored photo of the offer is read, not just the largest one, and the
    name only saves when the photos agree on it and it clearly beats the
    runner-up. With ``save`` the winning label becomes the product name, which
    clears the review flag so the score, the Meta Ad Library lookup and
    competitor price checks can run. Without it the ranked names are only
    returned, so the photo can be read first and the name confirmed before
    anything is saved.
    """
    with closing(sqlite3.connect(DB_PATH)) as connection:
        exists = connection.execute("SELECT 1 FROM messages WHERE scan_id = ? AND normalized_product = ? LIMIT 1",
            (scan_id, product_key),
        ).fetchone()
    if not exists:
        raise LookupError("That product is not part of the current scan.")
    photos = product_photo_candidates(scan_id, product_key)[:PHOTO_IDENTIFICATION_MAX_PHOTOS]
    if not photos:
        raise LookupError(
            "This offer has no stored photo, so the product cannot be named from an image."
        )
    per_photo = [product_image_choices(path) for path in photos]
    choices, tops = merge_photo_choices(per_photo)
    if not choices:
        raise ValueError("The photo did not match any known product category.")
    best = choices[0]
    if best["score"] < minimum_score:
        alternatives = ", ".join(
            f"{choice['label']} ({choice['score']:.0%})" for choice in choices[1:]
        )
        raise ValueError(
            f"The photo was not a confident match for a known product "
            f"(best guess “{best['label']}”, {best['score']:.0%}). "
            f"Closest alternatives: {alternatives or 'none'}. "
            "Type the product name above instead."
        )
    # Saving a name has to hold up across every photo: the photos must agree on
    # the winner and it must beat the runner-up by a clear margin. A preview
    # (save=False) stays lenient so the ranked names remain clickable.
    if save:
        if len(per_photo) > 1 and tops.get(best["label"], 0) * 2 <= len(per_photo):
            read_as = [photo[0]["label"] for photo in per_photo if photo]
            raise ValueError(
                "The offer's photos disagree about the product "
                f"(read as {' / '.join(read_as)}). Nothing was saved — pick the "
                "right name below, or type it."
            )
        runner_up = choices[1]["score"] if len(choices) > 1 else 0.0
        if best["score"] - runner_up < PHOTO_IDENTIFICATION_MIN_MARGIN:
            raise ValueError(
                f"“{best['label']}” ({best['score']:.0%}) is too close to "
                f"“{choices[1]['label']}” ({runner_up:.0%}) to name this product "
                "automatically. Check the photos and confirm the name below."
            )
    # Saving a photo name (interactive button or auto sweep) needs a
    # clearly-confident winner, not just a bare 0.30 guess.
    if save and best["score"] < _PHOTO_SAVE_MINIMUM_SCORE:
        raise ValueError(
            f"“{best['label']}” ({best['score']:.0%}) is not confident enough to name "
            "this product automatically. Pick the right name below, or type it."
        )
    new_key = (
        identify_product(
            scan_id,
            product_key,
            best["label"],
            method="photo",
            confidence=best["score"],
            alternatives=choices[1:],
        )
        if save
        else product_key
    )
    return {
        "saved": bool(save),
        "key": new_key,
        "name": best["label"],
        "score": best["score"],
        "choices": choices,
    }


# Automatic photo identification -------------------------------------------
# Review candidates are the norm in Arabic supplier channels, so naming them is
# a routine step. The sweep below names every candidate that has a stored
# supplier photo, in the background, after a scan finishes and whenever the
# results are opened, so the Meta Ad Library lookup can run without the user
# having to name each product by hand.

# The sweep saves a name automatically, which clears the review flag so the
# score, the Meta Ad Library lookup and the competitor checks then run on it.
# Weak photo guesses used to pass at 0.30 and were saved as confident names,
# which is what sent lookups after the wrong product. Auto-saving now needs a
# clearly confident match. The interactive "read it from the photo" button
# still ranks its suggestions from 0.30, so the user keeps the lenient path.
AUTO_IDENTIFY_MINIMUM_SCORE = 0.50


# Interactive photo reads used to save a name at 0.30, which sent Meta Ad
# Library lookups off to the wrong product. The lenient 0.30 floor is now only
# used to RANK candidates for the user to pick from; saving a name from a photo
# (interactive or automatic) needs the same clearly-confident 0.50 match the
# auto sweep requires, so a weak guess can never become the key the Ad Library is
# searched for.
_PHOTO_SAVE_MINIMUM_SCORE = 0.50


def photo_identification_installed():
    return (IMAGE_MATCHER_DIR / "config.json").is_file() and (
        IMAGE_MATCHER_DIR / "pytorch_model.bin"
    ).is_file()


def pending_photo_candidates(scan_id):
    """Review candidates in one scan that still have a stored supplier photo."""
    if not scan_id:
        return []
    with closing(sqlite3.connect(DB_PATH)) as connection:
        rows = connection.execute(
            """SELECT normalized_product FROM messages
               WHERE scan_id = ? AND review_candidate = 1
                 AND image_name IS NOT NULL AND image_name != ''
               GROUP BY normalized_product ORDER BY normalized_product""",
            (scan_id,),
        ).fetchall()
    return [row[0] for row in rows]


def auto_identify_product(scan_id, product_key):
    """Name one review candidate from the web, falling back to its photo.

    The web is asked first because the supplier's own words find real product
    names and page details; the local photo model only answers when the web
    cannot (offline, no results, or not enough agreement).
    """
    web_error = ""
    try:
        return identify_product_from_web(scan_id, product_key)
    except (LookupError, ValueError, RuntimeError, OSError) as error:
        web_error = str(error)
    try:
        result = identify_product_from_image(
            scan_id, product_key, minimum_score=AUTO_IDENTIFY_MINIMUM_SCORE
        )
    except (LookupError, ValueError, RuntimeError, OSError) as error:
        return {"error": f"Web research: {web_error} Photo reading: {error}"}
    if web_error:
        result["note"] = (
            f"Web research could not name this offer ({web_error}); "
            "it was named from the photo instead."
        )
    return result


def start_photo_identification(scan_id):
    """Name every pending candidate of a scan from its photo, in the background."""
    if not scan_id or not photo_identification_installed():
        return False
    pending = pending_photo_candidates(scan_id)
    if not pending:
        return False
    with _photo_identify_lock:
        if _photo_identify_state["running"] and _photo_identify_state["scan_id"] == scan_id:
            return False
        _photo_identify_cancel.clear()
        _photo_identify_state.update(
            running=True,
            scan_id=scan_id,
            checked=0,
            total=len(pending),
            identified=0,
            unreadable=0,
            message=f"Naming {len(pending)} products from their supplier photos…",
        )
    thread = threading.Thread(
        target=run_photo_identification,
        args=(scan_id, pending),
        name="photo-identification",
        daemon=True,
    )
    thread.start()
    return True


def run_photo_identification(scan_id, pending):
    identified = 0
    unreadable = 0
    stopped_early = False
    try:
        for index, product_key in enumerate(pending, start=1):
            if _photo_identify_cancel.is_set():
                stopped_early = True
                break
            with _photo_identify_lock:
                _photo_identify_state.update(
                    checked=index - 1,
                    message=f"Naming product {index} of {len(pending)} from its photo…",
                )
            result = auto_identify_product(scan_id, product_key)
            if result.get("saved"):
                identified += 1
            else:
                unreadable += 1
            with _photo_identify_lock:
                _photo_identify_state.update(checked=index, identified=identified)
    except Exception as error:  # a single bad photo must not stop the sweep
        unreadable += 1
        with _photo_identify_lock:
            _photo_identify_state["message"] = f"Photo identification stopped early: {error}"
    message = (
        f"Named {identified} of {len(pending)} products from their photos."
        if not stopped_early
        else f"Stopped after naming {identified} of {len(pending)} products."
    )
    if unreadable:
        message += f" {unreadable} could not be read and stayed as review offers."
    with _photo_identify_lock:
        _photo_identify_state.update(
            running=False,
            identified=identified,
            unreadable=unreadable,
            message=message,
        )
    if identified and not stopped_early:
        # Newly named products can now be searched in the Ad Library.
        try:
            start_meta_ads_sync(scan_id, load_results())
        except Exception:
            pass


def purge_weak_product_identifications():
    """Remove weak photo names that were saved in earlier sweeps or by hand.

    A photo identification whose confidence is below the save threshold must not
    remain as the key the Meta Ad Library is searched for. Earlier code allowed
    0.30 saves, so existing databases can carry poisoned product keys. This
    removes those rows once, lazily, the next time results are loaded — without
    touching any name the user actually corrected or confirmed (those are
    current message product values, not stale low-confidence photo rows).
    """
    with closing(sqlite3.connect(DB_PATH)) as connection:
        # Photo rows that are too weak to have been a real save under the current
        # threshold. We keep any row whose product_key still matches a live message
        # product, because that means the user (or a later correct sweep) settled
        # on it.
        connection.execute(
            """
            DELETE FROM product_identifications
            WHERE method = 'photo'
              AND confidence < ?
              AND product_key NOT IN (
                  SELECT DISTINCT normalized_product
                  FROM messages
                  WHERE normalized_product != ''
              )
            """,
            (_PHOTO_SAVE_MINIMUM_SCORE,),
        )
        connection.commit()


def photo_identification_summary(scan_id):
    with _photo_identify_lock:
        state = dict(_photo_identify_state)
    matches_scan = state["scan_id"] == scan_id
    return {
        "installed": photo_identification_installed(),
        "running": bool(state["running"] and matches_scan),
        "checked": state["checked"] if matches_scan else 0,
        "total": state["total"] if matches_scan else 0,
        "identified": state["identified"] if matches_scan else 0,
        "unreadable": state["unreadable"] if matches_scan else 0,
        "pending": len(pending_photo_candidates(scan_id)),
        "message": state["message"] if matches_scan else "",
    }




# Web product research ---------------------------------------------------
# Product names and details come from the live web through the ddgs GitHub
# package (DuckDuckGo text search): no API keys, no models, no downloads. The
# supplier's own post text is the query, and the matching shop pages become
# the product details injected into the product page. The photo model only
# answers when the web cannot.

WEB_RESEARCH_SOURCE = "duckduckgo"
WEB_RESEARCH_MINIMUM_CONFIDENCE = 0.65


# Web research can be confidently wrong: a noisy Arabic post whose fallback query
# is the product key can match a search result whose title overlaps that key, yet
# the "named" product is still wrong (for example a stock-photo tag or a listicle
# title). Auto-saving a web name therefore needs both a higher confidence and a
# concrete product phrase, not just overlap with the query.
WEB_AUTO_SAVE_MINIMUM_CONFIDENCE = 0.85
WEB_RESEARCH_MAX_RESULTS = 8
WEB_RESEARCH_SOURCE_LIMIT = 4
WEB_RESEARCH_PAUSE_SECONDS = 1.0
# How many shopping/competitor sites to ask for each product the web names,
# so the identification is not left to one page. Each source contributes its
# own brand, model and specifications, and the best answer wins.
WEB_RESEARCH_SOURCE_LIMIT = 4
# Promo filler and supplier catchphrases that appear in post titles and shop
# titles but carry no product meaning. They are dropped from search queries,
# from title scoring and from the saved product name, so a title like
# "Power Bank : تسوق اونلاين بأفضل الاسعار في السعودية" saves as "Power Bank".
_WEB_NOISE_WORDS = {
    # Arabic promo filler from shopping sites and suppliers.
    "تسوق", "تسوقنا", "اونلاين", "أونلاين", "اون لاين", "أفضل", "افضل",
    "الأسعار", "الاسعار", "أسعار", "اسعار", "عروض", "العروض", "وفى",
    "في", "من", "على", "و", "جديد", "الجديد", "مستعمل", "المستعمل",
    "السعودية", "السعوديه", "الامارات", "الإمارات", "الكويت", "الرياض",
    "جدة", "دبي", "متجر", "متاجر", "منتج", "منتجات", "توصيل", "شحن",
    "مجاني", "مجانية", "خصم", "تخفيض", "تخفيضات", "الان", "الآن", "الي",
    # Supplier catchphrases (Algerian wholesale posts).
    "متراطيش", "لافار", "لاتفوت", "لاتفوتها", "فرصة", "بسعر", "للبيع",
    "الف", "دينار", "درهم", "سعر", "السعر",
    # English/Spanish/French promo and stopwords.
    "shop", "online", "best", "price", "prices", "disponible", "disponibles",
    "the", "and", "for", "with", "from", "this", "that", "buy", "now",
    "cheap", "sale", "wholesale", "vente", "gros", "prix", "acheter",
    "pour", "chaque", "avec", "dans", "meilleur", "meilleure",
    # Shop hosts and domains are navigation, never the product name.
    "com", "www", "net", "org", "amazon", "ebay", "walmart", "aliexpress",
    "noon", "temu", "alibaba", "shopify", "etsy", "wish",
}


def web_noise_tokens(text):
    """Meaningful product tokens: everything except promo/stopword noise."""
    tokens = [
        token
        for token in re.findall(r"[\w\u0600-\u06FF]{3,}", str(text or "").casefold())
        if not token.isdigit()
    ]
    meaningful = [token for token in tokens if token not in _WEB_NOISE_WORDS]
    # A query made only of filler still needs *some* token to match on.
    return meaningful or tokens
_web_research_lock = threading.Lock()
_web_research_state = {
    "running": False,
    "scan_id": None,
    "checked": 0,
    "total": 0,
    "identified": 0,
    "failed": 0,
    "message": "",
}


def web_search_text(query, limit=WEB_RESEARCH_MAX_RESULTS):
    """Live web results for one query, through the ddgs package (no key)."""
    try:
        from ddgs import DDGS
    except ImportError as error:
        raise RuntimeError(
            "Web research needs the ddgs package. Install it with: "
            ".venv/Scripts/pip install ddgs"
        ) from error
    # Stay polite to the search service: one pause before every request.
    time.sleep(WEB_RESEARCH_PAUSE_SECONDS)
    try:
        results = DDGS().text(query, max_results=limit)
    except Exception as error:
        raise RuntimeError(
            f"The web search failed ({type(error).__name__}): {error}"
        ) from error
    return [dict(item) for item in results if isinstance(item, dict)]


def clean_web_title(title):
    """Reduce a search result title to the product name it contains."""
    text = " ".join(str(title or "").split())
    if not text:
        return ""
    # Snippets often glue a product name straight onto a shop name:
    # "…4 Port Car ChargerAmazon com UGREEN…" → split at the capital letter.
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    # Drop a trailing site suffix: "Hair Clipper :Amazon.ae" → "Hair Clipper".
    text = re.sub(
        r"\s*[:|•]\s*(?:www\.)?[\w.-]+\.[a-z]{2,}(?:\.[a-z]{2})?\s*$",
        "",
        text,
        flags=re.I,
    )
    fragments = [
        fragment.strip(" -–—·•\t")
        for fragment in re.split(r"\s*[|•]\s*|\s+[-–—]\s+", text)
    ]
    fragments = [
        fragment
        for fragment in fragments
        if len(fragment) > 3
        and not re.fullmatch(
            r"(?:www\.)?[\w.-]+\.[a-z]{2,}(?:\.[a-z]{2})?", fragment, re.I
        )
    ]
    if not fragments:
        return text[:120]
    if len(fragments) > 1:
        # "Amazon com UGREEN 150W…" is navigation, not the product name.
        shop_led = [
            fragment
            for fragment in fragments
            if re.match(
                r"^(?:www\.)?[\w -]{2,20}?\s+(?:com|net|org|io|co|ae|sa|fr|dz|es)\b",
                fragment,
                flags=re.I,
            )
        ]
        if len(shop_led) < len(fragments):
            fragments = [
                fragment
                for fragment in fragments
                if fragment not in shop_led
            ]
    # The longest fragment is the product name; the rest is navigation noise.
    return max(fragments, key=len)[:120]


def web_query_candidates(query, fallback_query=""):
    """Searchable versions of one supplier post, simplest one last.

    Supplier posts carry emoji, phone numbers and mixed-language noise that
    DuckDuckGo answers with "no results", so the post is reduced step by step
    — cleaned text, then its words, then the Latin words only, then the
    product name itself — until a search engine answers.
    """
    raw = " ".join(str(query or "").split())
    cleaned = " ".join(re.sub(r"[^\w\u0600-\u06FF]+", " ", raw).split())
    words = [
        word
        for word in re.findall(r"[\w\u0600-\u06FF]{3,}", cleaned)
        if re.search(r"[^\W\d_]", word)  # keep words that contain letters
        and word.casefold() not in _WEB_NOISE_WORDS  # drop promo filler
    ]
    if not words:
        words = [
            word
            for word in re.findall(r"[\w\u0600-\u06FF]{3,}", cleaned)
            if re.search(r"[^\W\d_]", word)
        ]
    candidates = []

    def add(text):
        text = " ".join(str(text or "").split())
        if len(text) >= 3 and text.casefold() not in {
            item.casefold() for item in candidates
        }:
            candidates.append(text)

    # The product words without promo filler first: one clean search usually
    # answers, instead of three noisy queries timing out.
    add(" ".join(words))
    add(" ".join(words[:6]))
    add(" ".join(w for w in words if re.search(r"[A-Za-z]", w)))
    add(" ".join(str(fallback_query or "").split()))
    return [candidate for candidate in candidates if len(candidate) >= 3]


def web_product_research(query, limit=WEB_RESEARCH_MAX_RESULTS, fallback_query=""):
    """Research one product: canonical name, description and source pages."""
    query = " ".join(str(query or "").split())
    if len(query) < 3 and len(" ".join(str(fallback_query or "").split())) < 3:
        raise ValueError("There is not enough product text to research on the web.")
    candidates = web_query_candidates(query, fallback_query)
    if not candidates:
        raise ValueError("There is not enough product text to research on the web.")
    results, used_query, errors = [], candidates[0], []
    for candidate in candidates:
        try:
            found = [
                item
                for item in web_search_text(candidate, limit=limit)
                if str(item.get("title") or "").strip()
                and str(item.get("href") or "").strip()
            ]
        except RuntimeError as error:
            errors.append(str(error))
            continue
        if found:
            results, used_query = found, candidate
            break
    if not results:
        if errors and len(errors) == len(candidates):
            raise RuntimeError(errors[-1])
        raise ValueError(f"The web returned no results for “{query[:80]}”.")
    # The name comes from the title that actually talks about this product,
    # not simply the first result: shopping sites love returning taglines and
    # category pages that name nothing.
    query_text = " ".join((used_query, query))
    query_tokens = web_noise_tokens(query_text)
    scored = []
    for item in results:
        title = clean_web_title(item.get("title"))
        if not title:
            continue
        haystack = title.casefold()
        overlap = sum(1 for token in query_tokens if token in haystack)
        scored.append((overlap, title, item))
    if not scored:
        raise ValueError("The web results did not contain a usable product name.")
    # Highest overlap wins; among equals prefer a real product title over a
    # listicle and the shorter title over a concatenated snippet.
    best_overlap, best_title, best_item = max(
        scored,
        key=lambda entry: (
            entry[0],
            not web_title_is_listicle(entry[1]),
            -len(entry[1]),
        ),
    )
    # A buying guide is never the product name — "15 best smartwatches…" only
    # wins when nothing else in the results even mentions this product.
    if web_title_is_listicle(best_title):
        real = [
            entry
            for entry in scored
            if not web_title_is_listicle(entry[1]) and entry[0] >= 1
        ]
        if real:
            best_overlap, best_title, best_item = max(
                real,
                key=lambda entry: (entry[0], -len(entry[1])),
            )
    if best_overlap == 0:
        raise ValueError(
            "The web results did not match this offer, so nothing was saved. "
            "Type the product name above instead."
        )
    name = strip_web_noise(best_title)
    # A name must be an actual phrase: "أل ف" (price-fragment residue) is not.
    if not re.search(r"[\w\u0600-\u06FF]{3,}", name):
        raise ValueError(
            "The web results did not match this offer, so nothing was saved. "
            "Type the product name above instead."
        )

    def agrees(item):
        haystack = f"{item.get('title') or ''} {item.get('body') or ''}".casefold()
        return any(token in haystack for token in query_tokens)

    agreeing = sum(1 for item in results if agrees(item))
    confidence = round(min(0.95, 0.45 + 0.5 * agreeing / len(results)), 3)
    sources, seen = [], set()
    for item in results:
        url = str(item.get("href") or "").strip()
        if url in seen or not url.startswith(("http://", "https://")):
            continue
        seen.add(url)
        sources.append(
            {
                "title": clean_web_title(item.get("title")) or url,
                "url": url,
                "domain": (urlparse(url).hostname or "").removeprefix("www."),
            }
        )
        if len(sources) >= 5:
            break
    return {
        "name": name,
        "brand": "",
        "category": "",
        "description": " ".join(str(best_item.get("body") or "").split())[:600],
        "specs": [],
        "sources": sources,
        "confidence": confidence,
    }


def strip_web_noise(text):
    """Remove promo filler and price noise from a title, keeping its wording."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    # Prices never belong in a product name: "bouilloire électrique 280 الف".
    text = re.sub(r"\d[\d\s.,]*\s*(?:أ?لف|دينار|درهم|دج|da)\b", " ", text, flags=re.I)
    # A shop host is never the product: "Amazon.com: UGREEN 150W…" → "UGREEN…",
    # both in its dotted form and in the space-glued snippet form.
    text = re.sub(
        r"^(?:www\.)?[\w-]+(?:\.[\w-]+)+\s*[:.-]?\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"^(?:www\.)?[\w -]{2,20}?\s+(?:com|net|org|io|co|ae|sa|fr|dz|es)\b[:\s]+",
        "",
        text,
        flags=re.I,
    )
    parts = re.findall(r"[\w\u0600-\u06FF]+", text)
    kept = [part for part in parts if part.casefold() not in _WEB_NOISE_WORDS]
    if not kept:
        return " ".join(text.split())
    if len(kept) == len(parts):
        return " ".join(text.split())
    return " ".join(kept)


def web_title_is_listicle(title):
    """Buying guides and reviews are not product names."""
    return bool(
        re.search(
            r"^(?:\d+\s+)?(?:best|top)\b|\bguide\b|\breview\b|"
            r"\bunder\s*\d+\b|\bcomplete guide\b|\b buying \b|"
            r"^دليل\b|\bدليل\b|\bتحليل\b|\bmejores\b|\bmejor\b",
            title,
            flags=re.I,
        )
    )


def web_research_query(scan_id, product_key):
    """The supplier's own words for this offer: the best web query there is."""
    with closing(sqlite3.connect(DB_PATH)) as connection:
        rows = connection.execute(
            """SELECT product, message_text FROM messages
               WHERE scan_id = ? AND normalized_product = ?
               ORDER BY message_date LIMIT 3""",
            (scan_id, product_key),
        ).fetchall()
    if not rows:
        raise LookupError("That product is not part of the current scan.")
    pieces = []
    for product, message_text in rows:
        for line in str(message_text or "").splitlines():
            line = " ".join(line.split())
            if len(line) >= 4:
                if line not in pieces:
                    pieces.append(line)
                break
        title = " ".join(str(product or "").split())
        if title and title not in pieces:
            pieces.append(title)
    query = " ".join(pieces)[:220]
    if not query.strip():
        raise ValueError("This offer has no text to research on the web.")
    return query


def identify_product_from_web(scan_id, product_key, save=True):
    """Name one offer from what the web says about the supplier's own text.

    The name is always returned for the user to confirm. Auto-saving it (which
    clears the review flag and triggers the Meta Ad Library lookup) needs both a
    higher confidence and a concrete product phrase, because a high-overlap web
    title can still be the wrong product.
    """
    query = web_research_query(scan_id, product_key)
    detail = web_product_research(query, fallback_query=product_key)
    if detail["confidence"] < WEB_RESEARCH_MINIMUM_CONFIDENCE:
        raise ValueError(
            "The web results for this offer did not agree enough to name it "
            f"({detail['confidence']:.0%} confidence). Type the product name, "
            "or read it from the photo instead."
        )
    if save and (
        detail["confidence"] < WEB_AUTO_SAVE_MINIMUM_CONFIDENCE
        or not _looks_like_concrete_product(detail["name"])
    ):
        raise ValueError(
            f"The web named this offer “{detail['name']}” "
            f"({detail['confidence']:.0%} confidence), but that is not confident "
            "enough to save automatically. Type the product name, read it from the "
            "photo, or confirm it above."
        )
    payload = {
        "saved": bool(save),
        "key": product_key,
        "name": detail["name"],
        "score": detail["confidence"],
        "choices": [{"label": detail["name"], "score": detail["confidence"]}],
        "source": WEB_RESEARCH_SOURCE,
        "details": detail,
    }
    if save:
        payload["key"] = identify_product(
            scan_id,
            product_key,
            detail["name"],
            method="web",
            confidence=detail["confidence"],
            specs=detail.get("specs"),
        )
        save_product_details(
            scan_id, payload["key"], WEB_RESEARCH_SOURCE, "", detail
        )
    return payload


def pending_web_research(scan_id):
    """Products named from a photo that the web has not confirmed yet."""
    if not scan_id:
        return []
    with closing(sqlite3.connect(DB_PATH)) as connection:
        rows = connection.execute(
            """
            SELECT identification.product_key
            FROM product_identifications AS identification
            WHERE identification.scan_id = ? AND identification.method = 'photo'
              AND NOT EXISTS (
                  SELECT 1 FROM product_details AS details
                  WHERE details.scan_id = identification.scan_id
                    AND details.product_key = identification.product_key
              )
              AND EXISTS (
                  SELECT 1 FROM messages AS message
                  WHERE message.scan_id = identification.scan_id
                    AND message.normalized_product = identification.product_key
              )
            ORDER BY identification.product_key
            """,
            (scan_id,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def start_web_research(scan_id):
    """Re-check photo-named products against the web, in the background."""
    if not scan_id:
        return False
    pending = pending_web_research(scan_id)
    if not pending:
        return False
    with _web_research_lock:
        if (
            _web_research_state["running"]
            and _web_research_state["scan_id"] == scan_id
        ):
            return False
        _web_research_state.update(
            running=True,
            scan_id=scan_id,
            checked=0,
            total=len(pending),
            identified=0,
            failed=0,
            message=f"Researching {len(pending)} products on the web…",
        )
    threading.Thread(
        target=run_web_research,
        args=(scan_id, pending),
        name="web-research",
        daemon=True,
    ).start()
    return True


def run_web_research(scan_id, pending):
    """Confirm or correct photo-given names, one product at a time."""
    identified = failed = 0
    last_error = ""
    try:
        for index, product_key in enumerate(pending, start=1):
            with _web_research_lock:
                _web_research_state.update(
                    checked=index - 1,
                    message=f"Researching product {index} of {len(pending)} on the web…",
                )
            try:
                identify_product_from_web(scan_id, product_key)
                identified += 1
            except (LookupError, ValueError, RuntimeError, OSError) as error:
                failed += 1
                last_error = str(error)
            with _web_research_lock:
                _web_research_state.update(
                    checked=index, identified=identified, failed=failed
                )
    except Exception as error:  # one failure must not stop the sweep
        failed += 1
        last_error = str(error)
    message = (
        f"Web research finished: {identified} of {len(pending)} products "
        "confirmed or corrected"
    )
    if failed:
        message += f", {failed} left unchanged"
        if last_error:
            message += f" (last: {last_error[:120]})"
    message += "."
    with _web_research_lock:
        _web_research_state.update(
            running=False, identified=identified, failed=failed, message=message
        )
    if identified:
        try:
            start_meta_ads_sync(scan_id, load_results())
        except Exception:
            pass


def web_research_summary(scan_id):
    with _web_research_lock:
        state = dict(_web_research_state)
    matches_scan = state["scan_id"] == scan_id
    return {
        "running": bool(state["running"] and matches_scan),
        "checked": state["checked"] if matches_scan else 0,
        "total": state["total"] if matches_scan else 0,
        "identified": state["identified"] if matches_scan else 0,
        "failed": state["failed"] if matches_scan else 0,
        "pending": len(pending_web_research(scan_id)),
        "message": state["message"] if matches_scan else "",
    }


def save_product_details(scan_id, product_key, source, model, details):
    """Store the researched product details shown on the product page."""
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.execute(
            """
            INSERT INTO product_details (
                scan_id, product_key, source, model, name, brand, category,
                description, specs_json, sources_json, confidence, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scan_id, product_key) DO UPDATE SET
                source = excluded.source,
                model = excluded.model,
                name = excluded.name,
                brand = excluded.brand,
                category = excluded.category,
                description = excluded.description,
                specs_json = excluded.specs_json,
                sources_json = excluded.sources_json,
                confidence = excluded.confidence,
                fetched_at = excluded.fetched_at
            """,
            (
                scan_id,
                product_key,
                source,
                model,
                details.get("name", ""),
                details.get("brand", ""),
                details.get("category", ""),
                details.get("description", ""),
                json.dumps(details.get("specs") or []),
                json.dumps(details.get("sources") or []),
                details.get("confidence"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()


def load_product_details(scan_id, product_key):
    """The stored AI description shown on the product page, if there is one."""
    if not scan_id or not product_key:
        return None
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM product_details WHERE scan_id = ? AND product_key = ?",
            (scan_id, product_key),
        ).fetchone()
    if not row:
        return None
    payload = dict(row)
    for column, field in (("specs_json", "specs"), ("sources_json", "sources")):
        try:
            payload[field] = json.loads(payload.pop(column) or "[]")
        except ValueError:
            payload[field] = []
    return payload


# Image-based "same product, all sellers" --------------------------------
# Alibaba-style photo search: the product page compares the product's own
# photos with every photo indexed from the supplier channels, so sellers are
# found by the picture itself even when their post titles the product
# something else. Two signals are used: an identical reposted photo (dHash,
# the same rule as the image-search tool, instant) and a CLIP embedding
# similarity for different photos of one product (cached per photo, computed
# in the background). Calibrated against the stored scan photos: identical
# reposts score 0.98+, while unrelated products in the same category sit
# between 0.74 and 0.86 (a novel and a brush set both matched a clothing
# offer at 0.75), so the visual gate is 0.90 — below that only an identical
# photo counts as the same product.

SAME_PRODUCT_MINIMUM_SCORE = 0.90
SAME_PHOTO_MAX_DISTANCE = 12
SAME_SELLER_LIMIT = 50
PHOTO_EMBEDDING_BATCH = 16
PHOTO_EMBEDDING_VERSION = IMAGE_MATCHER_MODEL

_photo_embed_lock = threading.Lock()
_photo_embed_state = {"running": False, "checked": 0, "total": 0, "message": ""}


def pack_embedding(vector):
    """Store a photo embedding as a compact float32 blob."""
    return array("f", vector).tobytes()


def unpack_embedding(blob):
    values = array("f")
    values.frombytes(bytes(blob))
    return values


def encode_photo_embeddings(paths):
    """CLIP image embeddings (L2-normalized) for the given photo paths.

    One entry per path, in order; an unreadable photo maps to ``None``. Raises
    when the CLIP photo model is not installed so callers can fall back to
    hash matching alone.
    """
    matcher, kind = load_image_matcher()
    if kind != "clip":
        raise RuntimeError(
            "Visual product matching needs the CLIP photo model. Download it once with: "
            "python -c \"from huggingface_hub import snapshot_download; "
            "snapshot_download('openai/clip-vit-base-patch32', local_dir='data/models/clip-vit-base-patch32')\""
        )
    import torch  # already required by transformers; kept lazy for start-up

    vectors = []
    for start in range(0, len(paths), PHOTO_EMBEDDING_BATCH):
        images = []
        for path in paths[start : start + PHOTO_EMBEDDING_BATCH]:
            try:
                with Image.open(path) as handle:
                    images.append(handle.convert("RGB"))
            except (OSError, ValueError, UnidentifiedImageError):
                images.append(None)
        feed = [image or Image.new("RGB", (32, 32), "white") for image in images]
        try:
            inputs = matcher.image_processor(images=feed, return_tensors="pt")
            with torch.no_grad():
                output = matcher.model.get_image_features(**inputs)
            features = getattr(output, "pooler_output", None)
            if features is None:
                features = getattr(output, "image_embeds", None)
            if features is None:
                features = output
            if not hasattr(features, "norm"):
                raise RuntimeError("The photo model returned an unexpected embedding.")
            features = features / features.norm(dim=-1, keepdim=True)
            rows = features.float().tolist()
        finally:
            for image in feed:
                image.close()
        vectors.extend(
            None if image is None else row for image, row in zip(images, rows)
        )
    return vectors


def store_photo_embeddings(paths, vectors):
    """Cache the embeddings computed for photo files that exist on disk."""
    saved = 0
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(DB_PATH)) as connection:
        for path, vector in zip(paths, vectors):
            if vector is None:
                continue
            connection.execute(
                """
                INSERT INTO photo_embeddings (image_name, model_version, embedding, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(image_name) DO UPDATE SET
                    model_version = excluded.model_version,
                    embedding = excluded.embedding,
                    created_at = excluded.created_at
                """,
                (Path(path).name, PHOTO_EMBEDDING_VERSION, pack_embedding(vector), now),
            )
            saved += 1
        connection.commit()
    return saved


def pending_photo_embeddings():
    """Indexed Telegram photos that still need a cached embedding."""
    with closing(sqlite3.connect(DB_PATH)) as connection:
        rows = connection.execute(
            """
            SELECT i.image_name FROM telegram_image_index AS i
            LEFT JOIN photo_embeddings AS e
              ON e.image_name = i.image_name AND e.model_version = ?
            WHERE e.image_name IS NULL
            ORDER BY i.message_date DESC
            """,
            (PHOTO_EMBEDDING_VERSION,),
        ).fetchall()
    return [
        str(row[0])
        for row in rows
        if (IMAGE_DIR / Path(str(row[0])).name).is_file()
    ]


def photo_embedding_counts():
    """How much of the photo index has been read for visual matching."""
    with closing(sqlite3.connect(DB_PATH)) as connection:
        indexed = connection.execute(
            "SELECT COUNT(*) FROM photo_embeddings WHERE model_version = ?",
            (PHOTO_EMBEDDING_VERSION,),
        ).fetchone()[0]
        total = connection.execute(
            "SELECT COUNT(DISTINCT image_name) FROM telegram_image_index"
        ).fetchone()[0]
    return int(indexed), int(total)


def start_photo_embedding():
    """Cache embeddings for every indexed photo, in the background."""
    try:
        _, kind = load_image_matcher()
    except RuntimeError:
        return False
    if kind != "clip":
        return False
    pending = pending_photo_embeddings()
    if not pending:
        return False
    with _photo_embed_lock:
        if _photo_embed_state["running"]:
            return False
        _photo_embed_state.update(
            running=True,
            checked=0,
            total=len(pending),
            message=f"Reading {len(pending)} photos for image matching…",
        )
    threading.Thread(
        target=run_photo_embedding,
        args=(pending,),
        name="photo-embedding",
        daemon=True,
    ).start()
    return True


def run_photo_embedding(pending):
    """Read the photo index in batches, so product pages can compare fast."""
    total = len(pending)
    checked = 0
    try:
        while pending:
            batch = pending[:PHOTO_EMBEDDING_BATCH]
            pending = pending[PHOTO_EMBEDDING_BATCH:]
            paths = [IMAGE_DIR / Path(str(name)).name for name in batch]
            vectors = encode_photo_embeddings(paths)
            store_photo_embeddings(paths, vectors)
            checked += len(batch)
            with _photo_embed_lock:
                _photo_embed_state.update(
                    checked=checked,
                    message=f"Read {checked} of {total} photos for image matching…",
                )
    except Exception as error:  # a single bad batch must not kill the index
        with _photo_embed_lock:
            _photo_embed_state.update(
                running=False,
                message=f"Photo indexing stopped: {error}",
            )
        return
    with _photo_embed_lock:
        _photo_embed_state.update(
            running=False,
            checked=checked,
            message=f"Photo index ready: {checked} photos read.",
        )


def cosine_scores(query_vectors, candidate_vectors):
    """Best cosine similarity of each candidate against the query photos."""
    if not query_vectors or not candidate_vectors:
        return []
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with transformers
        return [
            max(sum(a * b for a, b in zip(query, candidate)) for query in query_vectors)
            for candidate in candidate_vectors
        ]
    queries = np.asarray(query_vectors, dtype=np.float32)
    matrix = np.asarray(candidate_vectors, dtype=np.float32)
    return (matrix @ queries.T).max(axis=1).astype(float).tolist()


def _ensure_message_photos_indexed():
    """Seed the photo index from stored scans, but only when rows are missing."""
    with closing(sqlite3.connect(DB_PATH)) as connection:
        missing = connection.execute(
            """
            SELECT COUNT(*) FROM messages AS m
            WHERE m.image_name IS NOT NULL AND m.image_name != ''
              AND NOT EXISTS (
                  SELECT 1 FROM telegram_image_index AS i
                  WHERE i.image_name = m.image_name
              )
            """
        ).fetchone()[0]
    if missing:
        seed_saved_telegram_images()


def same_product_sellers(scan_id, product_key):
    """Every other seller posting this product's photo, Alibaba-style.

    The current product's photos are compared with the indexed channel photos:
    an identical reposted photo is a certain match, and a CLIP similarity of at
    least ``SAME_PRODUCT_MINIMUM_SCORE`` covers different photos of the same
    product. Offers already listed on the product page are left out, so this
    adds sellers whose post used another title or came from a channel outside
    the scan.
    """
    result = {
        "status": "ready",
        "matches": [],
        "identical": 0,
        "visual": 0,
        "indexed": 0,
        "total": 0,
        "pending": 0,
        "running": False,
        "threshold": SAME_PRODUCT_MINIMUM_SCORE,
        "visual_available": True,
        "reason": "",
    }
    photos = product_photo_candidates(scan_id, product_key)[
        :PHOTO_IDENTIFICATION_MAX_PHOTOS
    ]
    if not photos:
        result["visual_available"] = False
        result["reason"] = (
            "This product has no stored photo, so there is nothing to search by image."
        )
        return result
    _ensure_message_photos_indexed()
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        listed = {
            (row[0], row[1])
            for row in connection.execute(
                """
                SELECT chat_id, message_id FROM messages
                WHERE scan_id = ? AND normalized_product = ?
                """,
                (scan_id, product_key),
            ).fetchall()
        }
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT chat_id, message_id, chat_title, product, product_key,
                       supplier, price, currency, message_date, telegram_url,
                       image_name, image_hash
                FROM telegram_image_index
                """
            ).fetchall()
        ]
    query_hashes = []
    for path in photos:
        try:
            query_hashes.append(int(image_dhash_bytes(path.read_bytes()), 16))
        except ValueError:
            continue

    # The product's own photos: cached embeddings when possible, computed now
    # otherwise, so the first open of a product page still matches visually.
    query_vectors = []
    with closing(sqlite3.connect(DB_PATH)) as connection:
        cached = dict(
            connection.execute(
                """
                SELECT image_name, embedding FROM photo_embeddings
                WHERE model_version = ? AND image_name IN (?, ?, ?, ?, ?)
                """,
                (PHOTO_EMBEDDING_VERSION, *[path.name for path in photos])
                + ("",) * (5 - len(photos)),
            ).fetchall()
        )
    missing_paths = [path for path in photos if path.name not in cached]
    if missing_paths:
        try:
            computed = encode_photo_embeddings(missing_paths)
        except (RuntimeError, OSError, ValueError) as error:
            computed = None
            result["visual_available"] = False
            result["reason"] = str(error)
        if computed is not None:
            store_photo_embeddings(missing_paths, computed)
            for path, vector in zip(missing_paths, computed):
                if vector is not None:
                    cached[path.name] = pack_embedding(vector)
    query_vectors = [
        unpack_embedding(cached[path.name])
        for path in photos
        if path.name in cached
    ]

    embedded = {}
    if query_vectors:
        with closing(sqlite3.connect(DB_PATH)) as connection:
            for image_name, blob in connection.execute(
                "SELECT image_name, embedding FROM photo_embeddings WHERE model_version = ?",
                (PHOTO_EMBEDDING_VERSION,),
            ).fetchall():
                embedded[str(image_name)] = unpack_embedding(blob)
    width = len(query_vectors[0]) if query_vectors else 0
    scored_names = [
        name
        for name, vector in embedded.items()
        if len(vector) == width
    ]
    scores = cosine_scores(query_vectors, [embedded[name] for name in scored_names])
    similarity_by_name = dict(zip(scored_names, scores))

    best_by_chat = {}
    for row in rows:
        if (row["chat_id"], row["message_id"]) in listed:
            continue
        distance = None
        try:
            candidate_hash = int(str(row["image_hash"]), 16)
            distance = min(
                (candidate_hash ^ query_hash).bit_count()
                for query_hash in query_hashes
            )
        except (TypeError, ValueError):
            pass
        identical = distance is not None and distance <= SAME_PHOTO_MAX_DISTANCE
        similarity = similarity_by_name.get(str(row["image_name"]))
        if not identical and (
            similarity is None or similarity < SAME_PRODUCT_MINIMUM_SCORE
        ):
            continue
        row["identical"] = bool(identical)
        row["distance"] = distance if identical else None
        row["channel"] = row.get("chat_title") or ""
        row["similarity"] = (
            round(similarity, 3) if similarity is not None else None
        )
        row["same_title"] = str(row.get("product_key") or "") == product_key
        previous = best_by_chat.get(row["chat_id"])
        if previous is None or (
            (row["identical"], row["similarity"] or 0.0)
            > (previous["identical"], previous["similarity"] or 0.0)
        ):
            best_by_chat[row["chat_id"]] = row

    matches = sorted(
        best_by_chat.values(),
        key=lambda row: (
            not row["identical"],
            -(row["similarity"] or 0.0),
            str(row.get("message_date") or ""),
        ),
    )[:SAME_SELLER_LIMIT]
    result["matches"] = matches
    result["identical"] = sum(1 for row in matches if row["identical"])
    result["visual"] = sum(1 for row in matches if not row["identical"])
    result["indexed"], result["total"] = photo_embedding_counts()
    pending = pending_photo_embeddings()
    result["pending"] = len(pending)
    if pending and result["visual_available"]:
        start_photo_embedding()
    with _photo_embed_lock:
        result["running"] = bool(_photo_embed_state["running"])
        if result["running"]:
            result["reason"] = _photo_embed_state["message"]
            result["status"] = "indexing"
    if not result["visual_available"] and not result["reason"]:
        result["reason"] = "Visual photo matching is unavailable on this computer."
    return result


def save_meta_ads_lookup(scan_id, product_key, ads=None, error_text=""):
    fetched_at = datetime.now(timezone.utc).isoformat()
    status = "failed" if error_text else "ok"
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.execute(
            "DELETE FROM meta_ads WHERE scan_id = ? AND product_key = ?",
            (scan_id, product_key),
        )
        if ads:
            connection.executemany(
                """
                INSERT INTO meta_ads (
                    scan_id, product_key, ad_id, page_name, creative_text,
                    creative_title, delivery_start, delivery_stop, platforms_json,
                    snapshot_url, link_caption, link_url, cta_text, image_url,
                    video_url, thumbnail_url, languages, display_format,
                    ad_status, is_active, currency, spend, impressions, reach,
                    fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        scan_id,
                        product_key,
                        ad["ad_id"],
                        ad["page_name"],
                        ad["creative_text"],
                        ad["creative_title"],
                        ad["delivery_start"],
                        ad["delivery_stop"],
                        json.dumps(ad["platforms"]),
                        ad["snapshot_url"],
                        ad.get("link_caption", ""),
                        ad.get("link_url", ""),
                        ad.get("cta_text", ""),
                        ad.get("image_url", ""),
                        ad.get("video_url", ""),
                        ad.get("thumbnail_url", ""),
                        ad.get("languages", ""),
                        ad.get("display_format", ""),
                        ad.get("ad_status", ""),
                        ad.get("is_active", 0),
                        ad.get("currency", ""),
                        ad.get("spend", ""),
                        ad.get("impressions", ""),
                        ad.get("reach", ""),
                        fetched_at,
                    )
                    for ad in ads
                ),
            )
        connection.execute(
            """
            INSERT INTO meta_ad_lookups (
                scan_id, product_key, status, ad_count, error_text, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(scan_id, product_key) DO UPDATE SET
                status=excluded.status, ad_count=excluded.ad_count,
                error_text=excluded.error_text, fetched_at=excluded.fetched_at
            """,
            (scan_id, product_key, status, len(ads or []), error_text, fetched_at),
        )
        connection.commit()


# How many products are looked up at once. One lookup is a network call that
# costs seconds, so a batch used to take roughly (products x seconds). Each
# worker thread opens its own collector session, because one session cannot be
# shared safely across threads.
META_ADS_CONCURRENCY = 4


def run_meta_ads_sync(scan_id, products):
    identified = [product for product in products if not product["candidate_only"]]
    with _meta_ads_lock:
        _meta_ads_state.update(
            running=True,
            scan_id=scan_id,
            checked=0,
            total=len(identified),
            message="Searching Meta Ad Library for active ads…",
        )
    stop_early = threading.Event()

    def lookup_one(product):
        if stop_early.is_set() or _meta_ads_cancel.is_set():
            return
        with closing(sqlite3.connect(DB_PATH)) as connection:
            cached = connection.execute(
                "SELECT status FROM meta_ad_lookups WHERE scan_id = ? AND product_key = ?",
                (scan_id, product["key"]),
            ).fetchone()
        if not (cached and cached[0] == "ok"):
            try:
                ads = fetch_meta_ads(product["product"], product=product)
                save_meta_ads_lookup(scan_id, product["key"], ads=ads)
            except (RuntimeError, OSError, ValueError) as error:
                save_meta_ads_lookup(scan_id, product["key"], error_text=str(error))
                print(f"Meta Ad Library lookup failed for {product['product']}: {error}", flush=True)
                if classify_meta_error(str(error))[0] in ("permission", "blocked", "expired"):
                    # Every remaining product would be refused the same way, so
                    # stop instead of spending the rest of the batch on refusals.
                    stop_early.set()
        with _meta_ads_lock:
            _meta_ads_state["checked"] += 1
            _meta_ads_state["message"] = (
                f"Checked {_meta_ads_state['checked']} of {_meta_ads_state['total']} products "
                "in Meta Ad Library."
            )

    try:
        if identified:
            # The first product is looked up on its own. If Meta refuses this app
            # the whole batch would be refused identically, so one call settles
            # that (and catches a cancellation) before any parallel work starts.
            lookup_one(identified[0])
            rest = identified[1:]
            if rest and not stop_early.is_set() and not _meta_ads_cancel.is_set():
                with ThreadPoolExecutor(
                    max_workers=min(META_ADS_CONCURRENCY, len(rest))
                ) as pool:
                    list(pool.map(lookup_one, rest))
        with _meta_ads_lock:
            if _meta_ads_cancel.is_set():
                _meta_ads_state["message"] = (
                    "Meta ad lookup paused. Resume from Reports to continue."
                )
            elif stop_early.is_set():
                remaining = max(0, len(identified) - _meta_ads_state["checked"])
                _meta_ads_state["message"] = (
                    "Meta refused access for this app or token, so the remaining "
                    f"{remaining} product(s) were not searched. Use Diagnose in the Meta dialog."
                )
    finally:
        with _meta_ads_lock:
            _meta_ads_state["running"] = False
        prune_meta_collectors()


def meta_lookup_available():
    """True when Meta ad lookup can run on this machine.

    The scraper needs no token or app review, so availability is simply
    whether meta-ads-collector is installed.
    """
    return MetaAdsCollector is not None


def classify_meta_error(error_text):
    """Classify a Meta Ad Library error into a category and human-readable message.

    Returns a (category, message) tuple. Categories are:

    * ``"ok"`` — the lookup succeeded.
    * ``"missing"`` — the collector package is not installed.
    * ``"permission"`` — Meta refused access for this app or token.
    * ``"blocked"`` — Meta blocked the request (bot detection, rate limit).
    * ``"expired"`` — the session or token has expired.
    * ``"network"`` — a network or connection problem, retry may help.
    * ``"other"`` — anything else.

    Some of these categories describe problems that can no longer occur now that
    the official Graph API path is gone, but the classifier is kept because it is
    still wired into the batch runner and the diagnose endpoint.
    """
    text = str(error_text or "").lower()
    if "not installed" in text or "no module" in text:
        return "missing", "The meta-ads-collector package is not installed."
    if "permission" in text or "does not have permission" in text or "app review" in text:
        return "permission", "Meta refused access for this app or token."
    if "blocked" in text or "rate limit" in text or "too many requests" in text:
        return "blocked", "Meta blocked the request."
    if "expired" in text or "session expired" in text or "token expired" in text:
        return "expired", "The Meta session or token has expired."
    if "connection" in text or "timeout" in text or "network" in text or "name or service" in text:
        return "network", "A network problem prevented the lookup."
    return "other", "Meta Ad Library lookup failed."


def start_meta_ads_sync(scan_id, products):
    if not meta_lookup_available():
        return
    identified_count = sum(not product["candidate_only"] for product in products)
    with _meta_ads_lock:
        if _meta_ads_state["running"] and _meta_ads_state["scan_id"] == scan_id:
            return
        _meta_ads_cancel.clear()
        _meta_ads_state.update(
            running=True,
            scan_id=scan_id,
            checked=0,
            total=identified_count,
            message="Searching Meta Ad Library for active ads…",
        )
        thread = threading.Thread(
            target=run_meta_ads_sync,
            args=(scan_id, products),
            name="meta-ad-library-sync",
            daemon=True,
        )
        thread.start()


def meta_ads_summary(scan_id):
    with closing(sqlite3.connect(DB_PATH)) as connection:
        counts = connection.execute(
            """
            SELECT COUNT(*),
                (SELECT COUNT(DISTINCT ad_id) FROM meta_ads WHERE scan_id = ?),
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)
            FROM meta_ad_lookups WHERE scan_id = ?
            """,
            (scan_id, scan_id),
        ).fetchone()
    with _meta_ads_lock:
        state = dict(_meta_ads_state)
    return {
        "configured": meta_lookup_available(),
        "products_checked": counts[0] or 0,
        "active_ads_found": counts[1] or 0,
        "failed_products": counts[2] or 0,
        "running": state["running"] and state["scan_id"] == scan_id,
        "progress_checked": state["checked"] if state["scan_id"] == scan_id else 0,
        "progress_total": state["total"] if state["scan_id"] == scan_id else 0,
        "message": state["message"] if state["scan_id"] == scan_id else "",
    }


def calculate_test_priority(telegram_score, evidence):
    components = {}
    ads = evidence.get("active_ads")
    if ads is not None:
        components["meta_ads"] = 10 if ads == 0 else 18 if ads <= 3 else 14 if ads <= 15 else 8
    ad_age = evidence.get("oldest_ad_days")
    if ad_age is not None and ads:
        components["ad_longevity"] = 20 if ad_age >= 30 else 15 if ad_age >= 14 else 10 if ad_age >= 7 else 5
    views = evidence.get("social_views")
    interactions = evidence.get("social_interactions")
    if views is not None or interactions is not None:
        view_count = max(0, views or 0)
        interaction_count = max(0, interactions or 0)
        reach_points = min(14, math.log10(view_count + 1) * 3.5)
        interaction_rate = interaction_count / max(1, view_count)
        components["social_engagement"] = min(20, round(reach_points + min(6, interaction_rate * 100), 1))
    if not components:
        return telegram_score, {"telegram_baseline": telegram_score}, 0
    weights = {
        "meta_ads": 0.25,
        "ad_longevity": 0.2,
        "social_engagement": 0.3,
    }
    external_score = sum(components[key] * weights[key] for key in components) / sum(
        weights[key] for key in components
    ) * 5
    score = round(telegram_score * 0.65 + external_score * 0.35)
    components["telegram_baseline"] = telegram_score
    return score, components, len(components) - 1


# Weak photo names that earlier code saved at 0.30 must never be the key an
# Ad Library lookup runs on. The cleanup is idempotent, so it runs once per
# scan instead of on every results poll.
_purged_identifications_lock = threading.Lock()
_purged_identifications_for = set()


def purge_weak_product_identifications_once(scan_id):
    with _purged_identifications_lock:
        if scan_id in _purged_identifications_for:
            return False
        _purged_identifications_for.add(scan_id)
    purge_weak_product_identifications()
    return True


# ---------------------------------------------------------------------------
# Product intelligence: status, ad matching, pricing, trend and gallery.
#
# Every scan produces offer posts, and the dashboard asks the same four
# questions about each product it turns into: what state is it in, which ads
# really belong to it, what is it worth, and which images back it up. The
# answers are computed here so the results table and the product page can never
# disagree about the same product.
# ---------------------------------------------------------------------------

PRODUCT_STATUS_IDENTIFIED = "identified"
PRODUCT_STATUS_AWAITING_REVIEW = "awaiting_review"
PRODUCT_STATUS_PROCESSING = "processing"
PRODUCT_STATUS_FAILED = "failed"

PRODUCT_STATUS_LABELS = {
    PRODUCT_STATUS_IDENTIFIED: "Identified",
    PRODUCT_STATUS_AWAITING_REVIEW: "Awaiting review",
    PRODUCT_STATUS_PROCESSING: "Identifying\u2026",
    PRODUCT_STATUS_FAILED: "Failed",
}

# An ad belongs in a product's Exact Ads section only when the product's own
# evidence is inside the ad itself: its model number, its brand, its variant and
# most of its name. Category overlap alone — the same search result, a
# competitor, or another model of the same line — is not evidence, so those ads
# go to the Similar Ads section instead. This is what keeps category
# contamination out of the primary ads section.
EXACT_AD_MINIMUM_SCORE = 0.72
SIMILAR_AD_MINIMUM_SCORE = 0.18

_AD_MATCH_STOPWORDS = frozenset(
    word.casefold()
    for word in (
        "and for with from this that new original high quality best hot sale free "
        "shipping wholesale dropshipping buy price shop online official store brand "
        "premium the a an of to in on at by"
    ).split()
) | frozenset(str(word).casefold() for word in PRODUCT_NAME_STOPWORDS)

# Product variants change the product itself: "Item1001 Pro" is a different
# product from "Item1001", so an ad that names a variant the product does not
# have can never be an exact match.
_PRODUCT_VARIANT_WORDS = frozenset(
    "pro plus max mini lite ultra se xl xxl air 5g 4g note prime advanced edition "
    "version gen".split()
)


def _match_tokens(text):
    """Lower-cased, punctuation-free tokens worth matching on."""
    tokens = []
    for raw in re.findall(r"[a-z0-9]+", str(text or "").casefold()):
        if len(raw) < 2 or raw in _AD_MATCH_STOPWORDS:
            continue
        tokens.append(raw)
    return tokens


def product_match_profile(name, brand="", attributes=None):
    """The name, brand, model and variant evidence an ad must contain.

    ``model_tokens`` are the alphanumeric parts of the product name ("1001",
    "a12", "s24"). When the product has them, an ad that does not name them is a
    different product, whatever else it shares.
    """
    tokens = _match_tokens(name)
    if not brand and isinstance(attributes, dict):
        values = attributes.get("brand") or attributes.get("Brand")
        if values:
            brand = values[0] if isinstance(values, (list, tuple)) else values
    brand = str(brand or "")
    model_tokens = {token for token in tokens if any(ch.isdigit() for ch in token)}
    variant_tokens = {token for token in tokens if token in _PRODUCT_VARIANT_WORDS}
    brand_tokens = set(_match_tokens(brand))
    name_tokens = [
        token
        for token in tokens
        if token not in model_tokens
        and token not in variant_tokens
        and token not in brand_tokens
    ]
    # When the name is nothing but a model number ("Item1001"), that model number
    # is the product's identifying token, so it must not also be counted against
    # the name-coverage score that decides an exact match.
    identifying_tokens = name_tokens or [
        token for token in tokens if token not in variant_tokens and token not in brand_tokens
    ]
    return {
        "name": str(name or ""),
        "brand": brand,
        "tokens": tokens,
        "name_tokens": identifying_tokens,
        "model_tokens": model_tokens,
        "variant_tokens": variant_tokens,
        "brand_tokens": brand_tokens,
    }


def score_meta_ad(profile, ad):
    """Score one Ad Library row against one product.

    Returns ``{"level", "score", "reasons", "missing"}``. ``level`` is
    ``"exact"`` when the ad names this product, ``"similar"`` when it shares
    real evidence with it (brand, keywords, packaging wording) and
    ``"unrelated"`` when the only thing it shares is the category.
    """
    haystack = " ".join(
        str(ad.get(key) or "")
        for key in (
            "creative_title",
            "creative_text",
            "page_name",
            "link_url",
            "link_caption",
            "snapshot_url",
        )
    )
    hay_tokens = set(_match_tokens(haystack))
    normalized_hay = " ".join(_match_tokens(haystack))

    missing_models = sorted(profile["model_tokens"] - hay_tokens)
    ad_variants = hay_tokens & _PRODUCT_VARIANT_WORDS
    extra_variants = sorted(ad_variants - profile["variant_tokens"])
    missing_variants = sorted(profile["variant_tokens"] - hay_tokens)
    brand_required = bool(profile["brand_tokens"])
    brand_hit = brand_required and profile["brand_tokens"] <= hay_tokens

    name_tokens = profile["name_tokens"]
    matched = [token for token in name_tokens if token in hay_tokens]
    coverage = len(matched) / len(name_tokens) if name_tokens else 0.0
    full_name = " ".join(_match_tokens(profile["name"]))
    full_hit = len(full_name) > 3 and full_name in normalized_hay

    weights = []
    if brand_required:
        weights.append((0.35, 1.0 if brand_hit else 0.0))
    if profile["model_tokens"]:
        weights.append((0.45, 1.0 if not missing_models else 0.0))
    weights.append((0.2 if profile["model_tokens"] else 0.4, coverage))
    total_weight = sum(weight for weight, _ in weights) or 1.0
    score = sum(weight * value for weight, value in weights) / total_weight
    if full_hit:
        score = min(1.0, score + 0.15)

    exact = (
        score >= EXACT_AD_MINIMUM_SCORE
        and not missing_models
        and not extra_variants
        and not missing_variants
        and (brand_hit or not brand_required)
    )

    reasons = []
    if exact:
        if full_hit:
            reasons.append(f"Names this exact product: {profile['name']}")
        else:
            reasons.append("Names this exact product")
        if brand_hit:
            reasons.append(f"Brand confirmed: {profile['brand']}")
        if matched:
            reasons.append("Product keywords: " + ", ".join(matched[:5]))
    else:
        if extra_variants:
            reasons.append("Different variant of the same line: " + ", ".join(extra_variants))
        if missing_variants:
            reasons.append("Does not name the product variant: " + ", ".join(missing_variants))
        if missing_models:
            reasons.append("Different model, does not name: " + ", ".join(missing_models))
        if brand_hit:
            reasons.append(f"Same brand: {profile['brand']}")
        if matched:
            reasons.append("Shares product keywords: " + ", ".join(matched[:5]))
        if not reasons:
            reasons.append(
                "Same category search result only: no shared product evidence"
                if score < SIMILAR_AD_MINIMUM_SCORE
                else "Returned for this product's search, but does not name it"
            )

    if exact:
        level = "exact"
    else:
        # Everything that is not an exact match belongs in the similar section:
        # related products, competitors and same-category results are all useful
        # there, and the reasons say which one it is. Nothing overlaps with the
        # exact section.
        level = "similar"

    return {
        "level": level,
        "score": round(score, 3),
        "reasons": reasons,
        "missing": missing_models + missing_variants + extra_variants,
    }


def classify_product_ads(profile, ads):
    """Split one product's Ad Library rows into exact and similar ads.

    An exact ad names this product; every other row is a similar-product ad
    (related product, competitor or same-category result) and says why. The two
    sections can therefore never mix, and a repeated ad row is only counted
    once.
    """
    exact = []
    similar = []
    seen = set()
    for ad in ads or []:
        row = dict(ad)
        ad_id = str(row.get("ad_id") or "")
        if ad_id and ad_id in seen:
            continue
        if ad_id:
            seen.add(ad_id)
        match = score_meta_ad(profile, ad)
        row["match"] = match
        if match["level"] == "exact":
            exact.append(row)
        else:
            similar.append(row)
    return exact, similar


def _median(values):
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def product_pricing_estimate(lowest_price, currency, price_unit, listings, offers=None):
    """Estimate the buy cost, the likely selling price and the margin.

    The supplier's own lowest listed price is the cost estimate. The selling
    price estimate is the median of the competitor pages that were actually
    read for this product in the same currency, so it is always traceable to a
    source. Every value is an estimate, and the basis is returned with it.

    When no two offers agree on a currency (which is common on Arabic supplier
    posts, where the currency is in the image or the caption) the cheapest priced
    offer is still used as the cost, and the basis says the currency was not
    detected rather than pretending the number is comparable.
    """
    cost = lowest_price if lowest_price is not None else None
    cost_basis = "Cost: lowest supplier price in this scan" if cost is not None else None
    if cost is None and offers:
        priced = [offer for offer in offers if offer.get("price") is not None]
        if priced:
            cheapest = min(priced, key=lambda offer: offer["price"])
            cost = cheapest["price"]
            currency = cheapest.get("currency") or currency
            price_unit = cheapest.get("price_unit") or price_unit
            cost_basis = "Cost: cheapest listed supplier price (currency not detected)"
    selling_prices = [
        listing.get("price")
        for listing in listings or []
        if listing.get("price") is not None
        and (currency is None or not listing.get("currency") or listing.get("currency") == currency)
    ]
    selling = _median(selling_prices)
    margin_percent = None
    margin_value = None
    if selling and cost is not None and selling > 0:
        margin_value = round(selling - cost, 2)
        margin_percent = round((selling - cost) / selling * 100, 1)
    basis = []
    if cost_basis:
        basis.append(cost_basis)
    if selling is not None:
        basis.append(f"Selling price: median of {len(selling_prices)} competitor page(s)")
    if margin_percent is None:
        basis.append("Margin needs a comparable competitor price")
    return {
        "estimated_cost": cost,
        "estimated_selling_price": selling,
        "estimated_margin_value": margin_value,
        "profit_margin_percent": margin_percent,
        "currency": currency,
        "price_unit": price_unit,
        "selling_price_samples": len(selling_prices),
        "basis": "; ".join(basis),
        "warning": "Estimates from listed prices, not verified sales or verified costs.",
    }


_TREND_DIRECTION_SCORES = {"rising": 78, "stable": 55, "falling": 28}


def product_trend_estimate(research, age_days, period_days, oldest_ad_days, exact_ads):
    """A 0-100 demand-trend estimate with the evidence behind it.

    Prefers the trend direction recorded for the product in market research.
    When that is not entered yet, the freshly posted supplier offers carry the
    estimate, and a long-running active ad adds to it because an advertiser
    that keeps paying for the same product is showing real demand.
    """
    research = research or {}
    direction = str(research.get("trend_direction") or "unknown").casefold()
    basis = []
    if direction in _TREND_DIRECTION_SCORES:
        score = _TREND_DIRECTION_SCORES[direction]
        basis.append(f"Research trend: {direction}")
    else:
        score = int(max(0, min(100, round(100 * (1 - (age_days or 0) / max(1, period_days))))))
        basis.append("Freshness of the newest supplier post")
    if oldest_ad_days:
        if oldest_ad_days >= 30:
            score = min(100, score + 12)
            basis.append(f"An active ad has run {oldest_ad_days} days")
        elif oldest_ad_days >= 14:
            score = min(100, score + 8)
            basis.append(f"An active ad has run {oldest_ad_days} days")
        elif oldest_ad_days >= 7:
            score = min(100, score + 4)
            basis.append(f"An active ad has run {oldest_ad_days} days")
    if exact_ads:
        basis.append(f"{len(exact_ads)} exact-match active ad(s)")
    return {"trend_score": score, "trend_direction": direction, "basis": "; ".join(basis)}


def product_gallery(offers, ads, listings=None, limit=24):
    """Every image that backs this product, in the order a buyer would want them.

    Supplier post photos come first (they are the real product as shipped),
    then the exact-match ad creatives, then similar-ad creatives and finally
    competitor page images. One image appears once.
    """
    items = []
    seen = set()

    def add(url, source, label):
        url = str(url or "").strip()
        if not url or url in seen or len(items) >= limit:
            return
        seen.add(url)
        items.append({"url": url, "source": source, "label": label})

    for offer in offers or []:
        add(offer.get("image"), "supplier", offer.get("supplier") or "Supplier photo")
    for ad in ads or []:
        label = ad.get("page_name") or "Advertiser"
        add(ad.get("image_url"), "ad", label)
        add(ad.get("thumbnail_url"), "ad", label)
        add(ad.get("video_url"), "ad_video", label)
    for listing in listings or []:
        add(listing.get("image_url"), "competitor", listing.get("title") or "Competitor page")
    return items


def product_status_for(scan_id, product_key, candidate_only, identification):
    """The explicit state of one product row, never an implicit "unknown".

    A named product is identified. An unnamed one is either still being read
    (processing), has already failed both readers (failed, with the reason), or
    is waiting for a reader (awaiting review) — and it is shown in the table in
    every one of those states instead of being hidden.
    """
    if not candidate_only:
        if identification and identification.get("method") == "extractor":
            return PRODUCT_STATUS_IDENTIFIED, ""
        return PRODUCT_STATUS_IDENTIFIED, ""
    with _identify_lock:
        same_scan = _identify_state.get("scan_id") == scan_id
        if same_scan and product_key in _identify_state.get("active", set()):
            return PRODUCT_STATUS_PROCESSING, "Queued for identification"
        failure = _identify_state.get("failed", {}).get(product_key) if same_scan else None
    if identification:
        return PRODUCT_STATUS_IDENTIFIED, ""
    if failure:
        return PRODUCT_STATUS_FAILED, failure
    return PRODUCT_STATUS_AWAITING_REVIEW, "Waiting for the web or photo reader"


def identification_source(identification, candidate_only):
    if identification and identification.get("method"):
        return identification["method"]
    return "extractor" if not candidate_only else ""


def scan_integrity(scan_id, products, deep=False):
    """Compare what the scan detected with what the table is showing.

    Returns the counts plus any issue, so a scan can never quietly lose a
    product between the database and the results table. ``detected`` is the
    number of distinct products the scan recorded, ``returned`` is the number of
    table rows built from them.

    The comparison against the scan record is cheap and is what the page shows
    on every refresh. ``deep`` re-counts the products straight from the messages
    table instead; that is a full scan of the scan's rows, so it runs once when
    a scan finishes rather than on every poll.
    """
    products = products or []
    returned = len(products)
    recorded = 0
    scanned_messages = 0
    scan_status = ""
    counted = None
    if scan_id:
        with closing(sqlite3.connect(DB_PATH)) as connection:
            row = connection.execute(
                "SELECT products_found, scanned_messages, status FROM scans WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
            if row:
                recorded = int(row[0] or 0)
                scanned_messages = int(row[1] or 0)
                scan_status = str(row[2] or "")
            if deep:
                deep_row = connection.execute(
                    """
                    SELECT COUNT(DISTINCT normalized_product)
                    FROM messages WHERE scan_id = ?
                    """,
                    (scan_id,),
                ).fetchone()
                counted = int(deep_row[0] or 0)
    live = scan_status == "running"
    detected = returned if counted is None else int(counted)
    issues = []
    if counted is not None and detected != returned:
        issues.append(
            f"{detected} product(s) were detected but {returned} row(s) are shown."
        )
    empty_offers = [product["key"] for product in products if not product.get("offers")]
    if empty_offers:
        issues.append(
            f"{len(empty_offers)} row(s) have no offer attached and need re-scanning."
        )
    return {
        "scan_id": scan_id,
        "detected": detected,
        "returned": returned,
        "recorded_in_scan": recorded,
        "scanned_messages": scanned_messages,
        "scan_status": scan_status,
        "live": live,
        "checked_from_messages": counted is not None,
        "consistent": not issues,
        "issues": issues,
    }


_integrity_lock = threading.Lock()
_integrity_reports = {}


def verify_scan_integrity(scan_id):
    """Run the deep scan-to-table check and remember the verdict.

    Called once when a scan finishes, which is when the answer matters and when
    re-counting the scan's rows is affordable. The verdict is stored so the
    results API can show the last real check instead of an unverified number.
    """
    report = scan_integrity(scan_id, load_results(), deep=True)
    with _integrity_lock:
        _integrity_reports[scan_id] = report
    if not report["consistent"]:
        print(
            f"Scan integrity warning for {scan_id}: " + " ".join(report["issues"]),
            flush=True,
        )
    return report


def last_integrity_report(scan_id):
    if not scan_id:
        return None
    with _integrity_lock:
        return _integrity_reports.get(scan_id)


def count_products_by_status(products):
    counts = {
        PRODUCT_STATUS_IDENTIFIED: 0,
        PRODUCT_STATUS_PROCESSING: 0,
        PRODUCT_STATUS_AWAITING_REVIEW: 0,
        PRODUCT_STATUS_FAILED: 0,
    }
    for product in products or []:
        status = product.get("status") or PRODUCT_STATUS_AWAITING_REVIEW
        counts[status] = counts.get(status, 0) + 1
    counts["total"] = len(products or [])
    return counts


# ---------------------------------------------------------------------------
# Progressive identification.
#
# Identification used to start only after the scan had finished, so products sat
# unnamed for the whole run and were read one at a time. Now every unnamed offer
# post is handed to a worker pool the moment the scan writes it, and the pool
# names products while the scan is still walking the channels, so results appear
# progressively instead of all at the end.
# ---------------------------------------------------------------------------

PROGRESSIVE_IDENTIFY_WORKERS = 2
PROGRESSIVE_IDENTIFY_DRAIN_SECONDS = 45.0

_identify_lock = threading.Lock()
_identify_queue = None
_identify_threads = []
_identify_state = {
    "running": False,
    "scan_id": None,
    "workers": 0,
    "active": set(),
    "done": set(),
    "failed": {},
    "identified": 0,
    "queued": 0,
    "message": "Not started",
}


def _photo_preview(scan_id, product_key):
    """Read a product's photos without saving anything. None when unreadable."""
    try:
        return identify_product_from_image(
            scan_id, product_key, minimum_score=0.30, save=False
        )
    except (LookupError, ValueError, RuntimeError, OSError):
        return None


def _photo_preview_is_save_worthy(preview):
    """Whether a lenient photo preview would have passed the strict save rules.

    The preview path is deliberately lenient (it ranks names for the user to
    pick from), so its winner may be a weak guess. Agreement between sources may
    only be built on a preview that would also have saved on its own.
    """
    if not preview:
        return False
    choices = preview.get("choices") or []
    score = float(preview.get("score") or 0)
    if score < max(AUTO_IDENTIFY_MINIMUM_SCORE, _PHOTO_SAVE_MINIMUM_SCORE):
        return False
    runner_up = float(choices[1]["score"]) if len(choices) > 1 else 0.0
    return score - runner_up >= PHOTO_IDENTIFICATION_MIN_MARGIN


def identify_product_progressive(scan_id, product_key):
    """Identify one product with both readers and cross-check their answers.

    Accuracy layers, in order:

    1. The web reader reads the supplier's own words. A name at or above the
       auto-save floor is cross-checked against the photos: when both readers
       name the same product, that agreement raises the saved confidence, and
       when the photo disagrees the conflict is recorded on the product instead
       of being hidden.
    2. A web name between the research floor and the auto-save floor is only
       saved when the photos independently agree on it, so a borderline web
       guess can never become a product name on its own.
    3. When the web cannot name the offer at all, the photo reader names it
       under the strict save rules (agreement across photos, a clear margin and
       a confident winner).

    Returns ``{"saved": ...}``. A product that no reader can name honestly comes
    back unsaved, with both reasons, so its row shows "Failed" instead of a
    confident wrong name.
    """
    web_result = None
    web_error = ""
    try:
        web_result = identify_product_from_web(scan_id, product_key, save=False)
    except (LookupError, ValueError, RuntimeError, OSError) as error:
        web_error = str(error)

    if web_result is not None:
        web_name = normalize_product(web_result.get("name"))
        web_score = float(web_result.get("score") or 0)
        preview = _photo_preview(scan_id, product_key)
        photo_ok = _photo_preview_is_save_worthy(preview)
        photo_name = normalize_product(preview["name"]) if photo_ok else ""
        agreed = bool(web_name and photo_name and web_name == photo_name)
        high_confidence = web_score >= WEB_AUTO_SAVE_MINIMUM_CONFIDENCE
        if agreed:
            confidence = min(
                1.0,
                0.5
                + 0.25 * web_score
                + 0.25 * float(preview.get("score") or 0),
            )
            return _save_progressive_name(
                scan_id,
                product_key,
                web_result,
                method="web+photo",
                confidence=confidence,
                alternatives=[
                    {"name": web_result["name"], "score": web_score, "source": "web"},
                    {"name": preview["name"], "score": preview["score"], "source": "photo"},
                ],
                note="The web reader and the photo reader named the same product.",
            )
        if not high_confidence:
            return {
                "saved": False,
                "key": product_key,
                "name": web_result.get("name"),
                "score": web_score,
                "error": (
                    f"The web named this offer “{web_result.get('name')}” "
                    f"({web_score:.0%}) but the photos did not confirm it, so it was "
                    "left for review."
                ),
            }
        alternatives = []
        if preview is not None:
            alternatives.append(
                {
                    "name": preview["name"],
                    "score": preview["score"],
                    "source": "photo",
                    "note": "photo reader disagreed",
                }
            )
        note = (
            "The photo reader disagreed; the web name was kept at its own confidence."
            if alternatives
            else ""
        )
        return _save_progressive_name(
            scan_id,
            product_key,
            web_result,
            method=WEB_RESEARCH_SOURCE,
            confidence=web_score,
            alternatives=alternatives,
            note=note,
        )

    # No usable web answer: fall back to the strict photo save path, exactly as
    # the post-scan sweep does.
    try:
        result = identify_product_from_image(
            scan_id, product_key, minimum_score=AUTO_IDENTIFY_MINIMUM_SCORE
        )
    except (LookupError, ValueError, RuntimeError, OSError) as error:
        return {
            "saved": False,
            "key": product_key,
            "error": (
                f"Web research: {web_error or 'no usable result'}. "
                f"Photo reading: {error}"
            ),
        }
    if web_error:
        result["note"] = (
            f"Web research could not name this offer ({web_error}); "
            "it was named from the photo instead."
        )
    return result


def _save_progressive_name(scan_id, product_key, web_result, *, method, confidence, alternatives, note=""):
    """Save a cross-checked name and the details that came with it."""
    new_key = identify_product(
        scan_id,
        product_key,
        web_result["name"],
        method=method,
        confidence=confidence,
        alternatives=alternatives,
        specs=(web_result.get("details") or {}).get("specs"),
    )
    details = web_result.get("details")
    if isinstance(details, dict):
        save_product_details(scan_id, new_key, WEB_RESEARCH_SOURCE, "", details)
    return {
        "saved": True,
        "key": new_key,
        "previous_key": product_key,
        "name": web_result["name"],
        "score": confidence,
        "source": method,
        "note": note,
        "alternatives": alternatives,
    }


def start_progressive_identification(scan_id, workers=PROGRESSIVE_IDENTIFY_WORKERS):
    """Start the identification workers for one scan, before it walks the chats."""
    global _identify_queue, _identify_threads
    if not scan_id:
        return False
    with _identify_lock:
        if _identify_state["running"] and _identify_state["scan_id"] == scan_id:
            return False
        _identify_queue = queue.Queue()
        _identify_threads = []
        _identify_state.update(
            running=True,
            scan_id=scan_id,
            workers=workers,
            active=set(),
            done=set(),
            failed={},
            identified=0,
            queued=0,
            message="Waiting for the first products to identify…",
        )
        work_queue = _identify_queue
    for index in range(max(1, workers)):
        thread = threading.Thread(
            target=run_identification_worker,
            args=(scan_id, work_queue),
            name=f"identify-{index}",
            daemon=True,
        )
        thread.start()
        with _identify_lock:
            _identify_threads.append(thread)
    return True


def enqueue_identification(scan_id, product_keys):
    """Queue products for identification the instant the scan discovers them."""
    keys = [str(key) for key in (product_keys or []) if key]
    if not keys:
        return 0
    with _identify_lock:
        if not _identify_state["running"] or _identify_state["scan_id"] != scan_id:
            return 0
        work_queue = _identify_queue
        fresh = []
        for key in keys:
            if key in _identify_state["active"] or key in _identify_state["done"]:
                continue
            _identify_state["active"].add(key)
            fresh.append(key)
        _identify_state["queued"] += len(fresh)
        if _identify_state["active"]:
            _identify_state["message"] = (
                f"Identifying {len(_identify_state['active'])} product(s) while the scan runs…"
            )
    for key in fresh:
        work_queue.put(key)
    return len(fresh)


def run_identification_worker(scan_id, work_queue):
    """Name queued products until the queue is closed.

    One failure never stops the pool, and every failure is recorded so the row
    can show "Failed" with the reason instead of an unexplained blank identity.
    """
    while True:
        product_key = work_queue.get()
        saved = False
        try:
            if product_key is None:
                return
            result = identify_product_progressive(scan_id, product_key)
            saved = bool(result.get("saved"))
            with _identify_lock:
                _identify_state["active"].discard(product_key)
                if saved:
                    _identify_state["done"].add(product_key)
                    _identify_state["identified"] += 1
                    _identify_state["failed"].pop(product_key, None)
                    _identify_state["message"] = (
                        f"Identified {_identify_state['identified']} product(s) so far "
                        "while the scan runs."
                    )
                else:
                    _identify_state["failed"][product_key] = str(
                        result.get("error")
                        or "The web and photo readers could not name this offer."
                    )[:300]
                    _identify_state["message"] = (
                        f"{len(_identify_state['failed'])} product(s) could not be named "
                        "automatically and await review."
                    )
        except Exception as error:  # one bad product must not stop the pool
            with _identify_lock:
                _identify_state["active"].discard(product_key)
                _identify_state["failed"][product_key] = str(error)[:300]
        finally:
            work_queue.task_done()
        if saved:
            # A newly named product can be searched in the Ad Library right away,
            # so its ads land while the scan is still running.
            try:
                start_meta_ads_sync(scan_id, load_results())
            except Exception:
                pass


def finish_progressive_identification(scan_id, timeout=PROGRESSIVE_IDENTIFY_DRAIN_SECONDS):
    """Let the workers finish what they already hold, then stop them.

    Anything the workers have not reached before the timeout is left to the
    post-scan sweeps, which pick it up from the database, so a product is never
    dropped because a reader was slow.
    """
    global _identify_queue
    with _identify_lock:
        if not _identify_state["running"] or _identify_state["scan_id"] != scan_id:
            return {"identified": 0, "pending": 0, "drained": True}
        work_queue = _identify_queue
        workers = _identify_state["workers"]
        threads = list(_identify_threads)
    for _ in range(max(1, workers)):
        work_queue.put(None)
    deadline = time.monotonic() + max(0.0, timeout)
    for thread in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(remaining)
    drained = not any(thread.is_alive() for thread in threads)
    with _identify_lock:
        identified = _identify_state["identified"]
        pending = len(_identify_state["active"])
        _identify_state["running"] = not drained
        _identify_state["message"] = (
            f"{identified} product(s) were named while the scan ran."
            if drained
            else f"{pending} product(s) are still being identified in the background."
        )
    _identify_queue = None if drained else work_queue
    return {"identified": identified, "pending": pending, "drained": drained}


def progressive_identification_pending(scan_id):
    """How many products are queued or being read right now."""
    with _identify_lock:
        if _identify_state["scan_id"] != scan_id:
            return 0
        return len(_identify_state["active"])


def progressive_identification_summary(scan_id):
    with _identify_lock:
        state = dict(_identify_state)
        active = set(state.get("active") or ())
        failures = len(state.get("failed") or {})
        identified = state.get("identified", 0)
        message = state.get("message", "")
    matches_scan = state.get("scan_id") == scan_id
    return {
        "running": bool(state.get("running") and matches_scan),
        "pending": len(active) if matches_scan else 0,
        "identified": identified if matches_scan else 0,
        "failed": failures if matches_scan else 0,
        "queued": state.get("queued", 0) if matches_scan else 0,
        "message": message if matches_scan else "",
    }


def load_results():
    purge_weak_product_identifications_once(_current_scan_id or "")
    if not _current_scan_id:
        return []
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        scan = connection.execute(
            "SELECT start_date, end_date, market, extractor_version FROM scans WHERE scan_id = ?",
            (_current_scan_id,),
        ).fetchone()
        rows = connection.execute(
            "SELECT * FROM messages WHERE scan_id = ?", (_current_scan_id,)
        ).fetchall()
        listing_rows = connection.execute(
            "SELECT * FROM competitor_listings WHERE scan_id = ? ORDER BY fetched_at DESC",
            (_current_scan_id,),
        ).fetchall()
        meta_ad_rows = connection.execute(
            "SELECT * FROM meta_ads WHERE scan_id = ? ORDER BY delivery_start DESC",
            (_current_scan_id,),
        ).fetchall()
        meta_lookup_rows = connection.execute(
            "SELECT * FROM meta_ad_lookups WHERE scan_id = ?",
            (_current_scan_id,),
        ).fetchall()
        identification_rows = connection.execute(
            """SELECT product_key, previous_key, method, confidence, alternatives_json
               FROM product_identifications WHERE scan_id = ?""",
            (_current_scan_id,),
        ).fetchall()
        research_rows = connection.execute(
            """SELECT product_key, trend_direction, active_ads, oldest_ad_days,
                      social_views, social_interactions, notes
               FROM research_evidence WHERE scan_id = ?""",
            (_current_scan_id,),
        ).fetchall()
    if scan is None:
        return []
    if scan["extractor_version"] < 2:
        return []
    scan_market = scan["market"]
    groups = {}
    now = datetime.now(timezone.utc)
    for row in rows:
        item = dict(row)
        item_attributes = json.loads(item["attributes_json"] or "{}")
        item_attributes = product_attributes_from_text(
            item["message_text"], item_attributes
        )
        if item["review_candidate"]:
            item["product"], _ = derive_offer_title(item["message_text"])
        item["description_text"] = item["description_text"] or separate_description(
            item["product"], item["message_text"]
        )
        chat_entity = _chat_entities.get(item["chat_id"])
        item["telegram_url"] = item["telegram_url"] or telegram_message_url(
            item["chat_id"],
            getattr(chat_entity, "username", None),
            item["message_id"],
        )
        group = groups.setdefault(
            item["normalized_product"],
            {
                "product": item["product"],
                "offers": [],
                "sellers": set(),
                "views": [],
                "reactions": [],
                "comments": [],
                "forwards": [],
                "attributes": {},
                "candidate_only": True,
                "descriptions": [],
            },
        )
        group["candidate_only"] = group["candidate_only"] and bool(item["review_candidate"])
        for key, values in item_attributes.items():
            current_values = group["attributes"].setdefault(key, [])
            for value in values:
                if value not in current_values:
                    current_values.append(value)
        group["sellers"].add(normalize_supplier_name(item["supplier"]))
        if item["views"] is not None:
            group["views"].append(item["views"])
        group["reactions"].append(item["reactions"] or 0)
        group["comments"].append(item["comments"] or 0)
        group["forwards"].append(item["forwards"] or 0)
        image_url = f"/media/{item['image_name']}" if item["image_name"] else None
        if item["description_text"] and item["description_text"] not in group["descriptions"]:
            group["descriptions"].append(item["description_text"])
        group["offers"].append(
            {
                "supplier": item["supplier"],
                "channel": item["chat_title"],
                "chat_id": item["chat_id"],
                "date": item["message_date"],
                "price": item["price"],
                "currency": item["currency"],
                "price_unit": item["price_unit"],
                "moq": item["moq"],
                "size": item["size"],
                "views": item["views"],
                "forwards": item["forwards"],
                "reactions": item["reactions"],
                "comments": item["comments"],
                "image_hash": item["image_hash"],
                "image": image_url,
                "message": item["message_text"],
                "description": item["description_text"],
                "telegram_url": item["telegram_url"],
                "review_candidate": bool(item["review_candidate"]),
            }
        )
    listings_by_key = {}
    for row in listing_rows:
        listings_by_key.setdefault(row["product_key"], []).append(dict(row))
    meta_ads_by_key = {}
    for row in meta_ad_rows:
        ad = dict(row)
        ad["platforms"] = json.loads(ad.pop("platforms_json") or "[]")
        meta_ads_by_key.setdefault(ad["product_key"], []).append(ad)
    meta_lookups_by_key = {
        row["product_key"]: dict(row) for row in meta_lookup_rows
    }
    research_by_key = {row["product_key"]: dict(row) for row in research_rows}
    identifications_by_key = {
        row["product_key"]: dict(row) for row in identification_rows
    }
    products = []
    period_days = max(
        1,
        (date.fromisoformat(scan["end_date"]) - date.fromisoformat(scan["start_date"])).days
        + 1,
    )
    for normalized, group in groups.items():
        offers = group["offers"]
        seller_count = len(group["sellers"])
        views = group["views"]
        newest = max(
            (datetime.fromisoformat(offer["date"]) for offer in offers if offer["date"]),
            default=now,
        )
        age_days = max(0, (now - newest).days)
        posts_score = min(25, len(offers) * 4)
        seller_score = min(20, max(0, seller_count - 1) * 10)
        engagement_score = (
            min(20, (max(views).bit_length() / 14) * 20) if views else 0
        )
        completeness = (
            sum(
                bool(offer["price"]) + bool(offer["moq"]) + bool(offer["size"]) + bool(offer["image"])
                for offer in offers
            )
            / max(1, len(offers))
            * 5
        )
        freshness_score = max(0, 15 * (1 - age_days / period_days))
        score = round(posts_score + seller_score + engagement_score + completeness + freshness_score)
        comparison_offer = next(
            (
                offer
                for offer in offers
                if offer["price"] is not None and offer["currency"]
            ),
            None,
        )
        comparable_prices = [
            offer["price"]
            for offer in offers
            if offer["price"] is not None
            and comparison_offer is not None
            and offer["currency"] == comparison_offer["currency"]
            and offer["price_unit"] == comparison_offer["price_unit"]
        ]
        currency = comparison_offer["currency"] if comparison_offer else None
        price_unit = comparison_offer["price_unit"] if comparison_offer else None
        evidence = {}
        product_ads = meta_ads_by_key.get(normalized, [])
        # Category contamination ends here: only ads whose own text names this
        # product are counted as its ads. The rest become similar-product ads.
        profile = product_match_profile(group["product"], attributes=group["attributes"])
        exact_ads, similar_ads = classify_product_ads(profile, product_ads)
        meta_lookup = meta_lookups_by_key.get(normalized)
        if meta_lookup and meta_lookup["status"] == "ok":
            evidence["active_ads"] = len(exact_ads)
            evidence["category_ads"] = len(similar_ads)
            start_dates = []
            for ad in product_ads:
                try:
                    start_date = datetime.fromisoformat(ad["delivery_start"])
                    start_dates.append(
                        start_date.replace(tzinfo=timezone.utc)
                        if start_date.tzinfo is None
                        else start_date.astimezone(timezone.utc)
                    )
                except ValueError:
                    continue
            if start_dates:
                evidence["oldest_ad_days"] = max(
                    0, (now - min(start_dates)).days
                )
        test_priority, research_breakdown, research_coverage = calculate_test_priority(
            min(100, score), evidence
        )
        identification = identifications_by_key.get(normalized)
        research_row = research_by_key.get(normalized) or {}
        pricing = product_pricing_estimate(
            min(comparable_prices) if comparable_prices else None,
            currency,
            price_unit,
            listings_by_key.get(normalized, []),
            offers=offers,
        )
        trend = product_trend_estimate(
            research_row,
            age_days,
            period_days,
            evidence.get("oldest_ad_days"),
            exact_ads,
        )
        status, status_note = product_status_for(
            _current_scan_id, normalized, group["candidate_only"], identification
        )
        gallery = product_gallery(
            offers,
            exact_ads + similar_ads,
            listings_by_key.get(normalized, []),
        )
        products.append(
            {
                "key": normalized,
                "product": group["product"],
                "description": "\n\n".join(group["descriptions"]),
                "score": None if group["candidate_only"] else test_priority,
                "telegram_score": None if group["candidate_only"] else min(100, score),
                "research_coverage": research_coverage,
                "posts": len(offers),
                "sellers": seller_count,
                "max_views": max(views) if views else None,
                "total_reactions": sum(group["reactions"]),
                "total_comments": sum(group["comments"]),
                "total_forwards": sum(group["forwards"]),
                "price_currency": currency,
                "price_unit": price_unit,
                "lowest_price": min(comparable_prices) if comparable_prices else None,
                "offers": offers,
                "market_research": evidence,
                "competitor_listings": listings_by_key.get(normalized, []),
                "active_meta_ads": exact_ads,
                "exact_meta_ads": exact_ads,
                "similar_meta_ads": similar_ads,
                "meta_ads_lookup": meta_lookup,
                "ads_match_summary": {
                    "exact": len(exact_ads),
                    "similar": len(similar_ads),
                    "filtered_out": max(
                        0, len(product_ads) - len(exact_ads) - len(similar_ads)
                    ),
                },
                "gallery": gallery,
                "gallery_count": len(gallery),
                "status": status,
                "status_label": PRODUCT_STATUS_LABELS.get(status, status),
                "status_note": status_note,
                "identification_source": identification_source(
                    identification, group["candidate_only"]
                ),
                "pricing": pricing,
                "trend": trend,
                "attributes": group["attributes"],
                "candidate_only": group["candidate_only"],
                "identified_from_photo": bool(
                    identification and identification["method"] == "photo"
                ),
                "identification_method": identification["method"] if identification else None,
                "identification_confidence": (
                    identification["confidence"] if identification else None
                ),
                "identification_alternatives": (
                    json.loads(identification["alternatives_json"] or "[]")
                    if identification
                    else []
                ),
                "previous_product_key": (
                    identification["previous_key"] if identification else None
                ),
                "score_breakdown": {
                    "telegram_post_frequency": round(posts_score),
                    "supplier_mentions": round(seller_score),
                    "post_views": round(engagement_score),
                    "offer_detail_completeness": round(completeness),
                    "recency": round(freshness_score),
                    "market_research": research_breakdown,
                },
                "warning": "Research results and source-provided metrics are clues, not proof of sales, supplier quality, or profit.",
            }
        )
    products.sort(
        key=lambda product: (product["score"] is not None, product["score"] or 0),
        reverse=True,
    )
    return products


class Handler(BaseHTTPRequestHandler):
    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = (ROOT / "dashboard.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        elif parsed.path == "/api/settings":
            try:
                self.send_json(
                    {
                        **credential_status(),
                        "meta_ad_country": _meta_ad_settings["country"],
                        "meta_ad_countries": [
                            {"code": code, "name": name}
                            for code, name in META_AD_COUNTRIES
                        ],
                    }
                )
            except RuntimeError as error:
                self.send_json({"error": str(error)}, 500)
        elif parsed.path == "/api/status":
            with _state_lock:
                state = dict(_state)
            state["chats_loaded"] = bool(_chat_entities)
            state.update(get_model_download_status())
            self.send_json(state)
        elif parsed.path == "/api/chats":
            if (
                not _chat_entities
                and not _state["running"]
                and not _state["needs_login"]
            ):
                started = start_job("Connecting to Telegram and loading your chats…", load_chats)
                if not started:
                    self.send_json({"chats": [], "loading": True})
                    return
            self.send_json(
                {
                    "chats": get_chats(),
                    "loading": _state["running"] and not bool(_chat_entities),
                }
            )
        elif parsed.path == "/api/image-search/status":
            search_hash = parse_qs(parsed.query).get("search_hash", [""])[0]
            if search_hash and not re.fullmatch(r"[a-fA-F0-9]{16}", search_hash):
                self.send_json({"error": "Image-search status needs a valid search hash."}, 400)
                return
            self.send_json({"index": image_index_status(search_hash or None), "telegram": {
                "needs_login": _state["needs_login"],
                "running": _state["running"],
                "job_kind": _state["job_kind"],
                "error": _state["error"],
            }})
        elif parsed.path == "/api/scans":
            with closing(sqlite3.connect(DB_PATH)) as connection:
                connection.row_factory = sqlite3.Row
                scans = connection.execute(
                    """
                    SELECT scan_id, name, start_date, end_date, market, status,
                        chat_count, scanned_messages, products_found, completed_at,
                        chat_titles_json, chat_ids_json
                    FROM scans ORDER BY completed_at DESC, scan_id DESC LIMIT 100
                    """
                ).fetchall()
            self.send_json({"scans": [dict(scan) for scan in scans]})
        elif parsed.path == "/api/results":
            with sqlite3.connect(DB_PATH) as connection:
                connection.row_factory = sqlite3.Row
                scan = connection.execute(
                    "SELECT * FROM scans WHERE scan_id = ?",
                    (_current_scan_id or (_latest_scan_info or {}).get("scan_id"),),
                ).fetchone() if (_current_scan_id or _latest_scan_info) else None
            products = load_results()
            scan_id = scan["scan_id"] if scan else None
            if scan_id:
                start_meta_ads_sync(scan_id, products)
                start_photo_identification(scan_id)
                start_web_research(scan_id)
            self.send_json(
                {
                    "products": products,
                    "scan": dict(scan) if scan else None,
                    "alerts": load_engagement_alerts(
                        scan["scan_id"] if scan else None
                    ),
                    "legacy_results_unreliable": bool(
                        scan and scan["extractor_version"] < 2
                    ),
                    "meta_ads_summary": meta_ads_summary(scan_id) if scan_id else None,
                    "identification_summary": (
                        {
                            **photo_identification_summary(scan_id),
                            "web_research": web_research_summary(scan_id),
                            "progressive": progressive_identification_summary(scan_id),
                        }
                        if scan_id
                        else None
                    ),
                    "status_counts": count_products_by_status(products),
                    "integrity": scan_integrity(scan_id, products) if scan_id else None,
                    "integrity_verification": (
                        last_integrity_report(scan_id) if scan_id else None
                    ),
                }
            )
        elif parsed.path == "/api/identification/summary":
            scan_id = _current_scan_id or (_latest_scan_info or {}).get("scan_id")
            self.send_json(
                {
                    "summary": {
                        **(
                            {
                                **photo_identification_summary(scan_id),
                                "web_research": web_research_summary(scan_id),
                            }
                            if scan_id
                            else {
                                "installed": photo_identification_installed(),
                                "running": False,
                                "checked": 0,
                                "total": 0,
                                "identified": 0,
                                "unreadable": 0,
                                "pending": 0,
                                "message": "",
                            }
                        )
                    }
                }
            )
        elif parsed.path == "/api/meta-ads/summary":
            scan_id = _current_scan_id or (_latest_scan_info or {}).get("scan_id")
            self.send_json(
                {"summary": meta_ads_summary(scan_id)} if scan_id
                else {"summary": {"configured": meta_lookup_available(), "active_ads_found": 0, "products_checked": 0, "running": False}}
            )
        elif parsed.path == "/api/product":
            product_key = parse_qs(parsed.query).get("key", [""])[0]
            product = next(
                (item for item in load_results() if item["key"] == product_key),
                None,
            )
            if product is None:
                self.send_json({"error": "That product is not in the current scan."}, 404)
                return
            # Opening a product page names an unnamed offer from its own photo,
            # the same way the background sweep does after a scan.
            auto_identified = None
            if product["candidate_only"] and any(
                offer.get("image") for offer in product["offers"]
            ):
                result = auto_identify_product(_current_scan_id, product_key)
                if result.get("saved") and result.get("key") != product_key:
                    auto_identified = {
                        "from_key": product_key,
                        "to_key": result["key"],
                        "name": result["name"],
                        "score": result["score"],
                        "source": result.get("source") or "clip",
                    }
                    product = next(
                        (
                            item
                            for item in load_results()
                            if item["key"] == result["key"]
                        ),
                        product,
                    )
                    product_key = result["key"]
            product["history"] = load_product_history(product_key)
            product["details"] = load_product_details(_current_scan_id, product_key)
            product["alerts"] = [
                alert
                for alert in load_engagement_alerts(
                    _current_scan_id
                )
                if alert["product_key"] == product_key
            ]
            self.send_json({"product": product, "auto_identified": auto_identified})
        elif parsed.path == "/api/product/similar-sellers":
            product_key = parse_qs(parsed.query).get("key", [""])[0]
            if not product_key:
                self.send_json({"error": "Choose a product to search by image."}, 400)
                return
            if not any(
                item["key"] == product_key for item in load_results()
            ):
                self.send_json({"error": "That product is not in the current scan."}, 404)
                return
            try:
                self.send_json(same_product_sellers(_current_scan_id, product_key))
            except (OSError, ValueError, sqlite3.Error) as error:
                self.send_json({"error": str(error)}, 500)
        elif parsed.path == "/api/image-product":
            product_key = parse_qs(parsed.query).get("key", [""])[0]
            if not product_key:
                self.send_json({"error": "Choose an identified product from the image results."}, 400)
                return
            with closing(sqlite3.connect(DB_PATH)) as connection:
                connection.row_factory = sqlite3.Row
                indexed_posts = connection.execute(
                    """
                    SELECT product, supplier, chat_title, price, currency,
                        message_date, telegram_url, image_name
                    FROM telegram_image_index WHERE product_key = ?
                    ORDER BY message_date DESC LIMIT 200
                    """,
                    (product_key,),
                ).fetchall()
                offers = connection.execute(
                    """
                    SELECT product, supplier, chat_title, message_date, price,
                        currency, price_unit, moq, size, views, forwards,
                        reactions, comments, telegram_url, attributes_json,
                        description_text
                    FROM messages AS current
                    WHERE normalized_product = ? AND rowid = (
                        SELECT MAX(previous.rowid) FROM messages AS previous
                        WHERE previous.chat_id = current.chat_id
                          AND previous.message_id = current.message_id
                          AND previous.normalized_product = current.normalized_product
                    )
                    ORDER BY message_date DESC LIMIT 200
                    """,
                    (product_key,),
                ).fetchall()
            product_name = (
                indexed_posts[0]["product"] if indexed_posts else product_key
            )
            self.send_json(
                {
                    "product": product_name,
                    "key": product_key,
                    "image_posts": [dict(row) for row in indexed_posts],
                    "offers": [dict(row) for row in offers],
                }
            )
        elif parsed.path == "/api/export.csv":
            content = export_products_csv(load_results()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header(
                "Content-Disposition",
                'attachment; filename="telegram-product-research.csv"',
            )
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif parsed.path.startswith("/media/"):
            image_name = Path(parsed.path.removeprefix("/media/")).name
            if image_name != parsed.path.removeprefix("/media/"):
                self.send_error(404)
                return
            image_path = IMAGE_DIR / image_name
            if not image_path.is_file():
                self.send_error(404)
                return
            content = image_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(image_path.name)[0] or "image/jpeg")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)

    def do_POST(self):
        global _current_scan_id, _latest_scan_info
        parsed = urlparse(self.path)
        if parsed.path == "/api/image-search":
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_IMAGE_UPLOAD_BYTES + 128 * 1024:
                self.send_json({"error": "Image upload must be smaller than 8 MB."}, 413)
                return
            try:
                image_hash, selected_chat_ids = image_upload_hash_and_chat_ids(
                    self.headers.get("Content-Type", ""),
                    self.rfile.read(content_length),
                )
            except (ValueError, LookupError) as error:
                self.send_json({"error": str(error)}, 400)
                return
            if not selected_chat_ids:
                self.send_json({"error": "Select at least one Telegram channel or group for image search."}, 400)
                return
            unavailable_ids = set(selected_chat_ids) - set(_chat_entities)
            if unavailable_ids:
                self.send_json({"error": "Refresh Telegram and select channels that are currently available."}, 409)
                return
            matches = find_similar_telegram_images(
                image_hash, chat_ids=selected_chat_ids
            )
            matched_chat_ids = [match["chat_id"] for match in matches]
            index_status = image_index_status(image_hash)
            selected_indexed = set(index_status["ready_chat_ids"])
            pending_chat_ids = [
                chat_id
                for chat_id in selected_chat_ids
                if chat_id not in selected_indexed and chat_id not in matched_chat_ids
            ]
            if pending_chat_ids:
                indexing_job = (
                    _state["running"] and _state["job_kind"] == "image_index"
                )
                if not indexing_job and not start_job(
                    "Searching selected Telegram channels for a matching product image…",
                    lambda: build_telegram_image_index(pending_chat_ids, image_hash),
                    kind="image_index",
                ):
                    self.send_json(
                        {
                            "error": "Another Telegram operation is running. Wait for it to finish, then search the image again."
                        },
                        409,
                    )
                    return
                self.send_json(
                    {
                        "indexing": True,
                        "index": image_index_status(image_hash),
                        "matches": matches,
                        "chat_ids": selected_chat_ids,
                        "matched_chat_ids": matched_chat_ids,
                        "search_hash": image_hash,
                    },
                    202,
                )
                return
            self.send_json(
                {
                    "match_type": "perceptual image match",
                    "matches": matches,
                    "chat_ids": selected_chat_ids,
                }
            )
            return
        try:
            body = self.read_json()
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "Request must contain valid JSON."}, 400)
            return
        if parsed.path == "/api/operations/cancel":
            with _state_lock:
                job_kind = _state["job_kind"] if _state["running"] else None
            if job_kind == "scan":
                _scan_cancel.set()
                with _state_lock:
                    _state.update(
                        cancel_requested=True,
                        message="Stopping after the current Telegram message and saving partial results…",
                    )
                self.send_json({"cancelling": True, "job_kind": job_kind}, 202)
            elif job_kind == "image_index":
                _image_index_cancel.set()
                with _state_lock:
                    _state.update(
                        cancel_requested=True,
                        message="Stopping image search safely and saving its channel checkpoint…",
                    )
                self.send_json({"cancelling": True, "job_kind": job_kind}, 202)
            elif job_kind:
                if _active_job_future is not None:
                    _active_job_future.cancel()
                self.send_json({"cancelling": True, "job_kind": job_kind}, 202)
            else:
                with _photo_identify_lock:
                    identifying = _photo_identify_state["running"]
                if identifying:
                    _photo_identify_cancel.set()
                    with _photo_identify_lock:
                        _photo_identify_state["message"] = (
                            "Stopping photo naming after the current photo. Names already "
                            "saved are kept."
                        )
                    self.send_json({"cancelling": True, "job_kind": "photo_identification"}, 202)
                    return
                with _meta_ads_lock:
                    meta_running = _meta_ads_state["running"]
                    if meta_running:
                        _meta_ads_cancel.set()
                if meta_running:
                    self.send_json({"cancelling": True, "job_kind": "meta_ads"}, 202)
                else:
                    self.send_json({"error": "There is no cancellable operation running."}, 409)
        elif parsed.path == "/api/image-search/resume":
            if _state["running"]:
                self.send_json({"error": "Wait until the current operation ends before resuming image indexing."}, 409)
                return
            cutoff_date = (date.today() - timedelta(days=365)).isoformat()
            with closing(sqlite3.connect(DB_PATH)) as connection:
                paused_chat_ids = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT chat_id FROM telegram_image_index_state
                        WHERE cutoff_date = ? AND status = 'paused'
                        """,
                        (cutoff_date,),
                    ).fetchall()
                ]
            _image_index_cancel.clear()
            if not start_job(
                "Resuming Telegram image indexing…",
                lambda: build_telegram_image_index(paused_chat_ids or None),
                kind="image_index",
            ):
                self.send_json({"error": "Another operation is already running."}, 409)
                return
            self.send_json({"started": True}, 202)
        elif parsed.path == "/api/meta-ads/resume":
            scan_id = _current_scan_id or (_latest_scan_info or {}).get("scan_id")
            if not scan_id:
                self.send_json({"error": "Run a Telegram scan before resuming Meta ad lookups."}, 409)
                return
            start_meta_ads_sync(scan_id, load_results())
            with _meta_ads_lock:
                running = _meta_ads_state["running"] and _meta_ads_state["scan_id"] == scan_id
            if not running:
                self.send_json({"error": "There are no pending Meta ad lookups to resume, or meta-ads-collector is not installed."}, 409)
                return
            self.send_json({"started": True}, 202)
        elif parsed.path == "/api/scans/select":
            scan_id = str(body.get("scan_id", "")).strip()
            with closing(sqlite3.connect(DB_PATH)) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM scans WHERE scan_id=?", (scan_id,)
                ).fetchone()
            if not exists:
                self.send_json({"error": "That saved operation no longer exists."}, 404)
                return
            _current_scan_id = scan_id
            # Opening an older scan names its unnamed offers too.
            start_photo_identification(scan_id)
            start_web_research(scan_id)
            self.send_json({"selected": True})
        elif parsed.path == "/api/scans/rename":
            scan_id = str(body.get("scan_id", "")).strip()
            name = str(body.get("name", "")).strip()[:120]
            if not scan_id or not name:
                self.send_json({"error": "Choose a saved operation and enter a name."}, 400)
                return
            with closing(sqlite3.connect(DB_PATH)) as connection:
                cursor = connection.execute(
                    "UPDATE scans SET name=? WHERE scan_id=? AND status != 'running'",
                    (name, scan_id),
                )
                connection.commit()
            if not cursor.rowcount:
                self.send_json({"error": "That operation was not found or is still running."}, 404)
                return
            self.send_json({"updated": True})
        elif parsed.path == "/api/scans/delete":
            scan_id = str(body.get("scan_id", "")).strip()
            if not scan_id:
                self.send_json({"error": "Choose a saved operation to delete."}, 400)
                return
            if _state["running"] and _active_scan_id == scan_id:
                self.send_json({"error": "Stop the active scan before deleting it."}, 409)
                return
            with closing(sqlite3.connect(DB_PATH)) as connection:
                connection.execute("BEGIN")
                exists = connection.execute(
                    "SELECT 1 FROM scans WHERE scan_id=?", (scan_id,)
                ).fetchone()
                if exists:
                    for table in (
                        "messages",
                        "research_evidence",
                        "competitor_listings",
                        "engagement_alerts",
                        "meta_ads",
                        "meta_ad_lookups",
                    ):
                        connection.execute(f"DELETE FROM {table} WHERE scan_id=?", (scan_id,))
                    connection.execute("DELETE FROM scans WHERE scan_id=?", (scan_id,))
                connection.commit()
            if not exists:
                self.send_json({"error": "That saved operation no longer exists."}, 404)
                return
            if _current_scan_id == scan_id:
                _current_scan_id = None
            if (_latest_scan_info or {}).get("scan_id") == scan_id:
                _latest_scan_info = None
            self.send_json({"deleted": True})
        elif parsed.path == "/api/scans/rerun":
            scan_id = str(body.get("scan_id", "")).strip()
            with closing(sqlite3.connect(DB_PATH)) as connection:
                connection.row_factory = sqlite3.Row
                saved = connection.execute(
                    "SELECT start_date, end_date, market, chat_ids_json FROM scans WHERE scan_id=?",
                    (scan_id,),
                ).fetchone()
            if not saved:
                self.send_json({"error": "That saved operation no longer exists."}, 404)
                return
            try:
                chat_ids = [int(chat_id) for chat_id in json.loads(saved["chat_ids_json"])]
                start_date = date.fromisoformat(saved["start_date"])
                end_date = date.fromisoformat(saved["end_date"])
                market = saved["market"]
            except (TypeError, ValueError, json.JSONDecodeError):
                self.send_json({"error": "This older operation has no reusable channel list. Set up a new scan instead."}, 409)
                return
            if not chat_ids or any(chat_id not in _chat_entities for chat_id in chat_ids):
                self.send_json({"error": "Refresh Telegram and sign in before running these saved channels again."}, 409)
                return
            os.environ["MARKET_COUNTRY"] = market
            _scan_cancel.clear()
            if not start_job(
                "Re-running the saved scan criteria…",
                lambda: run_scan(chat_ids, start_date, end_date),
                kind="scan",
            ):
                self.send_json({"error": "Another Telegram operation is already running."}, 409)
                return
            self.send_json({"started": True, "note": "This creates a new scan with the saved dates and channels; it does not continue from a message checkpoint."}, 202)
        elif parsed.path == "/api/settings":
            if not isinstance(body, dict):
                self.send_json({"error": "Settings must be sent as a JSON object."}, 400)
                return
            telegram_changed = bool(
                str(body.get("telegram_api_id", "")).strip()
                or str(body.get("telegram_api_hash", "")).strip()
            )
            if telegram_changed and _state["running"]:
                self.send_json(
                    {"error": "Wait for the current Telegram operation to finish before changing Telegram credentials."},
                    409,
                )
                return
            try:
                update_local_credentials(body)
                if "meta_ad_country" in body:
                    save_meta_ad_settings(body.get("meta_ad_country"))
                if telegram_changed:
                    runner.submit(disconnect_telegram_client()).result(timeout=15)
                    _chat_entities.clear()
                    _login_state.update(phone=None, phone_code_hash=None)
                    with _state_lock:
                        _state.update(
                            needs_login=False,
                            login_stage="phone",
                            message="Telegram credentials changed. Refresh Telegram to reconnect.",
                            error=None,
                        )
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
                return
            except (OSError, RuntimeError, TimeoutError) as error:
                self.send_json({"error": str(error) or "Could not update local credentials."}, 500)
                return
            self.send_json(
                {
                    "saved": True,
                    **credential_status(),
                    "meta_ad_country": _meta_ad_settings["country"],
                }
            )
        elif parsed.path == "/api/meta-ads/test":
            if not meta_lookup_available():
                self.send_json({"error": META_ADS_COLLECTOR_HINT}, 400)
                return
            try:
                ads = fetch_meta_ads("product", max_results=1)
            except (RuntimeError, OSError, ValueError) as error:
                self.send_json({"error": str(error) or "Meta rejected the connection test."}, 502)
                return
            self.send_json(
                {
                    "connected": True,
                    "matching_ads": len(ads),
                    "message": (
                        "The Ad Library is reachable and returned an active ad record."
                        if ads
                        else "The Ad Library is reachable. The test query returned no matching active ads."
                    ),
                }
            )
        elif parsed.path == "/api/chats/reload":
            if not start_job("Reloading channels and groups…", load_chats, kind="telegram_connect"):
                self.send_json({"error": "Another operation is already running."}, 409)
                return
            self.send_json({"started": True}, 202)
        elif parsed.path == "/api/scan/cancel":
            if not _state["running"] or _state["job_kind"] != "scan":
                self.send_json({"error": "There is no active scan to stop."}, 409)
                return
            _scan_cancel.set()
            if _active_job_future is not None:
                _active_job_future.cancel()
            with _state_lock:
                _state.update(
                    cancel_requested=True,
                    message="Stopping after the current message and saving partial results…",
                )
            self.send_json({"cancelling": True}, 202)
        elif parsed.path == "/api/research/save":
            scan_id = _current_scan_id
            product_key = str(body.get("product_key", "")).strip()
            trend = str(body.get("trend_direction", "unknown")).strip().casefold()
            if not scan_id or not product_key:
                self.send_json({"error": "Run a scan and select a product before saving research."}, 400)
                return
            if trend not in ("unknown", "rising", "stable", "falling"):
                self.send_json({"error": "Trend must be unknown, rising, stable, or falling."}, 400)
                return
            with sqlite3.connect(DB_PATH) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM messages WHERE scan_id = ? AND normalized_product = ? LIMIT 1",
                    (scan_id, product_key),
                ).fetchone()
            if not exists:
                self.send_json({"error": "That product is not part of the current scan."}, 404)
                return
            try:
                active_ads = optional_nonnegative_int(body.get("active_ads"), "Active ads")
                oldest_ad_days = optional_nonnegative_int(
                    body.get("oldest_ad_days"), "Oldest ad age"
                )
                social_views = optional_nonnegative_int(
                    body.get("social_views"), "Social views"
                )
                social_interactions = optional_nonnegative_int(
                    body.get("social_interactions"), "Social interactions"
                )
                source_url = str(body.get("social_source_url", "")).strip()
                notes = str(body.get("notes", "")).strip()[:1000]
                if source_url:
                    validate_public_url(source_url)
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
                return
            with sqlite3.connect(DB_PATH) as connection:
                connection.execute(
                    """
                    INSERT INTO research_evidence (
                        scan_id, product_key, trend_direction, active_ads,
                        oldest_ad_days, social_views, social_interactions,
                        social_source_url, notes, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scan_id, product_key) DO UPDATE SET
                        trend_direction=excluded.trend_direction,
                        active_ads=excluded.active_ads,
                        oldest_ad_days=excluded.oldest_ad_days,
                        social_views=excluded.social_views,
                        social_interactions=excluded.social_interactions,
                        social_source_url=excluded.social_source_url,
                        notes=excluded.notes,
                        updated_at=excluded.updated_at
                    """,
                    (
                        scan_id,
                        product_key,
                        trend,
                        active_ads,
                        oldest_ad_days,
                        social_views,
                        social_interactions,
                        source_url,
                        notes,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            self.send_json({"saved": True})
        elif parsed.path == "/api/competitor/fetch":
            scan_id = _current_scan_id
            product_key = str(body.get("product_key", "")).strip()
            url = str(body.get("url", "")).strip()
            if not scan_id or not product_key or not url:
                self.send_json({"error": "Choose a scanned product and enter a competitor listing URL."}, 400)
                return
            with sqlite3.connect(DB_PATH) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM messages WHERE scan_id = ? AND normalized_product = ? LIMIT 1",
                    (scan_id, product_key),
                ).fetchone()
            if not exists:
                self.send_json({"error": "That product is not part of the current scan."}, 404)
                return
            try:
                listing = fetch_competitor_listing(url)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, LookupError) as error:
                message = str(error) or f"{type(error).__name__}: competitor page could not be read."
                save_competitor_listing(scan_id, product_key, url, error_text=message)
                self.send_json({"error": message}, 502)
                return
            fetched_at = save_competitor_listing(
                scan_id, product_key, listing["url"], listing=listing
            )
            self.send_json({"listing": listing, "fetched_at": fetched_at})
        elif parsed.path == "/api/competitor/fetch-from-ads":
            scan_id = _current_scan_id or (_latest_scan_info or {}).get("scan_id")
            product_key = str(body.get("product_key", "")).strip()
            if not scan_id or not product_key:
                self.send_json(
                    {"error": "Choose a scanned product before collecting competitor prices."},
                    400,
                )
                return
            try:
                listings = collect_competitor_prices_from_ads(scan_id, product_key)
            except LookupError as error:
                self.send_json({"error": str(error)}, 409)
                return
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
                return
            self.send_json({"listings": listings})
        elif parsed.path == "/api/products/identify":
            scan_id = _current_scan_id
            product_key = str(body.get("product_key", "")).strip()
            name = str(body.get("name", "")).strip()
            if not scan_id or not product_key:
                self.send_json(
                    {"error": "Run a scan and choose the product you want to identify."},
                    400,
                )
                return
            try:
                new_key = identify_product(scan_id, product_key, name)
            except LookupError as error:
                self.send_json({"error": str(error)}, 404)
                return
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
                return
            self.send_json({"saved": True, "key": new_key})
        elif parsed.path == "/api/products/identify-from-image":
            scan_id = _current_scan_id
            product_key = str(body.get("product_key", "")).strip()
            if not scan_id or not product_key:
                self.send_json(
                    {"error": "Run a scan and choose the product you want to identify."},
                    400,
                )
                return
            try:
                result = identify_product_from_image(
                    scan_id, product_key, save=bool(body.get("save", True))
                )
            except LookupError as error:
                self.send_json({"error": str(error)}, 409)
                return
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
                return
            except RuntimeError as error:
                self.send_json({"error": str(error)}, 503)
                return
            identification = connection.execute(
                "SELECT specs_json FROM product_identifications WHERE scan_id = ? AND product_key = ?",
                (scan_id, result["key"]),
            ).fetchone()
            result["specs"] = json.loads(identification["specs_json"] or "[]") if identification else []
            self.send_json(result)
        elif parsed.path == "/api/products/research-web":
            scan_id = _current_scan_id
            product_key = str(body.get("product_key", "")).strip()
            if not scan_id or not product_key:
                self.send_json(
                    {"error": "Run a scan and choose the product you want to research."},
                    400,
                )
                return
            try:
                result = identify_product_from_web(scan_id, product_key)
            except LookupError as error:
                self.send_json({"error": str(error)}, 404)
                return
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
                return
            except RuntimeError as error:
                self.send_json({"error": str(error)}, 503)
                return
            self.send_json(result)
        elif parsed.path == "/api/meta-ads/diagnose":
            if not meta_lookup_available():
                self.send_json(
                    {
                        "category": "missing",
                        "message": META_ADS_COLLECTOR_HINT,
                        "detail": "",
                    }
                )
                return
            try:
                ads = fetch_meta_ads("product", max_results=1)
            except (RuntimeError, OSError, ValueError) as error:
                category, message = classify_meta_error(str(error))
                self.send_json(
                    {
                        "category": category,
                        "message": message,
                        "detail": str(error),
                    }
                )
                return
            self.send_json(
                {
                    "category": "ok",
                    "message": "The Meta Ad Library answered a live query."
                    + (
                        ""
                        if ads
                        else " The test query returned no matching active ads."
                    ),
                    "detail": "",
                }
            )
        elif parsed.path == "/api/auth/send-code":
            phone = str(body.get("phone", "")).strip()
            if not phone:
                self.send_json({"error": "Enter your Telegram phone number with country code."}, 400)
                return
            if not start_job("Requesting a Telegram sign-in code…", lambda: send_login_code(phone), kind="telegram_auth"):
                self.send_json({"error": "Another operation is already running."}, 409)
                return
            self.send_json({"started": True}, 202)
        elif parsed.path == "/api/auth/verify-code":
            code = str(body.get("code", "")).strip()
            if not code:
                self.send_json({"error": "Enter the sign-in code Telegram sent you."}, 400)
                return
            if not start_job("Checking your Telegram sign-in code…", lambda: verify_login_code(code), kind="telegram_auth"):
                self.send_json({"error": "Another operation is already running."}, 409)
                return
            self.send_json({"started": True}, 202)
        elif parsed.path == "/api/auth/verify-password":
            password = str(body.get("password", ""))
            if not password:
                self.send_json({"error": "Enter your Telegram two-step verification password."}, 400)
                return
            if not start_job(
                "Checking your Telegram two-step verification password…",
                lambda: verify_two_step_password(password),
                kind="telegram_auth",
            ):
                self.send_json({"error": "Another operation is already running."}, 409)
                return
            self.send_json({"started": True}, 202)
        elif parsed.path == "/api/scan":
            try:
                start_date = date.fromisoformat(body["start_date"])
                end_date = date.fromisoformat(body["end_date"])
                chat_ids = [int(chat_id) for chat_id in body["chat_ids"]]
                market = body["market"]
                if start_date > end_date:
                    raise ValueError("The start date must be on or before the end date.")
                if not chat_ids:
                    raise ValueError("Select at least one channel or group.")
                if market not in (
                    "United States",
                    "United Kingdom",
                    "European Union",
                    "Algeria",
                ):
                    raise ValueError("Select a supported market.")
            except (KeyError, TypeError, ValueError) as error:
                self.send_json({"error": str(error) or "Please check the scan settings."}, 400)
                return
            if any(chat_id not in _chat_entities for chat_id in chat_ids):
                self.send_json({"error": "Reload your chat list and select accessible chats."}, 400)
                return
            os.environ["LAST_SCAN_START"] = start_date.isoformat()
            os.environ["LAST_SCAN_END"] = end_date.isoformat()
            os.environ["MARKET_COUNTRY"] = market
            _scan_cancel.clear()
            if not start_job(
                "Starting the scan…",
                lambda: run_scan(chat_ids, start_date, end_date),
                kind="scan",
            ):
                self.send_json({"error": "Another operation is already running."}, 409)
                return
            self.send_json({"started": True}, 202)
        else:
            self.send_error(404)

    def log_message(self, format_string, *args):
        print(f"Local dashboard: {format_string % args}", flush=True)


def main():
    initialize_database()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Product research dashboard: {url}", flush=True)
    print("This server is only available on this computer.", flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down the dashboard.", flush=True)
    finally:
        server.server_close()
        if _client is not None:
            try:
                runner.submit(_client.disconnect()).result(timeout=10)
            except Exception as error:
                print(f"Telegram disconnect failed: {error}", flush=True)
        runner.loop.call_soon_threadsafe(runner.loop.stop)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--purge-weak-identifications":
        purge_weak_product_identifications()
        print("Purged weak photo identifications from", DB_PATH, flush=True)
    else:
        main()
