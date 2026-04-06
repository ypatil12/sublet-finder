#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape a single Facebook group with facebook-scraper and keep recent posts only."
    )
    parser.add_argument(
        "--group-url",
        required=True,
        help="Facebook group URL, e.g. https://www.facebook.com/groups/ohananewyorkcity",
    )
    parser.add_argument(
        "--group-id",
        help="Optional explicit Facebook group id. Use this if the vanity URL does not work directly.",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="Only keep posts newer than N days. Default: 30",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=20,
        help="How many result pages to request from facebook-scraper. Default: 20",
    )
    parser.add_argument(
        "--cookies",
        required=True,
        help='Cookie source for facebook-scraper. Use a cookies file path or "from_browser".',
    )
    parser.add_argument(
        "--browser",
        default="chrome",
        choices=["chrome", "firefox", "edge", "opera", "brave", "vivaldi", "safari"],
        help='Reserved for future browser-specific cookie loading. Currently unused by upstream "facebook-scraper".',
    )
    parser.add_argument(
        "--output",
        help="Optional output JSON path. Defaults to data/scrapes/<group>_<timestamp>.json",
    )
    parser.add_argument(
        "--posts-per-page",
        type=int,
        default=100,
        help="facebook-scraper posts_per_page option. Default: 100",
    )
    return parser.parse_args()


def import_get_posts():
    try:
        from facebook_scraper import get_posts
    except ImportError as exc:
        raise SystemExit(
            "Could not import facebook-scraper dependencies. "
            "Install `facebook-scraper` and any missing extras, then retry. "
            f"Original error: {exc}"
        ) from exc
    return get_posts


def normalize_cookies_arg(raw: str, browser: str) -> str | CookieJar:
    if raw != "from_browser":
        return raw

    try:
        import browser_cookie3
    except ImportError as exc:
        raise SystemExit(
            'browser_cookie3 is required when --cookies is "from_browser". '
            "Run `pip3 install browser_cookie3` and retry."
        ) from exc

    loaders = {
        "chrome": browser_cookie3.chrome,
        "firefox": browser_cookie3.firefox,
        "edge": browser_cookie3.edge,
        "opera": browser_cookie3.opera,
        "brave": browser_cookie3.brave,
        "vivaldi": browser_cookie3.vivaldi,
        "safari": browser_cookie3.safari,
    }
    return loaders[browser](domain_name=".facebook.com")


def derive_group_identifier(group_url: str, explicit_group_id: str | None) -> str:
    if explicit_group_id:
        return explicit_group_id

    parsed = urlparse(group_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "groups":
        return parts[1]

    raise SystemExit(f"Could not derive a group identifier from URL: {group_url}")


def resolve_numeric_group_id(
    group_identifier: str,
    group_url: str,
    cookies: str | CookieJar,
) -> str:
    if group_identifier.isdigit():
        return group_identifier

    try:
        import requests
    except ImportError:
        return group_identifier

    response = requests.get(
        group_url,
        cookies=cookies,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()
    match = re.search(r'"groupID":"(\d+)"', response.text)
    if match:
        return match.group(1)
    return group_identifier


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def serialize_post(post: dict[str, Any], group_url: str) -> dict[str, Any]:
    copied = dict(post)
    if isinstance(copied.get("time"), datetime):
        copied["time"] = ensure_utc(copied["time"]).isoformat()
    if isinstance(copied.get("fetched_time"), datetime):
        copied["fetched_time"] = ensure_utc(copied["fetched_time"]).isoformat()
    copied["facebookUrl"] = group_url
    return copied


def default_output_path(group_identifier: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    return Path("data/scrapes") / f"{group_identifier}_{stamp}.json"


def main() -> None:
    args = parse_args()
    get_posts = import_get_posts()

    group_identifier = derive_group_identifier(args.group_url, args.group_id)
    cookies = normalize_cookies_arg(args.cookies, args.browser)
    group_identifier = resolve_numeric_group_id(group_identifier, args.group_url, cookies)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.since_days)
    output_path = Path(args.output) if args.output else default_output_path(group_identifier)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    kept_posts: list[dict[str, Any]] = []
    scanned = 0
    older_seen = 0

    post_iter = get_posts(
        group=group_identifier,
        pages=args.pages,
        cookies=cookies,
        options={"allow_extra_requests": False, "posts_per_page": args.posts_per_page},
    )

    for post in post_iter:
        scanned += 1
        post_time = ensure_utc(post.get("time"))

        if post_time is None:
            continue

        if post_time >= cutoff:
            kept_posts.append(serialize_post(post, args.group_url))
            continue

        older_seen += 1
        if older_seen >= 10:
            break

    output_path.write_text(json.dumps(kept_posts, indent=2, ensure_ascii=False))

    summary = {
        "group_identifier": group_identifier,
        "group_url": args.group_url,
        "cutoff_utc": cutoff.isoformat(),
        "scanned_posts": scanned,
        "kept_posts": len(kept_posts),
        "output": str(output_path),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
