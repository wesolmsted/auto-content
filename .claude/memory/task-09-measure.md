---
name: task-09-measure
description: Slices 9–10 measure: MetaReader/TikTokReader endpoints and fields, what's unverified, match_posts rules, run_measure state transitions, manual-link resolution, auth/token files, FakePlatformReader, owner's Meta/TikTok app setup
type: project
---

Slices 9–10 built read-only platform readers, post matching, metric checkpoints and their CLI in `engine/measure/`.

**Why:** learning (Phase 2) and the hand-off page depend on how posts get matched and which metric fields exist; much of the API behaviour is still unverified. **How to apply:** call the functions below; check the "Unverified" list before trusting a metric; use `FakePlatformReader` in tests.

## Public API
- `matching.py` (pure): `match_posts(awaiting, candidates, settings: MatchSettings) -> list[Match]`, `caption_similarity(provided, posted)`. Post links are parsed with the hand-off page's `engine.handoff.links.parse_post_url`, shared by `ace posts link` and manual-link resolution.
- `meta.py`: `MetaReader(client, graph_url, ig_user_id, access_token, http: HttpSettings, *, sleep, clock)`.
- `tiktok.py`: `TikTokReader(client, api_url, access_token, http, *, sleep, clock)`.
- `api.py`: `request_json(...)` (shared retries via `engine.http`) and `PlatformReadError`, whose message never holds the URL or token.
- `auth.py`: `StoredToken(access_token, expires_at, refresh_token, refresh_expires_at)` with `.reauthorize_by`; `token_path(secrets_dir, platform)` (`meta_token.json` / `tiktok_token.json`); `save_token` (0600) / `load_token`; `expiry_warning(platform, token, now, warn_days)`; `exchange_meta_token`; `new_pkce_verifier`, `pkce_challenge`, `tiktok_authorize_url`, `code_from_redirect`, `exchange_tiktok_code`, `refresh_tiktok_token`; `AuthError`.
- `job.py`: `run_measure(session, readers, settings: Settings, now) -> MeasureSummary(matched, resolved, not_posted, metrics_inserted, errors)`; `link_post(session, render_id, platform, url) -> PostRow`, raising `LinkError`. Both flush; the caller commits.
- `cli.py`: `ace measure due`, `ace posts link`, `ace auth meta`, `ace auth tiktok [--redirect-uri]`; `make_readers(settings, client, now)`.

## Endpoints and fields actually used
- Meta: `GET {meta_graph_url}/{META_IG_USER_ID}/media?fields=id,permalink,timestamp,caption,media_type,media_product_type&limit=50&access_token=…`. It follows `paging.next` (a full URL, sent with no extra params) and stops at the first item older than `since`, since the list is newest first. Only `media_product_type == "REELS"` items are kept.
- Meta: `GET {graph}/{media-id}/insights?metric=views,reach,saved,shares,ig_reels_avg_watch_time,reels_skip_rate`. Each `data[]` entry is `{name, values:[{value}]}`. `saved` → `saves`; `ig_reels_avg_watch_time` ÷ 1000 → `avg_watch_s`; `reels_skip_rate` ÷ 100 → `skip_rate` (a fraction). Absent entries stay None. The whole body goes into `raw_json`.
- Meta auth: `GET {graph}/oauth/access_token?grant_type=fb_exchange_token&client_id&client_secret&fb_exchange_token` → `{access_token, expires_in?}`.
- TikTok: `POST {tiktok_api_url}/video/list/?fields=id,create_time,video_description,duration,share_url,view_count,like_count,comment_count,share_count`, body `{"max_count":20[, "cursor":…]}`, header `Authorization: Bearer`. Pages while `has_more`, stopping at the first video older than `since`. A non-`ok` `error.code` in a 200 body raises.
- TikTok metrics: `POST {api}/video/query/?fields=id,view_count,like_count,comment_count,share_count`, body `{"filters":{"video_ids":[id]}}`. Maps to views/likes/comments/shares; the rest stay None.
- TikTok auth: `https://www.tiktok.com/v2/auth/authorize/` (client_key, scope `user.info.basic,video.list`, response_type=code, redirect_uri, state, code_challenge, S256). `POST {api}/oauth/token/` form-encoded with `authorization_code` + `code_verifier`, or `refresh_token`. Access tokens last ~24 h and refresh tokens ~365 d. `measure due` refreshes when the access token is within 5 min of expiry and saves the new token.

## Unverified against the real APIs
- **Instagram has no media duration field** (checked against the IG Media reference), so IG candidates have `duration_s=None` and the duration filter is skipped for IG.
- Units: `ig_reels_avg_watch_time` is assumed to be ms, and `reels_skip_rate` a percentage.
- Whether trial reels appear in `/media` before graduation (DESIGN §14), and whether their `media_product_type` is `REELS`.
- What Graph returns when an insight metric is unsupported for a media. It may 400 the whole request instead of omitting the metric.
- TikTok `duration` is assumed to be whole seconds. Rounding vs the render's fractional duration can exceed the 0.5 s tolerance. Watch for false negatives; widen `match.duration_tolerance_s` if needed.
- TikTok desktop PKCE uses the **hex** SHA-256 challenge (from TikTok docs, not RFC 7636 base64url), and the localhost redirect.
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
3. **Resolve manual links:** IG matches by shortcode (`parse_post_url` on the video permalink) or id; TikTok by id. Sets the numeric `external_id`, `permalink` and `posted_at`. A link not found is recorded as an error and retried next run.
4. Exclude videos whose id is already on any post of that platform; `match_posts`; matched → `matched`, `match_method=auto`, `match_score`, `external_id`, `permalink`, `posted_at`.
5. Still awaiting and `now - downloaded_at >= window_hours` → `not_posted`.
6. Metrics: for each `matched` post with `posted_at`, the checkpoints from `measure.checkpoint_hours` (`MetricCheckpoint(f"{h}h")`) where `posted_at + h <= now` and no row exists. **One `fetch_metrics` per post, inserted for every due checkpoint**: a late first run writes 24h and 72h with the same snapshot (see `fetched_at`). `source=api`, `raw_json` = the snapshot raw. A fetch error is recorded per post and the job continues.
- `posts` has no `error`/`attempts` columns, so errors live only in the summary and in log lines (`job=measure`, `row_id`). `measure.max_attempts` is unused.
- Renders whose `duration_s` is NULL are skipped from matching.

## Manual links (`link_post`, `ace posts link`), same contract as the hand-off page
- Uses `engine.handoff.links.parse_post_url`: IG → `external_id=<shortcode>`, `permalink=https://www.instagram.com/reel/<code>/`; TikTok → numeric id. Short links (`vm.tiktok.com`) and schemeless URLs are rejected with a `PostUrlError` (a ValueError).
- Sets `matched`, `manual`, `match_score=None`, `posted_at=None`; also re-links an auto-matched post. It refuses if another post on that platform has the same `external_id` or `permalink`, and if the render has no post row for that platform ("download it from the hand-off page first").
- The next `measure due` resolves the IG media id and posting time. TikTok posts without a TikTok token keep `posted_at` NULL, so they get no API checkpoints.
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

**TikTok:**
1. At developers.tiktok.com create an app; add Login Kit (Desktop) + Display API; scopes `user.info.basic` and `video.list` only.
2. Register the redirect URI `http://localhost:8765/callback/` (or pass `--redirect-uri`).
3. Use Sandbox with your account added as a target user, unless the app is approved (DESIGN §14).
4. Set `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET`, then run `ace auth tiktok`: open the printed link and allow access. The browser lands on a dead localhost page; paste that full URL back.
5. Re-run before the refresh token's ~1-year expiry (warned 7 days ahead).

## Gotchas
- The no-publish guard allows TikTok's read POSTs; never add a `/media` POST or `media_publish`.
- Meta tokens travel in query strings (and in `paging.next`), so never log or chain httpx errors; use `PlatformReadError`. httpx INFO logging is already silenced in `engine/log.py`.
- CLI tests set `ACE_MEASURE__META_GRAPH_URL`/`TIKTOK_API_URL` to fake hosts and `ACE_HTTP__MAX_RETRIES=0`. They must `mkdir` the db dir before `upgrade_to_head`.
- Run: `uv run pytest tests/unit/test_measure_*.py tests/integration/test_measure_*.py`.
