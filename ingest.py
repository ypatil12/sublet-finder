#!/usr/bin/env python3
"""
Ingest pipeline: loads raw Facebook housing posts JSON, splits into batches,
and loads extracted results into DuckDB.

Usage:
    # After subagent extraction, load NYC results into DuckDB:
    python3 ingest.py --input data/large-4-02.json --extracted-dir data/extracted

    # Just split into LA batches (prep step):
    python3 ingest.py --dataset la --split-only
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
from pathlib import Path

import duckdb
from la_location_lookup import build_la_location_commutes, normalize_la_location_token
from neighborhood_lookup import build_neighborhood_commutes


BATCH_SIZE = 50
LOW_PRICE_CUTOFF = 300

DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
MONEY_RE = re.compile(r"\$(\d{1,2}(?:,\d{3})+|\d+(?:\.\d+)?)")
SEEKER_HINT_RE = re.compile(
    r"(^|\b)(iso\b|looking for|seeking|need a place|need housing|incoming .* student looking for housing|"
    r"moving to la .* looking for|am looking for|i['’]m looking for|im looking for)",
    re.IGNORECASE,
)
SEEKER_STRONG_START_RE = re.compile(
    r"^\s*(iso\b|seeking\b|looking for\b|hi[! ]+i['’ ]?m looking for\b|hello[! ]+i['’ ]?m looking for\b)",
    re.IGNORECASE,
)
ROOMMATE_OFFER_RE = re.compile(
    r"looking for\s+(?:\d+\s+)?(?:a\s+|an\s+)?(?:roommate|roommates|tenant|tenants|someone|subletter|person)\b",
    re.IGNORECASE,
)
OFFER_HINT_RE = re.compile(
    r"(sublet available|lease takeover|private room for rent|room for rent|available may|available now|"
    r"looking for someone to take over|private bedroom available|master bedroom available|we have .* room available|"
    r"looking for a roommate to move in|private room available)",
    re.IGNORECASE,
)
MONTHLY_MARKER_RE = re.compile(r"(?:/mo\b|/month\b|per month\b|monthly\b|rent\b)", re.IGNORECASE)
EXCLUDE_PRICE_CONTEXT_RE = re.compile(
    r"(deposit|security|application fee|utilities|utility|per day|/day|daily|day rate|per night|nightly|"
    r"\+\$|\bfee\b|credit score|income|combined income|guarantor|parking)",
    re.IGNORECASE,
)

DATASET_CONFIG = {
    "nyc": {
        "default_input": "data/large-4-02.json",
        "default_batch_dir": "data/batches",
        "default_extracted_dir": "data/extracted",
        "posts_table": "posts",
        "lookup_table": "neighborhood_commutes",
    },
    "la": {
        "default_input": "data/la_list.json",
        "default_batch_dir": "data/la_batches",
        "default_extracted_dir": "data/la_extracted",
        "posts_table": "la_posts",
        "lookup_table": "la_location_commutes",
    },
}


def split_into_batches(input_path: Path, batch_dir: Path, limit: int | None = None):
    """Split raw JSON into batch files with minimal fields for extraction."""
    batch_dir.mkdir(parents=True, exist_ok=True)

    with input_path.open() as handle:
        posts = json.load(handle)

    if limit:
        posts = posts[:limit]

    batch_num = 0
    for start in range(0, len(posts), BATCH_SIZE):
        end = min(start + BATCH_SIZE, len(posts))
        batch = []
        for i in range(start, end):
            post = posts[i]
            batch.append({
                "index": i,
                "text": post.get("text", ""),
                "user_name": post.get("user", {}).get("name", ""),
                "url": post.get("url", ""),
                "time": post.get("time", ""),
                "group_title": post.get("groupTitle", ""),
                "likes": post.get("likesCount", 0),
                "comments": post.get("commentsCount", 0),
            })

        out_path = batch_dir / f"batch_{batch_num:03d}.json"
        out_path.write_text(json.dumps(batch, indent=2))
        print(f"  Wrote {out_path.name} ({len(batch)} posts, indices {start}-{end - 1})")
        batch_num += 1

    print(f"Split {len(posts)} posts into {batch_num} batches in {batch_dir}")
    return batch_num


def normalize_location(dataset: str, value: str | None) -> str | None:
    if dataset != "la":
        return value
    return normalize_la_location_token(value)


def sanitize_date(value: str | None) -> str | None:
    if not value:
        return None
    match = DATE_RE.match(value)
    if not match:
        return None

    year, month, day = (int(part) for part in match.groups())
    if month < 1 or month > 12:
        return None
    _, month_days = calendar.monthrange(year, month)
    if day < 1 or day > month_days:
        return None
    return value


def classify_seeking_post(raw_text: str) -> bool:
    text = " ".join((raw_text or "").split())
    if not text:
        return False
    if ROOMMATE_OFFER_RE.search(text[:180]):
        return False
    if SEEKER_STRONG_START_RE.search(text):
        return True
    seeker = SEEKER_HINT_RE.search(text)
    offer = OFFER_HINT_RE.search(text)
    return bool(seeker and not offer)


def refine_la_location(raw_text: str, extracted_location: str | None) -> str | None:
    text = (raw_text or "").lower()
    if "jefferson park" in text:
        return "Jefferson Park"
    if "mar vista" in text:
        return "Mar Vista"
    if "del rey" in text:
        return "Del Rey"
    return extracted_location


def find_best_monthly_price(raw_text: str) -> int | None:
    text = raw_text or ""
    candidates: list[tuple[int, int]] = []

    for match in MONEY_RE.finditer(text):
        amount_text = match.group(1).replace(",", "")
        amount = float(amount_text)
        if amount < 500 or amount > 10000:
            continue

        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 40)
        context = text[start:end]

        score = 0
        if MONTHLY_MARKER_RE.search(context):
            score += 4
        if re.search(r"rent\s*[:=-]?\s*\$?", context, re.IGNORECASE):
            score += 3
        if EXCLUDE_PRICE_CONTEXT_RE.search(context):
            score -= 3
        if re.search(r"(bedroom|room|sublet|lease)", context, re.IGNORECASE):
            score += 1

        candidates.append((score, int(round(amount))))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, best_amount = candidates[0]
    if best_score < 0:
        return None
    return best_amount


def sanitize_record(dataset: str, raw: dict, rec: dict) -> dict:
    sanitized = dict(rec)
    raw_text = raw.get("text", "")

    sanitized["move_in"] = sanitize_date(sanitized.get("move_in"))
    sanitized["move_out"] = sanitize_date(sanitized.get("move_out"))

    if dataset != "la":
        sanitized["neighborhood"] = normalize_location(dataset, sanitized.get("neighborhood"))
        return sanitized

    sanitized["neighborhood"] = normalize_location(dataset, sanitized.get("neighborhood"))
    sanitized["neighborhood"] = refine_la_location(raw_text, sanitized.get("neighborhood"))

    if classify_seeking_post(raw_text):
        sanitized["is_offering"] = False

    extracted_price = sanitized.get("price")
    improved_price = find_best_monthly_price(raw_text)

    if sanitized.get("is_offering"):
        if extracted_price is None and improved_price is not None:
            sanitized["price"] = improved_price
        elif isinstance(extracted_price, int) and extracted_price < LOW_PRICE_CUTOFF:
            sanitized["price"] = improved_price
        elif isinstance(extracted_price, int) and improved_price is not None and extracted_price < 500 <= improved_price:
            sanitized["price"] = improved_price
    else:
        if extracted_price is not None and classify_seeking_post(raw_text):
            sanitized["price"] = None

    return sanitized


def collect_extracted_records(extracted_dir: Path) -> list[dict]:
    extracted: list[dict] = []
    for batch_file in sorted(extracted_dir.glob("batch_*.json")):
        with batch_file.open() as handle:
            extracted.extend(json.load(handle))
    return extracted


def load_post_rows(raw_posts: list[dict], extracted: list[dict], dataset: str) -> list[tuple]:
    rows = []
    for rec in extracted:
        idx = rec["index"]
        raw = raw_posts[idx]
        rec = sanitize_record(dataset, raw, rec)
        rows.append((
            idx,
            rec.get("url") or raw.get("url", ""),
            raw.get("time", ""),
            raw.get("user", {}).get("name", ""),
            raw.get("groupTitle", ""),
            raw.get("text", ""),
            rec.get("price"),
            rec.get("neighborhood"),
            rec.get("commute_minutes"),
            rec.get("move_in"),
            rec.get("move_out"),
            rec.get("duration_months"),
            rec.get("is_offering"),
            rec.get("has_laundry"),
            rec.get("gender_restriction", "none"),
            rec.get("is_furnished"),
            rec.get("poster_gender", "unknown"),
            raw.get("likesCount", 0),
            raw.get("commentsCount", 0),
        ))
    return rows


def create_posts_table(conn: duckdb.DuckDBPyConnection, table_name: str):
    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    conn.execute(f"""
        CREATE TABLE {table_name} (
            id              INTEGER PRIMARY KEY,
            url             TEXT,
            posted_at       TEXT,
            user_name       TEXT,
            group_name      TEXT,
            raw_text        TEXT,
            price           INTEGER,
            neighborhood    TEXT,
            commute_minutes INTEGER,
            move_in         TEXT,
            move_out        TEXT,
            duration_months INTEGER,
            is_offering     BOOLEAN,
            has_laundry     BOOLEAN,
            gender_restrict TEXT,
            is_furnished    BOOLEAN,
            poster_gender   TEXT,
            likes           INTEGER,
            comments        INTEGER
        )
    """)


def create_lookup_table(conn: duckdb.DuckDBPyConnection, dataset: str, table_name: str):
    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    if dataset == "nyc":
        conn.execute(f"""
            CREATE TABLE {table_name} (
                neighborhood    TEXT,
                borough         TEXT,
                commute_minutes INTEGER,
                source_type     TEXT,
                source_query    TEXT,
                source_url      TEXT,
                is_fallback     BOOLEAN
            )
        """)
        return

    conn.execute(f"""
        CREATE TABLE {table_name} (
            location_key       TEXT,
            canonical_location TEXT,
            location_type      TEXT,
            commute_minutes    INTEGER,
            source_type        TEXT,
            source_query       TEXT,
            source_url         TEXT,
            is_fallback        BOOLEAN
        )
    """)


def insert_lookup_rows(conn: duckdb.DuckDBPyConnection, dataset: str, table_name: str):
    if dataset == "nyc":
        lookup_rows = build_neighborhood_commutes()
        conn.executemany(
            f"INSERT INTO {table_name} VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.neighborhood,
                    row.borough,
                    row.commute_minutes,
                    row.source_type,
                    row.source_query,
                    row.source_url,
                    row.is_fallback,
                )
                for row in lookup_rows
            ],
        )
        return lookup_rows

    lookup_rows = build_la_location_commutes()
    conn.executemany(
        f"INSERT INTO {table_name} VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                row.location_key,
                row.canonical_location,
                row.location_type,
                row.commute_minutes,
                row.source_type,
                row.source_query,
                row.source_url,
                row.is_fallback,
            )
            for row in lookup_rows
        ],
    )
    return lookup_rows


def backfill_commutes(conn: duckdb.DuckDBPyConnection, dataset: str, posts_table: str, lookup_table: str):
    if dataset == "nyc":
        conn.execute(f"""
            UPDATE {posts_table}
            SET commute_minutes = nc.commute_minutes
            FROM {lookup_table} nc
            WHERE lower({posts_table}.neighborhood) = lower(nc.neighborhood)
        """)
        return

    conn.execute(f"""
        UPDATE {posts_table}
        SET commute_minutes = lc.commute_minutes
        FROM {lookup_table} lc
        WHERE lower({posts_table}.neighborhood) = lower(lc.location_key)
    """)


def lookup_match_count(conn: duckdb.DuckDBPyConnection, dataset: str, posts_table: str, lookup_table: str) -> int:
    if dataset == "nyc":
        return conn.execute(f"""
            SELECT COUNT(*) FROM {posts_table} p
            JOIN {lookup_table} nc ON lower(p.neighborhood) = lower(nc.neighborhood)
            WHERE p.neighborhood IS NOT NULL
        """).fetchone()[0]

    return conn.execute(f"""
        SELECT COUNT(*) FROM {posts_table} p
        JOIN {lookup_table} lc ON lower(p.neighborhood) = lower(lc.location_key)
        WHERE p.neighborhood IS NOT NULL
    """).fetchone()[0]


def print_summary(
    conn: duckdb.DuckDBPyConnection,
    dataset: str,
    db_path: Path,
    posts_table: str,
    lookup_table: str,
):
    total = conn.execute(f"SELECT COUNT(*) FROM {posts_table}").fetchone()[0]
    offerings = conn.execute(f"SELECT COUNT(*) FROM {posts_table} WHERE is_offering").fetchone()[0]
    seekers = total - offerings
    with_price = conn.execute(f"SELECT COUNT(*) FROM {posts_table} WHERE price IS NOT NULL").fetchone()[0]
    with_hood = conn.execute(f"SELECT COUNT(*) FROM {posts_table} WHERE neighborhood IS NOT NULL").fetchone()[0]
    with_movein = conn.execute(f"SELECT COUNT(*) FROM {posts_table} WHERE move_in IS NOT NULL").fetchone()[0]
    with_duration = conn.execute(f"SELECT COUNT(*) FROM {posts_table} WHERE duration_months IS NOT NULL").fetchone()[0]
    with_lookup = lookup_match_count(conn, dataset, posts_table, lookup_table)
    total_lookup = conn.execute(f"SELECT COUNT(*) FROM {lookup_table}").fetchone()[0]
    label = "NYC neighborhoods" if dataset == "nyc" else "LA locations"

    print(f"\nLoaded {total} posts into {db_path} ({posts_table})")
    print(f"  Offerings: {offerings}  |  Seekers: {seekers}")
    print(f"  With price: {with_price}  |  With neighborhood: {with_hood}")
    print(f"  With move-in: {with_movein}  |  With duration: {with_duration}")
    print(f"  Commute lookup: {with_lookup} posts matched across {total_lookup} {label}")

    print("\nNeighborhood breakdown (offerings only):")
    hoods = conn.execute(f"""
        SELECT neighborhood, COUNT(*) as n, ROUND(AVG(price)) as avg_price
        FROM {posts_table}
        WHERE is_offering AND neighborhood IS NOT NULL AND price IS NOT NULL
        GROUP BY neighborhood
        ORDER BY n DESC
        LIMIT 15
    """).fetchall()
    for name, count, avg in hoods:
        print(f"  {name:20s}  {count:3d} listings  avg ${avg:,.0f}")


def load_into_duckdb(input_path: Path, extracted_dir: Path, db_path: Path, dataset: str):
    with input_path.open() as handle:
        raw_posts = json.load(handle)

    extracted = collect_extracted_records(extracted_dir)
    if not extracted:
        print("No extracted batch files found. Run subagent extraction first.")
        return

    config = DATASET_CONFIG[dataset]
    rows = load_post_rows(raw_posts, extracted, dataset)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    posts_table = config["posts_table"]
    lookup_table = config["lookup_table"]

    create_posts_table(conn, posts_table)
    create_lookup_table(conn, dataset, lookup_table)

    conn.executemany(
        f"INSERT INTO {posts_table} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    insert_lookup_rows(conn, dataset, lookup_table)
    backfill_commutes(conn, dataset, posts_table, lookup_table)
    print_summary(conn, dataset, db_path, posts_table, lookup_table)
    conn.close()


def resolve_path(arg_value: str | None, dataset: str, key: str) -> Path:
    if arg_value:
        return Path(arg_value)
    return Path(DATASET_CONFIG[dataset][key])


def main():
    parser = argparse.ArgumentParser(description="Sublet data ingest pipeline")
    parser.add_argument("--dataset", choices=sorted(DATASET_CONFIG), default="nyc", help="Dataset profile to use")
    parser.add_argument("--input", help="Path to raw JSON file")
    parser.add_argument("--extracted-dir", help="Directory with extracted batch results")
    parser.add_argument("--batch-dir", help="Directory for batch files")
    parser.add_argument("--db", default="output/sublets.duckdb", help="DuckDB output path")
    parser.add_argument("--split-only", action="store_true", help="Only split into batches, don't load")
    parser.add_argument("--limit", type=int, help="Only process first N posts")
    args = parser.parse_args()

    input_path = resolve_path(args.input, args.dataset, "default_input")
    batch_dir = resolve_path(args.batch_dir, args.dataset, "default_batch_dir")
    extracted_dir = resolve_path(args.extracted_dir, args.dataset, "default_extracted_dir")
    db_path = Path(args.db)

    if args.split_only:
        split_into_batches(input_path, batch_dir, args.limit)
    else:
        load_into_duckdb(input_path, extracted_dir, db_path, args.dataset)


if __name__ == "__main__":
    main()
