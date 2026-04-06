#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import browser_cookie3
from bs4 import BeautifulSoup as bs
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Selenium experiment based on brutalsavage/facebook-post-scraper")
    parser.add_argument("--group-url", required=True, help="Facebook group URL")
    parser.add_argument("--since-days", type=int, default=30, help="Keep only posts newer than N days")
    parser.add_argument("--scrolls", type=int, default=12, help="Number of downward scrolls before parsing")
    parser.add_argument("--wait-seconds", type=float, default=2.5, help="Sleep between scrolls")
    parser.add_argument("--debugger-address", help="Attach to an existing Chrome instance, e.g. 127.0.0.1:9222")
    parser.add_argument("--output", help="Output JSON path")
    parser.add_argument("--html-dump", help="Optional HTML dump path for debugging")
    return parser.parse_args()


def default_output_path(group_slug: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    return Path("data/scrapes") / f"brutalsavage_{group_slug}_{stamp}.json"


def derive_group_slug(group_url: str) -> str:
    parsed = urlparse(group_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "groups":
        return parts[1]
    return "facebook_group"


def make_driver(debugger_address: str | None = None) -> webdriver.Chrome:
    options = Options()
    options.binary_location = CHROME_BINARY
    options.add_argument("--disable-notifications")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    if debugger_address:
        options.debugger_address = debugger_address
    return webdriver.Chrome(service=Service(), options=options)


def inject_facebook_cookies(driver: webdriver.Chrome) -> None:
    driver.get("https://www.facebook.com/")
    cookies = browser_cookie3.chrome(domain_name=".facebook.com")
    now_ts = time.time()
    for cookie in cookies:
        payload: dict[str, Any] = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
        }
        if cookie.expires and cookie.expires > now_ts:
            payload["expiry"] = int(cookie.expires)
        try:
            driver.add_cookie(payload)
        except Exception:
            continue


def scroll_group(driver: webdriver.Chrome, scrolls: int, wait_seconds: float) -> None:
    for _ in range(scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(wait_seconds)


def extract_legacy_posts(page_source: str) -> list[dict[str, Any]]:
    soup = bs(page_source, "html.parser")
    items = soup.find_all(class_="_5pcr userContentWrapper")
    posts: list[dict[str, Any]] = []

    for item in items:
        text_nodes = item.find_all(attrs={"data-testid": "post_message"})
        text = "\n".join(node.get_text(" ", strip=True) for node in text_nodes if node.get_text(strip=True))

        post_links = item.find_all(class_="_5pcq")
        post_url = ""
        for post_link in post_links:
            href = post_link.get("href")
            if href:
                post_url = f"https://www.facebook.com{href}"

        image_nodes = item.find_all(class_="scaledImageFitWidth img")
        image = ""
        for image_node in image_nodes:
            src = image_node.get("src")
            if src:
                image = src

        if text or post_url or image:
            posts.append({
                "text": text,
                "url": post_url,
                "image": image,
            })

    return posts


def extract_modern_candidates(page_source: str) -> list[dict[str, Any]]:
    soup = bs(page_source, "html.parser")
    posts: list[dict[str, Any]] = []
    seen: set[str] = set()

    for article in soup.select('[role="article"]'):
        text = article.get_text("\n", strip=True)
        if len(text) < 80:
            continue

        url = ""
        for anchor in article.find_all("a", href=True):
            href = anchor["href"]
            if "/posts/" in href or "/permalink/" in href:
                url = href if href.startswith("http") else f"https://www.facebook.com{href}"
                break

        key = f"{url}|{text[:160]}"
        if key in seen:
            continue
        seen.add(key)
        posts.append({
            "text": text,
            "url": url,
        })

    return posts


def filter_recent(posts: list[dict[str, Any]], since_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    filtered: list[dict[str, Any]] = []
    for post in posts:
        text = post.get("text", "")
        post["scraped_at"] = datetime.now(timezone.utc).isoformat()
        post["cutoff_utc"] = cutoff.isoformat()
        filtered.append(post)
    return filtered


def main() -> None:
    args = parse_args()
    group_slug = derive_group_slug(args.group_url)
    output_path = Path(args.output) if args.output else default_output_path(group_slug)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    driver = make_driver(args.debugger_address)
    try:
        if not args.debugger_address:
            inject_facebook_cookies(driver)
        driver.get(args.group_url)
        time.sleep(4)
        scroll_group(driver, args.scrolls, args.wait_seconds)
        page_source = driver.page_source
    finally:
        if args.debugger_address:
            driver.close()
        else:
            driver.quit()

    if args.html_dump:
        Path(args.html_dump).write_text(page_source)

    legacy_posts = extract_legacy_posts(page_source)
    modern_posts = extract_modern_candidates(page_source)
    chosen = legacy_posts if legacy_posts else modern_posts
    chosen = filter_recent(chosen, args.since_days)
    output_path.write_text(json.dumps(chosen, indent=2, ensure_ascii=False))

    summary = {
        "group_url": args.group_url,
        "legacy_posts_found": len(legacy_posts),
        "modern_candidates_found": len(modern_posts),
        "written_posts": len(chosen),
        "output": str(output_path),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
