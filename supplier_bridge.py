"""Supplier-directory matching.

The app now holds two independent data sources:

* the Telegram scan, which discovers products, offers and prices;
* the imported supplier guide (``imported_suppliers``), which knows the
  wholesalers of El-Eulma by category, phone and city.

This module is a deliberately small, read-only bridge between them. It never
writes to the scan tables and never reads Telegram, so the scanner can keep
being replaced or rewritten without touching the directory side. Each match
carries the shared terms that produced it, so a match can be explained instead
of trusted blindly.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "telegram_research.db"

# Words that describe no product at all. They are dropped before comparing, so
# "Women's cotton dress" matches a "Cotton dresses" supplier row on cotton/dress.
STOPWORDS = {
    "the", "and", "for", "with", "from", "your", "you", "our", "new", "hot",
    "sale", "best", "top", "set", "pcs", "piece", "pieces", "pack", "lots",
    "pour", "avec", "les", "des", "une", "dans", "sur", "par", "aux",
    "من", "في", "على", "مع", "الى", "إلى", "عن", "هذا", "هذه", "او", "أو",
    "و", "ال", "كل", "جملة", "بالجملة", "سعر", "نوع", "قطعة",
}

# The guide is written in French (and some Arabic) while scanned products often
# arrive with English names. This table is the minimum translation layer needed
# for those two vocabularies to meet; it is intentionally small and readable so
# it can be extended as new categories are imported.
SYNONYM_GROUPS = [
    {"clothing", "vetement", "vetements", "tements", "habit", "habits", "ملابس"},
    {"women", "woman", "femme", "femmes", "نساء"},
    {"men", "man", "homme", "hommes", "رجال"},
    {"kids", "children", "child", "enfant", "enfants", "bebe", "اطفال"},
    {"dress", "robe", "robes", "dresses", "فستان"},
    {"shirt", "chemise", "chemises", "blouse", "قميص"},
    {"tshirt", "tee", "shirt", "t-shirt", "polo"},
    {"pants", "trousers", "pantalon", "pantalons", "jean", "jeans"},
    {"skirt", "jupe", "jupes"},
    {"coat", "jacket", "veste", "manteau", "vestes", "manteaux"},
    {"sweater", "pull", "pulls", "hoodie", "gilet"},
    {"shoes", "shoe", "sneakers", "chaussure", "chaussures", "basket", "baskets"},
    {"bag", "bags", "handbag", "sac", "sacs", "sacoche", "حقيبة"},
    {"jewelry", "bijou", "bijoux", "accessoire", "accessoires"},
    {"watch", "watches", "montre", "montres"},
    {"glasses", "sunglasses", "lunette", "lunettes"},
    {"cosmetics", "cosmetic", "cosmetique", "maquillage", "makeup", "beaute"},
    {"perfume", "parfum", "parfums"},
    {"toy", "toys", "jeu", "jeux", "jouet", "jouets"},
    {"kitchen", "cuisine", "ustensile", "menage"},
    {"phone", "telephone", "smartphone", "portable", "هاتف"},
    {"headphones", "earphones", "earbuds", "ecouteur", "ecouteurs", "casque"},
    {"charger", "cable", "chargeur", "cables"},
    {"car", "auto", "voiture", "automobile"},
    {"motorcycle", "moto", "motocycle", "scooter"},
    {"tools", "tool", "hardware", "quincaillerie", "outillage"},
    {"lighting", "light", "led", "lampe", "eclairage"},
    {"hair", "coiffure", "cheveux"},
    {"packaging", "emballage", "sachet", "carton"},
    {"computer", "laptop", "informatique", "ordinateur"},
    {"textile", "fabric", "tissu", "tissus"},
    {"gift", "cadeau", "decoration", "lumineux"},
    {"plumbing", "plomberie", "robinet"},
    {"sport", "fitness", "gym"},
    {"electricity", "electricite", "electricit"},
]

EXPANSIONS: dict[str, set[str]] = {}
for _group in SYNONYM_GROUPS:
    for _word in _group:
        EXPANSIONS.setdefault(_word, set()).update(_group - {_word})

MIN_TOKEN_LENGTH = 3
DEFAULT_LIMIT = 5
MIN_SCORE = 0.3

_SCHEMA_READY = False


def _connect(database: Path | str | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(database or DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def normalize(text: str) -> str:
    """Lowercase, strip accents and collapse everything that is not a word."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^\w\u0600-\u06FF]+", " ", stripped.lower()).strip()


def stem(word: str) -> str:
    """Crude plural folding: femme/femmes and robe/robes should meet."""
    if len(word) > 4 and word.endswith("s"):
        return word[:-1]
    return word


def tokens(text: str) -> set[str]:
    words = set()
    for word in normalize(text).split():
        if len(word) < MIN_TOKEN_LENGTH or word in STOPWORDS:
            continue
        if word.startswith("ال") and len(word) > 4:
            word = word[2:]
        if word in STOPWORDS or len(word) < MIN_TOKEN_LENGTH:
            continue
        words.add(stem(word))
    return words


def expand(words: set[str]) -> set[str]:
    """Add the cross-language equivalents of the words we know about."""
    expanded = set(words)
    for word in words:
        for alias in EXPANSIONS.get(word, set()):
            expanded.add(stem(alias))
            expanded.update(EXPANSIONS.get(stem(alias), set()))
    return expanded


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    """Remember that the directory tables exist; nothing to migrate otherwise."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    owns = connection is None
    connection = connection or _connect()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS product_supplier_matches (
                product_key TEXT NOT NULL,
                supplier_id INTEGER NOT NULL,
                score REAL NOT NULL,
                matched_on TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (product_key, supplier_id)
            )
            """
        )
        connection.commit()
        _SCHEMA_READY = True
    except sqlite3.Error:
        # A read-only database must not stop matching from working.
        pass
    finally:
        if owns:
            connection.close()


def directory_rows(connection: sqlite3.Connection | None = None) -> list[dict]:
    owns = connection is None
    connection = connection or _connect()
    try:
        rows = connection.execute(
            """
            SELECT id, category_sheet, supplier_name, category, product_type,
                   phone, whatsapp, telegram, facebook, city, notes
            FROM imported_suppliers
            ORDER BY category_sheet, row_number, id
            """
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        if owns:
            connection.close()
    return [dict(row) for row in rows]


def _row_tokens(row: dict) -> set[str]:
    blob = " ".join(
        str(row.get(field) or "")
        for field in ("supplier_name", "category", "product_type", "category_sheet", "notes")
    )
    return tokens(blob)


def score_row(
    product_tokens: set[str],
    row_tokens: set[str],
    hint_tokens: set[str] | None = None,
) -> tuple[float, list[str], list[str]]:
    """Explainable score: product wording, row precision and category sheet.

    A supplier sheet is the strongest signal the guide really has — it is the
    category the wholesaler signed up under — so a matching sheet alone is a
    usable lead, while a shared product word on top of it is a strong one.
    """
    hint_tokens = hint_tokens or set()
    shared = product_tokens & row_tokens
    hint_shared = hint_tokens & row_tokens
    if not shared and not hint_shared:
        return 0.0, [], []
    coverage = len(shared) / len(product_tokens) if product_tokens else 0.0
    precision = len(shared) / len(row_tokens) if row_tokens else 0.0
    hint = len(hint_shared) / len(hint_tokens) if hint_tokens else 0.0
    score = round(0.55 * coverage + 0.15 * precision + 0.30 * hint, 3)
    return score, sorted(shared), sorted(hint_shared)


def match_suppliers(
    product_name: str,
    category_hint: str = "",
    limit: int = DEFAULT_LIMIT,
    min_score: float = MIN_SCORE,
    rows: list[dict] | None = None,
) -> list[dict]:
    """Directory suppliers whose category/name vocabulary overlaps the product."""
    product_tokens = expand(tokens(product_name))
    # The sheet names are already the guide's own vocabulary, so the category
    # hint is compared literally instead of being expanded twice.
    hint_tokens = tokens(category_hint)
    if not product_tokens and not hint_tokens:
        return []
    rows = rows if rows is not None else directory_rows()
    matches = []
    for row in rows:
        row_tokens = _row_tokens(row)
        score, shared, hint_shared = score_row(product_tokens, row_tokens, hint_tokens)
        if score < min_score:
            continue
        matches.append(
            {
                "id": row.get("id"),
                "supplier": row.get("supplier_name") or "Unnamed supplier",
                "category_sheet": row.get("category_sheet") or "",
                "category": row.get("category") or "",
                "product_type": row.get("product_type") or "",
                "phone": row.get("phone") or "",
                "whatsapp": row.get("whatsapp") or "",
                "telegram": row.get("telegram") or "",
                "facebook": row.get("facebook") or "",
                "city": row.get("city") or "",
                "score": score,
                "matched_on": sorted(set(shared) | set(hint_shared)),
            }
        )
    matches.sort(key=lambda item: (-item["score"], item["supplier"].lower()))
    return matches[:limit]


def coverage(products: list[dict], limit: int = DEFAULT_LIMIT, rows: list[dict] | None = None) -> dict:
    """Match a whole scan at once: per-product matches plus a coverage summary.

    Coverage is the business question the directory answers right now: for how
    many scanned products do we already know a wholesaler to call?
    """
    rows = rows if rows is not None else directory_rows()
    per_product = {}
    matched = 0
    supplier_hits: dict[str, dict] = {}
    for product in products:
        name = product.get("product") or product.get("key") or ""
        hint = product.get("category") or ""
        found = match_suppliers(name, hint, limit=limit, rows=rows)
        if found:
            matched += 1
        per_product[product.get("key") or name] = {
            "product": name,
            "category": hint,
            "matches": found,
        }
        for item in found:
            key = str(item.get("id"))
            entry = supplier_hits.setdefault(
                key,
                {**item, "products": 0},
            )
            entry["products"] += 1
    top_suppliers = sorted(
        supplier_hits.values(), key=lambda item: (-item["products"], -item["score"])
    )[:10]
    return {
        "products_total": len(products),
        "products_matched": matched,
        "directory_size": len(rows),
        "products": per_product,
        "top_suppliers": top_suppliers,
    }
