/**
 * P0/P1 defect-fix regression suite (captions subsystem).
 *
 * Covers:
 * 1. normalizeTrack: outline_width 0 preserved (`??` not `||`), volume stored as
 *    percent (1 stays 1, no rescale), word is_exact preserved, track
 *    transcribe_source_mode preserved.
 * 2. timingPreservation.splitCaptionGroupAtTime: single-word caption splits,
 *    overlapping-seam splits clamp to boundaryMid, seam contract
 *    leftEnd === boundaryMid === rightStart, scrambled input still rejected.
 * 3. MiniFilmstrip.computeContextWindow: window never inverts and always
 *    contains the cut, even when the caller's totalDuration is stale,
 *    clip-relative, zero, or the cut lies outside [0, clipDuration].
 * 4. Template default-template storage keys: per-user namespacing, no legacy
 *    key writes when a user is known, legacy key only as last resort.
 * 5. useCaptionHistory: undo/redo, transient updates, mutation isolation via
 *    structuredClone, and the 50-snapshot cap (driven through the real React
 *    hook dispatcher).
 * 6. filmstrip.normalizeFilmstripPayload: coverage-based validation (a strip
 *    that covers the requested span passes; one that does not is stale).
 */

import React from 'react';
import { normalizeCaptionTrack, getSavedTemplateStorageKey, saveDefaultTemplate, getSavedTemplate } from './src/captions/normalizeTrack';
import { splitCaptionGroupAtTime } from './src/captions/timingPreservation';
import { computeContextWindow } from './src/captions/MiniFilmstrip';
import { normalizeFilmstripPayload } from './src/captions/filmstrip';
import { useCaptionHistory } from './src/captions/useCaptionHistory';
import { CaptionGroup } from './src/captions/types';

function assert(condition: boolean, msg: string) {
  if (!condition) {
    throw new Error(`Assertion failed: ${msg}`);
  }
}

console.log('\n🧪 Starting captions P0/P1 defect-fix test suite...\n');

// ---------------------------------------------------------------------------
// localStorage mock helper
// ---------------------------------------------------------------------------
type Store = Record<string, string>;
function installMockStorage(initial?: Store): { getItem: (k: string) => string | null; setItem: (k: string, v: string) => void; removeItem: (k: string) => void; _store: Store } {
  const store: Store = { ...(initial || {}) };
  const mock = {
    getItem: (k: string) => (k in store ? store[k] : null),
    setItem: (k: string, v: string) => { store[k] = String(v); },
    removeItem: (k: string) => { delete store[k]; },
    _store: store,
  };
  (global as any).localStorage = mock;
  return mock as any;
}

// ---------------------------------------------------------------------------
// Test 1: normalizeTrack preserves falsy outline_width, percent volume,
//         word is_exact and track transcribe_source_mode
// ---------------------------------------------------------------------------
console.log('Test 1: normalizeTrack outline_width 0 / volume percent / is_exact / transcribe_source_mode...');
{
  installMockStorage(); // empty: no default template interference

  const rawTrack = {
    id: 'src_track',
    transcribe_source_mode: 'voiceover_only',
    default_style: { font_size: 40 },
    groups: [
      {
        id: 'g1',
        type: 'caption',
        start: 0,
        end: 4,
        text: 'Hello world',
        volume: 1, // modern slider percent: must NOT be rescaled
        words: [
          { id: 'w0', text: 'Hello', start: 0, end: 2, is_exact: false },
          { id: 'w1', text: 'world', start: 2, end: 4 },
        ],
        style: { outline_width: 0, stroke_width: 7 },
      },
      {
        id: 'g2',
        type: 'caption',
        start: 4,
        end: 6,
        text: 'Second',
        volume: 150, // over-100 percent must survive too
        words: [{ id: 'w2', text: 'Second', start: 4, end: 6 }],
        style: { stroke_width: 7 }, // only legacy field present
      },
      {
        id: 'g3',
        type: 'caption',
        start: 6,
        end: 8,
        text: 'Third',
        words: [{ id: 'w3', text: 'Third', start: 6, end: 8 }],
      },
    ],
  };

  const normalized = normalizeCaptionTrack(rawTrack as any, 'clip1', 8);

  const g1 = normalized.groups[0];
  assert(g1.style.outline_width === 0, `outline_width 0 must be preserved, got ${g1.style.outline_width}`);
  assert(g1.volume === 1, `volume 1 (percent) must be preserved, got ${g1.volume}`);

  const g2 = normalized.groups[1];
  assert(g2.style.outline_width === 7, `stroke_width fallback should apply when outline_width absent, got ${g2.style.outline_width}`);
  assert(g2.volume === 150, `volume 150 (percent) must be preserved, got ${g2.volume}`);

  const g3 = normalized.groups[2];
  assert(g3.style.outline_width === 3, `default outline_width should be 3, got ${g3.style.outline_width}`);
  assert(g3.volume === 100, `missing volume should default to 100 percent, got ${g3.volume}`);

  const words = g1.words as any[];
  assert(words[0].is_exact === false, 'word is_exact=false must be preserved');
  assert(words[1].is_exact === undefined, 'word without is_exact must not invent one');

  assert(normalized.transcribe_source_mode === 'voiceover_only', `transcribe_source_mode must be preserved, got ${normalized.transcribe_source_mode}`);
}

// ---------------------------------------------------------------------------
// Test 2: splitCaptionGroupAtTime — single-word and overlapping-seam splits
// ---------------------------------------------------------------------------
console.log('Test 2: splitCaptionGroupAtTime 1-word split and overlap split...');
{
  // 2a. Single-word caption splits its own span and duplicates the text.
  const single: CaptionGroup = {
    id: 'g1',
    type: 'caption',
    start: 10,
    end: 14,
    text: 'Hello',
    words: [{ id: 'w0', text: 'Hello', start: 10, end: 14 }],
  } as any;

  const one = splitCaptionGroupAtTime(single, 12);
  assert(one !== null, 'single-word group must split (not reject)');
  const [l1, r1] = one!;
  assert(l1.end === 12, `left.end must be seam 12, got ${l1.end}`);
  assert(r1.start === 12, `right.start must be seam 12, got ${r1.start}`);
  assert(l1.start === 10 && r1.end === 14, 'outer bounds must be preserved');
  assert(l1.words!.length === 1 && r1.words!.length === 1, 'both halves need one word');
  assert(l1.words![0].start === 10 && l1.words![0].end === 12, 'left word clamped to seam');
  assert(r1.words![0].start === 12 && r1.words![0].end === 14, 'right word clamped to seam');
  assert(l1.words![0].text === 'Hello' && r1.words![0].text === 'Hello', 'word text duplicated into both halves');
  assert(r1.words![0].id === 'w0_b', 'right word gets a fresh id');
  assert(l1.id === 'g1' && r1.id !== 'g1', 'left keeps original id, right gets a new one');
  assert(l1.text === 'Hello' && r1.text === 'Hello', 'group text duplicated');
  // Seam contract: the seam instant is shared by both halves (leftEnd ===
  // rightStart), matching captionTimeIsActive's [start, end) boundary handling.
  assert(l1.end === r1.start, 'seam contract: leftEnd === rightStart');

  // 2b. Split request too close to the group edge is still rejected.
  assert(splitCaptionGroupAtTime(single, 10.02) === null, 'edge split (10.02) must be rejected');
  assert(splitCaptionGroupAtTime(single, 13.98) === null, 'edge split (13.98) must be rejected');

  // 2c. A too-short word span cannot be halved meaningfully.
  const tiny: CaptionGroup = {
    ...single,
    words: [{ id: 'w0', text: 'Hello', start: 10, end: 10.08 }],
  } as any;
  assert(splitCaptionGroupAtTime(tiny, 10.04) === null, 'sub-0.1s word span must be rejected');

  // 2d. Overlapping seam words: boundary = midpoint of overlap, seam words clamped.
  const overlap: CaptionGroup = {
    id: 'g2',
    type: 'caption',
    start: 10,
    end: 20,
    text: 'A B C',
    words: [
      { id: 'wa', text: 'A', start: 10, end: 12.5 },
      { id: 'wb', text: 'B', start: 12, end: 14 },
      { id: 'wc', text: 'C', start: 14, end: 16 },
    ],
  } as any;

  const two = splitCaptionGroupAtTime(overlap, 12.4);
  assert(two !== null, 'overlapping seam must clamp, not reject');
  const [l2, r2] = two!;
  assert(l2.words!.length === 1 && r2.words!.length === 2, 'split lands between A and B/C');
  assert(l2.end === 12.25, `left.end must be overlap midpoint 12.25, got ${l2.end}`);
  assert(r2.start === 12.25, `right.start must be overlap midpoint 12.25, got ${r2.start}`);
  assert(l2.words![0].end === 12.25, 'left seam word clamped onto boundaryMid');
  assert(r2.words![0].start === 12.25, 'right seam word clamped onto boundaryMid');
  assert(r2.words![0].end === 14, 'non-seam bounds untouched');
  assert(r2.words![1].start === 14 && r2.words![1].end === 16, 'trailing words untouched');
  assert(l2.end === r2.start, 'seam contract holds for overlap case');

  // 2e. Non-overlapping split leaves word timings completely untouched.
  const clean: CaptionGroup = {
    id: 'g3',
    type: 'caption',
    start: 10,
    end: 20,
    text: 'A B',
    words: [
      { id: 'wa', text: 'A', start: 10, end: 11.5 },
      { id: 'wb', text: 'B', start: 12.5, end: 14 },
    ],
  } as any;
  const three = splitCaptionGroupAtTime(clean, 12);
  assert(three !== null, 'clean multi-word split must succeed');
  const [l3, r3] = three!;
  assert(l3.words![0].end === 11.5 && l3.words![0].start === 10, 'left word timings untouched');
  assert(r3.words![0].start === 12.5 && r3.words![0].end === 14, 'right word timings untouched');
  assert(l3.end === 12 && r3.start === 12, 'group seam halves meet at boundaryMid');

  // 2f. Genuinely inconsistent (scrambled) word order is still rejected.
  const scrambled: CaptionGroup = {
    id: 'g4',
    type: 'caption',
    start: 10,
    end: 20,
    text: 'X Y',
    words: [
      { id: 'wx', text: 'X', start: 13, end: 13.5 },
      { id: 'wy', text: 'Y', start: 11, end: 12 },
    ],
  } as any;
  assert(splitCaptionGroupAtTime(scrambled, 12.25) === null, 'scrambled word sequence must be rejected');
}

// ---------------------------------------------------------------------------
// Test 3: computeContextWindow — never inverted, always contains the cut
// ---------------------------------------------------------------------------
console.log('Test 3: computeContextWindow non-inverted window...');
{
  // Normal case: padded window around the cut.
  const normal = computeContextWindow(100, 110, 200, 2.5);
  assert(normal.winStart === 97.5, `normal winStart, got ${normal.winStart}`);
  assert(normal.winEnd === 112.5, `normal winEnd, got ${normal.winEnd}`);
  assert(normal.winEnd >= normal.winStart, 'normal window must not invert');
  assert(normal.cutDuration === 10, `cutDuration, got ${normal.cutDuration}`);

  // Stale / clip-relative boundary far below the cut region: window must still
  // contain the entire cut.
  const stale = computeContextWindow(100, 110, 50, 2.5);
  assert(stale.winStart === 97.5, `stale winStart, got ${stale.winStart}`);
  assert(stale.winEnd === 110, `stale winEnd must extend to cut end, got ${stale.winEnd}`);
  assert(stale.winEnd >= stale.winStart, 'stale boundary must not invert the window');
  assert(stale.winStart <= 100 && stale.winEnd >= 110, 'window must cover the whole cut');

  // Cut entirely outside [0, clipDuration]: still sane.
  const outside = computeContextWindow(100, 110, 30, 2.5);
  assert(outside.winEnd >= outside.winStart, 'outside-context window must not invert');
  assert(outside.winEnd >= 110, 'outside-context window must still contain the cut');

  // Zero totalDuration falls back to source-bounded window.
  const zero = computeContextWindow(100, 110, 0, 2.5);
  assert(zero.winEnd === 112.5, `zero-duration fallback winEnd, got ${zero.winEnd}`);
  assert(zero.winEnd >= zero.winStart, 'zero-duration fallback must not invert');

  // Inverted caller input (sourceEnd < sourceStart) cannot invert the window.
  const inverted = computeContextWindow(100, 90, 200, 2.5);
  assert(inverted.winEnd >= inverted.winStart, 'inverted input must not invert the window');
}

// ---------------------------------------------------------------------------
// Test 4: Default-template storage keys — per-user namespacing
// ---------------------------------------------------------------------------
console.log('Test 4: template storage key namespacing...');
{
  const storage = installMockStorage();
  storage.setItem('dispatch_active_user_id', 'userA');

  assert(
    getSavedTemplateStorageKey() === 'dispatch_default_template_userA',
    `active-user key resolution, got ${getSavedTemplateStorageKey()}`
  );
  assert(
    getSavedTemplateStorageKey('  userB  ') === 'dispatch_default_template_userB',
    'explicit userId must be trimmed and win over active user'
  );

  // Saving for an explicit user writes ONLY the namespaced key.
  saveDefaultTemplate({ captions: { font_size: 77, primary_color: '#FF0000', position_y: 0.9 } }, 'userB');
  assert(storage.getItem('dispatch_default_template_userB') !== null, 'namespaced key written');
  assert(storage.getItem('dispatch_default_template') === null, 'legacy key must NOT be written when userId is known');
  assert(storage.getItem('dispatch_default_template_userA') === null, 'other user\'s key must not be touched');

  // Read-back is namespaced and isolated.
  const tplB = getSavedTemplate('userB');
  assert(tplB.style?.font_size === 77, `namespaced read-back font_size, got ${tplB.style?.font_size}`);
  assert(tplB.layout?.position_y === 0.9, `namespaced read-back position_y, got ${tplB.layout?.position_y}`);
  const tplA = getSavedTemplate('userA');
  assert(Object.keys(tplA).length === 0 || (Object.keys(tplA.style || {}).length === 0 && Object.keys(tplA.layout || {}).length === 0), 'userA must not see userB template');

  // No user anywhere: legacy key is the last resort, and a save writes it.
  storage.removeItem('dispatch_active_user_id');
  assert(getSavedTemplateStorageKey() === 'dispatch_default_template', 'legacy key is the fallback with no user');
  saveDefaultTemplate({ captions: { font_size: 55 } });
  assert(storage.getItem('dispatch_default_template') !== null, 'legacy save writes legacy key');

  const legacyTpl = getSavedTemplate();
  assert(legacyTpl.style?.font_size === 55, `legacy read-back font_size, got ${legacyTpl.style?.font_size}`);

  // sb_user_id is honored as a secondary active-user source.
  storage.removeItem('dispatch_default_template');
  storage.setItem('sb_user_id', 'userC');
  assert(getSavedTemplateStorageKey() === 'dispatch_default_template_userC', 'sb_user_id fallback resolution');
}

// ---------------------------------------------------------------------------
// Test 5: useCaptionHistory — undo/redo with structuredClone, transient
//         updates, mutation isolation, 50-snapshot cap
// ---------------------------------------------------------------------------
console.log('Test 5: useCaptionHistory undo/redo with structuredClone...');
{
  const dispatcherHost = (React as any).__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentDispatcher;
  assert(dispatcherHost, 'React secret internals available');

  const slots: any[] = [];
  let hookIndex = 0;
  let result: any;

  const depsEqual = (a: any[] | undefined, b: any[] | undefined) =>
    a === b || (!!a && !!b && a.length === b.length && a.every((x, i) => x === b[i]));

  const impl: any = {
    useState(init: any) {
      const i = hookIndex++;
      if (!slots[i]) slots[i] = { value: typeof init === 'function' ? init() : init, queue: [] };
      const slot = slots[i];
      while (slot.queue.length) {
        const action = slot.queue.shift();
        slot.value = typeof action === 'function' ? action(slot.value) : action;
      }
      return [slot.value, (action: any) => { slot.queue.push(action); }];
    },
    useRef(init: any) {
      const i = hookIndex++;
      if (!slots[i]) slots[i] = { current: init };
      return slots[i];
    },
    useCallback(fn: any, deps: any[]) {
      const i = hookIndex++;
      if (!slots[i] || !depsEqual(slots[i].deps, deps)) slots[i] = { fn, deps };
      return slots[i].fn;
    },
    useMemo(fn: any, deps: any[]) {
      const i = hookIndex++;
      if (!slots[i] || !depsEqual(slots[i].deps, deps)) slots[i] = { v: fn(), deps };
      return slots[i].v;
    },
    useEffect() {},
    useLayoutEffect() {},
    useInsertionEffect() {},
  };

  function render() {
    hookIndex = 0;
    dispatcherHost.current = impl;
    try {
      result = (useCaptionHistory as any)({ id: 't', groups: [], marker: 'v0' });
    } finally {
      dispatcherHost.current = null;
    }
  }

  function flush() {
    for (let guard = 0; guard < 200; guard++) {
      if (!slots.some((s) => s.queue && s.queue.length > 0)) return;
      render();
    }
  }

  const mk = (marker: string) => ({ id: 't', groups: [], marker }) as any;

  render();

  // Initial state
  assert(result.track.marker === 'v0', 'initial track present');
  assert(result.canUndo === false && result.canRedo === false, 'no undo/redo at start');

  // Committed updates push history
  result.setTrack(mk('p1'), true);
  result.setTrack(mk('p2'), true);
  flush();
  assert(result.track.marker === 'p2' && result.canUndo === true, 'commit updates track and canUndo');

  // Transient update: live preview only, no history entry
  result.setTrack(mk('transient'), false);
  flush();
  assert(result.track.marker === 'transient', 'transient update applies live');
  assert(result.undo()!.marker === 'p1', 'undo skips the transient snapshot');
  flush();
  assert(result.track.marker === 'p1', 'undo restores p1');
  assert(result.redo()!.marker === 'p2', 'redo returns to p2');
  flush();
  assert(result.track.marker === 'p2', 'redo restores p2');

  // Undo all the way back
  assert(result.undo()!.marker === 'p1', 'undo to p1');
  flush();
  assert(result.undo()!.marker === 'v0', 'undo to initial');
  flush();
  assert(result.undo() === null, 'undo at bottom returns null');
  flush();
  assert(result.track.marker === 'v0' && result.canUndo === false, 'bottom reached');
  assert(result.redo()!.marker === 'p1', 'redo re-walks forward');
  flush();

  // Mutation isolation: caller mutating the object after setTrack must not
  // corrupt the stored snapshot (structuredClone at push).
  const mutating = mk('p3');
  result.setTrack(mutating, true);
  mutating.marker = 'MUTATED';
  flush();
  assert(result.track.marker === 'p3', 'committed value wins over post-hoc mutation');
  assert(result.undo()!.marker === 'p1', 'history snapshot isolated from caller mutation');
  flush();
  const tampered = result.track;
  tampered.marker = 'TAMPERED';
  assert(result.redo()!.marker === 'p3', 'redo snapshot isolated from tampered undo result');
  flush();
  assert(result.track.marker === 'p3', 'redo restores clean p3');

  // 50-snapshot cap: 60 pushes keep only the last 50 snapshots.
  slots.length = 0;
  render();
  for (let i = 1; i <= 60; i++) {
    result.setTrack(mk(`s${i}`), true);
  }
  flush();
  assert(result.track.marker === 's60', 'latest commit is live');
  let undoCount = 0;
  while (undoCount < 300) {
    const r = result.undo();
    flush();
    if (r === null) break;
    undoCount++;
  }
  assert(undoCount === 49, `50-snapshot cap allows exactly 49 undos, got ${undoCount}`);
  assert(result.track.marker === 's11', `oldest surviving snapshot is s11, got ${result.track.marker}`);
}

// ---------------------------------------------------------------------------
// Test 6: normalizeFilmstripPayload — coverage-based validation
// ---------------------------------------------------------------------------
console.log('Test 6: filmstrip payload coverage validation...');
{
  const validStrip = {
    clip_id: 'abc',
    duration: 20,
    interval: 5,
    count: 4,
    scope: 'source',
    source_start: 100,
    frames: [
      { url: '/f0.jpg', t: 0, span: 5 },
      { url: '/f1.jpg', t: 5, span: 5 },
      { url: '/f2.jpg', t: 10, span: 5 },
      { url: '/f3.jpg', t: 15, span: 5 },
    ],
  };

  // Strip [100,120] covers requested [100,115]: stale-by-old-equality, valid now.
  const covered = normalizeFilmstripPayload(validStrip, { expectedClipId: 'abc', expectedClipStart: 100, expectedDuration: 15 });
  assert(covered !== null, 'strip covering the requested span must be accepted');

  // Strip [100,120] vs requested [100,130]: does not cover → stale.
  const uncovered = normalizeFilmstripPayload(validStrip, { expectedClipId: 'abc', expectedClipStart: 100, expectedDuration: 30 });
  assert(uncovered === null, 'strip failing to cover the requested span must be rejected');

  // Strip origin earlier than the requested clip start is legitimate context
  // (strip [100,120] requested [110,115]) → accepted.
  const contextBefore = normalizeFilmstripPayload(validStrip, { expectedClipId: 'abc', expectedClipStart: 110, expectedDuration: 5 });
  assert(contextBefore !== null, 'strip starting before the requested clip start provides context and is accepted');

  // Strip origin starts AFTER the requested clip start: wrong clip window.
  const lateStrip = { ...validStrip, source_start: 120 };
  const shifted = normalizeFilmstripPayload(lateStrip, { expectedClipId: 'abc', expectedClipStart: 100, expectedDuration: 5 });
  assert(shifted === null, 'strip starting after the requested clip start must be rejected');
}

console.log('\n✅ All captions P0/P1 defect-fix tests passed.\n');
