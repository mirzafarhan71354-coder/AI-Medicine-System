"""Build the local, current-product snapshot from the public FDA NDC API."""

from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import sys

import requests


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "medicine_catalog.sqlite3"
API_URL = "https://api.fda.gov/drug/ndc.json"
TODAY = date.today()
MAX_SKIP = 25000
PAGE_SIZE = 1000
PRODUCT_TYPES = ("HUMAN PRESCRIPTION DRUG", "HUMAN OTC DRUG")


def api_get(search, **params):
    response = requests.get(
        API_URL,
        params={"search": search, **params},
        timeout=30,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def count_records(search):
    payload = api_get(search, limit=1)
    if payload is None:
        return 0
    return payload["meta"]["results"]["total"]


def fetch_date_range(product_type, start, end):
    search = (
        f'product_type:"{product_type}" '
        f'AND listing_expiration_date:[{TODAY:%Y%m%d} TO 99991231] '
        f'AND marketing_start_date:[{start:%Y%m%d} TO {end:%Y%m%d}]'
    )
    total = count_records(search)
    if not total:
        return []

    if total > MAX_SKIP:
        if start >= end:
            raise RuntimeError(
                f"OpenFDA returned {total} records for one date; "
                "the supported pagination limit would omit records."
            )
        midpoint = start + timedelta(days=(end - start).days // 2)
        return (
            fetch_date_range(product_type, start, midpoint)
            + fetch_date_range(product_type, midpoint + timedelta(days=1), end)
        )

    records = []
    for offset in range(0, total, PAGE_SIZE):
        payload = api_get(search, limit=PAGE_SIZE, skip=offset)
        if payload is None:
            raise RuntimeError(f"OpenFDA result set changed while fetching: {search}")
        records.extend(payload["results"])
        print(
            f"\r{product_type}: {min(offset + PAGE_SIZE, total):,}/{total:,}",
            end="",
            flush=True,
        )
    print()
    return records


def normalize_name(value):
    return "".join(character.lower() for character in str(value or "") if character.isalnum())


def normalized_record(record):
    ingredients = record.get("active_ingredients") or []
    ingredient_text = "; ".join(
        " ".join(filter(None, (
            str(ingredient.get("name") or "").strip(),
            str(ingredient.get("strength") or "").strip(),
        )))
        for ingredient in ingredients
    )
    ingredient_names = [
        str(ingredient.get("name") or "").strip()
        for ingredient in ingredients
        if str(ingredient.get("name") or "").strip()
    ]
    brand = str(record.get("brand_name") or "").strip()
    brand_base = str(record.get("brand_name_base") or "").strip()
    generic = str(record.get("generic_name") or "").strip()
    name = brand or generic
    if not name:
        return None

    aliases = list(dict.fromkeys(
        value for value in (brand, brand_base, generic, *ingredient_names) if value
    ))
    categories = record.get("pharm_class") or []
    routes = record.get("route") or []
    product_id = record.get("product_id") or record.get("product_ndc")
    if not product_id:
        return None

    strength = ", ".join(
        str(ingredient.get("strength") or "").strip()
        for ingredient in ingredients
        if str(ingredient.get("strength") or "").strip()
    )
    dosage_form = str(record.get("dosage_form") or "")
    search_values = (
        name, brand_base, generic, ingredient_text, *ingredient_names,
        strength, dosage_form,
    )
    return (
        product_id,
        str(record.get("product_ndc") or ""),
        name,
        brand,
        brand_base,
        generic,
        ingredient_text,
        strength,
        dosage_form,
        str(record.get("labeler_name") or ""),
        "; ".join(str(category) for category in categories),
        str(record.get("product_type") or ""),
        ", ".join(str(route) for route in routes),
        json.dumps(aliases, ensure_ascii=True),
        normalize_name(" ".join(search_values)),
    )


def main():
    end = TODAY
    start = date(1900, 1, 1)
    records = []
    for product_type in PRODUCT_TYPES:
        records.extend(fetch_date_range(product_type, start, end))

    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    temporary_database = DATABASE.with_suffix(".sqlite3.tmp")
    if temporary_database.exists():
        temporary_database.unlink()

    connection = None
    try:
        with sqlite3.connect(temporary_database) as connection:
            connection.execute("""
                CREATE TABLE medicines (
                    product_id TEXT PRIMARY KEY,
                    product_ndc TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    brand_name TEXT NOT NULL,
                    brand_name_base TEXT NOT NULL,
                    generic_name TEXT NOT NULL,
                    active_ingredients TEXT NOT NULL,
                    strength TEXT NOT NULL,
                    dosage_form TEXT NOT NULL,
                    manufacturer TEXT NOT NULL,
                    category TEXT NOT NULL,
                    product_type TEXT NOT NULL,
                    route TEXT NOT NULL,
                    aliases TEXT NOT NULL,
                    search_text TEXT NOT NULL
                )
            """)
            connection.executemany(
                """
                INSERT OR IGNORE INTO medicines VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    row for record in records
                    if (row := normalized_record(record)) is not None
                ),
            )
            connection.execute("""
                DELETE FROM medicines
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM medicines
                    GROUP BY lower(name), lower(brand_name), lower(brand_name_base),
                             lower(generic_name), lower(active_ingredients),
                             lower(strength), lower(dosage_form), lower(manufacturer),
                             lower(category), lower(product_type), lower(route)
                )
            """)
            count = connection.execute("SELECT count(*) FROM medicines").fetchone()[0]
            connection.execute("""
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            metadata = {
                "source": "https://api.fda.gov/drug/ndc.json",
                "source_name": "FDA National Drug Code Directory",
                "as_of": TODAY.isoformat(),
                "record_count": str(count),
                "coverage": (
                    "Current US human prescription and OTC product listings with "
                    "marketing start dates from 1900 through the snapshot date."
                ),
                "limitations": (
                    "This is not an India-specific or complete global medicines register. "
                    "NDC records do not consistently provide uses, patient dosing, side "
                    "effects, or alternate spellings."
                ),
            }
            connection.executemany(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                metadata.items(),
            )
            connection.commit()

        connection.close()
        temporary_database.replace(DATABASE)
        print(f"Saved {count:,} distinct FDA NDC product listings to {DATABASE}")
    finally:
        if connection is not None:
            connection.close()
        if temporary_database.exists():
            temporary_database.unlink()


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, KeyError, RuntimeError, sqlite3.Error) as error:
        print(f"Catalog refresh failed: {error}", file=sys.stderr)
        raise
