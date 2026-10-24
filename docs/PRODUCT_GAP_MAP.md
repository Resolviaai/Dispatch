# Dispatch — Requirement-to-Implementation Gap Map
Date: 2026-10-24 · Baseline commit `de18a7a` · Authoritative requirements: the multi-user product brief (owner, 2026-10-24). This is the Phase-2 map the implementation sessions follow; every claim below was verified against the checked-out source, not documentation.

## Verified facts about the current architecture
- **No user identity exists anywhere.** Frontend has no auth screens/state (`frontend/src/screens/` = Camera, Clips, Processing, Recordings, Settings; zero sign-in/sign-up/session code; `frontend/package.json` has no Supabase/auth dependency). The local FastAPI serves everything at loopback/LAN with the device-token check only (`verify_dashboard_auth`, `dispatch/web/app.py:147`) — that authenticates a *device pairing*, not a *user*.
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
| G10 | Updater (explicitly out of scope §10) | PARKED | branch `feature/in-app-updater-wip` | — | 5 |

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
