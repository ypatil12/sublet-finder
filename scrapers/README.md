# Scrapers

Current experiment: scrape a single Facebook group with [`facebook-scraper`](https://github.com/kevinzg/facebook-scraper) and keep only the last 30 days of posts.

Install:

```bash
python3 -m pip install facebook-scraper
```

Run:

```bash
python3 scrapers/facebook_group_scraper.py \
  --group-url https://www.facebook.com/groups/ohananewyorkcity \
  --cookies from_browser \
  --since-days 30
```

Notes:

- `facebook-scraper` generally needs valid logged-in Facebook cookies for group scraping.
- If the vanity URL does not resolve through the library, rerun with `--group-id <numeric_group_id>`.
- Output is written to `data/scrapes/` by default as a flat JSON array of raw posts.
