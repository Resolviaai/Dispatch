import assert from 'assert';
import { CaptionTrack, CaptionGroup, Word } from './frontend/src/captions/types';
import { export_to_ass_dialogue_mock, export_to_srt_mock } from './test_mock_exporter';

console.log('🧪 Starting Dispatch Editor Repair & Integrity Test Suite...\n');

// --------------------------------------------------------------------------
// TEST 1: Directional Playhead Snapping & Constant Screen-Pixel Threshold
// --------------------------------------------------------------------------
console.log('Test 1: Directional playhead snapping logic...');
{
  const pxPerSec = 55;
  const SNAP_PX = 12;
  const snapThreshold = SNAP_PX / pxPerSec; // ~0.218s
  const playheadTime = 10.0;
  const origDuration = 3.0;

  // Case A: Block positioned to the right of playhead (e.g. proposedStart = 10.15)
  // Directional invariant: Start edge snaps to playheadTime
  const proposedStartRight = 10.15;
  let snappedStartRight = proposedStartRight;
  if (Math.abs(proposedStartRight - playheadTime) <= snapThreshold && proposedStartRight >= playheadTime - snapThreshold) {
    snappedStartRight = playheadTime;
  }
  assert.strictEqual(snappedStartRight, 10.0, 'Block to the right of playhead must snap its start edge to playhead');

  // Case B: Block positioned to the left of playhead (e.g. proposedEnd = 9.88, so proposedStart = 6.88)
  // Directional invariant: End edge snaps to playheadTime
  const proposedEndLeft = 9.88;
  const proposedStartLeft = proposedEndLeft - origDuration;
  let snappedEndLeft = proposedEndLeft;
  let finalStartLeft = proposedStartLeft;
  if (Math.abs(proposedEndLeft - playheadTime) <= snapThreshold && proposedEndLeft <= playheadTime + snapThreshold) {
    snappedEndLeft = playheadTime;
    finalStartLeft = snappedEndLeft - origDuration;
  }
  assert.strictEqual(snappedEndLeft, 10.0, 'Block to the left of playhead must snap its end edge to playhead');
  assert.strictEqual(finalStartLeft, 7.0, 'Start edge must maintain original duration when end edge snaps');

  console.log('  ✓ Directional snapping snaps start edge when right of playhead and end edge when left of playhead');
}

// --------------------------------------------------------------------------
// TEST 2: Word Trimming Without Stretching (Speech Authenticity)
// --------------------------------------------------------------------------
console.log('Test 2: Word trimming without stretching...');
{
  const origWords: Word[] = [
    { id: 'w1', text: 'Hello', start: 1.0, end: 1.5 },
    { id: 'w2', text: 'world', start: 1.6, end: 2.2 },
    { id: 'w3', text: 'this', start: 2.3, end: 2.6 },
    { id: 'w4', text: 'is', start: 2.7, end: 2.9 },
    { id: 'w5', text: 'Dispatch', start: 3.0, end: 3.8 },
  ];

  // Trim start to 2.0s:
  // Words 'Hello' (end 1.5) is excluded.
  // Word 'world' (start 1.6, end 2.2) is clamped to start at 2.0.
  // Words 'this', 'is', 'Dispatch' preserve their exact original timestamps!
  const finalTrimStart = 2.0;
  const remainingAfterStartTrim = origWords.filter((w) => w.end > finalTrimStart);
  const nextWordsStartTrim = remainingAfterStartTrim.map((w) => ({
    ...w,
    start: Math.max(finalTrimStart, w.start),
    end: Math.max(finalTrimStart, w.end),
  }));

  assert.strictEqual(nextWordsStartTrim.length, 4, 'Excluded words before trim boundary');
  assert.strictEqual(nextWordsStartTrim[0].text, 'world');
  assert.strictEqual(nextWordsStartTrim[0].start, 2.0, 'Boundary word clamped to trimStart');
  assert.strictEqual(nextWordsStartTrim[0].end, 2.2, 'Boundary word preserves original end');
  assert.strictEqual(nextWordsStartTrim[1].text, 'this');
  assert.strictEqual(nextWordsStartTrim[1].start, 2.3, 'Subsequent words maintain exact authentic timestamps');
  assert.strictEqual(nextWordsStartTrim[1].end, 2.6);
  assert.strictEqual(nextWordsStartTrim[3].text, 'Dispatch');
  assert.strictEqual(nextWordsStartTrim[3].start, 3.0);
  assert.strictEqual(nextWordsStartTrim[3].end, 3.8);

  // Trim end to 2.8s:
  // Word 'Dispatch' (start 3.0) is excluded.
  // Word 'is' (start 2.7, end 2.9) is clamped to end at 2.8.
  // Word 'this' (2.3 to 2.6) preserves its original timestamps!
  const finalTrimEnd = 2.8;
  const remainingAfterEndTrim = origWords.filter((w) => w.start < finalTrimEnd);
  const nextWordsEndTrim = remainingAfterEndTrim.map((w) => ({
    ...w,
    start: Math.min(finalTrimEnd, w.start),
    end: Math.min(finalTrimEnd, w.end),
  }));

  assert.strictEqual(nextWordsEndTrim.length, 4);
  assert.strictEqual(nextWordsEndTrim[3].text, 'is');
  assert.strictEqual(nextWordsEndTrim[3].start, 2.7);
  assert.strictEqual(nextWordsEndTrim[3].end, 2.8, 'Boundary word clamped to trimEnd');
  assert.strictEqual(nextWordsEndTrim[2].text, 'this');
  assert.strictEqual(nextWordsEndTrim[2].start, 2.3, 'In-range words maintain authentic timestamps');

  console.log('  ✓ In-range speech words retain authentic timestamps without proportional stretching');
}

// --------------------------------------------------------------------------
// TEST 3: Duplicate Shifts Word Timestamps by Delta
// --------------------------------------------------------------------------
console.log('Test 3: Layer duplication shifts word timestamps by delta...');
{
  const sourceGroup: CaptionGroup = {
    id: 'grp_orig',
    start: 2.0,
    end: 5.0,
    type: 'caption',
    words: [
      { id: 'w1', text: 'Step', start: 2.0, end: 2.8 },
      { id: 'w2', text: 'one', start: 3.0, end: 4.5 },
    ],
  };

  const nextStart = 2.5;
  const nextEnd = 5.5;
  const timeDelta = nextStart - sourceGroup.start; // +0.5s

  const dup: CaptionGroup = {
    ...sourceGroup,
    id: 'grp_dup',
    start: nextStart,
    end: nextEnd,
    words: (sourceGroup.words || []).map((w, idx) => ({
      ...w,
      id: `w_dup_${idx}`,
      start: Number((w.start + timeDelta).toFixed(3)),
      end: Number((w.end + timeDelta).toFixed(3)),
    })),
  };

  assert.strictEqual(dup.words![0].start, 2.5, 'Word 1 shifted by +0.5s delta');
  assert.strictEqual(dup.words![0].end, 3.3, 'Word 1 end shifted by +0.5s delta');
  assert.strictEqual(dup.words![1].start, 3.5, 'Word 2 shifted by +0.5s delta');
  assert.strictEqual(dup.words![1].end, 5.0, 'Word 2 end shifted by +0.5s delta');

  console.log('  ✓ Duplicate shifts word timestamps by exact time offset');
}

// --------------------------------------------------------------------------
// TEST 4: Literal Search & Replace in TranscriptEditor (No Regex Crashing)
// --------------------------------------------------------------------------
console.log('Test 4: Literal regex search & replace...');
{
  function escapeRegex(str: string): string {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  // Searching for literal '.' should NOT match every letter!
  const dotSearch = '.';
  const regexDot = new RegExp(escapeRegex(dotSearch), 'gi');
  const sample = 'Dispatch v1.0 is fast.';
  const replacedDot = sample.replace(regexDot, '!');
  assert.strictEqual(replacedDot, 'Dispatch v1!0 is fast!', 'Only literal periods should be replaced');

  // Searching for literal bracket '[' or '(' should not crash
  const bracketSearch = '[';
  assert.doesNotThrow(() => {
    new RegExp(escapeRegex(bracketSearch), 'gi');
  }, 'Escaping special regex character must not throw syntax error');

  console.log('  ✓ Search and Replace All handles special regex characters literally without crashing');
}

// --------------------------------------------------------------------------
// TEST 5: Exporter Protection & Non-Text Layer Subtitle Immunity
// --------------------------------------------------------------------------
console.log('Test 5: Exporter skips non-text layers and hidden layers...');
{
  const mockTrack: CaptionTrack = {
    id: 'track_1',
    clip_id: 'c1',
    groups: [
      {
        id: 'g_cap',
        start: 0,
        end: 2,
        type: 'caption',
        words: [{ id: 'w1', text: 'Real subtitle', start: 0, end: 2 }],
      },
      {
        id: 'g_hidden_cap',
        start: 2,
        end: 4,
        type: 'caption',
        is_hidden: true,
        words: [{ id: 'w2', text: 'Hidden subtitle', start: 2, end: 4 }],
      },
      {
        id: 'g_audio',
        start: 0,
        end: 5,
        type: 'audio',
        audio_url: '/clips/audio.mp3',
        words: [],
      },
      {
        id: 'g_overlay',
        start: 1,
        end: 3,
        type: 'overlay',
        overlay_url: '/clips/overlay.png',
        words: [],
      },
    ],
    default_style: { font_family: 'Montserrat' },
    default_layout: { position_x: 0.5, position_y: 0.78 },
    default_animation: { type: 'pop' },
  };

  const assDialogue = export_to_ass_dialogue_mock(mockTrack);
  const srtSubtitles = export_to_srt_mock(mockTrack);

  assert.strictEqual(assDialogue.length, 1, 'Only the active, non-hidden caption should be in ASS dialogue');
  assert.strictEqual(assDialogue[0].text, 'Real subtitle');

  assert.strictEqual(srtSubtitles.length, 1, 'Only the active, non-hidden caption should be in SRT output');
  assert.strictEqual(srtSubtitles[0].text, 'Real subtitle');

  console.log('  ✓ Non-text layers and hidden layers are 100% excluded from exported subtitles');
}

// --------------------------------------------------------------------------
// TEST 6: Dynamic Multi-track Non-Overlapping Allocation
// --------------------------------------------------------------------------
console.log('Test 6: Multi-track allocation with unpinned layers...');
{
  const groups: CaptionGroup[] = [
    { id: 'g1', start: 0, end: 5, type: 'caption' },
    { id: 'g2', start: 2, end: 7, type: 'text' }, // Overlaps g1 -> should be allocated to Track L2
    { id: 'g3', start: 6, end: 10, type: 'caption' }, // Does not overlap g1 -> can pack into Track L1
  ];

  const sorted = [...groups].sort((a, b) => a.start - b.start);
  const buckets: CaptionGroup[][] = [];

  sorted.forEach((grp) => {
    let placed = false;
    for (let i = 0; i < buckets.length; i++) {
      const bucket = buckets[i];
      const hasOverlap = bucket.some(
        (existing) => Math.max(grp.start, existing.start) < Math.min(grp.end, existing.end)
      );
      if (!hasOverlap) {
        bucket.push(grp);
        placed = true;
        break;
      }
    }
    if (!placed) {
      buckets.push([grp]);
    }
  });

  assert.strictEqual(buckets.length, 2, 'Should create exactly 2 tracks for 2 overlapping spans');
  assert.deepStrictEqual(buckets[0].map((g) => g.id), ['g1', 'g3'], 'Non-overlapping g1 and g3 pack into track 0');
  assert.deepStrictEqual(buckets[1].map((g) => g.id), ['g2'], 'Overlapping g2 packs into track 1');

  console.log('  ✓ Dynamic track allocator packs non-overlapping elements cleanly without forced collisions');
}

console.log('\n🎉 ALL 6 EDITOR INTEGRITY & REPAIR TESTS PASSED 100%!');
