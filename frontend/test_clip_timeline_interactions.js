/** Interactive timeline regression against the exact state helpers wired to pointer, seek, and caption events. */

import assert from 'node:assert/strict';
import {
  beginTrimPointer,
  captionTimeIsActive,
  clipTimeToSourceTime,
  moveTrimPointer,
  sourceTimeToClipTime,
} from './src/captions/timelineInteraction';
import { normalizeCaptionTrack, resolveStudioClip, trackForClip } from './src/captions/normalizeTrack';

console.log('Clip timeline pointer and playback interactions...');

// Pointer down → drag left extends beyond the suggested range while staying in the source.
const suggested = { start: 120, end: 150 };
const startDrag = beginTrimPointer('start', 7, 200, suggested);
const extendedStart = moveTrimPointer(startDrag, 7, 80, 2, 600);
assert.deepEqual(extendedStart, { start: 60, end: 150 }, 'start drag can extend beyond Gemini range');
assert.deepEqual(moveTrimPointer(startDrag, 8, 80, 2, 600), suggested, 'an unrelated pointer cannot move the active trim');

// End drag clamps against source duration and the 180-second product maximum.
const endDrag = beginTrimPointer('end', 9, 10, extendedStart);
assert.deepEqual(moveTrimPointer(endDrag, 9, 1000, 2, 600), { start: 60, end: 240 });
const nearEof = beginTrimPointer('end', 10, 0, { start: 570, end: 590 });
assert.deepEqual(moveTrimPointer(nearEof, 10, 500, 10, 600), { start: 570, end: 600 }, 'end drag clamps at source EOF');
const maxLength = beginTrimPointer('start', 11, 0, { start: 220, end: 390 });
assert.deepEqual(moveTrimPointer(maxLength, 11, -500, 10, 1000), { start: 210, end: 390 }, 'start drag preserves the 180-second limit');

// Playback uses clip-relative seconds; the filmstrip/playhead uses source-relative seconds.
assert.equal(clipTimeToSourceTime(6.25, 120), 126.25);
assert.equal(sourceTimeToClipTime(126.25, 120, 30), 6.25);
assert.equal(sourceTimeToClipTime(110, 120, 30), 0, 'seeking before the selected clip clamps to its start');

// Caption activity is evaluated on the clip clock even though timeline blocks are source-offset.
assert.equal(captionTimeIsActive(6, 5.8, 6.2), true);
assert.equal(captionTimeIsActive(126, 5.8, 6.2), false, 'source time is not compared directly to clip-relative captions');

// Switching clips hides the previous caption track until the new clip's track has loaded.
const clips = [{ id: 'a' }, { id: 'b' }];
const trackA = normalizeCaptionTrack({ groups: [{ id: 'a-caption', start: 1, end: 2, words: [] }] }, 'a', 10);
assert.equal(resolveStudioClip(clips, 'b')?.id, 'b');
assert.equal(trackForClip('b', 'a', trackA), null);
const trackB = normalizeCaptionTrack({ groups: [{ id: 'b-caption', start: 3, end: 4, words: [] }] }, 'b', 10);
assert.equal(trackForClip('b', 'b', trackB)?.groups[0].id, 'b-caption');

console.log('  passed: trim pointer sequence, suggested-range extension, source/clip seek mapping, caption timing, and clip switching');
