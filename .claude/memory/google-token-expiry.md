---
name: google-token-expiry
description: Drive refresh token dies with invalid_grant about 7 days after `ace auth google` while the OAuth app is in Testing
type: reference
---

The Drive token from `ace auth google` (2026-11-09) failed on 2026-11-18 with `RefreshError: invalid_grant: Token has been expired or revoked`. Google expires refresh tokens after 7 days for an External OAuth app whose publishing status is **Testing**.

**Why:** every Drive job (`ace ingest`, `ace micro run`) fails until the owner re-consents, and on a schedule this would break silently each week.
**How to apply:** fix right away with `uv run ace auth google` (interactive, in a browser). Lasting fix: in Google Cloud Console → OAuth consent screen, set the publishing status to **In production**. An unverified personal app still works; consent shows an "unverified app" warning once, and tokens stop expiring weekly.
