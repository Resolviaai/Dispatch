/** Shared-fixture contract tests for TypeScript canonical rounding, TimelineMapper, Cloud Worker contract, & RFC-8785 fingerprinting. */
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
import * as mapper from './src/lib/timelineMapper';
import * as workerContract from '../cloud-worker/src/timelineContract';

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(
  readFileSync(resolve(here, '..', 'tests', 'fixtures', 'fingerprints_v1.json'), 'utf8'),
);

console.log('🧪 Starting Dispatch Fingerprint & Canonical Rounding Shared Fixture Suite...');

// 1. Round ms vectors (timelineEngine + timelineMapper + cloud-worker timelineContract)
for (const item of fixtures.round_ms_vectors) {
  const actual = roundMs(item.input);
  assert.ok(Math.abs(actual - item.expected) < 1e-6, `roundMs(${item.input}): expected ${item.expected}, got ${actual}`);
  assert.equal(mapper.roundMs(item.input), item.expected, `mapper.roundMs(${item.input}) mismatch`);
  assert.equal(workerContract.roundMs(item.input), item.expected, `worker.roundMs(${item.input}) mismatch`);
}
assert.equal(Object.is(mapper.roundMs(-0), 0), true, 'roundMs(-0) must normalize to +0');
assert.equal(Object.is(workerContract.roundMs(-0.0001), 0), true, 'worker roundMs(-0.0001) must normalize to +0');
console.log('  ✓ Canonical half-up millisecond rounding passes all golden vectors');

// 2. Canonical spans vectors
for (const item of fixtures.canonical_spans_vectors) {
  const actualSpans = canonicalMediaSpans(item.ranges);
  assert.deepEqual(actualSpans, item.expected_spans, `${item.name}: spans mismatch`);
  const actualStr = canonicalSpansStr(item.ranges);
  assert.equal(actualStr, item.expected_str, `${item.name}: spans str mismatch`);

  assert.deepEqual(mapper.canonicalMediaSpans(item.ranges), item.expected_spans);
  assert.equal(mapper.canonicalSpansStr(item.ranges), item.expected_str);
  assert.deepEqual(workerContract.canonicalMediaSpans(item.ranges), item.expected_spans);
  assert.equal(workerContract.canonicalSpansStr(item.ranges), item.expected_str);
}
console.log('  ✓ Canonical media spans merging & string formatting match golden vectors');

// 3. Fingerprint vectors & split-only invariance
for (const item of fixtures.fingerprint_vectors) {
  const mediaFp = computeMediaFingerprint(item.source_asset_id, item.ranges);
  assert.equal(mediaFp, item.expected_media_fingerprint, `${item.name}: media_fingerprint mismatch`);

  const renderFp = computeRenderFingerprint(mediaFp, item.layout_mode, item.crop_anchor_x, item.layers);
  assert.equal(renderFp, item.expected_render_fingerprint, `${item.name}: render_fingerprint mismatch`);

  assert.equal(mapper.computeMediaFingerprint(item.source_asset_id, item.ranges), item.expected_media_fingerprint);
  assert.equal(mapper.computeRenderFingerprint(mediaFp, item.layout_mode, item.crop_anchor_x, item.layers), item.expected_render_fingerprint);
  assert.equal(workerContract.computeMediaFingerprint(item.source_asset_id, item.ranges), item.expected_media_fingerprint);
  assert.equal(workerContract.computeRenderFingerprint(mediaFp, item.layout_mode, item.crop_anchor_x, item.layers), item.expected_render_fingerprint);
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

// 6. Wave 2A TimelineMapper & Cloud Worker Contract shared golden vectors
function verifyTimelineMapperContract(label: string, impl: typeof mapper | typeof workerContract) {
  const vec = fixtures.timeline_mapper_vectors;
  const segments = vec.segments;
  assert.equal(impl.keptDuration(segments), vec.expected_kept_duration, `${label}: keptDuration`);

  for (const c of vec.source_to_concat_cases) {
    const res = impl.sourceToConcat(impl.asSourceTime(c.source_time), segments);
    assert.equal(res.kind, c.expected.kind, `${label} ${c.name}: kind`);
    if (c.expected.kind === 'ok') {
      assert.equal((res as any).time, c.expected.time, `${label} ${c.name}: time`);
      assert.equal((res as any).segmentIndex, c.expected.segment_index, `${label} ${c.name}: segmentIndex`);
      assert.equal((res as any).snapped, false);
    } else {
      assert.equal((res as any).reason, c.expected.reason, `${label} ${c.name}: reason`);
      assert.equal((res as any).nearestSegmentStart, c.expected.nearest_segment_start, `${label} ${c.name}: nearestSegmentStart`);
      assert.equal((res as any).nearestSegmentEnd, c.expected.nearest_segment_end, `${label} ${c.name}: nearestSegmentEnd`);
      assert.equal((res as any).nearestConcatTime, c.expected.nearest_concat_time, `${label} ${c.name}: nearestConcatTime`);
    }
  }

  for (const c of vec.snap_gap_opt_in_cases) {
    const res = impl.sourceToConcat(impl.asSourceTime(c.source_time), segments, { snapGapTo: c.snap_gap_to });
    assert.equal(res.kind, 'ok', `${label} ${c.name}: snap opt-in`);
    if (res.kind === 'ok') {
      assert.equal(res.snapped, true);
      assert.equal(res.time, c.expected_time);
      assert.equal(res.segmentIndex, c.expected_segment_index);
    }
  }

  for (const c of vec.concat_to_source_cases) {
    const res = impl.concatToSource(impl.asConcatTime(c.concat_time), segments);
    assert.equal(res.kind, c.expected.kind, `${label} ${c.name}: concatToSource kind`);
    if (c.expected.kind === 'ok') {
      assert.equal((res as any).time, c.expected.time);
      assert.equal((res as any).segmentIndex, c.expected.segment_index);
    } else {
      assert.equal((res as any).reason, c.expected.reason);
      assert.equal((res as any).clampedTime, c.expected.clamped_time);
      const clamped = impl.concatToSource(impl.asConcatTime(c.concat_time), segments, { clamp: true });
      assert.equal(clamped.kind, 'ok');
      if (clamped.kind === 'ok') {
        assert.equal(clamped.clamped, true);
        assert.equal(clamped.time, c.expected.clamped_time);
      }
    }
  }

  // Roundtrip invertibility
  for (const cPt of vec.roundtrip_concat_points) {
    const sRes = impl.concatToSource(impl.asConcatTime(cPt), segments);
    assert.equal(sRes.kind, 'ok');
    if (sRes.kind === 'ok') {
      const backRes = impl.sourceToConcat(sRes.time, segments);
      assert.equal(backRes.kind, 'ok');
      if (backRes.kind === 'ok') {
        assert.equal(backRes.time, cPt, `${label}: roundtrip(${cPt})`);
      }
    }
  }

  // Edit window cases & c2/c3 bounds
  const ew = vec.edit_window_cases;
  const ws = impl.asSourceTime(ew.window_start);
  const we = impl.asSourceTime(ew.window_end);
  for (const c of ew.source_to_proxy) {
    const res = impl.sourceToProxy(impl.asSourceTime(c.source_time), ws, we);
    assert.equal(res.kind, c.expected.kind, `${label} ${c.name}`);
    if (c.expected.kind === 'ok' && res.kind === 'ok') {
      assert.equal(res.time, c.expected.time);
    } else if (res.kind === 'out_of_window') {
      assert.equal(res.reason, c.expected.reason);
      assert.equal(res.clampedTime, c.expected.clamped_time);
    }
  }
  for (const c of ew.proxy_to_source) {
    const res = impl.proxyToSource(impl.asProxyTime(c.proxy_time), ws, we);
    assert.equal(res.kind, c.expected.kind, `${label} ${c.name}`);
    if (c.expected.kind === 'ok' && res.kind === 'ok') {
      assert.equal(res.time, c.expected.time);
    } else if (res.kind === 'out_of_window') {
      assert.equal(res.reason, c.expected.reason);
      assert.equal(res.clampedTime, c.expected.clamped_time);
    }
  }
  assert.equal(impl.c2ToC3Time(impl.asProxyTime(14.2), ws, we), 714.7);
  assert.equal(impl.c3ToC2Time(impl.asSourceTime(714.7), ws, we), 14.2);
  assert.throws(() => impl.c2ToC3Time(impl.asProxyTime(-0.1), ws, we));
  assert.throws(() => impl.c2ToC3Time(impl.asProxyTime(241.0), ws, we));
  assert.throws(() => impl.c3ToC2Time(impl.asSourceTime(700.0), ws, we));
  assert.throws(() => impl.c3ToC2Time(impl.asSourceTime(941.0), ws, we));

  // PlaybackDescriptor cases
  for (const c of vec.playback_descriptor_cases) {
    const res = impl.sourceToPlaybackTime(impl.asSourceTime(c.source_time), c.descriptor);
    assert.equal(res.kind, c.expected_kind, `${label} ${c.name}`);
    if (c.expected_kind === 'ok' && res.kind === 'ok') {
      assert.equal(res.time, c.expected_playback_time);
      const rev = impl.playbackTimeToSource(res.time, c.descriptor);
      assert.equal(rev.kind, 'ok');
      if (rev.kind === 'ok') {
        assert.equal(rev.time, c.source_time);
      }
    }
  }

  // RFC-8785 fingerprint vectors (including UTF-8 multi-byte + floats + -0)
  for (const rfc of fixtures.rfc8785_fingerprint_vectors) {
    assert.equal(impl.canonicalJsonStringify(rfc.layers), rfc.expected_canonical_layers_json, `${label} ${rfc.name}: layers JCS`);
    assert.equal(impl.canonicalJsonStringify(rfc.settings), rfc.expected_canonical_settings_json, `${label} ${rfc.name}: settings JCS`);
    const mfp = impl.computeMediaFingerprint(rfc.source_asset_id, rfc.ranges);
    assert.equal(mfp, rfc.expected_media_fingerprint, `${label} ${rfc.name}: media_fp`);
    const rfp = impl.computeRenderFingerprint(mfp, rfc.layout_mode, rfc.crop_anchor_x, rfc.layers, rfc.settings);
    assert.equal(rfp, rfc.expected_render_fingerprint, `${label} ${rfc.name}: render_fp`);
  }

  // Undefined property omission & non-finite rejection
  assert.equal(impl.canonicalJsonStringify({ b: -0, a: 1, skip: undefined }), '{"a":1,"b":0}');
  assert.equal(impl.canonicalJsonStringify([1, undefined, -0]), '[1,null,0]');
  assert.throws(() => impl.canonicalJsonStringify({ x: Number.NaN }));
  assert.throws(() => impl.canonicalJsonStringify({ x: Number.POSITIVE_INFINITY }));
  assert.throws(() => impl.asSourceTime(Number.NaN));
}

verifyTimelineMapperContract('frontend/timelineMapper', mapper);
verifyTimelineMapperContract('cloud-worker/timelineContract', workerContract);

// Validate segment bounds helper exported by timelineContract / timelineMapper
assert.equal(workerContract.validateTimelineSegments([[10, 18], [22, 30]]).valid, true);
assert.equal(workerContract.validateTimelineSegments([[10, 12]]).valid, false);
console.log('  ✓ Wave 2A TimelineMapper, PlaybackDescriptor, & RFC-8785 vectors verified across Frontend TS & Cloud Worker TS');

console.log('🎉 ALL FINGERPRINT & CANONICAL ROUNDING TESTS PASSED 100%!');
