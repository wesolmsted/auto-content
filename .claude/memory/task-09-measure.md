---
name: task-09-measure
description: Slices 9–10 measure: MetaReader endpoints and fields, what's unverified, match_posts rules, run_measure state transitions, manual-link resolution, auth/token files, FakePlatformReader, owner's Meta app setup
type: project
---

Slices 9–10 built the read-only Instagram reader, post matching, metric checkpoints and their CLI in `engine/measure/`.

**Why:** learning (Phase 2) and the hand-off page depend on how posts get matched and which metric fields exist; much of the API behaviour is still unverified. **How to apply:** call the functions below; check the "Unverified" list before trusting a metric; use `FakePlatformReader` in tests.

## Public API
- `matching.py` (pure): `match_posts(awaiting, candidates, settings: MatchSettings) -> list[Match]`, `caption_similarity(provided, posted)`. Post links are parsed with the hand-off page's `engine.handoff.links.parse_post_url`, shared by `ace posts link` and manual-link resolution.
- `meta.py`: `MetaReader(client, graph_url, ig_user_id, access_token, http: HttpSettings, *, sleep, clock)`.
- `api.py`: `request_json(...)` (shared retries via `engine.http`) and `PlatformReadError`, whose message never holds the URL or token.
- `auth.py`: `StoredToken(access_token, expires_at)`; `token_path(secrets_dir, platform)` (`meta_token.json`); `save_token` (0600) / `load_token`; `expiry_warning(platform, token, now, warn_days)`; `exchange_meta_token`.
- `job.py`: `run_measure(session, readers, settings: Settings, now) -> MeasureSummary(matched, resolved, not_posted, metrics_inserted, errors)`; `link_post(session, render_id, platform, url) -> PostRow`, raising `LinkError`. Both flush; the caller commits.
- `cli.py`: `ace measure due`, `ace posts link`, `ace auth meta`; `make_readers(settings, client, now)`.

## Endpoints and fields actually used
- Meta: `GET {meta_graph_url}/{META_IG_USER_ID}/media?fields=id,permalink,timestamp,caption,media_type,media_product_type&limit=50&access_token=…`. It follows `paging.next` (a full URL, sent with no extra params) and stops at the first item older than `since`, since the list is newest first. Only `media_product_type == "REELS"` items are kept.
- Meta: `GET {graph}/{media-id}/insights?metric=views,reach,saved,shares,ig_reels_avg_watch_time,reels_skip_rate`. Each `data[]` entry is `{name, values:[{value}]}`. `saved` → `saves`; `ig_reels_avg_watch_time` ÷ 1000 → `avg_watch_s`; `reels_skip_rate` ÷ 100 → `skip_rate` (a fraction). Absent entries stay None. The whole body goes into `raw_json`.
- Meta auth: `GET {graph}/oauth/access_token?grant_type=fb_exchange_token&client_id&client_secret&fb_exchange_token` → `{access_token, expires_in?}`.

## Unverified against the real APIs
- **Instagram has no media duration field** (checked against the IG Media reference), so IG candidates have `duration_s=None` and the duration filter is skipped for IG.
- Units: `ig_reels_avg_watch_time` is assumed to be ms, and `reels_skip_rate` a percentage.
- Whether trial reels appear in `/media` before graduation (DESIGN §14), and whether their `media_product_type` is `REELS`.
- What Graph returns when an insight metric is unsupported for a media. It may 400 the whole request instead of omitting the metric.
- Meta `expires_in` may be missing; then there's no expiry warning.
- Graph API version: `config/default.toml` has `v23.0` (May 2025). The changelog on 2026-11-08 listed v26.0 (2026-07-29) and named v27.0 as latest. Bump `measure.meta_graph_url`.

## Matching rules (DESIGN §7.6, `config [match]`)
- Hard filters: same platform; `posted_at >= downloaded_at - fetch_slack_hours` (1 h); `|duration - render.duration_s| <= duration_tolerance_s` (0.5), skipped when the candidate duration is None.
- Score = `caption_weight` (0.7) × caption similarity + `time_weight` (0.3) × time proximity.
  - Caption similarity: the share of the provided caption's lowercase `\w+` tokens found in the posted caption, so the owner's added hashtags don't hurt. An empty provided caption scores 0.
  - Time proximity: `max(0, 1 - delay_h / window_hours)`, where delay is clamped at 0 for posts inside the slack before download.
- Greedy best-first over all pairs (ties broken by post id, then external id); each post and each video used once; pairs below `min_score` (0.3) are dropped.
- **With the defaults, a caption with zero token overlap can only match when posted at download time (score exactly 0.3).** Effectively the caption must overlap.
- Properties (Hypothesis) in `tests/unit/test_measure_matching.py` were mutation-checked.

## Job state transitions (`run_measure`, per reader)
1. Load `awaiting_post` posts and `matched` posts with `posted_at` NULL (manual links), each with its render. `downloaded_at` is `renders.downloaded_at`, falling back to `posts.created_at`.
2. One `recent_videos(oldest download - slack)` call. If it raises, the error is recorded and steps 3–5 are skipped (posts don't go `not_posted` while the API is down).
3. **Resolve manual links:** IG matches by shortcode (`parse_post_url` on the video permalink) or id. Sets the numeric `external_id`, `permalink` and `posted_at`. A link not found is recorded as an error and retried next run.
4. Exclude videos whose id is already on any post of that platform; `match_posts`; matched → `matched`, `match_method=auto`, `match_score`, `external_id`, `permalink`, `posted_at`.
5. Still awaiting and `now - downloaded_at >= window_hours` → `not_posted`.
6. Metrics: for each `matched` post with `posted_at`, the checkpoints from `measure.checkpoint_hours` (`MetricCheckpoint(f"{h}h")`) where `posted_at + h <= now` and no row exists. **One `fetch_metrics` per post, inserted for every due checkpoint**: a late first run writes 24h and 72h with the same snapshot (see `fetched_at`). `source=api`, `raw_json` = the snapshot raw. A fetch error is recorded per post and the job continues.
- `posts` has no `error`/`attempts` columns, so errors live only in the summary and in log lines (`job=measure`, `row_id`). `measure.max_attempts` is unused.
- Renders whose `duration_s` is NULL are skipped from matching.

## Manual links (`link_post`, `ace posts link`), same contract as the hand-off page
- Uses `engine.handoff.links.parse_post_url`: IG → `external_id=<shortcode>`, `permalink=https://www.instagram.com/reel/<code>/`. Non-reel and schemeless URLs are rejected with a `PostUrlError` (a ValueError).
- Sets `matched`, `manual`, `match_score=None`, `posted_at=None`; also re-links an auto-matched post. It refuses if another post on that platform has the same `external_id` or `permalink`, and if the render has no post row for that platform ("download it from the hand-off page first").
- The next `measure due` resolves the IG media id and posting time.
- `engine.measure.job` imports `engine.handoff.links` (deliberate, to share one parser).

## Fake (`tests/fakes/measure.py`)
`FakePlatformReader(platform, videos=(), metrics=None)`:
- `.videos` (list) is filtered by `posted_at >= since`; `.metrics[external_id]` is the snapshot to return.
- `.fail_listing = True` makes listing raise; `.fail_metrics = {ids}` makes those fetches raise `PlatformReadError`, as does an unknown id.
- Calls are recorded in `.listed_since` and `.fetched`.
- Test helpers: `add_post(...)` and `add_manual_post(...)` in `tests/integration/test_measure_job.py`.

## Owner setup (read scopes only, no secrets here)
**Meta (Instagram professional account linked to a Facebook Page):**
1. At developers.facebook.com create an app (type Business). Add the "Instagram" product with **Facebook Login** (the code uses `graph.facebook.com`).
2. App roles: add yourself, so the app works in development mode without App Review.
3. In `.env` set `META_APP_ID`, `META_APP_SECRET`, and `META_IG_USER_ID` (Graph Explorer: `GET me/accounts?fields=instagram_business_account`).
4. Graph API Explorer: pick the app and generate a user token with only `instagram_basic, instagram_manage_insights, pages_show_list, pages_read_engagement`.
5. Run `ace auth meta`, paste the token, and it's exchanged for a ~60-day token.
6. Re-run `ace auth meta` when `measure due` warns (7 days ahead). Long-lived user tokens can't be extended by API.

## Gotchas
- Never add a `/media` POST or `media_publish`; the no-publish guard test fails on them.
- Meta tokens travel in query strings (and in `paging.next`), so never log or chain httpx errors; use `PlatformReadError`. httpx INFO logging is already silenced in `engine/log.py`.
- CLI tests set `ACE_MEASURE__META_GRAPH_URL` to a fake host and `ACE_HTTP__MAX_RETRIES=0`. They must `mkdir` the db dir before `upgrade_to_head`.
- Run: `uv run pytest tests/unit/test_measure_*.py tests/integration/test_measure_*.py`.
