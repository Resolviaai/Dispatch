# Dispatch Future Tasks (Deferred Work)

> **Document Status:** Active Future Roadmap Tracker<br/>
> **Date:** November 7, 2026<br/>
> **Current Baseline:** Dispatch V2 Hybrid Architecture (`v1.7.32`, Build 100)<br/>
> **Authoritative Specification:** `docs/architecture/DISPATCH_V2_ARCHITECTURE.md`

Tasks recorded here are explicitly out of scope for the current stabilization milestone. Each entry lists the reason for deferral and dependencies.

## Route A — Android camera module repairs
- **Mp4FastStart.kt fast-start moov placement** — deferred: Touches recording hardware paths that need device testing on the POCO C65. Dependency: Route A physical device test session.
- **Camera bitrate changes** — deferred: Same reasoning as Mp4FastStart; touches recording hardware paths that need device testing on the POCO C65. Dependency: Route A physical test session.
- **Other Route A repairs** (viewfinder/lifecycle polish) — deferred until the camera module is the active workstream.

## Cloud & infrastructure
- **Multi-region cloud redundancy** — deferred: Single-region Cloudflare Worker + Supabase satisfies current scale.
- **Advanced automated billing / tier management** — deferred: Currently personal / trusted friends workflow.

## Publishing
- **Multi-platform publishing expansion** (native TikTok, Instagram Reels, LinkedIn, X adapters beyond current YouTube Shorts pipeline) — deferred: YouTube Shorts is the primary production publishing target; additional platform APIs require developer app verification from respective platforms.

## Physical Hardware Verification (Outstanding)
- **POCO C65 3-Hour Continuous Recording Stress Test:** While all unit and integration tests for Route A channel alignment, crash recovery, and inbox polling pass in CI/local suites, the 3-hour continuous recording thermal, battery, and background WorkManager upload test on physical POCO C65 hardware remains outstanding.
