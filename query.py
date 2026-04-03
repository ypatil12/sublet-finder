#!/usr/bin/env python3
"""
Query sublet listings from DuckDB.

Usage:
    python3 query.py                                    # Best matches with defaults
    python3 query.py --max-rent 2500 --max-commute 20   # Custom filters
    python3 query.py --neighborhood "East Village,UES"   # Filter by neighborhood
    python3 query.py --mode stats                        # Show statistics
    python3 query.py --mode all                          # All offerings
    python3 query.py --sql "SELECT * FROM posts LIMIT 5" # Custom SQL
"""

import argparse
import duckdb


DB_PATH = "output/sublets.duckdb"


def print_results(rows, columns):
    if not rows:
        print("  No results found.")
        return

    for row in rows:
        rec = dict(zip(columns, row))
        price_str = f"${rec.get('price', '?'):,}" if rec.get('price') else "price unknown"
        hood = rec.get('neighborhood') or 'location unknown'
        commute = f"{rec['commute_minutes']}min" if rec.get('commute_minutes') else "?min"
        move_in = rec.get('move_in') or 'date unknown'
        dur = f"{rec['duration_months']}mo" if rec.get('duration_months') else "?mo"
        furn = "furnished" if rec.get('is_furnished') else ""
        laundry = "W/D" if rec.get('has_laundry') else ""
        gender = rec.get('poster_gender', '?')
        name = rec.get('user_name', 'Unknown')
        url = rec.get('url', '')
        amenities = " | ".join(filter(None, [furn, laundry]))

        print(f"\n  #{rec.get('id', '?')} | {name} ({gender}) | {price_str} | {hood} ({commute}) | {move_in} | {dur}")
        if amenities:
            print(f"     {amenities}")
        # Show first 200 chars of text
        text = (rec.get('raw_text') or '')[:200].replace('\n', ' ')
        if text:
            print(f"     {text}")
        if url:
            print(f"     {url}")


def cmd_best_matches(conn, args):
    """Find offerings matching criteria."""
    params = []
    conditions = ["is_offering = true"]

    if args.max_rent:
        conditions.append("price <= ?")
        params.append(args.max_rent)
    if args.min_rent:
        conditions.append("price >= ?")
        params.append(args.min_rent)
    if args.max_commute:
        conditions.append("commute_minutes <= ?")
        params.append(args.max_commute)
    if args.neighborhood:
        hoods = [h.strip() for h in args.neighborhood.split(",")]
        placeholders = ",".join(["?"] * len(hoods))
        conditions.append(f"neighborhood IN ({placeholders})")
        params.extend(hoods)
    if args.move_by:
        conditions.append("move_in <= ?")
        params.append(args.move_by)
    if args.min_duration:
        conditions.append("duration_months >= ?")
        params.append(args.min_duration)
    if args.max_duration:
        conditions.append("duration_months <= ?")
        params.append(args.max_duration)
    if args.furnished:
        conditions.append("is_furnished = true")
    if args.laundry:
        conditions.append("has_laundry = true")
    if args.gender:
        conditions.append("gender_restrict = 'none' OR gender_restrict IS NULL")

    # Require at least price and neighborhood to be known
    conditions.append("price IS NOT NULL")
    conditions.append("neighborhood IS NOT NULL")

    where = " AND ".join(conditions)

    if args.dedup:
        # Deduplicate cross-posts: keep earliest post per (user_name, price, neighborhood)
        sql = f"""
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY user_name, price, neighborhood
                    ORDER BY id ASC
                ) as rn
                FROM posts
                WHERE {where}
            )
            SELECT id, url, user_name, price, neighborhood, commute_minutes,
                   move_in, move_out, duration_months, is_furnished, has_laundry,
                   gender_restrict, poster_gender, raw_text
            FROM ranked WHERE rn = 1
            ORDER BY commute_minutes ASC, price ASC
            LIMIT ?
        """
    else:
        sql = f"""
            SELECT id, url, user_name, price, neighborhood, commute_minutes,
                   move_in, move_out, duration_months, is_furnished, has_laundry,
                   gender_restrict, poster_gender, raw_text
            FROM posts
            WHERE {where}
            ORDER BY commute_minutes ASC, price ASC
            LIMIT ?
        """
    params.append(args.limit)

    results = conn.execute(sql, params).fetchall()
    columns = ["id", "url", "user_name", "price", "neighborhood", "commute_minutes",
               "move_in", "move_out", "duration_months", "is_furnished", "has_laundry",
               "gender_restrict", "poster_gender", "raw_text"]

    print(f"\n{'='*60}")
    print(f"Best Matches ({len(results)} found)")
    print(f"Filters: max ${args.max_rent}/mo, {args.max_commute}min commute"
          + (f", move by {args.move_by}" if args.move_by else "")
          + (f", {args.min_duration}-{args.max_duration}mo" if args.min_duration else ""))
    print(f"{'='*60}")
    print_results(results, columns)


def cmd_stats(conn):
    """Show database statistics."""
    print("\n=== Database Statistics ===\n")

    total = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    offerings = conn.execute("SELECT COUNT(*) FROM posts WHERE is_offering").fetchone()[0]
    print(f"Total posts: {total}")
    print(f"Offerings: {offerings}  |  Seekers: {total - offerings}")

    print(f"\nField coverage (offerings only):")
    for field in ["price", "neighborhood", "move_in", "duration_months", "is_furnished", "has_laundry"]:
        n = conn.execute(f"SELECT COUNT(*) FROM posts WHERE is_offering AND {field} IS NOT NULL").fetchone()[0]
        pct = n / offerings * 100 if offerings else 0
        print(f"  {field:20s}: {n:4d} / {offerings} ({pct:.0f}%)")

    print(f"\nPrice distribution (offerings):")
    brackets = conn.execute("""
        SELECT
            CASE
                WHEN price < 1500 THEN 'Under $1,500'
                WHEN price < 2000 THEN '$1,500-$2,000'
                WHEN price < 2500 THEN '$2,000-$2,500'
                WHEN price < 3000 THEN '$2,500-$3,000'
                ELSE '$3,000+'
            END as bracket,
            COUNT(*) as n
        FROM posts WHERE is_offering AND price IS NOT NULL
        GROUP BY bracket ORDER BY MIN(price)
    """).fetchall()
    for bracket, n in brackets:
        print(f"  {bracket:20s}: {n}")

    print(f"\nNeighborhood breakdown (offerings):")
    hoods = conn.execute("""
        SELECT neighborhood, COUNT(*) as n, ROUND(AVG(price)) as avg_price,
               ROUND(AVG(commute_minutes)) as avg_commute
        FROM posts
        WHERE is_offering AND neighborhood IS NOT NULL
        GROUP BY neighborhood ORDER BY n DESC LIMIT 20
    """).fetchall()
    for name, count, avg_price, avg_commute in hoods:
        price_s = f"avg ${avg_price:,.0f}" if avg_price else "no prices"
        commute_s = f"{avg_commute:.0f}min" if avg_commute else ""
        print(f"  {name:20s}: {count:3d} listings  {price_s:>12s}  {commute_s}")

    print(f"\nPoster gender breakdown:")
    genders = conn.execute("""
        SELECT poster_gender, COUNT(*) FROM posts GROUP BY poster_gender ORDER BY 2 DESC
    """).fetchall()
    for g, n in genders:
        print(f"  {g}: {n}")


def cmd_custom_sql(conn, sql):
    """Run custom SQL query."""
    result = conn.execute(sql)
    columns = [desc[0] for desc in result.description]
    rows = result.fetchall()

    # Simple table output
    print(f"\n{len(rows)} rows returned\n")
    if not rows:
        return

    # Print header
    print("  |  ".join(columns))
    print("-" * (sum(len(c) for c in columns) + len(columns) * 5))
    for row in rows[:50]:
        vals = []
        for v in row:
            s = str(v) if v is not None else "NULL"
            if len(s) > 60:
                s = s[:57] + "..."
            vals.append(s)
        print("  |  ".join(vals))
    if len(rows) > 50:
        print(f"  ... ({len(rows) - 50} more rows)")


def main():
    parser = argparse.ArgumentParser(description="Query sublet listings")
    parser.add_argument("--db", default=DB_PATH, help="DuckDB database path")
    parser.add_argument("--mode", choices=["best", "stats", "all"], default="best")
    parser.add_argument("--sql", help="Run custom SQL query")
    parser.add_argument("--max-rent", type=int, default=3000)
    parser.add_argument("--min-rent", type=int, default=None)
    parser.add_argument("--max-commute", type=int, default=30)
    parser.add_argument("--neighborhood", type=str, default=None)
    parser.add_argument("--move-by", type=str, default=None, help="Latest move-in date (YYYY-MM-DD)")
    parser.add_argument("--min-duration", type=int, default=None)
    parser.add_argument("--max-duration", type=int, default=None)
    parser.add_argument("--furnished", action="store_true")
    parser.add_argument("--laundry", action="store_true")
    parser.add_argument("--gender", action="store_true", help="Exclude gender-restricted listings")
    parser.add_argument("--dedup", action="store_true", default=True, help="Deduplicate cross-posts (default: on)")
    parser.add_argument("--no-dedup", dest="dedup", action="store_false", help="Show all posts including duplicates")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    conn = duckdb.connect(args.db, read_only=True)

    if args.sql:
        cmd_custom_sql(conn, args.sql)
    elif args.mode == "stats":
        cmd_stats(conn)
    else:
        cmd_best_matches(conn, args)

    conn.close()


if __name__ == "__main__":
    main()
