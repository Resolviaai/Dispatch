/** Shared-fixture contract tests for TypeScript canonical rounding & fingerprinting. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  canonicalMediaSpans,
  canonicalSpansStr,
  computeMediaFingerprint,
  computeRenderFingerprint,
  fragmentLayerInterval,
  c2ToC3Time,
  c3ToC2Time,
  roundMs,
} from './src/captions/timelineEngine';

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(
  readFileSync(resolve(here, '..', 'tests', 'fixtures', 'fingerprints_v1.json'), 'utf8'),
);

console.log('🧪 Starting Dispatch Fingerprint & Canonical Rounding Shared Fixture Suite...');

// 1. Round ms vectors
for (const item of fixtures.round_ms_vectors) {
  const actual = roundMs(item.input);
  assert.ok(Math.abs(actual - item.expected) < 1e-6, `roundMs(${item.input}): expected ${item.expected}, got ${actual}`);
}
console.log('  ✓ Canonical half-up millisecond rounding passes all golden vectors');

// 2. Canonical spans vectors
for (const item of fixtures.canonical_spans_vectors) {
  const actualSpans = canonicalMediaSpans(item.ranges);
  assert.deepEqual(actualSpans, item.expected_spans, `${item.name}: spans mismatch`);
  const actualStr = canonicalSpansStr(item.ranges);
  assert.equal(actualStr, item.expected_str, `${item.name}: spans str mismatch`);
}
console.log('  ✓ Canonical media spans merging & string formatting match golden vectors');

// 3. Fingerprint vectors & split-only invariance
for (const item of fixtures.fingerprint_vectors) {
  const mediaFp = computeMediaFingerprint(item.source_asset_id, item.ranges);
  assert.equal(mediaFp, item.expected_media_fingerprint, `${item.name}: media_fingerprint mismatch`);

  const renderFp = computeRenderFingerprint(mediaFp, item.layout_mode, item.crop_anchor_x, item.layers);
  assert.equal(renderFp, item.expected_render_fingerprint, `${item.name}: render_fingerprint mismatch`);
}
console.log('  ✓ SHA-256 media and render fingerprints match Python exactly (split invariance verified!)');

// 4. Layer fragmentation
for (const item of fixtures.layer_fragmentation_vectors) {
  const actual = fragmentLayerInterval(item.layer_interval, item.deleted_gap);
  assert.deepEqual(actual, item.expected, `${item.name}: fragmentation mismatch`);
}
console.log('  ✓ All-layer interval fragmentation matches golden vectors across cut gaps');

// 5. C2/C3 time mapping
const c3 = c2ToC3Time(14.2, 700.5);
assert.equal(c3, 714.7, 'c2ToC3Time mapping error');
const c2 = c3ToC2Time(c3, 700.5);
assert.equal(c2, 14.2, 'c3ToC2Time mapping error');
console.log('  ✓ C2 proxy to C3 master source-time mapping verified');

console.log('🎉 ALL FINGERPRINT & CANONICAL ROUNDING TESTS PASSED 100%!');
