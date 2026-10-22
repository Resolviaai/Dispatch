# Dispatch Future Tasks (Deferred Work)

Tasks recorded here are explicitly OUT of scope for the current milestone (Route B manual-link ingestion). Do not implement them during Route B work. Each entry lists the reason for deferral and dependencies.

## Route A — Android camera module repairs
- **Mp4FastStart.kt fast-start moov placement** — deferred: Route B must ship independently of the camera module; no recording behavior changes in this milestone. Dependency: none (isolated Android media work).
- **Camera bitrate changes** — deferred: same reasoning as Mp4FastStart; touches recording hardware paths that need device testing on the POCO C65. Dependency: Route A test session.
- **Other Route A repairs** (viewfinder/lifecycle polish) — deferred until the camera module is the active workstream.

## Cloud & infrastructure
- **Supabase migration** — deferred: current SQLite + single-PC daemon satisfies the single-user POC; migration is SaaS-scope work. Dependency: SaaS decision.
- **SaaS / multi-user infrastructure** — deferred: Dispatch is currently a single-creator local pipeline. Dependency: product decision on multi-tenancy.
- **R2 (or other object storage) integration** — deferred: local disk storage is sufficient for current clip volumes. Dependency: SaaS workstream.
- **New cloud services / queues / worker frameworks** — deferred: the existing daemon-thread workers + SQLite state machine cover current throughput; adding infrastructure now would be speculative (YAGNI).

## Publishing
- **Multi-platform publishing** (Instagram Reels, LinkedIn, X adapters beyond current stubs) — deferred: YouTube Shorts is the only live publishing target in this milestone. Dependency: publisher outbox hardening, per-platform API credentials.
- **YouTube publishing defaults / automatic publication changes** — deferred: approval-before-publishing behavior is intentionally preserved; any default change is a product decision.

## Known limits accepted for now
- **In-memory single-flight ingestion guard is process-local** (`dispatch/youtube_inbox/catcher.py`). Correct for the single-process daemon. If Dispatch ever runs as multiple processes/servers, replace with a database-level claim (e.g. atomic status transition) — dependency: SaaS/multi-process decision.
- **PC dashboard template** (`dispatch/web/templates/index.html`) has no link-entry form; the Route B entry point is the Sessions tab in the React app per user decision. Add a form there only if the legacy dashboard becomes the primary PC experience again.
