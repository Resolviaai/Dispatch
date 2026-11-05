# Project: CapCut-Style Mobile Timeline & EDL Playback

## Architecture
- **Captions & Timeline Module (`frontend/src/captions/`)**:
  - `CaptionTimeline.tsx`: Main video filmstrip track, segment piece rendering, 2px cut seams, piece selection state, dual 44px trim handles, layer tracks.
  - `CaptionStudio.tsx`: Edge-to-edge docked contextual bottom thumb bar, tool categorization (`edit`, `text`, `overlay`), 44px [ Split ] and [ Delete ] buttons, audio button gating.
  - `timelineEngine.ts`: Deterministic mathematical core (`splitAt`, `trimEdge`, `deletePiece`, `concatToSource`, `sourceToConcat`, `normalizeRanges`, `keptDuration`).
- **Screen Controller (`frontend/src/screens/ClipsScreen.tsx`)**:
  - State management for active EDL pieces (`activeSegments`), selection (`selectedPieceIndex`, `isVideoSelected`, `selectedGroupId`).
  - Playback engine: `handleTimeUpdate` with frame-accurate gap skipping via `requestVideoFrameCallback`.
  - Seeking engine: `handleSeek` mapping presentation time to source time via `concatToSource`.
  - Mutation handlers: `handleSplitClip` (using `concatToSource` and `splitAt`), `handleDeletePiece` (using `deletePiece`), `handleTrimPiece` (using `trimEdge`).
- **Master Task Tracker (`docs/2026-11-05_MASTER_TASK_LIST.md`)**:
  - Authoritative status tracking for Stages 1-4 tasks (T01, UI-01, UI-02, P0-A, P0-B, P1-C, etc.).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Visual Piece Blocks & 2px Seams | Main video track renders discrete visual piece blocks separated by 2px `#121418` cut seams | M1 | ORIGINAL_REQUEST §1, Explorer 1 |
| 2 | Piece Selection Frame | Tapping a piece selects it with high-contrast `ring-2 ring-white rounded-md` | M1 | ORIGINAL_REQUEST §1, Explorer 1 |
| 3 | Dual 44px Trim Handles | Selected piece renders dual 44px touch bounding boxes (`w-[44px]`) with white visual bars | M1 | ORIGINAL_REQUEST §1, Explorer 1 |
| 4 | Remove Pieces Chip Strip | Remove redundant `Pieces (N)` auxiliary chip strip from `ClipsScreen.tsx` | M1 | ORIGINAL_REQUEST §1, Explorer 1 |
| 5 | Remove In-Layer Scissors | Remove embedded 16px scissors button from timeline caption/overlay blocks | M1 | ORIGINAL_REQUEST §1, Explorer 1 |
| 6 | Prominent 44px Split & Delete | Contextual thumb bar provides 44px [ Split ] and [ Delete ] buttons at positions 1 & 2 | M1 | ORIGINAL_REQUEST §2, Explorer 2 |
| 7 | Relax Video Split Guard | Relax split guard threshold from 1.0s to 0.1s in `CaptionStudio.tsx` | M1 | ORIGINAL_REQUEST §2, Explorer 2 |
| 8 | Multi-Piece Split Mapping | `handleSplitClip` maps presentation time to source time via `concatToSource` | M1 | ORIGINAL_REQUEST §2, Explorer 2 |
| 9 | Ripple Delete Integration | Wire [ Delete ] in video edit mode to `handleDeletePiece` (disabled if piecesCount <= 1) | M1 | ORIGINAL_REQUEST §2, Explorer 2 |
| 10 | Split/Delete Context Routing | Split/Delete acts strictly on active layer (selected piece vs caption group) | M1 | ORIGINAL_REQUEST §2, Explorer 2 |
| 11 | Hide Unsupported Audio Buttons | Gate non-functional Import Audio and Voiceover buttons behind feature flag | M1 | ORIGINAL_REQUEST §2, Explorer 2 |
| 12 | EDL Playback Gap Skipping | `handleTimeUpdate` detects segment ends (`cur >= piece.source_end`) and jumps to next piece `source_start` | M1 | ORIGINAL_REQUEST §3, Explorer 3 |
| 13 | Concat-to-Source Seeking | `handleSeek` maps presentation time to source time via `concatToSource()` | M1 | ORIGINAL_REQUEST §3, Explorer 3 |
| 14 | Master Task List Verification | Update `docs/2026-11-05_MASTER_TASK_LIST.md` marking completed tasks with evidence | M1 | ORIGINAL_REQUEST §4, Explorer 3 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | CapCut Mobile Timeline & EDL Playback | UI-01 (Piece rendering & trim handles), UI-02 (Contextual toolbar & split/delete), T01 (Gap skipping & seeking), Master Task List update | Phase 0 Survey Complete | DONE (13/13 tests pass, build code 0, 2 Reviewers APPROVE, 2 Challengers APPROVE, Auditor CLEAN) |

## Interface Contracts
### `CaptionTimeline.tsx` ↔ `ClipsScreen.tsx`
- **Props Added to `CaptionTimelineProps`:**
  - `segments?: TimelineSegmentPiece[]` where `TimelineSegmentPiece = { id: string; start: number; end: number; index?: number }`
  - `selectedPieceIndex?: number | null`
  - `onSelectPiece?: (pieceIndex: number) => void`
  - `onTrimPiece?: (pieceIndex: number, side: 'start' | 'end', newSourceTime: number) => Promise<boolean | void> | boolean | void`
- **Behavior:**
  - When `segments` is empty or undefined, defaults to single piece `[{ id: 'seg_1', start: clipStart, end: clipEnd, index: 0 }]`.
  - Tapping piece `i` calls `onSelectPiece(i)` and `onSelectVideo?.()`.
  - Dragging left handle calls `onTrimPiece(i, 'start', newSourceTime)`.
  - Dragging right handle calls `onTrimPiece(i, 'end', newSourceTime)`.

### `CaptionStudio.tsx` ↔ `ClipsScreen.tsx`
- **Props Added to `CaptionStudioProps`:**
  - `onDeletePiece?: () => void`
  - `canDeletePiece?: boolean`
  - `piecesCount?: number`
  - `selectedPieceIndex?: number | null`
- **Comparator `areCaptionStudioPropsEqual`:**
  - Must compare `canDeletePiece`, `piecesCount`, `selectedPieceIndex`.
- **Toolbar Layout:**
  - In `edit` category: `[ Back ]`, `[ Split ]`, `[ Delete ]`, `[ Framing ]`, `[ Volume ]`, `[ Reset Crop ]`.
  - In `text` category: `[ Back ]`, `[ Split ]`, `[ Delete ]`, `[ Transcript ]`, `[ Add Text ]`, `[ Style ]`, `[ Presets ]`, `[ Position ]`, ...

### `timelineEngine.ts` ↔ `ClipsScreen.tsx`
- `concatToSource(ranges: [number, number][], concatTime: number, sourceDuration: number): number`
- `sourceToConcat(ranges: [number, number][], sourceTime: number, sourceDuration: number): number | null`
- `splitAt(ranges: [number, number][], sourceTime: number, sourceDuration: number): [number, number][]`
- `trimEdge(ranges: [number, number][], index: number, edge: 'start' | 'end', newSourceTime: number, sourceDuration: number): [number, number][]`
- `deletePiece(ranges: [number, number][], index: number, sourceDuration: number): [number, number][]`

## Code Layout
- `frontend/src/captions/CaptionTimeline.tsx` — Owned by Worker (UI-01 changes)
- `frontend/src/captions/CaptionStudio.tsx` — Owned by Worker (UI-02 changes)
- `frontend/src/screens/ClipsScreen.tsx` — Owned by Worker (T01 playback & seeking, UI-01 & UI-02 wiring)
- `frontend/test_edl_playback_gap_skip.ts` — Owned by Worker (T01 unit test suite)
- `frontend/run_tests.js` — Owned by Worker (registration of new test suite)
- `docs/2026-11-05_MASTER_TASK_LIST.md` — Owned by Worker (task status update)
