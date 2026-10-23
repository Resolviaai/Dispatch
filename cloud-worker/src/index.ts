/**
 * Dispatch cloud control-plane Worker (plan D5/D8, alpha brief).
 * Auth: `Authorization: Bearer <DISPATCH_WORKER_TOKEN>` = trusted owner PC;
 * Supabase end-user JWTs are verified against SUPABASE_URL/auth/v1/user.
 * Privileged DB operations run as atomic Postgres functions (migration 0002) via
 * the service-role key (Worker secret). Video bytes NEVER pass through this Worker:
 * clients upload/fetch directly from private R2 via short-lived presigned URLs.
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

const PRESIGN_SECONDS = 600;
const KINDS = { clip_video: "video/mp4", thumbnail: "image/jpeg" } as const;
type Kind = keyof typeof KINDS;
const CAP_BYTES: Record<Kind, number> = { clip_video: 536870912, thumbnail: 8388608 };

// ---------- pure helpers (unit-tested offline) ----------
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
  const data = (await res.json()) as T & { message?: string };
  if (!res.ok) throw new Error(`rpc ${fn} failed: ${res.status} ${data?.message ?? ""}`);
  return data;
}

// ---------- auth ----------
async function authenticate(env: Env, request: Request): Promise<{ userId: string } | null> {
  const auth = request.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) return null;
  const token = auth.slice(7);
  if (env.DISPATCH_WORKER_TOKEN && token === env.DISPATCH_WORKER_TOKEN) {
    // Trusted PC actor: caller must still name the target user explicitly.
    const uid = request.headers.get("X-Dispatch-User-Id");
    return uid && /^[0-9a-f-]{36}$/i.test(uid) ? { userId: uid } : null;
  }
  // End-user Supabase JWT: verify with the auth service, never trust the claim locally.
  const res = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
    headers: { apikey: env.SUPABASE_ANON_KEY, Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return null;
  const user = (await res.json()) as { id?: string };
  return user.id ? { userId: user.id } : null;
}

// ---------- R2 presign (S3 SIGV4; bytes never transit this Worker) ----------
function s3(env: Env): AwsClient {
  return new AwsClient({
    accessKeyId: env.R2_S3_ACCESS_KEY_ID,
    secretAccessKey: env.R2_S3_SECRET_ACCESS_KEY,
  });
}
function s3Url(env: Env, key: string): string {
  return `https://${env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com/${env.R2_BUCKET_NAME}/${key}`;
}
async function presignPut(env: Env, key: string, mime: string): Promise<string> {
  return (await s3(env).sign(new Request(s3Url(env, key), { method: "PUT" }), {
    aws: { signQuery: true },
    [Symbol.for("aws.signing Service")]: "s3",
    [Symbol.for("aws.signing ts")]: PRESIGN_SECONDS,
  })).url.toString(); // short-lived signed PUT; never persisted, never logged
}
async function presignGet(env: Env, key: string): Promise<string> {
  return (await s3(env).sign(new Request(s3Url(env, key), { method: "GET" }), {
    aws: { signQuery: true },
    [Symbol.for("aws.signing Service")]: "s3",
    [Symbol.for("aws.signing ts")]: PRESIGN_SECONDS,
  })).url.toString();
}

async function readJson<T>(request: Request): Promise<T | null> {
  try { return (await request.json()) as T; } catch { return null; }
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
        const r = await rpc<{ reservation_id: string; staging_key: string; media_object_id: string }>(env, "cloud_reserve_upload", {
          p_user_id: userId, p_kind: body.kind, p_mime: body.mime_type,
          p_expected_bytes: body.expected_bytes, p_client_token: body.client_token,
          p_clip_id: body.clip_id ?? null,
        });
        return json(200, {
          reservation_id: r.reservation_id,
          media_object_id: r.media_object_id,
          upload_url: await presignPut(env, r.staging_key, body.mime_type),
          expires_in: PRESIGN_SECONDS,
        });
      }

      if (request.method === "POST" && url.pathname === "/complete") {
        const body = await readJson<{ reservation_id: string }>(request);
        if (!body?.reservation_id) return json(400, { error: "reservation_id required" });
        // Fetch the reservation row via service-role REST to resolve keys + ownership.
        const res = await rpc<{ media_object_id: string }>(env, "cloud_reserve_lookup", { p_reservation_id: body.reservation_id }).catch(() => null);
        if (!res) return json(404, { error: "reservation not found" });
        const objRes = await fetch(
          `${env.SUPABASE_URL}/rest/v1/media_objects?id=eq.${res.media_object_id}&select=staging_key,kind,mime_type,user_id,status`,
          { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } });
        const obj = (await objRes.json() as Array<{ staging_key: string; kind: string; mime_type: string; user_id: string; status: string }>)[0];
        if (!obj || obj.user_id !== userId) return json(403, { error: "forbidden" });
        // REAL object size from the R2 binding — the client claim never governs usage.
        const head = await env.BUCKET.head(obj.staging_key);
        if (!head) return json(409, { error: "staging object missing; re-upload then complete" });
        const invalid = validateReserveInput(obj.kind, obj.mime_type, head.size);
        if (invalid) { await env.BUCKET.delete(obj.staging_key); await rpc(env, "cloud_fail_reservation", { p_reservation_id: body.reservation_id }); return json(422, { error: `uploaded object rejected: ${invalid}` }); }
        // Promote staging -> immutable final via S3 server-side copy, then remove staging.
        const finalKey = `final/${userId}/${crypto.randomUUID()}`;
        await s3(env).fetch(new Request(s3Url(env, finalKey), {
          method: "PUT", headers: { "x-amz-copy-source": `/${env.R2_BUCKET_NAME}/${obj.staging_key}` },
        }));
        await env.BUCKET.delete(obj.staging_key);
        const stored = await rpc<{ id: string; final_key: string; byte_count: number }>(env, "cloud_complete_upload", {
          p_reservation_id: body.reservation_id, p_actual_bytes: head.size, p_final_key: finalKey,
        });
        return json(200, { object_id: stored.id, final_key: stored.final_key, byte_count: stored.byte_count });
      }

      if (request.method === "POST" && url.pathname === "/media-url") {
        const body = await readJson<{ media_object_id: string }>(request);
        if (!body?.media_object_id) return json(400, { error: "media_object_id required" });
        const res = await fetch(
          `${env.SUPABASE_URL}/rest/v1/media_objects?id=eq.${body.media_object_id}&select=final_key,user_id,status,expires_at`,
          { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } });
        const obj = (await res.json() as Array<{ final_key: string; user_id: string; status: string; expires_at: string | null }>)[0];
        if (!obj || obj.user_id !== userId) return json(403, { error: "forbidden" });
        if (obj.status !== "active") return json(409, { error: `media not active (${obj.status})` });
        return json(200, { url: await presignGet(env, obj.final_key), expires_in: PRESIGN_SECONDS });
      }

      if (request.method === "POST" && url.pathname === "/delete") {
        const body = await readJson<{ media_object_id: string }>(request);
        if (!body?.media_object_id) return json(400, { error: "media_object_id required" });
        const res = await fetch(
          `${env.SUPABASE_URL}/rest/v1/media_objects?id=eq.${body.media_object_id}&select=final_key,user_id,status`,
          { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } });
        const obj = (await res.json() as Array<{ final_key: string; user_id: string; status: string }>)[0];
        if (!obj || obj.user_id !== userId) return json(403, { error: "forbidden" });
        if (obj.status === "deleted") return json(200, { status: "deleted", idempotent: true }); // repeat delete
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
