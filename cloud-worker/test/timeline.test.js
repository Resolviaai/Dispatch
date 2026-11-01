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
  const fp3 = computeMediaFingerprint("source_123", [
    { id: "seg-1", source_start: 10.0, source_end: 20.0 },
    { id: "seg-2", source_start: 20.0, source_end: 30.0 },
  ]);
  assert.equal(fp1, fp2);
  assert.equal(fp1, fp3);
});

test("validateTimelineSegments accepts canonical segment objects and arrays", () => {
  const objSegs = [
    { id: "s1", source_start: 1.0, source_end: 10.0 },
    { id: "s2", source_start: 10.0, source_end: 20.0 },
  ];
  const check = validateTimelineSegments(objSegs);
  assert.equal(check.valid, true);
  assert.equal(check.keptDuration, 19.0);
  assert.deepEqual(check.canonicalSegments, [
    { id: "s1", source_start: 1.0, source_end: 10.0 },
    { id: "s2", source_start: 10.0, source_end: 20.0 },
  ]);
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

test("drainPublishingQueue handles empty queue gracefully", async () => {
  const { drainPublishingQueue } = await import("../src/index.ts");
  const mockEnv = {
    SUPABASE_URL: "https://mock.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "mock-key",
    BUCKET: {
      get: async () => null,
    },
  };
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("[]", { status: 200, headers: { "content-type": "application/json" } });
  try {
    const res = await drainPublishingQueue(mockEnv);
    assert.equal(res.processed, 0);
    assert.equal(res.succeeded, 0);
    assert.equal(res.failed, 0);
  } finally {
    globalThis.fetch = origFetch;
  }
});
