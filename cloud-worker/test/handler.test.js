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
  const res = await post("/complete", { reservation_id: RES });
  assert.equal(res.status, 200);
  assert.ok(completeCalled);
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
