import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import {
  buildVaultAad,
  encryptToken,
  encryptTokenV2,
  decryptToken,
  decryptTokenWithMigration,
} from "../src/oauth.ts";
import worker, {
  ProviderAuthError,
  getValidProviderToken,
  clearProviderTokenCacheForTests,
  resetPcBrokerRateLimitsForTests,
  drainPublishingQueue,
} from "../src/index.ts";

const USER_A = "11111111-1111-1111-1111-111111111111";
const USER_B = "22222222-2222-2222-2222-222222222222";
const USER_OUTSIDER = "99999999-9999-9999-9999-999999999999";
const CLIP_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const CLIP_OTHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const MEDIA_A = "44444444-4444-4444-4444-444444444444";
const ROW_1 = "55555555-5555-5555-5555-555555555555";
const VAULT_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";

let routes;
let fetchCalls;
let bucketObjects;

beforeEach(() => {
  clearProviderTokenCacheForTests();
  resetPcBrokerRateLimitsForTests();
  routes = [];
  fetchCalls = [];
  bucketObjects = new Map();
  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : (input instanceof URL ? input.toString() : input.url);
    const reqMethod = (typeof input === "object" && input !== null && "method" in input && input.method) ? input.method : "GET";
    const method = String(init?.method || reqMethod || "GET").toUpperCase();
    const headers = new Headers(init?.headers ?? (typeof input === "object" && input !== null && "headers" in input ? input.headers : undefined));
    const body = typeof init?.body === "string" ? init.body : (init?.body ? String(init.body) : null);
    fetchCalls.push({ url, method, headers, body });
    for (const r of routes) {
      if (url.includes(r.match) && (!r.method || r.method.toUpperCase() === method)) {
        const res = typeof r.fn === "function" ? await r.fn(url, { method, headers, body }) : r.fn;
        return new Response(res.body === undefined ? "null" : JSON.stringify(res.body), {
          status: res.status ?? 200,
          headers: { "content-type": "application/json", ...(res.headers || {}) },
        });
      }
    }
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  };
});

function mockEnv(overrides = {}) {
  return {
    SUPABASE_URL: "https://sb.test",
    SUPABASE_SERVICE_ROLE_KEY: "svc-secret",
    SUPABASE_ANON_KEY: "anon-key",
    DISPATCH_WORKER_TOKEN: "pc-worker-secret",
    PC_OWNER_USER_IDS: USER_A,
    BUCKET: {
      get: async (k) => bucketObjects.get(k) ?? null,
      put: async (k, val) => { bucketObjects.set(k, { arrayBuffer: async () => val, httpEtag: "etag", writeHttpMetadata: () => {} }); },
      head: async (k) => bucketObjects.get(k) ?? null,
      delete: async (k) => { bucketObjects.delete(k); },
    },
    R2_S3_ACCESS_KEY_ID: "ak",
    R2_S3_SECRET_ACCESS_KEY: "sk",
    R2_BUCKET_NAME: "clips",
    R2_ACCOUNT_ID: "acct",
    TOKEN_VAULT_KEY: VAULT_KEY,
    YOUTUBE_CLIENT_ID: "yt-client",
    YOUTUBE_CLIENT_SECRET: "yt-secret",
    OAUTH_REDIRECT_URI: "https://w.test/oauth/google/callback",
    ADMIN_EMAILS: "",
    SITE_ORIGIN: "https://app.test",
    INSTAGRAM_CLIENT_ID: "ig-id",
    INSTAGRAM_CLIENT_SECRET: "ig-sec",
    INSTAGRAM_LOGIN_CLIENT_ID: "igl-id",
    INSTAGRAM_LOGIN_CLIENT_SECRET: "igl-sec",
    FACEBOOK_CLIENT_ID: "fb-id",
    FACEBOOK_CLIENT_SECRET: "fb-sec",
    LINKEDIN_CLIENT_ID: "li-id",
    LINKEDIN_CLIENT_SECRET: "li-sec",
    TWITTER_CLIENT_ID: "tw-id",
    TWITTER_CLIENT_SECRET: "tw-sec",
    ...overrides,
  };
}

const pcHeaders = (uid = USER_A) => ({
  Authorization: "Bearer pc-worker-secret",
  "X-Dispatch-User-Id": uid,
  "Content-Type": "application/json",
});

// ---- 1. Vault AES-GCM v2 AAD & Migration ----

test("vault_v: 2 enforces exact AAD binding and dual-reads legacy v1 for migration", async () => {
  const aadA = buildVaultAad(USER_A, "youtube", "UC123");
  const encV2 = await encryptTokenV2(VAULT_KEY, JSON.stringify({ refresh_token: "rt-secret" }), aadA);
  const parsed = JSON.parse(encV2);
  assert.equal(parsed.vault_v, 2);

  const ok = await decryptTokenWithMigration(VAULT_KEY, encV2, aadA);
  assert.equal(ok.version, 2);
  assert.equal(ok.needsMigration, false);
  assert.equal(JSON.parse(ok.plaintext).refresh_token, "rt-secret");

  // Cross-user, cross-provider, cross-account, and missing AAD MUST fail closed.
  await assert.rejects(() => decryptTokenWithMigration(VAULT_KEY, encV2, buildVaultAad(USER_B, "youtube", "UC123")));
  await assert.rejects(() => decryptTokenWithMigration(VAULT_KEY, encV2, buildVaultAad(USER_A, "twitter", "UC123")));
  await assert.rejects(() => decryptTokenWithMigration(VAULT_KEY, encV2, buildVaultAad(USER_A, "youtube", "UC999")));
  await assert.rejects(() => decryptToken(VAULT_KEY, encV2));

  // Legacy v1 ciphertext decrypts cleanly when expectedAad provided, signaling migration.
  const encV1 = await encryptToken(VAULT_KEY, "legacy-refresh-token");
  assert.ok(!encV1.startsWith("{"));
  const legacyRes = await decryptTokenWithMigration(VAULT_KEY, encV1, aadA);
  assert.equal(legacyRes.version, 1);
  assert.equal(legacyRes.needsMigration, true);
  assert.equal(legacyRes.plaintext, "legacy-refresh-token");
});

test("getValidProviderToken migrates legacy v1 -> v2, caches short-lived token, and persists needs_reauth on revocation", async () => {
  const legacyEnc = await encryptToken(VAULT_KEY, JSON.stringify({
    access_token: "cached-ig-access-tok",
    auth_mode: "instagram_login",
    token_expires_at: new Date(Date.now() + 3600_000).toISOString(),
  }));
  let patchedEnc = null;
  routes.push({
    match: "/rest/v1/connected_accounts",
    method: "GET",
    fn: () => ({
      body: [{
        id: ROW_1,
        user_id: USER_A,
        provider: "instagram",
        account_id: "ig-acct-1",
        credentials_encrypted: legacyEnc,
        metadata: {},
      }],
    }),
  });
  routes.push({
    match: "/rest/v1/connected_accounts",
    method: "PATCH",
    fn: (_u, init) => {
      patchedEnc = JSON.parse(init.body).credentials_encrypted;
      return { body: [{ id: ROW_1 }] };
    },
  });

  const tok = await getValidProviderToken(mockEnv(), {
    userId: USER_A,
    provider: "instagram",
    accountId: "ig-acct-1",
  });
  assert.equal(tok.accessToken, "cached-ig-access-tok");
  assert.ok(patchedEnc, "legacy v1 should be CAS-migrated on read");
  const migrated = await decryptTokenWithMigration(
    VAULT_KEY,
    patchedEnc,
    buildVaultAad(USER_A, "instagram", "ig-acct-1"),
  );
  assert.equal(migrated.version, 2);

  // Cache hit avoids redundant PATCH or refresh calls.
  const callsBeforeCacheHit = fetchCalls.length;
  const cached = await getValidProviderToken(mockEnv(), {
    userId: USER_A,
    provider: "instagram",
    accountId: "ig-acct-1",
  });
  assert.equal(cached.accessToken, "cached-ig-access-tok");
  assert.equal(fetchCalls.filter((c) => c.method === "PATCH").length, 1);
  assert.ok(fetchCalls.length <= callsBeforeCacheHit + 1);
});

test("getValidProviderToken handles X/Twitter single-use refresh CAS conflict and revoked YouTube needs_reauth", async () => {
  const aadTw = buildVaultAad(USER_A, "twitter", "tw-1");
  const expiredTwEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({
      access_token: "old-tw",
      refresh_token: "old-rt",
      token_expires_at: new Date(Date.now() - 60_000).toISOString(),
    }),
    aadTw,
  );
  const winnerTwEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({
      access_token: "winner-fresh-tw-access",
      refresh_token: "winner-rt",
      token_expires_at: new Date(Date.now() + 7200_000).toISOString(),
    }),
    aadTw,
  );

  let getCount = 0;
  routes.push({
    match: "/rest/v1/connected_accounts",
    method: "GET",
    fn: () => {
      getCount += 1;
      return {
        body: [{
          id: ROW_1,
          user_id: USER_A,
          provider: "twitter",
          account_id: "tw-1",
          credentials_encrypted: getCount === 1 ? expiredTwEnc : winnerTwEnc,
          metadata: {},
        }],
      };
    },
  });
  routes.push({
    match: "api.twitter.com/2/oauth2/token",
    method: "POST",
    fn: () => ({ body: { access_token: "loser-access", refresh_token: "loser-rt", expires_in: 7200 } }),
  });
  routes.push({
    match: "/rest/v1/connected_accounts",
    method: "PATCH",
    // Simulate concurrent CAS winner having modified credentials_encrypted already (returns 0 rows).
    fn: () => ({ body: [] }),
  });

  const twRes = await getValidProviderToken(mockEnv(), { userId: USER_A, provider: "twitter", accountId: "tw-1" });
  assert.equal(twRes.accessToken, "winner-fresh-tw-access");

  // Revoked YouTube token flags needs_reauth and throws ProviderAuthError.
  clearProviderTokenCacheForTests();
  routes = [];
  const ytEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({ refresh_token: "revoked-rt" }),
    buildVaultAad(USER_A, "youtube", "UC_REVOKED"),
  );
  let markedReauth = null;
  routes.push({
    match: "/rest/v1/connected_accounts",
    method: "GET",
    fn: () => ({
      body: [{
        id: ROW_1,
        user_id: USER_A,
        provider: "youtube",
        account_id: "UC_REVOKED",
        credentials_encrypted: ytEnc,
        metadata: {},
      }],
    }),
  });
  routes.push({
    match: "oauth2.googleapis.com/token",
    method: "POST",
    fn: () => ({ status: 400, body: { error: "invalid_grant" } }),
  });
  routes.push({
    match: "/rest/v1/connected_accounts",
    method: "PATCH",
    fn: (_u, init) => {
      markedReauth = JSON.parse(init.body);
      return { body: [{ id: ROW_1 }] };
    },
  });

  await assert.rejects(
    () => getValidProviderToken(mockEnv(), { userId: USER_A, provider: "youtube", accountId: "UC_REVOKED" }),
    (err) => err instanceof ProviderAuthError && err.code === "needs_reauth",
  );
  assert.equal(markedReauth?.metadata?.needs_reauth, true);
  assert.equal(markedReauth?.is_verified, false);
});

// ---- 2. Multi-page Facebook Callback + Zero Token in URL + Avatar / Stream Hardening ----

test("facebook callback stores all admined Pages with vault_v:2 AAD and never puts access_token in URL query", async () => {
  const storedPages = [];
  routes.push({ match: "/rpc/cloud_consume_oauth_state", method: "POST", fn: () => ({ body: USER_A }) });
  routes.push({ match: "graph.facebook.com/v21.0/oauth/access_token", fn: () => ({ body: { access_token: "fb-long-user", expires_in: 5184000 } }) });
  routes.push({
    match: "graph.facebook.com/v21.0/me/accounts",
    fn: () => ({
      body: {
        data: [
          { id: "page-1", name: "Page One", access_token: "page-tok-1" },
          { id: "page-2", name: "Page Two", access_token: "page-tok-2" },
        ],
      },
    }),
  });
  routes.push({
    match: "/rpc/cloud_store_connected_account",
    method: "POST",
    fn: (_u, init) => {
      storedPages.push(JSON.parse(init.body));
      return { body: `row-${storedPages.length}` };
    },
  });

  const res = await worker.fetch(new Request("https://w.test/oauth/facebook/callback?code=c1&state=st1"), mockEnv());
  assert.equal(res.status, 200);
  const html = await res.text();
  assert.match(html, /2 Facebook Pages/);
  assert.equal(storedPages.length, 2);
  assert.deepEqual(storedPages.map((p) => p.p_account_id), ["page-1", "page-2"]);

  for (const call of fetchCalls) {
    assert.ok(!call.url.includes("access_token="), `access_token leaked in URL: ${call.url}`);
  }
  const dec2 = await decryptTokenWithMigration(
    VAULT_KEY,
    storedPages[1].p_credentials_encrypted,
    buildVaultAad(USER_A, "facebook", "page-2"),
  );
  assert.equal(JSON.parse(dec2.plaintext).access_token, "page-tok-2");
});

test("stream endpoint rejects ?token= query auth and /avatars rejects raw PostgREST or=() filter injection", async () => {
  routes.push({
    match: "/auth/v1/user",
    fn: () => ({ body: { id: USER_A, email: "a@example.com" } }),
  });

  // ?token= query parameter must NOT authenticate stream fallback.
  const streamRes = await worker.fetch(
    new Request(`https://w.test/media/${MEDIA_A}/stream?token=user-jwt-secret`),
    mockEnv(),
  );
  assert.equal(streamRes.status, 401);

  // Malicious accountId attempting or=() PostgREST injection must fail with 400 and make zero Supabase calls.
  const avRes = await worker.fetch(
    new Request("https://w.test/avatars/instagram_foo,provider.eq.youtube.jpg"),
    mockEnv(),
  );
  assert.equal(avRes.status, 400);
  assert.equal(fetchCalls.filter((c) => c.url.includes("connected_accounts")).length, 0);
});

// ---- 3. Cloud Media Ownership & Asset Selection Guards ----

test("/publish/queue enforces clip_id ownership, final/${userId}/ prefix, and kind=clip_video only", async () => {
  // Foreign clip_id -> 403
  routes.push({
    match: "/rest/v1/clips_cloud",
    fn: () => ({ body: [] }),
  });
  const foreignClipRes = await worker.fetch(
    new Request("https://w.test/publish/queue", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ clip_id: CLIP_OTHER, title: "Hijack", r2_key: `final/${USER_A}/${MEDIA_A}` }),
    }),
    mockEnv(),
  );
  assert.equal(foreignClipRes.status, 403);

  // Owned clip + cross-user r2_key -> 403
  routes = [{ match: "/rest/v1/clips_cloud", fn: () => ({ body: [{ id: CLIP_A, user_id: USER_A }] }) }];
  const foreignKeyRes = await worker.fetch(
    new Request("https://w.test/publish/queue", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ clip_id: CLIP_A, title: "BadKey", r2_key: `final/${USER_B}/foreign.mp4` }),
    }),
    mockEnv(),
  );
  assert.equal(foreignKeyRes.status, 403);

  // Owned clip + edit_window media object -> 422 (must only publish clip_video)
  routes.push({
    match: "/rest/v1/media_objects",
    fn: () => ({ body: [{ id: MEDIA_A, user_id: USER_A, clip_id: CLIP_A, kind: "edit_window", final_key: `final/${USER_A}/${MEDIA_A}` }] }),
  });
  const editWindowRes = await worker.fetch(
    new Request("https://w.test/publish/queue", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ clip_id: CLIP_A, title: "Window", r2_key: `final/${USER_A}/${MEDIA_A}` }),
    }),
    mockEnv(),
  );
  assert.equal(editWindowRes.status, 422);

  // Valid clip_video succeeds cleanly
  let enqueuedPayload = null;
  routes = [
    { match: "/rest/v1/clips_cloud", fn: () => ({ body: [{ id: CLIP_A, user_id: USER_A }] }) },
    { match: "/rest/v1/media_objects", fn: () => ({ body: [{ id: MEDIA_A, user_id: USER_A, clip_id: CLIP_A, kind: "clip_video", final_key: `final/${USER_A}/${MEDIA_A}` }] }) },
    { match: "/rpc/cloud_enqueue_publishing_job", method: "POST", fn: (_u, init) => { enqueuedPayload = JSON.parse(init.body); return { body: [{ id: "job-1", status: "queued" }] }; } },
  ];
  const okRes = await worker.fetch(
    new Request("https://w.test/publish/queue", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ clip_id: CLIP_A, title: "My Short", r2_key: `final/${USER_A}/${MEDIA_A}` }),
    }),
    mockEnv(),
  );
  assert.equal(okRes.status, 200);
  assert.equal(enqueuedPayload?.p_r2_key, `final/${USER_A}/${MEDIA_A}`);
});

test("/clips/:id/source-url never serves kind=thumbnail and prefers edit_window over clip_video", async () => {
  routes.push({
    match: "/rest/v1/media_objects",
    fn: (u) => {
      assert.ok(u.includes("kind=neq.thumbnail"), "query must filter out thumbnails");
      return {
        body: [
          { id: "m-clip", kind: "clip_video", user_id: USER_A, final_key: `final/${USER_A}/clip.mp4`, byte_count: 100, mime_type: "video/mp4" },
          { id: "m-win", kind: "edit_window", user_id: USER_A, final_key: `final/${USER_A}/win.mp4`, byte_count: 200, mime_type: "video/mp4" },
        ],
      };
    },
  });
  const res = await worker.fetch(
    new Request(`https://w.test/clips/${CLIP_A}/source-url`, { headers: pcHeaders(USER_A) }),
    mockEnv(),
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.media_object_id, "m-win");
});

test("drainPublishingQueue refuses foreign r2_key or non-clip_video media", async () => {
  let patchedBody = null;
  routes.push({
    match: "/rpc/cloud_claim_publishing_jobs",
    method: "POST",
    fn: () => ({
      body: [{
        id: "pub-job-1",
        user_id: USER_A,
        clip_id: CLIP_A,
        title: "Hack",
        description: "",
        platforms: ["youtube"],
        publish_mode: "private",
        r2_key: `final/${USER_B}/stolen.mp4`,
        platform_results: {},
      }],
    }),
  });
  routes.push({
    match: "/rest/v1/publishing_jobs",
    method: "PATCH",
    fn: (_u, init) => {
      patchedBody = JSON.parse(init.body);
      return { body: [{ id: "pub-job-1" }] };
    },
  });

  const stats = await drainPublishingQueue(mockEnv());
  assert.equal(stats.failed, 1);
  assert.equal(patchedBody?.status, "failed");
  assert.match(String(patchedBody?.last_error || ""), /final prefix/i);
});

// ---- 4. Scoped /sync/pc/* OAuth, Gemini, YouTube, Remote Jobs Brokers ----

test("/sync/pc/oauth/access-token enforces allowlist, strips refresh_token/client_secret, and enforces rate limit", async () => {
  const ytEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({
      refresh_token: "TOP_SECRET_REFRESH_TOKEN",
      access_token: "fresh-yt-access-123",
      token_expires_at: new Date(Date.now() + 3600_000).toISOString(),
    }),
    buildVaultAad(USER_A, "youtube", "UC_CHAN_1"),
  );
  routes.push({
    match: "/rest/v1/connected_accounts",
    method: "GET",
    fn: () => ({
      body: [{
        id: ROW_1,
        user_id: USER_A,
        provider: "youtube",
        account_id: "UC_CHAN_1",
        account_name: "Channel",
        account_email: "yt@example.com",
        credentials_encrypted: ytEnc,
        metadata: {},
      }],
    }),
  });

  // Unauthorized outsider UUID is rejected even with valid worker token.
  const outsiderRes = await worker.fetch(
    new Request("https://w.test/sync/pc/oauth/access-token", {
      method: "POST",
      headers: pcHeaders(USER_OUTSIDER),
      body: JSON.stringify({ provider: "youtube", account_id: "UC_CHAN_1" }),
    }),
    mockEnv(),
  );
  assert.equal(outsiderRes.status, 401);

  // Regular Supabase user JWT is forbidden on PC-only route.
  routes.push({ match: "/auth/v1/user", fn: () => ({ body: { id: USER_A, email: "a@example.com" } }) });
  const userJwtRes = await worker.fetch(
    new Request("https://w.test/sync/pc/oauth/access-token", {
      method: "POST",
      headers: { Authorization: "Bearer supa-jwt", "Content-Type": "application/json" },
      body: JSON.stringify({ provider: "youtube", account_id: "UC_CHAN_1" }),
    }),
    mockEnv(),
  );
  assert.equal(userJwtRes.status, 403);

  // Valid PC request returns short-lived access_token ONLY and NEVER leaks refresh_token/secret.
  const okRes = await worker.fetch(
    new Request("https://w.test/sync/pc/oauth/access-token", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ provider: "youtube", account_id: "UC_CHAN_1" }),
    }),
    mockEnv(),
  );
  assert.equal(okRes.status, 200);
  const rawBody = await okRes.text();
  const body = JSON.parse(rawBody);
  assert.equal(body.access_token, "fresh-yt-access-123");
  assert.equal("refresh_token" in body, false);
  assert.equal("client_secret" in body, false);
  assert.equal(rawBody.includes("TOP_SECRET_REFRESH_TOKEN"), false);
  assert.equal(rawBody.includes(VAULT_KEY), false);

  // Rate limit enforces 429 after quota exhausted.
  let rateLimited = false;
  for (let i = 0; i < 125; i++) {
    const r = await worker.fetch(
      new Request("https://w.test/sync/pc/oauth/access-token", {
        method: "POST",
        headers: pcHeaders(USER_A),
        body: JSON.stringify({ provider: "youtube", account_id: "UC_CHAN_1" }),
      }),
      mockEnv(),
    );
    if (r.status === 429) {
      rateLimited = true;
      break;
    }
  }
  assert.equal(rateLimited, true);
});

test("/sync/pc/gemini/* never returns raw BYOK key and forwards generateContent server-side", async () => {
  const rawGeminiKey = "AIzaSyMockUserSecretKeyForWorkerOnlyTest1234";
  const encKey = await encryptTokenV2(VAULT_KEY, rawGeminiKey, buildVaultAad(USER_A, "gemini", "default"));

  routes.push({ match: "/rpc/cloud_get_gemini_key", method: "POST", fn: () => ({ body: encKey }) });
  routes.push({
    match: "generativelanguage.googleapis.com/v1beta/models/",
    method: "POST",
    fn: (_u, init) => {
      assert.equal(init.headers.get("x-goog-api-key"), rawGeminiKey);
      return { body: { candidates: [{ content: { parts: [{ text: "hello from proxy" }] } }] } };
    },
  });

  const statusRes = await worker.fetch(
    new Request("https://w.test/sync/pc/gemini/status", { method: "GET", headers: pcHeaders(USER_A) }),
    mockEnv(),
  );
  assert.equal(statusRes.status, 200);
  const statusText = await statusRes.text();
  const statusJson = JSON.parse(statusText);
  assert.equal(statusJson.configured, true);
  assert.equal("api_key" in statusJson, false);
  assert.equal(statusText.includes(rawGeminiKey), false);

  const genRes = await worker.fetch(
    new Request("https://w.test/sync/pc/gemini/generate", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ model: "gemini-2.5-flash", contents: [{ parts: [{ text: "hi" }] }] }),
    }),
    mockEnv(),
  );
  assert.equal(genRes.status, 200);
  const genText = await genRes.text();
  assert.equal(genText.includes(rawGeminiKey), false);
  assert.equal(JSON.parse(genText).candidates[0].content.parts[0].text, "hello from proxy");
});

test("/sync/pc/remote-jobs/claim enforces PC_OWNER_USER_IDS fail-closed allowlist", async () => {
  routes.push({
    match: "/rpc/cloud_claim_job",
    method: "POST",
    fn: () => ({ body: [{ job_id: CLIP_A, user_id: USER_OUTSIDER, youtube_url: "https://youtu.be/abc12345" }] }),
  });

  const rejected = await worker.fetch(
    new Request("https://w.test/sync/pc/remote-jobs/claim", {
      method: "POST",
      headers: { Authorization: "Bearer pc-worker-secret", "Content-Type": "application/json" },
      body: JSON.stringify({ worker_id: "pc-1" }),
    }),
    mockEnv(),
  );
  assert.equal(rejected.status, 403);
});

test("real migration RPC/table/column contracts: remote-jobs, disconnect, and Gemini v1->v2 CAS migration", async () => {
  const seen = [];
  const rawGeminiKey = "AIzaSyLegacyGeminiKeyForMigrationTest1234";
  const legacyV1Gemini = await encryptToken(VAULT_KEY, rawGeminiKey);
  let patchedGemini = null;

  routes.push({
    match: "/rpc/cloud_get_gemini_key",
    method: "POST",
    fn: () => ({ body: legacyV1Gemini }),
  });
  routes.push({
    match: "/rest/v1/gemini_connections",
    method: "PATCH",
    fn: (u, init) => {
      patchedGemini = { url: u, body: JSON.parse(init.body) };
      return { body: [{ user_id: USER_A }] };
    },
  });
  routes.push({
    match: "/rpc/cloud_claim_job",
    method: "POST",
    fn: () => ({
      body: [{
        job_id: CLIP_A,
        user_id: USER_A,
        youtube_url: "",
        source: "direct",
        upload_object_id: MEDIA_A,
        source_title: "Direct Clip",
      }],
    }),
  });
  routes.push({
    match: "/rest/v1/processing_jobs",
    method: "PATCH",
    fn: (u, init) => {
      seen.push({ url: u, body: JSON.parse(init.body) });
      return { body: [] };
    },
  });
  routes.push({
    match: "/rest/v1/processing_jobs",
    method: "GET",
    fn: () => ({ body: [{ user_id: USER_A }] }),
  });
  routes.push({
    match: "/rpc/cloud_finish_job",
    method: "POST",
    fn: (_u, init) => {
      seen.push({ rpc: "cloud_finish_job", body: JSON.parse(init.body) });
      return { body: null };
    },
  });
  routes.push({
    match: "/rpc/cloud_delete_refresh_token",
    method: "POST",
    fn: (_u, init) => {
      seen.push({ rpc: "cloud_delete_refresh_token", body: JSON.parse(init.body) });
      return { body: null };
    },
  });

  const gemStatus = await worker.fetch(
    new Request("https://w.test/sync/pc/gemini/status", { method: "GET", headers: pcHeaders(USER_A) }),
    mockEnv(),
  );
  assert.equal(gemStatus.status, 200);
  assert.ok(patchedGemini, "legacy v1 Gemini key must CAS-patch gemini_connections");
  assert.ok(patchedGemini.url.includes("api_key_encrypted=eq."));
  const decGem = await decryptTokenWithMigration(
    VAULT_KEY,
    patchedGemini.body.api_key_encrypted,
    buildVaultAad(USER_A, "gemini", "default"),
  );
  assert.equal(decGem.version, 2);
  assert.equal(decGem.plaintext, rawGeminiKey);

  const claimRes = await worker.fetch(
    new Request("https://w.test/sync/pc/remote-jobs/claim", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ worker_id: "pc-1" }),
    }),
    mockEnv(),
  );
  assert.equal(claimRes.status, 200);
  const claimJson = await claimRes.json();
  assert.equal(claimJson.job.job_id, CLIP_A);
  assert.equal(claimJson.job.source, "direct");

  const stageRes = await worker.fetch(
    new Request("https://w.test/sync/pc/remote-jobs/stage", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ job_id: CLIP_A, stage: "rendering", video_id: "ytvid123", duration: 12.345 }),
    }),
    mockEnv(),
  );
  assert.equal(stageRes.status, 200);

  const stageVidRes = await worker.fetch(
    new Request("https://w.test/sync/pc/remote-jobs/stage-by-video", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ video_id: "ytvid123", stage: "transcribing" }),
    }),
    mockEnv(),
  );
  assert.equal(stageVidRes.status, 200);

  const titleVidRes = await worker.fetch(
    new Request("https://w.test/sync/pc/remote-jobs/title-by-video", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ video_id: "ytvid123", title: "Enriched Video Title" }),
    }),
    mockEnv(),
  );
  assert.equal(titleVidRes.status, 200);

  const finishRes = await worker.fetch(
    new Request("https://w.test/sync/pc/remote-jobs/finish", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ job_id: CLIP_A, status: "done", video_id: "ytvid123" }),
    }),
    mockEnv(),
  );
  assert.equal(finishRes.status, 200);

  const discRes = await worker.fetch(
    new Request("https://w.test/sync/pc/disconnect", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: "{}",
    }),
    mockEnv(),
  );
  assert.equal(discRes.status, 200);

  assert.equal(seen[0].body.stage, "rendering");
  assert.equal(seen[0].body.last_error, "stage:rendering");
  assert.equal(seen[0].body.video_id, "ytvid123");
  assert.ok(seen[1].url.includes("video_id=eq.ytvid123"));
  assert.ok(seen[1].url.includes("status=neq.failed"));
  assert.equal(seen[1].body.stage, "transcribing");
  assert.ok(seen[2].url.includes("/rest/v1/processing_jobs"));
  assert.equal(seen[2].body.source_title, "Enriched Video Title");
  assert.equal(seen[3].rpc, "cloud_finish_job");
  assert.equal(seen[4].rpc, "cloud_delete_refresh_token");
});

test("resolveConnectedAccountRow does not substitute single YouTube account for non-matching explicit account_id", async () => {
  const ytEnc = await encryptTokenV2(
    VAULT_KEY,
    JSON.stringify({
      refresh_token: "rt-yt",
      access_token: "yt-access",
      token_expires_at: new Date(Date.now() + 3600_000).toISOString(),
    }),
    buildVaultAad(USER_A, "youtube", "UC_CHAN_1"),
  );
  routes.push({
    match: "/rest/v1/connected_accounts",
    method: "GET",
    fn: (u) => {
      // Only return the YouTube row if query matches UC_CHAN_1, ROW_1, or has NO account filter (unqualified fallback).
      if (u.includes("account_id=eq.ig_other_acct") || u.includes("account_email=eq.ig_other_acct")) {
        return { body: [] };
      }
      if (u.includes("provider=eq.instagram")) {
        return { body: [] };
      }
      return {
        body: [{
          id: ROW_1,
          user_id: USER_A,
          provider: "youtube",
          account_id: "UC_CHAN_1",
          credentials_encrypted: ytEnc,
          metadata: {},
        }],
      };
    },
  });

  // Explicit non-matching account_id on provider=youtube must return 404 (not substitute user's single YT channel!).
  const res = await worker.fetch(
    new Request("https://w.test/sync/pc/oauth/access-token", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ provider: "youtube", account_id: "ig_other_acct" }),
    }),
    mockEnv(),
  );
  assert.equal(res.status, 404);

  // Explicit UUID lookup matches either id or legacy UUID account_id via or=(id.eq...,account_id.eq...).
  const uuidRes = await worker.fetch(
    new Request("https://w.test/sync/pc/oauth/access-token", {
      method: "POST",
      headers: pcHeaders(USER_A),
      body: JSON.stringify({ provider: "youtube", account_id: ROW_1 }),
    }),
    mockEnv(),
  );
  assert.equal(uuidRes.status, 200);
  const orQuery = fetchCalls.find((c) => c.url.includes("connected_accounts") && c.url.includes(ROW_1));
  assert.ok(orQuery && decodeURIComponent(orQuery.url).includes(`or=(id.eq.${ROW_1},account_id.eq.${ROW_1})`));
});

test("GET /publish/status/:localClipId resolves local_clip_id -> clips_cloud.id and returns jobs", async () => {
  routes.push({
    match: "/rest/v1/clips_cloud",
    method: "GET",
    fn: (u) => {
      assert.ok(u.includes("local_clip_id=eq.clip_local_42"));
      return { body: [{ id: CLIP_A, local_clip_id: "clip_local_42" }] };
    },
  });
  routes.push({
    match: "/rest/v1/publishing_jobs",
    method: "GET",
    fn: (u) => {
      assert.ok(u.includes(CLIP_A), "must resolve local_clip_id to clips_cloud UUID");
      return { body: [{ id: "pub-1", clip_id: CLIP_A, status: "queued" }] };
    },
  });

  const res = await worker.fetch(
    new Request("https://w.test/publish/status/clip_local_42", { headers: pcHeaders(USER_A) }),
    mockEnv(),
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.clip_id, CLIP_A);
  assert.equal(body.jobs.length, 1);
  assert.equal(body.jobs[0].id, "pub-1");
});
