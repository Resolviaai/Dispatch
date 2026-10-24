# ADR-001 — Dispatch Canonical Multi-User Architecture (PROPOSED — awaiting owner approval)

Status: **PROPOSED**. Nothing here is settled until the owner approves. Implementation of the
following workstreams does not start until approval.

## Decision 1 — What runs where
- **Android app (primary user interface):** email/password sign-in via Supabase Auth;
  on-device YouTube OAuth initiation; recording; review of own clips; publishing approval.
- **Hosted cloud (source of truth for user-owned state):** Supabase Postgres (profiles,
  sessions/clips metadata, review status, per-user OAuth token vault) + R2 (clip media) +
  Cloudflare Worker (authenticated control plane). Cloud review state **overrides** local.
- **Windows PC (owner's worker, no login UI):** downloads/transcribes/selects/renders/publishes.
  Removed: PC sign-in, PC YouTube login, all credential transfer.
- **Landing page:** stays a static public download page.

## Decision 2 — Worker identity and enrollment (the key question)
**The PC never receives a shared token that can act as any user.** Enrollment:

1. The owner signs in to Dispatch (email/password) on the PC's local dashboard — a one-time
   local action on the owner's own machine, not a cloud login surface for friends.
2. In the app/PC dashboard the owner opens **"Link processing PC"**; the backend issues a
   short-lived, single-use **enrollment code bound to that owner's user_id** (same
   digest-and-rate-limit pattern the old pairing codes used — reused only as a
   worker-enrollment mechanism, never for YouTube credentials).
3. The PC exchanges the code (over HTTPS) for a **worker credential: a per-worker token
   stored in the PC env, cryptographically bound to that one user_id** (backend stores
   token→user binding; every worker API call resolves the user from the token server-side —
   the client can never claim another user).
4. Revoking the binding in the admin panel invalidates the worker token; the PC loses access
   to that user's jobs until re-enrolled.

Consequently: friends' PCs (if ever enrolled by those friends) would each hold a token bound
ONLY to their own user. The shared `DISPATCH_WORKER_TOKEN` becomes backend-maintenance-only
and is deleted from user-facing paths. Adversarial tests: worker-A token cannot read/claim
user B's jobs, media, or tokens; replayed/expired enrollment codes fail; revoked tokens fail.

## Decision 3 — Source-of-truth boundaries
- **Cloud-owned:** identity, sessions/clips metadata + review state, per-user OAuth tokens
  (encrypted at rest), quota, publication history.
- **Local-owned (PC):** processing leases, local file paths, checkpoints, temporary renders.
- **Phone-owned:** recordings until uploaded (unchanged), Room outbox (unchanged).
- **Conflict rule:** remote approval/rejection is durable in the cloud; the PC applies it on
  next sync and never destructively changes local state on sync failure (existing rule).
- **Legacy local data migration:** existing PC-side records stay owned by the PC's enrolled
  worker identity (the owner's own user). Never reassigned to "whoever logs in first"; no
  recordings deleted.

## Decision 4 — YouTube OAuth (per Google web-server flow)
User initiates in-app → Custom Tab → offline-access consent → one-time code → **PC backend
exchanges it server-side** (Web-server client secret in PC env) → refresh token stored
**server-side, encrypted per user** (never on device, never in PC's old token file) →
refresh/revoke/status handled by backend → truthful UI states. The Android OAuth client ID
is public (no secret). No token in URLs, logs, or browser storage.

## Consequences / risks
- Requires the owner to create the Web-server OAuth client + consent screen.
- Workers depend on cloud reachability for job claiming; offline behavior = pause, not
  data loss (existing local checkpointing preserved).
- The old single-user flows keep working for the owner's own processing during migration.

## Explicitly rejected alternatives
- Shared worker token with user-ID header (current prototype): cross-tenant risk — rejected.
- Refresh tokens stored on device: contradicts Google guidance for offline access — rejected.
- Any new pairing code for YouTube credentials: banned by owner directive.
- Automatic LAN discovery as PC identity: unauthenticated — rejected.

**Owner approval needed on:** D1–D4 as written, or amendments in chat.
