import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { buildVaultAad, encryptTokenV2 } from "../src/oauth.ts";
import worker, {
  clearProviderTokenCacheForTests,
  resetPcBrokerRateLimitsForTests,
  drainPublishingQueue,
} from "../src/index.ts";

const USER_A = "11111111-1111-1111-1111-111111111111";
const CLIP_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const MEDIA_CLIP = "44444444-4444-4444-4444-444444444444";
const MEDIA_EW = "55555555-5555-5555-5555-555555555555";
const EW_ID = "66666666-6666-6666-6666-666666666666";
const JOB_ID = "77777777-7777-7777-7777-777777777777";
const VAULT_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";

let routes;
let fetchCalls;

function pcHeaders(uid = USER_A) {
  return {
    Authorization: "Bearer worker-secret",
    "X-Dispatch-User-Id": uid,
    "Content-Type": "application/json",
  };
}

function makeStreamingBucket(totalBytes, options = {}) {
  const byteVal = options.fillByte ?? 0x41;
  const rangeCalls = [];
  const createRangeStream = (offset, length) => {
    const end = Math.min(totalBytes, offset + length);
    let sent = offset;
    return new ReadableStream({
      pull(controller) {
        if (sent >= end) {
          controller.close();
          return;
        }
        const sliceLen = Math.min(1024 * 1024, end - sent); // 1 MiB inner stream chunks
        const buf = new Uint8Array(sliceLen);
        buf.fill(byteVal);
        sent += sliceLen;
        controller.enqueue(buf);
      },
    });
  };

  return {
    rangeCalls,
    head: async (_key) => ({ size: totalBytes }),
    get: async (_key, opts) => {
      const offset = opts?.range?.offset ?? 0;
      const length = opts?.range?.length ?? totalBytes;
      rangeCalls.push({ offset, length });
      return {
        size: totalBytes,
        range: opts?.range ? { offset, length: Math.min(length, Math.max(0, totalBytes - offset)) } : undefined,
        body: createRangeStream(offset, length),
        arrayBuffer: async () => {
          throw new Error("OOM: arrayBuffer() must NOT be called for streaming R2 reads!");
        },
      };
    },
  };
}

function mockEnv(bucketOverride = null) {
  return {
    SUPABASE_URL: "https://supa.test",
    SUPABASE_SERVICE_ROLE_KEY: "srv-key",
    SUPABASE_ANON_KEY: "anon-key",
    DISPATCH_WORKER_TOKEN: "worker-secret",
    PC_OWNER_USER_IDS: USER_A,
    R2_S3_ACCESS_KEY_ID: "r2-ak",
    R2_S3_SECRET_ACCESS_KEY: "r2-sk",
    R2_BUCKET_NAME: "clips",
    R2_ACCOUNT_ID: "acct",
    TOKEN_VAULT_KEY: VAULT_KEY,
    YOUTUBE_CLIENT_ID: "yt-cid",
    YOUTUBE_CLIENT_SECRET: "yt-sec",
    OAUTH_REDIRECT_URI: "https://w.test/oauth/google/callback",
    ADMIN_EMAILS: "admin@test",
    SITE_ORIGIN: "https://site.test",
    BUCKET: bucketOverride || makeStreamingBucket(1024),
  };
}

beforeEach(() => {
  clearProviderTokenCacheForTests();
  resetPcBrokerRateLimitsForTests();
  routes = [];
  fetchCalls = [];
  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : (input instanceof URL ? input.toString() : input.url);
    const reqMethod = (typeof input === "object" && input !== null && "method" in input && input.method) ? input.method : "GET";
    const method = String(init?.method || reqMethod || "GET").toUpperCase();
    const headers = new Headers(init?.headers ?? (typeof input === "object" && input !== null && "headers" in input ? input.headers : undefined));

    let body = null;
    let bodyBytes = null;
    if (typeof init?.body === "string") {
      body = init.body;
    } else if (init?.body instanceof Uint8Array) {
      bodyBytes = init.body.byteLength;
    } else if (init?.body instanceof ArrayBuffer) {
      bodyBytes = init.body.byteLength;
    } else if (init?.body && typeof init.body.getReader === "function") {
      const reader = init.body.getReader();
      let total = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) total += value.byteLength;
      }
      bodyBytes = total;
    } else if (init?.body && typeof init.body.get === "function") {
      // FormData
      const mediaBlob = init.body.get("media");
      bodyBytes = mediaBlob && typeof mediaBlob.size === "number" ? mediaBlob.size : null;
      body = init.body;
    }

    fetchCalls.push({ url, method, headers, body, bodyBytes });
    for (const r of routes) {
      if (url.includes(r.match) && (!r.method || r.method.toUpperCase() === method)) {
        const out = await r.fn(url, { method, headers, body, bodyBytes });
        if (out instanceof Response) return out;
        const status = out?.status ?? 200;
        if (status === 204 || status === 304) {
          return new Response(null, { status, headers: out?.headers || {} });
        }
        return new Response(
          typeof out?.body === "string" ? out.body : JSON.stringify(out?.body ?? {}),
          {
            status,
            headers: { "content-type": "application/json", ...(out?.headers || {}) },
          },
        );
      }
    }
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  };
});

test("§15-cloud: /clips, /clips/:id, and /clips/:id/source-url hydrate edit_window_start and edit_window_end", async () => {
  const clipRow = {
    id: CLIP_A,
    local_clip_id: "clip_local_15",
    title: "Window Clip",
    status: "ready_review",
    start_time: 12.5,
    end_time: 28.0,
    cloud_updated_at: "2026-11-11T00:00:00Z",
    layout_mode: "fit_blur",
  };
  routes = [
    { match: "/rest/v1/clips_cloud", fn: () => ({ body: [clipRow] }) },
    { match: "/rest/v1/processing_jobs", fn: () => ({ body: [] }) },
    {
      match: "/rest/v1/media_objects",
      fn: () => ({
        body: [
          { id: MEDIA_EW, clip_id: CLIP_A, kind: "edit_window", final_key: `final/${USER_A}/ew.mp4`, byte_count: 1024, mime_type: "video/mp4" },
          { id: MEDIA_CLIP, clip_id: CLIP_A, kind: "clip_video", final_key: `final/${USER_A}/clip.mp4`, byte_count: 512, mime_type: "video/mp4" },
        ],
      }),
    },
    {
      match: "/rest/v1/edit_windows",
      fn: () => ({
        body: [{ id: EW_ID, local_clip_id: "clip_local_15", window_start: 0, window_end: 45.25, media_object_id: MEDIA_EW }],
      }),
    },
    { match: "/rest/v1/window_transcripts", fn: () => ({ body: [] }) },
    { match: "/rest/v1/publications", fn: () => ({ body: [] }) },
    { match: "/rpc/cloud_list_live_clip_commands", fn: () => ({ body: [] }) },
  ];

  // 1. GET /clips
  const listRes = await worker.fetch(new Request("https://w.test/clips", { headers: pcHeaders() }), mockEnv());
  assert.equal(listRes.status, 200);
  const listJson = await listRes.json();
  assert.equal(listJson.ready[0].edit_window_start, 0);
  assert.equal(listJson.ready[0].edit_window_end, 45.25);

  // 2. GET /clips/:id
  const detailRes = await worker.fetch(new Request("https://w.test/clips/clip_local_15", { headers: pcHeaders() }), mockEnv());
  assert.equal(detailRes.status, 200);
  const detailJson = await detailRes.json();
  assert.equal(detailJson.id, "clip_local_15");
  assert.equal(detailJson.edit_window_start, 0);
  assert.equal(detailJson.edit_window_end, 45.25);

  // 3. GET /clips/:id/source-url
  const srcRes = await worker.fetch(new Request("https://w.test/clips/clip_local_15/source-url", { headers: pcHeaders() }), mockEnv());
  assert.equal(srcRes.status, 200);
  const srcJson = await srcRes.json();
  assert.equal(srcJson.edit_window_start, 0);
  assert.equal(srcJson.edit_window_end, 45.25);
});

test("#20: POST /jobs/submit forwards p_source_type and p_session_id to cloud_submit_job", async () => {
  const rpcCalls = [];
  routes = [
    {
      match: "/rpc/cloud_submit_job",
      method: "POST",
      fn: (_u, init) => {
        rpcCalls.push(JSON.parse(init.body));
        return { body: [{ id: "job-sub-1", status: "queued" }] };
      },
    },
  ];

  const camRes = await worker.fetch(
    new Request("https://w.test/jobs/submit", {
      method: "POST",
      headers: pcHeaders(),
      body: JSON.stringify({
        url: "https://youtu.be/dQw4w9WgXcQ",
        source_type: "camera",
        session_id: "sess_phone_42",
      }),
    }),
    mockEnv(),
  );
  assert.equal(camRes.status, 200);
  assert.equal(rpcCalls[0].p_source_type, "camera");
  assert.equal(rpcCalls[0].p_session_id, "sess_phone_42");

  // Unknown source_type (e.g. youtube_self_heal) normalizes cleanly to youtube_import
  const healRes = await worker.fetch(
    new Request("https://w.test/jobs/submit", {
      method: "POST",
      headers: pcHeaders(),
      body: JSON.stringify({
        url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        source_type: "youtube_self_heal",
      }),
    }),
    mockEnv(),
  );
  assert.equal(healRes.status, 200);
  assert.equal(rpcCalls[1].p_source_type, "youtube_import");
  assert.equal(rpcCalls[1].p_session_id, null);
});

test("§31 R2 Streaming: large 18 MiB video streams in <= 8 MiB chunks without calling arrayBuffer()", async () => {
  const totalBytes = 18 * 1024 * 1024;
  const bucket = makeStreamingBucket(totalBytes);
  const ytEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({
      refresh_token: "rt-yt",
      access_token: "at-yt",
      token_expires_at: new Date(Date.now() + 3600_000).toISOString(),
    }),
    buildVaultAad(USER_A, "youtube", "UC_STREAM_1"),
  );
  const twEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({
      access_token: "at-tw",
      refresh_token: "rt-tw",
      token_expires_at: new Date(Date.now() + 3600_000).toISOString(),
    }),
    buildVaultAad(USER_A, "twitter", "TW_1"),
  );

  const ytPutRanges = [];
  const twAppendSizes = [];
  let ytChunkIdx = 0;

  routes = [
    {
      match: "/rpc/cloud_claim_publishing_jobs",
      fn: () => ({
        body: [{
          id: JOB_ID,
          user_id: USER_A,
          clip_id: CLIP_A,
          title: "Stream Test",
          description: "Large video",
          platforms: ["youtube", "twitter"],
          account_ids: { youtube: "UC_STREAM_1", twitter: "TW_1" },
          publish_mode: "private",
          r2_key: `final/${USER_A}/${MEDIA_CLIP}`,
        }],
      }),
    },
    { match: "/rest/v1/media_objects", fn: () => ({ body: [{ user_id: USER_A, kind: "clip_video" }] }) },
    { match: "/rest/v1/clips_cloud", fn: () => ({ body: [{ id: CLIP_A, local_clip_id: "local_18m" }] }) },
    {
      match: "/rpc/cloud_claim_publishing_targets",
      fn: () => ({
        body: [
          { id: "tgt-yt", job_id: JOB_ID, user_id: USER_A, platform: "youtube", account_id: "UC_STREAM_1", status: "uploading" },
          { id: "tgt-tw", job_id: JOB_ID, user_id: USER_A, platform: "twitter", account_id: "TW_1", status: "uploading" },
        ],
      }),
    },
    { match: "/rest/v1/publishing_job_targets", fn: () => ({ body: [{ id: "tgt-yt", status: "published" }, { id: "tgt-tw", status: "published" }] }) },
    { match: "/rpc/cloud_update_publishing_target", fn: () => ({ body: {} }) },
    {
      match: "/rest/v1/connected_accounts",
      fn: (u) => {
        if (u.includes("provider=eq.youtube")) {
          return { body: [{ id: "row-yt", user_id: USER_A, provider: "youtube", account_id: "UC_STREAM_1", credentials_encrypted: ytEnc, metadata: {} }] };
        }
        return { body: [{ id: "row-tw", user_id: USER_A, provider: "twitter", account_id: "TW_1", credentials_encrypted: twEnc, metadata: {} }] };
      },
    },
    {
      match: "googleapis.com/upload/youtube/v3/videos",
      method: "POST",
      fn: () => ({ status: 200, headers: { Location: "https://yt-upload.test/session-1" }, body: {} }),
    },
    {
      match: "https://yt-upload.test/session-1",
      method: "PUT",
      fn: (_u, init) => {
        ytChunkIdx++;
        ytPutRanges.push({
          contentRange: init.headers.get("Content-Range"),
          contentLength: Number(init.headers.get("Content-Length")),
          actualBytes: init.bodyBytes,
        });
        if (ytChunkIdx < 3) return new Response("", { status: 308 });
        return { status: 200, body: { id: "yt-vid-18m" } };
      },
    },
    { match: "googleapis.com/youtube/v3/videos", method: "GET", fn: () => ({ body: { items: [{ status: { uploadStatus: "processed" } }] } }) },
    { match: "api.x.com/2/media/upload/initialize", method: "POST", fn: () => ({ body: { data: { id: "tw-media-18m" } } }) },
    {
      match: "api.x.com/2/media/upload/tw-media-18m/append",
      method: "POST",
      fn: (_u, init) => {
        twAppendSizes.push(init.bodyBytes);
        return { status: 204, body: {} };
      },
    },
    { match: "api.x.com/2/media/upload/tw-media-18m/finalize", method: "POST", fn: () => ({ body: { data: { id: "tw-media-18m" } } }) },
    { match: "api.twitter.com/2/tweets", method: "POST", fn: () => ({ body: { data: { id: "tw-post-18m" } } }) },
  ];

  const stats = await drainPublishingQueue(mockEnv(bucket), { pollIntervalMs: 0 });
  assert.equal(stats.processed, 1);
  assert.equal(stats.succeeded, 1);
  assert.equal(stats.failed, 0);

  // Every R2 range fetch must be <= 8 MiB
  assert.ok(bucket.rangeCalls.length >= 7);
  for (const rc of bucket.rangeCalls) {
    assert.ok(rc.length <= 8 * 1024 * 1024, `R2 range exceeded 8 MiB: ${rc.length}`);
  }
  // YouTube received 3 chunks: 8 MiB, 8 MiB, 2 MiB
  assert.deepEqual(
    ytPutRanges.map((r) => r.actualBytes),
    [8 * 1024 * 1024, 8 * 1024 * 1024, 2 * 1024 * 1024],
  );
  assert.equal(ytPutRanges[0].contentRange, `bytes 0-${8 * 1024 * 1024 - 1}/${totalBytes}`);
  assert.equal(ytPutRanges[2].contentRange, `bytes ${16 * 1024 * 1024}-${totalBytes - 1}/${totalBytes}`);

  // Twitter/X received 4 chunks: 5 MiB, 5 MiB, 5 MiB, 3 MiB
  assert.deepEqual(
    twAppendSizes,
    [5 * 1024 * 1024, 5 * 1024 * 1024, 5 * 1024 * 1024, 3 * 1024 * 1024],
  );
});

test("§32 Multi-target /publish/queue passes p_account_ids and drainPublishingQueue executes IG, LI, FB, X with needs_reauth isolation", async () => {
  // 1. POST /publish/queue forwards normalized p_account_ids
  let enqueuedPayload = null;
  routes = [
    { match: "/rest/v1/clips_cloud", fn: () => ({ body: [{ id: CLIP_A, user_id: USER_A, local_clip_id: "local_multi" }] }) },
    { match: "/rest/v1/media_objects", fn: () => ({ body: [{ id: MEDIA_CLIP, user_id: USER_A, clip_id: CLIP_A, kind: "clip_video", final_key: `final/${USER_A}/${MEDIA_CLIP}` }] }) },
    {
      match: "/rpc/cloud_enqueue_publishing_job",
      method: "POST",
      fn: (_u, init) => {
        enqueuedPayload = JSON.parse(init.body);
        return { body: [{ id: JOB_ID, status: "queued" }] };
      },
    },
    { match: "/rpc/cloud_claim_publishing_jobs", fn: () => ({ body: [] }) },
  ];

  const qRes = await worker.fetch(
    new Request("https://w.test/publish/queue", {
      method: "POST",
      headers: pcHeaders(),
      body: JSON.stringify({
        clip_id: CLIP_A,
        title: "Multi Target Post",
        description: "Desc",
        platforms: ["youtube_shorts", "instagram", "linkedin", "x", "facebook"],
        account_ids: {
          youtube_shorts: "UC_REVOKED",
          instagram: "IG_123",
          linkedin: "urn:li:person:LI_99",
          x: "TW_77",
          facebook: "FB_PAGE_55",
        },
        publish_mode: "public",
      }),
    }),
    mockEnv(),
  );
  assert.equal(qRes.status, 200);
  assert.deepEqual(enqueuedPayload.p_platforms, ["youtube", "instagram", "linkedin", "twitter", "facebook"]);
  assert.deepEqual(enqueuedPayload.p_account_ids, {
    youtube: "UC_REVOKED",
    instagram: "IG_123",
    linkedin: "urn:li:person:LI_99",
    twitter: "TW_77",
    facebook: "FB_PAGE_55",
  });

  // 2. drainPublishingQueue with YouTube needs_reauth while IG, LI, X, FB succeed
  const igEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({
      access_token: "SECRET_IG_TOKEN",
      ig_user_id: "IG_123",
      auth_mode: "instagram_login",
      api_base: "https://graph.instagram.com/v21.0",
      token_expires_at: new Date(Date.now() + 3600_000).toISOString(),
    }),
    buildVaultAad(USER_A, "instagram", "IG_123"),
  );
  const liEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({
      access_token: "SECRET_LI_TOKEN",
      author_urn: "urn:li:person:LI_99",
      token_expires_at: new Date(Date.now() + 3600_000).toISOString(),
    }),
    buildVaultAad(USER_A, "linkedin", "urn:li:person:LI_99"),
  );
  const twEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({
      access_token: "SECRET_TW_TOKEN",
      token_expires_at: new Date(Date.now() + 3600_000).toISOString(),
    }),
    buildVaultAad(USER_A, "twitter", "TW_77"),
  );
  const fbEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({
      access_token: "SECRET_FB_PAGE_TOKEN",
      page_id: "FB_PAGE_55",
      token_expires_at: new Date(Date.now() + 3600_000).toISOString(),
    }),
    buildVaultAad(USER_A, "facebook", "FB_PAGE_55"),
  );

  const targetUpdates = [];
  const pubRows = [];
  let igPollCount = 0;
  let liCdnHadBearer = false;

  routes = [
    {
      match: "/rpc/cloud_claim_publishing_jobs",
      fn: () => ({
        body: [{
          id: JOB_ID,
          user_id: USER_A,
          clip_id: CLIP_A,
          title: "Multi Target Post",
          description: "Desc",
          platforms: ["youtube", "instagram", "linkedin", "twitter", "facebook"],
          account_ids: enqueuedPayload.p_account_ids,
          publish_mode: "public",
          r2_key: `final/${USER_A}/${MEDIA_CLIP}`,
        }],
      }),
    },
    { match: "/rest/v1/media_objects", fn: () => ({ body: [{ user_id: USER_A, kind: "clip_video" }] }) },
    { match: "/rest/v1/clips_cloud", fn: () => ({ body: [{ id: CLIP_A, local_clip_id: "local_multi" }] }) },
    {
      match: "/rpc/cloud_claim_publishing_targets",
      fn: () => ({
        body: [
          { id: "t-yt", job_id: JOB_ID, user_id: USER_A, platform: "youtube", account_id: "UC_REVOKED", status: "uploading" },
          { id: "t-ig", job_id: JOB_ID, user_id: USER_A, platform: "instagram", account_id: "IG_123", status: "uploading" },
          { id: "t-li", job_id: JOB_ID, user_id: USER_A, platform: "linkedin", account_id: "urn:li:person:LI_99", status: "uploading" },
          { id: "t-tw", job_id: JOB_ID, user_id: USER_A, platform: "twitter", account_id: "TW_77", status: "uploading" },
          { id: "t-fb", job_id: JOB_ID, user_id: USER_A, platform: "facebook", account_id: "FB_PAGE_55", status: "uploading" },
        ],
      }),
    },
    {
      match: "/rpc/cloud_update_publishing_target",
      fn: (_u, init) => {
        targetUpdates.push(JSON.parse(init.body));
        return { body: {} };
      },
    },
    {
      match: "/rest/v1/connected_accounts",
      fn: (u) => {
        if (u.includes("youtube")) {
          return { body: [{ id: "row-yt", user_id: USER_A, provider: "youtube", account_id: "UC_REVOKED", credentials_encrypted: "v2:dummy", metadata: { needs_reauth: true } }] };
        }
        if (u.includes("instagram")) {
          return { body: [{ id: "row-ig", user_id: USER_A, provider: "instagram", account_id: "IG_123", credentials_encrypted: igEnc, metadata: {} }] };
        }
        if (u.includes("linkedin")) {
          return { body: [{ id: "row-li", user_id: USER_A, provider: "linkedin", account_id: "urn:li:person:LI_99", credentials_encrypted: liEnc, metadata: {} }] };
        }
        if (u.includes("twitter")) {
          return { body: [{ id: "row-tw", user_id: USER_A, provider: "twitter", account_id: "TW_77", credentials_encrypted: twEnc, metadata: {} }] };
        }
        if (u.includes("facebook")) {
          return { body: [{ id: "row-fb", user_id: USER_A, provider: "facebook", account_id: "FB_PAGE_55", credentials_encrypted: fbEnc, metadata: {} }] };
        }
        return { body: [] };
      },
    },
    // Instagram Login host + container polling IN_PROGRESS -> FINISHED -> media_publish
    { match: "https://graph.instagram.com/v21.0/IG_123/media_publish", method: "POST", fn: () => ({ body: { id: "ig-media-99" } }) },
    { match: "https://graph.instagram.com/v21.0/IG_123/media", method: "POST", fn: () => ({ body: { id: "ig-cont-1" } }) },
    {
      match: "https://graph.instagram.com/v21.0/ig-cont-1",
      method: "GET",
      fn: () => {
        igPollCount++;
        return { body: { status_code: igPollCount >= 2 ? "FINISHED" : "IN_PROGRESS" } };
      },
    },
    { match: "https://graph.instagram.com/v21.0/ig-media-99", method: "GET", fn: () => ({ body: { permalink: "https://instagram.com/reel/ig-99" } }) },
    // LinkedIn Videos + Posts
    {
      match: "api.linkedin.com/rest/videos?action=initializeUpload",
      method: "POST",
      fn: () => ({
        body: {
          value: {
            video: "urn:li:video:12345",
            uploadToken: "tok-li-up",
            uploadInstructions: [{ uploadUrl: "https://cdn-li.test/part1", firstByte: 0, lastByte: 1023 }],
          },
        },
      }),
    },
    {
      match: "https://cdn-li.test/part1",
      method: "PUT",
      fn: (_u, init) => {
        if (init.headers.get("Authorization")) liCdnHadBearer = true;
        return { status: 200, headers: { ETag: '"etag-li-1"' }, body: "" };
      },
    },
    { match: "api.linkedin.com/rest/videos?action=finalizeUpload", method: "POST", fn: () => ({ status: 200, body: {} }) },
    { match: "api.linkedin.com/rest/videos/urn%3Ali%3Avideo%3A12345", method: "GET", fn: () => ({ body: { status: "AVAILABLE" } }) },
    {
      match: "api.linkedin.com/rest/posts",
      method: "POST",
      fn: () => new Response("", { status: 201, headers: { "x-restli-id": "urn:li:share:98765" } }),
    },
    // Twitter/X v2
    { match: "api.x.com/2/media/upload/initialize", method: "POST", fn: () => ({ body: { data: { id: "tw-media-55" } } }) },
    { match: "api.x.com/2/media/upload/tw-media-55/append", method: "POST", fn: () => ({ status: 204, body: {} }) },
    {
      match: "api.x.com/2/media/upload/tw-media-55/finalize",
      method: "POST",
      fn: () => ({ body: { data: { processing_info: { state: "pending", check_after_secs: 0 } } } }),
    },
    { match: "api.x.com/2/media/upload?command=STATUS", method: "GET", fn: () => ({ body: { processing_info: { state: "succeeded" } } }) },
    { match: "api.twitter.com/2/tweets", method: "POST", fn: () => ({ body: { data: { id: "tw-id-55" } } }) },
    // Facebook Reels
    {
      match: "https://graph.facebook.com/v21.0/FB_PAGE_55/video_reels",
      method: "POST",
      fn: (_u, init) => {
        const parsed = JSON.parse(init.body);
        if (parsed.upload_phase === "start") {
          return { body: { video_id: "fb-vid-55", upload_url: "https://rupload.facebook.com/video-upload/v21.0/fb-vid-55" } };
        }
        return { body: { success: true } };
      },
    },
    {
      match: "https://rupload.facebook.com/video-upload/v21.0/fb-vid-55",
      method: "POST",
      fn: (_u, init) => {
        assert.equal(init.headers.get("Authorization"), "OAuth SECRET_FB_PAGE_TOKEN");
        return { body: { success: true } };
      },
    },
    { match: "https://graph.facebook.com/v21.0/fb-vid-55", method: "GET", fn: () => ({ body: { status: { video_status: "ready" } } }) },
    {
      match: "/rest/v1/publications",
      method: "POST",
      fn: (_u, init) => {
        pubRows.push(JSON.parse(init.body));
        return { status: 201, body: {} };
      },
    },
  ];

  const stats = await drainPublishingQueue(mockEnv(), { pollIntervalMs: 0 });
  assert.equal(stats.processed, 1);
  assert.equal(stats.succeeded, 1);
  assert.equal(stats.failed, 0);

  // YouTube isolated to blocked_needs_auth; IG/LI/X/FB all reached processing -> published
  const ytBlocked = targetUpdates.find((u) => u.p_target_id === "t-yt" && u.p_status === "blocked_needs_auth");
  assert.ok(ytBlocked, "YouTube target must transition to blocked_needs_auth");
  assert.equal(igPollCount, 2, "Instagram must poll until FINISHED before media_publish");
  assert.equal(liCdnHadBearer, false, "LinkedIn pre-signed PUT must not send Bearer auth");

  for (const tid of ["t-ig", "t-li", "t-tw", "t-fb"]) {
    assert.ok(
      targetUpdates.some((u) => u.p_target_id === tid && u.p_status === "processing"),
      `Target ${tid} must pass through processing state`,
    );
    assert.ok(
      targetUpdates.some((u) => u.p_target_id === tid && u.p_status === "published"),
      `Target ${tid} must reach published state`,
    );
  }

  assert.equal(pubRows.length, 4);
  assert.deepEqual(pubRows.map((p) => p.platform).sort(), ["facebook", "instagram", "linkedin", "twitter"]);
  assert.ok(pubRows.every((p) => p.clip_id === CLIP_A && p.local_clip_id === "local_multi" && p.account_id));
});

test("§32 Instagram container ERROR & token-safe error sanitization", async () => {
  const igEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({ access_token: "IG_SECRET_XYZ", ig_user_id: "IG_1" }),
    buildVaultAad(USER_A, "instagram", "IG_1"),
  );
  const updates = [];
  let jobPatch = null;

  routes = [
    {
      match: "/rpc/cloud_claim_publishing_jobs",
      fn: () => ({
        body: [{
          id: JOB_ID,
          user_id: USER_A,
          clip_id: CLIP_A,
          title: "Fail Test",
          platforms: ["instagram"],
          account_ids: { instagram: "IG_1" },
          publish_mode: "private",
          r2_key: `final/${USER_A}/${MEDIA_CLIP}`,
        }],
      }),
    },
    { match: "/rest/v1/media_objects", fn: () => ({ body: [{ user_id: USER_A, kind: "clip_video" }] }) },
    { match: "/rest/v1/clips_cloud", fn: () => ({ body: [{ id: CLIP_A, local_clip_id: "local_err" }] }) },
    {
      match: "/rpc/cloud_claim_publishing_targets",
      fn: () => ({ body: [{ id: "t-ig-err", job_id: JOB_ID, user_id: USER_A, platform: "instagram", account_id: "IG_1", status: "uploading" }] }),
    },
    { match: "/rpc/cloud_update_publishing_target", fn: (_u, init) => { updates.push(JSON.parse(init.body)); return { body: {} }; } },
    { match: "/rest/v1/connected_accounts", fn: () => ({ body: [{ id: "row-ig", user_id: USER_A, provider: "instagram", account_id: "IG_1", credentials_encrypted: igEnc, metadata: {} }] }) },
    { match: "graph.facebook.com/v21.0/IG_1/media", method: "POST", fn: () => ({ body: { id: "cont-err" } }) },
    { match: "graph.facebook.com/v21.0/cont-err", method: "GET", fn: () => ({ body: { status_code: "ERROR", status: "codec unsupported Bearer IG_SECRET_XYZ" } }) },
    { match: "/rest/v1/publishing_jobs", method: "PATCH", fn: (_u, init) => { jobPatch = JSON.parse(init.body); return { body: [] }; } },
  ];

  const stats = await drainPublishingQueue(mockEnv(), { pollIntervalMs: 0 });
  assert.equal(stats.failed, 1);
  const failedUpdate = updates.find((u) => u.p_target_id === "t-ig-err" && u.p_status === "failed");
  assert.ok(failedUpdate);
  assert.ok(!String(failedUpdate.p_last_error).includes("IG_SECRET_XYZ"), "raw token must be redacted from errors");
  assert.equal(jobPatch?.status, "failed");
});
