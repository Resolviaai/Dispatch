# Dispatch — Requirement-to-Implementation Gap Map (v2, normalized)
Date: 2026-10-24 · Baseline `75137b3` · This is the SINGLE authoritative plan (v1 + amendment merged; earlier versions superseded). External review findings incorporated and source-verified.

## Verified current state
- **No user identity in the main app.** No auth screens/dependency; SQLite and Room have no `user_id`; workers are tenant-blind. (Supabase schema + Worker DO have identity — disconnected from the main app.)
- **PC credential transfer REMOVED** (410 stubs, commit `de18a7a`); PC-side Google sign-in (`start-auth`, `scripts/auth_youtube.py`) still exists and powers the current pipeline — retire LAST.
- **Source-visibility policy: DONE + tested** (private/unverifiable rejected pre-download; defer-without-download; cookies-bypass test).
- **Updater:** core policy/parsing + 10 JVM tests on `main`; integration snapshot is a LOCAL-ONLY branch `feature/in-app-updater-wip` (remote has only `main` + `gh-pages`); integration phases out of scope this task.
- **Admin: entirely missing.** Cloud RLS schema exists, disconnected.

## Gap table (authoritative)
| # | Requirement | Status | Defect / gap | Key files |
|---|---|---|---|---|
| G1 | Multi-user identity + isolation (§2.1, §4) | MISSING | No auth provider in app; no ownership columns; tenant-blind workers | frontend, db.py, Room, workers |
| G2 | Email/password auth (§2.2) | MISSING | Never implemented; to be built on Supabase Auth | frontend, Android |
| G3 | On-device YouTube OAuth lifecycle (§2.3, §6) | MISSING | Needs **Web-server OAuth client** (server-side code exchange for offline access; refresh token stays server-side encrypted per user — Google web-server flow), Android Custom-Tab flow, backend token vault, refresh/revoke/status UI | Android, backend module |
| G4 | PC = worker, no login (§2.4) | PARTIAL | Credential endpoints removed; PC sign-in/start-auth retire after G3 replaces their function | web/app.py, scripts/auth_youtube.py |
| G5 | Admin panel + roles (§5) | MISSING | No role model; bootstrap = `DISPATCH_ADMIN_EMAILS` allowlist (owner-confirmed: resolviaai@gmail.com) | new |
| G6 | Visibility policy (§2.5) | DONE+TESTED | — | catcher, tests |
| G7 | APK install guidance (§7.1) | DONE (device-unverified) | Signature-transition guidance on landing page; true phone cause unverified | site |
| G8 | Connection/staleness truthfulness (§7.2–7.3) | **CONFIRMED DEFECTS** | (1) SettingsScreen saves host then reports success even when ping fails; (2) App.tsx hard-coded `192.168.0.200` fallback can override saved host via auto-reconnect; (3) recordings list updates only on non-empty native results — real empty transitions hidden; (4) RecordingsScreen swallows poll failures (clips 4s / inbox 5s) — no stale marker | SettingsScreen.tsx ~L175–198, App.tsx, RecordingsScreen.tsx |
| G9 | Readability/touch (§7.4) | **CONFIRMED (source-level)** | Sessions: `text-[9px]`×1, `text-[10px]`×22, `text-[11px]`×11, `min-h-[38px]`×3; no visual device audit | Sessions/Settings |
| G10 | Updater integration (out of scope §10) | PARKED | Local-only WIP branch; core on `main` | — |
| G11 | Legacy OAuth/pairing remnants (§6.2) | PARTIAL | To remove AFTER G3: native bridge `connectYouTube(pcHost, pairingCode)`, 4-digit pairing UI, PC pairing copy, `start-auth`, `scripts/auth_youtube.py` | listed files |

## Implementation order (single, authoritative — supersedes all earlier sequences)
1. **P0 Architecture decision → APPROVED (2026-10-24):** ADR-001 ratified — per-worker bound tokens, Android-only review UI, server-side token vault. Implementation may start.
2. **P0 Auth:** Supabase email/password in app UI; server-side JWT verification on every local API; session expiry/logout/registration.
3. **P0 Tenant isolation:** ownership strategy per ADR (legacy local data = PC-worker-owned; cloud = per-user), worker identity per ADR, adversarial cross-user + worker-impersonation tests.
4. **P0 Admin:** role from `DISPATCH_ADMIN_EMAILS` bootstrap, backend-enforced admin APIs, minimal protected admin UI, audit log, no-secret-disclosure tests.
5. **P0 On-device OAuth:** owner creates **Web-server OAuth client** (+ Android client for the Custom-Tab step); PKCE/offline-access code → backend exchange → encrypted per-user token vault; refresh/reconnect/revoke + truthful status UI.
6. **P1 Retire legacy OAuth:** remove G11 remnants only after per-user tokens are operational.
7. **P1 UI fixes:** G8 (stale-response guards, truthful save/ping split, no host override) + G9 (44–48px targets, readable badges) with regression tests.
8. **Docs:** README consistency (manual-token instructions, private/unlisted contradiction — upload worker uses `unlisted`).

## Owner prerequisites
- Google Cloud: **Web-server OAuth client** (server-side exchange, confidential secret in PC backend env only) + Android client for the browser step; consent screen with YouTube scopes.
- Supabase: email/password auth enabled (project exists; anon key in `.env`).
- Admin allowlist already set in `.env`: `DISPATCH_ADMIN_EMAILS=resolviaai@gmail.com`.
