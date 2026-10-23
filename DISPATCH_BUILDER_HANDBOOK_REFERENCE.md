# Dispatch — Builder's Handbook Engineering Reference

Purpose: A small, reusable engineering guide for future Dispatch planning and coding-agent sessions. It distills the relevant rules from the user's Builder's Handbook (uploaded 2026-10-23). Use the full book for deeper reading when necessary; do not reread all 4,875 lines for every task.

## Relevant handbook sections

The chosen reading path follows the book's own index and Part 6.5 guidance:

- 6.3 The Builder's Playbook: MVP means the smallest slice that tests a risky assumption; plan in demo-able milestones; define acceptance criteria before code; regression-test bug fixes; keep main deployable.
- 6.4 The Good vs Bad catalog: small commits, reproducible checks, stable interfaces, simplicity, observability, and explicit trade-offs; reject large unreviewable diffs, silent failures, unused abstractions, undocumented manual changes, and vague "fix everything" tasks.
- 6.5–6.6 AI-agent workflow: read the repo and relevant rules first; plan before a non-trivial change; scope each task tightly; inspect every diff; test rather than infer; make no secret-bearing prompts or commits; flag human review for auth, crypto, and permissions; no surprise dependencies or schema/API changes.
- 6.7 Audit-Fix-Audit Trap: define an exit condition. Findings must point to a file/line and reproduced behavior or evidence. Tests decide correctness. Stop when P0/P1 issues are fixed and acceptance tests pass; do not keep adding nits or abstractions.
- 2.3 Backend engineering: validate inputs server-side, authenticate before authorizing, check ownership on every protected operation, use clear errors, idempotency keys, request/correlation IDs, timeouts, and bounded retries.
- 2.4 SaaS engineering: use a shared relational database where appropriate, enforce tenant isolation in both application logic and database RLS, keep state/config out of frontend bundles, and design mutations to be idempotent.
- 3.1 Android: retain UI/state separation, repositories, Room persistence, lifecycle-safe work, and WorkManager for deferrable background work. Assume Android can kill the process at any time.
- 3.4 Mobile hard problems: the phone's local database is the durable source for local work; network sync is a retryable channel. Avoid RAM-only queues and make conflict/retry behavior explicit.
- 4.1 Software process: write a short scope/acceptance spec, then implement in small increments.
- 4.3 Architecture: default to a modular monolith; add service/network boundaries only when they solve a current need. Keep decisions explainable and record load-bearing choices.
- 4.4 APIs: use correct methods/status codes, consistent machine-readable errors, server validation, pagination for growing lists, rate limits, and idempotency on mutations.
- 4.5 Testing: many fast unit tests, fewer integration tests, and only critical end-to-end tests; test observable behavior, boundaries, failures, and regressions.
- 4.6 Code quality: small reviewable diffs, readable names, comments explaining why, migration-backed schema changes, and short Architecture Decision Records (ADRs) for decisions that affect future work.
- 5.1 Databases: version-controlled migrations, constraints and foreign keys, explicit transactions for related writes, parameterized SQL, least privilege, and no plaintext secrets.
- 5.3–5.4 Operations/reliability: configuration from environment/secret stores; timeouts, bounded retries with exponential backoff, structured logs and correlation IDs; do not make manual production changes; do not claim an infrastructure check without running it.
- 5.5 Security: authorization at every resource boundary, input validation server-side, least privilege, vetted cryptography, encrypted sensitive material, and secrets out of code, logs, repository, and client bundles.
- Part 7 reference: use the relevant Security, Database, Distributed Systems, Resilience, Messaging/Async, HTTP/API, Cloud/Infrastructure, Reliability, Delivery/DevOps, Testing, Code Pitfalls, and Compliance concepts when touching those areas.

## Dispatch defaults — already decided; do not re-ask

1. Multi-tenant ownership: explicitly store user_id on each critical user-owned record. Back it with foreign keys, Supabase RLS, and server-side ownership checks. Explicit ownership helps avoid complicated inference and accidental cross-user leaks.
2. Database access: use a hybrid model. Direct Supabase reads are acceptable only for the signed-in user's RLS-protected rows. All privileged writes/operations go through the authenticated server-side Worker: invites/approvals, admin controls, credential handling, processing-job claims, review transitions, upload reservation/completion/deletion, and quota accounting.
3. Secrets: do not store Gemini keys or YouTube refresh tokens in plaintext database fields. When the Worker is implemented, use a vetted AES-256-GCM implementation/library, with a master encryption key held as a Worker secret. Store ciphertext, nonce, authentication tag (or the library's combined representation), and key-version metadata. Never log secrets. Deliver only the relevant user's credential to the trusted PC worker for the specifically leased job, over TLS, just in time and memory-only; no fallback to the owner's key. Do not build a disconnected crypto helper before there is a real Worker flow that uses it.
4. Quota contract: default per-user quota = 2,000,000,000 bytes (2 decimal GB); global hard cap = 10,000,000,000 bytes (10 decimal GB). The cap accounts for finalized stored objects plus active reservations and must not be exceeded under concurrent uploads. Include incomplete/staging media and deletion-pending states in the accounting/cleanup design. Admin may tune per-user quotas, but no individual quota can bypass the global cap.
5. Storage: R2 stays private. No permanent public URLs. Reservation accounting and size/MIME checks happen server-side. Verify actual stored size before finalization. Keep the three-outstanding-upload limit unless the owner changes it.
6. Processing: the owner's Windows PC does the expensive rendering for all invited users in the alpha. Jobs are durable and leased with heartbeat/retry/idempotency; jobs resume after PC restart/offline time. Never put Supabase service-role credentials on the PC.
7. Admin priority: a simple drag-and-drop user order is stored in the database. The PC chooses the highest-priority user with eligible work and processes that user's FIFO jobs. Avoid starvation: after three consecutive jobs for one user, let another eligible user run. Reordering never interrupts active work.
8. YouTube-first: implement and validate YouTube publishing before secondary platforms. Preserve private workspace boundaries. Instagram, LinkedIn, X, and Facebook must not delay the first usable alpha.
9. Source ingestion: private URL ingestion is explicitly out of scope. Public/unlisted URLs do not automatically authorize downloading. Do not expand or promise URL-byte downloading until the applicable method is validated. Preserve existing local behavior unless a separately approved change is required; report this as a launch blocker and do not quietly disable unrelated working paths.
10. Infrastructure boundary: no Docker start/stop/pulls, Supabase CLI initialization/link/reset/migration execution, live SQL/schema/RLS changes, Worker deployments, billing/resource creation, or Git push without explicit owner approval. Code-only work, local offline tests, and Git inspection are allowed. Never claim a live service/database check passed without actual evidence.

## Implementation discipline for every task

1. Inspect git status, branch, HEAD, origin/main, recent commits, and current handoff before edits. Preserve dirty/user changes.
2. Read AGENT_HANDOFF.md, AGENTS.md, PROGRESS.md, and the relevant architecture doc before implementation. Treat the newest brief as authoritative when it explicitly supersedes an older plan; document the conflict and decision.
3. State the scope and measurable done criteria in a short plan. Prefer one coherent feature/fix per task; no repo-wide rewrite.
4. Find the existing code path and callers first. Reuse established patterns. Avoid new dependencies and speculative abstractions.
5. Add/update tests with the behavior. Test boundaries, failures, concurrency/idempotency where relevant, and regression cases.
6. Run the smallest relevant offline checks first, then broader checks if time/changes justify them. Report exact commands and output. Static review is not runtime verification; unit tests are not live RLS verification.
7. Review the diff line by line; run git diff --check; update handoff with changed files, tests, unresolved issues, next exact action, commit hash, and push status.
8. A local commit is okay when the working tree was inspected and the change is coherent; never push automatically. Stop before any owner-controlled infrastructure step.
9. Prioritize P0 (security/data loss) and P1 (core feature broken). Put P2/P3 style or speculative ideas in backlog. Stop when acceptance tests pass and no P0/P1 issue remains.
10. Never say "done" based on code reading alone. Say separately what was implemented, what was tested, and what remains unverified.

## Suggested project task order

1. Reconcile local Git state, then align quota config, kernel defaults/tests, SQL default/check/comments, and plan/handoff docs. Add consistency checks to prevent future mismatches. Offline only.
2. Implement a minimal Worker control plane that enforces auth/ownership, atomic quotas, private R2 lifecycle, and durable job leases. Implement the encrypted credential flow as part of this real service, not a throwaway utility.
3. Add PC sync to the Worker with bounded retries, idempotency, and safe offline recovery.
4. Add invited-user signup/approval, user-private review UI, owner admin/priorities, then complete YouTube end-to-end and phone testing.
5. Only after core alpha works, add other publishing platforms and landing-page polish.

Landing page is last. Simplicity is a hard requirement: don't add a new service, framework, dependency, or abstraction unless a current acceptance criterion requires it.
