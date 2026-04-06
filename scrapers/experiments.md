# Scraper Experiments

## 2026-04-06 `kevinzg/facebook-scraper`

- Target group: `https://www.facebook.com/groups/ohananewyorkcity`
- Attempted method: `facebook_scraper.get_posts(...)` with logged-in Chrome cookies
- Resolved numeric group id: `545476534447087`
- Result: no posts returned

Notes:

- Initial scrape with the vanity slug returned `0` scanned posts and `0` kept posts.
- A direct logged-in HTML fetch succeeded and exposed the numeric group id.
- Retrying with the numeric group id still returned an empty iterator.
- The returned HTML looked like a modern Comet app shell and did not contain server-rendered post links such as `/groups/<id>/posts/...`.
- Conclusion: this requests-based library appears incompatible with the current rendering path for this Facebook group in this environment.

## 2026-04-06 `brutalsavage/facebook-post-scraper`

- Target group: `https://www.facebook.com/groups/ohananewyorkcity`
- Attempted method: local Selenium port of upstream `scraper.py`
- Browser: Google Chrome via Selenium
- Authentication: Chrome Facebook cookies injected into the Selenium session
- Result: no posts returned

Notes:

- The upstream scraper relies on legacy Facebook selectors such as `_5pcr userContentWrapper` and `data-testid="post_message"`.
- I added a fallback parser for modern `[role="article"]` nodes in case the old selectors were dead.
- The Selenium run completed successfully and wrote:
  - JSON output: `data/scrapes/brutalsavage_ohananewyorkcity_2026-04-06_16-55-11.json`
  - HTML debug dump: `data/scrapes/brutalsavage_ohananewyorkcity_debug.html`
- The rendered HTML was not the group feed:
  - page title was just `Facebook`
  - `0` legacy post containers
  - `0` modern article nodes
  - the HTML contained a captcha/interstitial shell rather than post markup
- Conclusion: this Selenium-based approach is currently getting intercepted by Facebook before the group feed renders, so the upstream parser never sees post content.

Retry after manual login:

- After you logged in manually, rerunning the Selenium experiment produced `3` extracted items and the page title correctly rendered as `NYC Sublets & Short-Term Rentals | Facebook`.
- Output: `data/scrapes/brutalsavage_ohananewyorkcity_2026-04-06_16-57-44.json`
- Debug HTML: `data/scrapes/brutalsavage_ohananewyorkcity_debug_retry.html`
- However, the extracted items were comment/reply cards linked to post URLs with `comment_id=...`, not clean top-level feed posts.
- Conclusion: manual login removed the interstitial, but the current DOM slice accessible to this scraper still does not map cleanly to top-level group posts.

## 2026-04-06 `ridy-saas/Facebook-Public-Group-Scraper`

- Target group: `https://www.facebook.com/groups/ohananewyorkcity`
- Attempted method: local Node.js + Playwright run, no Docker
- Run command: `node src/index.js --url https://www.facebook.com/groups/ohananewyorkcity --no-proxy --headless=true --max-posts 15 --output-dir output/ohananewyorkcity_codex_run`
- Authentication: none
- Result: `7` posts returned

Notes:

- This scraper successfully extracted clean top-level group post URLs under `/groups/ohananewyorkcity/posts/...`.
- It completed in `56s` with `7` unique posts, all from the network extraction path.
- Output files:
  - JSON export: `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_codex_run/output.json`
  - Full posts archive: `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_codex_run/posts.json`
  - JSONL archive: `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_codex_run/posts.jsonl`
  - Stats: `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_codex_run/stats.json`
- Key stats from `stats.json`:
  - `uniquePosts: 7`
  - `sourceBreakdown.network: 7`
  - `sourceBreakdown.dom: 0`
  - `rejectedNoiseCandidates: 744`
  - `requestFailures: 0`
  - `retries: 2`
- Example extracted top-level posts:
  - `https://www.facebook.com/groups/ohananewyorkcity/posts/1286537743674292/`
  - `https://www.facebook.com/groups/ohananewyorkcity/posts/1286653253662741/`
  - `https://www.facebook.com/groups/ohananewyorkcity/posts/1287280263600040/`
  - `https://www.facebook.com/groups/ohananewyorkcity/posts/1286765933651473/`
- Limitation:
  - The scraper reports `744` rejected noise candidates, but this run does not persist a linkable list of those rejected candidates. The count appears to include comment-like nodes, shallow plugin/embed nodes, and objects that fail story-structure checks during structured parsing.
- Conclusion: this is the first scraper tried here that produced clean top-level post URLs for the target group without manual login, but the yield was still low (`7` posts) and the rejected-noise diagnostics are currently too coarse for post-level auditing.

Iteration notes:

- I patched the scraper to persist a bounded rejected sample file:
  - `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_rejected_samples_run/debug/rejected-samples.json`
- First diagnostic rerun:
  - Output dir: `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_rejected_samples_run`
  - Result: `4` accepted posts, `615` rejected noise candidates, `50` sampled rejects
- I then split rejected samples into explicit categories:
  - `comments`
  - `comment-ui`
  - `comment-context`
  - `non-post-plugins`
  - `story-structure-miss`
- Split-category rerun:
  - Output dir: `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_reject_split_run`
  - Sample breakdown:
    - `comments: 46`
    - `comment-ui: 4`
  - Dominant reject signals:
    - `__typename includes 'comment'`
    - `depth`
    - `comment_rendering_instance`
    - `comment_action_links`
- This showed that the reject bucket was mostly genuine comments or comment UI, not an obvious mass of missed top-level feed posts.

Specific rejected permalink investigation:

- Rejected sample:
  - `https://www.facebook.com/groups/ohananewyorkcity/permalink/1286576127003787/`
- Logged reason:
  - `reason: comment-ui`
  - `matchedSignal: comment_rendering_instance`
  - `typename: Feedback`
- Interpretation:
  - The scraper saw a valid group post permalink, but the node carrying it lived inside a comment-rendering/feedback structure and was rejected before canonical post extraction.

Parser refinement:

- I added an exception for `Feedback` nodes that carry a canonical group post URL so they are not discarded as comment UI.
- Salvage rerun:
  - Output dir: `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_feedback_salvage_run`
  - Result: `7` accepted posts
  - The previously rejected post `1286576127003787` was successfully recovered into accepted output.
- Higher-cap validation run:
  - Command: `node src/index.js --url https://www.facebook.com/groups/ohananewyorkcity --no-proxy --headless=true --max-posts 50 --runtime-minutes 3 --resume=false --output-dir output/ohananewyorkcity_feedback_salvage_50_run`
  - Output dir: `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_feedback_salvage_50_run`
  - Result: still plateaued at `7` accepted posts

Why this scraper is better than the previous scrapers:

- It is the only scraper tested here that reliably produced clean top-level `/groups/.../posts/...` URLs without manual login.
- It extracts from Facebook network/structured payloads instead of relying primarily on brittle legacy DOM selectors.
- It avoids the `comment_id=...` reply-card failure mode seen in the Selenium scraper.
- It now has auditable reject diagnostics, so parser decisions can be inspected and refined instead of treated as a black box.

Current bottom line:

- This scraper is better than the prior attempts because it can recover real top-level group posts and can be debugged structurally.
- However, after the parser fixes, it still plateaued at `7` posts for this group in logged-out public mode.
- The remaining ceiling appears to be Facebook’s accessible public surface for this session, not an obvious parser bug in the sampled reject set.
- Next likely improvement path: rerun the same scraper with authenticated cookies or a logged-in browser session.

Submodule code changes made locally:

- `src/core/run-scraper.js`
  - Added `rejectedSamples` tracking to extraction diagnostics.
  - Aggregated up to `50` rejected samples across parsing passes.
  - Persisted `debug/rejected-samples.json` and exposed `rejectedSampleCount` in run stats.
- `src/extract/structured-parser.js`
  - Added bounded rejected-sample capture with reason, matched signal, canonicalized URL, post/group ids, and preview text.
  - Split previously coarse noise rejects into explicit categories:
    - `comments`
    - `comment-ui`
    - `comment-context`
    - `non-post-plugins`
    - `story-structure-miss`
  - Replaced the old boolean comment-node heuristic with a structured detector that records why a node was rejected.
  - Replaced the old story-structure boolean check with signal collection so misses can be audited.
  - Added an exception for `Feedback` nodes that carry a canonical group post URL so valid post permalinks are not discarded as comment UI.
- `dom_experiment.js`
  - Added a standalone Playwright DOM-only experiment script for comparing rendered-article extraction against the network-first scraper path.

## Architecture Comparison

### `kevinzg/facebook-scraper`

- Core architecture:
  - Python requests/session scraper
  - Parses returned Facebook HTML and lightweight endpoints
  - Assumes the page response contains enough server-rendered or directly parseable post structure
- Strength:
  - Simple and lightweight when the target HTML still exposes post content directly
- Failure mode here:
  - The group page returned a modern Comet shell rather than parseable post markup
  - Even with cookies, the library did not successfully transition into the current structured data path for this group
- Why it failed here:
  - Its architecture is fundamentally older and more page-HTML-oriented than Facebook’s current rendering path for this surface

### `brutalsavage/facebook-post-scraper`

- Core architecture:
  - Selenium browser automation
  - DOM-first extraction using page selectors
  - Originally built around legacy Facebook DOM classes and attributes
- Strength:
  - Can sometimes work when a real browser session is required and the DOM still contains stable post containers
- Failure mode here:
  - Before login, Facebook served an interstitial/captcha shell instead of the feed
  - After manual login, the DOM parser mostly landed on comment/reply cards rather than top-level feed stories
- Why it failed here:
  - The browser could reach the surface, but the parser was coupled to unstable DOM structure
  - On modern Facebook, DOM slices for groups often expose comment/reply UI more readily than clean feed-post containers

### `ridy-saas/Facebook-Public-Group-Scraper`

- Core architecture:
  - Playwright browser automation
  - Network-first extraction from Facebook structured payloads
  - DOM fallback only after structured/network extraction stops producing results
  - Persistent checkpoints, payload logging, and parser diagnostics
- Strength:
  - It does not depend primarily on legacy visible DOM selectors
  - It can recover top-level posts from GraphQL / Comet response structures even when the rendered DOM is noisy
- Why it worked better here:
  - Facebook still exposed enough structured network data for this public group to recover canonical post ids and URLs
  - The parser could be instrumented and refined when it rejected valid post-bearing nodes
  - This let us recover a previously rejected `Feedback` node into a valid top-level post

## Why This Scraper Can Scrape When The Others Cannot

- The key difference is extraction layer.
- `kevinzg/facebook-scraper` tries to work from request/HTML assumptions that no longer match the current Facebook group rendering path.
- `brutalsavage/facebook-post-scraper` depends on the rendered DOM, which is both unstable and polluted by comment/reply UI on modern Facebook.
- `ridy-saas/Facebook-Public-Group-Scraper` reads the structured network payloads behind the page, which are closer to Facebook’s real data model than the visible DOM.
- That network-first design is why it could recover clean top-level `/groups/.../posts/...` URLs while the DOM-oriented scraper mostly surfaced reply artifacts.

Put more simply:

- The earlier scrapers were trying to infer posts from what Facebook rendered.
- The `ridy-saas` scraper was closer to extracting posts from what Facebook sent.
- That is why it was able to scrape real posts at all in this environment.

Remaining limitation:

- Even this scraper still depends on what Facebook exposes to a logged-out public session.
- So its architecture is better, but it is not omniscient; once the public network surface stops yielding new feed stories, recall still plateaus.

## 2026-04-06 Pure Playwright DOM-Only Experiment

- Target group: `https://www.facebook.com/groups/ohananewyorkcity`
- Attempted method: standalone Playwright scraper using only rendered DOM extraction
- Script: `scrapers/Facebook-Public-Group-Scraper/dom_experiment.js`
- Run command: `node dom_experiment.js --url https://www.facebook.com/groups/ohananewyorkcity --output-dir output/ohananewyorkcity_dom_only_run --max-scrolls 12 --settle-ms 2500 --headless true`
- Authentication: none
- Result: `10` deduped items returned

Notes:

- This experiment intentionally did not use the network/GraphQL extraction path.
- It scanned rendered `[role="article"]` nodes, looked for top-level `/groups/.../posts/...` or `/permalink/...` links, and extracted `innerText` from each article.
- Output files:
  - JSON export: `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_dom_only_run/output.json`
  - Stats: `scrapers/Facebook-Public-Group-Scraper/output/ohananewyorkcity_dom_only_run/stats.json`
  - Final HTML snapshot: `scrapers/Facebook-Public-Group-Scraper/output/ohananeworkcity_dom_only_run/debug/final-2026-04-06T19-19-30-879Z.html`
- Stats summary:
  - `totalExtractedBeforeDedupe: 106`
  - `uniquePosts: 10`
  - rendered article count plateaued at `27`
- What it got right:
  - It did recover real top-level post URLs from the rendered DOM.
  - It surfaced a few posts that did not appear in the earlier `7`-post public network-first run.
- Main drawbacks:
  - The extracted text blobs include comment/reply text, reaction text, and UI strings such as `All reactions`, `Like`, `Comment`, `Share`, and `View more comments`.
  - URLs still contain Facebook tracking query parameters (`__cft__`, `__tn__`) and need normalization.
  - One result was an older December 2025 post, so the DOM feed is not a clean "latest posts only" surface.
- Conclusion:
  - A pure Playwright DOM-first approach is viable and can recover more rendered feed items than the public network-first run here (`10` vs `7`).
  - But the raw extraction quality is worse because rendered article text mixes the post body with comment/UI chrome.
  - In this environment, the best practical path is likely a hybrid:
    - network-first extraction for clean canonical post records
    - DOM-first fallback for additional recall
    - optional authenticated session for deeper feed access
