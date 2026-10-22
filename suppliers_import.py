#!/usr/bin/env python3
"""Import the supplier guide exactly as it is written.

Every worksheet becomes a category; every non-empty row becomes one supplier row
whose raw column values are preserved under the worksheet's own header names.
Nothing is invented: a blank cell stays blank, and a row is only kept when it
carries at least one contact or name.

Usage
-----
    python suppliers_import.py --workbook "guide.xlsx"
    python suppliers_import.py --html saved_sheet.html --sheet sport
    python suppliers_import.py --report

The workbook form replaces the current supplier tables, because the guide is the
single reference source and re-importing must never duplicate rows.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "telegram_research.db"

# Header keywords, in French / Arabic / English, longest match wins.
HEADER_HINTS = {
    "supplier_name": [
        "nom de la boutique", "nom boutique", "nom du fournisseur", "nom fournisseur",
        "nom", "boutique", "fournisseur", "supplier name", "supplier", "shop name", "store",
        "اسم المحل", "اسم المتجر", "اسم التاجر", "اسم المورد", "المتجر", "المحل", "التاجر",
        "المورد", "الاسم", "اسم القناة", "القناة", "قناة", "اسم الصفحة", "الصفحة",
    ],
    "phone": [
        "numéro de téléphone", "numero de telephone", "numéro téléphone", "telephone",
        "téléphone", "phone number", "phone", "tel", "هاتف", "الهاتف", "رقم الهاتف",
        "رقم الهاتف المحمول", "رقم",
    ],
    "whatsapp": ["whatsapp", "whats app", "واتساب", "واتس اب", "واتس"],
    "telegram": [
        "telegram", "t.me", "تلغرام", "تيليغرام", "تيليجرام", "تلجرام",
        "المعرف", "معرف", "اسم المستخدم", "username", "identifiant", "pseudo",
        "رابط القناة", "رابط التلغرام", "lien telegram",
    ],
    "facebook": ["page facebook", "facebook page", "facebook", "فيسبوك", "فايسبوك"],
    "delivery": ["livraison", "delivery", "توصيل", "التوصيل"],
    "product_type": [
        "type de produit", "type produit", "type des produit", "produits", "produit",
        "product type", "products", "product", "نوع المنتج", "نوع المنتوج", "المنتوج",
        "المنتج", "التخصص", "تخصص", "catégorie", "categorie", "category",
    ],
    "city": ["ville", "city", "wilaya", "مدينة", "ولاية", "البلدية", "بلدية"],
}

EMPTY_MARKERS = {"", "//", "-", "--", "---", "n/a", "na", "none", "null", "nan", "/"}

# A worksheet whose first column is a channel or contact link instead of a shop
# name (the Telegram-channel list) still has a header row, it just never
# mentions a shop name.
CONTACT_FIELDS = {"phone", "whatsapp", "telegram", "facebook"}


def _norm(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("ـ", "").lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n.:;,،؛-_/\\")


def classify_header(header: str) -> str | None:
    """Return the field a column header refers to, or None when unrecognised."""
    key = _norm(header)
    if not key:
        return None
    best_field, best_len = None, 0
    for field, hints in HEADER_HINTS.items():
        for hint in hints:
            hint_key = _norm(hint)
            if not hint_key:
                continue
            if hint_key == key or hint_key in key:
                if len(hint_key) > best_len:
                    best_field, best_len = field, len(hint_key)
    return best_field


def clean_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = html.unescape(str(value)).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def meaningful(value: str) -> bool:
    return _norm(value) not in EMPTY_MARKERS


def row_is_blank(values) -> bool:
    return not any(meaningful(clean_cell(v)) for v in values)


def find_header(rows, limit=20):
    """Find the most header-like row within the first `limit` rows."""
    best_index, best_score, best_fields = None, 0, {}
    for index, row in enumerate(rows[:limit]):
        fields = {}
        for position, value in enumerate(row):
            field = classify_header(clean_cell(value))
            if field and field not in fields:
                fields[field] = position
        score = len(fields)
        if score > best_score:
            best_index, best_score, best_fields = index, score, fields
    if best_score >= 2 and (
        "supplier_name" in best_fields
        or CONTACT_FIELDS.intersection(best_fields)
    ):
        return best_index, best_fields
    if best_score >= 3:
        return best_index, best_fields
    # No recognisable header: treat the first non-empty row as the header so the
    # data is still imported verbatim instead of silently dropped.
    for index, row in enumerate(rows[:limit]):
        if not row_is_blank(row):
            return index, {}
    return None, {}


def build_headers(header_row, fields):
    headers = []
    seen = {}
    for position, value in enumerate(header_row):
        label = clean_cell(value) or f"Column {chr(ord('A') + position)}"
        count = seen.get(label, 0) + 1
        seen[label] = count
        headers.append(label if count == 1 else f"{label} ({count})")
    return headers


class _TableParser(HTMLParser):
    """Collect the rows of the first <table> in a saved Google Sheets page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None
        self._in_table = False

    def handle_starttag(self, tag, attrs):
        if tag == "table" and not self._in_table:
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._row = []
        elif self._in_table and tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            # Keep blank rows: the sheet's real row numbers depend on them.
            self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._in_table:
            self._in_table = False


def rows_from_html(path: Path):
    parser = _TableParser()
    parser.feed(Path(path).read_text(encoding="utf-8", errors="ignore"))
    return parser.rows


def rows_from_workbook(path: Path):
    try:
        import openpyxl
    except ImportError as error:  # pragma: no cover - dependency is installed
        raise SystemExit("openpyxl is required to read .xlsx files") from error
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        for worksheet in workbook.worksheets:
            rows = [list(row) for row in worksheet.iter_rows(values_only=True)]
            yield worksheet.title, rows
    finally:
        workbook.close()


def leading_index_column(header_row, body_rows) -> bool:
    """True when column A is just the sheet's row counter (1, 2, 3, ...).

    The saved preview page renders the row number as a first cell, and many
    workbooks keep an unlabelled numbering column. It is not a column of the
    guide, so it must not become one.
    """
    if not header_row:
        return False
    label = clean_cell(header_row[0])
    if label and not label.isdigit():
        return False
    values = [clean_cell(row[0]) for row in body_rows if row and clean_cell(row[0])]
    if len(values) < 3:
        return False
    return all(value.isdigit() for value in values)


def dataset_from_rows_fallback(sheet_name, rows, source_file, gid, width):
    """Import a worksheet that has no header row, using positional labels."""
    headers = [f"Column {chr(ord('A') + position)}" for position in range(width)]
    fields = {"supplier_name": 0}
    records = []
    for offset, row in enumerate(rows, start=1):
        if row_is_blank(row):
            continue
        values = [clean_cell(value) for value in row]
        raw = {header: (values[position] if position < len(values) else "")
               for position, header in enumerate(headers)}
        name = values[0] if values else ""
        phone = values[1] if len(values) > 1 else ""
        if not meaningful(name) and not meaningful(phone):
            continue
        records.append(
            {
                "row_number": offset,
                "supplier_name": name or phone,
                "phone": phone,
                "whatsapp": "",
                "telegram": "",
                "facebook": "",
                "delivery": "",
                "product_type": "",
                "city": "",
                "raw": raw,
                "raw_json": json.dumps(raw, ensure_ascii=False),
            }
        )
    return {
        "sheet_name": sheet_name,
        "gid": gid,
        "source_file": source_file,
        "header_row": 0,
        "headers": headers,
        "fields": fields,
        "records": records,
    }


def dataset_from_rows(sheet_name: str, rows, source_file: str, gid: str = ""):
    """Turn raw worksheet rows into mapped supplier records without inventing data."""
    header_index, fields = find_header(rows)
    if header_index is None:
        return None
    if not fields:
        # No recognisable header in this worksheet. Keep every row verbatim
        # under positional column labels instead of dropping the tab; only the
        # leading title rows (a single filled cell) are skipped.
        while rows and (
            row_is_blank(rows[0])
            or sum(1 for value in rows[0] if meaningful(clean_cell(value))) < 2
        ):
            rows = rows[1:]
        if not rows:
            return None
        width = max(len(row) for row in rows)
        return dataset_from_rows_fallback(sheet_name, rows, source_file, gid, width)
    header_row = rows[header_index]
    body_rows = rows[header_index + 1:]
    drop_index_column = leading_index_column(header_row, body_rows)
    headers = build_headers(header_row, fields)
    if drop_index_column:
        headers = headers[1:]
        fields = {field: position - 1 for field, position in fields.items() if position > 0}
    header_labels = {_norm(header) for header in headers if _norm(header)}
    records = []
    for offset, row in enumerate(body_rows, start=header_index + 2):
        if row_is_blank(row):
            continue
        values = [clean_cell(v) for v in row]
        if drop_index_column:
            values = values[1:]
        raw = {}
        for position, header in enumerate(headers):
            raw[header] = values[position] if position < len(values) else ""
        # Skip a repeated header row inside the body (two or more cells that are
        # literally the header labels), but never a supplier whose name merely
        # sounds like a column title.
        if sum(1 for value in values if _norm(value) and _norm(value) in header_labels) >= 2:
            continue
        mapped = {}
        for field, position in fields.items():
            mapped[field] = values[position] if position < len(values) else ""
        name = mapped.get("supplier_name", "")
        phone = mapped.get("phone", "")
        telegram = mapped.get("telegram", "")
        whatsapp = mapped.get("whatsapp", "")
        facebook = mapped.get("facebook", "")
        if not any(meaningful(v) for v in (name, phone, telegram, whatsapp, facebook)):
            continue
        if not name:
            # Keep the row, label it by the handle it advertises rather than
            # inventing a shop name.
            name = telegram or whatsapp or phone
        records.append(
            {
                "row_number": offset,
                "supplier_name": name,
                "phone": phone,
                "whatsapp": whatsapp,
                "telegram": telegram,
                "facebook": facebook,
                "delivery": mapped.get("delivery", ""),
                "product_type": mapped.get("product_type", ""),
                "city": mapped.get("city", ""),
                "raw": raw,
                "raw_json": json.dumps(raw, ensure_ascii=False),
            }
        )
    return {
        "sheet_name": sheet_name,
        "gid": gid,
        "source_file": source_file,
        "header_row": header_index + 1,
        "headers": headers,
        "fields": fields,
        "records": records,
    }


def ensure_schema(connection: sqlite3.Connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS supplier_sheets (
            sheet_name TEXT PRIMARY KEY,
            gid TEXT,
            source_file TEXT,
            header_row INTEGER,
            columns_json TEXT,
            row_count INTEGER,
            imported_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS imported_suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_sheet TEXT,
            supplier_name TEXT,
            category TEXT,
            phone TEXT,
            city TEXT,
            notes TEXT,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    existing = {row[1] for row in connection.execute("PRAGMA table_info(imported_suppliers)")}
    for column, kind in (
        ("row_number", "INTEGER"),
        ("whatsapp", "TEXT"),
        ("telegram", "TEXT"),
        ("facebook", "TEXT"),
        ("delivery", "TEXT"),
        ("product_type", "TEXT"),
        ("raw_json", "TEXT"),
        ("source_file", "TEXT"),
        ("gid", "TEXT"),
    ):
        if column not in existing:
            connection.execute(f"ALTER TABLE imported_suppliers ADD COLUMN {column} {kind}")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            supplier_name TEXT,
            contact_info TEXT,
            products TEXT,
            location TEXT,
            notes TEXT,
            source_sheet TEXT
        )
        """
    )


def replace_with(connection: sqlite3.Connection, datasets):
    """Replace the supplier tables with exactly the rows found in the source."""
    ensure_schema(connection)
    imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    connection.execute("DELETE FROM imported_suppliers")
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='suppliers'"
    ).fetchone():
        connection.execute("DELETE FROM suppliers")
    connection.execute("DELETE FROM supplier_sheets")
    total = 0
    for data in datasets:
        if data is None:
            continue
        connection.execute(
            """
            INSERT OR REPLACE INTO supplier_sheets
                (sheet_name, gid, source_file, header_row, columns_json, row_count, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["sheet_name"],
                data["gid"],
                data["source_file"],
                data["header_row"],
                json.dumps(data["headers"], ensure_ascii=False),
                len(data["records"]),
                imported_at,
            ),
        )
        for record in data["records"]:
            notes = ""
            if meaningful(record["delivery"]):
                notes = f"Livraison: {record['delivery']}"
            connection.execute(
                """
                INSERT INTO imported_suppliers (
                    category_sheet, row_number, supplier_name, category, phone, city,
                    notes, whatsapp, telegram, facebook, delivery, product_type,
                    raw_json, source_file, gid, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["sheet_name"],
                    record["row_number"],
                    record["supplier_name"],
                    record["product_type"] or data["sheet_name"],
                    record["phone"],
                    record["city"],
                    notes,
                    record["whatsapp"],
                    record["telegram"],
                    record["facebook"],
                    record["delivery"],
                    record["product_type"],
                    record["raw_json"],
                    data["source_file"],
                    data["gid"],
                    imported_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO suppliers (
                    category, supplier_name, contact_info, products, location,
                    notes, source_sheet
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["sheet_name"],
                    record["supplier_name"],
                    record["phone"],
                    record["product_type"],
                    record["city"],
                    notes,
                    data["sheet_name"],
                ),
            )
            total += 1
    connection.commit()
    return total


def report(connection: sqlite3.Connection):
    rows = connection.execute(
        """
        SELECT sheet_name, source_file, header_row, columns_json, row_count
        FROM supplier_sheets ORDER BY sheet_name
        """
    ).fetchall()
    print(f"sheeted categories: {len(rows)}")
    print(f"{'sheet':42} {'rows':>5}  {'header':>6}  columns")
    for sheet, source, header_row, columns_json, row_count in rows:
        columns = json.loads(columns_json or "[]")
        print(f"{sheet[:42]:42} {row_count:>5}  {header_row if header_row else '-':>6}  {', '.join(columns)}")
    total = connection.execute("SELECT COUNT(*) FROM imported_suppliers").fetchone()[0]
    print(f"total supplier rows: {total}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--workbook", type=Path, help="Exported .xlsx of the guide")
    source.add_argument("--html", type=Path, help="Saved Google Sheets preview page")
    source.add_argument("--report", action="store_true", help="Show what is in the database")
    parser.add_argument("--sheet", default="", help="Sheet name for --html (required)")
    parser.add_argument("--gid", default="", help="Optional worksheet id for --html")
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)

    connection = sqlite3.connect(args.database)
    try:
        ensure_schema(connection)
        if args.report:
            report(connection)
            return 0
        datasets = []
        if args.workbook:
            if not args.workbook.exists():
                raise SystemExit(f"Workbook not found: {args.workbook}")
            for sheet_name, rows in rows_from_workbook(args.workbook):
                dataset = dataset_from_rows(sheet_name, rows, args.workbook.name)
                if dataset is None:
                    print(f"WARNING: {sheet_name}: no header row found, skipped", file=sys.stderr)
                    continue
                datasets.append(dataset)
        else:
            if not args.html.exists():
                raise SystemExit(f"HTML not found: {args.html}")
            if not args.sheet:
                raise SystemExit("--sheet is required with --html")
            rows = rows_from_html(args.html)
            dataset = dataset_from_rows(args.sheet, rows, args.html.name, args.gid)
            if dataset is None:
                raise SystemExit("No table rows found in that page")
            datasets.append(dataset)

        total = replace_with(connection, datasets)
        for data in datasets:
            print(f"{data['sheet_name']}: {len(data['records'])} suppliers (header row {data['header_row']})")
        print(f"imported {total} supplier rows into {args.database}")
        report(connection)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
