import test from "node:test";
import assert from "node:assert/strict";
import {
  roundMs,
  canonicalMediaSpans,
  canonicalSpansStr,
  computeMediaFingerprint,
  computeRenderFingerprint,
  validateTimelineSegments,
} from "../src/index.ts";

test("roundMs rounds half-up to exact millisecond", () => {
  assert.equal(roundMs(10.0), 10.0);
  assert.equal(roundMs(10.000499), 10.0);
  assert.equal(roundMs(10.0005), 10.001);
  assert.equal(roundMs(10.0005001), 10.001);
});

test("canonicalMediaSpans merges contiguous adjacent intervals", () => {
  const merged = canonicalMediaSpans([[10.0, 20.0], [20.0, 30.0]]);
  assert.deepEqual(merged, [[10.0, 30.0]]);

  const spansStr = canonicalSpansStr([[10.0, 20.0], [20.0, 30.0]]);
  assert.equal(spansStr, "[10.000-30.000]");
});

test("computeMediaFingerprint split invariance", () => {
  const fp1 = computeMediaFingerprint("source_123", [[10.0, 30.0]]);
  const fp2 = computeMediaFingerprint("source_123", [[10.0, 20.0], [20.0, 30.0]]);
  assert.equal(fp1, fp2);
});

test("validateTimelineSegments rejects invalid durations and overlaps", () => {
  // Too short (< 5s)
  const short = validateTimelineSegments([[1.0, 4.0]]);
  assert.equal(short.valid, false);
  assert.match(short.error ?? "", /at least 5\.0s/);

  // Overlapping
  const overlap = validateTimelineSegments([[1.0, 10.0], [8.0, 15.0]]);
  assert.equal(overlap.valid, false);
  assert.match(overlap.error ?? "", /overlapping/);

  // Valid
  const valid = validateTimelineSegments([[1.0, 10.0], [10.0, 20.0]]);
  assert.equal(valid.valid, true);
  assert.equal(valid.keptDuration, 19.0);
});
