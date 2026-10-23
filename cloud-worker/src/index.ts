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
async function presign(env: Env, method: "PUT" | "GET", key: string, mime?: string): Promise<string> {
  const signed = await makeS3(env).sign(
    new Request(`${s3Url(env, key)}?X-Amz-Expires=${PRESIGN_EXPIRES}`,
      { method, headers: mime ? { "content-type": mime } : undefined }),
    { aws: { signQuery: true } },
  );
  return signed.url.toString(); // bearer-capable short-lived URL; never persisted or logged
}

async function readJson<T>(request: Request): Promise<T | null> {
  try { return (await request.json()) as T; } catch { return null; }
}

interface MediaObj {
  id: string; user_id: string; status: string; kind: Kind; mime_type: string;
  staging_key: string | null; final_key: string | null; expected_bytes?: number;
}

// ---------- routes ----------
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") return json(200, { ok: true });
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
            upload_url: await presign(env, "PUT", r.staging_key, body.mime_type),
            expires_in: PRESIGN_EXPIRES,
          });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          const status = msg.includes("quota") || msg.includes("cap") || msg.includes("outstanding") ? 413
            : msg.includes("different parameters") ? 409 : 502;
          return json(status, { error: msg });
        }
      }

      if (request.method === "POST" && url.pathname === "/complete") {
        const body = await readJson<{ reservation_id: string }>(request);
        if (!body?.reservation_id) return json(400, { error: "reservation_id required" });
        const lookup = await rpc<{ media_object_id: string; user_id: string; status: string }>(env, "cloud_reserve_lookup", { p_reservation_id: body.reservation_id });
        if (!lookup?.media_object_id) return json(404, { error: "reservation not found" });
        const obj = await selectOne<MediaObj>(env, "media_objects", lookup.media_object_id, "id,user_id,status,kind,mime_type,staging_key,final_key");
        if (!obj || obj.user_id !== userId) return json(403, { error: "forbidden" });
        if (lookup.status === "completed" && obj.final_key) {
          return json(200, { object_id: obj.id, final_key: obj.final_key, byte_count: 0, idempotent: true });
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
        // DB succeeded — only now may staging be removed.
        await env.BUCKET.delete(obj.staging_key);
        return json(200, { object_id: stored.id, final_key: stored.final_key, byte_count: stored.byte_count });
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
};
