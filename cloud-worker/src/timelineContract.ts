/**
 * Cloud Worker timeline contract — import-shared with the canonical frontend
 * mapper (OQ7, 2026-11-14). The Worker no longer keeps its own copy; drift
 * between runtimes is structurally impossible for this contract.
 *
 * Canonical implementation:
 *   - frontend/src/lib/timelineMapper.ts (single source of truth)
 * Independent mirrors (kept in sync by contract tests):
 *   - dispatch/segments.py & dispatch/lib/timeline_mapper.py
 *   - android/.../timeline/TimelineMapper.kt
 */

export * from '../../frontend/src/lib/timelineMapper.ts';
