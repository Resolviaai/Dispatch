# Dispatch — Authoritative Operations Dashboard & Multi-Platform Management

**Document Status:** COMPLETE & DEPLOYED  
**Author:** Antigravity  
**Date:** 2026-10-25  
**File Target:** `docs/ADMIN_OPERATIONS_DASHBOARD.md`  
**Live Production Worker:** `https://dispatch-cloud.resolviaai.workers.dev` (Version `0a5ce946-7e8c-4914-b60b-04e22b143e23`)  
**Live Admin Console:** `https://resolviaai.github.io/Dispatch/admin.html` and in-app `AdminScreen`  

---

## 1. Executive Summary & Design Invariants

Dispatch implements a unified operations control plane answering five foundational operational questions without exposing secrets, tokens, or private media:

1. **User Identity & State**: Which users are active vs disabled? (Reversible ban/unban with non-destructive preservation of all R2 storage and database records).
2. **Authoritative Storage & Budget**:
   - **Actual R2 Usage**: Real verified stored bytes in active R2 objects.
   - **Pending Reservations**: Byte reservations in `upload_reservations`.
   - **Allocated User Quotas**: Sum of user `storage_quota_bytes`.
   - **Unallocated Quota Pool**: `10,000,000,000 bytes - sum(allocated_quotas)` (strictly labeled as *Unallocated Quota Pool*, never "free storage").
3. **Processing Jobs**: Live queue status (`queued`, `running`, `done`, `failed`) from `processing_jobs`.
4. **Quota & Priority Governance**:
   - Per-user decimal GB quotas (`1.0 GB`, `2.0 GB`, `5.0 GB`, and custom input bounded by current stored bytes and 10 GB global ceiling).
   - Per-user queue priority (`1–1000`, lower number = processed first).
5. **Publishing Destinations Visibility**:
   - Displays connected platform badges (YouTube, X/Twitter, Instagram, Facebook, LinkedIn) without ever surfacing access tokens, refresh tokens, client secrets, or Gemini API keys.

---

## 2. Completed Architecture & Changes Log

### A. Database Migrations & Live Supabase Synchronisation
- **Applied Migration 10 (`20261025000010_connected_accounts_and_keys.sql`):**
  - Created `public.connected_accounts` table for multi-account publishing (YouTube, Instagram, Facebook, LinkedIn, Twitter/X) with unique constraints and RLS policies.
  - Created `public.gemini_connections` encrypted vault table.
  - Backwards-compatibility data migration: automatically transferred existing `public.youtube_connections` into `public.connected_accounts`.
  - Created security-definer RPC functions:
    - `public.cloud_list_connected_accounts(p_user_id)`
    - `public.cloud_store_connected_account(...)`
    - `public.cloud_delete_connected_account(...)`
    - `public.cloud_store_gemini_key(...)`
    - `public.cloud_get_gemini_status(...)`
    - `public.cloud_set_user_priority(...)`
    - `public.cloud_claim_job(...)` with stale lease reclamation (45 min) and user anti-starvation fairness.
- **Applied Migration 11 (`20261025000011_user_ownership_and_edit_windows.sql`):**
  - Added support for `edit_window` kind in upload reservations with 512 MiB limit.
  - Hardened `cloud_delete_connected_account` to clean up legacy `youtube_connections` when a YouTube account is disconnected.
- Recorded both versions in `supabase_migrations.schema_migrations` on live Supabase `aldzlbdxplqkcnyjhzmm`.

### B. Cloudflare Worker Control Plane (`cloud-worker/src/index.ts`)
- **Admin Endpoints:**
  - `GET /admin/whoami`: Confirms admin status for verified Supabase identity.
  - `GET /admin/users`: Returns authoritative users list, connected provider names, and overall storage/job queue metrics.
  - `POST /admin/users/:id/quota`: Updates storage quota with decimal validation and stored-bytes boundary protection.
  - `POST /admin/users/:id/priority`: Updates processing priority (1–1000).
  - `POST /admin/users/:id/ban` & `POST /admin/users/:id/unban`: Sets/clears ban status via Supabase auth admin API.
- **Admin Allowlist (`ADMIN_EMAILS`):**
  - Updated to `resolviaai@gmail.com,rohitbag959@gmail.com`.
- **OAuth Multi-Provider Integrations:**
  - Google / YouTube (OAuth 2.0)
  - Instagram Reels (Meta Graph API)
  - Facebook Pages / Reels (Meta Graph API)
  - LinkedIn (OAuth 2.0 with author URN)
  - X / Twitter (OAuth 2.0 with PKCE and AES-256-GCM token encryption)
  - Added error checks on all callback storage RPCs with user-friendly error views instead of silent failures.
- **CORS Preflight:**
  - Full OPTIONS handler for web/admin origins returning 204 with required headers.

### C. Python PC Studio Backend (`dispatch/web/app.py`)
- Added `/api/admin/users/{user_id}/quota` endpoint for local studio admin control.
- Added `/api/admin/users/{user_id}/priority` endpoint for local studio admin control.
- Added connected providers lookup to `/api/admin/users`.
- Updated `DISPATCH_ADMIN_EMAILS` to include `rohitbag959@gmail.com`.
- Verified 7/7 unit tests in `tests/test_admin.py` passing.

### D. Frontend React Application (`frontend/src/`)
- **`AdminScreen.tsx` (Major Upgrade):**
  - Dual Mode support: automatically uses LAN PC studio if online, or falls back seamlessly to Cloud Worker with Supabase JWT.
  - Metric cards: Users count, Stored Media (R2), Quota Pool, and Job Queue.
  - Search input (by email or user ID) and status filter (All, Active, Disabled).
  - Quota controls: presets (`1G`, `2G`, `5G`), custom decimal input, and save button.
  - Priority controls: `▲ -10` nudge, numeric input, `▼ +10` nudge, and save button.
  - Safe destination badges without secrets.
  - Double-confirmation modal for disabling accounts, confirming data preservation.
- **`SettingsScreen.tsx`:**
  - Added document `visibilitychange` and window `focus` event listeners to automatically trigger `refreshAccounts()` when returning from external browser OAuth flows.
  - Added multi-stage polling (2s, 4s, 7s, 11s, 16s, 22s, 30s) upon launching OAuth.
- **`ClipsScreen.tsx`:**
  - Fixed Lucide `Plus` import.
  - Uses `listConnectedAccounts` for multi-destination publishing selection.
- **`CameraScreen.tsx`:**
  - Clean edge-to-edge camera viewfinder without dark gradient overlays.

---

## 3. Operations Runbook & Testing Guide

### A. Testing Admin Access
1. Open the Admin Operations Console at `https://resolviaai.github.io/Dispatch/admin.html` or in the Dispatch mobile app (Admin tab).
2. Sign in with an admin email (`resolviaai@gmail.com` or `rohitbag959@gmail.com`).
3. Verify the Overview row displays real counts:
   - Stored Media shows actual GB from active R2 objects.
   - Unallocated Quota Pool displays remaining budget out of 10 GB.
   - Job queue counts reflect live `processing_jobs`.

### B. Managing User Quota & Priority
1. Use the search bar to locate any user by email or partial UUID.
2. Click `1G`, `2G`, or `5G` to immediately update quota, or enter a custom decimal (e.g. `1.5`) and click **Set**.
3. Use `▲ -10` to prioritize a user's jobs ahead of others in the queue, or `▼ +10` to lower priority. Click **Save**.

### C. Disabling a User
1. Click **Disable Account** on any active user row.
2. Read the confirmation dialog: all media and data remain preserved.
3. Click **Confirm Disable**.
4. The user's status updates to `Disabled`. They can be re-enabled at any time by clicking **Enable Account**.

---

## 4. Verification Record

| Component | Target / Suite | Result |
| :--- | :--- | :--- |
| Supabase Remote DB | `supabase_migrations.schema_migrations` | Migrations 10 & 11 applied |
| Cloud Worker Tests | `npm test --prefix cloud-worker` | 38 / 38 passing |
| Python Admin Tests | `tests/test_admin.py` | 7 / 7 passing |
| Python Publisher Tests | `tests/test_publisher.py` | 2 / 2 passing |
| Frontend Build | `npm run build --prefix frontend` | Clean (23.86s, 0 errors) |
| Live Worker Deploy | `dispatch-cloud.resolviaai.workers.dev` | Version `0a5ce946-7e8c-4914-b60b-04e22b143e23` |
