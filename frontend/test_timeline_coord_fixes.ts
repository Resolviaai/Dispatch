/**
 * P0 timeline coordinate-space fix verification:
 * 1. Read-only EDL mappers (sourceToConcat, concatToSource, intersectSourceInterval,
 *    remapCaptionTrackToTimeline) never enforce the 5s/180s total-duration contract;
 *    mutation validators (normalizeRanges, splitAt) still do.
 * 2. remapCaptionTrackToTimeline preserves fragment envelopes (outward trims and
 *    Span Video spans survive) and links fragments back to their source group via
 *    sourceGroupId so `${id}_${fIdx}` edits resolve.
 * 3. CaptionTimeline clip-mode ruler length is the KEPT duration, not the outer
 *    source-time hull (a [120,150]s segment is a 30s clip, not a 150s ruler).
 * 4. handleUpdateGroup maps dragged presentation-coord words into source
 *    coordinates with exactly ONE shift (no double-shift).
 * 5. captionTimeIsActive uses a half-open seam [start, end) so adjacent captions
 *    never double-render at the shared boundary. End-of-track inclusivity is a
 *    renderer concern (DispatchCaptionRenderer / InteractiveTextCanvas).
 */

import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  concatToSource,
  intersectSourceInterval,
  normalizeRanges,
  remapCaptionTrackToTimeline,
  sourceToConcat,
  splitAt,
  TimelineError,
} from './src/captions/timelineEngine';
import { captionTimeIsActive } from './src/captions/timelineInteraction';
import { CaptionTimeline } from './src/captions/CaptionTimeline';

console.log('Timeline coordinate-space fixes...');

// ---------------------------------------------------------------------------
// 1. Read-only mappers do not enforce the 5s/180s total-duration contract.
// ---------------------------------------------------------------------------
console.log('Test 1: read-only mappers skip total-duration enforcement...');
{
  // 3s kept timeline — below MIN_TOTAL_DURATION (5s).
  const shortRanges = [[10, 13]];
  assert.equal(sourceToConcat(shortRanges, 11, 600), 1, 'sourceToConcat maps inside short timelines');
  assert.equal(concatToSource(shortRanges, 1, 600), 11, 'concatToSource maps inside short timelines');
  assert.equal(intersectSourceInterval(shortRanges, 10, 13, 600).length, 1, 'intersectSourceInterval fragments short timelines');

  // 200s kept timeline — above MAX_TOTAL_DURATION (180s).
  const longRanges = [[0, 200]];
  assert.equal(sourceToConcat(longRanges, 150, 200), 150, 'sourceToConcat maps inside long timelines');
  assert.equal(concatToSource(longRanges, 150, 200), 150, 'concatToSource maps inside long timelines');

  // remap must not silently return the track unremapped for short timelines:
  // the group inside the kept range survives, the trimmed-off one is omitted.
  const multiTrack = {
    groups: [
      { id: 'keep', start: 0.5, end: 2.5, words: [{ id: 'w1', text: 'hi', start: 0.5, end: 2.5 }] },
      { id: 'drop', start: 5, end: 6, words: [{ id: 'w2', text: 'cut', start: 5, end: 6 }] },
    ],
  };
  const remappedShort = remapCaptionTrackToTimeline(multiTrack, [[100, 103]], 100, 600);
  assert.deepEqual(
    remappedShort.groups.map((g: any) => g.id),
    ['keep'],
    'short kept timelines remap captions instead of returning the track untouched',
  );

  // Mutation validators still enforce the total-duration contract.
  assert.throws(() => normalizeRanges([[10, 13]], 600), TimelineError, 'normalizeRanges still rejects <5s totals');
  assert.throws(() => normalizeRanges([[0, 200]], 200), TimelineError, 'normalizeRanges still rejects >180s totals');
  assert.throws(() => splitAt([[0, 3]], 1.5, 600), TimelineError, 'splitAt still enforces the total contract');
  console.log('  ✓ read-only mappings work for <5s and >180s kept timelines; mutations still enforce');
}

// ---------------------------------------------------------------------------
// 2. Fragment envelopes survive remap; fragments carry sourceGroupId.
// ---------------------------------------------------------------------------
console.log('Test 2: fragment envelope preservation and fragmented-id resolution...');
{
  // Clip cut from source 120s with a middle piece removed: kept = [120,135] + [140,150].
  const ranges = [[120, 135], [140, 150]];
  // Caption track times are relative to the caption source origin (120).
  const captionTrack: any = {
    groups: [
      {
        id: 'grp_span',
        start: 10, // source [130,145] — spans the removed gap [135,140] -> two fragments
        end: 25,
        words: [
          { id: 'w1', text: 'a', start: 10, end: 12 },
          { id: 'w2', text: 'b', start: 13, end: 14 },
          { id: 'w3', text: 'c', start: 22, end: 24 },
        ],
      },
      {
        id: 'grp_tail',
        start: 20, // source [140,144] — inside the second segment only
        end: 24,
        words: [
          { id: 'w4', text: 'd', start: 20, end: 21 },
          { id: 'w5', text: 'e', start: 22, end: 23 },
        ],
      },
    ],
  };
  const remapped = remapCaptionTrackToTimeline(captionTrack, ranges, 120, 600);

  const spanFragments = remapped.groups.filter((g: any) => g.sourceGroupId === 'grp_span');
  assert.equal(spanFragments.length, 2, 'gap-spanning group emits two fragments linked by sourceGroupId');
  assert.deepEqual(spanFragments.map((g: any) => g.id), ['grp_span_0', 'grp_span_1']);

  // Outward envelopes survive: fragment envelopes [10,15] and [15,20] must not
  // shrink to the word extents [10,14] and [17,19].
  assert.equal(spanFragments[0].start, 10);
  assert.equal(spanFragments[0].end, 15, 'outward-trimmed fragment end survives word-tight remapping');
  assert.equal(spanFragments[1].start, 15, 'outward-trimmed fragment start survives word-tight remapping');
  assert.equal(spanFragments[1].end, 20);

  // Single-fragment groups keep their original id but still carry the stable link.
  const tail = remapped.groups.find((g: any) => g.id === 'grp_tail');
  assert.ok(tail, 'single-fragment group keeps its original id');
  assert.equal(tail.sourceGroupId, 'grp_tail', 'single-fragment groups also expose sourceGroupId');
  // Segment 2 begins at concat 15; source 140 maps to concat 15.
  assert.equal(tail.start, 15);
  assert.equal(tail.end, 19);
  assert.deepEqual(tail.words.map((w: any) => [w.start, w.end]), [[15, 16], [17, 18]]);

  // Fragmented ids resolve back to the source group exactly as the
  // resolveSourceGroupId helper in ClipsScreen.tsx does.
  const resolveSourceGroupId = (groupId: string): string => {
    if (captionTrack.groups.some((g: any) => g.id === groupId)) return groupId;
    const fragment = remapped.groups.find((g: any) => g.id === groupId) as any;
    if (fragment && typeof fragment.sourceGroupId === 'string' && fragment.sourceGroupId) {
      return fragment.sourceGroupId;
    }
    const match = /^(.*)_(\d+)$/.exec(groupId);
    return match ? match[1] : groupId;
  };
  assert.equal(resolveSourceGroupId('grp_span'), 'grp_span', 'unfragmented ids resolve to themselves');
  assert.equal(resolveSourceGroupId('grp_span_0'), 'grp_span', 'fragment id resolves through sourceGroupId');
  assert.equal(resolveSourceGroupId('grp_span_1'), 'grp_span', 'second fragment resolves to the same source group');
  console.log('  ✓ envelopes preserved, sourceGroupId carried, fragmented ids resolve');
}

// ---------------------------------------------------------------------------
// 3. Clip-mode ruler length uses kept duration (safeClipDuration).
// ---------------------------------------------------------------------------
console.log('Test 3: safeClipDuration in clip mode with a [120,150] segment...');
{
  const baseProps = {
    track: { groups: [{ id: 'g', start: 0, end: 3, words: [] }] },
    currentTime: 0,
    duration: 30,
    selectedGroupId: null,
    isPlaying: false,
    onSeek: () => {},
    onSelectGroup: () => {},
    segments: [{ id: 'seg_1', start: 120, end: 150, index: 0 }],
    clipStart: 120,
    clipEnd: 150,
    sourceDuration: 600,
    hideTrimControls: true,
  } as any;

  // 30s kept * 55px/s default zoom = 1650px; the old hull bug rendered 8250px.
  const clipModeMarkup = renderToStaticMarkup(React.createElement(CaptionTimeline, {
    ...baseProps,
    timeMode: 'clip',
    sourceTimebase: false,
  } as any));
  assert.match(clipModeMarkup, /width:1650px/, 'clip-mode ruler length is the kept 30s duration');
  assert.doesNotMatch(clipModeMarkup, /width:8250px/, 'clip-mode ruler must not use the 150s source hull');

  // Source mode keeps the source hull so pieces laid out at source coords fit.
  const sourceModeMarkup = renderToStaticMarkup(React.createElement(CaptionTimeline, {
    ...baseProps,
    timeMode: 'source',
    sourceTimebase: true,
  } as any));
  assert.match(sourceModeMarkup, /width:33000px/, 'source-mode ruler still spans the 600s source hull');
  console.log('  ✓ clip mode uses kept duration (1650px), source mode keeps the hull (33000px)');
}

// ---------------------------------------------------------------------------
// 4. handleUpdateGroup maps dragged words with exactly one shift.
// ---------------------------------------------------------------------------
console.log('Test 4: handleUpdateGroup single-shift word mapping...');
{
  const ranges = [[120, 135], [140, 150]];
  const captionTrack: any = {
    groups: [{
      id: 'grp_tail',
      start: 20,
      end: 24,
      words: [
        { id: 'w4', text: 'd', start: 20, end: 21 },
        { id: 'w5', text: 'e', start: 22, end: 23 },
      ],
    }],
  };
  const presentationCaptionTrack = remapCaptionTrackToTimeline(captionTrack, ranges, 120, 600);

  // CaptionTimeline emits presentation-coord patches with the drag ALREADY
  // applied to both bounds and words (drag of +1.5s in presentation time).
  const dragDelta = 1.5;
  const presGrp = presentationCaptionTrack.groups.find((g: any) => g.id === 'grp_tail');
  const patch = {
    start: presGrp.start + dragDelta,
    end: presGrp.end + dragDelta,
    words: presGrp.words.map((w: any) => ({ ...w, start: w.start + dragDelta, end: w.end + dragDelta })),
  };

  // === Conversion exactly as implemented in ClipsScreen handleUpdateGroup ===
  const sourceGrp = captionTrack.groups.find((g: any) => g.id === 'grp_tail');
  const deltaStart = patch.start - presGrp.start;
  const finalPatch: any = {
    start: Math.max(0, Math.round((sourceGrp.start + deltaStart) * 1000) / 1000),
    end: Math.max(sourceGrp.start + deltaStart + 0.05, Math.round((sourceGrp.end + (patch.end - presGrp.end)) * 1000) / 1000),
  };
  const presToSourceShift = sourceGrp.start - presGrp.start;
  finalPatch.words = patch.words.map((w: any) => ({
    ...w,
    start: Math.max(0, Math.round((w.start + presToSourceShift) * 1000) / 1000),
    end: Math.max(0, Math.round((w.end + presToSourceShift) * 1000) / 1000),
  }));

  // The group's words moved exactly once by the drag delta in source coordinates
  // (20 -> 21.5). The old bug applied deltaStart AGAIN on already-shifted
  // presentation words (producing 18 instead of 21.5 for the first word).
  assert.equal(finalPatch.start, 21.5, 'group start receives the single drag delta');
  assert.deepEqual(
    finalPatch.words.map((w: any) => [w.start, w.end]),
    [[21.5, 22.5], [23.5, 24.5]],
    'words shift exactly once: presentation + (sourceGrp.start - presGrp.start)',
  );
  console.log('  ✓ dragged words land at source 21.5/23.5 (single shift, no double-shift)');
}

// ---------------------------------------------------------------------------
// 5. Half-open caption seam [start, end).
// ---------------------------------------------------------------------------
console.log('Test 5: captionTimeIsActive half-open seam...');
{
  assert.equal(captionTimeIsActive(5, 5, 6), true, 'caption activates at its start');
  assert.equal(captionTimeIsActive(5.999, 5, 6), true, 'caption stays active before its end');
  assert.equal(captionTimeIsActive(6, 5, 6), false, 'seam is half-open: no double render where the next caption starts');
  console.log('  ✓ [start, end) seam with no double render at the boundary');
}

console.log('\nAll timeline coordinate-space fix tests passed.');
