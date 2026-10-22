#!/usr/bin/env python3
"""Import every saved preview tab of the supplier guide.

Each tab was saved from the sheet's own preview page, so the rows and column
headers are exactly what the document contains. The legal-notice tab is not a
supplier category and is skipped by name.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from suppliers_import import dataset_from_rows, replace_with, rows_from_html


ROOT = Path(__file__).resolve().parent
TABS_JSON = ROOT / "data" / "tabs" / "tabs.json"
SKIP_TABS = {"Mention Légale"}


def html_for(gid: str) -> Path | None:
    path = ROOT / "data" / "tabs" / f"prev_{gid}.html"
    return path if path.exists() else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "telegram_research.db")
    parser.add_argument("--dry-run", action="store_true", help="Only report what each tab contains")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Import the tabs that are saved and report the rest (default: refuse to replace the tables)",
    )
    args = parser.parse_args(argv)

    tabs = json.loads(TABS_JSON.read_text(encoding="utf-8"))
    datasets, missing, skipped = [], [], []
    print(f"{'category':44} {'rows':>5} {'header':>6}  columns")
    for name, gid in tabs:
        name = name.strip()
        if name in SKIP_TABS:
            skipped.append(name)
            continue
        path = html_for(gid)
        if path is None:
            missing.append(name)
            continue
        data = dataset_from_rows(name, rows_from_html(path), f"preview:{gid}", gid)
        if data is None:
            missing.append(name)
            continue
        datasets.append(data)
        print(
            f"{name[:44]:44} {len(data['records']):>5} "
            f"{data['header_row'] or '-':>6}  {', '.join(data['headers'][:8])}"
        )

    total = sum(len(data["records"]) for data in datasets)
    print(f"\ncategories imported: {len(datasets)}/{len(tabs) - len(skipped)}")
    print(f"supplier rows: {total}")
    if skipped:
        print(f"skipped (not suppliers): {', '.join(skipped)}")
    if missing:
        print(f"missing saved tabs: {', '.join(missing)}")
        if not args.allow_partial:
            print("Nothing imported; pass --allow-partial to import the saved tabs anyway.")
            return 1
    if args.dry_run:
        return 0

    connection = sqlite3.connect(args.database)
    try:
        replace_with(connection, datasets)
        print(f"database replaced with {total} rows from {len(datasets)} categories")
    finally:
        connection.close()
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
