# Dispatch — Focused Admin Operations Dashboard & Multi-Device Roadmap

**Status:** IN PROGRESS (Account connections & Cloud Mode fixes currently executing; Admin Console prepped)  
**Author:** Antigravity  
**Date:** 2026-10-25  
**File Target:** `docs/ADMIN_OPERATIONS_DASHBOARD.md`

---

## 1. Context & Objectives

The owner requested an authoritative, safe, DBMS-style operations dashboard for Dispatch that answers five core operational questions without exposing secrets or disrupting the live repository:

1. **Which users are active or disabled?** (Reversible ban/unban with double-confirmation modal; preserves user data)
2. **How much storage is actually used, reserved, and allocated?**
   - **Actual R2 usage:** Verified stored bytes in active R2 objects.
   - **Pending reservations:** Ephemeral byte reservations in `upload_reservations`.
   - **Assigned user quotas:** Sum of per-user `storage_quota_bytes`.
   - **Unallocated quota:** `10 GB - sum(assigned_quotas)` (must *never* be labeled as "free storage").
3. **Which processing jobs are queued, running, or failing?** (Real counts from `processing_jobs` table).
4. **What quota and processing priority does each user have?**
   - Quota presets (`1 GB`, `2 GB`, `5 GB`) + decimal custom input, strictly bounded by the 10,000,000,000-byte global budget.
   - Priority reordering (1–1000, lower number = higher priority).
5. **Which publishing accounts are connected, without exposing credentials?**
   - Safe metadata only: provider, account title/handle, avatar, connected date. Zero access tokens, refresh tokens, or Gemini keys surfaced or logged.

---

## 2. What Has Been Done So Far

### A. Core Architecture & API Fixes
- [x] **Safe Profile Parsing in Python Admin API:**
  - Resolved `AttributeError: 'str' object has no attribute 'get'` in `dispatch/web/app.py` line 2424 (`admin_list_users`). Verified with 6 unit tests passing in `tests/test_admin.py`.
- [x] **Publisher Test Fix:**
  - Updated `tests/test_publisher.py` to use `custom_platforms` instead of `target_platforms`. Verified 2/2 tests passing.
- [x] **Production Multi-Platform OAuth Integration:**
  - Configured and safely stored client IDs/secrets for Google/YouTube, Instagram, Facebook, LinkedIn, and Twitter/X into Cloudflare Worker secrets via `wrangler secret put`.
  - Built PKCE SHA-256 and AES-256-GCM token encryption in `cloud-worker/src/oauth.ts`.
  - Deployed Cloudflare Worker `dispatch-cloud.resolviaai.workers.dev` (version `b645cb8e-5115-4548-a011-0db0003087a9`).
- [x] **Android Release v1.1.16 & Landing Page:**
  - Built signed release APK (`release-out/Dispatch.apk`, versionCode 20, v1.1.16-alpha).
  - Pushed to `main` branch and `gh-pages` branch. Verified live at `https://resolviaai.github.io/Dispatch/`.

---

## 3. Immediate Interleaved Fixes (Currently Being Executed)

Before resuming the Admin UI directly, the user identified two blocking issues in the live mobile app:

### Issue 1: Connecting Account Buttons Broken
- **Symptom:** YouTube gave "Failed to fetch"; X & Instagram displayed "OAuth is not configured on the server yet...".
- **Root Cause Discovered:**
  1. The Cloudflare Worker had **no global CORS OPTIONS preflight handler** for API endpoints outside `/admin`. When the WebView sent preflight `OPTIONS` with `Authorization` and `Content-Type`, the worker rejected it with `401 Unauthorized` without CORS headers. The browser threw `TypeError: Failed to fetch`.
  2. In `frontend/src/screens/SettingsScreen.tsx`, when `initProviderOAuth` failed with an error, the code discarded `res.error` and displayed a misleading fallback string: `"${label} OAuth is not configured on the server yet..."`.
  3. Facebook was omitted from the UI provider list.
- **Fix:**
  - Add global CORS preflight handler in `cloud-worker/src/index.ts` returning 204 with full headers (`Origin`, `Authorization`, `Content-Type`, `X-Dispatch-User-Id`).
  - Pass `Access-Control-Allow-Origin: *` across all JSON responses.
  - Update `SettingsScreen.tsx` to surface real error feedback and add Facebook connection.

### Issue 2: Hardcoded LAN IP `192.168.0.200:8000` & Wi-Fi Dependency
- **Symptom:** When the phone is on mobile data (cellular) or external Wi-Fi, the Clips tab blocks with:
  *"Cannot connect to PC Studio. Attempting to reach 192.168.0.200:8000. Ensure the server is running on the same device."*
- **Root Cause Discovered:**
  - `DispatchNativeBridge.kt`, `frontend/src/bridge.ts`, and `frontend/src/App.tsx` hardcoded `'192.168.0.200:8000'` as the default host when on Android.
  - In `frontend/src/screens/ClipsScreen.tsx`, when `fetch('/api/clips')` fails, the screen was completely replaced with a blocking error screen instead of gracefully operating in **Cloud Mode**.
- **Fix:**
  - Remove forced fallback to `192.168.0.200:8000`. If `pcHost` is not explicitly set, leave it empty.
  - Enable **Cloud Mode** in `ClipsScreen.tsx`: when PC is offline/unreachable, seamlessly fetch processing jobs and cloud clips from Cloudflare Worker (`WORKER_BASE/jobs/status` and `WORKER_BASE/clips`).
  - Add `/clips` endpoint to Cloudflare Worker to serve cloud clips directly from Supabase `clips_cloud` with presigned R2 URLs.
  - Add a graceful, non-blocking header pill indicating `Cloud Mode (Remote)` or `Studio Online (LAN)`.

---

## 4. What Needs to be Done for Admin Operations Dashboard

Once the account connections and Cloud Mode fixes are verified and committed:

### A. Overarching Design & System Standards
- Follow `design.md` v3 invariants:
  - 4-layer elevation architecture: `#161616` (Layer 0), `#1C1C1C` (Layer 1), `#232323` (Layer 2), `#2563EB` (Layer 3).
  - Single hero CTA per screen.
  - 44px minimum hitboxes on all interactive elements.
  - Zero decorative emojis (clean Lucide/SVG vector icons only).
  - Strict numeric place-value alignment: `font-mono tabular-nums`, right-aligned numbers and byte metrics.
  - Decimal GB standard: `1 GB = 1,000,000,000 bytes`.

### B. Overview Metrics Row (Authoritative Data Only)
- [ ] **Total Users**: Count of auth profiles; split by Active vs Disabled.
- [ ] **Actual Stored Storage (R2)**: Sum of verified bytes in `media_objects` (`status = 'active'`).
- [ ] **Pending Reservations**: Sum of expected bytes in `upload_reservations`.
- [ ] **Allocated Quota**: Sum of user quotas vs Global Budget (`10,000,000,000 bytes`).
- [ ] **Unallocated Quota**: Distinctly labeled as "Unallocated Quota Pool" — **never** labeled as "free storage".
- [ ] **Job Queue Counters**: Breakdown of `queued`, `claimed`, `completed`, `failed` from `processing_jobs`.

### C. Searchable & Paginated Users Table
- [ ] **Email & Identity**: Masked/clean display, joined date, last seen timestamp (`tabular-nums`).
- [ ] **Account Status**: Pill badge (`Active` green, `Disabled` amber/danger). Reversible toggle with double-confirmation dialog ("Disable this user? All existing data and R2 storage will remain preserved.").
- [ ] **Connected Providers**: Badges indicating connected services (YouTube, Instagram, Facebook, LinkedIn, X). Strictly no tokens or secrets.
- [ ] **Processing Priority**: Explicit integer priority (1–1000). Inline `▲ -10` and `▼ +10` nudge buttons and direct numeric input. Lower number = processed first.
- [ ] **Storage Quota Control**:
  - Current usage bar vs allocated quota.
  - Preset buttons: `1 GB`, `2 GB`, `5 GB`, and custom input.
  - Server validation: must reject if `new_quota < current_stored_bytes` or if `sum(all_quotas) > 10 GB`.

### D. Server Endpoints & Audit Logging
- [ ] Ensure all admin mutations (`/admin/users/:id/quota`, `/admin/users/:id/priority`, `/admin/users/:id/ban`, `/admin/users/:id/unban`) record structured audit entries into Supabase `audit_logs` or log with:
  - `actor_email`
  - `target_user_id`
  - `action`
  - `before_value` / `after_value`
  - `timestamp`
  - Zero secrets logged.

### E. Regression Test Suite
- [ ] `tests/test_admin.py` expanded to cover:
  - Admin allowlist authorization check (403 for non-admins).
  - Quota boundaries (<= 10 GB global, >= stored bytes).
  - Priority boundaries (1–1000).
  - Absence of credentials/tokens in all responses.
  - Reversible ban/unban data integrity.

---

## 5. Verification Checklist

1. [x] Cloud Worker unit tests (`npm test --prefix cloud-worker`) -> 38/38 pass.
2. [x] Python admin and publisher tests (`.venv/Scripts/python.exe -m unittest tests/test_admin.py tests/test_publisher.py`) -> 8/8 pass.
3. [x] Frontend build (`npm run build` in `frontend/`) -> clean, zero errors.
4. [x] Live Cloudflare Worker deployment verified (Version ID `ebb7e097-f4ea-45b8-bec4-9df353aa8434`).
5. [x] Release APK compiled and verified: Alpha 1.1.18 (`f97cd9b321946498f29a5886f0c420cb498018f9db998efe2e9127667a94f909`).
6. [x] Live Landing Page & Admin Console deployed on `gh-pages` and `main`.
