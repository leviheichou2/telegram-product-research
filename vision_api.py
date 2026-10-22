"""Database glue for the multi-layer identifier.

``product_vision`` knows nothing about Telegram or the scan tables; this module
is the thin layer that answers the two questions the UI asks:

* ``status_payload``   - is the vision stack installed on this machine?
* ``identify_payload`` - what does this photo show, and who supplies it?

Keeping the SQL here (and importing it from app.py) means the heavy models can
be exercised from a script or a test without starting an HTTP server.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import product_vision as vision
import supplier_bridge

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "telegram_research.db"
IMAGE_DIR = ROOT / "data" / "images"

# Building an index of every product in a scan would take minutes on CPU, so the
# index covers the products that actually have photos, most-posted first.
INDEX_MAX_PRODUCTS = 150

_index_lock = threading.Lock()
_index_cache: dict[tuple, dict] = {}


def _connect(database: Path | str | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(database or DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def product_photos(scan_id: str, product_key: str, limit: int = 6, database=None, image_dir=None) -> list[Path]:
    """The stored photos of one product, largest first."""
    image_dir = Path(image_dir or IMAGE_DIR)
    try:
        with closing(_connect(database)) as connection:
            rows = connection.execute(
                """SELECT image_name FROM messages
                   WHERE scan_id = ? AND normalized_product = ?
                     AND image_name IS NOT NULL AND image_name != ''""",
                (scan_id, product_key),
            ).fetchall()
    except sqlite3.Error:
        return []
    paths = []
    for row in rows:
        path = image_dir / Path(str(row[0])).name
        if path.is_file():
            paths.append(path)
    paths.sort(key=lambda item: item.stat().st_size, reverse=True)
    return paths[:limit]


def candidates(scan_id: str, limit: int = INDEX_MAX_PRODUCTS, database=None, image_dir=None) -> dict:
    """What is known about each product in a scan, keyed by product key.

    The name and brand come from ``product_details`` when the product has been
    identified, because that is the corrected name; otherwise the raw post title
    is used, so an unidentified product can still be matched on its wording.
    """
    image_dir = Path(image_dir or IMAGE_DIR)
    try:
        with closing(_connect(database)) as connection:
            rows = connection.execute(
                """SELECT normalized_product, product, MAX(image_name) AS image_name
                   FROM messages
                   WHERE scan_id = ? AND COALESCE(image_name, '') != ''
                   GROUP BY normalized_product
                   ORDER BY COUNT(*) DESC
                   LIMIT ?""",
                (scan_id, limit),
            ).fetchall()
            details = {}
            try:
                for row in connection.execute(
                    """SELECT product_key, name, brand, category, description
                       FROM product_details WHERE scan_id = ?""",
                    (scan_id,),
                ):
                    details[str(row["product_key"])] = dict(row)
            except sqlite3.Error:
                details = {}
    except sqlite3.Error:
        return {}

    found = {}
    for row in rows:
        key = str(row["normalized_product"] or "")
        if not key:
            continue
        detail = details.get(key) or {}
        photo = image_dir / Path(str(row["image_name"])).name
        if not photo.is_file():
            continue
        found[key] = {
            "product_key": key,
            "name": str(detail.get("name") or row["product"] or key),
            "brand": str(detail.get("brand") or ""),
            "category": str(detail.get("category") or ""),
            "description": str(detail.get("description") or ""),
            "photo": photo,
        }
    return found


def _index_for(scan_id: str, catalog: dict) -> vision.VectorIndex | None:
    """A cached visual index over one representative photo per product."""
    if not catalog:
        return None
    signature = (scan_id, vision.embedding_version(), len(catalog))
    with _index_lock:
        cached = _index_cache.get(signature)
        if cached:
            return cached["index"]
    rows = [(key, entry["photo"]) for key, entry in catalog.items()]
    index = vision.index_from_paths(rows)
    with _index_lock:
        _index_cache.clear()
        _index_cache[signature] = {"index": index, "built_at": time.time()}
    return index


def supplier_matches(name: str, category: str = "", limit: int = 6, database=None) -> list[dict]:
    """Directory suppliers for one product, tolerant of a missing database."""
    try:
        rows = None
        if database:
            with closing(_connect(database)) as connection:
                rows = supplier_bridge.directory_rows(connection)
        return supplier_bridge.match_suppliers(name, category, limit=limit, rows=rows)
    except Exception:
        return []


MAX_IMAGE_BYTES = 8 * 1024 * 1024


def request_payload(content_type: str, body: bytes) -> dict:
    """Parse one multipart vision request.

    ``image`` (a file), ``image_url`` (a link) and the optional ``scan_id`` /
    ``product_key`` are read; the URL itself is downloaded by the caller so a
    malformed link cannot reach the model.
    """
    from email import policy
    from email.parser import BytesParser

    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
    )
    payload = {"image": None, "image_url": "", "scan_id": "", "product_key": ""}
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if name == "image" and part.get_filename():
            data = part.get_payload(decode=True) or b""
            if not data:
                raise ValueError("Choose a non-empty image file.")
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("Images must be smaller than 8 MB.")
            payload["image"] = data
        elif name in {"image_url", "scan_id", "product_key"}:
            payload[name] = (part.get_content() or "").strip()
    if not payload["image"] and not payload["image_url"]:
        raise ValueError("Add a photo or a photo link to identify.")
    return payload


def identify_payload(
    image_bytes: bytes,
    scan_id: str = "",
    product_key: str = "",
    limit: int = 5,
    database=None,
    image_dir=None,
) -> dict:
    """Identify one uploaded photo and rank its suppliers.

    With ``product_key`` the photo is checked against that product's own photos,
    which is how "is this really the product?" is answered. Without it, the photo
    is searched against the scan's product photos and the best candidates are
    re-scored with the OCR layers.
    """
    started = time.time()
    try:
        with vision.load_image(image_bytes):
            pass
    except Exception as error:
        raise ValueError("That file is not an image the identifier can read.") from error

    catalog = candidates(scan_id, database=database, image_dir=image_dir) if scan_id else {}
    index = None
    target_key = product_key

    if product_key and scan_id:
        photos = product_photos(scan_id, product_key, database=database, image_dir=image_dir)
        entry = catalog.get(product_key) or {}
        if photos:
            entry = dict(entry or {"product_key": product_key, "name": product_key,
                                   "brand": "", "category": "", "description": ""})
            index = vision.index_from_paths([(product_key, photos[0])])
            catalog = {product_key: entry}
        elif entry:
            catalog = {product_key: entry}
    elif catalog:
        index = _index_for(scan_id, catalog)

    matches = {key: supplier_matches(entry.get("name") or key, entry.get("category") or "",
                                     database=database)
               for key, entry in catalog.items()} if catalog else {}

    identity = vision.identify(
        image_bytes,
        index=index,
        candidates=catalog,
        supplier_matches=matches,
        limit=limit,
    )
    chosen = catalog.get(identity.product_key) if identity.product_key else None
    result = identity.as_dict()
    result["product_name"] = (chosen or {}).get("name", "")
    result["suppliers"] = matches.get(identity.product_key, []) if identity.product_key else []
    result["supplier_tiers"] = rank_suppliers(result["suppliers"])
    result["target_product"] = target_key
    result["status"] = vision.status()
    result["indexed_products"] = len(catalog)
    result["elapsed_ms"] = int((time.time() - started) * 1000)
    return result


SUPPLIER_TIERS = (
    (0.65, "exact", "Exact supplier match"),
    (0.45, "high", "High confidence"),
    (0.0, "similar", "Similar category"),
)


def rank_suppliers(matches: list[dict]) -> list[dict]:
    """Split supplier rows into the three tiers the product page shows.

    The score comes from ``supplier_bridge``: shared product wording first, the
    supplier's own category sheet second. The tier is what the UI labels, so a
    wholesaler is never presented as an exact match on a category alone.
    """
    ranked = []
    for match in matches or []:
        score = float(match.get("score") or 0.0)
        tier, label = next(((name, text) for cut, name, text in SUPPLIER_TIERS if score >= cut),
                           ("similar", "Similar category"))
        row = dict(match)
        row["tier"] = tier
        row["tier_label"] = label
        ranked.append(row)
    ranked.sort(key=lambda item: -float(item.get("score") or 0.0))
    return ranked


def status_payload(database=None) -> dict:
    """Everything the settings panel needs, including index size."""
    payload = vision.status()
    payload["cached_indexes"] = len(_index_cache)
    payload["index_max_products"] = INDEX_MAX_PRODUCTS
    try:
        with closing(_connect(database)) as connection:
            payload["indexed_photos"] = connection.execute(
                "SELECT COUNT(*) FROM telegram_image_index"
            ).fetchone()[0]
    except sqlite3.Error:
        payload["indexed_photos"] = 0
    return payload
