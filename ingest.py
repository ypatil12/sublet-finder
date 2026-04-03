#!/usr/bin/env python3
"""
Ingest pipeline: loads raw Facebook housing posts JSON, splits into batches,
and loads extracted results into DuckDB.

Usage:
    # After subagent extraction, load results into DuckDB:
    python3 ingest.py --input data/large-4-02.json --extracted-dir data/extracted

    # Just split into batches (prep step):
    python3 ingest.py --input data/large-4-02.json --split-only --limit 100
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
from neighborhood_lookup import build_neighborhood_commutes


BATCH_SIZE = 50


def split_into_batches(input_path: Path, batch_dir: Path, limit: int | None = None):
    """Split raw JSON into batch files with minimal fields for extraction."""
    batch_dir.mkdir(parents=True, exist_ok=True)

    with open(input_path) as f:
        posts = json.load(f)

    if limit:
        posts = posts[:limit]

    batch_num = 0
    for start in range(0, len(posts), BATCH_SIZE):
        end = min(start + BATCH_SIZE, len(posts))
        batch = []
        for i in range(start, end):
            p = posts[i]
            batch.append({
                "index": i,
                "text": p.get("text", ""),
                "user_name": p.get("user", {}).get("name", ""),
                "url": p.get("url", ""),
                "time": p.get("time", ""),
                "group_title": p.get("groupTitle", ""),
                "likes": p.get("likesCount", 0),
                "comments": p.get("commentsCount", 0),
            })

        out_path = batch_dir / f"batch_{batch_num:03d}.json"
        out_path.write_text(json.dumps(batch, indent=2))
        print(f"  Wrote {out_path.name} ({len(batch)} posts, indices {start}-{end - 1})")
        batch_num += 1

    print(f"Split {len(posts)} posts into {batch_num} batches in {batch_dir}")
    return batch_num


def load_into_duckdb(input_path: Path, extracted_dir: Path, db_path: Path):
    """Load extracted batch results + original metadata into DuckDB."""
    with open(input_path) as f:
        raw_posts = json.load(f)

    # Collect all extracted records
    extracted = []
    for batch_file in sorted(extracted_dir.glob("batch_*.json")):
        with open(batch_file) as f:
            extracted.extend(json.load(f))

    if not extracted:
        print("No extracted batch files found. Run subagent extraction first.")
        return

    # Build rows
    rows = []
    for rec in extracted:
        idx = rec["index"]
        raw = raw_posts[idx]
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

    # Create database
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    conn.execute("DROP TABLE IF EXISTS posts")
    conn.execute("""
        CREATE TABLE posts (
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
    conn.execute("DROP TABLE IF EXISTS neighborhood_commutes")
    conn.execute("""
        CREATE TABLE neighborhood_commutes (
            neighborhood    TEXT,
            borough         TEXT,
            commute_minutes INTEGER,
            source_type     TEXT,
            source_query    TEXT,
            source_url      TEXT,
            is_fallback     BOOLEAN
        )
    """)

    conn.executemany(
        "INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    lookup_rows = build_neighborhood_commutes()
    conn.executemany(
        "INSERT INTO neighborhood_commutes VALUES (?, ?, ?, ?, ?, ?, ?)",
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
    conn.execute("""
        UPDATE posts
        SET commute_minutes = nc.commute_minutes
        FROM neighborhood_commutes nc
        WHERE lower(posts.neighborhood) = lower(nc.neighborhood)
    """)

    # Print summary
    total = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    offerings = conn.execute("SELECT COUNT(*) FROM posts WHERE is_offering").fetchone()[0]
    seekers = total - offerings
    with_price = conn.execute("SELECT COUNT(*) FROM posts WHERE price IS NOT NULL").fetchone()[0]
    with_hood = conn.execute("SELECT COUNT(*) FROM posts WHERE neighborhood IS NOT NULL").fetchone()[0]
    with_movein = conn.execute("SELECT COUNT(*) FROM posts WHERE move_in IS NOT NULL").fetchone()[0]
    with_duration = conn.execute("SELECT COUNT(*) FROM posts WHERE duration_months IS NOT NULL").fetchone()[0]
    with_lookup = conn.execute("""
        SELECT COUNT(*) FROM posts p
        JOIN neighborhood_commutes nc ON lower(p.neighborhood) = lower(nc.neighborhood)
        WHERE p.neighborhood IS NOT NULL
    """).fetchone()[0]
    total_lookup = conn.execute("SELECT COUNT(*) FROM neighborhood_commutes").fetchone()[0]

    print(f"\nLoaded {total} posts into {db_path}")
    print(f"  Offerings: {offerings}  |  Seekers: {seekers}")
    print(f"  With price: {with_price}  |  With neighborhood: {with_hood}")
    print(f"  With move-in: {with_movein}  |  With duration: {with_duration}")
    print(f"  Commute lookup: {with_lookup} posts matched across {total_lookup} NYC neighborhoods")

    # Neighborhood breakdown
    print("\nNeighborhood breakdown (offerings only):")
    hoods = conn.execute("""
        SELECT neighborhood, COUNT(*) as n, ROUND(AVG(price)) as avg_price
        FROM posts
        WHERE is_offering AND neighborhood IS NOT NULL AND price IS NOT NULL
        GROUP BY neighborhood
        ORDER BY n DESC
        LIMIT 15
    """).fetchall()
    for name, count, avg in hoods:
        print(f"  {name:20s}  {count:3d} listings  avg ${avg:,.0f}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Sublet data ingest pipeline")
    parser.add_argument("--input", required=True, help="Path to raw JSON file")
    parser.add_argument("--extracted-dir", default="data/extracted", help="Directory with extracted batch results")
    parser.add_argument("--batch-dir", default="data/batches", help="Directory for batch files")
    parser.add_argument("--db", default="output/sublets.duckdb", help="DuckDB output path")
    parser.add_argument("--split-only", action="store_true", help="Only split into batches, don't load")
    parser.add_argument("--limit", type=int, help="Only process first N posts")
    args = parser.parse_args()

    input_path = Path(args.input)
    batch_dir = Path(args.batch_dir)
    extracted_dir = Path(args.extracted_dir)
    db_path = Path(args.db)

    if args.split_only:
        split_into_batches(input_path, batch_dir, args.limit)
    else:
        load_into_duckdb(input_path, extracted_dir, db_path)


if __name__ == "__main__":
    main()
