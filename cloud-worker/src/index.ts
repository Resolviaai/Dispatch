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
// Tolerance for PC/cloud clock drift when enforcing the reject purge deadline.
const PURGE_CLOCK_SKEW_MS = 5 * 60_000;
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

// Multi-row table-returning RPCs (claim batches, live command lists).
async function rpcRaw(env: Env, fn: string, params: Record<string, unknown>): Promise<unknown> {
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/${fn}`, {
    method: "POST",
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`rpc ${fn} failed: ${res.status} ${text.slice(0, 200)}`);
  }
  return await res.json();
}

async function selectOne<T>(env: Env, table: string, id: string, columns: string): Promise<T | null> {
  const res = await fetch(
    `${env.SUPABASE_URL}/rest/v1/${table}?id=eq.${encodeURIComponent(id)}&select=${columns}`,
    { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } });
  if (!res.ok) throw new Error(`select ${table} failed: ${res.status}`);
  return ((await res.json()) as T[])[0] ?? null;
}

async function getUserYouTubeAccessToken(env: Env, accountId: string, userId: string): Promise<string | null> {
  const accountUrl = new URL(`${env.SUPABASE_URL}/rest/v1/connected_accounts`);
  accountUrl.searchParams.set("id", `eq.${accountId}`);
  accountUrl.searchParams.set("user_id", `eq.${userId}`);
  accountUrl.searchParams.set("provider", "eq.youtube");
  accountUrl.searchParams.set("select", "credentials_encrypted,metadata");
  const accountRes = await fetch(accountUrl, {
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
    },
  });
  if (!accountRes.ok) throw new Error(`YouTube account lookup failed: ${accountRes.status}`);
  const rows = await accountRes.json() as Array<{ credentials_encrypted?: string; metadata?: Record<string, unknown> }>;
  const account = rows[0];
  if (!account?.credentials_encrypted || account.metadata?.is_source_vault === false) return null;

  const stored = await decryptToken(env.TOKEN_VAULT_KEY, account.credentials_encrypted);
  let refreshToken = stored.trim();
  try {
    const parsed = JSON.parse(stored) as { refresh_token?: unknown; token?: unknown };
    refreshToken = typeof parsed.refresh_token === "string" ? parsed.refresh_token :
      typeof parsed.token === "string" ? parsed.token : "";
  } catch { /* OAuth callback stores a bare refresh-token string. */ }
  if (!refreshToken) return null;

  const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: env.YOUTUBE_CLIENT_ID,
      client_secret: env.YOUTUBE_CLIENT_SECRET,
      refresh_token: refreshToken,
      grant_type: "refresh_token",
    }),
  });
  if (!tokenRes.ok) return null;
  const tokenBody = await tokenRes.json() as { access_token?: unknown };
  return typeof tokenBody.access_token === "string" ? tokenBody.access_token : null;
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

// PostgREST `in.()` list with double-quoted values (" doubled as escape) so
// local clip ids containing commas or quotes stay single filter tokens.
export function postgrestInList(values: string[]): string {
  return values.filter(v => v.length > 0).map(v => `"${v.replace(/"/g, '""')}"`).join(",");
}

// Worker-token bearer check: distinguishes the trusted PC identity from an
// ordinary Supabase end-user JWT (2026-10-27 audit: PC-only routes must not be
// reachable with a plain user session).
export function isPcWorkerAuth(env: Env, request: Request): boolean {
  const auth = request.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ") || !env.DISPATCH_WORKER_TOKEN) return false;
  return auth.slice(7) === env.DISPATCH_WORKER_TOKEN;
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

    if (deleteAccountMatch && (request.method === "PATCH" || request.method === "POST")) {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const accountId = deleteAccountMatch[1];
      const body = await request.json().catch(() => ({})) as {
        is_source_vault?: boolean;
        is_publisher?: boolean;
      };
      const getRes = await fetch(`${env.SUPABASE_URL}/rest/v1/connected_accounts?id=eq.${accountId}&user_id=eq.${user.userId}&select=metadata,provider`, {
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` },
      });
      if (!getRes.ok) return json(502, { error: "failed to retrieve account" });
      const rows = await getRes.json() as Array<{ metadata?: Record<string, unknown>; provider: string }>;
      if (!rows.length) return json(404, { error: "account not found" });
      const currentMeta = rows[0].metadata || {};
      const updatedMeta = {
        ...currentMeta,
        ...(typeof body.is_source_vault === "boolean" ? { is_source_vault: body.is_source_vault } : {}),
        ...(typeof body.is_publisher === "boolean" ? { is_publisher: body.is_publisher } : {}),
      };
      const updateRes = await fetch(`${env.SUPABASE_URL}/rest/v1/connected_accounts?id=eq.${accountId}&user_id=eq.${user.userId}`, {
        method: "PATCH",
        headers: {
          apikey: env.SUPABASE_SERVICE_ROLE_KEY,
          Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
          "content-type": "application/json",
          "prefer": "return=minimal"
        },
        body: JSON.stringify({ metadata: updatedMeta, updated_at: new Date().toISOString() })
      });
      if (!updateRes.ok) return json(502, { error: "failed to update account roles" });
      return json(200, { ok: true, metadata: updatedMeta });
    }

    if (url.pathname === "/youtube/upload-sessions" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" }, reqOrigin);
      const body = await request.json().catch(() => ({})) as {
        account_id?: string;
        segment_id?: string;
        session_id?: string;
        sequence_number?: number;
        dispatch_id?: string;
        sha256?: string;
        file_size?: number;
      };
      const accountId = typeof body.account_id === "string" ? body.account_id.trim() : "";
      const segmentId = typeof body.segment_id === "string" ? body.segment_id.trim() : "";
      const sessionId = typeof body.session_id === "string" ? body.session_id.trim() : "";
      const dispatchId = typeof body.dispatch_id === "string" ? body.dispatch_id.trim() : "";
      const fileSize = body.file_size;
      if (!/^[0-9a-f-]{36}$/i.test(accountId) || !segmentId || segmentId.length > 128 ||
          !sessionId || sessionId.length > 128 || !/^[a-zA-Z0-9_-]{1,128}$/.test(dispatchId) ||
          !Number.isInteger(body.sequence_number) || (body.sequence_number ?? 0) < 1 ||
          !Number.isSafeInteger(fileSize) || (fileSize ?? 0) <= 0 || (fileSize ?? 0) > 5 * 1024 ** 3) {
        return json(400, { error: "invalid upload session request" }, reqOrigin);
      }

      try {
        const accessToken = await getUserYouTubeAccessToken(env, accountId, user.userId);
        if (!accessToken) return json(409, { error: "raw vault account is disconnected or not authorized for raw uploads" }, reqOrigin);
        const now = new Date().toISOString().slice(0, 16).replace("T", " ");
        const sha = typeof body.sha256 === "string" ? body.sha256.slice(0, 128) : "";
        const title = `[DISPATCH] Session ${sessionId.slice(-6)} - Part ${body.sequence_number} (${now})`;
        const description = `dispatch_id: ${dispatchId}\nsession_id: ${sessionId}\nsegment_id: ${segmentId}\nsequence_number: ${body.sequence_number}\nsource_sha256: ${sha}\nsource_file_size: ${fileSize}\nAutonomous mobile recording uploaded by Dispatch.`;
        const metadata = {
          snippet: {
            title,
            description,
            tags: ["dispatch", `dispatch_id_${dispatchId}`, `dispatch_session_${sessionId}`, `dispatch_seg_${body.sequence_number}`, ...(sha ? [`sha256_${sha}`] : [])],
            categoryId: "22",
          },
          status: { privacyStatus: "unlisted", selfDeclaredMadeForKids: false },
        };
        const initRes = await fetch("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${accessToken}`,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": String(fileSize),
          },
          body: JSON.stringify(metadata),
        });
        if (!initRes.ok) {
          console.error("YouTube resumable session init failed", initRes.status);
          return json(502, { error: "YouTube could not start the upload session" }, reqOrigin);
        }
        const uploadUrl = initRes.headers.get("Location");
        if (!uploadUrl || new URL(uploadUrl).hostname !== "www.googleapis.com") {
          return json(502, { error: "YouTube returned an invalid upload session" }, reqOrigin);
        }
        return new Response(JSON.stringify({ upload_url: uploadUrl, account_id: accountId }), {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "Cache-Control": "no-store, private",
            "Access-Control-Allow-Origin": reqOrigin,
            "Vary": "Origin",
          },
        });
      } catch (error) {
        console.error("YouTube upload session setup failed", error instanceof Error ? error.message : "unknown error");
        return json(502, { error: "Could not prepare a YouTube upload" }, reqOrigin);
      }
    }

    if (url.pathname === "/youtube/uploads/lookup" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" }, reqOrigin);
      const body = await request.json().catch(() => ({})) as { account_id?: string; dispatch_id?: string };
      const accountId = typeof body.account_id === "string" ? body.account_id.trim() : "";
      const dispatchId = typeof body.dispatch_id === "string" ? body.dispatch_id.trim() : "";
      if (!/^[0-9a-f-]{36}$/i.test(accountId) || !/^[a-zA-Z0-9_-]{1,128}$/.test(dispatchId)) {
        return json(400, { error: "invalid upload lookup request" }, reqOrigin);
      }
      try {
        const accessToken = await getUserYouTubeAccessToken(env, accountId, user.userId);
        if (!accessToken) return json(409, { error: "raw vault account is unavailable" }, reqOrigin);
        const query = new URLSearchParams({
          part: "snippet", forMine: "true", q: `dispatch_id_${dispatchId}`, type: "video", maxResults: "1",
        });
        const response = await fetch(`https://www.googleapis.com/youtube/v3/search?${query}`, {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (!response.ok) return json(502, { error: "YouTube upload lookup failed" }, reqOrigin);
        const data = await response.json() as { items?: Array<{ id?: { videoId?: string } }> };
        const videoId = data.items?.[0]?.id?.videoId ?? null;
        return json(200, { video_id: videoId }, reqOrigin);
      } catch (error) {
        console.error("YouTube upload lookup failed", error instanceof Error ? error.message : "unknown error");
        return json(502, { error: "Could not check YouTube upload status" }, reqOrigin);
      }
    }

    if (url.pathname === "/youtube/videos/status" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" }, reqOrigin);
      const body = await request.json().catch(() => ({})) as { account_id?: string; video_id?: string };
      const accountId = typeof body.account_id === "string" ? body.account_id.trim() : "";
      const videoId = typeof body.video_id === "string" ? body.video_id.trim() : "";
      if (!/^[0-9a-f-]{36}$/i.test(accountId) || !/^[a-zA-Z0-9_-]{6,32}$/.test(videoId)) {
        return json(400, { error: "invalid YouTube status request" }, reqOrigin);
      }
      try {
        const accessToken = await getUserYouTubeAccessToken(env, accountId, user.userId);
        if (!accessToken) return json(409, { error: "raw vault account is unavailable" }, reqOrigin);
        const query = new URLSearchParams({ part: "processingDetails,status", id: videoId });
        const response = await fetch(`https://www.googleapis.com/youtube/v3/videos?${query}`, {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (!response.ok) return json(502, { error: "YouTube processing check failed" }, reqOrigin);
        const data = await response.json() as { items?: Array<{ status?: { uploadStatus?: string }; processingDetails?: { processingStatus?: string } }> };
        const item = data.items?.[0];
        if (!item) return json(200, { status: "not_found" }, reqOrigin);
        const upload = item.status?.uploadStatus ?? "";
        const processing = item.processingDetails?.processingStatus ?? "";
        const status = processing === "succeeded" ? "succeeded" :
          processing === "failed" || upload === "rejected" || upload === "failed" ? "failed" :
          processing === "processing" || upload === "uploaded" ? "processing" : "unknown";
        return json(200, { status }, reqOrigin);
      } catch (error) {
        console.error("YouTube processing check failed", error instanceof Error ? error.message : "unknown error");
        return json(502, { error: "Could not check YouTube processing status" }, reqOrigin);
      }
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

      // Audit 2026-10-27: limit/offset pagination so accounts with more clips
      // can page through them instead of silently missing older rows.
      const limitRaw = Number(url.searchParams.get("limit") ?? 50);
      const offsetRaw = Number(url.searchParams.get("offset") ?? 0);
      const limit = Math.min(Math.max(Number.isFinite(limitRaw) ? Math.floor(limitRaw) : 50, 1), 200);
      const offset = Math.min(Math.max(Number.isFinite(offsetRaw) ? Math.floor(offsetRaw) : 0, 0), 100000);

      // Audit 2026-10-27: the PRIMARY listing query must fail loudly. A failed
      // Supabase round trip previously degraded to an empty 200 response, which
      // wipe-cleaned; now it returns 502 so clients can keep their last data.
      const clipsRes = await fetch(
        `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${user.userId}&select=id,local_clip_id,title,status,start_time,end_time,ai_suggested_start_time,ai_suggested_end_time,range_revision,render_stale,caption_data,caption_revision,cloud_updated_at&order=cloud_updated_at.desc&limit=${limit}&offset=${offset}`,
        { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
      );
      if (!clipsRes.ok) {
        const text = await clipsRes.text().catch(() => "");
        return json(502, { error: `clip listing failed: HTTP ${clipsRes.status}`, detail: text.slice(0, 200) });
      }
      const parsedClips = await clipsRes.json() as unknown;
      if (!Array.isArray(parsedClips)) {
        // A malformed (non-array) body is an upstream failure, never a clean
        // empty list — fail loudly instead of crash-rendering an error page.
        return json(502, { error: "clip listing failed: unexpected response shape" });
      }
      const rawClips = parsedClips as Array<Record<string, unknown>>;

      // Auxiliary queries are scoped to exactly the clips that were returned
      // (no window truncation at odds with the clip page) and failures surface
      // as degraded flags instead of silently empty collections.
      const localClipIds = postgrestInList(rawClips.map(c => String(c.local_clip_id || c.id)));
      const cloudClipIds = postgrestInList(rawClips.map(c => String(c.id)));
      let jobsRes: Response | null = null;
      let mediaRes: Response | null = null;
      let editWindowsRes: Response | null = null;
      if (rawClips.length > 0) {
        const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
        [jobsRes, mediaRes, editWindowsRes] = await Promise.all([
          fetch(
            `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=eq.${user.userId}&status=eq.queued&select=id`,
            { headers: svc }
          ).catch(() => null),
          fetch(
            `${env.SUPABASE_URL}/rest/v1/media_objects?user_id=eq.${user.userId}&status=eq.active&clip_id=in.(${cloudClipIds})&select=id,clip_id,kind,mime_type,final_key&order=created_at.desc,id.desc`,
            { headers: svc }
          ).catch(() => null),
          fetch(
            `${env.SUPABASE_URL}/rest/v1/edit_windows?user_id=eq.${user.userId}&status=eq.ready&local_clip_id=in.(${localClipIds})&select=id,local_clip_id,window_start,window_end,media_object_id&order=created_at.desc`,
            { headers: svc }
          ).catch(() => null),
        ]);
      }
      const mediaDegraded = !mediaRes || !mediaRes.ok;
      const windowsDegraded = !editWindowsRes || !editWindowsRes.ok;
      const rawMedia = mediaRes && mediaRes.ok ? await mediaRes.json() : [];
      const activeMedia = Array.isArray(rawMedia) ? (rawMedia as Array<Record<string, unknown>>) : [];
      const rawEw = editWindowsRes && editWindowsRes.ok ? await editWindowsRes.json() : [];
      const readyWindows = Array.isArray(rawEw) ? (rawEw as Array<Record<string, unknown>>) : [];

      // Cloud caption contract: window transcripts sit in window_transcripts
      // (words SOURCE-relative seconds). Fetch the batch for this user's ready
      // windows so offline editing can seed a caption track without the PC.
      const transcriptMap = new Map<string, Array<Record<string, unknown>>>();
      if (readyWindows.length > 0) {
        const windowIds = readyWindows.map(w => String(w.id)).slice(0, 100);
        if (windowIds.length > 0) {
          try {
            const trRes = await fetch(
              `${env.SUPABASE_URL}/rest/v1/window_transcripts?window_id=in.(${windowIds.map(id => `"${id}"`).join(",")})&select=window_id,words`,
              { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
            );
            if (trRes.ok) {
              const rows = await trRes.json() as Array<{ window_id: string; words: unknown }>;
              for (const row of rows) {
                const words = Array.isArray(row?.words) ? (row.words as Array<Record<string, unknown>>) : [];
                if (words.length > 0) transcriptMap.set(String(row.window_id), words);
              }
            }
          } catch {
            // Captions stay absent if transcripts cannot be loaded; clips still render.
          }
        }
      }

      // Audit 2026-10-27: a saved edited caption track is returned INDEPENDENT
      // of whether a ready edit window exists (pc-consistency + offline editing
      // no longer depend on a transcript proxy). Returns source so clients can
      // distinguish an edited track from a window transcript seed.
      const buildCloudCaptions = (clip: Record<string, unknown>, clipEw: Record<string, unknown> | null): { captions: Record<string, unknown> | undefined; source: string | undefined } => {
        const savedCaption = clip.caption_data;
        if (savedCaption && typeof savedCaption === "object" && !Array.isArray(savedCaption)) {
          const savedGroups = (savedCaption as Record<string, unknown>).groups;
          if (Array.isArray(savedGroups) && savedGroups.length > 0) {
            return { captions: savedCaption as Record<string, unknown>, source: "edited" };
          }
        }
        if (!clipEw) return { captions: undefined, source: undefined };
        const words = transcriptMap.get(String(clipEw.id));
        if (!words || words.length === 0) return { captions: undefined, source: undefined };
        const windowStart = Number(clipEw.window_start || 0);
        const windowEnd = Number(clipEw.window_end || 0);
        const startWindow = windowEnd > windowStart ? windowStart : 0;
        const relative = words
          .map(word => ({
            w: String(word.w ?? word.text ?? ""),
            start: Number(word.start) - startWindow,
            end: Number(word.end) - startWindow,
          }))
          .filter(word => Number.isFinite(word.start) && Number.isFinite(word.end) && word.end > 0);
        if (relative.length === 0) return { captions: undefined, source: undefined };
        const clipEnd = Number(clip.end_time || 0);
        const clipStart = Number(clip.start_time || 0);
        // The editor renders against the window proxy span (window_end - window_start),
        // but the clip itself may sit inside a larger window; keep words clipped to span.
        const spanDuration = Math.max(0.1, windowEnd - startWindow, clipEnd - clipStart);
        return {
          captions: {
            words: relative.filter(word => word.start < spanDuration),
            span_duration: spanDuration,
          },
          source: "window_seed",
        };
      };

      // Audit 2026-10-27: deterministic, kind-aware media selection. A clip's
      // rendered video is the media object with kind='clip_video' linked to the
      // cloud clip; edit-window proxies only surface through their ready window
      // row; ordering comes from the query (created_at.desc, id.desc). A
      // thumbnail can never stand in for the video URL again.
      const mapClipItem = async (c: Record<string, unknown>, targetStatus: string) => {
        const localId = String(c.local_clip_id || c.id);
        const cloudClipId = String(c.id);
        const clipEw = readyWindows.find(w => String(w.local_clip_id) === localId) ?? null;
        const ewMo = clipEw
          ? activeMedia.find(m => String(m.id) === String(clipEw.media_object_id)) ?? null
          : null;
        const renderedMo = activeMedia.find(m => String(m.clip_id) === cloudClipId && m.kind === "clip_video") ?? null;
        const thumbMo = activeMedia.find(m => String(m.clip_id) === cloudClipId && m.kind === "thumbnail") ?? null;

        let videoUrl: string | undefined = undefined;
        let editWindowUrl: string | undefined = undefined;
        let thumbUrl: string | undefined = undefined;

        if (renderedMo?.final_key) {
          try { videoUrl = await presign(env, "GET", String(renderedMo.final_key)); } catch {}
        }
        if (ewMo?.final_key) {
          try { editWindowUrl = await presign(env, "GET", String(ewMo.final_key)); } catch {}
        }
        if (!videoUrl && editWindowUrl) {
          videoUrl = editWindowUrl;
        }
        if (thumbMo?.final_key) {
          try { thumbUrl = await presign(env, "GET", String(thumbMo.final_key)); } catch {}
        }

        const { captions, source } = buildCloudCaptions(c, clipEw);
        const hasVideo = Boolean(videoUrl || editWindowUrl);

        return {
          id: localId,
          cloud_id: c.id,
          title: c.title,
          status: targetStatus,
          start_time: Number(c.start_time || 0),
          end_time: Number(c.end_time || 0),
          ai_suggested_start_time: Number(c.ai_suggested_start_time || c.start_time || 0),
          ai_suggested_end_time: Number(c.ai_suggested_end_time || c.end_time || 0),
          duration: Math.max(0, Number(c.end_time || 0) - Number(c.start_time || 0)),
          range_revision: Number(c.range_revision || 0),
          is_render_stale: Boolean(c.render_stale),
          created_at: c.cloud_updated_at,
          video_url: videoUrl,
          source_video_url: undefined,
          thumbnail_url: thumbUrl,
          edit_window_url: editWindowUrl,
          edit_window_start: clipEw ? Number(clipEw.window_start) : undefined,
          edit_window_end: clipEw ? Number(clipEw.window_end) : undefined,
          captions,
          captions_source: source,
          captions_revision: Number(c.caption_revision || 0),
          // Explicit unavailable state instead of a silent missing player.
          media_missing: !mediaDegraded && !hasVideo ? true : undefined,
          is_cloud: true,
        };
      };

      // Durable tombstones live on clips_cloud itself (status='rejected' +
      // reject_purge_after stamped at request time), so a hidden clip cannot
      // reappear after the PC acknowledges the reject command. The live-command
      // lookup remains as belt-and-braces for rows that predate this contract.
      const tombstoned = new Set<string>();
      try {
        const cmdRes = await rpcRaw(env, "cloud_list_live_clip_commands", { p_user_id: user.userId });
        if (Array.isArray(cmdRes)) {
          for (const cmd of cmdRes as Array<{ local_clip_id: string; kind: string }>) {
            if (cmd?.kind === "reject" && cmd?.local_clip_id) tombstoned.add(String(cmd.local_clip_id));
          }
        }
      } catch {
        // Filtering is best-effort; clips still render if lookup fails.
      }

      const isHiddenByReject = (c: Record<string, unknown>) =>
        tombstoned.has(String(c.local_clip_id)) || c.status === "rejected";

      const readyRaw = rawClips
        .filter(c => c.status === "ready_review" || c.status === "ready")
        .filter(c => !isHiddenByReject(c));
      const approvedRaw = rawClips
        .filter(c => c.status === "approved" || c.status === "published")
        .filter(c => !isHiddenByReject(c));

      const ready = await Promise.all(readyRaw.map(c => mapClipItem(c, "ready")));
      const recent_approved = await Promise.all(approvedRaw.map(c => mapClipItem(c, String(c.status))));

      const queuedJobs = jobsRes?.ok ? (await jobsRes.json() as unknown[]) : [];
      return json(200, {
        ready,
        recent_approved,
        queue_stats: { queued_count: Array.isArray(queuedJobs) ? queuedJobs.length : 0, daily_quota_used: 0, daily_quota_max: 10 },
        cloud_mode: true,
        media_degraded: mediaDegraded ? true : undefined,
        windows_degraded: windowsDegraded ? true : undefined,
        jobs_degraded: !jobsRes || !jobsRes.ok ? true : undefined,
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

      // Cloud caption editing: persist an edited offline caption track so it can
      // be reloaded cloud-side without the PC, and so laptops sync it later.
      // Conflict-safe: the save carries the revision it was prepared against;
      // stale saves are refused (409) instead of clobbering a newer edit.
      if (request.method === "PUT" && url.pathname.startsWith("/clips/") && url.pathname.endsWith("/captions")) {
        const localClipId = decodeURIComponent(url.pathname.slice("/clips/".length, -"/captions".length));
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        const body = await readJson<{ track?: unknown; captions?: unknown; base_revision?: number }>(request);
        const track = body?.track ?? body?.captions ?? null;
        if (!track || typeof track !== "object" || Array.isArray(track)) return json(400, { error: "caption track required" });
        try {
          const result = await rpc<{ revision: number; conflict: boolean }>(env, "cloud_put_clip_captions", {
            p_user_id: userId,
            p_local_clip_id: localClipId,
            p_caption: track,
            p_base_revision: typeof body?.base_revision === "number" ? Math.floor(body.base_revision) : null,
          });
          if (result?.conflict) {
            return json(409, { error: "caption_conflict", revision: result.revision });
          }
          return json(200, { ok: true, revision: result?.revision ?? 0 });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          return json(502, { error: `failed to save captions: ${msg}` });
        }
      }

      // Offline Reject: tombstones the cloud row durably (status='rejected',
      // purge_after = request time + 24h) and queues a command for the PC.
      // Undo/restore voids a PENDING reject instantly, or queues a durable
      // restore for a reject the PC has already claimed.
      if (request.method === "POST"
        && (url.pathname.startsWith("/clips/") && url.pathname.slice("/clips/".length).split("/")[1] === "reject"
          || url.pathname.startsWith("/clips/") && url.pathname.slice("/clips/".length).split("/")[1] === "restore")) {
        const localClipId = decodeURIComponent(url.pathname.slice("/clips/".length).split("/")[0]);
        const kind = url.pathname.slice("/clips/".length).split("/")[1] as "reject" | "restore";
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        const body = await readJson<{ reason?: string }>(request);
        try {
          const queued = await rpc<{ command_id: string; state: string; purge_after: string | null }>(env, "cloud_enqueue_clip_command", {
            p_user_id: userId,
            p_local_clip_id: localClipId,
            p_kind: kind,
            p_payload: kind === "reject" ? { reason: typeof body?.reason === "string" ? body.reason.slice(0, 500) : "Rejected from mobile review" } : {},
          });
          return json(200, {
            ok: true,
            command_id: queued.command_id,
            state: queued.state,
            purge_after: queued.purge_after,
          });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          return json(502, { error: `failed to queue command: ${msg}` });
        }
      }

      // PC command drain: claim a lease-scoped batch of pending commands.
      // Audit 2026-10-27: these are PC-infrastructure routes — an ordinary
      // Supabase user session must not be able to drive the PC command
      // lifecycle (claim/ack/purge) even for their own clips.
      if (request.method === "POST" && url.pathname === "/sync/commands/claim") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const body = await readJson<{ limit?: number; lease_seconds?: number }>(request);
        try {
          const rows = await rpcRaw(env, "cloud_claim_clip_commands",
            { p_user_id: userId, p_limit: Number(body?.limit ?? 10), p_lease_seconds: Number(body?.lease_seconds ?? 600) });
          return json(200, { commands: Array.isArray(rows) ? rows : [] });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          return json(502, { error: `claim failed: ${msg}` });
        }
      }

      // PC command drain: acknowledge a claimed command (done/voided/failed/unknown).
      if (request.method === "POST" && url.pathname === "/sync/commands/ack") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const body = await readJson<{ command_id: string; result: string; error?: string }>(request);
        if (!body?.command_id || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(body.command_id)) {
          return json(400, { error: "command_id required" });
        }
        if (!body.result || !["done", "voided", "failed", "unknown"].includes(body.result)) {
          return json(400, { error: "invalid result" });
        }
        try {
          const ok = await rpc<boolean>(env, "cloud_ack_clip_command", {
            p_user_id: userId, p_command_id: body.command_id, p_result: body.result, p_error: body.error ?? null,
          });
          return json(200, { ok: ok === true });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          return json(502, { error: `ack failed: ${msg}` });
        }
      }

      // PC post-grace purge of cloud-rendered assets for a tombstoned clip.
      // Owner-validated and idempotent: only media_objects whose clip_id points
      // at THIS user's cloud clip are touched (rendered video + thumbnail).
      // Edit-window proxies and other sessions' assets are deliberately NOT
      // deleted — a reject never removes the source recording.
      const purgeMatch = url.pathname.match(/^\/sync\/clips\/([^/]+)\/purge-media$/);
      if (request.method === "POST" && purgeMatch) {
        // Audit 2026-10-27: PC-infrastructure route. A plain user JWT must not
        // be able to pull media deletion forward past the grace deadline; only
        // the owner PC (worker token) may drive post-grace purges.
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const localClipId = decodeURIComponent(purgeMatch[1]);
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        let cloudClipId: string | null = null;
        try {
          const res = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&local_clip_id=eq.${encodeURIComponent(localClipId)}&select=id,reject_purge_after`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          const rows = res.ok ? (await res.json() as Array<{ id: string; reject_purge_after: string | null }>) : [];
          if (rows.length === 0) return json(404, { error: "clip not found" });
          cloudClipId = rows[0]?.id ?? null;
          if (!rows[0]?.reject_purge_after) return json(409, { error: "clip is not tombstoned for purge" });
          // Audit 2026-10-27: the deadline is enforced SERVER-SIDE. Previously
          // the mere existence of a stamp was enough, so a caller could delete
          // media well before the request-time 24h grace elapsed. The PC's
          // clock may drift a little, so a 5-minute skew tolerance applies.
          const purgeMs = Date.parse(rows[0].reject_purge_after);
          if (Number.isFinite(purgeMs) && purgeMs - PURGE_CLOCK_SKEW_MS > Date.now()) {
            return json(409, { error: "purge window has not elapsed", purge_after: rows[0].reject_purge_after });
          }
        } catch (e) {
          return json(502, { error: `clip lookup failed: ${e instanceof Error ? e.message : e}` });
        }
        if (cloudClipId) {
          let deleted = 0;
          let remaining = 0;
          try {
            const res = await fetch(
              `${env.SUPABASE_URL}/rest/v1/media_objects?user_id=eq.${userId}&clip_id=eq.${cloudClipId}&status=in.(active,deletion_pending)&select=id,kind,status,staging_key,final_key`,
              { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
            );
            const objs = res.ok ? (await res.json() as Array<{ id: string; kind: string; status: string; staging_key: string | null; final_key: string | null }>) : [];
            if (!Array.isArray(objs)) return json(502, { error: "media lookup failed" });
            for (const obj of objs) {
              let r2Failed = false;
              for (const key of [obj.final_key, obj.staging_key]) {
                if (!key) continue;
                try {
                  await env.BUCKET.delete(key);
                } catch {
                  r2Failed = true; // retried on the next PC sweep; token retry-safe
                }
              }
              if (r2Failed) {
                if (obj.status !== "deletion_pending") {
                  await rpc(env, "cloud_mark_deletion", { p_media_id: obj.id }).catch(() => {});
                }
                remaining++;
                continue; // confirm deletion only once R2 accepts
              }
              // Deletion pipeline: active → deletion_pending → deleted.
              // (cloud_confirm_deletion refuses to confirm an 'active' object.)
              try {
                await rpc(env, "cloud_mark_deletion", { p_media_id: obj.id });
              } catch {
                // Object may already be deletion_pending from an earlier sweep.
              }
              try {
                await rpc(env, "cloud_confirm_deletion", { p_media_id: obj.id });
                deleted++;
              } catch {
                remaining++; // retried by the next PC sweep
              }
            }
          } catch (e) {
            return json(502, { error: `purge failed: ${e instanceof Error ? e.message : e}` });
          }
          return json(200, { purged: deleted, remaining, cloud_clip_id: cloudClipId });
        }
      }

      // ---- PC sync endpoints (worker-token ONLY; audit 2026-10-27) ----
      // The PC daemon no longer holds the Supabase service-role key: every
      // privileged operation runs here on trusted infrastructure, scoped to the
      // account named by the PC token's X-Dispatch-User-Id header.

      // Clip upsert (metadata) with a server-side tombstone guard: an ACTIVE
      // local push can never resurrect a cloud reject tombstone.
      if (request.method === "POST" && url.pathname === "/sync/pc/clips") {
        if (!isPcWorkerAuth(env, request)) return json(403, { error: "PC worker auth required" });
        const body = await readJson<{
          local_clip_id: string; title: string; status: string;
          start_time: number; end_time: number;
          ai_suggested_start_time?: number; ai_suggested_end_time?: number; range_revision?: number;
        }>(request);
        const localClipId = String(body?.local_clip_id ?? "");
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        try {
          const existing = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&local_clip_id=eq.${encodeURIComponent(localClipId)}&select=id,status,reject_purge_after`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          const rows = existing.ok ? (await existing.json() as Array<{ id: string; status: string; reject_purge_after: string | null }>) : [];
          if (rows[0]?.status === "rejected" && rows[0]?.reject_purge_after && body && body.status !== "rejected") {
            return json(200, { cloud_clip_id: rows[0].id, skipped_tombstoned: true });
          }
          const upsertRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?on_conflict=user_id,local_clip_id`,
            {
              method: "POST",
              headers: {
                apikey: env.SUPABASE_SERVICE_ROLE_KEY,
                Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
                "Content-Type": "application/json",
                Prefer: "resolution=merge-duplicates,return=representation",
              },
              body: JSON.stringify({
                user_id: userId,
                local_clip_id: localClipId,
                title: String(body?.title ?? "Clip Highlight").slice(0, 300),
                status: ["ready_review", "approved", "published", "rejected"].includes(String(body?.status)) ? body!.status : "ready_review",
                ai_suggested_start_time: Number(body?.ai_suggested_start_time ?? body?.start_time ?? 0),
                ai_suggested_end_time: Number(body?.ai_suggested_end_time ?? body?.end_time ?? 0),
                start_time: Number(body?.start_time ?? 0),
                end_time: Number(body?.end_time ?? 0),
                range_revision: Number(body?.range_revision ?? 0),
              }),
            }
          );
          if (!upsertRes.ok) {
            const text = await upsertRes.text().catch(() => "");
            return json(502, { error: `clips_cloud upsert failed: ${upsertRes.status} ${text.slice(0, 200)}` });
          }
          const upsertRows = await upsertRes.json() as Array<{ id: string }>;
          return json(200, { cloud_clip_id: upsertRows[0]?.id ?? null });
        } catch (e) {
          return json(502, { error: `clip upsert failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      // Cloud state for one local clip: tombstone, staging bookkeeping, cloud id.
      const pcClipStateMatch = url.pathname.match(/^\/sync\/pc\/clips\/([^/]+)$/);
      if (request.method === "GET" && pcClipStateMatch) {
        if (!isPcWorkerAuth(env, request)) return json(403, { error: "PC worker auth required" });
        const localClipId = decodeURIComponent(pcClipStateMatch[1]);
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        try {
          const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
          const [clipRes, windowRes] = await Promise.all([
            fetch(
              `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&local_clip_id=eq.${encodeURIComponent(localClipId)}&select=id,status,reject_requested_at,reject_purge_after`,
              { headers: svc }
            ),
            fetch(
              `${env.SUPABASE_URL}/rest/v1/edit_windows?user_id=eq.${userId}&local_clip_id=eq.${encodeURIComponent(localClipId)}&status=eq.ready&select=id&limit=1`,
              { headers: svc }
            ),
          ]);
          const clipRows = clipRes.ok ? (await clipRes.json() as Array<Record<string, unknown>>) : [];
          const clipRow = Array.isArray(clipRows) ? clipRows[0] : null;
          const windowRows = windowRes.ok ? (await windowRes.json() as unknown[]) : [];
          return json(200, {
            local_clip_id: localClipId,
            cloud_clip_id: (clipRow && typeof clipRow.id === "string") ? clipRow.id : null,
            tombstoned: Boolean(clipRow && clipRow.status === "rejected" && clipRow.reject_purge_after),
            reject_requested_at: clipRow ? clipRow.reject_requested_at : null,
            reject_purge_after: clipRow ? clipRow.reject_purge_after : null,
            already_staged: Array.isArray(windowRows) && windowRows.length > 0,
          });
        } catch (e) {
          return json(502, { error: `clip state lookup failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      // Edit-window registration: records the ready 720p proxy row, links its
      // media object to the cloud clip, and stores the window transcript.
      if (request.method === "POST" && url.pathname === "/sync/pc/windows") {
        if (!isPcWorkerAuth(env, request)) return json(403, { error: "PC worker auth required" });
        const body = await readJson<{
          local_clip_id: string; media_object_id: string;
          window_start: number; window_end: number; words?: Array<Record<string, unknown>>;
        }>(request);
        const localClipId = String(body?.local_clip_id ?? "");
        const mediaObjectId = String(body?.media_object_id ?? "");
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        if (!mediaObjectId || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(mediaObjectId)) {
          return json(400, { error: "media_object_id required" });
        }
        const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
        const patchHeaders = { ...svc, "Content-Type": "application/json" };
        try {
          const clipRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&local_clip_id=eq.${encodeURIComponent(localClipId)}&select=id`,
            { headers: svc }
          );
          const clipRows = clipRes.ok ? (await clipRes.json() as Array<{ id: string }>) : [];
          const cloudClipId = Array.isArray(clipRows) ? clipRows[0]?.id : undefined;
          if (!cloudClipId) return json(404, { error: "clip not found for this account" });

          // Ownership + lifecycle check on the referenced media object.
          const mediaRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/media_objects?id=eq.${mediaObjectId}&select=user_id,status`,
            { headers: svc }
          );
          const mediaRows = mediaRes.ok ? (await mediaRes.json() as Array<{ user_id: string; status: string }>) : [];
          if (!mediaRows[0] || mediaRows[0].user_id !== userId) return json(403, { error: "media object not owned by caller" });
          if (mediaRows[0].status !== "active" && mediaRows[0].status !== "deletion_pending") {
            return json(409, { error: `media not usable (${mediaRows[0].status})` });
          }

          const insertWindow = async () => {
            const res = await fetch(
              `${env.SUPABASE_URL}/rest/v1/edit_windows`,
              {
                method: "POST",
                headers: { ...svc, "Content-Type": "application/json", Prefer: "return=representation" },
                body: JSON.stringify({
                  user_id: userId,
                  local_clip_id: localClipId,
                  window_start: Number(body!.window_start ?? 0),
                  window_end: Number(body!.window_end ?? 0),
                  status: "ready",
                  media_object_id: mediaObjectId,
                  staged_at: new Date().toISOString(),
                }),
              }
            );
            if (!res.ok) {
              const text = await res.text().catch(() => "");
              throw new Error(`edit_windows insert failed: ${res.status} ${text.slice(0, 200)}`);
            }
            const rows = await res.json() as Array<{ id: string }>;
            return rows[0]?.id ?? null;
          };

          // Idempotent re-registration: same-bounds ready window is reused.
          let windowId: string | null = null;
          let reused = false;
          const dupRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/edit_windows?user_id=eq.${userId}&local_clip_id=eq.${encodeURIComponent(localClipId)}&status=eq.ready&window_start=eq.${Number(body?.window_start ?? 0)}&window_end=eq.${Number(body?.window_end ?? 0)}&select=id&limit=1`,
            { headers: svc }
          );
          const dupRows = dupRes.ok ? (await dupRes.json() as Array<{ id: string }>) : [];
          if (Array.isArray(dupRows) && dupRows.length > 0) {
            windowId = dupRows[0].id;
            reused = true;
          } else {
            windowId = await insertWindow();
          }

          // Link the media object to the cloud clip (idempotent) and store the
          // staged transcript words when provided.
          let transcriptsStored = false;
          if (windowId) {
            const linkRes = await fetch(
              `${env.SUPABASE_URL}/rest/v1/media_objects?id=eq.${mediaObjectId}`,
              { method: "PATCH", headers: patchHeaders, body: JSON.stringify({ clip_id: cloudClipId }) }
            );
            if (!linkRes.ok) return json(502, { error: `media link failed: ${linkRes.status}` });
            const words = body?.words;
            if (windowId && Array.isArray(words) && words.length > 0) {
              const trRes = await fetch(
                `${env.SUPABASE_URL}/rest/v1/window_transcripts`,
                {
                  method: "POST",
                  headers: { ...svc, "Content-Type": "application/json", Prefer: "resolution=merge-duplicates" },
                  body: JSON.stringify({
                    window_id: windowId,
                    user_id: userId,
                    words,
                    transcribed_at: new Date().toISOString(),
                  }),
                }
              );
              if (!trRes.ok) {
                // Transcripts seed offline caption editing; failure is not
                // fatal but is surfaced so the PC can retry later.
                return json(200, {
                  window_id: windowId, cloud_clip_id: cloudClipId,
                  window_reused: reused, media_linked: true, transcripts_stored: false,
                });
              }
              transcriptsStored = true;
            }
          }
          return json(200, {
            window_id: windowId,
            cloud_clip_id: cloudClipId,
            window_reused: reused,
            media_linked: true,
            transcripts_stored: transcriptsStored,
          });
        } catch (e) {
          return json(502, { error: `window registration failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      // Generic media-object-to-clip link (thumbnails): ownership-checked.
      if (request.method === "POST" && url.pathname === "/sync/pc/media-link") {
        if (!isPcWorkerAuth(env, request)) return json(403, { error: "PC worker auth required" });
        const body = await readJson<{ media_object_id: string; local_clip_id: string }>(request);
        const mediaObjectId = String(body?.media_object_id ?? "");
        const localClipId = String(body?.local_clip_id ?? "");
        if (!mediaObjectId || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(mediaObjectId)) {
          return json(400, { error: "media_object_id required" });
        }
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        try {
          const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
          const [mediaRes, clipRes] = await Promise.all([
            fetch(`${env.SUPABASE_URL}/rest/v1/media_objects?id=eq.${mediaObjectId}&select=user_id`, { headers: svc }),
            fetch(
              `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&local_clip_id=eq.${encodeURIComponent(localClipId)}&select=id`,
              { headers: svc }
            ),
          ]);
          const mediaRows = mediaRes.ok ? (await mediaRes.json() as Array<{ user_id: string }>) : [];
          if (!mediaRows[0] || mediaRows[0].user_id !== userId) return json(403, { error: "media object not owned by caller" });
          const clipRows = clipRes.ok ? (await clipRes.json() as Array<{ id: string }>) : [];
          const cloudClipId = Array.isArray(clipRows) ? clipRows[0]?.id : undefined;
          if (!cloudClipId) return json(404, { error: "clip not found for this account" });
          const linkRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/media_objects?id=eq.${mediaObjectId}`,
            {
              method: "PATCH",
              headers: { ...svc, "Content-Type": "application/json" },
              body: JSON.stringify({ clip_id: cloudClipId }),
            }
          );
          if (!linkRes.ok) return json(502, { error: `media link failed: ${linkRes.status}` });
          return json(200, { linked: true, cloud_clip_id: cloudClipId });
        } catch (e) {
          return json(502, { error: `media link failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      // Caption reconciliation download: revision>0 tracks saved by the phone.
      if (request.method === "GET" && url.pathname === "/sync/pc/captions") {
        if (!isPcWorkerAuth(env, request)) return json(403, { error: "PC worker auth required" });
        try {
          const res = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&caption_revision=gt.0&select=local_clip_id,caption_data,caption_revision,range_revision&order=caption_updated_at.desc&limit=200`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          const rows = res.ok ? (await res.json() as unknown[]) : [];
          return json(200, { clips: Array.isArray(rows) ? rows : [] });
        } catch (e) {
          return json(502, { error: `caption fetch failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      // Legacy single-profile bootstrap hint: ownership for unowned local clips
      // is adoptable ONLY when the project has exactly one profile. On
      // multi-profile projects there is no safe guess ("most recent profile"
      // must never decide ownership) and the PC must be configured explicitly.
      if (request.method === "GET" && url.pathname === "/sync/pc/eligible-owner") {
        if (!isPcWorkerAuth(env, request)) return json(403, { error: "PC worker auth required" });
        try {
          const res = await fetch(
            `${env.SUPABASE_URL}/rest/v1/profiles?select=user_id&order=created_at.desc&limit=2`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          const rows = res.ok ? (await res.json() as Array<{ user_id: string }>) : [];
          if (!Array.isArray(rows) || rows.length === 0) {
            return json(200, { user_id: null, reason: "no_profiles" });
          }
          if (rows.length > 1) {
            return json(200, { user_id: null, reason: "multiple_profiles" });
          }
          const uid = String(rows[0].user_id ?? "");
          return json(200, { user_id: uid || null, reason: uid ? "single_profile" : "no_profiles" });
        } catch (e) {
          return json(502, { error: `owner lookup failed: ${e instanceof Error ? e.message : e}` });
        }
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
