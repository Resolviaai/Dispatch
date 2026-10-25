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
import {
  encryptToken,
  decryptToken,
  buildAuthorizeUrl,
  buildInstagramAuthorizeUrl,
  buildFacebookAuthorizeUrl,
  buildLinkedInAuthorizeUrl,
  buildTwitterAuthorizeUrl,
  generateRandomString,
  sha256B64Url,
  validateTokenResponse,
  renderOAuthResultHtml,
} from "./oauth.ts";

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
  ADMIN_EMAILS: string; // comma-separated allowlist (server-side only)
  SITE_ORIGIN: string;  // e.g. https://resolviaai.github.io — CORS-pinned admin origin
  INSTAGRAM_CLIENT_ID?: string;
  INSTAGRAM_CLIENT_SECRET?: string;
  FACEBOOK_CLIENT_ID?: string;
  FACEBOOK_CLIENT_SECRET?: string;
  LINKEDIN_CLIENT_ID?: string;
  LINKEDIN_CLIENT_SECRET?: string;
  TWITTER_CLIENT_ID?: string;
  TWITTER_CLIENT_SECRET?: string;
}

const PRESIGN_EXPIRES = 600; // seconds; short-lived by contract
const KINDS = { clip_video: "video/mp4", thumbnail: "image/jpeg", edit_window: "video/mp4" } as const;
type Kind = keyof typeof KINDS;
const CAP_BYTES: Record<Kind, number> = { clip_video: 536870912, thumbnail: 8388608, edit_window: 536870912 };

// ---------- pure helpers (also unit-tested) ----------
export function validateReserveInput(kind: string, mime: string, expectedBytes: number): string | null {
  if (!(kind in KINDS)) return "unsupported kind";
  if (KINDS[kind as Kind] !== mime) return "unsupported mime for kind";
  if (!Number.isInteger(expectedBytes) || expectedBytes <= 0) return "expected_bytes must be a positive integer";
  if (expectedBytes > CAP_BYTES[kind as Kind]) return "expected_bytes exceeds object cap";
  return null;
}

export function json(status: number, body: Record<string, unknown>, corsOrigin: string = "*"): Response {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "Access-Control-Allow-Origin": corsOrigin,
    "Access-Control-Allow-Headers": "authorization, content-type, x-dispatch-user-id, apikey",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  };
  if (corsOrigin !== "*") {
    headers["Vary"] = "Origin";
  }
  return new Response(JSON.stringify(body), { status, headers });
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
export async function authenticate(env: Env, request: Request): Promise<{ userId: string; email: string } | null> {
  const auth = request.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) return null;
  const token = auth.slice(7);
  if (env.DISPATCH_WORKER_TOKEN && token === env.DISPATCH_WORKER_TOKEN) {
    const uid = request.headers.get("X-Dispatch-User-Id");
    return uid && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(uid)
      ? { userId: uid, email: "" } : null;
  }
  const res = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
    headers: { apikey: env.SUPABASE_ANON_KEY, Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return null;
  const user = (await res.json()) as { id?: string; email?: string };
  return user.id ? { userId: user.id, email: user.email ?? "" } : null;
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
    try {
      const url = new URL(request.url);
    const reqOrigin = request.headers.get("Origin") || "*";
    if (request.method === "OPTIONS") {
      const allowedOrigin = (url.pathname.startsWith("/admin/") || url.pathname === "/admin")
        ? (env.SITE_ORIGIN || reqOrigin)
        : reqOrigin;
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": allowedOrigin,
          "Access-Control-Allow-Headers": "authorization, content-type, x-dispatch-user-id, apikey",
          "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
          "Access-Control-Max-Age": "86400",
          "Vary": "Origin",
        },
      });
    }
    if (url.pathname === "/health") return json(200, { ok: true });
      if (request.method === "POST" && url.pathname === "/cleanup") {
        // Trusted-PC maintenance only: the raw worker token, no user impersonation.
        const token = (request.headers.get("Authorization") ?? "").slice(7);
        if (!env.DISPATCH_WORKER_TOKEN || token !== env.DISPATCH_WORKER_TOKEN) {
          return json(401, { error: "unauthorized" });
        }
        return runCleanup(env);
      }

    // ---- Web admin panel (served from the pinned site origin; server-side allowlist) ----
    const adminOrigin = env.SITE_ORIGIN || "";
    if (url.pathname.startsWith("/admin/") || url.pathname === "/admin") {
      if (request.method === "OPTIONS") {
        return new Response(null, { status: 204, headers: {
          "Access-Control-Allow-Origin": adminOrigin,
          "Access-Control-Allow-Headers": "authorization, content-type",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Max-Age": "86400",
          "Vary": "Origin",
        }});
      }
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" }, adminOrigin);
      const admins = (env.ADMIN_EMAILS || "").split(",").map(e => e.trim().toLowerCase()).filter(Boolean);
      if (!admins.includes(user.email.toLowerCase())) {
        return json(403, { error: "admin access required" }, adminOrigin);
      }
      const S = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" };

      if (url.pathname === "/admin/whoami" && request.method === "GET") {
        return json(200, { is_admin: true, email: user.email }, adminOrigin);
      }
      if (url.pathname === "/admin/users" && request.method === "GET") {
        const [authRes, profRes, mediaRes, resvRes, jobsRes, accsRes] = await Promise.all([
          fetch(`${env.SUPABASE_URL}/auth/v1/admin/users?per_page=100`, { headers: S }),
          fetch(`${env.SUPABASE_URL}/rest/v1/profiles?select=user_id,storage_quota_bytes,queue_priority`, { headers: S }),
          fetch(`${env.SUPABASE_URL}/rest/v1/media_objects?status=eq.active&select=user_id,byte_count`, { headers: S }),
          fetch(`${env.SUPABASE_URL}/rest/v1/upload_reservations?select=expected_bytes`, { headers: S }),
          fetch(`${env.SUPABASE_URL}/rest/v1/processing_jobs?select=id,status,youtube_url,created_at&order=created_at.desc&limit=50`, { headers: S }),
          fetch(`${env.SUPABASE_URL}/rest/v1/connected_accounts?select=user_id,provider,account_name`, { headers: S }),
        ]);
        if (!authRes.ok) return json(502, { error: `list failed: ${authRes.status}` }, adminOrigin);
        const authData = await authRes.json() as { users?: Array<Record<string, unknown>> };
        const profilesList = profRes.ok ? (await profRes.json() as Array<Record<string, unknown>>) : [];
        const mediaList = mediaRes.ok ? (await mediaRes.json() as Array<{ user_id: string; byte_count: number }>) : [];
        const reservationsList = resvRes.ok ? (await resvRes.json() as Array<{ expected_bytes: number }>) : [];
        const jobsList = jobsRes.ok ? (await jobsRes.json() as Array<{ id: string; status: string; youtube_url?: string; created_at?: string }>) : [];
        const accountsList = accsRes.ok ? (await accsRes.json() as Array<{ user_id: string; provider: string; account_name: string }>) : [];

        const profileMap = new Map<string, Record<string, unknown>>();
        let totalAllocatedBytes = 0;
        for (const p of profilesList) {
          const pUid = String(p.user_id || "");
          profileMap.set(pUid, p);
          totalAllocatedBytes += Number(p.storage_quota_bytes || 0);
        }

        // Aggregate actual R2 bytes by user
        const userUsageMap = new Map<string, number>();
        let totalActualStoredBytes = 0;
        for (const m of mediaList) {
          const uId = String(m.user_id || "");
          const bytes = Number(m.byte_count || 0);
          userUsageMap.set(uId, (userUsageMap.get(uId) || 0) + bytes);
          totalActualStoredBytes += bytes;
        }

        // Aggregate pending reservations
        let totalPendingReservedBytes = 0;
        for (const r of reservationsList) {
          totalPendingReservedBytes += Number(r.expected_bytes || 0);
        }

        // Aggregate connected provider names by user (zero tokens or secrets)
        const userProvidersMap = new Map<string, string[]>();
        for (const a of accountsList) {
          const uId = String(a.user_id || "");
          const prov = String(a.provider || "").toLowerCase();
          if (prov) {
            const list = userProvidersMap.get(uId) || [];
            if (!list.includes(prov)) list.push(prov);
            userProvidersMap.set(uId, list);
          }
        }

        // Aggregate processing jobs
        const jobCounts = { queued: 0, running: 0, completed: 0, failed: 0, total: jobsList.length };
        for (const j of jobsList) {
          if (j.status === "queued") jobCounts.queued++;
          else if (j.status === "claimed") jobCounts.running++;
          else if (j.status === "completed") jobCounts.completed++;
          else if (j.status === "failed") jobCounts.failed++;
        }

        const POOL_TOTAL_BYTES = 10_000_000_000;
        const unallocatedQuotaBytes = Math.max(0, POOL_TOTAL_BYTES - totalAllocatedBytes);
        const unallocatedQuotaGb = Math.round((unallocatedQuotaBytes / 1_000_000_000) * 100) / 100;
        const totalAllocatedGb = Math.round((totalAllocatedBytes / 1_000_000_000) * 100) / 100;
        const actualStoredGb = Math.round((totalActualStoredBytes / 1_000_000_000) * 100) / 100;
        const pendingReservedGb = Math.round((totalPendingReservedBytes / 1_000_000_000) * 100) / 100;

        let activeUsersCount = 0;
        let disabledUsersCount = 0;

        const users = (authData.users ?? []).map(u => {
          const isBanned = !!u.banned_until;
          if (isBanned) disabledUsersCount++;
          else activeUsersCount++;

          const prof = profileMap.get(String(u.id)) || {};
          const qBytes = Number(prof.storage_quota_bytes || 2_000_000_000);
          const qGb = Math.round((qBytes / 1_000_000_000) * 100) / 100;
          const uBytes = userUsageMap.get(String(u.id)) || 0;
          const uGb = Math.round((uBytes / 1_000_000_000) * 100) / 100;
          const prio = Number(prof.queue_priority || 100);
          const providers = userProvidersMap.get(String(u.id)) || [];

          return {
            id: u.id,
            email: u.email,
            created_at: u.created_at,
            last_sign_in: u.last_sign_in_at,
            banned: isBanned,
            quota_gb: qGb,
            quota_bytes: qBytes,
            used_gb: uGb,
            used_bytes: uBytes,
            queue_priority: prio,
            connected_providers: providers,
          };
        });

        return json(200, {
          users,
          overview: {
            user_counts: {
              total: users.length,
              active: activeUsersCount,
              disabled: disabledUsersCount,
            },
            storage: {
              actual_stored_bytes: totalActualStoredBytes,
              actual_stored_gb: actualStoredGb,
              pending_reserved_bytes: totalPendingReservedBytes,
              pending_reserved_gb: pendingReservedGb,
              allocated_quota_bytes: totalAllocatedBytes,
              allocated_quota_gb: totalAllocatedGb,
              unallocated_quota_bytes: unallocatedQuotaBytes,
              unallocated_quota_gb: unallocatedQuotaGb,
              global_budget_bytes: POOL_TOTAL_BYTES,
              global_budget_gb: 10.0,
            },
            jobs: jobCounts,
          },
          pool_remaining_gb: unallocatedQuotaGb,
          pool_total_gb: 10,
        }, adminOrigin);
      }
      const userAction = url.pathname.match(/^\/admin\/users\/([0-9a-fA-F-]{36})\/(ban|unban|quota|priority)$/);
      if (userAction && request.method === "POST") {
        const [, uid, action] = userAction;
        if (action === "quota") {
          const body = await request.json().catch(() => ({})) as { quota_gb?: number };
          const gb = Number(body.quota_gb);
          if (!Number.isFinite(gb) || gb <= 0 || gb > 10) {
            return json(400, { error: "quota_gb must be between 0.1 and 10 (decimal GB)" }, adminOrigin);
          }
          const quotaBytes = Math.round(gb * 1_000_000_000);

          // Guard against reducing quota below currently stored bytes
          const mediaCheck = await fetch(
            `${env.SUPABASE_URL}/rest/v1/media_objects?user_id=eq.${uid}&status=eq.active&select=byte_count`,
            { headers: S }
          );
          if (mediaCheck.ok) {
            const objs = await mediaCheck.json() as Array<{ byte_count: number }>;
            const currentStored = objs.reduce((sum, o) => sum + Number(o.byte_count || 0), 0);
            if (quotaBytes < currentStored) {
              const storedGb = Math.round((currentStored / 1_000_000_000) * 100) / 100;
              return json(400, { error: `Cannot reduce quota to ${gb} GB: user currently has ${storedGb} GB of stored media.` }, adminOrigin);
            }
          }

          const qres = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_set_user_quota`, {
            method: "POST", headers: S,
            body: JSON.stringify({ p_target_user: uid, p_quota_bytes: quotaBytes }),
          });
          if (!qres.ok) {
            const errData = await qres.json().catch(() => ({})) as { message?: string; details?: string };
            const errMsg = errData.message || errData.details || `quota update failed (${qres.status})`;
            return json(400, { error: errMsg }, adminOrigin);
          }
          console.log(JSON.stringify({ audit: true, actor: user.email, target_user: uid, action: "set_quota", quota_gb: gb, quota_bytes: quotaBytes }));
          return json(200, { quota_gb: gb, quota_bytes: quotaBytes }, adminOrigin);
        }
        if (action === "priority") {
          const body = await request.json().catch(() => ({})) as { priority?: number };
          const prio = Number(body.priority);
          if (!Number.isInteger(prio) || prio < 1 || prio > 1000) {
            return json(400, { error: "priority must be an integer between 1 and 1000 (lower number runs first)" }, adminOrigin);
          }
          const pres = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_set_user_priority`, {
            method: "POST", headers: S,
            body: JSON.stringify({ p_user_id: uid, p_priority: prio }),
          });
          if (!pres.ok) {
            const errData = await pres.json().catch(() => ({})) as { message?: string };
            return json(502, { error: errData.message || `priority update failed: ${pres.status}` }, adminOrigin);
          }
          console.log(JSON.stringify({ audit: true, actor: user.email, target_user: uid, action: "set_priority", priority: prio }));
          return json(200, { priority: prio }, adminOrigin);
        }
        const res = await fetch(`${env.SUPABASE_URL}/auth/v1/admin/users/${uid}`, {
          method: "PUT", headers: S,
          body: JSON.stringify({ ban_duration: action === "ban" ? "876000h" : "none" }),
        });
        if (!res.ok) return json(502, { error: `${action} failed: ${res.status}` }, adminOrigin);
        console.log(JSON.stringify({ audit: true, actor: user.email, target_user: uid, action }));
        return json(200, { status: action === "ban" ? "banned" : "active" }, adminOrigin);
      }
      return json(404, { error: "not found" }, adminOrigin);
    }

    // ---- Connected Accounts & Verification ----
    if (url.pathname === "/accounts" && request.method === "GET") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const res = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_list_connected_accounts`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ p_user_id: user.userId }),
      });
      if (!res.ok) return json(502, { error: `list accounts failed: ${res.status}` });
      const accounts = await res.json() as unknown;
      return json(200, { accounts: Array.isArray(accounts) ? accounts : [] });
    }

    if (url.pathname === "/accounts/connect" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const body = await request.json().catch(() => ({})) as {
        provider?: string;
        account_id?: string;
        account_name?: string;
        account_email?: string | null;
        avatar_url?: string | null;
        credentials?: Record<string, unknown> | string;
        metadata?: Record<string, unknown>;
      };
      const provider = (body.provider ?? "").trim().toLowerCase();
      const validProviders = ["youtube", "instagram", "linkedin", "twitter", "x"];
      const normalizedProvider = provider === "x" ? "twitter" : provider;
      if (!validProviders.includes(provider)) {
        return json(400, { error: `invalid provider '${provider}'. Supported: youtube, instagram, linkedin, twitter` });
      }
      const accountId = (body.account_id ?? "").trim();
      if (!accountId) {
        return json(400, { error: "account_id is required" });
      }
      const accountName = (body.account_name ?? accountId).trim();
      if (!body.credentials) {
        return json(400, { error: "credentials are required" });
      }
      const credString = typeof body.credentials === "string" ? body.credentials : JSON.stringify(body.credentials);
      const enc = await encryptToken(env.TOKEN_VAULT_KEY, credString);

      const res = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_connected_account`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({
          p_user_id: user.userId,
          p_provider: normalizedProvider,
          p_account_id: accountId,
          p_account_name: accountName,
          p_account_email: body.account_email ?? null,
          p_avatar_url: body.avatar_url ?? null,
          p_credentials_encrypted: enc,
          p_metadata: body.metadata ?? {},
        }),
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({})) as { message?: string };
        const errMsg = errJson.message || `storage failed: ${res.status}`;
        if (errMsg.includes("LIMIT_EXCEEDED")) {
          return json(409, { error: errMsg });
        }
        return json(502, { error: errMsg });
      }
      const newId = await res.json() as unknown;
      return json(200, {
        ok: true,
        id: Array.isArray(newId) ? (newId[0] as unknown) : newId,
        provider: normalizedProvider,
        account_id: accountId,
        account_name: accountName
      });
    }

    const deleteAccountMatch = url.pathname.match(/^\/accounts\/([0-9a-fA-F-]{36})$/);
    if (deleteAccountMatch && request.method === "DELETE") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const accountId = deleteAccountMatch[1];
      const res = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_delete_connected_account`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ p_account_id: accountId, p_user_id: user.userId }),
      });
      if (!res.ok) return json(502, { error: `delete account failed: ${res.status}` });
      return json(200, { deleted: true, id: accountId });
    }

    // ---- Per-User Gemini API Keys Vault ----
    if (url.pathname === "/keys/gemini" && request.method === "GET") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const res = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_get_gemini_status`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ p_user_id: user.userId }),
      });
      if (!res.ok) return json(502, { error: "failed to retrieve gemini key status" });
      const statusArr = await res.json() as Array<{ has_key: boolean; key_prefix: string; updated_at: string }>;
      if (Array.isArray(statusArr) && statusArr.length > 0) {
        return json(200, { configured: true, key_prefix: statusArr[0].key_prefix, updated_at: statusArr[0].updated_at });
      }
      return json(200, { configured: false, key_prefix: null });
    }

    if (url.pathname === "/keys/gemini" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const body = await request.json().catch(() => ({})) as { api_key?: string };
      const apiKey = (body.api_key ?? "").trim();
      if (!apiKey || apiKey.length < 30 || apiKey.length > 200 || !apiKey.startsWith("AIza")) {
        return json(400, { error: "Invalid Gemini API key format (must start with AIza...)" });
      }
      const enc = await encryptToken(env.TOKEN_VAULT_KEY, apiKey);
      const prefix = `${apiKey.slice(0, 8)}...${apiKey.slice(-4)}`;
      const res = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_gemini_key`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ p_user_id: user.userId, p_key_encrypted: enc, p_key_prefix: prefix }),
      });
      if (!res.ok) return json(502, { error: "failed to store gemini key" });
      return json(200, { saved: true, key_prefix: prefix });
    }

    if (url.pathname === "/keys/gemini" && request.method === "DELETE") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const res = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_delete_gemini_key`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ p_user_id: user.userId }),
      });
      if (!res.ok) return json(502, { error: "failed to remove gemini key" });
      return json(200, { deleted: true });
    }

    // ---- Multi-Provider Social OAuth (Ticket-based + backward-compatible JWT) ----
    const oauthInitMatch = url.pathname.match(/^\/oauth\/([a-zA-Z0-9_-]+)\/init$/);
    if (oauthInitMatch && request.method === "POST") {
      const rawProvider = oauthInitMatch[1].toLowerCase();
      const provider = rawProvider === "x" ? "twitter" : rawProvider;
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });

      const supported = ["google", "youtube", "instagram", "facebook", "linkedin", "twitter"];
      if (!supported.includes(provider)) {
        return json(400, {
          error: `OAuth flow for ${provider} is not supported. Use direct account credentials connection instead.`,
          manual_connect_available: true
        });
      }

      const ticket = crypto.randomUUID();
      await rpc(env, "cloud_put_oauth_state", { p_state: "ticket:" + ticket, p_user_id: user.userId });
      const targetRoute = (provider === "google" || provider === "youtube") ? "google" : provider;
      return json(200, {
        url: `https://dispatch-cloud.resolviaai.workers.dev/oauth/${targetRoute}/start?ticket=${ticket}`
      });
    }

    // Helper: consume single-use oauth state/ticket and return user_id
    async function consumeOAuthState(pState: string): Promise<string | null> {
      try {
        const consumeRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_consume_oauth_state`, {
          method: "POST",
          headers: {
            apikey: env.SUPABASE_SERVICE_ROLE_KEY,
            Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
            "content-type": "application/json",
          },
          body: JSON.stringify({ p_state: pState }),
        });
        if (!consumeRes.ok) {
          console.error(`cloud_consume_oauth_state failed: HTTP ${consumeRes.status}`);
          return null;
        }
        const consumed = await consumeRes.json() as unknown;
        if (typeof consumed === "string" && consumed.trim().length > 0) return consumed.trim();
        if (Array.isArray(consumed) && consumed.length > 0) {
          const first = consumed[0];
          if (typeof first === "string" && first.trim().length > 0) return first.trim();
          if (typeof first === "object" && first !== null) {
            const val = (first as Record<string, unknown>).cloud_consume_oauth_state;
            if (typeof val === "string" && val.trim().length > 0) return val.trim();
          }
        }
        if (typeof consumed === "object" && consumed !== null) {
          const val = (consumed as Record<string, unknown>).cloud_consume_oauth_state;
          if (typeof val === "string" && val.trim().length > 0) return val.trim();
        }
        return null;
      } catch (err) {
        console.error("cloud_consume_oauth_state error:", err);
        return null;
      }
    }

    // Helper: resolve user ID from ticket or JWT query token
    async function resolveOAuthUser(): Promise<string | null> {
      const ticket = url.searchParams.get("ticket");
      if (ticket) {
        return await consumeOAuthState("ticket:" + ticket);
      }
      const qsToken = url.searchParams.get("token");
      const reqForAuth = qsToken
        ? new Request(request.url, { headers: { Authorization: `Bearer ${qsToken}` } })
        : request;
      const user = await authenticate(env, reqForAuth);
      return user ? user.userId : null;
    }

    // 1. Google / YouTube Start & Callback
    if (url.pathname === "/oauth/google/start" && request.method === "GET") {
      try {
        const uid = await resolveOAuthUser();
        if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization ticket expired or already used. Please retry from Dispatch Settings.", true);
        const state = crypto.randomUUID() + crypto.randomUUID();
        await rpc(env, "cloud_put_oauth_state", { p_state: state, p_user_id: uid });
        return Response.redirect(buildAuthorizeUrl(env.YOUTUBE_CLIENT_ID, env.OAUTH_REDIRECT_URI, state), 302);
      } catch (err: any) {
        console.error("Google OAuth start error:", err);
        return renderOAuthResultHtml("Connection Error", err?.message || "Failed to start Google sign-in. Please retry from Dispatch.", true);
      }
    }

    if (url.pathname === "/oauth/google/callback" && request.method === "GET") {
      const code = url.searchParams.get("code") ?? "";
      const state = url.searchParams.get("state") ?? "";
      if (!code || !state) return renderOAuthResultHtml("Missing Code/State", "Authorization code or state was missing.", true);
      const uid = await consumeOAuthState(state);
      if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization session expired or was already used. Please retry from Dispatch.", true);

      const tokRes = await fetch("https://oauth2.googleapis.com/token", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          code, client_id: env.YOUTUBE_CLIENT_ID, client_secret: env.YOUTUBE_CLIENT_SECRET,
          redirect_uri: env.OAUTH_REDIRECT_URI, grant_type: "authorization_code",
        }),
      });
      const tok = (await tokRes.json()) as Record<string, unknown>;
      if (!tokRes.ok) return renderOAuthResultHtml("Token Exchange Failed", `Google rejected authorization: ${JSON.stringify(tok).slice(0, 100)}`, true);
      const bad = validateTokenResponse(tok);
      if (bad) return renderOAuthResultHtml("Invalid Token", bad, true);

      const enc = await encryptToken(env.TOKEN_VAULT_KEY, tok.refresh_token as string);
      let email: string | null = null;
      try {
        const payload = JSON.parse(atob((tok.id_token as string).split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
        email = typeof payload.email === "string" ? payload.email : null;
      } catch { /* id_token optional */ }

      let channelTitle = "YouTube Channel";
      let channelId = uid;
      let avatarUrl: string | null = null;
      try {
        const chRes = await fetch("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", {
          headers: { Authorization: `Bearer ${tok.access_token}` },
        });
        if (chRes.ok) {
          const chData = await chRes.json() as { items?: Array<{ id: string; snippet?: { title?: string; thumbnails?: { default?: { url?: string } } } }> };
          if (chData.items && chData.items.length > 0) {
            channelId = chData.items[0].id || channelId;
            channelTitle = chData.items[0].snippet?.title || channelTitle;
            avatarUrl = chData.items[0].snippet?.thumbnails?.default?.url || null;
          }
        }
      } catch { /* channel fetch optional */ }

      const storeRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_connected_account`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({
          p_user_id: uid, p_provider: "youtube", p_account_id: channelId, p_account_name: channelTitle,
          p_account_email: email, p_avatar_url: avatarUrl, p_credentials_encrypted: enc,
          p_metadata: { channel_id: channelId, channel_title: channelTitle },
        }),
      });
      if (!storeRes.ok) {
        const errText = await storeRes.text().catch(() => "");
        console.error("Failed to store YouTube account:", storeRes.status, errText);
        return renderOAuthResultHtml("Database Error", `Failed to save YouTube account: ${errText.slice(0, 150)}`, true);
      }

      await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_refresh_token`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ p_user_id: uid, p_refresh_token_encrypted: enc, p_google_email: email }),
      });

      return renderOAuthResultHtml("YouTube Connected", `Channel "${channelTitle}" is connected to Dispatch. You can return to the app.`);
    }

    // 2. Instagram Start & Callback
    if (url.pathname === "/oauth/instagram/start" && request.method === "GET") {
      try {
        const uid = await resolveOAuthUser();
        if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization ticket expired or already used. Please retry from Dispatch Settings.", true);
        const state = crypto.randomUUID() + crypto.randomUUID();
        await rpc(env, "cloud_put_oauth_state", { p_state: state, p_user_id: uid });
        const clientId = env.INSTAGRAM_CLIENT_ID || env.FACEBOOK_CLIENT_ID || "";
        return Response.redirect(buildInstagramAuthorizeUrl(clientId, "https://dispatch-cloud.resolviaai.workers.dev/oauth/instagram/callback", state), 302);
      } catch (err: any) {
        console.error("Instagram OAuth start error:", err);
        return renderOAuthResultHtml("Connection Error", err?.message || "Failed to start Instagram sign-in. Please retry from Dispatch.", true);
      }
    }

    if (url.pathname === "/oauth/instagram/callback" && request.method === "GET") {
      const code = url.searchParams.get("code") ?? "";
      const state = url.searchParams.get("state") ?? "";
      if (!code || !state) return renderOAuthResultHtml("Missing Code/State", "Authorization code or state was missing.", true);
      const uid = await consumeOAuthState(state);
      if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization session expired or was already used. Please retry from Dispatch.", true);

      const clientId = env.INSTAGRAM_CLIENT_ID || env.FACEBOOK_CLIENT_ID || "";
      const clientSecret = env.INSTAGRAM_CLIENT_SECRET || env.FACEBOOK_CLIENT_SECRET || "";
      const redirectUri = "https://dispatch-cloud.resolviaai.workers.dev/oauth/instagram/callback";

      const tokRes = await fetch(`https://graph.facebook.com/v21.0/oauth/access_token?` + new URLSearchParams({
        client_id: clientId, client_secret: clientSecret, redirect_uri: redirectUri, code
      }));
      const tok = await tokRes.json() as Record<string, unknown>;
      if (!tokRes.ok || !tok.access_token) {
        return renderOAuthResultHtml("Instagram Auth Failed", `Token exchange failed: ${JSON.stringify(tok).slice(0, 100)}`, true);
      }

      // Exchange short-lived token for 60-day long-lived token
      let longToken = tok.access_token as string;
      try {
        const longRes = await fetch(`https://graph.facebook.com/v21.0/oauth/access_token?` + new URLSearchParams({
          grant_type: "fb_exchange_token", client_id: clientId, client_secret: clientSecret, fb_exchange_token: longToken
        }));
        if (longRes.ok) {
          const longData = await longRes.json() as Record<string, unknown>;
          if (longData.access_token) longToken = longData.access_token as string;
        }
      } catch { /* fallback to shortToken */ }

      // Fetch Instagram accounts linked to user's Facebook pages
      let igUserId = "me";
      let igUsername = "Instagram User";
      let igAvatar: string | null = null;
      let pageId: string | null = null;
      let pageAccessToken: string | null = null;

      try {
        const pagesRes = await fetch(`https://graph.facebook.com/v21.0/me/accounts?fields=id,name,access_token,instagram_business_account{id,username,profile_picture_url}&access_token=${longToken}`);
        if (pagesRes.ok) {
          const pagesData = await pagesRes.json() as { data?: Array<{ id: string; name: string; access_token?: string; instagram_business_account?: { id: string; username: string; profile_picture_url?: string } }> };
          const found = (pagesData.data || []).find(p => p.instagram_business_account?.id);
          if (found && found.instagram_business_account) {
            igUserId = found.instagram_business_account.id;
            igUsername = "@" + found.instagram_business_account.username;
            igAvatar = found.instagram_business_account.profile_picture_url || null;
            pageId = found.id;
            pageAccessToken = found.access_token || null;
          } else if (pagesData.data && pagesData.data.length > 0) {
            const first = pagesData.data[0];
            pageId = first.id;
            igUserId = first.id;
            igUsername = first.name;
            pageAccessToken = first.access_token || null;
          }
        }
      } catch { /* use defaults */ }

      const credPayload = {
        access_token: pageAccessToken || longToken,
        user_access_token: longToken,
        page_id: pageId,
        ig_user_id: igUserId,
        account_id: igUserId
      };
      const enc = await encryptToken(env.TOKEN_VAULT_KEY, JSON.stringify(credPayload));

      const storeRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_connected_account`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({
          p_user_id: uid, p_provider: "instagram", p_account_id: igUserId, p_account_name: igUsername,
          p_account_email: null, p_avatar_url: igAvatar, p_credentials_encrypted: enc,
          p_metadata: { ig_user_id: igUserId, page_id: pageId, username: igUsername },
        }),
      });
      if (!storeRes.ok) {
        const errText = await storeRes.text().catch(() => "");
        console.error("Failed to store Instagram account:", storeRes.status, errText);
        return renderOAuthResultHtml("Database Error", `Failed to save Instagram account: ${errText.slice(0, 150)}`, true);
      }

      return renderOAuthResultHtml("Instagram Connected", `Account "${igUsername}" connected successfully! You can return to Dispatch.`);
    }

    // 3. Facebook Start & Callback
    if (url.pathname === "/oauth/facebook/start" && request.method === "GET") {
      try {
        const uid = await resolveOAuthUser();
        if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization ticket expired or already used. Please retry from Dispatch Settings.", true);
        const state = crypto.randomUUID() + crypto.randomUUID();
        await rpc(env, "cloud_put_oauth_state", { p_state: state, p_user_id: uid });
        const clientId = env.FACEBOOK_CLIENT_ID || env.INSTAGRAM_CLIENT_ID || "";
        return Response.redirect(buildFacebookAuthorizeUrl(clientId, "https://dispatch-cloud.resolviaai.workers.dev/oauth/facebook/callback", state), 302);
      } catch (err: any) {
        console.error("Facebook OAuth start error:", err);
        return renderOAuthResultHtml("Connection Error", err?.message || "Failed to start Facebook sign-in. Please retry from Dispatch.", true);
      }
    }

    if (url.pathname === "/oauth/facebook/callback" && request.method === "GET") {
      const code = url.searchParams.get("code") ?? "";
      const state = url.searchParams.get("state") ?? "";
      if (!code || !state) return renderOAuthResultHtml("Missing Code/State", "Authorization code or state was missing.", true);
      const uid = await consumeOAuthState(state);
      if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization session expired or was already used. Please retry from Dispatch.", true);

      const clientId = env.FACEBOOK_CLIENT_ID || env.INSTAGRAM_CLIENT_ID || "";
      const clientSecret = env.FACEBOOK_CLIENT_SECRET || env.INSTAGRAM_CLIENT_SECRET || "";
      const redirectUri = "https://dispatch-cloud.resolviaai.workers.dev/oauth/facebook/callback";

      const tokRes = await fetch(`https://graph.facebook.com/v21.0/oauth/access_token?` + new URLSearchParams({
        client_id: clientId, client_secret: clientSecret, redirect_uri: redirectUri, code
      }));
      const tok = await tokRes.json() as Record<string, unknown>;
      if (!tokRes.ok || !tok.access_token) {
        return renderOAuthResultHtml("Facebook Auth Failed", `Token exchange failed: ${JSON.stringify(tok).slice(0, 100)}`, true);
      }

      let longToken = tok.access_token as string;
      try {
        const longRes = await fetch(`https://graph.facebook.com/v21.0/oauth/access_token?` + new URLSearchParams({
          grant_type: "fb_exchange_token", client_id: clientId, client_secret: clientSecret, fb_exchange_token: longToken
        }));
        if (longRes.ok) {
          const longData = await longRes.json() as Record<string, unknown>;
          if (longData.access_token) longToken = longData.access_token as string;
        }
      } catch { /* fallback */ }

      let pageId = "me";
      let pageTitle = "Facebook Page";
      let pageAccessToken: string | null = null;
      try {
        const pagesRes = await fetch(`https://graph.facebook.com/v21.0/me/accounts?fields=id,name,access_token&access_token=${longToken}`);
        if (pagesRes.ok) {
          const pagesData = await pagesRes.json() as { data?: Array<{ id: string; name: string; access_token?: string }> };
          if (pagesData.data && pagesData.data.length > 0) {
            pageId = pagesData.data[0].id;
            pageTitle = pagesData.data[0].name;
            pageAccessToken = pagesData.data[0].access_token || null;
          }
        }
      } catch { /* fallback */ }

      const credPayload = {
        access_token: pageAccessToken || longToken,
        user_access_token: longToken,
        page_id: pageId,
        account_id: pageId
      };
      const enc = await encryptToken(env.TOKEN_VAULT_KEY, JSON.stringify(credPayload));

      const storeRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_connected_account`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({
          p_user_id: uid, p_provider: "facebook", p_account_id: pageId, p_account_name: pageTitle,
          p_account_email: null, p_avatar_url: null, p_credentials_encrypted: enc,
          p_metadata: { page_id: pageId, title: pageTitle },
        }),
      });
      if (!storeRes.ok) {
        const errText = await storeRes.text().catch(() => "");
        console.error("Failed to store Facebook account:", storeRes.status, errText);
        return renderOAuthResultHtml("Database Error", `Failed to save Facebook account: ${errText.slice(0, 150)}`, true);
      }

      return renderOAuthResultHtml("Facebook Connected", `Page "${pageTitle}" connected successfully! You can return to Dispatch.`);
    }

    // 4. LinkedIn Start & Callback
    if (url.pathname === "/oauth/linkedin/start" && request.method === "GET") {
      try {
        const uid = await resolveOAuthUser();
        if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization ticket expired or already used. Please retry from Dispatch Settings.", true);
        const state = crypto.randomUUID() + crypto.randomUUID();
        await rpc(env, "cloud_put_oauth_state", { p_state: state, p_user_id: uid });
        return Response.redirect(buildLinkedInAuthorizeUrl(env.LINKEDIN_CLIENT_ID || "", "https://dispatch-cloud.resolviaai.workers.dev/oauth/linkedin/callback", state), 302);
      } catch (err: any) {
        console.error("LinkedIn OAuth start error:", err);
        return renderOAuthResultHtml("Connection Error", err?.message || "Failed to start LinkedIn sign-in. Please retry from Dispatch.", true);
      }
    }

    if (url.pathname === "/oauth/linkedin/callback" && request.method === "GET") {
      const code = url.searchParams.get("code") ?? "";
      const state = url.searchParams.get("state") ?? "";
      if (!code || !state) return renderOAuthResultHtml("Missing Code/State", "Authorization code or state was missing.", true);
      const uid = await consumeOAuthState(state);
      if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization session expired or was already used. Please retry from Dispatch.", true);

      const redirectUri = "https://dispatch-cloud.resolviaai.workers.dev/oauth/linkedin/callback";
      const tokRes = await fetch("https://www.linkedin.com/oauth/v2/accessToken", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          code,
          redirect_uri: redirectUri,
          client_id: env.LINKEDIN_CLIENT_ID || "",
          client_secret: env.LINKEDIN_CLIENT_SECRET || "",
        }),
      });
      const tok = await tokRes.json() as Record<string, unknown>;
      if (!tokRes.ok || !tok.access_token) {
        return renderOAuthResultHtml("LinkedIn Auth Failed", `Token exchange failed: ${JSON.stringify(tok).slice(0, 100)}`, true);
      }

      let authorUrn = "urn:li:person:me";
      let profileName = "LinkedIn User";
      let avatarUrl: string | null = null;
      let email: string | null = null;

      try {
        const ures = await fetch("https://api.linkedin.com/v2/userinfo", {
          headers: { Authorization: `Bearer ${tok.access_token}` },
        });
        if (ures.ok) {
          const uinfo = await ures.json() as { sub: string; name?: string; picture?: string; email?: string };
          authorUrn = `urn:li:person:${uinfo.sub}`;
          profileName = uinfo.name || profileName;
          avatarUrl = uinfo.picture || null;
          email = uinfo.email || null;
        }
      } catch { /* use defaults */ }

      const credPayload = {
        access_token: tok.access_token,
        refresh_token: tok.refresh_token ?? null,
        author_urn: authorUrn,
        account_id: authorUrn
      };
      const enc = await encryptToken(env.TOKEN_VAULT_KEY, JSON.stringify(credPayload));

      const storeRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_connected_account`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({
          p_user_id: uid, p_provider: "linkedin", p_account_id: authorUrn, p_account_name: profileName,
          p_account_email: email, p_avatar_url: avatarUrl, p_credentials_encrypted: enc,
          p_metadata: { author_urn: authorUrn, name: profileName },
        }),
      });
      if (!storeRes.ok) {
        const errText = await storeRes.text().catch(() => "");
        console.error("Failed to store LinkedIn account:", storeRes.status, errText);
        return renderOAuthResultHtml("Database Error", `Failed to save LinkedIn account: ${errText.slice(0, 150)}`, true);
      }

      return renderOAuthResultHtml("LinkedIn Connected", `Profile "${profileName}" connected successfully! You can return to Dispatch.`);
    }

    // 5. X / Twitter (OAuth 2.0 PKCE) Start & Callback
    if (url.pathname === "/oauth/twitter/start" && request.method === "GET") {
      try {
        const uid = await resolveOAuthUser();
        if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization ticket expired or already used. Please retry from Dispatch Settings.", true);
        const rawState = crypto.randomUUID() + crypto.randomUUID();
        const codeVerifier = generateRandomString(48);
        const codeChallenge = await sha256B64Url(codeVerifier);
        await rpc(env, "cloud_put_oauth_state", { p_state: rawState, p_user_id: uid });
        const encryptedState = await encryptToken(env.TOKEN_VAULT_KEY, `${rawState}:::${codeVerifier}`);
        return Response.redirect(
          buildTwitterAuthorizeUrl(env.TWITTER_CLIENT_ID || "", "https://dispatch-cloud.resolviaai.workers.dev/oauth/twitter/callback", encryptedState, codeChallenge),
          302
        );
      } catch (err: any) {
        console.error("Twitter OAuth start error:", err);
        return renderOAuthResultHtml("Connection Error", err?.message || "Failed to start X (Twitter) sign-in. Please retry from Dispatch.", true);
      }
    }

    if (url.pathname === "/oauth/twitter/callback" && request.method === "GET") {
      const code = url.searchParams.get("code") ?? "";
      const stateParam = url.searchParams.get("state") ?? "";
      if (!code || !stateParam) return renderOAuthResultHtml("Missing Code/State", "Authorization code or state was missing.", true);

      let rawState = "";
      let codeVerifier = "";
      try {
        const decrypted = await decryptToken(env.TOKEN_VAULT_KEY, stateParam);
        const parts = decrypted.split(":::");
        rawState = parts[0] || "";
        codeVerifier = parts[1] || "";
      } catch {
        return renderOAuthResultHtml("Invalid State", "State validation failed.", true);
      }

      const uid = await consumeOAuthState(rawState);
      if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization session expired. Please retry from Dispatch.", true);

      const redirectUri = "https://dispatch-cloud.resolviaai.workers.dev/oauth/twitter/callback";
      const basicAuth = btoa(`${env.TWITTER_CLIENT_ID || ""}:${env.TWITTER_CLIENT_SECRET || ""}`);
      const tokRes = await fetch("https://api.twitter.com/2/oauth2/token", {
        method: "POST",
        headers: {
          "content-type": "application/x-www-form-urlencoded",
          Authorization: `Basic ${basicAuth}`,
        },
        body: new URLSearchParams({
          code,
          grant_type: "authorization_code",
          redirect_uri: redirectUri,
          code_verifier: codeVerifier,
        }),
      });
      const tok = await tokRes.json() as Record<string, unknown>;
      if (!tokRes.ok || !tok.access_token) {
        return renderOAuthResultHtml("X Auth Failed", `Token exchange failed: ${JSON.stringify(tok).slice(0, 100)}`, true);
      }

      let twitterHandle = "X User";
      let twitterId = "me";
      let avatarUrl: string | null = null;

      try {
        const ures = await fetch("https://api.twitter.com/2/users/me?user.fields=profile_image_url,username,name", {
          headers: { Authorization: `Bearer ${tok.access_token}` },
        });
        if (ures.ok) {
          const udata = await ures.json() as { data?: { id: string; name: string; username: string; profile_image_url?: string } };
          if (udata.data) {
            twitterId = udata.data.id;
            twitterHandle = "@" + udata.data.username;
            avatarUrl = udata.data.profile_image_url || null;
          }
        }
      } catch { /* use defaults */ }

      const credPayload = {
        access_token: tok.access_token,
        refresh_token: tok.refresh_token ?? null,
        bearer_token: tok.access_token,
        account_id: twitterId,
        username: twitterHandle
      };
      const enc = await encryptToken(env.TOKEN_VAULT_KEY, JSON.stringify(credPayload));

      const storeRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_connected_account`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({
          p_user_id: uid, p_provider: "twitter", p_account_id: twitterId, p_account_name: twitterHandle,
          p_account_email: null, p_avatar_url: avatarUrl, p_credentials_encrypted: enc,
          p_metadata: { twitter_id: twitterId, handle: twitterHandle },
        }),
      });
      if (!storeRes.ok) {
        const errText = await storeRes.text().catch(() => "");
        console.error("Failed to store Twitter/X account:", storeRes.status, errText);
        return renderOAuthResultHtml("Database Error", `Failed to save X account: ${errText.slice(0, 150)}`, true);
      }

      return renderOAuthResultHtml("X (Twitter) Connected", `Account "${twitterHandle}" connected successfully! You can return to Dispatch.`);
    }

    // ---- Remote processing jobs (authenticated app clients, any network) ----
    if (url.pathname === "/jobs/submit" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const body = await request.json().catch(() => ({})) as { url?: string };
      const ytUrl = (body.url ?? "").trim();
      if (!/^https:\/\/(www\.)?(youtube\.com\/(watch\?v=|shorts\/)|youtu\.be\/)[A-Za-z0-9_-]{6,20}/.test(ytUrl))
        return json(400, { error: "invalid YouTube URL" });
      const sub = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_submit_job`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ p_user_id: user.userId, p_url: ytUrl }),
      });
      if (!sub.ok) return json(502, { error: `submit failed: ${sub.status}` });
      const subBody = await sub.json() as unknown;
      const arr = Array.isArray(subBody) ? subBody as Array<{ id: string; status: string }> : [];
      return json(200, { job_id: arr[0]?.id ?? null, status: arr[0]?.status ?? "queued" });
    }
    if (url.pathname === "/jobs/status" && request.method === "GET") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const res = await fetch(
        `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=eq.${user.userId}&select=id,youtube_url,status,video_id,last_error,created_at&order=created_at.desc&limit=20`,
        { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } });
      if (!res.ok) return json(502, { error: `status failed: ${res.status}` });
      return json(200, { jobs: await res.json() });
    }

    // ---- Cloud Clips (authenticated clients on any network / cellular) ----
    if (url.pathname === "/clips" && request.method === "GET") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const [clipsRes, jobsRes] = await Promise.all([
        fetch(
          `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${user.userId}&select=id,local_clip_id,title,status,start_time,end_time,range_revision,render_stale,cloud_updated_at&order=cloud_updated_at.desc&limit=50`,
          { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
        ),
        fetch(
          `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=eq.${user.userId}&status=eq.queued&select=id`,
          { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
        ),
      ]);
      const rawClips = clipsRes.ok ? (await clipsRes.json() as Array<Record<string, unknown>>) : [];
      const ready = rawClips
        .filter(c => c.status === "ready_review" || c.status === "ready")
        .map(c => ({
          id: String(c.local_clip_id || c.id),
          cloud_id: c.id,
          title: c.title,
          status: "ready",
          start_time: Number(c.start_time || 0),
          end_time: Number(c.end_time || 0),
          duration: Math.max(0, Number(c.end_time || 0) - Number(c.start_time || 0)),
          range_revision: Number(c.range_revision || 0),
          created_at: c.cloud_updated_at,
          is_cloud: true,
        }));
      const recent_approved = rawClips
        .filter(c => c.status === "approved" || c.status === "published")
        .map(c => ({
          id: String(c.local_clip_id || c.id),
          cloud_id: c.id,
          title: c.title,
          status: c.status,
          start_time: Number(c.start_time || 0),
          end_time: Number(c.end_time || 0),
          duration: Math.max(0, Number(c.end_time || 0) - Number(c.start_time || 0)),
          range_revision: Number(c.range_revision || 0),
          created_at: c.cloud_updated_at,
          is_cloud: true,
        }));
      const queuedJobs = jobsRes.ok ? (await jobsRes.json() as unknown[]) : [];
      return json(200, {
        ready,
        recent_approved,
        queue_stats: { queued_count: Array.isArray(queuedJobs) ? queuedJobs.length : 0, daily_quota_used: 0, daily_quota_max: 10 },
        cloud_mode: true,
      });
    }

    const actor = await authenticate(env, request);
    if (!actor) return json(401, { error: "unauthorized" });
    const userId = actor.userId;

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
    } catch (e: unknown) {
      console.error("Worker unhandled exception:", e);
      const isHtml = request.headers.get("accept")?.includes("text/html") || request.method === "GET";
      if (isHtml) {
        return renderOAuthResultHtml("Temporary Service Error", (e instanceof Error ? e.message : String(e)) || "An unexpected error occurred. Please retry from Dispatch.", true);
      }
      return json(500, { error: `worker error: ${e instanceof Error ? e.message : String(e)}` });
    }
  },

  async scheduled(_event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    // Scheduled cleanup (see [triggers] in wrangler.toml): abandoned staging,
    // promotion leftovers, and the staging-watch sweep run unattended.
    ctx.waitUntil(runCleanup(env).then((r) => console.log(`scheduled cleanup: ${r.status}`)));
  },
};
