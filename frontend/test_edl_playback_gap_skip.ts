/**
 * Test Suite: Multi-Piece EDL Playback Gap Skipping & Time Mapping (T01)
 *
 * Verifies:
 * 1. Concat-to-source presentation time mapping across multi-piece EDL with gaps.
 * 2. Source-to-concat output presentation time mapping and gap rejection (null inside deleted gaps).
 * 3. EDL playback gap skipping detection logic (jumping across deleted gaps to next piece start).
 * 4. Boundary conditions: clip start underflow, clip EOF pause/reset, seam preference.
 * 5. Presentation-time splitting (concatToSource -> splitAt) preserving exact kept duration.
 * 6. Ripple piece deletion (deletePiece) and edge trimming (trimEdge).
 * 7. Out-of-bounds error handling and guards.
 */

import assert from 'node:assert/strict';
import {
  concatToSource,
  sourceToConcat,
  splitAt,
  deletePiece,
  trimEdge,
  normalizeRanges,
  keptDuration,
  roundMs,
  TimelineError,
} from './src/captions/timelineEngine';

console.log('🧪 Starting Dispatch EDL Playback Gap Skipping & Time Mapping Test Suite (T01)...');

// ---------------------------------------------------------------------------
// TEST 1: Single Piece Baseline (No cuts)
// ---------------------------------------------------------------------------
console.log('\nTest 1: Single Piece Baseline (No Cuts)...');
{
  const singleRange: [number, number][] = [[10.0, 40.0]];
  const sourceDur = 60.0;
  const total = keptDuration(singleRange);
  assert.equal(total, 30.0, 'Kept duration should be 30.0s');

  // Presentation time to source time
  assert.equal(concatToSource(singleRange, 0.0, sourceDur), 10.0, 'Concat 0.0s -> Source 10.0s');
  assert.equal(concatToSource(singleRange, 15.0, sourceDur), 25.0, 'Concat 15.0s -> Source 25.0s');
  assert.equal(concatToSource(singleRange, 30.0, sourceDur), 40.0, 'Concat 30.0s -> Source 40.0s');

  // Source time to presentation time
  assert.equal(sourceToConcat(singleRange, 10.0, sourceDur), 0.0, 'Source 10.0s -> Concat 0.0s');
  assert.equal(sourceToConcat(singleRange, 25.0, sourceDur), 15.0, 'Source 25.0s -> Concat 15.0s');
  assert.equal(sourceToConcat(singleRange, 40.0, sourceDur), 30.0, 'Source 40.0s -> Concat 30.0s');

  // Outside bounds returns null
  assert.equal(sourceToConcat(singleRange, 5.0, sourceDur), null, 'Source 5.0s before start -> null');
  assert.equal(sourceToConcat(singleRange, 45.0, sourceDur), null, 'Source 45.0s after end -> null');

  console.log('  ✓ Single piece timeline maps 1:1 between presentation and source clocks');
}

// ---------------------------------------------------------------------------
// TEST 2: Multi-Piece EDL with Gaps (Golden Vector)
// Given ranges: [[10.0, 15.0], [20.0, 30.0]] (gap [15.0, 20.0] removed)
// Total kept duration = 5.0s + 10.0s = 15.0s
// ---------------------------------------------------------------------------
console.log('\nTest 2: Multi-Piece EDL with Gaps (Golden Vector)...');
{
  const ranges: [number, number][] = [[10.0, 15.0], [20.0, 30.0]];
  const sourceDur = 60.0;
  const total = keptDuration(ranges);
  assert.equal(total, 15.0, 'Total kept duration must be 15.0s');

  // Concat to Source mappings
  assert.equal(concatToSource(ranges, 0.0, sourceDur), 10.0, 'Concat 0.0s maps to Piece 0 start (10.0s)');
  assert.equal(concatToSource(ranges, 2.5, sourceDur), 12.5, 'Concat 2.5s maps to 12.5s');
  assert.equal(concatToSource(ranges, 4.0, sourceDur), 14.0, 'Concat 4.0s maps to 14.0s');

  // At seam boundary (5.0s presentation time), concatToSource selects next piece start
  assert.equal(concatToSource(ranges, 5.0, sourceDur), 20.0, 'Concat 5.0s skips gap [15, 20] -> Piece 1 start (20.0s)');
  assert.equal(concatToSource(ranges, 7.0, sourceDur), 22.0, 'Concat 7.0s maps to 22.0s');
  assert.equal(concatToSource(ranges, 10.0, sourceDur), 25.0, 'Concat 10.0s maps to 25.0s');
  assert.equal(concatToSource(ranges, 15.0, sourceDur), 30.0, 'Concat 15.0s maps to Piece 1 end (30.0s)');

  // Source to Concat mappings
  assert.equal(sourceToConcat(ranges, 10.0, sourceDur), 0.0, 'Source 10.0s -> Concat 0.0s');
  assert.equal(sourceToConcat(ranges, 12.5, sourceDur), 2.5, 'Source 12.5s -> Concat 2.5s');
  assert.equal(sourceToConcat(ranges, 15.0, sourceDur), 5.0, 'Source 15.0s -> Concat 5.0s');

  // Inside deleted gap: must return null!
  assert.equal(sourceToConcat(ranges, 16.0, sourceDur), null, 'Source 16.0s (inside gap) -> null');
  assert.equal(sourceToConcat(ranges, 17.5, sourceDur), null, 'Source 17.5s (inside gap) -> null');
  assert.equal(sourceToConcat(ranges, 19.9, sourceDur), null, 'Source 19.9s (inside gap) -> null');

  // In second piece
  assert.equal(sourceToConcat(ranges, 20.0, sourceDur), 5.0, 'Source 20.0s -> Concat 5.0s');
  assert.equal(sourceToConcat(ranges, 25.0, sourceDur), 10.0, 'Source 25.0s -> Concat 10.0s');
  assert.equal(sourceToConcat(ranges, 30.0, sourceDur), 15.0, 'Source 30.0s -> Concat 15.0s');

  console.log('  ✓ Multi-piece EDL correctly maps across cuts and rejects deleted gaps');
}

// ---------------------------------------------------------------------------
// TEST 3: Three-Piece EDL with Multiple Deleted Gaps
// Ranges: [[5.0, 10.0], [15.0, 25.0], [35.0, 40.0]] (gaps [10-15], [25-35])
// Kept: 5.0 + 10.0 + 5.0 = 20.0s
// ---------------------------------------------------------------------------
console.log('\nTest 3: Three-Piece EDL with Multiple Deleted Gaps...');
{
  const ranges: [number, number][] = [[5.0, 10.0], [15.0, 25.0], [35.0, 40.0]];
  const sourceDur = 60.0;
  const total = keptDuration(ranges);
  assert.equal(total, 20.0, 'Kept duration must be 20.0s');

  // Piece 0: [5.0, 10.0] -> concat [0, 5]
  assert.equal(concatToSource(ranges, 0, sourceDur), 5.0);
  assert.equal(concatToSource(ranges, 3, sourceDur), 8.0);
  assert.equal(concatToSource(ranges, 5, sourceDur), 15.0, 'Skips first gap [10, 15]');

  // Piece 1: [15.0, 25.0] -> concat [5, 15]
  assert.equal(concatToSource(ranges, 6, sourceDur), 16.0);
  assert.equal(concatToSource(ranges, 10, sourceDur), 20.0);
  assert.equal(concatToSource(ranges, 15, sourceDur), 35.0, 'Skips second gap [25, 35]');

  // Piece 2: [35.0, 40.0] -> concat [15, 20]
  assert.equal(concatToSource(ranges, 17, sourceDur), 37.0);
  assert.equal(concatToSource(ranges, 20, sourceDur), 40.0);

  // Gaps return null
  assert.equal(sourceToConcat(ranges, 12.0, sourceDur), null, 'First gap returns null');
  assert.equal(sourceToConcat(ranges, 30.0, sourceDur), null, 'Second gap returns null');

  console.log('  ✓ Three-piece EDL with multiple gaps traverses all seams and gaps seamlessly');
}

// ---------------------------------------------------------------------------
// TEST 4: Gap-Skipping Playback Engine Simulation (handleTimeUpdate logic)
// ---------------------------------------------------------------------------
console.log('\nTest 4: Gap-Skipping Playback Engine Simulation...');
{
  const ranges: [number, number][] = [[10.0, 15.0], [20.0, 30.0]];
  const sourceDur = 60.0;

  // Simulate playback loop behavior for a given video.currentTime
  interface PlaybackAction {
    type: 'jump_gap' | 'stop_eof' | 'underflow_reset' | 'play_in_piece';
    targetTime: number;
    concatTime: number | null;
  }

  function simulatePlaybackTick(cur: number, effectiveRanges: [number, number][], totalSourceDur: number): PlaybackAction {
    const firstPieceStart = effectiveRanges[0][0];
    const lastPieceEnd = effectiveRanges[effectiveRanges.length - 1][1];

    // 1. EOF
    if (cur >= lastPieceEnd - 0.005) {
      return { type: 'stop_eof', targetTime: firstPieceStart, concatTime: 0 };
    }

    // 2. Underflow
    if (cur < firstPieceStart - 0.2) {
      return { type: 'underflow_reset', targetTime: firstPieceStart, concatTime: 0 };
    }

    // 3. Gap skipping
    for (let i = 0; i < effectiveRanges.length - 1; i++) {
      const pieceEnd = effectiveRanges[i][1];
      const nextPieceStart = effectiveRanges[i + 1][0];
      if (cur >= pieceEnd - 0.005 && cur < nextPieceStart) {
        const nextConcat = sourceToConcat(effectiveRanges, nextPieceStart, totalSourceDur);
        return { type: 'jump_gap', targetTime: nextPieceStart, concatTime: nextConcat };
      }
    }

    // 4. In piece
    const concatTime = sourceToConcat(effectiveRanges, cur, totalSourceDur);
    return { type: 'play_in_piece', targetTime: cur, concatTime };
  }

  // Case A: Playback progressing normally inside Piece 0
  const action1 = simulatePlaybackTick(12.5, ranges, sourceDur);
  assert.equal(action1.type, 'play_in_piece');
  assert.equal(action1.targetTime, 12.5);
  assert.equal(action1.concatTime, 2.5);

  // Case B: Playback reaches exact end of Piece 0 (15.0s) -> Immediate jump to Piece 1 start (20.0s)
  const action2 = simulatePlaybackTick(15.0, ranges, sourceDur);
  assert.equal(action2.type, 'jump_gap', 'Must trigger gap jump at piece end');
  assert.equal(action2.targetTime, 20.0, 'Must jump to start of next piece (20.0s)');
  assert.equal(action2.concatTime, 5.0, 'Presentation time must update to 5.0s');

  // Case C: Sub-frame near boundary (14.996s, within 0.005s epsilon) -> Immediate jump
  const action3 = simulatePlaybackTick(14.996, ranges, sourceDur);
  assert.equal(action3.type, 'jump_gap', 'Sub-frame boundary triggers jump');
  assert.equal(action3.targetTime, 20.0);

  // Case D: User seeks into deleted gap (e.g. 17.5s) -> Immediate jump to next piece start (20.0s)
  const action4 = simulatePlaybackTick(17.5, ranges, sourceDur);
  assert.equal(action4.type, 'jump_gap', 'Landing in deleted gap triggers immediate jump to next piece');
  assert.equal(action4.targetTime, 20.0);

  // Case E: Playback reaches end of final piece (30.0s) -> Stop and reset to start of first piece (10.0s)
  const action5 = simulatePlaybackTick(30.0, ranges, sourceDur);
  assert.equal(action5.type, 'stop_eof', 'Must trigger EOF stop at final piece end');
  assert.equal(action5.targetTime, 10.0, 'Must reset position to first piece start (10.0s)');

  // Case F: Pre-clip underflow (e.g. 8.0s < 10.0 - 0.2) -> Reset to start
  const action6 = simulatePlaybackTick(8.0, ranges, sourceDur);
  assert.equal(action6.type, 'underflow_reset');
  assert.equal(action6.targetTime, 10.0);

  console.log('  ✓ Playback engine simulation handles gap jumping, EOF reset, and underflow accurately');
}

// ---------------------------------------------------------------------------
// TEST 5: Presentation-Clock Timeline Splitting (concatToSource + splitAt)
// ---------------------------------------------------------------------------
console.log('\nTest 5: Presentation-Clock Timeline Splitting...');
{
  const ranges: [number, number][] = [[10.0, 15.0], [20.0, 30.0]];
  const sourceDur = 60.0;

  // Split at presentation time 7.0s
  // 1. Map presentation time to source time
  const sourceSplitTime = roundMs(concatToSource(ranges, 7.0, sourceDur));
  assert.equal(sourceSplitTime, 22.0, 'Presentation 7.0s maps to source 22.0s');

  // 2. Perform splitAt
  const newRanges = splitAt(ranges, sourceSplitTime, sourceDur);
  assert.equal(newRanges.length, 3, 'Split should produce 3 pieces');
  assert.deepEqual(newRanges, [
    [10.0, 15.0],
    [20.0, 22.0],
    [22.0, 30.0],
  ]);
  assert.equal(keptDuration(newRanges), 15.0, 'Kept duration must remain invariant at 15.0s');

  // Splitting at presentation time 2.0s
  const splitSource2 = roundMs(concatToSource(newRanges, 2.0, sourceDur));
  assert.equal(splitSource2, 12.0, 'Presentation 2.0s maps to source 12.0s');
  const fourRanges = splitAt(newRanges, splitSource2, sourceDur);
  assert.equal(fourRanges.length, 4, 'Split should produce 4 pieces');
  assert.deepEqual(fourRanges, [
    [10.0, 12.0],
    [12.0, 15.0],
    [20.0, 22.0],
    [22.0, 30.0],
  ]);
  assert.equal(keptDuration(fourRanges), 15.0, 'Kept duration remains invariant across multiple splits');

  console.log('  ✓ Splitting in presentation time correctly slices source segments without duration drift');
}

// ---------------------------------------------------------------------------
// TEST 6: Ripple Delete & Edge Trim Primitives
// ---------------------------------------------------------------------------
console.log('\nTest 6: Ripple Delete & Edge Trim Primitives...');
{
  const ranges: [number, number][] = [
    [10.0, 12.0],
    [12.0, 15.0],
    [20.0, 22.0],
    [22.0, 30.0],
  ];
  const sourceDur = 60.0;

  // Ripple delete Piece 2 ([20.0, 22.0])
  const afterDelete = deletePiece(ranges, 2, sourceDur);
  assert.equal(afterDelete.length, 3, 'Must have 3 pieces after delete');
  assert.deepEqual(afterDelete, [
    [10.0, 12.0],
    [12.0, 15.0],
    [22.0, 30.0],
  ]);
  assert.equal(keptDuration(afterDelete), 13.0, 'Duration reduced by 2.0s');

  // Trim Piece 0 start from 10.0 to 11.0
  const afterTrimStart = trimEdge(afterDelete, 0, 'start', 11.0, sourceDur);
  assert.deepEqual(afterTrimStart[0], [11.0, 12.0]);
  assert.equal(keptDuration(afterTrimStart), 12.0);

  // Trim Piece 2 end from 30.0 to 28.0
  const afterTrimEnd = trimEdge(afterTrimStart, 2, 'end', 28.0, sourceDur);
  assert.deepEqual(afterTrimEnd[2], [22.0, 28.0]);
  assert.equal(keptDuration(afterTrimEnd), 10.0);

  // Cannot delete only remaining piece
  const single: [number, number][] = [[10.0, 20.0]];
  assert.throws(
    () => deletePiece(single, 0, sourceDur),
    (err) => err instanceof TimelineError && err.message.includes('cannot delete the final kept segment'),
    'Deleting final piece must throw TimelineError'
  );

  console.log('  ✓ Ripple deletion and edge trimming function cleanly with invariant guards');
}

// ---------------------------------------------------------------------------
// TEST 7: Boundary Conditions & Error Handling
// ---------------------------------------------------------------------------
console.log('\nTest 7: Boundary Conditions & Error Handling...');
{
  const ranges: [number, number][] = [[10.0, 15.0], [20.0, 30.0]];
  const sourceDur = 60.0;
  const total = keptDuration(ranges); // 15.0s

  // Negative presentation time throws
  assert.throws(
    () => concatToSource(ranges, -1.0, sourceDur),
    (err) => err instanceof TimelineError && err.message.includes('outside the kept timeline'),
    'Negative concat time must throw'
  );

  // Beyond total kept duration throws
  assert.throws(
    () => concatToSource(ranges, 16.0, sourceDur),
    (err) => err instanceof TimelineError && err.message.includes('outside the kept timeline'),
    'Excess concat time must throw'
  );

  // Sub-100ms piece split throws
  assert.throws(
    () => splitAt(ranges, 10.05, sourceDur),
    (err) => err instanceof TimelineError && err.message.includes('at least 0.1s'),
    'Split leaving <0.1s must throw'
  );

  console.log('  ✓ Boundary edge cases and out-of-bounds inputs are safely rejected');
}

console.log('\n🎉 ALL 7 EDL PLAYBACK GAP SKIPPING & TIME MAPPING TESTS PASSED 100%!\n');
