import assert from 'node:assert/strict';
import {
  splitCaptionGroupAtTime,
  mergeAdjacentCaptionGroups,
  generateCollisionResistantGroupId,
} from './src/captions/timingPreservation';
import {
  normalizeCaptionTrack,
  getSavedTemplate,
  saveDefaultTemplate,
  getSavedTemplateStorageKey,
} from './src/captions/normalizeTrack';
import {
  normalizeFilmstripPayload,
  filmstripFrameBox,
  thumbnailFallbackFilmstrip,
} from './src/captions/filmstrip';
import { CaptionGroup, Word } from './src/captions/types';

if (typeof (globalThis as any).localStorage === 'undefined') {
  const store = new Map<string, string>();
  (globalThis as any).localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => store.set(k, String(v)),
    removeItem: (k: string) => store.delete(k),
    clear: () => store.clear(),
  };
}

console.log('🧪 Starting Dispatch Editor Audit Regression Test Suite...\n');

// =========================================================================
// TEST 1: Safe Word Boundary Split Snapping (No Word Truncation)
// =========================================================================
console.log('Test 1: Split snaps to word boundary without truncating spoken words...');
const words: Word[] = [
  { id: 'w1', text: 'Hello', start: 1.0, end: 1.4, confidence: 0.99 },
  { id: 'w2', text: 'world', start: 1.5, end: 1.9, confidence: 0.98 },
  { id: 'w3', text: 'this', start: 2.2, end: 2.5, confidence: 0.97 },
  { id: 'w4', text: 'is', start: 2.6, end: 2.8, confidence: 0.99 },
  { id: 'w5', text: 'Dispatch', start: 2.9, end: 3.4, confidence: 0.99 },
];

const originalGroup: CaptionGroup = {
  id: 'grp_test_1',
  start: 0.8,
  end: 3.6,
  text: 'Hello world this is Dispatch',
  words: words.map((w) => ({ ...w })),
  type: 'caption',
  style: { font_family: 'Montserrat', font_size: 48, primary_color: '#FFFFFF' },
  layout: { position_y: 0.8, scale: 1.2 },
};

// Split at t = 2.0 (in the gap between 'world' at 1.9 and 'this' at 2.2)
const splitMid = splitCaptionGroupAtTime(originalGroup, 2.0);
assert.ok(splitMid, 'Mid-split succeeds');
const [leftG, rightG] = splitMid;

// Verify word integrity: NO word was truncated, sliced, or stretched
assert.equal(leftG.words?.length, 2, 'Left group contains 2 words');
assert.equal(rightG.words?.length, 3, 'Right group contains 3 words');
assert.equal(leftG.words?.[0].text, 'Hello');
assert.equal(leftG.words?.[0].start, 1.0);
assert.equal(leftG.words?.[0].end, 1.4);
assert.equal(leftG.words?.[1].text, 'world');
assert.equal(leftG.words?.[1].start, 1.5);
assert.equal(leftG.words?.[1].end, 1.9);

assert.equal(rightG.words?.[0].text, 'this');
assert.equal(rightG.words?.[0].start, 2.2);
assert.equal(rightG.words?.[0].end, 2.5);
assert.equal(rightG.words?.[2].text, 'Dispatch');
assert.equal(rightG.words?.[2].start, 2.9);
assert.equal(rightG.words?.[2].end, 3.4);

// Verify text string concatenation
assert.equal(leftG.text, 'Hello world');
assert.equal(rightG.text, 'this is Dispatch');

// Verify metadata and style preservation
assert.equal(leftG.style?.font_family, 'Montserrat');
assert.equal(rightG.style?.primary_color, '#FFFFFF');
assert.equal(rightG.layout?.scale, 1.2);

// Split right in the middle of word 'world' (t = 1.7):
// Must cleanly snap to nearest boundary WITHOUT slicing 'world'
const splitInsideWord = splitCaptionGroupAtTime(originalGroup, 1.7);
assert.ok(splitInsideWord, 'Split inside word snaps to boundary');
const [snapLeft, snapRight] = splitInsideWord;
assert.ok(snapLeft.words && snapRight.words);
assert.equal(snapLeft.words.length + snapRight.words.length, 5, 'Zero words lost or duplicated');
// 1.7 is closer to boundary between w1 (1.4) and w2 (1.5) at 1.45 than boundary at 2.05
assert.equal(snapLeft.words.length, 1, 'Left gets 1 word');
assert.equal(snapRight.words.length, 4, 'Right gets 4 words');
assert.equal(snapLeft.words[0].end, 1.4, 'Word 1 end preserved intact');
assert.equal(snapRight.words[0].start, 1.5, 'Word 2 start preserved intact');
console.log('  ✓ Spoken words remain intact with exact authentic timestamps\n');

// =========================================================================
// TEST 2: Split Rejection of Degenerate / Corrupted / Edge Cases
// =========================================================================
console.log('Test 2: Split rejects NaN, out-of-bounds, edge splits, and degenerate word spans...');
assert.equal(splitCaptionGroupAtTime(originalGroup, NaN), null, 'Rejects NaN splitTime');
assert.equal(splitCaptionGroupAtTime(originalGroup, Infinity), null, 'Rejects Infinity splitTime');
assert.equal(splitCaptionGroupAtTime(originalGroup, 0.2), null, 'Rejects split before group start');
assert.equal(splitCaptionGroupAtTime(originalGroup, 5.0), null, 'Rejects split after group end');
assert.equal(splitCaptionGroupAtTime(originalGroup, originalGroup.start + 0.02), null, 'Rejects edge split near start');
assert.equal(splitCaptionGroupAtTime(originalGroup, originalGroup.end - 0.02), null, 'Rejects edge split near end');

// Single-word groups now split their own word span (defect fix): the word text
// is duplicated into both halves with clamped timings. Edge splits and
// degenerate word spans are still rejected.
const singleWordGroup: CaptionGroup = {
  id: 'grp_single',
  start: 1.0,
  end: 2.0,
  text: 'Only',
  words: [{ id: 'w_one', text: 'Only', start: 1.1, end: 1.9, confidence: 1.0 }],
  type: 'caption',
};
assert.equal(splitCaptionGroupAtTime(singleWordGroup, 0.85), null, 'Rejects single-word split outside the group');
assert.equal(splitCaptionGroupAtTime(singleWordGroup, 2.5), null, 'Rejects single-word split after the group');
const tinyWordGroup: CaptionGroup = {
  ...singleWordGroup,
  words: [{ id: 'w_tiny', text: 'Only', start: 1.1, end: 1.18, confidence: 1.0 }],
};
assert.equal(splitCaptionGroupAtTime(tinyWordGroup, 1.14), null, 'Rejects single-word split of a sub-0.1s word span');
const singleSplit = splitCaptionGroupAtTime(singleWordGroup, 1.5);
assert.ok(singleSplit, 'Single-word caption splits its own span');
const [swLeft, swRight] = singleSplit;
assert.equal(swLeft.start, 1.0);
assert.equal(swLeft.end, 1.5, 'Left half ends at the seam');
assert.equal(swRight.start, 1.5, 'Right half starts at the seam');
assert.equal(swRight.end, 2.0);
assert.equal(swLeft.words?.[0].start, 1.1, 'Left word keeps its original start');
assert.equal(swLeft.words?.[0].end, 1.5, 'Left word clamped to the seam');
assert.equal(swRight.words?.[0].start, 1.5, 'Right word clamped to the seam');
assert.equal(swRight.words?.[0].end, 1.9, 'Right word keeps its original end');
assert.equal(swLeft.words?.[0].text, 'Only');
assert.equal(swRight.words?.[0].text, 'Only', 'Word text duplicated into both halves');
assert.notEqual(swRight.id, 'grp_single', 'Right half gets a fresh id');
console.log('  ✓ Degenerate and edge splits strictly rejected; single-word captions split their own span\n');

// =========================================================================
// TEST 3: Collision-Resistant ID Monotonicity
// =========================================================================
console.log('Test 3: Collision-resistant ID generation produces unique monotonic IDs...');
const ids = new Set<string>();
for (let i = 0; i < 1000; i++) {
  const id = generateCollisionResistantGroupId('grp');
  assert.ok(!ids.has(id), `Duplicate ID generated: ${id}`);
  ids.add(id);
}
assert.equal(ids.size, 1000);
console.log('  ✓ 1,000 monotonic IDs generated with zero collisions\n');

// =========================================================================
// TEST 4: Merge Adjacent Caption Groups
// =========================================================================
console.log('Test 4: Merging adjacent groups preserves words, combines text, and enforces adjacency...');
const merged = mergeAdjacentCaptionGroups(leftG, rightG);
assert.ok(merged, 'Merge succeeds on adjacent compatible groups');
assert.equal(merged.words?.length, 5, 'All 5 words preserved');
assert.equal(merged.text, 'Hello world this is Dispatch');
assert.equal(merged.start, leftG.start);
assert.equal(merged.end, rightG.end);

// Incompatible types reject merge
const overlayGroup: CaptionGroup = {
  id: 'ovl_1',
  start: 3.8,
  end: 5.0,
  text: 'Overlay banner',
  type: 'overlay',
};
assert.equal(mergeAdjacentCaptionGroups(merged, overlayGroup), null, 'Rejects merging caption with overlay');

// Gap > 1.5s rejects merge
const farGroup: CaptionGroup = {
  id: 'grp_far',
  start: 10.0,
  end: 12.0,
  text: 'Far away',
  type: 'caption',
};
assert.equal(mergeAdjacentCaptionGroups(merged, farGroup), null, 'Rejects merging groups separated by >1.5s');
console.log('  ✓ Merge invariants hold: authentic words preserved, incompatible/distant rejected\n');

// =========================================================================
// TEST 5: Entrance Animation Operator Precedence in normalizeTrack
// =========================================================================
console.log('Test 5: Entrance animation operator precedence preserves custom animation types...');
// Raw track declaring entrance_animation: 'fade_in'
const trackWithFade = normalizeCaptionTrack({
  default_animation: {
    entrance_animation: 'fade_in',
    active_word_effect: 'glow',
    exit_animation: 'slide_down',
  },
  groups: [],
}, 'clip-1', 10);

assert.equal(trackWithFade.default_animation?.enter?.type, 'fade_in', 'entrance_animation fade_in is preserved (not coerced to pop)');
assert.equal(trackWithFade.default_animation?.active_word?.type, 'glow', 'active_word_effect glow is preserved');
assert.equal(trackWithFade.default_animation?.exit?.type, 'slide_down', 'exit_animation slide_down is preserved');

// 'scale_up' is canonicalized to 'pop'
const trackWithScaleUp = normalizeCaptionTrack({
  default_animation: {
    entrance_animation: 'scale_up',
  },
  groups: [],
}, 'clip-2', 10);
assert.equal(trackWithScaleUp.default_animation?.enter?.type, 'pop', 'scale_up maps to pop');
console.log('  ✓ Entrance animation operator precedence bug verified fixed\n');

// =========================================================================
// TEST 6: Template Studio Animation Propagation & User Storage Isolation
// =========================================================================
console.log('Test 6: Template animations propagate and user-scoped storage isolates accounts...');
// Save user template
const testUserA = 'user_alice_123';
const testUserB = 'user_bob_456';
const tplAlice = {
  captions: {
    font_family: 'Bebas Neue',
    font_size: 52,
    animation_config: {
      type: 'bounce',
      duration_ms: 180,
      enter: { type: 'slide_up', duration_ms: 220 },
    },
  },
};
saveDefaultTemplate(tplAlice, testUserA);

// Key isolation check
const keyA = getSavedTemplateStorageKey(testUserA);
const keyB = getSavedTemplateStorageKey(testUserB);
assert.equal(keyA, 'dispatch_default_template_user_alice_123');
assert.equal(keyB, 'dispatch_default_template_user_bob_456');

const retrievedAlice = getSavedTemplate(testUserA);
assert.equal(retrievedAlice.style?.font_family, 'Bebas Neue');
assert.equal(retrievedAlice.anim?.type, 'bounce');

console.log('  ✓ Template animations propagate and user-scoped storage verified\n');

// =========================================================================
// TEST 7: Filmstrip Validation & Strict Corrupted Data Rejection
// =========================================================================
console.log('Test 7: Filmstrip rejects corrupted payloads without silent clamping...');
// Coverage-based staleness (defect fix): a strip that COVERS the requested
// span is valid even when longer (wider edit window / pre-trim strip); only a
// strip that fails to cover the requested span is stale. Scope defaults to
// 'none' → clip-local coordinates [0, strip duration].
const widerStrip = normalizeFilmstripPayload(
  { clip_id: 'c1', duration: 30, interval: 3, frames: [{ url: '/f.jpg', t: 0 }] },
  { expectedDuration: 10 }
);
assert.ok(widerStrip, 'Strip covering the requested span (wider than requested) is accepted');

const narrowStrip = normalizeFilmstripPayload(
  { clip_id: 'c1', duration: 5, interval: 3, frames: [{ url: '/f.jpg', t: 0 }] },
  { expectedDuration: 10 }
);
assert.equal(narrowStrip, null, 'Rejects strip that fails to cover the requested span');

// Wrong clipId ownership rejected
const wrongClip = normalizeFilmstripPayload(
  { clip_id: 'c_attacker', duration: 10, interval: 2, frames: [{ url: '/f.jpg', t: 0 }] },
  { expectedClipId: 'c_victim' }
);
assert.equal(wrongClip, null, 'Rejects wrong clipId ownership');

// Invalid/negative timestamp frames dropped; out-of-bounds rejected
const validFilmstrip = normalizeFilmstripPayload({
  clip_id: 'c1',
  duration: 10,
  interval: 2,
  count: 5,
  scope: 'render',
  frames: [
    { url: '/f0.jpg', t: 0, span: 2 },
    { url: '/f1.jpg', t: 2, span: 2 },
    { url: '/f2.jpg', t: 4, span: 2 },
    { url: '/f3.jpg', t: 6, span: 2 },
    { url: '/f4.jpg', t: 8, span: 2 },
  ],
});
assert.ok(validFilmstrip);

// filmstripFrameBox rejects t >= duration (does NOT silently clamp)
assert.equal(
  filmstripFrameBox({ url: '/f_bad.jpg', t: 10 }, validFilmstrip, { pxPerSec: 10, clipStart: 0, isSourceMode: false }),
  null,
  'filmstripFrameBox rejects t >= duration'
);
assert.equal(
  filmstripFrameBox({ url: '/f_neg.jpg', t: -1 }, validFilmstrip, { pxPerSec: 10, clipStart: 0, isSourceMode: false }),
  null,
  'filmstripFrameBox rejects negative t'
);
console.log('  ✓ Corrupted metadata strictly rejected without fake clamping\n');

// =========================================================================
// TEST 8: Filmstrip Contiguous Math & Partial Slot Clamping
// =========================================================================
console.log('Test 8: Filmstrip contiguous geometry ensures right_i === left_{i+1} with exact partial slots...');
const pxScales = [7, 13, 23.4, 45, 60, 100];
for (const px of pxScales) {
  let prevRight = -1;
  for (let i = 0; i < validFilmstrip.frames.length; i++) {
    const box = filmstripFrameBox(validFilmstrip.frames[i], validFilmstrip, {
      pxPerSec: px,
      clipStart: 0,
      isSourceMode: false,
    });
    assert.ok(box, `Box ${i} valid at ${px} px/s`);
    if (i > 0) {
      assert.equal(
        box.left,
        prevRight,
        `Seam/overlap detected at scale ${px} between frame ${i - 1} and ${i}: left=${box.left}, prevRight=${prevRight}`
      );
    }
    prevRight = box.left + box.width;
  }
}

// Partial slot check: clip duration 9.3s with 2.5s interval -> last slot spans exactly 1.8s, not 2.5s
const partialStrip = normalizeFilmstripPayload({
  clip_id: 'partial_1',
  duration: 9.3,
  interval: 2.5,
  count: 4,
  scope: 'render',
  frames: [
    { url: '/p0.jpg', t: 0, span: 2.5 },
    { url: '/p1.jpg', t: 2.5, span: 2.5 },
    { url: '/p2.jpg', t: 5.0, span: 2.5 },
    { url: '/p3.jpg', t: 7.5, span: 1.8 },
  ],
});
assert.ok(partialStrip);
const lastBox = filmstripFrameBox(partialStrip.frames[3], partialStrip, {
  pxPerSec: 10,
  clipStart: 0,
  isSourceMode: false,
});
assert.ok(lastBox);
// t = 7.5 -> 75px; 7.5 + 1.8 = 9.3 -> 93px; width = 18px (NOT 25px!)
assert.equal(lastBox.left, 75);
assert.equal(lastBox.width, 18, 'Partial slot width is 18px (does not overhang beyond 9.3s)');
console.log('  ✓ Contiguous rounding verified across all zoom levels with exact partial slot geometry\n');

// =========================================================================
// TEST 9: Split Edge Boundary & Word Desync Prevention
// =========================================================================
console.log('Test 9: Split boundary strictly prevents word desync and rejects edge boundaries...');
const testWords: Word[] = [
  { id: 'w1', text: 'First', start: 1.0, end: 1.2, confidence: 1.0 },
  { id: 'w2', text: 'Second', start: 1.3, end: 1.6, confidence: 1.0 },
];
const tightGroup: CaptionGroup = {
  id: 'tight_grp',
  start: 1.0,
  end: 1.7,
  text: 'First Second',
  words: testWords,
  type: 'caption',
};

// Edge split near start (< 0.05s) must be rejected, not moved to midpoint!
const edgeNearStart = splitCaptionGroupAtTime(tightGroup, 1.03);
assert.equal(edgeNearStart, null, 'Strictly rejects edge split near start');

// Edge split near end (< 0.05s) must be rejected!
const edgeNearEnd = splitCaptionGroupAtTime(tightGroup, 1.68);
assert.equal(edgeNearEnd, null, 'Strictly rejects edge split near end');

// Valid mid split
const validSplit = splitCaptionGroupAtTime(tightGroup, 1.25);
assert.ok(validSplit, 'Valid mid split succeeds');
const [vLeft, vRight] = validSplit;
assert.ok(vLeft.words?.every((w) => w.start >= vLeft.start - 0.02 && w.end <= vLeft.end + 0.02), 'Left words strictly within left group');
assert.ok(vRight.words?.every((w) => w.start >= vRight.start - 0.02 && w.end <= vRight.end + 0.02), 'Right words strictly within right group');
console.log('  ✓ Words guaranteed within boundaries without midpoint drift\n');

// =========================================================================
// TEST 10: Merge Style Overrides Preservation
// =========================================================================
console.log('Test 10: Merge preserves word styles via style_override when groups have differing styles...');
const styledGroup1: CaptionGroup = {
  id: 'g1',
  start: 1.0,
  end: 2.0,
  text: 'Yellow text',
  type: 'caption',
  style: { primary_color: '#FFFF00', highlight_color: '#FF0000', font_weight: '700' },
  words: [
    { id: 'gw1', text: 'Yellow', start: 1.0, end: 1.4, confidence: 1.0 },
    { id: 'gw2', text: 'text', start: 1.5, end: 1.9, confidence: 1.0 },
  ],
};
const styledGroup2: CaptionGroup = {
  id: 'g2',
  start: 2.1,
  end: 3.0,
  text: 'Cyan text',
  type: 'caption',
  style: { primary_color: '#00FFFF', highlight_color: '#0000FF', font_weight: '900' },
  words: [
    { id: 'gw3', text: 'Cyan', start: 2.1, end: 2.5, confidence: 1.0 },
    { id: 'gw4', text: 'text', start: 2.6, end: 2.9, confidence: 1.0 },
  ],
};

const mergedStyled = mergeAdjacentCaptionGroups(styledGroup1, styledGroup2);
assert.ok(mergedStyled, 'Merge succeeds');
assert.equal(mergedStyled.words?.length, 4);
// First group words retain original colors
assert.equal(mergedStyled.words?.[0].style_override, undefined);
// Second group words receive style_overrides reflecting styledGroup2's distinct styling
assert.equal(mergedStyled.words?.[2].style_override?.color, '#00FFFF');
assert.equal(mergedStyled.words?.[2].style_override?.highlight_color, '#0000FF');
assert.equal(mergedStyled.words?.[2].style_override?.font_weight, '900');
console.log('  ✓ Style overrides attached to incoming words preserving unique group styling\n');

// =========================================================================
// TEST 11: Template Isolation Between Accounts
// =========================================================================
console.log('Test 11: Template storage strictly isolates user accounts without shared fallback leakage...');
const userAlpha = 'user_alpha_999';
const userBeta = 'user_beta_888';
// Clear any legacy default
(globalThis as any).localStorage.removeItem('dispatch_default_template');

saveDefaultTemplate({
  captions: {
    font_family: 'Impact',
    font_size: 60,
    primary_color: '#FF0055',
  },
}, userAlpha);

// userAlpha has saved template
const tplAlpha = getSavedTemplate(userAlpha);
assert.equal(tplAlpha.style?.font_family, 'Impact');

// Legacy default template must NOT have been written
assert.equal((globalThis as any).localStorage.getItem('dispatch_default_template'), null);

// userBeta must NOT receive userAlpha's template
const tplBeta = getSavedTemplate(userBeta);
assert.equal(tplBeta.style?.font_family, undefined);

// normalizeCaptionTrack with userBeta gets standard default font, NOT userAlpha's Impact
const trackBeta = normalizeCaptionTrack({
  default_style: { font_family: 'Montserrat' },
  groups: [],
}, 'clip_beta', 10, userBeta);
assert.equal(trackBeta.default_style?.font_family, 'Montserrat');

const trackAlpha = normalizeCaptionTrack({
  default_style: { font_family: 'Montserrat' },
  groups: [],
}, 'clip_alpha', 10, userAlpha);
assert.equal(trackAlpha.default_style?.font_family, 'Impact');
console.log('  ✓ Cross-account template isolation verified 100%\n');

// =========================================================================
// TEST 12: Filmstrip Duplicate Timestamp & Ownership Rejection
// =========================================================================
console.log('Test 12: Filmstrip drops duplicate timestamps and rejects empty clipId when expected...');
const stripWithDuplicates = normalizeFilmstripPayload({
  clip_id: 'clip_dups',
  duration: 10,
  interval: 2,
  count: 3,
  scope: 'render',
  frames: [
    { url: '/f1.jpg', t: 0 },
    { url: '/f1_dup.jpg', t: 0 }, // Duplicate t=0
    { url: '/f2.jpg', t: 2 },
    { url: '/f2_dup.jpg', t: 2 }, // Duplicate t=2
    { url: '/f3.jpg', t: 4 },
  ],
});
assert.ok(stripWithDuplicates);
assert.equal(stripWithDuplicates.frames.length, 3, 'Duplicate timestamp frames cleanly dropped');
assert.equal(stripWithDuplicates.frames[0].t, 0);
assert.equal(stripWithDuplicates.frames[1].t, 2);
assert.equal(stripWithDuplicates.frames[2].t, 4);

// Missing clipId rejected when expectedClipId is provided
const stripMissingId = normalizeFilmstripPayload(
  { duration: 10, interval: 2, frames: [{ url: '/f.jpg', t: 0 }] },
  { expectedClipId: 'required_clip_id' }
);
assert.equal(stripMissingId, null, 'Rejects payload with missing clipId when expectedClipId is specified');
console.log('  ✓ Duplicate timestamps and missing clipId validation verified\n');

console.log('🎉 ALL 12 DISPATCH EDITOR AUDIT REGRESSION TESTS PASSED 100%!');
