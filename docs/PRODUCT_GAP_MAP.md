# Dispatch — Requirement-to-Implementation Gap Map
Date: 2026-10-24 · Baseline commit `de18a7a` · Authoritative requirements: the multi-user product brief (owner, 2026-10-24). This is the Phase-2 map the implementation sessions follow; every claim below was verified against the checked-out source, not documentation.

## Verified facts about the current architecture
- **No user identity exists in the MAIN application.** (The Cloudflare Worker and Supabase schema do validate/own user identity — the gap is that the main app never connects to them.) Frontend has no auth screens/state (`frontend/src/screens/` = Camera, Clips, Processing, Recordings, Settings; zero sign-in/sign-up/session code; `frontend/package.json` has no Supabase/auth dependency). The local FastAPI serves everything at loopback/LAN with the device-token check only (`verify_dashboard_auth`, `dispatch/web/app.py:147`) — that authenticates a *device pairing*, not a *user*.
- **Single-tenant by construction:** SQLite (`dispatch/db.py`) has no `user_id` on sessions/chunks/transcripts/clips/publishing_outbox; the Android Room DB likewise. Processing workers (`dispatch/transcription/worker.py`, `dispatch/ai_clips/worker.py`, `dispatch/publisher/outbox.py`) consume global queues with no tenant context.
- **PC-side YouTube OAuth exists** (`scripts/auth_youtube.py`, `dispatch/youtube_inbox/oauth.py`, token at `youtube_token.json`) and its credential endpoints were removed 2026-10-24 (410, commit `de18a7a`). PC-side Google sign-in (brief §2.4 obsolete) still exists at `/api/integrations/youtube/start-auth` — must be retired when on-device OAuth lands.
- **Source-visibility policy: DONE and tested** (private/unverifiable rejected pre-download, defer-without-download on API errors; `tests/test_ingest_privacy_gate.py`, `tests/test_youtube_processing_tracker.py`).
- **Cloud multi-user foundation exists but is disconnected:** `supabase/migrations/20261023000001_init.sql` has profiles/RLS (verified live); the PC and Android apps never talk to Supabase.
- **Admin functionality: entirely missing** (no role model, no admin routes, no UI).
- **In-app updater: core policy/parsing on branch `feature/in-app-updater-wip` + `main` `DispatchUpdateManager.kt`; integration phases intentionally out of scope this task.**

## Gap classification (brief §3 categories 1–6)

| # | Requirement (§) | Status | Gap / root cause | Files | Category |
|---|---|---|---|---|---|
| G1 | Multi-user identity + isolation (2.1, 4.x) | MISSING | No auth provider wired; DB has no ownership columns; workers are tenant-blind | `frontend/*`, `dispatch/db.py`, workers, Room schema | 4 Missing |
| G2 | Email/password auth preserved (2.2) | MISSING | Nothing to preserve — never implemented | — | 4 |
| G3 | On-device YouTube OAuth lifecycle (2.3, 6.x) | MISSING | Requires: Google OAuth client (PKCE public client), Android Chrome-Custom-Tabs flow, backend token exchange + encrypted per-user storage, refresh/revoke, status UI. PC sign-in/start-auth must be retired afterward | Android, `dispatch/web/app.py`, new backend module | 4 (needs owner GCP OAuth client) |
| G4 | PC = worker only, no login (2.4) | PARTIAL | Credential endpoints removed; PC-side Google sign-in + start-auth remain until G3 | `dispatch/web/app.py` | 3 |
| G5 | Admin panel + roles (5.x) | MISSING | No role/permission model anywhere; needs backend-enforced role + bootstrap via env allowlist | new | 4 |
| G6 | Visibility policy (2.5) | DONE | — | catcher + tests | 1 |
| G7 | APK install guidance (7.1) | DONE (documented) | Signature-mismatch transition documented on landing page; true device cause still unverified | `site/index.html` | 1 + 6 |
| G8 | Truthful connection/states (7.2, 7.3) | PARTIAL | Settings save/ping separation and Sessions staleness not yet audited in this pass | `SettingsScreen.tsx`, `RecordingsScreen.tsx`, `App.tsx` | 3 |
| G9 | Readability/touch (7.4) | PARTIAL | 9–11px badges / 38px controls reported, unmeasured this pass | Sessions/Settings | 3 |
| G10 | Updater (explicitly out of scope §10) | PARKED | Core + tests are on `main`; the WIP integration snapshot lives on a LOCAL-ONLY branch `feature/in-app-updater-wip` (never pushed — remote has only `main`+`gh-pages`) | — | 5 |

## Implementation order (dependency-driven, P0 first)
1. **A-auth foundation:** add Supabase Auth to the Android/React layer (email/password), backend identity boundary (`X-User-JWT` verified against Supabase on every local API), DB migration adding `user_id` to local entities + workers keyed by user. This is the gate for everything else.
2. **A-isolation:** per-user data partitioning end-to-end + cross-user adversarial tests.
3. **B-admin:** role claim from Supabase (`app_metadata.role`), env-allowlist bootstrap, backend-enforced admin APIs + minimal React admin area + audit log.
4. **C-OAuth:** GCP OAuth client (owner action: create Android + Web client IDs), PKCE Android flow, backend token vault (Android Keystore on device / encrypted-at-rest on PC worker), refresh/reconnect/revoke + status UI, retire PC sign-in (`start-auth`) last.
5. **D-reliability:** G8/G9 targeted fixes with tests.

## Environment prerequisites (owner actions)
- Google Cloud project with OAuth consent screen + **Android** OAuth client ID (public/PKCE — no secret in app) and authorized redirect scheme.
- Supabase project (exists: `aldzlbdxplqkcnyjhzmm`) with email/password auth enabled; anon key already in `.env`.
- Admin bootstrap: `DISPATCH_ADMIN_EMAILS` env allowlist (no hardcoded password, no auto-first-user-admin).

**No implementation of A/B/C may be honestly claimed before this map's gaps are closed; this document exists so no session mistakes partial work for done.**


## Amendment (2026-10-24, after independent external review — confirmed against source)

**A. P0 prerequisite — canonical architecture decision (new, gates everything):**
Define before any implementation: where the authenticated product UI runs (hosted web + Android), where metadata/credentials/clip media live (Supabase/R2 per existing migrations), whether the PC is one owner-worker bound to its own authorized jobs (service identity, NOT shared customer tokens), and the authority rule when local and cloud state disagree (cloud owns review state; local owns processing truth — per existing plan D2/D3). Without this, adding `user_id` columns produces an inconsistent hybrid.

**B. Worker-token tenant boundary (P0 security):** `cloud-worker/src/index.ts` `authenticate()` lets any holder of the shared `DISPATCH_WORKER_TOKEN` impersonate ANY user via `X-Dispatch-User-Id`. Restrict the shared token to backend maintenance only, or replace with a per-worker identity bound to one user. Add adversarial tests: worker A cannot act on user B's media.

**C. OAuth flow corrections (P0):** Google's supported pattern for offline YouTube access is: Android requests offline access via a SERVER web client ID, sends the one-time code to our backend, which exchanges it (server-held confidential secret) and stores the refresh token server-side encrypted per user — NOT on the device. Map updated accordingly; requires owner to create the Web server OAuth client. The PC worker keeps using its own service authorization for its own processing until per-user flows replace it — deleting `start-auth` before per-user tokens exist would break the current pipeline (do it LAST).

**D. Confirmed UI defects (upgrade G8 to confirmed-defect):**
1. `SettingsScreen.tsx` (~L90): checks `connectYouTubeOnDevice` exists but NEVER CALLS it — even with the native method present, OAuth would not start. Also shows a false "Opening Google sign-in…" state.
2. `App.tsx` hard-coded `192.168.0.200:8000` fallback can override the user's explicitly saved host via auto-reconnect.
3. `App.tsx` updates the recordings list from the native bridge only on non-empty results — a real transition to empty is hidden.
4. `RecordingsScreen.tsx` swallows poll failures (clips 4s / inbox 5s) with no stale-data marker.
5. G9 confirmed at source level: `text-[9px]` ×1, `text-[10px]` ×22, `text-[11px]` ×11, `min-h-[38px]` ×3 on Sessions.
6. Legacy UI remnants to remove with the OAuth replacement: native bridge `connectYouTube(pcHost, pairingCode)`, the 4-digit pairing UI, the PC pairing-code copy, the desktop/web `start-auth` path, and `scripts/auth_youtube.py` retirement (after per-user tokens exist).

**E. Legacy data ownership migration (P0 design input):** existing SQLite/Room records have no owner. Policy: the local PC database stays a single-owner worker store bound to the PC's worker identity (no per-user rows there); cloud tables own multi-tenant metadata. Existing phone recordings/outbox are owner-data — never reassigned by "first login"; owner claims them via the account link step, explicitly.

**F. Docs:** README still instructs manual refresh-token entry + PC sign-in and contradicts itself on session visibility (upload worker sets `unlisted`). Consistency pass after the architecture settles.

**Corrected implementation order = ChatGPT §5 (architecture decision → auth → tenant isolation incl. worker identity → admin → on-device OAuth w/ server-side token vault → retire PC OAuth → UI fixes → docs), adopted as the authoritative sequence.**
