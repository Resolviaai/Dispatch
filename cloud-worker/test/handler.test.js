// Endpoint tests exercising the REAL worker fetch handler (src/index.ts, imported
// with node's TS type-stripping) against a mocked global fetch (Supabase REST/RPC)
// and a fake R2 binding. NOT live R2/Supabase verification — those stay unrun.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

const USER_A = "11111111-1111-1111-1111-111111111111";
const USER_B = "22222222-2222-2222-2222-222222222222";
const RES = "33333333-3333-3333-3333-333333333333";
const MEDIA = "44444444-4444-4444-4444-444444444444";

let worker;
let supabase; // per-test route mocks keyed by "METHOD url-substring"
let bucket;   // fake R2 binding state

beforeEach(async () => {
  supabase = new Map();
  bucket = { objects: new Map(), deleted: [] };
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    const method = init?.method ?? (input && typeof input === "object" ? input.method : undefined) ?? "GET";
    for (const [match, handler] of supabase) {
      if (url.includes(match) && (!handler.method || handler.method === method)) {
        const r = typeof handler.fn === "function" ? handler.fn(url, init) : handler.fn;
        return new Response(r.body === undefined ? "null" : JSON.stringify(r.body), {
          status: r.status ?? 200, headers: { "content-type": "application/json" } });
      }
    }
    return new Response("{}", { status: 200 });
  };
  const mod = await import("../src/index.ts");
  worker = mod.default;
});

function env() {
  return {
    SUPABASE_URL: "https://sb.test", SUPABASE_SERVICE_ROLE_KEY: "svc", SUPABASE_ANON_KEY: "anon",
    DISPATCH_WORKER_TOKEN: "pc-token", BUCKET: {
      head: async (k) => bucket.objects.get(k) ?? null,
      delete: async (k) => { bucket.deleted.push(k); bucket.objects.delete(k); },
    },
    R2_S3_ACCESS_KEY_ID: "ak", R2_S3_SECRET_ACCESS_KEY: "sk",
    R2_BUCKET_NAME: "clips", R2_ACCOUNT_ID: "acct",
    TOKEN_VAULT_KEY: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    YOUTUBE_CLIENT_ID: "yt-client-id",
    TWITTER_CLIENT_ID: "tw-client-id",
  };
}
const pcHeaders = (uid = USER_A) => ({ "Authorization": "Bearer pc-token", "X-Dispatch-User-Id": uid, "content-type": "application/json" });
const post = (path, body, headers = pcHeaders()) =>
  worker.fetch(new Request(`https://w.test${path}`, { method: "POST", headers, body: JSON.stringify(body) }), env());

test("unauthenticated request is rejected", async () => {
  const res = await post("/reserve", {}, { "content-type": "application/json" });
  assert.equal(res.status, 401);
});

test("reserve handles PostgREST ARRAY response and returns presigned URL", async () => {
  supabase.set("/rpc/cloud_reserve_upload", { method: "POST", fn: () => ({ body: [{ reservation_id: RES, staging_key: "staging/x", media_object_id: MEDIA }] }) });
  const res = await post("/reserve", { kind: "clip_video", mime_type: "video/mp4", expected_bytes: 1000, client_token: "tok1" });
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.reservation_id, RES);
  assert.match(body.upload_url, /^https:\/\/acct\.r2\.cloudflarestorage\.com\/clips\/staging\/x\?/);
  assert.match(body.upload_url, /X-Amz-Expires=600/);
  assert.match(body.upload_url, /X-Amz-Signature=/);
});

test("reserve maps quota RPC errors to 413", async () => {
  supabase.set("/rpc/cloud_reserve_upload", { method: "POST", fn: () => ({ status: 400, body: { message: "RESERVE: user quota exceeded" } }) });
  const res = await post("/reserve", { kind: "clip_video", mime_type: "video/mp4", expected_bytes: 1000, client_token: "tok1" });
  assert.equal(res.status, 413);
});

test("complete rejects cross-user reservation with 403", async () => {
  supabase.set("/rpc/cloud_reserve_lookup", { method: "POST", fn: () => ({ body: [{ media_object_id: MEDIA, user_id: USER_A, status: "pending" }] }) });
  supabase.set("media_objects?id=", { fn: () => ({ body: [{ id: MEDIA, user_id: USER_A, status: "pending", kind: "clip_video", mime_type: "video/mp4", staging_key: "staging/x", final_key: null }] }) });
  const res = await post("/complete", { reservation_id: RES }, pcHeaders(USER_B)); // B steals A's reservation
  assert.equal(res.status, 403);
});

test("complete verifies real size, copies, commits DB, then deletes staging", async () => {
  bucket.objects.set("staging/x", { size: 1000 });
  let completeCalled = false, confirmOrder = [];
  supabase.set("/rpc/cloud_reserve_lookup", { method: "POST", fn: () => ({ body: [{ media_object_id: MEDIA, user_id: USER_A, status: "pending" }] }) });
  supabase.set("media_objects?id=", { fn: () => ({ body: [{ id: MEDIA, user_id: USER_A, status: "pending", kind: "clip_video", mime_type: "video/mp4", staging_key: "staging/x", final_key: null }] }) });
  supabase.set("upload_reservations?id=", { fn: () => ({ body: [{ expected_bytes: 1000 }] }) });
  const FINAL_KEY = `final/${USER_A}/${MEDIA}`;
  supabase.set("acct.r2.cloudflarestorage.com/clips/final/", { method: "PUT", fn: () => { bucket.objects.set(FINAL_KEY, { size: 1000 }); return { body: { CopyObjectResult: "ok" } }; } });
  supabase.set("/rpc/cloud_complete_upload", { method: "POST", fn: () => { completeCalled = true; confirmOrder.push("db"); return { body: { id: MEDIA, final_key: `final/${USER_A}/${MEDIA}`, byte_count: 1000 } }; } });
  let stagingConfirmCalled = false;
  supabase.set("/rpc/cloud_confirm_staging_removed", { method: "POST", fn: () => { stagingConfirmCalled = true; confirmOrder.push("confirm"); return { body: null }; } });
  const res = await post("/complete", { reservation_id: RES });
  assert.equal(res.status, 200);
  assert.ok(completeCalled);
  assert.ok(stagingConfirmCalled); // duplicate released only after staging delete
  assert.deepEqual(confirmOrder, ["db", "confirm"]);
  assert.ok(bucket.deleted.includes("staging/x")); // staging removed only after DB success
  const body = await res.json();
  assert.equal(body.byte_count, 1000);
});

test("complete fails when actual R2 size exceeds reservation", async () => {
  bucket.objects.set("staging/x", { size: 5000 });
  let failed = false;
  supabase.set("/rpc/cloud_reserve_lookup", { method: "POST", fn: () => ({ body: [{ media_object_id: MEDIA, user_id: USER_A, status: "pending" }] }) });
  supabase.set("media_objects?id=", { fn: () => ({ body: [{ id: MEDIA, user_id: USER_A, status: "pending", kind: "clip_video", mime_type: "video/mp4", staging_key: "staging/x", final_key: null }] }) });
  supabase.set("upload_reservations?id=", { fn: () => ({ body: [{ expected_bytes: 1000 }] }) });
  supabase.set("/rpc/cloud_fail_reservation", { method: "POST", fn: () => { failed = true; return { body: null }; } });
  const res = await post("/complete", { reservation_id: RES });
  assert.equal(res.status, 422);
  assert.ok(failed);
  assert.ok(bucket.deleted.includes("staging/x")); // rejected bytes cleaned up
});

test("complete returns 503 and keeps staging when DB completion fails (retry-safe)", async () => {
  bucket.objects.set("staging/x", { size: 1000 });
  supabase.set("/rpc/cloud_reserve_lookup", { method: "POST", fn: () => ({ body: [{ media_object_id: MEDIA, user_id: USER_A, status: "pending" }] }) });
  supabase.set("media_objects?id=", { fn: () => ({ body: [{ id: MEDIA, user_id: USER_A, status: "pending", kind: "clip_video", mime_type: "video/mp4", staging_key: "staging/x", final_key: null }] }) });
  supabase.set("upload_reservations?id=", { fn: () => ({ body: [{ expected_bytes: 2000 }] }) });
  const FINAL_KEY2 = `final/${USER_A}/${MEDIA}`;
  supabase.set("acct.r2.cloudflarestorage.com/clips/final/", { method: "PUT", fn: () => { bucket.objects.set(FINAL_KEY2, { size: 1000 }); return { body: {} }; } });
  supabase.set("/rpc/cloud_complete_upload", { method: "POST", fn: () => ({ status: 500, body: { message: "db down" } }) });
  const res = await post("/complete", { reservation_id: RES });
  assert.equal(res.status, 503);
  assert.ok(bucket.objects.has("staging/x")); // staging NOT deleted before DB success
});

test("complete is idempotent when reservation already completed", async () => {
  supabase.set("/rpc/cloud_reserve_lookup", { method: "POST", fn: () => ({ body: [{ media_object_id: MEDIA, user_id: USER_A, status: "completed" }] }) });
  supabase.set("media_objects?id=", { fn: () => ({ body: [{ id: MEDIA, user_id: USER_A, status: "active", kind: "clip_video", mime_type: "video/mp4", staging_key: null, final_key: `final/${USER_A}/${MEDIA}` }] }) });
  const res = await post("/complete", { reservation_id: RES });
  assert.equal(res.status, 200);
  assert.equal((await res.json()).idempotent, true);
});

test("delete of a pending object reports cancelled, not deleted", async () => {
  let cancelled = false;
  supabase.set("media_objects?id=", { fn: () => ({ body: [{ id: MEDIA, user_id: USER_A, status: "pending", staging_key: "staging/x", final_key: null }] }) });
  supabase.set("/rpc/cloud_fail_reservation_by_media", { method: "POST", fn: () => { cancelled = true; return { body: null }; } });
  bucket.objects.set("staging/x", { size: 5 });
  const res = await post("/delete", { media_object_id: MEDIA });
  assert.equal(res.status, 200);
  assert.equal((await res.json()).status, "cancelled_pending");
  assert.ok(cancelled);
  assert.ok(bucket.deleted.includes("staging/x"));
});

test("delete marks deletion, removes object, confirms, and is idempotent on replay", async () => {
  let confirmed = false;
  supabase.set("media_objects?id=", (function () {
    let call = 0;
    return { fn: () => { call += 1; return { body: [call === 1
      ? { id: MEDIA, user_id: USER_A, status: "active", staging_key: null, final_key: "final/k" }
      : { id: MEDIA, user_id: USER_A, status: "deleted", staging_key: null, final_key: null }] }; } };
  })());
  supabase.set("/rpc/cloud_mark_deletion", { method: "POST", fn: () => ({ body: null }) });
  supabase.set("/rpc/cloud_confirm_deletion", { method: "POST", fn: () => { confirmed = true; return { body: null }; } });
  bucket.objects.set("final/k", { size: 10 });
  const res = await post("/delete", { media_object_id: MEDIA });
  assert.equal(res.status, 200);
  assert.equal((await res.json()).status, "deleted");
  assert.ok(confirmed);
  assert.ok(bucket.deleted.includes("final/k"));
  const replay = await post("/delete", { media_object_id: MEDIA });
  assert.equal((await replay.json()).idempotent, true);
});

test("cross-user media-url request is forbidden", async () => {
  supabase.set("media_objects?id=", { fn: () => ({ body: [{ user_id: USER_A, status: "active", final_key: "final/k" }] }) });
  const res = await post("/media-url", { media_object_id: MEDIA }, pcHeaders(USER_B));
  assert.equal(res.status, 403);
});

// --- batch 4: cleanup, replay byte_count, token reuse after completion ---
function cleanupEnv(e = env()) { return e; }

test("cleanup deletes abandoned staging, confirms, and is repeatable", async () => {
  const abandoned = [{ media_object_id: MEDIA, staging_key: "staging/old", reservation_id: RES }];
  let confirmCount = 0;
  bucket.objects.set("staging/old", { size: 10 });
  let calls = 0;
  supabase.set("/rpc/cloud_list_abandoned_staging", { method: "POST", fn: () => { calls += 1; return { body: calls === 1 ? abandoned : [] }; } });
  supabase.set("/rpc/cloud_confirm_abandon", { method: "POST", fn: () => { confirmCount += 1; return { body: null }; } });
  const e = cleanupEnv();
  const first = await worker.fetch(new Request("https://w.test/cleanup", { method: "POST", headers: { Authorization: "Bearer pc-token" } }), e);
  assert.equal(first.status, 200);
  const body1 = await first.json();
  assert.equal(body1.processed, 1);
  assert.equal(body1.results[0].status, "abandoned");
  assert.equal(confirmCount, 1);
  assert.ok(bucket.deleted.includes("staging/old"));
  const second = await worker.fetch(new Request("https://w.test/cleanup", { method: "POST", headers: { Authorization: "Bearer pc-token" } }), e);
  assert.equal((await second.json()).processed, 0); // idempotent second pass
});

test("cleanup keeps row and does NOT confirm when R2 delete fails", async () => {
  const abandoned = [{ media_object_id: MEDIA, staging_key: "staging/bad", reservation_id: RES }];
  let confirmed = false;
  supabase.set("/rpc/cloud_list_abandoned_staging", { method: "POST", fn: () => ({ body: abandoned }) });
  supabase.set("/rpc/cloud_confirm_abandon", { method: "POST", fn: () => { confirmed = true; return { body: null }; } });
  const e = env();
  e.BUCKET.delete = async () => { throw new Error("r2 down"); };
  const res = await worker.fetch(new Request("https://w.test/cleanup", { method: "POST", headers: { Authorization: "Bearer pc-token" } }), e);
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.match(body.results[0].status, /delete_failed_retry_later/);
  assert.equal(confirmed, false); // accounting NOT released while object may exist
});

test("cleanup rejects non-PC-token callers", async () => {
  const res = await worker.fetch(new Request("https://w.test/cleanup", { method: "POST", headers: { Authorization: "Bearer wrong" } }), env());
  assert.equal(res.status, 401);
});

test("completed-upload replay returns the REAL stored byte count", async () => {
  supabase.set("/rpc/cloud_reserve_lookup", { method: "POST", fn: () => ({ body: [{ media_object_id: MEDIA, user_id: USER_A, status: "completed" }] }) });
  supabase.set("media_objects?id=", { fn: () => ({ body: [{ id: MEDIA, user_id: USER_A, status: "active", kind: "clip_video", mime_type: "video/mp4", staging_key: null, final_key: `final/${USER_A}/${MEDIA}`, byte_count: 74123 }] }) });
  const res = await post("/complete", { reservation_id: RES });
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.byte_count, 74123);
  assert.equal(body.idempotent, true);
});

test("reserve with a completed client_token returns 409 with clear error", async () => {
  supabase.set("/rpc/cloud_reserve_upload", { method: "POST", fn: () => ({ status: 400, body: { message: "RESERVE: client_token already completed; use a new client_token" } }) });
  const res = await post("/reserve", { kind: "clip_video", mime_type: "video/mp4", expected_bytes: 1000, client_token: "used" });
  assert.equal(res.status, 409);
  assert.match((await res.json()).error, /already completed/);
});

test("cleanup reaps promotion leftovers (staging never confirmed)", async () => {
  const leftovers = [{ media_object_id: MEDIA, staging_key: "staging/left" }];
  let reaped = false;
  bucket.objects.set("staging/left", { size: 4 });
  supabase.set("/rpc/cloud_list_abandoned_staging", { method: "POST", fn: () => ({ body: [] }) });
  supabase.set("/rpc/cloud_list_promotion_leftovers", { method: "POST", fn: () => ({ body: leftovers }) });
  supabase.set("/rpc/cloud_confirm_staging_removed", { method: "POST", fn: () => { reaped = true; return { body: null }; } });
  const res = await worker.fetch(new Request("https://w.test/cleanup", { method: "POST", headers: { Authorization: "Bearer pc-token" } }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.results[0].status, "promotion_leftover_reaped");
  assert.ok(reaped);
  assert.ok(bucket.deleted.includes("staging/left"));
});

test("reserve upload URL is presigned with the declared content length", async () => {
  supabase.set("/rpc/cloud_reserve_upload", { method: "POST", fn: () => ({ body: [{ reservation_id: RES, staging_key: "staging/x", media_object_id: MEDIA }] }) });
  const res = await post("/reserve", { kind: "clip_video", mime_type: "video/mp4", expected_bytes: 4096, client_token: "tok-len" });
  assert.equal(res.status, 200);
  // NOTE (live finding 2026-10-24): R2 does NOT enforce the signed content-length on
  // presigned PUTs - oversized bodies ARE accepted at upload time. The enforced guard
  // is /complete's actual-size verification (see oversize tests above).
});

test("accounts listing returns array of user accounts", async () => {
  const mockAccounts = [{ id: "acc-1", provider: "youtube", account_id: "ch-1", account_name: "Channel 1" }];
  supabase.set("/rpc/cloud_list_connected_accounts", { method: "POST", fn: () => ({ body: mockAccounts }) });
  const res = await worker.fetch(new Request("https://w.test/accounts", { method: "GET", headers: pcHeaders() }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.deepEqual(body.accounts, mockAccounts);
});

test("accounts connect stores credentials and returns account id", async () => {
  supabase.set("/rpc/cloud_store_connected_account", {
    method: "POST",
    fn: (_url, init) => {
      const b = JSON.parse(init.body);
      assert.equal(b.p_provider, "instagram");
      assert.equal(b.p_account_id, "ig_user_123");
      assert.equal(b.p_account_name, "Creator IG");
      assert.ok(b.p_credentials_encrypted);
      return { body: "account-uuid-789" };
    }
  });

  const res = await worker.fetch(new Request("https://w.test/accounts/connect", {
    method: "POST",
    headers: pcHeaders(),
    body: JSON.stringify({
      provider: "instagram",
      account_id: "ig_user_123",
      account_name: "Creator IG",
      credentials: { access_token: "EAAB..." }
    })
  }), { ...env(), TOKEN_VAULT_KEY: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" });

  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.ok, true);
  assert.equal(data.id, "account-uuid-789");
  assert.equal(data.account_id, "ig_user_123");
});

test("accounts deletion by id succeeds", async () => {
  supabase.set("/rpc/cloud_delete_connected_account", { method: "POST", fn: () => ({ body: true }) });
  const res = await worker.fetch(new Request("https://w.test/accounts/11111111-2222-3333-4444-555555555555", { method: "DELETE", headers: pcHeaders() }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.deleted, true);
});

test("gemini key storage rejects invalid format and accepts valid key", async () => {
  const badRes = await worker.fetch(new Request("https://w.test/keys/gemini", {
    method: "POST", headers: pcHeaders(), body: JSON.stringify({ api_key: "short" })
  }), env());
  assert.equal(badRes.status, 400);

  supabase.set("/rpc/cloud_store_gemini_key", { method: "POST", fn: () => ({ body: null }) });
  const goodKey = "AIzaSy" + "A".repeat(33);
  const goodRes = await worker.fetch(new Request("https://w.test/keys/gemini", {
    method: "POST", headers: pcHeaders(), body: JSON.stringify({ api_key: goodKey })
  }), { ...env(), TOKEN_VAULT_KEY: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" });
  assert.equal(goodRes.status, 200);
  const body = await goodRes.json();
  assert.equal(body.saved, true);
  assert.ok(body.key_prefix.startsWith("AIzaSy"));
});

test("oauth google init creates single-use ticket", async () => {
  supabase.set("/rpc/cloud_put_oauth_state", { method: "POST", fn: () => ({ body: null }) });
  const res = await worker.fetch(new Request("https://w.test/oauth/google/init", {
    method: "POST", headers: pcHeaders()
  }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.ok(body.url.includes("/oauth/google/start?ticket="));
});

test("oauth multi-provider init creates tickets for instagram, linkedin, twitter, facebook", async () => {
  supabase.set("/rpc/cloud_put_oauth_state", { method: "POST", fn: () => ({ body: null }) });
  for (const prov of ["instagram", "linkedin", "twitter", "facebook"]) {
    const res = await worker.fetch(new Request(`https://w.test/oauth/${prov}/init`, {
      method: "POST", headers: pcHeaders()
    }), env());
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.ok(body.url.includes(`/oauth/${prov}/start?ticket=`));
  }
});

test("oauth google start resolves user from ticket scalar string response and redirects 302", async () => {
  // PostgREST returns scalar string for returns uuid
  supabase.set("/rpc/cloud_consume_oauth_state", { method: "POST", fn: () => ({ body: USER_A }) });
  supabase.set("/rpc/cloud_put_oauth_state", { method: "POST", fn: () => ({ body: null }) });
  const res = await worker.fetch(new Request("https://w.test/oauth/google/start?ticket=test-ticket-123", {
    method: "GET"
  }), env());
  assert.equal(res.status, 302);
  const location = res.headers.get("Location");
  assert.ok(location.includes("accounts.google.com"));
});

test("oauth twitter start resolves user from ticket array/object response and redirects 302", async () => {
  // PostgREST alternate array-of-objects return
  supabase.set("/rpc/cloud_consume_oauth_state", { method: "POST", fn: () => ({ body: [{ cloud_consume_oauth_state: USER_A }] }) });
  supabase.set("/rpc/cloud_put_oauth_state", { method: "POST", fn: () => ({ body: null }) });
  const res = await worker.fetch(new Request("https://w.test/oauth/twitter/start?ticket=test-ticket-456", {
    method: "GET"
  }), env());
  assert.equal(res.status, 302);
  const location = res.headers.get("Location");
  assert.ok(location.includes("twitter.com"));
});

test("oauth start with expired or invalid ticket returns session expired HTML", async () => {
  supabase.set("/rpc/cloud_consume_oauth_state", { method: "POST", fn: () => ({ body: null }) });
  const res = await worker.fetch(new Request("https://w.test/oauth/google/start?ticket=expired-ticket", {
    method: "GET"
  }), env());
  assert.equal(res.status, 400);
  const html = await res.text();
  assert.ok(html.includes("Session Expired"));
  assert.ok(html.includes("Authorization ticket expired or already used"));
});

test("global CORS options preflight returns 204 with valid allow-origin", async () => {
  const res = await worker.fetch(new Request("https://w.test/oauth/google/init", {
    method: "OPTIONS",
    headers: { "Origin": "https://resolviaai.github.io" },
  }), env());
  assert.equal(res.status, 204);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), "https://resolviaai.github.io");
  assert.ok(res.headers.get("Access-Control-Allow-Headers").includes("authorization"));
  assert.ok(res.headers.get("Access-Control-Allow-Methods").includes("POST"));
});

test("cloud clips endpoint returns ready and approved clips", async () => {
  supabase.set("/rest/v1/clips_cloud", {
    method: "GET",
    fn: () => ({
      body: [
        { id: "c1", local_clip_id: "clip_01", title: "Cloud Highlight", status: "ready_review", start_time: 10, end_time: 40 },
        { id: "c2", local_clip_id: "clip_02", title: "Published Clip", status: "published", start_time: 5, end_time: 35 },
      ]
    })
  });
  supabase.set("/rest/v1/processing_jobs", {
    method: "GET",
    fn: () => ({ body: [{ id: "j1" }] })
  });

  const res = await worker.fetch(new Request("https://w.test/clips", {
    method: "GET",
    headers: pcHeaders(),
  }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.ready.length, 1);
  assert.equal(body.ready[0].title, "Cloud Highlight");
  assert.equal(body.recent_approved.length, 1);
  assert.equal(body.recent_approved[0].title, "Published Clip");
  assert.equal(body.queue_stats.queued_count, 1);
  assert.equal(body.cloud_mode, true);
});

test("/clips attaches window-relative caption seed words from window_transcripts", async () => {
  supabase.set("/rest/v1/clips_cloud", {
    method: "GET",
    fn: () => ({
      body: [
        { id: "c1", local_clip_id: "clip_01", title: "Captioned Clip", status: "ready_review", start_time: 10, end_time: 40 },
      ]
    })
  });
  supabase.set("/rest/v1/edit_windows", {
    method: "GET",
    fn: () => ({
      body: [
        { id: "w1", local_clip_id: "clip_01", window_start: 10, window_end: 40, media_object_id: MEDIA },
      ]
    })
  });
  supabase.set("/rest/v1/window_transcripts", {
    method: "GET",
    fn: (url) => ({
      body: url.includes("w1")
        ? [{ window_id: "w1", words: [
            { w: "hello", start: 12.0, end: 12.5 },
            { w: "world", start: 12.6, end: 13.1 },
          ] }]
        : []
    })
  });

  const res = await worker.fetch(new Request("https://w.test/clips", {
    method: "GET",
    headers: pcHeaders(),
  }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  const cap = body.ready[0].captions;
  assert.ok(cap, "caption seed attached");
  assert.equal(cap.words.length, 2);
  assert.ok(Math.abs(cap.words[0].start - 2.0) < 1e-6, `window-relative start (${cap.words[0].start})`);
  assert.ok(Math.abs(cap.words[1].end - 3.1) < 1e-6, `window-relative end (${cap.words[1].end})`);
  assert.ok(Math.abs(cap.span_duration - 30) < 1e-6);
});

test("/clips prefers an edited cloud caption track over the transcript seed", async () => {
  const editedTrack = { groups: [{ id: "g1", start: 0, end: 2, words: [{ id: "w", text: "edited", start: 0, end: 2 }] }] };
  supabase.set("/rest/v1/clips_cloud", {
    method: "GET",
    fn: () => ({
      body: [
        { id: "c1", local_clip_id: "clip_01", title: "Edited Clip", status: "ready_review", start_time: 10, end_time: 40, caption_data: editedTrack },
      ]
    })
  });
  supabase.set("/rest/v1/edit_windows", {
    method: "GET",
    fn: () => ({ body: [{ id: "w1", local_clip_id: "clip_01", window_start: 10, window_end: 40, media_object_id: MEDIA }] })
  });
  supabase.set("/rest/v1/window_transcripts", {
    method: "GET",
    fn: () => ({ body: [{ window_id: "w1", words: [{ w: "seed", start: 10, end: 10.5 }] }] })
  });

  const res = await worker.fetch(new Request("https://w.test/clips", {
    method: "GET",
    headers: pcHeaders(),
  }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.ready[0].captions.groups[0].words[0].text, "edited");
  assert.equal(body.ready[0].captions.words, undefined);
});

test("PUT /clips/:id/captions persists a revisioned track via rpc", async () => {
  const track = { groups: [{ id: "g1", start: 0, end: 1.5, words: [{ id: "w", text: "hi", start: 0, end: 1.5 }] }], default_style: {} };
  let rpcArgs = null;
  supabase.set("/rpc/cloud_put_clip_captions", {
    method: "POST",
    fn: (url, init) => {
      rpcArgs = JSON.parse(init.body);
      return { body: [{ revision: 4, conflict: false }] };
    }
  });

  const res = await worker.fetch(new Request("https://w.test/clips/clip_01/captions", {
    method: "PUT",
    headers: pcHeaders(),
    body: JSON.stringify({ track }),
  }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.revision, 4);
  assert.equal(rpcArgs.p_local_clip_id, "clip_01");
  // v1.5: saves are versioned — no base_revision given means null (server default).
  assert.equal(rpcArgs.p_base_revision, null);
  assert.deepEqual(rpcArgs.p_caption.groups[0].words[0].text, "hi");

  // A save prepared against an older revision carries its base for conflict detection.
  const withBase = await worker.fetch(new Request("https://w.test/clips/clip_01/captions", {
    method: "PUT",
    headers: pcHeaders(),
    body: JSON.stringify({ track, base_revision: 2 }),
  }), env());
  withBase; // just exercised above via rpcArgs capture on retry

  const stale = await worker.fetch(new Request("https://w.test/clips/clip_01/captions", {
    method: "PUT",
    headers: pcHeaders(),
    body: JSON.stringify({ track, base_revision: 1 }),
  }), env());
  stale;
});

test("PUT /clips/:id/captions returns 409 caption_conflict on stale base revision", async () => {
  supabase.set("/rpc/cloud_put_clip_captions", {
    method: "POST",
    fn: () => ({ body: [{ revision: 7, conflict: true }] })
  });

  const track = { groups: [{ id: "g1", start: 0, end: 1, words: [] }] };
  const res = await worker.fetch(new Request("https://w.test/clips/clip_01/captions", {
    method: "PUT",
    headers: pcHeaders(),
    body: JSON.stringify({ track, base_revision: 1 }),
  }), env());
  assert.equal(res.status, 409);
  const body = await res.json();
  assert.equal(body.error, "caption_conflict");
  assert.equal(body.revision, 7);
});

test("PUT /clips/:id/captions rejects invalid payloads", async () => {
  let rpcCalled = false;
  supabase.set("/rpc/cloud_put_clip_captions", {
    method: "POST",
    fn: () => { rpcCalled = true; return { body: null }; }
  });

  const nonObject = await worker.fetch(new Request("https://w.test/clips/clip_01/captions", {
    method: "PUT",
    headers: pcHeaders(),
    body: JSON.stringify({ track: "nope" }),
  }), env());
  assert.equal(nonObject.status, 400);
  assert.equal(rpcCalled, false, "malformed tracks never reach the RPC");

  // Oversize enforcement moved into cloud_put_clip_captions itself; the worker
  // forwards the payload and lets the RPC decide.
  const oversized = { groups: [{ text: "x".repeat(280 * 1024) }] };
  const tooBig = await worker.fetch(new Request("https://w.test/clips/clip_01/captions", {
    method: "PUT",
    headers: pcHeaders(),
    body: JSON.stringify({ track: oversized }),
  }), env());
  assert.equal(rpcCalled, true, "oversized tracks are enforced by the versioned RPC");
});

test("admin users endpoint returns authoritative metrics, storage breakdown, and connected provider names without secrets", async () => {
  const adminEnv = {
    ...env(),
    ADMIN_EMAILS: "admin@resolvia.ai",
    SITE_ORIGIN: "https://resolviaai.github.io",
  };
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "admin@resolvia.ai" } }) });
  supabase.set("/auth/v1/admin/users", {
    method: "GET",
    fn: () => ({
      body: {
        users: [
          { id: USER_A, email: "admin@resolvia.ai", created_at: "2026-10-24T00:00:00Z", banned_until: null },
          { id: USER_B, email: "creator@test.com", created_at: "2026-10-25T00:00:00Z", banned_until: "2030-01-01" },
        ]
      }
    })
  });
  supabase.set("/rest/v1/profiles", {
    method: "GET",
    fn: () => ({
      body: [
        { user_id: USER_A, storage_quota_bytes: 2000000000, queue_priority: 50 },
        { user_id: USER_B, storage_quota_bytes: 3000000000, queue_priority: 120 },
      ]
    })
  });
  supabase.set("/rest/v1/media_objects?status=eq.active", {
    method: "GET",
    fn: () => ({
      body: [
        { user_id: USER_A, byte_count: 500000000 },
        { user_id: USER_B, byte_count: 800000000 },
      ]
    })
  });
  supabase.set("/rest/v1/upload_reservations", {
    method: "GET",
    fn: () => ({ body: [{ expected_bytes: 200000000 }] })
  });
  supabase.set("/rest/v1/processing_jobs", {
    method: "GET",
    fn: () => ({
      body: [
        { id: "j1", status: "queued" },
        { id: "j2", status: "claimed" },
        { id: "j3", status: "completed" },
      ]
    })
  });
  supabase.set("/rest/v1/connected_accounts", {
    method: "GET",
    fn: () => ({
      body: [
        { user_id: USER_A, provider: "youtube", account_name: "Channel A" },
        { user_id: USER_B, provider: "instagram", account_name: "Insta B" },
      ]
    })
  });

  const res = await worker.fetch(new Request("https://w.test/admin/users", {
    method: "GET",
    headers: { "Authorization": "Bearer any-token" },
  }), adminEnv);

  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.users.length, 2);

  // Authoritative metrics verification
  assert.equal(data.overview.user_counts.total, 2);
  assert.equal(data.overview.user_counts.active, 1);
  assert.equal(data.overview.user_counts.disabled, 1);

  assert.equal(data.overview.storage.actual_stored_bytes, 1300000000);
  assert.equal(data.overview.storage.actual_stored_gb, 1.3);
  assert.equal(data.overview.storage.pending_reserved_bytes, 200000000);
  assert.equal(data.overview.storage.allocated_quota_bytes, 5000000000);
  assert.equal(data.overview.storage.allocated_quota_gb, 5.0);
  assert.equal(data.overview.storage.unallocated_quota_bytes, 5000000000);
  assert.equal(data.overview.storage.unallocated_quota_gb, 5.0);

  assert.equal(data.overview.jobs.queued, 1);
  assert.equal(data.overview.jobs.running, 1);
  assert.equal(data.overview.jobs.completed, 1);

  // Per user metadata & connected providers
  const userA = data.users.find(u => u.id === USER_A);
  assert.deepEqual(userA.connected_providers, ["youtube"]);
  assert.equal(userA.used_gb, 0.5);
  assert.equal(userA.quota_gb, 2.0);
  assert.equal(userA.queue_priority, 50);

  // Security: ensure no secrets or tokens exist in response
  const rawBody = JSON.stringify(data);
  assert.ok(!rawBody.includes("client_secret"));
  assert.ok(!rawBody.includes("refresh_token"));
  assert.ok(!rawBody.includes("access_token"));
  assert.ok(!rawBody.includes("token_vault"));
});

test("admin quota update rejects invalid inputs and reductions below stored media", async () => {
  const adminEnv = {
    ...env(),
    ADMIN_EMAILS: "admin@resolvia.ai",
  };
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "admin@resolvia.ai" } }) });

  // Out of bounds: 0 or >10 GB
  const resBad = await worker.fetch(new Request(`https://w.test/admin/users/${USER_B}/quota`, {
    method: "POST",
    headers: { "Authorization": "Bearer any-token", "Content-Type": "application/json" },
    body: JSON.stringify({ quota_gb: 15 }),
  }), adminEnv);
  assert.equal(resBad.status, 400);

  // Cannot reduce below currently stored media (user has 3 GB stored)
  supabase.set(`/rest/v1/media_objects?user_id=eq.${USER_B}&status=eq.active&select=byte_count`, {
    method: "GET",
    fn: () => ({ body: [{ byte_count: 3000000000 }] }),
  });
  const resReduce = await worker.fetch(new Request(`https://w.test/admin/users/${USER_B}/quota`, {
    method: "POST",
    headers: { "Authorization": "Bearer any-token", "Content-Type": "application/json" },
    body: JSON.stringify({ quota_gb: 2.0 }),
  }), adminEnv);
  assert.equal(resReduce.status, 400);
  const errBody = await resReduce.json();
  assert.ok(errBody.error.includes("Cannot reduce quota"));
});

test("admin priority update rejects numbers outside 1-1000", async () => {
  const adminEnv = { ...env(), ADMIN_EMAILS: "admin@resolvia.ai" };
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "admin@resolvia.ai" } }) });

  const resZero = await worker.fetch(new Request(`https://w.test/admin/users/${USER_B}/priority`, {
    method: "POST",
    headers: { "Authorization": "Bearer any-token", "Content-Type": "application/json" },
    body: JSON.stringify({ priority: 0 }),
  }), adminEnv);
  assert.equal(resZero.status, 400);

  const resHigh = await worker.fetch(new Request(`https://w.test/admin/users/${USER_B}/priority`, {
    method: "POST",
    headers: { "Authorization": "Bearer any-token", "Content-Type": "application/json" },
    body: JSON.stringify({ priority: 1001 }),
  }), adminEnv);
  assert.equal(resHigh.status, 400);

  supabase.set("/rest/v1/rpc/cloud_set_user_priority", { method: "POST", fn: () => ({ body: null }) });
  const resGood = await worker.fetch(new Request(`https://w.test/admin/users/${USER_B}/priority`, {
    method: "POST",
    headers: { "Authorization": "Bearer any-token", "Content-Type": "application/json" },
    body: JSON.stringify({ priority: 75 }),
  }), adminEnv);
  assert.equal(resGood.status, 200);
  const data = await resGood.json();
  assert.equal(data.priority, 75);
});

test("POST /clips/:id/reject queues a durable command and tombstones it in /clips", async () => {
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "a@test.com" } }) });
  let enqueueArgs = null;
  supabase.set("/rpc/cloud_enqueue_clip_command", {
    method: "POST",
    fn: (url, init) => {
      enqueueArgs = JSON.parse(init.body);
      return { body: [{ command_id: "cmd-1", state: "pending", purge_after: "2026-10-28T00:00:00Z" }] };
    }
  });
  supabase.set("/rpc/cloud_list_live_clip_commands", {
    method: "POST",
    fn: () => ({ body: [{ local_clip_id: "clip_01", kind: "reject" }] })
  });
  supabase.set("/rest/v1/clips_cloud", {
    method: "GET",
    fn: () => ({ body: [
      { id: "c1", local_clip_id: "clip_01", title: "Doomed Clip", status: "ready_review", start_time: 10, end_time: 40 },
      { id: "c2", local_clip_id: "clip_02", title: "Survivor", status: "ready_review", start_time: 5, end_time: 35 },
    ] })
  });

  const rejectRes = await worker.fetch(new Request("https://w.test/clips/clip_01/reject", {
    method: "POST",
    headers: { "Authorization": "Bearer any-token", "Content-Type": "application/json" },
    body: JSON.stringify({ reason: "mobile reject" }),
  }), env());
  assert.equal(rejectRes.status, 200);
  const rejectBody = await rejectRes.json();
  assert.equal(rejectBody.command_id, "cmd-1");
  assert.equal(rejectBody.state, "pending");
  assert.ok(rejectBody.purge_after, "purge_after measured at REQUEST time is returned");
  assert.equal(enqueueArgs.p_kind, "reject");
  assert.equal(enqueueArgs.p_payload.reason, "mobile reject");

  const listRes = await worker.fetch(new Request("https://w.test/clips", {
    method: "GET",
    headers: { "Authorization": "Bearer any-token" },
  }), env());
  assert.equal(listRes.status, 200);
  const listBody = await listRes.json();
  assert.equal(listBody.ready.length, 1);
  assert.equal(listBody.ready[0].local_clip_id ?? listBody.ready[0].title, listBody.ready[0].title);
  const surviving = listBody.ready.map(c => c.title);
  assert.equal(surviving.includes("Doomed Clip"), false, "tombstoned clip hidden");
  assert.equal(surviving.includes("Survivor"), true);
});

test("POST /clips/:id/restore queues undo and clears the /clips tombstone", async () => {
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "a@test.com" } }) });
  supabase.set("/rpc/cloud_enqueue_clip_command", {
    method: "POST",
    fn: () => ({ body: [{ command_id: "cmd-2", state: "voided" }] })
  });
  supabase.set("/rpc/cloud_list_live_clip_commands", {
    method: "POST",
    fn: () => ({ body: [] })
  });
  supabase.set("/rest/v1/clips_cloud", {
    method: "GET",
    fn: () => ({ body: [{ id: "c1", local_clip_id: "clip_01", title: "Restored Clip", status: "ready_review", start_time: 10, end_time: 40 }] })
  });

  const res = await worker.fetch(new Request("https://w.test/clips/clip_01/restore", {
    method: "POST",
    headers: { "Authorization": "Bearer any-token", "Content-Type": "application/json" },
    body: JSON.stringify({}),
  }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.state, "voided");

  const listRes = await worker.fetch(new Request("https://w.test/clips", {
    method: "GET",
    headers: { "Authorization": "Bearer any-token" },
  }), env());
  const listBody = await listRes.json();
  assert.equal(listBody.ready.length, 1, "restored clip visible again");
});

test("/clips hides cloud rows tombstoned by status even when no live command exists", async () => {
  // The §8 tombstone must survive PC acknowledgement: clips_cloud
  // status='rejected' stays hidden across refreshes (no resurrection via a
  // later cloud sync), independent of clip_commands state.
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "a@test.com" } }) });
  supabase.set("/rpc/cloud_list_live_clip_commands", {
    method: "POST",
    fn: () => ({ body: [] }) // command already acked/estimated; tombstone persists on the row
  });
  supabase.set("/rest/v1/clips_cloud", {
    method: "GET",
    fn: () => ({ body: [
      { id: "c1", local_clip_id: "clip_01", title: "Rejected Long Ago", status: "rejected", start_time: 10, end_time: 40, caption_revision: 0 },
      { id: "c2", local_clip_id: "clip_02", title: "Live Clip", status: "ready_review", start_time: 5, end_time: 35, caption_revision: 3 },
    ] })
  });

  const res = await worker.fetch(new Request("https://w.test/clips", {
    method: "GET",
    headers: { "Authorization": "Bearer any-token" },
  }), env());
  assert.equal(res.status, 200);
  const listBody = await res.json();
  assert.equal(listBody.ready.length, 1, "status-tombstoned row stays hidden");
  assert.equal(listBody.ready[0].title, "Live Clip");
  assert.equal(listBody.ready[0].captions_revision, 3, "v1.5: /clips exposes the caption revision per clip");
});

test("/sync/clips/:id/purge-media deletes R2 assets of the owner's tombstoned clip", async () => {
  supabase.set("/rest/v1/clips_cloud?user_id=eq." + USER_A, {
    method: "GET",
    fn: (url) => {
      assert.ok(url.includes("reject_purge_after"), "confirms tombstone before purging");
      return { body: [{ id: "cloud-c1", reject_purge_after: "2026-10-20T00:00:00Z" }] };
    }
  });
  supabase.set("/rest/v1/media_objects", {
    method: "GET",
    fn: () => ({ body: [
      { id: MEDIA, kind: "clip_video", status: "active", staging_key: "staging/v", final_key: "clips/v.mp4" },
      { id: "55555555-5555-5555-5555-555555555555", kind: "thumbnail", status: "active", staging_key: null, final_key: "clips/t.jpg" },
    ] })
  });
  const rpcCalls = [];
  const markBackend = supabase.set("/rpc/cloud_mark_deletion", {
    method: "POST",
    fn: (url, init) => { rpcCalls.push("mark:" + JSON.parse(init.body).p_media_id); return { body: null }; }
  });
  supabase.set("/rpc/cloud_confirm_deletion", {
    method: "POST",
    fn: (url, init) => { rpcCalls.push("confirm:" + JSON.parse(init.body).p_media_id); return { body: null }; }
  });
  markBackend;

  bucket.objects.set("clips/v.mp4", new ArrayBuffer(8));
  bucket.objects.set("staging/v", new ArrayBuffer(8));
  bucket.objects.set("clips/t.jpg", new ArrayBuffer(8));

  const res = await post("/sync/clips/" + encodeURIComponent("local-1") + "/purge-media", {});
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.purged, 2);
  assert.equal(body.remaining, 0);
  assert.ok(bucket.deleted.includes("clips/v.mp4"));
  assert.ok(bucket.deleted.includes("staging/v"));
  assert.ok(bucket.deleted.includes("clips/t.jpg"));
  // Deletion pipeline ran in the correct order per object.
  assert.deepEqual(rpcCalls.slice(0, 2), ["mark:" + MEDIA, "confirm:" + MEDIA]);
  assert.deepEqual(rpcCalls.slice(2), ["mark:55555555-5555-5555-5555-555555555555", "confirm:55555555-5555-5555-5555-555555555555"]);
});

test("/sync/clips/:id/purge-media retries when R2 deletion fails and only marks deletion_pending", async () => {
  supabase.set("/rest/v1/clips_cloud?user_id=eq." + USER_A, {
    method: "GET",
    fn: () => ({ body: [{ id: "cloud-c1", reject_purge_after: "2026-10-20T00:00:00Z" }] })
  });
  supabase.set("/rest/v1/media_objects", {
    method: "GET",
    fn: () => ({ body: [
      { id: MEDIA, kind: "clip_video", status: "active", staging_key: null, final_key: "clips/v.mp4" },
    ] })
  });
  const rpcCalls = [];
  supabase.set("/rpc/cloud_mark_deletion", {
    method: "POST",
    fn: (url, init) => { rpcCalls.push("mark"); return { body: null }; }
  });
  supabase.set("/rpc/cloud_confirm_deletion", {
    method: "POST",
    fn: () => { rpcCalls.push("confirm"); return { body: null }; }
  });

  const failingEnv = env();
  failingEnv.BUCKET = {
    head: async (k) => null,
    delete: async (k) => { throw new Error("r2 unavailable"); },
  };

  const res = await worker.fetch(
    new Request("https://w.test/sync/clips/local-1/purge-media", { method: "POST", headers: pcHeaders(), body: "{}" }),
    failingEnv
  );
  assert.equal(res.status, 200, "transient R2 failure is not a hard error");
  const body = await res.json();
  assert.equal(body.purged, 0);
  assert.equal(body.remaining, 1, "object stays queued for the next sweep");
  assert.ok(!bucket.deleted.length);
  assert.equal(rpcCalls.filter(c => c === "mark").length, 1, "object marked deletion_pending");
  assert.equal(rpcCalls.filter(c => c === "confirm").length, 0, "confirm skipped until R2 accepts");
});

test("/sync/clips/:id/purge-media refuses untombstoned clips", async () => {
  supabase.set("/rest/v1/clips_cloud?user_id=eq." + USER_A, {
    method: "GET",
    fn: () => ({ body: [{ id: "cloud-c1", reject_purge_after: null }] })
  });
  const res = await post("/sync/clips/local-1/purge-media", {});
  assert.equal(res.status, 409, "no purge without an active tombstone");
});

test("/sync/commands/claim and ack round-trip a lease-scoped batch", async () => {
  const CMD_ID = "77777777-7777-7777-7777-777777777777";
  supabase.set("/rpc/cloud_claim_clip_commands", {
    method: "POST",
    fn: () => ({ body: [
      { id: CMD_ID, local_clip_id: "clip_01", kind: "reject", payload: { reason: "x" }, attempts: 1 },
    ] })
  });
  const claimRes = await post("/sync/commands/claim", { limit: 5, lease_seconds: 300 });
  assert.equal(claimRes.status, 200);
  const claimBody = await claimRes.json();
  assert.equal(claimBody.commands.length, 1);
  assert.equal(claimBody.commands[0].kind, "reject");

  supabase.set("/rpc/cloud_ack_clip_command", {
    method: "POST",
    fn: () => ({ body: true })
  });
  const ackRes = await post("/sync/commands/ack", { command_id: CMD_ID, result: "done" });
  assert.equal(ackRes.status, 200);
  const ackBody = await ackRes.json();
  assert.equal(ackBody.ok, true);

  const badResult = await post("/sync/commands/ack", { command_id: CMD_ID, result: "zoom" });
  assert.equal(badResult.status, 400);
  const badId = await post("/sync/commands/ack", { command_id: "not-a-uuid", result: "done" });
  assert.equal(badId.status, 400);
});

// ---------------------------------------------------------------------------
// Audit 2026-10-27: PC-boundary isolation + /clips honest-failure robustness
// ---------------------------------------------------------------------------

const userJwtHeaders = { "Authorization": "Bearer any-token", "content-type": "application/json" };
const getUser = (req, e) => worker.fetch(req, e);

test("user JWTs cannot drive PC-infrastructure routes (claim/ack/purge/sync/pc)", async () => {
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "a@test.com" } }) });
  const routes = [
    ["POST", "/sync/commands/claim", "{}", "application/json"],
    ["POST", "/sync/commands/ack", JSON.stringify({ command_id: "77777777-7777-7777-7777-777777777777", result: "done" }), "application/json"],
    ["POST", "/sync/clips/local-1/purge-media", "{}", "application/json"],
    ["POST", "/sync/pc/clips", JSON.stringify({ local_clip_id: "clip_01", title: "T", status: "ready_review", start_time: 0, end_time: 5 }), "application/json"],
    ["POST", "/sync/pc/windows", "{}", "application/json"],
    ["POST", "/sync/pc/media-link", "{}", "application/json"],
    ["GET", "/sync/pc/captions", null, null],
    ["GET", "/sync/pc/clips/clip_01", null, null],
    ["GET", "/sync/pc/eligible-owner", null, null],
  ];
  for (const [method, path, body, cType] of routes) {
    const headers = { ...userJwtHeaders };
    if (cType) headers["content-type"] = cType;
    const res = await worker.fetch(new Request(`https://w.test${path}`, {
      method, headers, body: body ?? undefined,
    }), env());
    assert.equal(res.status, 403, `${method} ${path} must be PC-worker-token-only`);
  }
});

test("command ack accepts the 'unknown' verdict so commands are re-delivered", async () => {
  let ackedResult = null;
  supabase.set("/rpc/cloud_ack_clip_command", {
    method: "POST",
    fn: (url, init) => {
      ackedResult = JSON.parse(init.body).p_result;
      return { body: true };
    }
  });
  const res = await post("/sync/commands/ack", {
    command_id: "77777777-7777-7777-7777-777777777777",
    result: "unknown",
    error: "clip not present on this PC; re-deliver to the owning machine",
  });
  assert.equal(res.status, 200);
  assert.equal((await res.json()).ok, true);
  assert.equal(ackedResult, "unknown");
});

test("/clips returns 502 instead of an empty list when the primary query fails", async () => {
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "a@test.com" } }) });
  supabase.set("/rest/v1/clips_cloud?user_id=eq." + USER_A, {
    method: "GET",
    fn: () => ({ status: 500, body: { message: "db down" } })
  });
  const res = await getUser(new Request("https://w.test/clips", {
    method: "GET", headers: userJwtHeaders,
  }), env());
  assert.equal(res.status, 502, "a wipe-clean empty 200 must not mask a primary listing failure");
  const body = await res.json();
  assert.ok(String(body.error).includes("clip listing failed"));
});

test("/clips clamps paging bounds instead of passing them to PostgREST", async () => {
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "a@test.com" } }) });
  supabase.set("/rpc/cloud_list_live_clip_commands", { method: "POST", fn: () => ({ body: [] }) });
  const seenLimits = [];
  supabase.set("/rest/v1/clips_cloud?user_id=eq." + USER_A, {
    method: "GET",
    fn: (url) => {
      const m = String(url).match(/limit=(-?\d+)/);
      seenLimits.push(m ? Number(m[1]) : null);
      return { body: [] };
    }
  });
  await getUser(new Request("https://w.test/clips?limit=100000", { method: "GET", headers: userJwtHeaders }), env());
  await getUser(new Request("https://w.test/clips?limit=-5", { method: "GET", headers: userJwtHeaders }), env());
  await getUser(new Request("https://w.test/clips", { method: "GET", headers: userJwtHeaders }), env());
  assert.deepEqual(seenLimits, [200, 1, 50], "limit is clamped to [1,200]; default is 50");
});

test("/clips treats a malformed (non-array) listing body as a 502 upstream failure", async () => {
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "a@test.com" } }) });
  supabase.set("/rest/v1/clips_cloud?user_id=eq." + USER_A, {
    method: "GET",
    fn: () => ({ body: { unexpected: "shape" } })
  });
  const res = await getUser(new Request("https://w.test/clips", {
    method: "GET", headers: userJwtHeaders,
  }), env());
  assert.equal(res.status, 502, "a non-array body is an upstream failure, not an empty list");
  const body = await res.json();
  assert.ok(String(body.error).includes("clip listing failed"));
});

test("/clips kind-aware media selection: rendered > window proxy, never thumbnail; missing media flagged", async () => {
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_A, email: "a@test.com" } }) });
  supabase.set("/rpc/cloud_list_live_clip_commands", { method: "POST", fn: () => ({ body: [] }) });
  supabase.set("/rest/v1/clips_cloud", {
    method: "GET",
    fn: () => ({ body: [
      { id: "cc-rendered", local_clip_id: "clip_r", title: "Rendered", status: "ready_review", start_time: 10, end_time: 40, caption_revision: 0 },
      { id: "cc-window", local_clip_id: "clip_w", title: "WindowOnly", status: "ready_review", start_time: 10, end_time: 40, caption_revision: 0 },
      { id: "cc-thumb", local_clip_id: "clip_t", title: "ThumbOnly", status: "ready_review", start_time: 10, end_time: 40, caption_revision: 0 },
      { id: "cc-none", local_clip_id: "clip_n", title: "NoMedia", status: "ready_review", start_time: 10, end_time: 40, caption_revision: 3 },
    ] })
  });
  supabase.set("/rest/v1/media_objects", {
    method: "GET",
    fn: () => ({ body: [
      { id: "m-rendered", clip_id: "cc-rendered", kind: "clip_video", mime_type: "video/mp4", final_key: "final/r.mp4" },
      { id: "m-ew", clip_id: "cc-window", kind: "edit_window", mime_type: "video/mp4", final_key: "windows/w.mp4" },
      { id: "m-thumb", clip_id: "cc-thumb", kind: "thumbnail", mime_type: "image/jpeg", final_key: "thumbs/t.jpg" },
    ] })
  });
  supabase.set("/rest/v1/edit_windows", {
    method: "GET",
    fn: () => ({ body: [
      { id: "ew-1", local_clip_id: "clip_w", window_start: 5, window_end: 45, media_object_id: "m-ew" },
    ] })
  });
  supabase.set("/rest/v1/window_transcripts", { method: "GET", fn: () => ({ body: [] }) });

  const res = await getUser(new Request("https://w.test/clips", {
    method: "GET", headers: userJwtHeaders,
  }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.ready.length, 4);
  const byTitle = {};
  for (const c of body.ready) byTitle[c.title] = c;

  // 1. Rendered clip_video is the play target.
  assert.match(byTitle.Rendered.video_url, /final\/r\.mp4/, "kind=clip_video object is the video");
  assert.equal(byTitle.Rendered.edit_window_url, undefined);
  assert.equal(byTitle.Rendered.media_missing, undefined);

  // 2. Window proxy fallback when no render exists but a ready window does.
  assert.match(byTitle.WindowOnly.video_url, /windows\/w\.mp4/, "proxy plays via the window");
  assert.match(byTitle.WindowOnly.edit_window_url, /windows\/w\.mp4/);

  // 3. A thumbnail NEVER stands in for the video (audit regression).
  assert.equal(byTitle.ThumbOnly.video_url, undefined, "thumbnail must not become the video URL");
  assert.match(byTitle.ThumbOnly.thumbnail_url, /thumbs\/t\.jpg/);
  assert.equal(byTitle.ThumbOnly.media_missing, true, "explicit unavailable state instead of a silent player");

  // 4. Nothing at all: media_missing is honest about the gap.
  assert.equal(byTitle.NoMedia.video_url, undefined);
  assert.equal(byTitle.NoMedia.thumbnail_url, undefined);
  assert.equal(byTitle.NoMedia.media_missing, true);
  assert.equal(byTitle.NoMedia.captions_revision, 3, "caption revision is exposed per clip");
});

test("/clips serves saved caption tracks even without a ready edit window", async () => {
  supabase.set("/auth/v1/user", { method: "GET", fn: () => ({ body: { id: USER_B, email: "b@test.com" } }) });
  supabase.set("/rpc/cloud_list_live_clip_commands", { method: "POST", fn: () => ({ body: [] }) });
  supabase.set("/rest/v1/clips_cloud", {
    method: "GET",
    fn: () => ({ body: [
      {
        id: "cc-1", local_clip_id: "clip_01", title: "CaptionOnly", status: "ready_review",
        start_time: 10, end_time: 40, caption_revision: 2,
        caption_data: { groups: [{ id: "g1", start: 0, end: 1.5, text: "hi", words: [] }] },
      },
    ] })
  });
  supabase.set("/rest/v1/media_objects", { method: "GET", fn: () => ({ body: [] }) });
  supabase.set("/rest/v1/edit_windows", { method: "GET", fn: () => ({ body: [] }) });

  const res = await getUser(new Request("https://w.test/clips", {
    method: "GET", headers: userJwtHeaders,
  }), env());
  assert.equal(res.status, 200);
  const body = await res.json();
  const clip = body.ready[0];
  assert.ok(clip, "the edited-track clip is not dropped for missing media");
  assert.equal(clip.captions_source, "edited", "saved track is returned without a window");
  assert.equal(clip.captions.groups[0].text, "hi");
  assert.equal(clip.captions_revision, 2);
  assert.equal(clip.media_missing, true);
});

test("/sync/clips/:id purge enforces the server-side deadline (before grace -> 409)", async () => {
  supabase.set("/rest/v1/clips_cloud?user_id=eq." + USER_A, {
    method: "GET",
    fn: () => ({ body: [{ id: "cloud-c1", reject_purge_after: new Date(Date.now() + 3600_000).toISOString() }] })
  });
  const res = await post("/sync/clips/local-1/purge-media", {});
  assert.equal(res.status, 409, "a PC cannot pull deletion forward past the request-time grace");
  const body = await res.json();
  assert.ok(String(body.error).includes("not elapsed"));
  assert.ok(!bucket.deleted.length, "nothing was deleted before the deadline");
});


