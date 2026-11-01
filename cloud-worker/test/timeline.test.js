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

test("validateReserveInput accepts kind 'render' as normalized clip_video", async () => {
  const { validateReserveInput } = await import("../src/index.ts");
  const errClipVideo = validateReserveInput("clip_video", "video/mp4", 1024 * 1024);
  assert.equal(errClipVideo, null);

  const errRender = validateReserveInput("render", "video/mp4", 1024 * 1024);
  assert.equal(errRender, null);

  const errInvalidKind = validateReserveInput("unknown_kind", "video/mp4", 1024);
  assert.equal(errInvalidKind, "unsupported kind");
});

test("drainPublishingQueue claims job atomically and dispatches publication", async () => {
  const { drainPublishingQueue } = await import("../src/index.ts");
  const sampleVideoBytes = new Uint8Array([0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70]); // mp4 header
  const mockEnv = {
    SUPABASE_URL: "https://mock.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "mock-key",
    TOKEN_VAULT_KEY: "0123456789abcdef0123456789abcdef",
    BUCKET: {
      get: async (key) => ({
        arrayBuffer: async () => sampleVideoBytes.buffer,
      }),
    },
  };

  const sampleJob = {
    id: "job-atomic-1",
    user_id: "user-1",
    clip_id: "clip-1",
    title: "Awesome Short",
    description: "Published from Android phone",
    platforms: ["youtube"],
    publish_mode: "unlisted",
    r2_key: "final/user-1/media-1",
    platform_results: {},
  };

  let claimAttempts = 0;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    const urlStr = String(url);
    if (urlStr.includes("cloud_claim_publishing_jobs")) {
      claimAttempts++;
      if (claimAttempts === 1) {
        return new Response(JSON.stringify([{ ...sampleJob, status: "processing" }]), { status: 200, headers: { "content-type": "application/json" } });
      } else {
        return new Response(JSON.stringify([]), { status: 200, headers: { "content-type": "application/json" } });
      }
    }
    if (urlStr.includes("publishing_jobs") && options.method === "PATCH") {
      claimAttempts++;
      // First attempt succeeds, concurrent second attempt returns 0 rows (already claimed)
      if (claimAttempts === 1) {
        return new Response(JSON.stringify([{ ...sampleJob, status: "processing" }]), { status: 200, headers: { "content-type": "application/json" } });
      } else {
        return new Response(JSON.stringify([]), { status: 200, headers: { "content-type": "application/json" } });
      }
    }
    if (urlStr.includes("connected_accounts")) {
      return new Response(JSON.stringify([{ id: "acc-yt-1", account_id: "yt-channel-1" }]), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (urlStr.includes("youtube.googleapis.com")) {
      return new Response(JSON.stringify({ id: "yt-video-123" }), {
        status: 200,
        headers: {
          "Location": "https://youtube.googleapis.com/upload-target",
          "content-type": "application/json",
        },
      });
    }
    return new Response(JSON.stringify([sampleJob]), { status: 200, headers: { "content-type": "application/json" } });
  };

  try {
    // Run worker 1
    const res1 = await drainPublishingQueue(mockEnv);
    assert.equal(res1.processed, 1);

    // Run concurrent worker 2 on the same queue (must be safely skipped)
    const res2 = await drainPublishingQueue(mockEnv);
    assert.equal(res2.processed, 0);
  } finally {
    globalThis.fetch = origFetch;
  }
});
