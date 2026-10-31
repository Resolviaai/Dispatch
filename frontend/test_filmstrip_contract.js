/** Filmstrip contract regression: normalized payload + time-anchored placement geometry. */

import assert from 'node:assert/strict';
import {
  filmstripFrameBox,
  normalizeFilmstripPayload,
  thumbnailFallbackFilmstrip,
} from './src/captions/filmstrip';

console.log('Filmstrip time-anchored contract...');

// 1. Server payload parses: snakeCase fields, frame objects, plan passthrough.
const payload = normalizeFilmstripPayload({
  clip_id: 'abc',
  duration: 80,
  interval: 5,
  count: 16,
  scope: 'source',
  source_start: 120,
  frames: [
    { url: '/clips/filmstrip_cache/abc/f00.jpg?v=1', t: 0, span: 5 },
    { url: '/clips/filmstrip_cache/abc/f01.jpg?v=1', t: 5, span: 5 },
    { url: '/clips/filmstrip_cache/abc/f15.jpg?v=1', t: 75, span: 5 },
  ],
});
assert.ok(payload, 'valid payload normalizes');
assert.equal(payload.clipId, 'abc');
assert.equal(payload.sourceStart, 120);
assert.equal(payload.scope, 'source');
assert.equal(payload.frames.length, 3);
assert.equal(payload.frames[2].t, 75);

// 2. Plan consistency: consecutive slot starts differ by exactly interval.
assert.equal(payload.frames[1].t - payload.frames[0].t, 5);
assert.equal(payload.frames[2].t - payload.frames[1].t, 70);

// 3. Junk survives gracefully: bad frames dropped, empty listing -> null.
assert.equal(normalizeFilmstripPayload(null), null);
assert.equal(normalizeFilmstripPayload({ frames: [] }), null);
const partial = normalizeFilmstripPayload({
  duration: 10,
  frames: [{ url: '', t: 0 }, 'junk', { url: '/x.jpg' }, { url: '/ok.jpg', t: '2.5' }],
});
assert.ok(partial);
assert.equal(partial.frames.length, 1);
assert.equal(partial.frames[0].t, 2.5);

// 4. Source-ruler placement: left = (clipStart + t) * px, width = interval * px.
const srcBox = filmstripFrameBox(payload.frames[1], payload, { pxPerSec: 10, clipStart: 120, isSourceMode: true });
assert.deepEqual(srcBox, { left: 1250, width: 50 });

// 5. Local-ruler placement: left = t * px.
const locBox = filmstripFrameBox(payload.frames[1], payload, { pxPerSec: 10, clipStart: 120, isSourceMode: false });
assert.deepEqual(locBox, { left: 50, width: 50 });

// 6. Slot rounding is index-derived: it never accumulates down the strip.
const lastBox = filmstripFrameBox(payload.frames[2], payload, { pxPerSec: 7, clipStart: 0, isSourceMode: false });
assert.deepEqual(lastBox, { left: Math.round(75 * 7), width: Math.round(5 * 7) });

// 7. Single-frame thumbnail fallback spans the whole clip exactly once.
const fb = thumbnailFallbackFilmstrip('/clips/x.jpg?v=9', 12);
assert.ok(fb);
assert.deepEqual(fb.frames, [{ url: '/clips/x.jpg?v=9', t: 0 }]);
const fbBox = filmstripFrameBox(fb.frames[0], fb, { pxPerSec: 10, clipStart: 0, isSourceMode: false });
assert.deepEqual(fbBox, { left: 0, width: 120 });

// 8. Empty/zero-duration fallbacks are rejected so the placeholder stays.
assert.equal(thumbnailFallbackFilmstrip('', 12), null);
assert.equal(thumbnailFallbackFilmstrip('/clips/x.jpg', 0), null);

// 9. Degenerate geometry opts -> no box (placeholder branch keeps showing).
assert.equal(filmstripFrameBox(fb.frames[0], fb, { pxPerSec: 0, clipStart: 0, isSourceMode: false }), null);

console.log('Filmstrip contract OK.');
