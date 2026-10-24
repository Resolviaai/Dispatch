/**
 * Dispatch cloud control-plane Worker (plan D5/D8, alpha brief).
 * Auth: `Authorization: Bearer <DISPATCH_WORKER_TOKEN>` = trusted owner PC (names the
 * target user via X-Dispatch-User-Id); Supabase end-user JWTs are verified against
 * SUPABASE_URL/auth/v1/user. Privileged DB operations run as atomic Postgres functions
 * (migration 0002) via the service-role key (Worker secret). Video bytes NEVER pass
 * through this Worker: clients upload/fetch private R2 directly via short-lived
 * presigned URLs (aws4fetch SIGV4, service s3 / region auto, explicit expiry).
 */
import { AwsClient } from "aws4fetch";
import { encryptToken, buildAuthorizeUrl, validateTokenResponse } from "./oauth.ts";

export interface Env {
  SUPABASE_URL: string;
  SUPABASE_SERVICE_ROLE_KEY: string;
  SUPABASE_ANON_KEY: string;
  DISPATCH_WORKER_TOKEN: string;
  BUCKET: R2Bucket;
  R2_S3_ACCESS_KEY_ID: string;
  R2_S3_SECRET_ACCESS_KEY: string;
  R2_BUCKET_NAME: string;
  R2_ACCOUNT_ID: string;
  TOKEN_VAULT_KEY: string;
  YOUTUBE_CLIENT_ID: string;
  YOUTUBE_CLIENT_SECRET: string;
  OAUTH_REDIRECT_URI: string;
}

const PRESIGN_EXPIRES = 600; // seconds; short-lived by contract
const KINDS = { clip_video: "video/mp4", thumbnail: "image/jpeg" } as const;
type Kind = keyof typeof KINDS;
const CAP_BYTES: Record<Kind, number> = { clip_video: 536870912, thumbnail: 8388608 };

// ---------- pure helpers (also unit-tested) ----------
export function validateReserveInput(kind: string, mime: string, expectedBytes: number): string | null {
  if (!(kind in KINDS)) return "unsupported kind";
  if (KINDS[kind as Kind] !== mime) return "unsupported mime for kind";
  if (!Number.isInteger(expectedBytes) || expectedBytes <= 0) return "expected_bytes must be a positive integer";
  if (expectedBytes > CAP_BYTES[kind as Kind]) return "expected_bytes exceeds object cap";
  return null;
}

export function json(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

// Table-valued Postgres RPCs come back from PostgREST as ARRAYS.
function firstRow<T>(rows: unknown): T {
  if (Array.isArray(rows)) return rows[0] as T;
  return rows as T; // scalar/void/single-object RPCs
}

// ---------- Supabase REST (service-role) ----------
async function rpc<T>(env: Env, fn: string, params: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/${fn}`, {
    method: "POST",
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(params),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`rpc ${fn} failed: ${res.status} ${text.slice(0, 200)}`);
  return firstRow<T>(text ? JSON.parse(text) : null);
}

async function selectOne<T>(env: Env, table: string, id: string, columns: string): Promise<T | null> {
  const res = await fetch(
    `${env.SUPABASE_URL}/rest/v1/${table}?id=eq.${encodeURIComponent(id)}&select=${columns}`,
    { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } });
  if (!res.ok) throw new Error(`select ${table} failed: ${res.status}`);
  return ((await res.json()) as T[])[0] ?? null;
}

// ---------- auth ----------
export async function authenticate(env: Env, request: Request): Promise<{ userId: string } | null> {
  const auth = request.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) return null;
  const token = auth.slice(7);
  if (env.DISPATCH_WORKER_TOKEN && token === env.DISPATCH_WORKER_TOKEN) {
    const uid = request.headers.get("X-Dispatch-User-Id");
    return uid && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(uid)
      ? { userId: uid } : null;
  }
  const res = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
    headers: { apikey: env.SUPABASE_ANON_KEY, Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return null;
  const user = (await res.json()) as { id?: string };
  return user.id ? { userId: user.id } : null;
}

// ---------- R2 presign (aws4fetch: service s3, region auto, explicit X-Amz-Expires) ----------
export function makeS3(env: Env): AwsClient {
  return new AwsClient({
    accessKeyId: env.R2_S3_ACCESS_KEY_ID,
    secretAccessKey: env.R2_S3_SECRET_ACCESS_KEY,
    service: "s3",
    region: "auto",
  });
}
function s3Url(env: Env, key: string): string {
  return `https://${env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com/${env.R2_BUCKET_NAME}/${key}`;
}
async function presign(env: Env, method: "PUT" | "GET", key: string, mime?: string, contentLength?: number): Promise<string> {
  const headers: Record<string, string> = {};
  if (mime) headers["content-type"] = mime;
  if (contentLength !== undefined) headers["content-length"] = String(contentLength); // defense-in-depth only: R2 does NOT enforce signed content-length on presigned PUTs (verified live 2026-10-24); the authoritative guard is complete-time actual-size verification
  const signed = await makeS3(env).sign(
    new Request(`${s3Url(env, key)}?X-Amz-Expires=${PRESIGN_EXPIRES}`,
      { method, headers: Object.keys(headers).length ? headers : undefined }),
    { aws: { signQuery: true } },
  );
  return signed.url.toString(); // bearer-capable short-lived URL; never persisted or logged
}

async function readJson<T>(request: Request): Promise<T | null> {
  try { return (await request.json()) as T; } catch { return null; }
}

interface MediaObj {
  id: string; user_id: string; status: string; kind: Kind; mime_type: string;
  staging_key: string | null; final_key: string | null; expected_bytes?: number; byte_count?: number;
}

// ---------- routes ----------
async function runCleanup(env: Env): Promise<Response> {
      const list = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_list_abandoned_staging`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: "{}",
      });
      if (!list.ok) return json(502, { error: `list abandoned failed: ${list.status}` });
      const listBody = await list.json();
      const rows: Array<{ media_object_id: string; staging_key: string; reservation_id: string }> = Array.isArray(listBody) ? listBody : [];
      const results: Array<{ media_object_id: string; status: string }> = [];
      for (const row of rows ?? []) {
        try {
          await env.BUCKET.delete(row.staging_key); // throws on R2 failure
        } catch (e) {
          results.push({ media_object_id: row.media_object_id, status: `delete_failed_retry_later:${e instanceof Error ? e.message : e}` });
          continue; // nothing released; the row stays listed for the next run
        }
        await rpc(env, "cloud_confirm_abandon", { p_media_object_id: row.media_object_id });
        results.push({ media_object_id: row.media_object_id, status: "abandoned" });
      }
      const leftovers = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_list_promotion_leftovers`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: "{}",
      });
      const leftoverBody = leftovers.ok ? await leftovers.json() : null;
      const leftoverRows: Array<{ media_object_id: string; staging_key: string }> = Array.isArray(leftoverBody) ? leftoverBody : [];
      for (const row of leftoverRows) {
        try {
          await env.BUCKET.delete(row.staging_key);
          await rpc(env, "cloud_confirm_staging_removed", { p_media_id: row.media_object_id });
          results.push({ media_object_id: row.media_object_id, status: "promotion_leftover_reaped" });
        } catch (e) {
          results.push({ media_object_id: row.media_object_id, status: `leftover_retry_later:${e instanceof Error ? e.message : e}` });
        }
      }
      // Phase 3: staging-watch sweep. Presigned PUT URLs can be replayed until
      // they expire, recreating staging objects after completion/abandonment.
      // Re-delete every watched key (idempotent) throughout the watch window.
      const watched = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_list_watched_staging`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: "{}",
      });
      const watchedBody = watched.ok ? await watched.json() : null;
      const watchedKeys: Array<string> = Array.isArray(watchedBody) ? watchedBody.map((r: { staging_key: string }) => r.staging_key) : [];
      for (const key of watchedKeys) {
        try {
          await env.BUCKET.delete(key);
        } catch {
          // retried on the next run; watch rows are pruned only after expiry
        }
      }
      await rpc(env, "cloud_prune_staging_watch", {}).catch(() => {});
      return json(200, { processed: results.length, swept_staging_keys: watchedKeys.length, results });
}


export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") return json(200, { ok: true });
      if (request.method === "POST" && url.pathname === "/cleanup") {
        // Trusted-PC maintenance only: the raw worker token, no user impersonation.
        const token = (request.headers.get("Authorization") ?? "").slice(7);
        if (!env.DISPATCH_WORKER_TOKEN || token !== env.DISPATCH_WORKER_TOKEN) {
          return json(401, { error: "unauthorized" });
        }
        return runCleanup(env);
      }

    // ---- On-device YouTube OAuth (authenticated via Supabase JWT only) ----
    if (url.pathname === "/oauth/google/start" && request.method === "GET") {
      // Custom-Tab navigation cannot set headers: the short-lived Supabase access
      // token arrives as ?token= and is verified the same strict way (never trusted
      // without server-side validation). It expires in <=1h, limiting replay value.
      const qsToken = url.searchParams.get("token");
      const reqForAuth = qsToken
        ? new Request(request.url, { headers: { Authorization: `Bearer ${qsToken}` } })
        : request;
      const user = await authenticate(env, reqForAuth);
      if (!user) return json(401, { error: "unauthorized" });
      const state = crypto.randomUUID() + crypto.randomUUID();
      await rpc(env, "cloud_put_oauth_state", { p_state: state, p_user_id: user.userId });
      return Response.redirect(
        buildAuthorizeUrl(env.YOUTUBE_CLIENT_ID, env.OAUTH_REDIRECT_URI, state), 302);
    }
    if (url.pathname === "/oauth/google/callback" && request.method === "GET") {
      const code = url.searchParams.get("code") ?? "";
      const state = url.searchParams.get("state") ?? "";
      if (!code || !state) return json(400, { error: "missing code/state" });
      const consumeRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_consume_oauth_state`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ p_state: state }),
      });
      const consumed = consumeRes.ok ? await consumeRes.json() as unknown : null;
      const consumedArr = Array.isArray(consumed) ? consumed as Array<Record<string, unknown>> : [];
      const uid = (consumedArr[0]?.cloud_consume_oauth_state as string | undefined) ?? null;
      if (!uid) return json(400, { error: "invalid or expired state" });
      const tokRes = await fetch("https://oauth2.googleapis.com/token", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          code, client_id: env.YOUTUBE_CLIENT_ID, client_secret: env.YOUTUBE_CLIENT_SECRET,
          redirect_uri: env.OAUTH_REDIRECT_URI, grant_type: "authorization_code",
        }),
      });
      const tok = (await tokRes.json()) as Record<string, unknown>;
      if (!tokRes.ok) return json(502, { error: `token exchange failed: ${JSON.stringify(tok).slice(0, 120)}` });
      const bad = validateTokenResponse(tok);
      if (bad) return json(502, { error: bad });
      const enc = await encryptToken(env.TOKEN_VAULT_KEY, tok.refresh_token as string);
      let email: string | null = null;
      try {
        const payload = JSON.parse(atob((tok.id_token as string).split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
        email = typeof payload.email === "string" ? payload.email : null;
      } catch { /* id_token optional */ }
      await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_refresh_token`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ p_user_id: uid, p_refresh_token_encrypted: enc, p_google_email: email }),
      });
      return new Response("<html><body style='font-family:sans-serif;background:#161616;color:#F5F5F5;text-align:center;padding-top:20vh'><h2>YouTube connected</h2><p>You can close this page and return to Dispatch.</p></body></html>",
        { headers: { "content-type": "text/html" } });
    }

    const actor = await authenticate(env, request);
    if (!actor) return json(401, { error: "unauthorized" });
    const userId = actor.userId;

    try {
      if (request.method === "POST" && url.pathname === "/reserve") {
        const body = await readJson<{ kind: string; mime_type: string; expected_bytes: number; client_token: string; clip_id?: string }>(request);
        if (!body?.client_token) return json(400, { error: "client_token required" });
        const invalid = validateReserveInput(body.kind, body.mime_type, body.expected_bytes);
        if (invalid) return json(400, { error: invalid });
        try {
          const r = await rpc<{ reservation_id: string; staging_key: string; media_object_id: string }>(env, "cloud_reserve_upload", {
            p_user_id: userId, p_kind: body.kind, p_mime: body.mime_type,
            p_expected_bytes: body.expected_bytes, p_client_token: body.client_token,
            p_clip_id: body.clip_id ?? null,
          });
          if (!r?.reservation_id) return json(502, { error: "reserve returned no reservation" });
          return json(200, {
            reservation_id: r.reservation_id,
            media_object_id: r.media_object_id,
            upload_url: await presign(env, "PUT", r.staging_key, body.mime_type, body.expected_bytes),
            expires_in: PRESIGN_EXPIRES,
          });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          const status = msg.includes("quota") || msg.includes("cap") || msg.includes("outstanding") ? 413
            : msg.includes("different parameters") || msg.includes("already ") ? 409 : 502;
          return json(status, { error: msg });
        }
      }

      if (request.method === "POST" && url.pathname === "/complete") {
        const body = await readJson<{ reservation_id: string }>(request);
        if (!body?.reservation_id) return json(400, { error: "reservation_id required" });
        const lookup = await rpc<{ media_object_id: string; user_id: string; status: string }>(env, "cloud_reserve_lookup", { p_reservation_id: body.reservation_id });
        if (!lookup?.media_object_id) return json(404, { error: "reservation not found" });
        const obj = await selectOne<MediaObj>(env, "media_objects", lookup.media_object_id, "id,user_id,status,kind,mime_type,staging_key,final_key,byte_count");
        if (!obj || obj.user_id !== userId) return json(403, { error: "forbidden" });
        if (lookup.status === "completed" && obj.final_key) {
          return json(200, { object_id: obj.id, final_key: obj.final_key, byte_count: obj.byte_count, idempotent: true });
        }
        if (!obj.staging_key) return json(409, { error: "no staging object; re-reserve" });
        // REAL object size from the R2 binding — client claims never govern usage.
        const head = await env.BUCKET.head(obj.staging_key);
        if (!head) return json(409, { error: "staging object missing; re-upload then complete" });
        const res = await selectOne<{ expected_bytes: number }>(env, "upload_reservations", body.reservation_id, "expected_bytes");
        const invalid = validateReserveInput(obj.kind, obj.mime_type, head.size)
          ?? (head.size > (res?.expected_bytes ?? 0) ? "actual size exceeds reserved expectation" : null);
        if (invalid) {
          await env.BUCKET.delete(obj.staging_key);
          await rpc(env, "cloud_fail_reservation", { p_reservation_id: body.reservation_id });
          return json(422, { error: `uploaded object rejected: ${invalid}` });
        }
        // Stable final key per media object: retries reuse it, so no unbounded orphans.
        const finalKey = `final/${userId}/${obj.id}`;
        const copy = await makeS3(env).fetch(new Request(s3Url(env, finalKey), {
          method: "PUT", headers: { "x-amz-copy-source": `/${env.R2_BUCKET_NAME}/${obj.staging_key}` },
        }));
        if (!copy.ok) return json(502, { error: `R2 copy failed: ${copy.status}` });
        // Verify the final object exists before committing anything.
        if (!(await env.BUCKET.head(finalKey))) return json(502, { error: "final object missing after copy" });
        let stored: { id: string; final_key: string; byte_count: number };
        try {
          stored = await rpc<typeof stored>(env, "cloud_complete_upload", {
            p_reservation_id: body.reservation_id, p_actual_bytes: head.size, p_final_key: finalKey,
          });
        } catch (e) {
          // DB completion failed: staging stays for retry; final object is the stable
          // key so a retry overwrites it rather than accumulating orphans.
          return json(503, { error: `completion persisted failed; retry: ${e instanceof Error ? e.message : e}` });
        }
        // DB succeeded — only now may staging be removed; then release the duplicate.
        await env.BUCKET.delete(obj.staging_key);
        let stagingCleanupPending = false;
        try {
          await rpc(env, "cloud_confirm_staging_removed", { p_media_id: obj.id });
        } catch {
          stagingCleanupPending = true; // duplicate stays counted; /cleanup reaps it
        }
        return json(200, { object_id: stored.id, final_key: stored.final_key, byte_count: stored.byte_count, staging_cleanup_pending: stagingCleanupPending });
      }

      if (request.method === "POST" && url.pathname === "/media-url") {
        const body = await readJson<{ media_object_id: string }>(request);
        if (!body?.media_object_id) return json(400, { error: "media_object_id required" });
        const obj = await selectOne<MediaObj>(env, "media_objects", body.media_object_id, "user_id,status,final_key");
        if (!obj || obj.user_id !== userId) return json(403, { error: "forbidden" });
        if (obj.status !== "active") return json(409, { error: `media not active (${obj.status})` });
        return json(200, { url: await presign(env, "GET", obj.final_key as string), expires_in: PRESIGN_EXPIRES });
      }

      if (request.method === "POST" && url.pathname === "/delete") {
        const body = await readJson<{ media_object_id: string }>(request);
        if (!body?.media_object_id) return json(400, { error: "media_object_id required" });
        const obj = await selectOne<MediaObj>(env, "media_objects", body.media_object_id, "id,user_id,status,staging_key,final_key");
        if (!obj || obj.user_id !== userId) return json(403, { error: "forbidden" });
        if (obj.status === "pending") {
          // Never falsely report deletion: a pending upload is cancelled, not deleted.
          await rpc(env, "cloud_fail_reservation_by_media", { p_media_id: body.media_object_id });
          if (obj.staging_key) await env.BUCKET.delete(obj.staging_key).catch(() => {});
          return json(200, { status: "cancelled_pending" });
        }
        if (obj.status === "deleted") return json(200, { status: "deleted", idempotent: true });
        if (obj.status !== "deletion_pending") await rpc(env, "cloud_mark_deletion", { p_media_id: body.media_object_id });
        try {
          if (obj.final_key) await env.BUCKET.delete(obj.final_key);
        } catch (e) {
          return json(503, { status: "deletion_pending", error: `R2 delete failed; will retry: ${e}` });
        }
        await rpc(env, "cloud_confirm_deletion", { p_media_id: body.media_object_id });
        return json(200, { status: "deleted" });
      }

      return json(404, { error: "not found" });
    } catch (e) {
      return json(500, { error: `worker error: ${e instanceof Error ? e.message : String(e)}` });
    }
  },

  async scheduled(_event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    // Scheduled cleanup (see [triggers] in wrangler.toml): abandoned staging,
    // promotion leftovers, and the staging-watch sweep run unattended.
    ctx.waitUntil(runCleanup(env).then((r) => console.log(`scheduled cleanup: ${r.status}`)));
  },
};
