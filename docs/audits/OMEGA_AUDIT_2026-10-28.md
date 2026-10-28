# Dispatch — Omega forensic audit and alpha-readiness supplement

**Verdict: NOT READY**  
**Audited baseline:** `82309ffd50b722ce2ed878856e4894e7a801a531`  
**Branch:** `arena/6daba057-dispatch` · **Audit date:** 2026-10-28 UTC  
**Mode:** audit only; no application fixes. Reproduction scripts, inventories and logs are audit artifacts.

## 0. What this report does—and does not—establish

This is an evidence-backed repository audit with local tests and controlled fault probes, **not a completed exhaustive line-by-line certification**. The requested real-device, Windows, live publishing, sustained recording, human AI-quality review and three-consecutive-E2E acceptance gates could not be executed here. It would be dishonest to claim all code, every control, every device state, every policy section, or every concurrency interleaving was individually verified. The automated inventory is explicitly not a manual review certificate. Further review can discover additional P0/P1 defects.

The review used security, SRE, protocol, correctness, Android lifecycle, accounting, truthfulness and historical lenses. No independent subagents were available or spawned; these are review lenses, not nine independent audits. Findings below were challenged against concrete failure scenarios. For example, quota-backlog stranding was NOT reported: a drain actually exists in `transcription/gemini_engine.py` and is called by `transcription/worker.py`.

### Evidence vocabulary

- **Verified defect (runtime):** isolated local execution reproduced the incorrect behavior.
- **Verified defect (source scenario):** inspected executable code establishes the specified failure path; device/browser/provider execution was not performed.
- **Verified passing behavior:** a named test/command actually passed, within its mocks and environment.
- **Unverified risk:** credible but not reproduced, or dependent on unknown deployment/provider conditions.
- **Missing requirement:** an acceptance decision or essential contract remains unspecified/unverified.
- **Optional improvement:** not an alpha blocker. No architectural cleanup is required merely for style.

Severity follows the supplied prompt: P0 includes fake visible states, lost edits, confidentiality violations and silent corruption; P1 covers recovery/reliability/protocol failures; P2 covers test/build-contract and accessibility gaps. These labels do not imply every P0 has equal exploitability. LAN exposure prerequisites and mock boundaries are stated explicitly.

### Environment and exact actions

Linux x86_64 sandbox, kernel `6.1.158+`, 2 online CPUs, approximately 3.85 GiB RAM; Python 3.11.2; Node 22.22.3; npm 10.9.8. No `ffmpeg`, `ffprobe`, `adb` or Java executable was found. No Android SDK/device, Windows system, live service credentials, production database access or publishing consent was used. No Android APK installed or built. No browser performance trace, screen-reader session, real media playback or battery/thermal measurement was performed.

A `.venv` was created and test/web/Google dependencies installed. `faster-whisper` and its model were not installed. Exact installed Python versions are in `omega-2026-10-28/python-dependencies.txt`; JS installs used each existing `package-lock.json` through `npm ci`. This is a partial audit environment, not a clean supported production installation. No lockfile pins all Python dependencies in the project itself.

| Executed check | Actual outcome | Interpretation |
|---|---|---|
| `python -m pytest -q --disable-warnings` in isolated audit storage | **414 passed, 63 failed, 1 skipped, 20 errors**, 13.93 s | NOT a clean suite; many failures/setup errors require FFmpeg. Not 83 independently established product bugs. |
| Five selected Python modules, separately | **28 passed**, 1.22 s | Editorial validation, range/trim, credential-transfer-removal and vault/remote-job unit/contract checks pass; mostly controlled fixtures/mocks. |
| `frontend: npm test` | exit 0 | Existing script-driven caption/camera/studio/update checks passed, not browser E2E. |
| `frontend: npm run build` | exit 0, Vite 14.28 s | Bundles successfully; does NOT typecheck or prove a usable signed APK. |
| `frontend: tsc --noEmit` | **9 diagnostics, failure** | Vite's transpile-only build hides type-contract defects; F16. |
| `cloud-worker: npm test` | **50/50 passed**, 348 ms test-run duration | Controlled handler/unit tests, not deployed R2/Supabase/OAuth verification. |
| `reproduce.py` | **11 current-defect assertions reproduced** | Assertions intentionally demonstrate existing failures; these are not acceptance tests saying product behavior is correct. |
| Vite SSR bridge probe | HTTPS → HTTP; `Bridge.getAuthToken` undefined | Confirms transport rewrite and missing wrapper member without a browser/device. |
| Git archaeology | shallow checkout, **one reachable commit** | Cannot establish earlier fixes, dates of regression, key rotation or historical secret removal. |

Full Python output is retained. Common failure signatures include missing `ffmpeg`, missing `tests/fixtures/tiny_valid.mp4`, unconfigured Gemini key, authorization expectation mismatches, and shared-suite/mock assumptions. Investigating them individually is still required; this report does not waive non-FFmpeg failures. The production storage files were not intentionally modified. Tests ran with isolated DB/storage settings and disabled automatic outbox/pollers. Frontend build rewrote Android web assets; those generated changes were restored to the exact baseline afterward.

Artifacts: [`omega-2026-10-28/`](omega-2026-10-28/) contains raw test logs, dependencies, environment, source inventory, control/route inventory and isolated reproducer.

## 1. Actual architecture and feature completeness matrix

The supplied seven-pillar architecture is no longer the whole implementation. There are **Supabase identity/RLS, Cloudflare Worker/R2 storage, remote processing jobs, cloud caption edits and offline reject/restore commands, per-user credentials, Gemini Stage 1/Stage 2, extra social publishers, media/font import, browser capture, and legacy LAN/Python-mobile/Compose paths**. These are existing features, not proposed additions. Treating only the older seven pillars as the audit boundary misses production trust boundaries.

Dependencies: **A** Android/CameraX/Room/WorkManager; **D** PC/Python/SQLite/filesystem/FFmpeg; **C** Worker/Supabase/R2; **Y** Google OAuth/YouTube; **G** Gemini; **W** local Whisper. Entry paths below are implementation evidence, not proof of successful journeys.

| Feature / intended outcome | Actual implementation and entry | Dependencies | Expected success | Failure/recovery implemented | Evidence and minimum gap/fix |
|---|---|---|---|---|---|
| First launch and account identity | `AuthGate`, Supabase client, native current-user bridge | A,C | Real session or clearly anonymous local mode | Native no-session path fabricates a session | F05 P0; remove pretend auth, retain explicitly labeled offline capture only if intended |
| Permissions and camera setup | `MainActivity`, `CameraCaptureManager`, CameraScreen | A | Camera/audio permission then truthful ready/capture | Native error/state events, permission requests | Inspected selected lifecycle/bridge paths; actual denial/revocation/device behavior untested |
| Record/pause/resume/stop, camera/zoom/focus/torch/audio | CameraScreen → bridge → CameraCaptureManager/SegmenterEngine | A, Room, disk | Actual capture, correct state, finalized playable segments | Disk checks, callbacks, foreground-service lifecycle, error statuses | Some pure camera-state tests pass; no physical recording verified |
| Screen-off rolling segments | RecordingForegroundService, SegmenterEngine | A | Supported foreground capture across screen-off | 10-minute rolling timer; Room row precedes capture; temporary-file finalization | 30–60-minute, contention, thermal and process-death tests missing evidence, not assumed passing |
| Raw account connection and upload | Settings/accounts → Worker OAuth → encrypted vault; upload worker cloud and legacy routes | A,C,Y | Correct authorized channel and resumable transfer | Offset reconciliation, lookup, bounded no-progress helper; Room persists IDs | F04, F13; policy R01; live 308/401/final-response-loss tests not executed |
| Recordings/sessions list, playback, delete | RecordingsScreen; native bridge; inbox/source endpoints | A,D,C | Correct segment/session grouping; owner-bound durable delete | Mixed phone/local/cloud paths, modal confirmation | F16 missing auth wrapper; session ID vs video ID deletion requires further path testing |
| Automatic YouTube discovery/download | `main.py` → poller/catcher/oauth | D,Y,yt-dlp | New uploads ingested once into validated media | Processing wait/stall states, retry/status polling, download promotion | Visibility/timing tests execute in suite, no real unlisted download; R01 policy blocker |
| Manual ingestion/browser capture/legacy transport | `/api/youtube/ingest`, `/api/recordings/upload`, `/api/media/upload`, sync receiver, `dispatch_mobile` | D,A,Y | Explicit import/capture is honest and owner-bound | Multiple alternate entry points exist | Contradicts literal “YouTube only” if applied to all ingestion. Product decision: distinguish supported automatic transport from legacy/manual APIs; do not add a second transport silently |
| Transcription and quota backlog | TranscriptionWorker; VTT parser; Gemini engine; Whisper adapter | D,Y,G,W | Accurate source-aligned transcript and eventual backlog drain | Caption wait; Stage 1/2; FIFO quota drain | Named timing/range tests pass, no actual speech-quality/Whisper run; per-account cost/fairness unverified |
| AI highlights | HighlightWorker/highlight_finder; preference learner | D,G | Valid, supported, nonduplicate standalone clips | Structured candidates, snapping/clamping, retries | F08 recovery; F09 media coordinates; F12 cross-user feedback; no human corpus evaluated |
| Candidate rendering, layout, subtitles | HighlightWorker → renderer/captions/FFmpeg; preview overlay | D, disk | Right source/time, matching layout and word timing | Temporary render, range revision checks, final probe | FFmpeg unavailable. Candidate video deliberately captionless with overlay; do not mistake this design alone for a defect |
| Review, trim, edit captions/templates | ClipsScreen/StudioWorkstation/CaptionStudio; trim/layout/captions APIs | D,C, React | Save survives reload/restart; conflicts detected | Range revisions, serialized request mutations, cloud import | F07 edit loss, F09 source mismatch, F10 stale cloud asset; no browser interaction certification |
| Approve/reject | ClipsScreen → approve/reject API → DB/outbox | D,C | Approval only after valid render and durable queue | Final render/probe before backend approval; rejects logged | Backend contract has useful guards, but F06 frontend lies on failure; F12 preference privacy |
| Publishing and reconciliation | durable `publishing_outbox`; YouTube/Instagram/Facebook/LinkedIn/X adapters | D,C,Y/social APIs | Correct account/render, confirmed remote result | Claim guard, ambiguity → reconciliation, backoff, auth block, processing state | F02 authorization; F14 aggregation; F15 endless polling; provider-specific live contracts unverified |
| Cloud media/edit window/captions | `cloud/sync.py`, Worker, SQL migrations, phone cloud clip library | D,C | Correct owner/current asset and honest sync status | Reservations, caps, command drain, tombstones, cloud caption revisions | F03 misownership, F10 outdated asset, F11 false sync completion |
| Offline reject/restore and purge | Worker clip commands → sync drain → DB grace purge | C,D | Durable intent, owner checks, grace-period purge | Command claim/ack, tombstone suppression, restore support | Relevant tests partly pass/partly fail; not a deployed two-user recovery proof |
| Admin/settings/provider keys/updates/fonts | AdminScreen, SettingsScreen, updateService/native update manager, font endpoints | A,D,C, GitHub | Authorized settings/admin; authentic update; usable fonts | Encrypted prefs, release identity tests, validation | Admin suite not clean here; no installed release verification. Font import boundary remains R07 |
| Daemon startup/resource governance/backups | `main.py`, governor, orchestrator/recovery, db.backup_database | D | Single instance, orderly restart, preserved work | PID file, resource gates, SQLite backup helper | F08 actual inbox outside orchestrator recovery; duplicate startup/Windows behavior R04; helper existence ≠ scheduled backup proof |

A generated line-index of visible handlers/routes is retained in `controls-and-routes.txt`. It is a worklist, **not** evidence that each control's complete UI → side-effect → refresh path was executed. In particular, TemplateStudio's full control surface, legacy HTML UI, native Compose screens and alternate transports remain only partially reviewed. No absent imaginable enhancement is classified as a release requirement.

### State reconstruction from the reviewed active paths

- Android: session/segment IDs → `RECORDING` row before hardware start → finalized `.mp4` + hash/metadata/outbox → `QUEUED_FOR_UPLOAD` → upload/YouTube/PC confirmation states. `ERROR_*`, missing-file and credential conditions need device-backed recovery testing. Room migration code exists, but old-install upgrade was not run.
- Inbox: `DISCOVERED → DOWNLOADING → VALIDATING → DOWNLOADED → WAITING_FOR_TRANSCRIPT → TRANSCRIBED → ANALYZING → CLIPS_DEFINED → COMPLETED`; processing/visibility waits, stalled/failed paths supplement these. `ANALYZING` has no normal restart consumer: F08. The schema comment's older state names are not the complete executable state machine.
- Clips: definitions and quota backlog → `ready_review → approved → published`; reject/restore/purge paths and revision/stale metadata overlay these states. Approval must refer to the same source and edit revision: currently not guaranteed, F07/F09/F10.
- Outbox: `queued → uploading → processing/published`, or `blocked_needs_auth`, `failed`, `reconciliation_required`; nonambiguous retry returns to queued with deadline. `processing` lacks a bounded terminal/recovery policy: F15.
- Orchestrator jobs separately define VERIFY/TRANSCRIBE/ANALYZE/RENDER/FINALIZE/COMPLETED and lease recovery. **That recovery does not repair the inbox state machine used by the active HighlightWorker.** A robust legacy orchestrator is not proof of active-path recovery.
- Cloud reservations, media objects, processing jobs and clip commands add independent durable state machines. No full deployed transition/observer audit was executed; SQL migration and RLS test presence alone do not establish it.

## 2. Confirmed findings

Every fix below is a sketch, not implemented. Each reproduction references the immutable baseline. Regression suggestions describe **desired** behavior; the retained defect-probe assertions must be inverted/replaced after remediation.

### [SEV-P0] F01 — LAN location is accepted as authorization

**Location:** `dispatch/web/app.py:237–309`, especially the local-network branch; media exception at `178–220`, `459–480`.  
**Category:** security/authentication. **Evidence:** `if is_trusted_localhost(client_host) or _is_local_network_peer(client_host): ... return`.

**Failure scenario:** configure the intended phone-accessible LAN bind; an unpaired client on the LAN POSTs `/api/settings` with the server's normal Host and no Origin/token. `verify_dashboard_auth` accepts it. A browser served by another private-IP host can also satisfy the permissive private-IP Origin rule. Media resolution with `allow_lan_peer=True` deliberately returns unrestricted identity for unauthenticated LAN callers. This is not an Internet exploit against the default loopback-only bind; it is a broken boundary when LAN access is enabled.

**Verification:** runtime probe 01 called the actual guard with `192.168.1.77`, no auth, normal Host and no Origin: accepted. Existing `test_dashboard_auth` expectations also fail, but the independent probe avoids relying on their expected status code.

**Root cause (5 Whys):** unauthorized write → LAN branch bypasses token → address treated as identity → convenience origin/host validation substitutes for authentication → no uniform policy separating local-owner privilege from paired remote access.

**Patch-vs-fix:** host/origin validation is a symptom defense, not authorization; it does not pass the costume test. **Minimum fix:** retain explicit loopback owner exception if intended; require a valid paired/session principal for all non-loopback operations; use owner-scoped signed media URLs rather than granting all LAN media access.

**Blast radius:** settings/integrations and any guard-only mutation; LAN-exposed media. **Regression:** no-token and bogus-token LAN requests rejected regardless of missing/private-IP Origin; valid paired principal succeeds with only its intended permissions. **Outcome:** unfixed.

### [SEV-P0] F02 — Publisher jobs expose all tenants and reconciliation ignores ownership

**Location:** `dispatch/web/app.py:1398–1418`, `1421–1540`. **Category:** security/BOLA.  
**Evidence:** unfiltered `SELECT * FROM publishing_outbox`; mutations select `WHERE id = ?` after generic `verify_dashboard_auth`, with no caller/owner comparison.

**Failure scenario:** B lists the unprotected outbox, learns A's job ID and metadata, then uses B's legitimate credentials to mark A's job failed or retry/reconcile it. Authentication fixes alone do not prevent this.

**Verification:** probe 02 controlled the auth-success boundary for principal B and used actual endpoint/SQLite operations; A's job became `failed`. This proves absent owner enforcement, not real Supabase JWT validation. Reads have no auth dependency at all.

**Root cause (5 Whys):** foreign job modified → ID-only query → no owner predicate → authN mistaken for authZ → multi-user model added without applying authorization across all existing routes.

**Patch-vs-fix:** clip ownership checks in other endpoints are local patches; they do not protect this module. **Minimum fix:** resolve identity once, scope reads and every reconciliation mutation to job owner; explicit local-owner administration is a separate permission. Do not infer ownership from client-supplied account IDs.

**Blast radius:** every tenant on shared PC dashboard. **Regression:** two-user list/resolve/auto-reconcile checks; B cannot learn or mutate A jobs. **Outcome:** unfixed.

### [SEV-P0] F03 — Legacy clips are assigned to the newest unrelated cloud user

**Location:** `dispatch/cloud/sync.py:30–51, 78`; profile query orders `created_at.desc&limit=1`. **Category:** privacy/ownership.  
**Evidence:** `user_id = clip.get("user_id") or get_default_user_id()`.

**Failure scenario:** local legacy clip lacks owner; a friend signs up later than the operator; background sync chooses that friend's newest profile and uploads private title/media as that user using service-role authority. If profile lookup fails it substitutes a literal UUID instead of preserving unknown ownership.

**Verification:** probe 05 supplied a controlled newest-profile response and reproduced selection of `newest-unrelated-user`; subsequent `sync_clip_to_cloud` uses that value for metadata and Worker owner headers. No private content was really uploaded.

**Root cause (5 Whys):** content assigned incorrectly → missing owner substituted → newest user assumed operator → no explicit legacy adoption → single-user fallback survived multi-user rollout.

**Patch-vs-fix:** per-command ownership gates elsewhere cannot repair an incorrect owner assigned at creation/sync. **Minimum fix:** refuse ownerless sync until explicit local owner adoption; remove literal UUID/newest-user heuristics. Single-profile auto-adoption, if accepted, must verify exactly one eligible profile, never select one from many.

**Blast radius:** legacy/imported ownerless clips. **Regression:** two profiles plus ownerless clip never syncs or changes owner without consent; service failure also fails closed. **Outcome:** unfixed.

### [SEV-P0] F04 — Vault has a publicly known fallback encryption key

**Location:** `cloud-worker/src/oauth.ts:47–79`; `dispatch/youtube_connections.py:38–103`. **Category:** secrets/cryptography.  
**Evidence:** SHA-256 of `trimmed || "dispatch-fallback-vault-key"`; PC always appends the same fallback candidate.

**Failure scenario:** missing/blank Worker vault secret causes encryption of OAuth/provider credentials with a key derivable from public source. Anyone who obtains the ciphertext can decrypt it; PC silently accepts such ciphertext even without configured vault key. This is conditional on bad deployment configuration and ciphertext access, **not evidence that production credentials have leaked**.

**Verification:** probe 08 generated nonsecret fixture ciphertext under the public key and decrypted it through the real PC decryptor with no configured secret. Worker fallback is directly established by source; deployed secret configuration unknown.

**Root cause (5 Whys):** known-key encryption → blank-secret fallback → configuration failure made operational → interoperability prioritized over fail-closed setup → no required validated cryptographic configuration boundary.

**Patch-vs-fix:** ciphertext encryption alone does not satisfy confidentiality if the key is public. **Minimum fix:** reject empty/invalid vault config in both components; use one documented encoding; remove public candidate; identify/re-encrypt affected records and revoke/rotate affected credentials if any deployment used fallback.

**Blast radius:** YouTube tokens and other credentials/keys using this vault. **Regression:** blank secret must fail before storing/decrypting; valid configured-key round trip passes; known fallback rejected. **Outcome:** unfixed; actual deployment exposure unresolved.

### [SEV-P0] F05 — Native first launch manufactures an authenticated identity

**Location:** `frontend/src/auth/AuthGate.tsx:21–36`. **Category:** ux-truthfulness/identity.  
**Evidence:** native/no-session branch constructs `user.id = 'dev-creator-user'`, `access_token = 'dev-token'`, then renders authenticated children and calls `Bridge.setCurrentUserId`.

**Failure scenario:** fresh Android launch with no Supabase session bypasses real login and assigns a nonexistent user. Subsequent capture ownership uses the fake ID while cloud calls cannot authenticate; UI and persistence disagree about who owns the work. A native platform is not a development-build predicate.

**Verification:** verified source scenario, not installed APK execution. The branch is not gated by a build-development flag. This does not demonstrate a Supabase RLS bypass.

**Root cause (5 Whys):** fake session shown → no-session path fabricates it → native platform used as dev flag → prototype convenience retained → local offline access and authenticated cloud access lack separate representations.

**Patch-vs-fix:** server rejection is not a fix for a fabricated client identity. **Minimum fix:** real sign-in, or explicitly anonymous offline recording with no invented credential/owner and explicit later adoption. **Regression:** native/no-session starts sign-in or clearly anonymous mode; expired/revoked session never becomes `dev-*`. **Outcome:** unfixed.

### [SEV-P0] F06 — Failed approval remains visibly approved with no rollback

**Location:** `frontend/src/screens/ClipsScreen.tsx:2363–2433`. **Category:** ux-truthfulness.  
**Evidence:** removes ready item, inserts `status:'approved'`, clears submission state and closes editor **before** fetch; `!res.ok` only sets feedback; catch only logs.

**Failure scenario:** user approves; backend returns 500 for render failure, 409 for changed range, 401, or request disconnects. UI still displays approved item and removes it from review; thrown network errors may produce no visible failure. Local pending intent is represented as completed approval. Caption pre-save HTTP failure is ignored too.

**Verification:** source-established deterministic branch; no mounted-browser fault injection was executed. Backend itself does render/probe before durable approval—this finding is specifically frontend divergence, not that backend guard being absent.

**Root cause (5 Whys):** false approved state → optimistic completion before acknowledgment → error paths omit rollback/refetch → pending and approved conflated → action-result state lacks durable acknowledgment contract.

**Patch-vs-fix:** feedback text alone is a symptom patch. **Minimum fix:** show `approval_pending` separately; preserve snapshot; on failure restore/refetch and surface actionable error; use finally for in-flight state and bound requests; only show approved after backend acceptance. **Regression:** 401/409/422/500/disconnect each restores reviewable clip and edits; repeated click does not create competing approval. **Outcome:** unfixed.

### [SEV-P0] F07 — Stale editors silently overwrite newer captions

**Location:** `dispatch/db.py:783–817`; `dispatch/web/app.py:1893–1930`. **Category:** data-integrity/lost edits.  
**Evidence:** update predicate checks only `range_revision`; caption-only edits do not increment that revision.

**Failure scenario:** A and B load same range revision 0; A saves corrected text/styles; stale B saves old text with another style; both succeed and B erases A's changes. Serializing requests per clip prevents simultaneous execution, not stale sequential overwrites. Omitting range revision defaults to current revision as well.

**Verification:** probe 03 used real SQLite writes, both returned True, final JSON contained stale B. No browser tabs needed to demonstrate the storage contract failure.

**Root cause (5 Whys):** edit lost → stale save accepted → range unchanged → range revision reused as edit version → concurrency contract covers trim only, not complete editable document.

**Patch-vs-fix:** locks/range fencing are symptom patches for this class. **Minimum fix:** caption/document revision or content hash, required compare-and-swap on save and approve; return 409 with latest version. Reconcile cloud and local edit versions deliberately. **Regression:** two stale saves—one wins, one conflicts without data loss; range and caption conflicts tested separately. **Outcome:** unfixed.

### [SEV-P1] F08 — Process termination permanently strands ANALYZING inbox jobs

**Location:** `dispatch/ai_clips/worker.py:114–140, 636–652`; `dispatch/db.py:1434–1443`; `dispatch/orchestrator/recovery.py:27–52`. **Category:** crash recovery.

**Failure scenario:** active highlight extraction sets inbox `ANALYZING`; PC terminates during Gemini/validation/save; reboot starts a worker that reads only `TRANSCRIBED` and `CLIPS_DEFINED`. Poller considers `ANALYZING` already handled, and pipeline-job lease recovery touches another table. The recording never progresses automatically.

**Verification:** probe 04 persisted ANALYZING, instantiated a new worker and ran process_pending; no highlight callback, unchanged state. This models the durable post-crash condition, not a real power-cut test.

**Root cause (5 Whys):** no resume → no ANALYZING consumer → no claim expiry → active inbox state lacks lease/recovery → legacy orchestrator recovery assumed to cover a separate worker.

**Patch-vs-fix:** retry handling inside a live try/except does not survive process death. **Minimum fix:** active-path durable claims/expiry and startup reconciliation to TRANSCRIBED; preserve saved definitions and avoid duplicate/approved overwrite. An explicit single-daemon startup reclaim may suffice for alpha if exclusive lock is sound. **Regression:** injected termination before and after definitions commit converges without duplicate clips. **Outcome:** unfixed.

### [SEV-P0] F09 — Multi-segment clips lose source-coordinate identity at approval

**Location:** `dispatch/ai_clips/worker.py:238–366, 640–648, 730–738`; `dispatch/web/app.py:640–670, 1299–1346`. **Category:** media correctness/data integrity.

**Failure scenario:** two 600 s segments are stitched at transcript level; clip selected at session 610–630 s. Candidate worker resolves second file at local 10–30 s. Saved clip still has source chunk/global times; final approval resolves only that chunk's filepath and renders 610–630 s from it, not the normalized candidate source. A cross-boundary candidate similarly requires a stitched asset that the approval path does not reconstruct. At best approval fails; with longer/different source, wrong content can be selected.

**Verification:** probe 10 used placeholder paths, actual worker source resolver and actual dashboard resolver: candidate `(part2,10,30)`, dashboard `part1` while approval retains `(610,630)`. **No decoded wrong render claimed.** Coordinate-contract divergence is reproduced independently of FFmpeg.

**Root cause (5 Whys):** wrong final source/time → final resolver knows only one chunk → normalized source result is ephemeral → session-global and chunk-local timestamps share fields → render/preview/approve lack a shared explicit media-coordinate contract.

**Patch-vs-fix:** special stitching in candidate rendering is a local patch; approval/backlog/source endpoints bypass it. **Minimum fix:** persist source segment manifest + time coordinate, or reuse one authoritative resolver across candidate/backlog/source/final render; ensure edits remain in a documented coordinate system. Also remove rendered-output-as-original fallback when original source is required. **Regression:** clip in part2, boundary-spanning clip, missing middle segment, approval and restart all resolve intended decoded frames/audio. **Outcome:** unfixed.

### [SEV-P0] F10 — Cloud sync advertises current metadata but retains old media

**Location:** `dispatch/cloud/sync.py:126–145, 169–185`. **Category:** stale media/ux-truthfulness.  
**Evidence:** always sends `"render_stale": False`; any ready edit window for local clip causes immediate success before upload.

**Failure scenario:** stage render v1, then trim/re-render v2 or mark render stale locally. Metadata is upserted with new range/revision and stale=false; old ready window makes sync skip staging. Phone gets v2 metadata with v1 bytes and possibly old captions/thumbnail. Existence is being used as version equality.

**Verification:** probe 06 provided a changed local file, revision 2, stale=1 and old ready window: only metadata POST happened; stale was falsely cleared. Provider responses were controlled; no R2 bytes uploaded.

**Root cause (5 Whys):** mismatched preview → old window reused → no revision/fingerprint comparison → readiness mistaken for freshness → asset and document versions not linked.

**Patch-vs-fix:** filename/existence checks cannot satisfy current-render identity. **Minimum fix:** stage based on render/content revision/hash; preserve stale flag until matching media is ready; atomically activate new window/media with its exact range revision; old object cleanup only after replacement. **Regression:** unchanged asset skips upload; changed bytes/range/captions require replacement; failed replacement leaves old version clearly stale. **Outcome:** unfixed.

### [SEV-P0] F11 — Failed cloud linkage is reported as successful synchronization

**Location:** `dispatch/cloud/sync.py:224–278, 317–356`. **Category:** durability/truthfulness.

**Failure scenario:** reservation, PUT and complete succeed; Supabase edit_windows insertion returns 500. Code sets `window_id=None`, skips transcript sync, continues optional thumbnail calls and logs/returns success. Media may be charged/stored but undiscoverable as an edit window. Link and transcript/thumbnail responses are also unchecked in several paths.

**Verification:** probe 07 injected actual control-plane HTTP responses via mocks; edit-window POST rejected with 500 and actual sync function returned True.

**Root cause (5 Whys):** false completion → required link failure ignored → response converted to None → optional/required stages conflated → multi-store sync lacks explicit postconditions/checkpoints.

**Patch-vs-fix:** checking reserve/PUT/complete alone is a partial patch. **Minimum fix:** verify each required link and return durable partial failure; persist identifiers to retry linking idempotently rather than reserve/upload anew; optional assets must have separate observable outcomes. **Regression:** fail each boundary, restart/retry, exactly one active linked object and truthful status. **Outcome:** unfixed.

### [SEV-P0] F12 — One user's private rejection excerpts are sent in another user's AI prompt

**Location:** `dispatch/ai_clips/preference_learner.py:10–39`; `dispatch/db.py:1189–1194`; `dispatch/ai_clips/worker.py:689–690`. **Category:** multi-user privacy.

**Failure scenario:** A rejects a private clip; row stores title/hook/reason. B's next analysis calls global `get_negative_feedback_prompt()` and includes A's excerpt/title/reason in the Gemini request, possibly using B's API key. Preferences also contaminate another user's editorial decisions.

**Verification:** probe 11 inserted a private fixture excerpt and real prompt construction contained it without any user parameter. Source traces prompt into B's extraction call; no live Gemini request made.

**Root cause (5 Whys):** private context crosses tenant → global prompt fetch → unscoped rejection table/query → identity not propagated → preference learning remained single-user inside multi-user pipeline.

**Patch-vs-fix:** securing clip API reads doesn't protect outbound model context. **Minimum fix:** owner on rejection rows and required owner predicate through preference retrieval/prompt call; do not reuse unowned historical examples across users. **Regression:** A/B isolated prompts, ownerless legacy examples excluded unless adopted. **Outcome:** unfixed.

### [SEV-P1] F13 — Native API URL helper downgrades configured HTTPS to HTTP

**Location:** `frontend/src/bridge.ts:640–653`. **Category:** transport/reliability/security.  
**Evidence:** removes `https?://` then always returns ``http://${cleanHost}``.

**Failure scenario:** operator enables documented TLS and sets `https://PC:8000`. Native frontend sends requests to `http://PC:8000`, failing against TLS-only listener; if a plaintext endpoint is available, requests/headers use unencrypted transport. This report does not claim a token was captured.

**Verification:** Vite SSR execution with native-style window and HTTPS host produced `http://192.168.1.10:8000`. Raw result retained.

**Root cause (5 Whys):** TLS unusable/downgraded → scheme forcibly rebuilt → host treated as address-only string → legacy HTTP assumption → configuration lacks preserved validated URL semantics.

**Patch-vs-fix:** adding server TLS alone cannot repair a downgrading client. **Minimum fix:** parse URL, preserve explicit supported scheme, validate origin; reject insecure remote credential paths or clearly constrain legacy HTTP use. **Regression:** HTTP/HTTPS, trailing slash, hostname, IPv6 and port tests; native TLS integration. **Outcome:** unfixed.

### [SEV-P1] F14 — Reconciliation finalization reads pre-commit job state

**Location:** `dispatch/web/app.py:1438–1459, 1510–1531`; `dispatch/publisher/outbox.py:467–481`. **Category:** transactions/state consistency.

**Failure scenario:** sole clip job is reconciliation_required/failed. Manual/auto reconciliation updates it to published inside an uncommitted connection, then calls `check_and_finalize_clip`, which opens another connection and sees the old job status. Outer transaction commits published job, but clip stays approved forever; ordinary processing no longer visits that job.

**Verification:** probe 09 on real SQLite: manual published response, outbox published, clip still approved. No remote publication was performed or claimed.

**Root cause (5 Whys):** clip not finalized → aggregator sees stale job → second connection before commit → transaction boundary hidden by helper → related durable state transitions lack one consistency context.

**Patch-vs-fix:** helper reuse without transaction context is a symptom abstraction. **Minimum fix:** commit job first then idempotent aggregate with startup reconciliation, or pass shared transaction and update atomically. **Regression:** last-job manual and auto resolution yield matching clip status after restart; multiple-platform partial completion does not publish early. **Outcome:** unfixed.

### [SEV-P1] F15 — YouTube processing jobs poll forever without deadline/backoff

**Location:** `dispatch/publisher/outbox.py:170–228, 486–526`. **Category:** quota/recovery.

**Failure scenario:** provider returns no item, `uploaded` forever, deleted ID, missing credentials or repeated status-query errors. Job remains `processing`; worker scans all such jobs every 10 s, querying one video per job. Errors refresh updated_at without an independent terminal deadline. One stuck job generates up to **8,640 status requests/day** while credentials work; multiple jobs share a shrinking quota pool.

**Verification:** verified source scenario; request count is interval arithmetic, not a 24-hour measured run. There is no elapsed/attempt bound in this branch. Ingestion has a different stall policy, which does not fix publishing.

**Root cause (5 Whys):** endless pending/quota use → every pass revisits job → only selected terminal provider states exit → no attempt/deadline/retry-after → remote acceptance and remote processing lifecycle not fully modeled.

**Patch-vs-fix:** checking processing once after upload is a partial patch, not bounded convergence. **Minimum fix:** next_check_at/backoff and a separate first_processing_at/deadline; batch IDs where appropriate; expose stalled/auth/deleted/inconclusive state with manual retry. Do not re-upload automatically on an ambiguous remote result. **Regression:** empty/perpetual-processing/403/429/revoked credentials reach honest bounded state and obey next retry. **Outcome:** unfixed.

### [SEV-P2] F16 — Passing frontend build conceals type/API contract failures

**Location:** `frontend/package.json:9`; diagnostics in `omega-2026-10-28/frontend-types.txt`; `frontend/src/screens/RecordingsScreen.tsx:774–779`. **Category:** test/build contract.

**Failure scenario:** `npm run build` reports success while `tsc --noEmit` fails with nine diagnostics: impossible `slide` animation comparisons, missing `ClipItem.approved_at`, nullability errors, and nonexistent `Bridge.getAuthToken`. Deletion branch checks an undefined wrapper method, so silently omits auth headers instead of using the existing auth-header helper. Actual delete denial also depends on other ownership/request conditions; not every delete is claimed broken.

**Verification:** Vite build succeeded, tsc failed; SSR bridge probe confirms wrapper member undefined. **Root cause:** transpile-only production command omits type contract verification. **Minimum fix:** correct each mismatch and gate build/check on typecheck, use actual auth helper. **Regression:** tsc clean; deletion request carries intended session/device credential. **Outcome:** unfixed. Type errors alone do not prove every implicated runtime feature is broken.

### [SEV-P2] F17 — Workstation shortcut dialog lacks accessible dialog/name semantics

**Location:** `frontend/src/studio/KeyboardShortcutsModal.tsx:32–51`. **Category:** accessibility.

**Failure scenario:** opening the shortcuts overlay produces ordinary divs, no dialog role/aria-modal/title association, and an icon-only close button with no accessible label. The component has no focus-initialization/restoration or containment behavior. A screen reader cannot derive a meaningful name for the close button from the X SVG. This is an actual markup barrier, not a full WCAG audit verdict.

**Verification:** source inspection only; no screen-reader/browser keyboard session. Applicable concerns include WCAG 2.2 4.1.2 Name/Role/Value and dialog focus expectations; full conformance cannot be concluded. **Minimum fix:** accessible dialog primitive or native dialog with label, named close control, initial/return focus, Escape and keyboard operation; honor reduced motion as applicable. **Regression:** browser accessibility-tree + manual keyboard/screen-reader check including background focus. **Outcome:** unfixed. No redesign requested.

## 3. End-to-end journeys and requirements-to-test matrix

**No complete real recording-to-publishing journey passed in this environment.** Files under `tests/e2e/` use fixtures/mocks and are not substitutes for device/service E2E. No critical scenario has three consecutive real-device successful runs here.

| Critical journey / requirement | What actually ran or was traced | Outcome | Minimum acceptance evidence still needed |
|---|---|---|---|
| Fresh install → usable setup/permissions | Manifest/build config/AuthGate source; pure camera tests in suite | F05; real install **UNTESTED** | Signed release, clean state, denial/regrant, real account identity, actionable setup |
| OAuth → correct raw/publish channel | Worker tests, vault/credentials contracts; selected callback source | Unit/handler passes; live channel selection **UNTESTED** | Real two accounts/channels, expiry/revocation/reconnect, no wrong-channel fallback, provider consent disclosure |
| Start → real capture → pause/resume/stop | Native/bridge source; frontend scripted tests | No physical media produced | Hardware state backs indicator, audio mode clear, rapid repeated taps, stop/finalize failure |
| Screen locked/background → 30–60 min rolling capture | Service/segment code inspection only | **UNTESTED**, cannot release-gate pass | Reference POCO C65 or confirmed substitute plus second device; three repeated workflows and long run |
| Segment finalize → resumable upload | Policies/tests/worker source | Pure upload-policy checks do not verify network behavior | 308/no Range, Wi-Fi transition, lost final response, expired session, revoked token, no duplicate remote upload |
| PC discovery → validated download | Poller/catcher source and suite | Environment/media tests fail without FFmpeg | Real unlisted video, actual bytes/decode/audio/duration; policy resolution first |
| Transcript → highlights → render/subtitles | Deterministic validation tests, mock fixtures | 28 selected tests pass; F08/F09/F12 | Representative real speech/silence/noise/Hinglish, model-call diagnostics, decoded clip and timed subtitle review |
| Review → edit/save → refresh/restart | DB probe and UI handler tracing | **FAIL** stale save, F07; source failure F06 | Two tabs, caption/range conflicts, unsaved navigation, persistence after PC/phone restart |
| Approve → render approved asset → publish | Source; mocked outbox tests/probes | F02/F06/F09/F14/F15; live publication **UNTESTED** | Exact approved hash/version, intended channel/visibility, processed playable remote result |
| Offline cloud review/reject/restore | Existing handler/unit tests; sync fault probes | F03/F10/F11; deployed recovery **UNTESTED** | Two-user deployed RLS, partial PUT/complete/link, expired media URLs, 24h purge/undo, PC restart |
| Crash/reboot/network/disk-full convergence | Isolated persisted ANALYZING simulation, source state reconstruction | **FAIL** F08; device/Windows tests missing | Injection at every finalization/upload/link/publish commit boundary; no orphan loss or duplicate publication |

Missing product decisions, rather than invented bugs: explicit supported Android versions/reference second device; whether anonymous/offline capture is supported; account ownership/adoption of old recordings; supported path for session-spanning edits; supported desktop/browser/Windows configurations; retention and disclosure rules; expected handling when no good AI highlight exists; acceptable quota/cost envelope per user. The existing app needs coherent decisions here, not a new feature suite.

## 4. UI/UX and accessibility supplemental assessment

Concrete failures are F05/F06/F07/F09/F10/F13/F14/F17. Users can see approved when approval failed; an old cloud preview with new trim metadata; a fake account; or a permanently analyzing/publishing operation without recovery. These are release concerns, not cosmetic polish.

For review/captions there is real range-fencing and some unsaved-edit UI; it would be false to say no unsaved-change protection exists. Those controls have not been validated across all tab changes, rerenders and cloud/local mode transitions. Likewise, native camera events and resource error paths exist; a timer alone was not assumed to be the only recording indicator.

**Not executed:** supported browser/window matrix, 200%/400% zoom, Windows scaling, long titles/lists, seek reliability with large videos, focus order/restoration, color contrast calculation, TalkBack, screen readers, switch access, text scaling, touch-target measurement, axe scan or Core Web Vitals. A source modal defect is not proof of full accessibility nonconformance across all screens. These remain required practical checks rather than fabricated findings.

Minimum meaningful screen-state coverage still needed: loading/empty/offline/auth-expired, render failed versus no preview, partial multi-platform publication, stale edits, canceled/aborted actions, cloud pending versus local ready, missing media and permanent provider failures. Show the next actionable step; do not call a state “complete” merely because a request was launched.

## 5. Performance report

Only test/build wall-clock measurements were collected, not product latency benchmarks:

- Vite production build: one observed **14.28 s**; JS bundle **908.29 kB raw / 231.56 kB gzip**, CSS **70.90 kB / 12.22 kB gzip**. Vite emitted large-chunk and static/dynamic Supabase import warnings. Bundle size is a diagnostic risk, not proof of an unacceptable LCP/INP or an automatic blocker.
- Full Python test suite: one observed **13.93 s**, heavily mocked and many early missing-binary failures. Cannot infer actual pipeline throughput.
- Worker test runner: **348 ms**; selected Python tests: **1.22 s**. Neither is request/render latency.
- F15 establishes a concrete wasteful request pattern. One 10-second polling job consumes 8,640 status calls/day; this is calculated, not measured production telemetry.

**No median/p95/p99 claimed** from a single run. No startup-to-usable, Android memory/battery/thermal/I/O, sustained camera stability, preview seek, render speed, resource release or Windows sleep/resume measurement exists from this audit. No optimization patch or before/after improvement was made. Proposed diagnostic targets are LCP ≤2.5 s, INP <200 ms, CLS ≤0.1, plus no steadily growing memory and bounded retry/request counts. Actual hardware-specific budgets must be agreed and measured with consistent inputs. Do not add resource-aware concurrency or caching until profiles demonstrate a need.

## 6. Media quality report

**No actual recorded/final video or published asset was independently decoded or played in this environment.** FFmpeg/ffprobe absence prevents claiming recording integrity, audio presence/loudness/drift, rotation/VFR correctness, subtitle readability, safe areas, karaoke synchronization, or final platform compatibility. Existing tests that fail because the tools are missing are limitations, not proof of those specific media defects.

F09 is a verified source/time contract defect even without codec execution. The minimal regression fixture should use two clearly different 600 s labeled/audio-marked segments, a clip wholly in segment two and a join-spanning clip, then compare preview, approved render and publication. Include gap/missing-segment cases; never concatenate existing segments while silently collapsing an absent segment's timeline.

For the release gate retain small reproducible media fixtures covering truncated/zero-byte MP4, absent audio, portrait/rotation, timestamps not starting at zero, spaces/Unicode/apostrophe filenames, short tails and finalization after stop timeout. Validate full decode to EOF and source correspondence, not just file existence or API success. Current final render probe/duration checks are useful but not proof of human-readable captions or complete media integrity.

## 7. AI evaluation report

**Executed evaluation:** deterministic existing editorial tests in `test_ai_highlight_editorial_quality.py` and related selected modules passed; full suite includes other schema/timing checks. **No live model output or human-rated reference recording set was evaluated.** The repository's mocks and test names cannot establish real highlight quality or statistical reliability.

**Practical proposed reference set (NOT yet collected/reviewed):** eight consented short recordings/transcripts: ordinary English; ordinary Hindi-English code switching; slow speech; fast speech; silence/no useful content; background noise/imperfect transcription; contextual dependency requiring setup; multiple speakers only if supported. Do not call synthetic excerpts human-reviewed ground truth.

**Rubric to apply before model review:**

1. Source faithfulness and no invented claims (hard gate).
2. Valid source identity, finite ordered bounds, no missing-media span (hard gate).
3. Hook/context/completeness: 0 absent, 1 weak, 2 usable, 3 strong.
4. Timestamp boundary precision and no clipped sentence: same 0–3 scale with human-marked desired boundaries.
5. Word transcript accuracy and measured subtitle offset; reject unusable timing rather than synthesizing fake word timings.
6. Standalone watchability, typography/safe-area readability and correct approved render: human playback.
7. No-highlight outcome acceptable on silence/low-value input; do not force eight clips.

Track prompt/model/version/source hash, candidate validation reasons, retries and API usage without logging secrets. Tiny-set results are an alpha smoke gate, not a reliability percentage. F08/F09/F12 already invalidate aspects of recovery/source isolation/privacy independently of editorial taste. Nondeterministic reanalysis of growing session transcripts, cross-segment duplicates, prompt injection in transcript/rejection text, large/truncated responses and provider version changes need additional controlled tests. Text is not shown being executed as shell code; do not invent an execution vulnerability from prompt injection alone.

## 8. Integration, quota, privacy and compliance

### R01 — UNRESOLVED — POLICY REVIEW REQUIRED (alpha blocker)

Official pages were actually retrieved on 2026-10-28 using the page-fetch tool:

1. [YouTube Required Minimum Functionality — Uploading videos](https://developers.google.com/youtube/terms/required-minimum-functionality#uploading-videos), fetched chunks 0, 2 and 3. It says upload clients must let users set title and description, and: **“Users must be able to choose whether the uploaded video will be public, private, or unlisted.”**
2. [Developer Policies, III.E.1 Audiovisual Content](https://developers.google.com/youtube/terms/developer-policies#e.-handling-youtube-data-and-content), fetched chunk 6: clients must not **“download, import, backup, cache, or store copies of YouTube audiovisual content without YouTube's prior written approval”**; also restricts offline playback.
3. [Developer Policies, III.C](https://developers.google.com/youtube/terms/developer-policies#c.-implementing-youtube-features), fetched chunk 3: descriptions may not gain appended text after submission without prior consent; users must be told how provided data is used. Relevant to hidden dispatch/idempotency markers and appended `#Shorts`, not just raw upload visibility.
4. [Current quota calculator](https://developers.google.com/youtube/data/determine_quota_cost), both chunks fetched; page states last updated 2026-10-15.

**Implementation:** Android `YouTubeDirectUploadWorker.kt:392–413` and Worker raw session code `cloud-worker/src/index.ts:670–699` generate titles/descriptions and fix raw visibility to unlisted. PC catcher uses yt-dlp to download the audiovisual content as the fundamental phone→PC handoff. Publishing adapter adds `#Shorts` and outbox adds idempotency text. These are not made compliant merely by ownership, unlisted status, OAuth consent, alpha distribution or successful API calls.

**Exact questions requiring decision:** Does YouTube grant Dispatch written approval for automated downloads of these uploads? Does a fixed-unlisted intermediary have an applicable documented exception to user privacy/title/description choice? What explicit consent is required for automatic metadata additions? Is the published privacy policy/Terms/revocation/deletion flow adequate for actual cloud/AI storage and authorized data? No exemption or written approval was supplied or found in the inspected evidence.

**Minimum remediation:** obtain qualified/platform policy resolution and implement applicable choices/consent/disclosures. If written authorization for the core download handoff is unavailable, the “YouTube only transport” architecture itself may be unusable. The smallest justified architecture correction would then be replacing that handoff—not rewriting transcription/editor/publishing. This is a conditional product decision, not permission to add LAN sync during an audit. No legal-compliance certification is made.

### Quota budget: current official contract, not remembered historical costs

The fetched current calculator states **separate defaults of 100 `search.list` calls/day and 100 `videos.insert` calls/day**, at cost 1 in those buckets, and **10,000 units/day for other endpoints**; `videos.list`, channels.list and playlistItems.list cost 1. Reset midnight Pacific Time. **Do not apply the older 1,600-unit upload assumption to this report.** Actual deployed project quotas may differ; cloud-console evidence is needed before release.

Let U=10 users, S=sessions/user/day, N=segments/session, C=published clips/session, A=average raw upload attempts and L=lookup calls/raw attempt. With the present pre-upload search:

- Raw uploads: U×S×N×A `videos.insert` initiations (successful uploads plus restart/ambiguous attempts as applicable).
- Publish uploads: at least U×S×C, plus any publish retries/new initiations.
- Raw lookup search: at least U×S×N×A×L, where L≥1 on normal paths; final ambiguity/lookup/reconciliation adds more searches.
- Publishing reconciliation: extra search calls, not free idempotency.
- Publishing processing: sum of per-job polls, currently roughly 6/minute/job until terminal. Ingestion polling adds playlist/channel/video requests and paging. Tokens/PUT chunks use different request boundaries; do not count each byte chunk as a new videos.insert without provider evidence.

**Explicit illustrative workload, not claimed usage:** 10 users × one 60-minute session/day × six 10-minute segments = **60 raw inserts + ≥60 searches/day**. At four published clips/user/session, **40 publish inserts**, totaling the default **100 insert/day** with **zero retry headroom**. Two such sessions per user exceed both raw search and insert defaults before publishing. One lost final-response or expired upload session can add lookup/initiation work. One forever-processing job can consume 8,640 of the other-endpoint units/day alone (F15). At six new jobs processing for an hour, that branch makes roughly 2,160 queries, excluding uploads, discovery and other users.

The workload assumptions, number of Google projects, actual quota allocations, daily recording durations and expected clip counts are **missing launch inputs**. Users are not separate quota pools when using one API project. Minimum fix is bounded/adaptive status polling, explicit quota/auth/permanent-error UI, request accounting and a workload budget with retry headroom; not a complicated million-user scheduler.

### Data leaving the PC / disclosures

Source inspection shows YouTube receives raw video/audio and generated session/hash metadata; Gemini receives transcript/prompt context, and the precision pipeline uploads bounded audio slices; Supabase receives identities, clip metadata, captions, commands and encrypted credential records; R2 receives clip/edit-window/thumbnail assets. Social adapters receive chosen clip content and metadata; PC holds service-role/vault secrets. F12 additionally leaks another tenant's editorial examples into model context. Local retention and remote retention are different operations; disconnecting an account is not automatically deletion of all remotely stored content.

Not verified: actual deployed privacy/Terms links and content, data deletion deadlines/consent, Google app verification/testing-mode expiry, actual scopes versus each feature, channel selection, brand-account switching, external platform app review permissions, production token rotation, cloud secret configuration, RLS enforcement after all migrations, deployed backup/restore. These are material review tasks, not certified violations without evidence.

### Integration contract coverage

Worker tests cover controlled request validation/ownership/quota/OAuth and pass; selected PC credential-removal/vault/remote-job tests pass. Live YouTube, Gemini, R2, Supabase and all social adapter contracts were **not** exercised. Pagination changes, provider processing deadlines, error payloads/Retry-After, malformed 200 bodies, missing fields, session expiry and ambiguous remote completion still need controlled contract fixtures through the actual adapter. LinkedIn uses a fixed `Linkedin-Version: 202408`, only first upload instruction and no explicit finalizeUpload call in inspected adapter; this is **R06 unverified provider-contract risk**, not declared proven rejection without current official contract validation.

## 9. Remaining material risks and missing evidence

| ID | Classification / issue | What resolves it / smallest action |
|---|---|---|
| R01 | High-risk policy uncertainty | Written download authorization/qualified policy decision and compliant upload choice/consent, or change only the transport boundary |
| R02 | Android lifecycle/long-session reliability unverified | Two supported physical devices; model/Android/SDK/camera/storage/battery details; real lock/background/permission/process-death/30–60-minute media tests |
| R03 | No release-build E2E proof | Clean signed installed release and supported PC environment, three consecutive successes per critical scenario |
| R04 | Duplicate daemon/Windows lifecycle risk | `main.py` lock does check/read/write, not atomic OS lock; race test two startup processes, Windows logoff/sleep/kill/subprocess cleanup; smallest fix only after demonstrating interleaving |
| R05 | Historical secrets/rotation unresolved | Checkout has one commit; full authorized history and provider-side key invalidation evidence. Never publish any discovered token in a report |
| R06 | Additional social publisher contracts unverified | Current official provider requirements and controlled multipart/finalization/processing/error fixtures; disable unsupported existing targets if necessary rather than pretend success |
| R07 | Font import resource/auth boundary | `app.py:3157–3173` has no auth guard and reads entire upload before upper-size bound; establish server/proxy body limits and demonstrate bounded reject. Add authentication/size limit at boundary, not a new font subsystem |
| R08 | WebView attack surface | Selected MainActivity code has no navigation allowlist override and grants requested web permissions; test hostile navigation before declaring JS bridge exploit. Restrict trusted origin/navigation and permission origin checks if exposed |
| R09 | Repeat/reanalysis/AI cost and source-session stability | Growing session transcript/repeated workers, approved-result preservation, daily quota contention and no-highlight handling with controlled provider failures |
| R10 | Disk/DB/filesystem consistency and retention | Disk-full, migration interruption, file lock, Unicode/long/apostrophe paths, unlink-before-commit rejection crash, missing/orphan files; prove restart converges, preserve irreplaceable source |
| R11 | Cloud acceptance ≠ final workflow success | RemoteJobPoller labels ingest acceptance `done`; verify frontend label means accepted rather than fully rendered; do not classify fake completion without tracing that observer |
| R12 | Suite health beyond missing FFmpeg | Re-run with full documented dependencies/media tools; isolate all remaining failures, admin/auth/cloud command mocks and test isolation; don't waive failures as “only environment” |

Neither a circuit breaker for every call nor a formal error-budget service is automatically necessary for ten users. Concrete bounded operations, honest error states, recovery and request budgeting are necessary. Optional improvements include code splitting after real browser measurements, broader SLO telemetry, and architectural consolidation only when a demonstrated failure warrants it.

## 10. Alpha acceptance gate

| Gate | Verdict / evidence |
|---|---|
| No known P0/unresolved critical P1 | **FAIL** — F01–F15 contain confirmed blockers |
| No high-risk YouTube policy assumption | **FAIL / UNRESOLVED** — R01 |
| Two-device repeated recording-to-publishing (3 consecutive per scenario) | **NOT EXECUTED** |
| Screen-off 30–60-minute supported recording | **NOT EXECUTED** |
| Recovery from process/network/storage/provider failures | **FAIL** persisted ANALYZING probe; broad recovery not executed |
| Final media/subtitle integrity and remote publication | **NOT EXECUTED** |
| Saved edits and decisions persist correctly | **FAIL** F07, F14; other restart cases untested |
| No runaway retry/polling or silent contradictory UI | **FAIL** F06/F10/F11/F15 |
| Clean release build/install in documented environment | **NOT EXECUTED**; Vite build only, typecheck fails |
| Explicit limitations accepted by owner | **NOT RECORDED**; an audit cannot accept risks on the user's behalf |

**Release verdict: NOT READY.** This is not a judgment based simply on test failures or missing hardware. Confirmed incorrect behavior already prevents approval. Missing physical/live evidence independently prevents a READY verdict even after local defects are repaired.

## 11. Ranked remediation plan and likely first-30-day failures

### Root-cause order (required, not optional)

1. **Resolve R01 before investing in transport tuning.** The intended download architecture needs an explicit policy basis. No silent exemption.
2. **Repair identity/authorization/privacy boundary together:** F01/F02/F03/F04/F05/F12. Remove pretend auth and guessed ownership; scope reads, mutations and outbound AI context; fail closed on vault config. These are distinct boundaries, not one token-check patch.
3. **Make media identity consistent:** F09/F10. Persist/resolve segment manifests and coordinate/version identity through preview, trim, captions, cloud and final publication.
4. **Enforce edit and operation acknowledgment:** F06/F07/F11. Separate pending from completed, compare-and-swap whole edits and check required durable cloud links.
5. **Repair active-path convergence:** F08/F14/F15. Recover abandoned inbox claims, use one transaction context for related status and bound provider processing polls.
6. **Repair actual transport/build/accessibility contracts:** F13/F16/F17. Preserve TLS URL, make typecheck a gate, fix accessible dialog/name behavior.
7. **Run full clean suite then reference-device/release acceptance:** R02/R03/R06/R09/R10/R12. Retain tiny deterministic regression media and controlled provider failures; do not start with broad rewrites or performance tuning.

### Ten most likely to bite within 30 days (scenario-based, not measured probabilities)

1. **F06** approval fails remotely but remains approved locally.
2. **F08** closing/restarting PC during Gemini strands recording.
3. **F07** stale tab/phone caption save erases newer edit.
4. **F09** second-segment or join-spanning clip fails approval or uses wrong source/time.
5. **F10** re-rendered clip on phone still plays old cloud asset.
6. **F15** stalled YouTube processing burns status quota and never exits.
7. **F13** enabling TLS breaks native API requests through scheme rewrite.
8. **F05** clean native install uses fake owner and cloud integration fails.
9. **F03** inviting another alpha user assigns old ownerless clips to the wrong profile.
10. **F11** transient cloud link failure leaves charged/stored assets but claims sync success.

F01/F02/F04/F12 are not relegated by this frequency ranking: confidentiality/authorization failures remain top-priority even if ordinary happy-path users do not trigger them.

## 12. Deduplication and historical verdict

`FINAL_INTEGRATION_AUDIT.md` and `DISPATCH_REMEDIATION_LEDGER.md` assert prior wiring/security/reliability fixes and readiness; this audit does not repeat those old tickets as newly found merely because they once existed. It verifies that outbox is wired in main, transcription backlog drain exists, capture row precedes writing, backend approval validates final render, token-transfer endpoints were removed and selected regression tests pass. These are meaningful existing protections.

Where earlier themes survive, findings here specify the remaining root cause: auth guards still trust LAN and omit job ownership (F01/F02); legacy recovery does not cover active inbox state (F08); partial media coordinate fixes miss final approval (F09); range locks don't protect caption edit versions (F07); checking some HTTP steps doesn't prove cloud completion (F11). Cross-reference ledger TASK-1.1, TASK-2.1, TASK-3.1 and its idempotent outbox assertions rather than creating duplicate historical tickets. A separate “patch ticket” is not added without concrete independent behavior; partial protections are identified in each finding's patch-vs-fix section.

The checkout's one reachable commit prevents attributing these problems to a particular historical patch or confirming the earlier audit's reproduction environment. Prior test counts and “all P0/P1 resolved” claims are not evidence for the current commit. Current tests/builds also cannot prove absence of bugs.

**Would a fresh full execution find zero P0/P1 after only these listed fixes? No such assurance is justified.** Unreviewed control paths, native timing/lifecycle, multi-store crash boundaries, social API contracts, secret history and deployed two-user authorization can still yield serious findings. A fresh independent audit plus actual three-run/device/media/recovery acceptance is required. The report's evidentiary strength is the failures it demonstrates and the limitations it states—not a claim to exhaustive correctness.
