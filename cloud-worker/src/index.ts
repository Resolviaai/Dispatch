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
  buildVaultAad,
  encryptTokenV2,
  decryptTokenWithMigration,
  encryptToken,
  decryptToken,
  buildAuthorizeUrl,
  buildInstagramLoginAuthorizeUrl,
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
  PC_OWNER_USER_IDS?: string; // D13: comma-separated UUID allowlist binding the worker-token bearer to PC owner identity(ies); unset = legacy permissive (logged)
  SITE_ORIGIN: string;  // e.g. https://resolviaai.github.io — CORS-pinned admin origin
  INSTAGRAM_CLIENT_ID?: string;
  INSTAGRAM_CLIENT_SECRET?: string;
  INSTAGRAM_LOGIN_CLIENT_ID?: string;
  INSTAGRAM_LOGIN_CLIENT_SECRET?: string;
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
const KINDS = { clip_video: "video/mp4", thumbnail: "image/jpeg", edit_window: "video/mp4", render: "video/mp4" } as const;
type Kind = keyof typeof KINDS;
const CAP_BYTES: Record<Kind, number> = { clip_video: 536870912, thumbnail: 8388608, edit_window: 536870912, render: 536870912 };

// ---------- pure helpers (also unit-tested) ----------
export function roundMs(seconds: number): number {
  return Math.floor(Number(seconds) * 1000.0 + 0.5) / 1000.0;
}

export function canonicalMediaSpans(ranges: unknown): [number, number][] {
  if (!Array.isArray(ranges)) throw new Error("ranges must be an array of [start, end] pairs or segment objects");
  const normalized: [number, number][] = ranges.map((item, idx) => {
    let s: number;
    let e: number;
    if (item && typeof item === "object" && !Array.isArray(item)) {
      const obj = item as Record<string, unknown>;
      s = roundMs(Number(obj.source_start ?? obj.start));
      e = roundMs(Number(obj.source_end ?? obj.end));
    } else if (Array.isArray(item) && item.length === 2) {
      s = roundMs(Number(item[0]));
      e = roundMs(Number(item[1]));
    } else {
      throw new Error(`range at index ${idx} must be a [start, end] pair or segment object`);
    }
    if (s < 0 || e <= s || !Number.isFinite(s) || !Number.isFinite(e)) {
      throw new Error(`invalid segment bounds: [${s}, ${e}]`);
    }
    return [s, e];
  });

  if (normalized.length === 0) return [];
  const sorted = [...normalized].sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [sorted[0]];

  for (let i = 1; i < sorted.length; i++) {
    const [s, e] = sorted[i];
    const prev = merged[merged.length - 1];
    if (s < prev[1] - 1e-9) {
      throw new Error(`overlapping segments detected: [${prev[0]}, ${prev[1]}] and [${s}, ${e}]`);
    } else if (Math.abs(s - prev[1]) <= 1e-6) {
      prev[1] = e;
    } else {
      merged.push([s, e]);
    }
  }
  return merged.map(([s, e]) => [roundMs(s), roundMs(e)]);
}

export function canonicalSpansStr(ranges: unknown): string {
  const spans = canonicalMediaSpans(ranges);
  return "[" + spans.map(([s, e]) => `${s.toFixed(3)}-${e.toFixed(3)}`).join(",") + "]";
}

export function sha256Hex(ascii: string): string {
  function rightRotate(value: number, amount: number): number {
    return (value >>> amount) | (value << (32 - amount));
  }
  const mathPow = Math.pow;
  const maxWord = mathPow(2, 32);
  let i = 0, j = 0, result = "";
  const words: number[] = [];
  const asciiBitLength = ascii.length * 8;
  let hash: number[] = [];
  const k: number[] = [];
  let primeCounter = 0;
  const isComposite: Record<number, number> = {};

  for (let candidate = 2; primeCounter < 64; candidate++) {
    if (!isComposite[candidate]) {
      for (i = 0; i < 313; i += candidate) isComposite[i] = candidate;
      hash[primeCounter] = (mathPow(candidate, 0.5) * maxWord) | 0;
      k[primeCounter++] = (mathPow(candidate, 1 / 3) * maxWord) | 0;
    }
  }

  ascii += "\x80";
  while ((ascii.length % 64) - 56) ascii += "\x00";
  for (i = 0; i < ascii.length; i++) {
    j = ascii.charCodeAt(i);
    words[i >> 2] |= j << (((3 - i) % 4) * 8);
  }
  words[words.length] = (asciiBitLength / maxWord) | 0;
  words[words.length] = asciiBitLength;

  for (j = 0; j < words.length; ) {
    const w = words.slice(j, (j += 16));
    const oldHash = hash;
    hash = hash.slice(0, 8);
    for (i = 0; i < 64; i++) {
      const w15 = w[i - 15], w2 = w[i - 2];
      const s0 = rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3);
      const s1 = rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10);
      const ch = (hash[4] & hash[5]) ^ (~hash[4] & hash[6]);
      const maj = (hash[0] & hash[1]) ^ (hash[0] & hash[2]) ^ (hash[1] & hash[2]);
      const temp1 = hash[7] + (rightRotate(hash[4], 6) ^ rightRotate(hash[4], 11) ^ rightRotate(hash[4], 25)) + ch + k[i] + (w[i] = i < 16 ? w[i] : (w[i - 16] + s0 + w[i - 7] + s1) | 0);
      const temp2 = (rightRotate(hash[0], 2) ^ rightRotate(hash[0], 13) ^ rightRotate(hash[0], 22)) + maj;
      hash = [(temp1 + temp2) | 0, hash[0], hash[1], hash[2], (hash[3] + temp1) | 0, hash[4], hash[5], hash[6]];
    }
    for (i = 0; i < 8; i++) hash[i] = (hash[i] + oldHash[i]) | 0;
  }
  for (i = 0; i < 8; i++) {
    for (j = 3; j + 1; j--) {
      const b = (hash[i] >> (j * 8)) & 255;
      result += (b < 16 ? "0" : "") + b.toString(16);
    }
  }
  return result;
}

export function computeMediaFingerprint(sourceAssetId: string, ranges: unknown): string {
  if (typeof sourceAssetId !== "string" || !sourceAssetId.trim()) {
    throw new Error("sourceAssetId must be a non-empty string");
  }
  const spansStr = canonicalSpansStr(ranges);
  return sha256Hex(`v1:media:${sourceAssetId.trim()}:${spansStr}`);
}

export function computeRenderFingerprint(
  mediaFingerprint: string,
  layoutMode: string,
  cropAnchorX: number,
  layers?: unknown[],
  settings?: Record<string, unknown>
): string {
  if (typeof mediaFingerprint !== "string" || mediaFingerprint.length !== 64) {
    throw new Error("mediaFingerprint must be a valid 64-character SHA-256 hex string");
  }
  const layout = layoutMode ? String(layoutMode).trim() : "portrait_9_16";
  const cropStr = roundMs(cropAnchorX).toFixed(3);
  function canonicalStringify(obj: unknown): string {
    if (obj === null || typeof obj !== "object") return JSON.stringify(obj);
    if (Array.isArray(obj)) return "[" + obj.map(canonicalStringify).join(",") + "]";
    const keys = Object.keys(obj as Record<string, unknown>).sort();
    return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalStringify((obj as Record<string, unknown>)[k])).join(",") + "}";
  }
  const layersStr = canonicalStringify(Array.isArray(layers) ? layers : []);
  const layersHash = sha256Hex(layersStr);
  const settingsClean = settings && typeof settings === "object" ? settings : {};
  let payload = `v1:render:${mediaFingerprint}:${layout}:${cropStr}:${layersHash}`;
  if (Object.keys(settingsClean).length > 0) {
    payload += `:${sha256Hex(canonicalStringify(settingsClean))}`;
  }
  return sha256Hex(payload);
}

export function validateTimelineSegments(segments: unknown): {
  valid: boolean;
  error?: string;
  keptDuration: number;
  canonicalSegments?: Array<{ id: string; source_start: number; source_end: number }>;
} {
  if (!Array.isArray(segments) || segments.length === 0) {
    return { valid: false, error: "segments must be a non-empty array", keptDuration: 0 };
  }
  let total = 0;
  let prevEnd = -1;
  const canonicalSegments: Array<{ id: string; source_start: number; source_end: number }> = [];

  for (let i = 0; i < segments.length; i++) {
    const item = segments[i];
    let s: number;
    let e: number;
    let id: string = `seg-${i + 1}`;

    if (item && typeof item === "object" && !Array.isArray(item)) {
      const obj = item as Record<string, unknown>;
      const startVal = obj.source_start ?? obj.start;
      const endVal = obj.source_end ?? obj.end;
      if (typeof obj.id === "string" && obj.id.trim()) {
        id = obj.id.trim();
      }
      if (typeof startVal !== "number" || typeof endVal !== "number") {
        return { valid: false, error: `segment at index ${i} missing numeric source_start/source_end`, keptDuration: 0 };
      }
      s = roundMs(startVal);
      e = roundMs(endVal);
    } else if (Array.isArray(item) && item.length === 2) {
      s = roundMs(Number(item[0]));
      e = roundMs(Number(item[1]));
    } else {
      return { valid: false, error: `segment at index ${i} must be [start, end] or { source_start, source_end }`, keptDuration: 0 };
    }

    if (s < 0 || e <= s || !Number.isFinite(s) || !Number.isFinite(e)) {
      return { valid: false, error: `invalid segment bounds [${s}, ${e}] at index ${i}`, keptDuration: 0 };
    }
    if (s < prevEnd - 1e-9) {
      return { valid: false, error: `overlapping segment at index ${i}`, keptDuration: 0 };
    }
    prevEnd = e;
    total += (e - s);
    canonicalSegments.push({ id, source_start: s, source_end: e });
  }

  total = roundMs(total);
  if (total < 5.0) {
    return { valid: false, error: `total kept duration (${total}s) must be at least 5.0s`, keptDuration: total };
  }
  if (total > 180.0) {
    return { valid: false, error: `total kept duration (${total}s) exceeds 180.0s maximum`, keptDuration: total };
  }
  return { valid: true, keptDuration: total, canonicalSegments };
}

export function validateReserveInput(kind: string, mime: string, expectedBytes: number): string | null {
  const normalizedKind = kind === "render" ? "clip_video" : kind;
  if (!(normalizedKind in KINDS)) return "unsupported kind";
  if (KINDS[normalizedKind as Kind] !== mime) return "unsupported mime for kind";
  if (!Number.isInteger(expectedBytes) || expectedBytes <= 0) return "expected_bytes must be a positive integer";
  if (expectedBytes > CAP_BYTES[normalizedKind as Kind]) return "expected_bytes exceeds object cap";
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

export class ProviderAuthError extends Error {
  readonly code: "not_connected" | "needs_reauth" | "ambiguous_owner" | "legacy_reauth_required" | "refresh_failed" | "vault_error";
  readonly status: number;
  readonly provider: string;
  readonly accountId?: string;

  constructor(
    code: ProviderAuthError["code"],
    status: number,
    provider: string,
    message: string,
    accountId?: string,
  ) {
    super(message);
    this.name = "ProviderAuthError";
    this.code = code;
    this.status = status;
    this.provider = provider;
    this.accountId = accountId;
  }
}

export interface ValidProviderTokenResult {
  provider: "youtube" | "instagram" | "facebook" | "linkedin" | "twitter";
  accountId: string;
  rowId: string;
  accountName: string | null;
  accountEmail: string | null;
  accessToken: string;
  expiresAtMs: number | null;
  expiresAtIso: string | null;
  payload: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

interface ConnectedAccountRow {
  id: string;
  user_id?: string;
  provider?: string;
  account_id?: string;
  account_name?: string | null;
  account_email?: string | null;
  credentials_encrypted?: string;
  metadata?: Record<string, unknown> | null;
  is_verified?: boolean | null;
}

const TOKEN_REFRESH_MARGIN_MS = 5 * 60_000;
const SHORT_LIVED_CACHE_CAP_MS = 10 * 60_000;
const providerTokenCache = new Map<string, {
  accessToken: string;
  expiresAtMs: number;
  payload: Record<string, unknown>;
  accountId: string;
  rowId: string;
  accountName: string | null;
  accountEmail: string | null;
  metadata: Record<string, unknown>;
}>();
const inflightProviderRefreshes = new Map<string, Promise<ValidProviderTokenResult | null>>();

export function clearProviderTokenCacheForTests(): void {
  providerTokenCache.clear();
  inflightProviderRefreshes.clear();
}

const pcBrokerRateBuckets = new Map<string, { count: number; resetAt: number }>();

export function checkPcBrokerRateLimit(
  bucketKey: string,
  maxRequests = 120,
  windowMs = 60_000,
): { allowed: boolean; retryAfterSeconds: number } {
  const now = Date.now();
  if (pcBrokerRateBuckets.size > 512) {
    for (const [k, v] of pcBrokerRateBuckets.entries()) {
      if (v.resetAt <= now) pcBrokerRateBuckets.delete(k);
    }
  }
  const existing = pcBrokerRateBuckets.get(bucketKey);
  if (!existing || existing.resetAt <= now) {
    pcBrokerRateBuckets.set(bucketKey, { count: 1, resetAt: now + windowMs });
    return { allowed: true, retryAfterSeconds: Math.ceil(windowMs / 1000) };
  }
  if (existing.count >= maxRequests) {
    return {
      allowed: false,
      retryAfterSeconds: Math.max(1, Math.ceil((existing.resetAt - now) / 1000)),
    };
  }
  existing.count += 1;
  return {
    allowed: true,
    retryAfterSeconds: Math.max(1, Math.ceil((existing.resetAt - now) / 1000)),
  };
}

export function resetPcBrokerRateLimitsForTests(): void {
  pcBrokerRateBuckets.clear();
}

function normalizeOAuthProvider(raw: string): "youtube" | "instagram" | "facebook" | "linkedin" | "twitter" {
  const p = String(raw || "").trim().toLowerCase();
  if (p === "x" || p === "twitter") return "twitter";
  if (p === "google" || p === "youtube") return "youtube";
  if (p === "instagram" || p === "facebook" || p === "linkedin") return p;
  throw new ProviderAuthError("not_connected", 400, p, `Unsupported provider: ${raw}`);
}

function parseProviderPayload(provider: string, raw: string): Record<string, unknown> {
  const trimmed = raw.trim();
  if (trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch { /* fall through to bare string */ }
  }
  return provider === "youtube" ? { refresh_token: trimmed } : { access_token: trimmed };
}

async function casUpdateConnectedAccountCredentials(
  env: Env,
  row: ConnectedAccountRow,
  expectedOldEncrypted: string,
  newEncrypted: string,
  extraFields: Record<string, unknown> = {},
): Promise<{ updated: boolean; conflict: boolean }> {
  const patchUrl = new URL(`${env.SUPABASE_URL}/rest/v1/connected_accounts`);
  patchUrl.searchParams.set("id", `eq.${row.id}`);
  patchUrl.searchParams.set("credentials_encrypted", `eq.${expectedOldEncrypted}`);
  const res = await fetch(patchUrl.toString(), {
    method: "PATCH",
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      "content-type": "application/json",
      Prefer: "return=representation",
    },
    body: JSON.stringify({ credentials_encrypted: newEncrypted, ...extraFields }),
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) return { updated: false, conflict: false };
  const text = await res.text();
  if (!text.trim()) {
    return { updated: true, conflict: false };
  }
  try {
    const parsed = JSON.parse(text) as unknown;
    if (Array.isArray(parsed)) {
      if (parsed.length === 0) return { updated: false, conflict: true };
      return { updated: true, conflict: false };
    }
  } catch { /* simple mock response */ }
  return { updated: true, conflict: false };
}

async function markConnectedAccountNeedsReauth(
  env: Env,
  row: ConnectedAccountRow,
  reason: string,
): Promise<void> {
  const nextMeta: Record<string, unknown> = {
    ...(row.metadata || {}),
    needs_reauth: true,
    reauth_reason: reason.slice(0, 200),
    reauth_marked_at: new Date().toISOString(),
  };
  await fetch(`${env.SUPABASE_URL}/rest/v1/connected_accounts?id=eq.${encodeURIComponent(row.id)}`, {
    method: "PATCH",
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ metadata: nextMeta, is_verified: false }),
    signal: AbortSignal.timeout(8000),
  }).catch(() => null);
}

async function readConnectedAccountByRowId(
  env: Env,
  rowId: string,
): Promise<ConnectedAccountRow | null> {
  const url = new URL(`${env.SUPABASE_URL}/rest/v1/connected_accounts`);
  url.searchParams.set("id", `eq.${rowId}`);
  url.searchParams.set(
    "select",
    "id,user_id,provider,account_id,account_name,account_email,credentials_encrypted,metadata,is_verified",
  );
  url.searchParams.set("limit", "1");
  const res = await fetch(url.toString(), {
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
    },
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) return null;
  const rows = (await res.json()) as ConnectedAccountRow[];
  return rows[0] ?? null;
}

async function resolveConnectedAccountRow(
  env: Env,
  userId: string,
  normProvider: "youtube" | "instagram" | "facebook" | "linkedin" | "twitter",
  accountRefRaw: string,
): Promise<ConnectedAccountRow | null> {
  const selectCols = "id,user_id,provider,account_id,account_name,account_email,credentials_encrypted,metadata,is_verified";
  const providerFilter = normProvider === "twitter" ? "in.(twitter,x)" : `eq.${normProvider}`;
  const accountRef = accountRefRaw.trim();

  if (accountRef) {
    const url = new URL(`${env.SUPABASE_URL}/rest/v1/connected_accounts`);
    url.searchParams.set("user_id", `eq.${userId}`);
    url.searchParams.set("provider", providerFilter);
    url.searchParams.set("select", selectCols);
    if (UUID_RE.test(accountRef)) {
      url.searchParams.set("or", `(id.eq.${accountRef},account_id.eq.${accountRef})`);
    } else {
      url.searchParams.set("account_id", `eq.${accountRef}`);
    }
    const res = await fetch(url.toString(), {
      headers: {
        apikey: env.SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) throw new Error(`${normProvider} account lookup failed: ${res.status}`);
    let rows = (await res.json()) as ConnectedAccountRow[];
    if (rows.length === 0 && normProvider === "youtube" && !UUID_RE.test(accountRef)) {
      url.searchParams.delete("account_id");
      url.searchParams.set("account_email", `eq.${accountRef}`);
      const emailRes = await fetch(url.toString(), {
        headers: {
          apikey: env.SUPABASE_SERVICE_ROLE_KEY,
          Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
        },
        signal: AbortSignal.timeout(8000),
      });
      if (!emailRes.ok) throw new Error(`youtube account lookup failed: ${emailRes.status}`);
      rows = (await emailRes.json()) as ConnectedAccountRow[];
    }
    return rows.length > 0 ? rows[0] : null;
  }

  // Fallback ONLY when no explicit account_id was requested (default/unambiguous single account).
  const fallbackUrl = new URL(`${env.SUPABASE_URL}/rest/v1/connected_accounts`);
  fallbackUrl.searchParams.set("user_id", `eq.${userId}`);
  fallbackUrl.searchParams.set("provider", providerFilter);
  fallbackUrl.searchParams.set("select", selectCols);
  fallbackUrl.searchParams.set("limit", "2");
  const fallbackRes = await fetch(fallbackUrl.toString(), {
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
    },
    signal: AbortSignal.timeout(8000),
  });
  if (!fallbackRes.ok) throw new Error(`${normProvider} account lookup failed: ${fallbackRes.status}`);
  const candidates = (await fallbackRes.json()) as ConnectedAccountRow[];
  if (!Array.isArray(candidates) || candidates.length === 0) return null;
  if (candidates.length === 1) return candidates[0];
  throw new ProviderAuthError(
    "ambiguous_owner",
    409,
    normProvider,
    `Multiple ${normProvider} accounts exist; explicit account_id is required`,
  );
}

/**
 * Centralized credential decryptor, AES-GCM v2 AAD migrator, short-lived
 * cache, single-flight refresh coordinator, and atomic CAS writer across all
 * connected social providers.
 */
export async function getValidProviderToken(
  env: Env,
  params: {
    userId: string;
    provider: string;
    accountId?: string | null;
    forceRefresh?: boolean;
    requireSourceVault?: boolean;
  },
): Promise<ValidProviderTokenResult | null> {
  const normProvider = normalizeOAuthProvider(params.provider);
  const account = await resolveConnectedAccountRow(
    env,
    params.userId,
    normProvider,
    String(params.accountId || ""),
  );
  if (!account || !account.credentials_encrypted) return null;

  const meta = (account.metadata && typeof account.metadata === "object" ? account.metadata : {}) as Record<string, unknown>;
  const requireSourceVault = params.requireSourceVault !== false;
  if (requireSourceVault && normProvider === "youtube" && meta.is_source_vault === false) {
    return null;
  }
  if (meta.needs_reauth === true) {
    throw new ProviderAuthError(
      "needs_reauth",
      401,
      normProvider,
      `${normProvider} connection requires re-authentication`,
      account.account_id || account.id,
    );
  }

  const resolvedAccountId = String(account.account_id || account.id);
  const cacheKey = `${params.userId}:${normProvider}:${account.id}`;
  const now = Date.now();
  if (!params.forceRefresh) {
    const cached = providerTokenCache.get(cacheKey);
    if (cached && cached.expiresAtMs - now > TOKEN_REFRESH_MARGIN_MS) {
      return {
        provider: normProvider,
        accountId: cached.accountId,
        rowId: cached.rowId,
        accountName: cached.accountName,
        accountEmail: cached.accountEmail,
        accessToken: cached.accessToken,
        expiresAtMs: cached.expiresAtMs,
        expiresAtIso: new Date(cached.expiresAtMs).toISOString(),
        payload: cached.payload,
        metadata: cached.metadata,
      };
    }
  }

  const aad = buildVaultAad(params.userId, normProvider, resolvedAccountId);
  let decryptRes;
  try {
    decryptRes = await decryptTokenWithMigration(env.TOKEN_VAULT_KEY, account.credentials_encrypted, aad);
  } catch (err) {
    throw new ProviderAuthError(
      "vault_error",
      500,
      normProvider,
      `Vault decryption failed: ${err instanceof Error ? err.message : String(err)}`,
      resolvedAccountId,
    );
  }

  const credPayload = parseProviderPayload(normProvider, decryptRes.plaintext);
  const existingAccess =
    typeof credPayload.access_token === "string" && credPayload.access_token
      ? credPayload.access_token
      : (typeof credPayload.bearer_token === "string" && credPayload.bearer_token
        ? credPayload.bearer_token
        : (normProvider === "facebook" && typeof credPayload.user_access_token === "string"
          ? credPayload.user_access_token
          : ""));

  const parsedExpiry = typeof credPayload.token_expires_at === "string"
    ? Date.parse(credPayload.token_expires_at)
    : Number.NaN;
  const hasValidExpiry = Number.isFinite(parsedExpiry) && parsedExpiry - now > TOKEN_REFRESH_MARGIN_MS;

  // Non-expiring / still-fresh tokens: migrate legacy v1 -> v2 via CAS and return.
  const canUseWithoutRefresh =
    !params.forceRefresh &&
    Boolean(existingAccess) &&
    (
      normProvider === "facebook"
        ? (!Number.isFinite(parsedExpiry) || hasValidExpiry)
        : (normProvider === "youtube" ? hasValidExpiry : (hasValidExpiry || (!Number.isFinite(parsedExpiry) && normProvider !== "twitter")))
    );

  if (canUseWithoutRefresh) {
    if (decryptRes.needsMigration) {
      const migratedEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, JSON.stringify(credPayload), aad);
      await casUpdateConnectedAccountCredentials(env, account, account.credentials_encrypted, migratedEnc).catch(() => null);
    }
    const cacheExp = Number.isFinite(parsedExpiry)
      ? Math.min(parsedExpiry, now + SHORT_LIVED_CACHE_CAP_MS)
      : now + SHORT_LIVED_CACHE_CAP_MS;
    providerTokenCache.set(cacheKey, {
      accessToken: existingAccess,
      expiresAtMs: cacheExp,
      payload: credPayload,
      accountId: resolvedAccountId,
      rowId: account.id,
      accountName: account.account_name ?? null,
      accountEmail: account.account_email ?? null,
      metadata: meta,
    });
    return {
      provider: normProvider,
      accountId: resolvedAccountId,
      rowId: account.id,
      accountName: account.account_name ?? null,
      accountEmail: account.account_email ?? null,
      accessToken: existingAccess,
      expiresAtMs: Number.isFinite(parsedExpiry) ? parsedExpiry : cacheExp,
      expiresAtIso: new Date(Number.isFinite(parsedExpiry) ? parsedExpiry : cacheExp).toISOString(),
      payload: credPayload,
      metadata: meta,
    };
  }

  // Coalesce concurrent refreshes in this isolate.
  const existingFlight = inflightProviderRefreshes.get(cacheKey);
  if (existingFlight) return existingFlight;

  const flight = (async (): Promise<ValidProviderTokenResult | null> => {
    const oldEnc = account.credentials_encrypted!;

    const handleCasConflictRecovery = async (): Promise<ValidProviderTokenResult | null> => {
      const latest = await readConnectedAccountByRowId(env, account.id);
      if (!latest?.credentials_encrypted || latest.credentials_encrypted === oldEnc) {
        return null;
      }
      if (latest.metadata?.needs_reauth === true) {
        throw new ProviderAuthError("needs_reauth", 401, normProvider, `${normProvider} connection requires re-authentication`, resolvedAccountId);
      }
      const latestDec = await decryptTokenWithMigration(env.TOKEN_VAULT_KEY, latest.credentials_encrypted, aad);
      const latestPayload = parseProviderPayload(normProvider, latestDec.plaintext);
      const latestAccess =
        typeof latestPayload.access_token === "string" && latestPayload.access_token
          ? latestPayload.access_token
          : (typeof latestPayload.bearer_token === "string" ? latestPayload.bearer_token : "");
      if (!latestAccess) return null;
      const latestExp = typeof latestPayload.token_expires_at === "string" ? Date.parse(latestPayload.token_expires_at) : Number.NaN;
      const expMs = Number.isFinite(latestExp) ? latestExp : Date.now() + 50 * 60_000;
      providerTokenCache.set(cacheKey, {
        accessToken: latestAccess,
        expiresAtMs: Math.min(expMs, Date.now() + SHORT_LIVED_CACHE_CAP_MS),
        payload: latestPayload,
        accountId: resolvedAccountId,
        rowId: account.id,
        accountName: latest.account_name ?? account.account_name ?? null,
        accountEmail: latest.account_email ?? account.account_email ?? null,
        metadata: (latest.metadata && typeof latest.metadata === "object" ? latest.metadata : meta) as Record<string, unknown>,
      });
      return {
        provider: normProvider,
        accountId: resolvedAccountId,
        rowId: account.id,
        accountName: latest.account_name ?? account.account_name ?? null,
        accountEmail: latest.account_email ?? account.account_email ?? null,
        accessToken: latestAccess,
        expiresAtMs: expMs,
        expiresAtIso: new Date(expMs).toISOString(),
        payload: latestPayload,
        metadata: (latest.metadata && typeof latest.metadata === "object" ? latest.metadata : meta) as Record<string, unknown>,
      };
    };

    if (normProvider === "youtube") {
      const refreshToken =
        typeof credPayload.refresh_token === "string" && credPayload.refresh_token
          ? credPayload.refresh_token
          : (typeof credPayload.token === "string" ? credPayload.token : "");
      if (!refreshToken) {
        await markConnectedAccountNeedsReauth(env, account, "missing_refresh_token");
        throw new ProviderAuthError("needs_reauth", 401, "youtube", "YouTube refresh token missing", resolvedAccountId);
      }
      const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: env.YOUTUBE_CLIENT_ID,
          client_secret: env.YOUTUBE_CLIENT_SECRET,
          refresh_token: refreshToken,
          grant_type: "refresh_token",
        }),
        signal: AbortSignal.timeout(8000),
      });
      const tokenBody = (await tokenRes.json().catch(() => ({}))) as {
        access_token?: string;
        refresh_token?: string;
        expires_in?: number;
        error?: string;
        error_description?: string;
      };
      if (!tokenRes.ok || typeof tokenBody.access_token !== "string" || !tokenBody.access_token) {
        if (tokenRes.status === 400 || tokenRes.status === 401 || tokenBody.error === "invalid_grant" || tokenBody.error === "unauthorized_client") {
          const recovered = await handleCasConflictRecovery();
          if (recovered) return recovered;
          await markConnectedAccountNeedsReauth(env, account, tokenBody.error || `http_${tokenRes.status}`);
          throw new ProviderAuthError("needs_reauth", 401, "youtube", `YouTube OAuth revoked or expired (${tokenBody.error || tokenRes.status})`, resolvedAccountId);
        }
        throw new ProviderAuthError("refresh_failed", 502, "youtube", `YouTube token refresh failed (${tokenRes.status})`, resolvedAccountId);
      }
      const expMs = Date.now() + (typeof tokenBody.expires_in === "number" ? tokenBody.expires_in : 3600) * 1000;
      const updatedPayload: Record<string, unknown> = {
        ...credPayload,
        refresh_token: typeof tokenBody.refresh_token === "string" && tokenBody.refresh_token ? tokenBody.refresh_token : refreshToken,
        access_token: tokenBody.access_token,
        token_refreshed_at: new Date().toISOString(),
        token_expires_at: new Date(expMs).toISOString(),
      };
      const newEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, JSON.stringify(updatedPayload), aad);
      const cas = await casUpdateConnectedAccountCredentials(env, account, oldEnc, newEnc);
      if (cas.conflict) {
        const recovered = await handleCasConflictRecovery();
        if (recovered) return recovered;
      }
      providerTokenCache.set(cacheKey, {
        accessToken: tokenBody.access_token,
        expiresAtMs: Math.min(expMs, Date.now() + SHORT_LIVED_CACHE_CAP_MS),
        payload: updatedPayload,
        accountId: resolvedAccountId,
        rowId: account.id,
        accountName: account.account_name ?? null,
        accountEmail: account.account_email ?? null,
        metadata: meta,
      });
      return {
        provider: "youtube",
        accountId: resolvedAccountId,
        rowId: account.id,
        accountName: account.account_name ?? null,
        accountEmail: account.account_email ?? null,
        accessToken: tokenBody.access_token,
        expiresAtMs: expMs,
        expiresAtIso: new Date(expMs).toISOString(),
        payload: updatedPayload,
        metadata: meta,
      };
    }

    if (normProvider === "instagram") {
      if (!existingAccess) {
        await markConnectedAccountNeedsReauth(env, account, "missing_instagram_token");
        throw new ProviderAuthError("needs_reauth", 401, "instagram", "Instagram access token missing", resolvedAccountId);
      }
      if (credPayload.auth_mode !== "instagram_login") {
        if (Number.isFinite(parsedExpiry) && parsedExpiry <= Date.now()) {
          await markConnectedAccountNeedsReauth(env, account, "instagram_token_expired");
          throw new ProviderAuthError("needs_reauth", 401, "instagram", "Instagram token expired", resolvedAccountId);
        }
        const expMs = Number.isFinite(parsedExpiry) ? parsedExpiry : Date.now() + 30 * 24 * 60 * 60_000;
        if (decryptRes.needsMigration) {
          const newEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, JSON.stringify(credPayload), aad);
          await casUpdateConnectedAccountCredentials(env, account, oldEnc, newEnc).catch(() => null);
        }
        return {
          provider: "instagram",
          accountId: resolvedAccountId,
          rowId: account.id,
          accountName: account.account_name ?? null,
          accountEmail: account.account_email ?? null,
          accessToken: existingAccess,
          expiresAtMs: expMs,
          expiresAtIso: new Date(expMs).toISOString(),
          payload: credPayload,
          metadata: meta,
        };
      }
      const refreshRes = await fetch("https://graph.instagram.com/refresh_access_token", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "ig_refresh_token",
          access_token: existingAccess,
        }),
        signal: AbortSignal.timeout(8000),
      });
      const refreshed = (await refreshRes.json().catch(() => ({}))) as {
        access_token?: string;
        expires_in?: number;
        error?: { code?: number; message?: string };
      };
      if (!refreshRes.ok || !refreshed.access_token) {
        if (refreshRes.status === 400 || refreshRes.status === 401 || refreshed.error?.code === 190) {
          const recovered = await handleCasConflictRecovery();
          if (recovered) return recovered;
          await markConnectedAccountNeedsReauth(env, account, refreshed.error?.message || `http_${refreshRes.status}`);
          throw new ProviderAuthError("needs_reauth", 401, "instagram", "Instagram token expired or revoked; reconnect required", resolvedAccountId);
        }
        throw new ProviderAuthError("refresh_failed", 502, "instagram", `Instagram token refresh failed (${refreshRes.status})`, resolvedAccountId);
      }
      const expMs = Date.now() + (typeof refreshed.expires_in === "number" ? refreshed.expires_in : 5184000) * 1000;
      const updatedPayload: Record<string, unknown> = {
        ...credPayload,
        access_token: refreshed.access_token,
        token_refreshed_at: new Date().toISOString(),
        token_expires_at: new Date(expMs).toISOString(),
      };
      const newEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, JSON.stringify(updatedPayload), aad);
      const cas = await casUpdateConnectedAccountCredentials(env, account, oldEnc, newEnc);
      if (cas.conflict) {
        const recovered = await handleCasConflictRecovery();
        if (recovered) return recovered;
      }
      providerTokenCache.set(cacheKey, {
        accessToken: refreshed.access_token,
        expiresAtMs: Math.min(expMs, Date.now() + SHORT_LIVED_CACHE_CAP_MS),
        payload: updatedPayload,
        accountId: resolvedAccountId,
        rowId: account.id,
        accountName: account.account_name ?? null,
        accountEmail: account.account_email ?? null,
        metadata: meta,
      });
      return {
        provider: "instagram",
        accountId: resolvedAccountId,
        rowId: account.id,
        accountName: account.account_name ?? null,
        accountEmail: account.account_email ?? null,
        accessToken: refreshed.access_token,
        expiresAtMs: expMs,
        expiresAtIso: new Date(expMs).toISOString(),
        payload: updatedPayload,
        metadata: meta,
      };
    }

    if (normProvider === "facebook") {
      if (!existingAccess) {
        await markConnectedAccountNeedsReauth(env, account, "missing_facebook_token");
        throw new ProviderAuthError("needs_reauth", 401, "facebook", "Facebook token missing", resolvedAccountId);
      }
      if (Number.isFinite(parsedExpiry) && parsedExpiry <= Date.now()) {
        await markConnectedAccountNeedsReauth(env, account, "facebook_token_expired");
        throw new ProviderAuthError("needs_reauth", 401, "facebook", "Facebook token expired; reconnect required", resolvedAccountId);
      }
      if (decryptRes.needsMigration) {
        const newEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, JSON.stringify(credPayload), aad);
        await casUpdateConnectedAccountCredentials(env, account, oldEnc, newEnc).catch(() => null);
      }
      const expMs = Number.isFinite(parsedExpiry) ? parsedExpiry : Date.now() + SHORT_LIVED_CACHE_CAP_MS;
      providerTokenCache.set(cacheKey, {
        accessToken: existingAccess,
        expiresAtMs: Math.min(expMs, Date.now() + SHORT_LIVED_CACHE_CAP_MS),
        payload: credPayload,
        accountId: resolvedAccountId,
        rowId: account.id,
        accountName: account.account_name ?? null,
        accountEmail: account.account_email ?? null,
        metadata: meta,
      });
      return {
        provider: "facebook",
        accountId: resolvedAccountId,
        rowId: account.id,
        accountName: account.account_name ?? null,
        accountEmail: account.account_email ?? null,
        accessToken: existingAccess,
        expiresAtMs: expMs,
        expiresAtIso: new Date(expMs).toISOString(),
        payload: credPayload,
        metadata: meta,
      };
    }

    if (normProvider === "linkedin") {
      const refreshToken = typeof credPayload.refresh_token === "string" ? credPayload.refresh_token : "";
      if (!refreshToken) {
        if (!existingAccess || (Number.isFinite(parsedExpiry) && parsedExpiry <= Date.now())) {
          await markConnectedAccountNeedsReauth(env, account, "linkedin_token_expired_no_refresh");
          throw new ProviderAuthError("needs_reauth", 401, "linkedin", "LinkedIn token expired; reconnect required", resolvedAccountId);
        }
        const expMs = Number.isFinite(parsedExpiry) ? parsedExpiry : Date.now() + SHORT_LIVED_CACHE_CAP_MS;
        return {
          provider: "linkedin",
          accountId: resolvedAccountId,
          rowId: account.id,
          accountName: account.account_name ?? null,
          accountEmail: account.account_email ?? null,
          accessToken: existingAccess,
          expiresAtMs: expMs,
          expiresAtIso: new Date(expMs).toISOString(),
          payload: credPayload,
          metadata: meta,
        };
      }
      const tokRes = await fetch("https://www.linkedin.com/oauth/v2/accessToken", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          refresh_token: refreshToken,
          client_id: env.LINKEDIN_CLIENT_ID || "",
          client_secret: env.LINKEDIN_CLIENT_SECRET || "",
        }),
        signal: AbortSignal.timeout(8000),
      });
      const tok = (await tokRes.json().catch(() => ({}))) as {
        access_token?: string;
        refresh_token?: string;
        expires_in?: number;
        error?: string;
      };
      if (!tokRes.ok || !tok.access_token) {
        if (tokRes.status === 400 || tokRes.status === 401 || tok.error === "invalid_grant") {
          const recovered = await handleCasConflictRecovery();
          if (recovered) return recovered;
          await markConnectedAccountNeedsReauth(env, account, tok.error || `http_${tokRes.status}`);
          throw new ProviderAuthError("needs_reauth", 401, "linkedin", "LinkedIn refresh token invalid or revoked", resolvedAccountId);
        }
        throw new ProviderAuthError("refresh_failed", 502, "linkedin", `LinkedIn token refresh failed (${tokRes.status})`, resolvedAccountId);
      }
      const expMs = Date.now() + (typeof tok.expires_in === "number" ? tok.expires_in : 5184000) * 1000;
      const updatedPayload: Record<string, unknown> = {
        ...credPayload,
        access_token: tok.access_token,
        refresh_token: typeof tok.refresh_token === "string" && tok.refresh_token ? tok.refresh_token : refreshToken,
        token_refreshed_at: new Date().toISOString(),
        token_expires_at: new Date(expMs).toISOString(),
      };
      const newEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, JSON.stringify(updatedPayload), aad);
      const cas = await casUpdateConnectedAccountCredentials(env, account, oldEnc, newEnc);
      if (cas.conflict) {
        const recovered = await handleCasConflictRecovery();
        if (recovered) return recovered;
      }
      providerTokenCache.set(cacheKey, {
        accessToken: tok.access_token,
        expiresAtMs: Math.min(expMs, Date.now() + SHORT_LIVED_CACHE_CAP_MS),
        payload: updatedPayload,
        accountId: resolvedAccountId,
        rowId: account.id,
        accountName: account.account_name ?? null,
        accountEmail: account.account_email ?? null,
        metadata: meta,
      });
      return {
        provider: "linkedin",
        accountId: resolvedAccountId,
        rowId: account.id,
        accountName: account.account_name ?? null,
        accountEmail: account.account_email ?? null,
        accessToken: tok.access_token,
        expiresAtMs: expMs,
        expiresAtIso: new Date(expMs).toISOString(),
        payload: updatedPayload,
        metadata: meta,
      };
    }

    // Twitter / X OAuth 2.0 (rotating single-use refresh_token protected by CAS)
    const refreshToken = typeof credPayload.refresh_token === "string" ? credPayload.refresh_token : "";
    if (!refreshToken) {
      if (existingAccess && (!Number.isFinite(parsedExpiry) || parsedExpiry > Date.now())) {
        if (decryptRes.needsMigration) {
          const newEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, JSON.stringify(credPayload), aad);
          await casUpdateConnectedAccountCredentials(env, account, oldEnc, newEnc).catch(() => null);
        }
        const expMs = Number.isFinite(parsedExpiry) ? parsedExpiry : Date.now() + SHORT_LIVED_CACHE_CAP_MS;
        return {
          provider: "twitter",
          accountId: resolvedAccountId,
          rowId: account.id,
          accountName: account.account_name ?? null,
          accountEmail: account.account_email ?? null,
          accessToken: existingAccess,
          expiresAtMs: expMs,
          expiresAtIso: new Date(expMs).toISOString(),
          payload: credPayload,
          metadata: meta,
        };
      }
      await markConnectedAccountNeedsReauth(env, account, "twitter_missing_refresh_token");
      throw new ProviderAuthError("needs_reauth", 401, "twitter", "X (Twitter) refresh token missing; reconnect required", resolvedAccountId);
    }

    const basicAuth = btoa(`${env.TWITTER_CLIENT_ID || ""}:${env.TWITTER_CLIENT_SECRET || ""}`);
    const twRes = await fetch("https://api.twitter.com/2/oauth2/token", {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
        Authorization: `Basic ${basicAuth}`,
      },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        refresh_token: refreshToken,
        client_id: env.TWITTER_CLIENT_ID || "",
      }),
      signal: AbortSignal.timeout(8000),
    });
    const twTok = (await twRes.json().catch(() => ({}))) as {
      access_token?: string;
      refresh_token?: string;
      expires_in?: number;
      error?: string;
    };
    if (!twRes.ok || !twTok.access_token) {
      if (twRes.status === 400 || twRes.status === 401 || twTok.error === "invalid_grant") {
        const recovered = await handleCasConflictRecovery();
        if (recovered) return recovered;
        await markConnectedAccountNeedsReauth(env, account, twTok.error || `http_${twRes.status}`);
        throw new ProviderAuthError("needs_reauth", 401, "twitter", "X (Twitter) refresh token invalid or revoked", resolvedAccountId);
      }
      throw new ProviderAuthError("refresh_failed", 502, "twitter", `X (Twitter) token refresh failed (${twRes.status})`, resolvedAccountId);
    }
    const expMs = Date.now() + (typeof twTok.expires_in === "number" ? twTok.expires_in : 7200) * 1000;
    const updatedPayload: Record<string, unknown> = {
      ...credPayload,
      access_token: twTok.access_token,
      bearer_token: twTok.access_token,
      refresh_token: typeof twTok.refresh_token === "string" && twTok.refresh_token ? twTok.refresh_token : refreshToken,
      token_refreshed_at: new Date().toISOString(),
      token_expires_at: new Date(expMs).toISOString(),
    };
    const newEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, JSON.stringify(updatedPayload), aad);
    const cas = await casUpdateConnectedAccountCredentials(env, account, oldEnc, newEnc);
    if (cas.conflict) {
      const recovered = await handleCasConflictRecovery();
      if (recovered) return recovered;
    }
    providerTokenCache.set(cacheKey, {
      accessToken: twTok.access_token,
      expiresAtMs: Math.min(expMs, Date.now() + SHORT_LIVED_CACHE_CAP_MS),
      payload: updatedPayload,
      accountId: resolvedAccountId,
      rowId: account.id,
      accountName: account.account_name ?? null,
      accountEmail: account.account_email ?? null,
      metadata: meta,
    });
    return {
      provider: "twitter",
      accountId: resolvedAccountId,
      rowId: account.id,
      accountName: account.account_name ?? null,
      accountEmail: account.account_email ?? null,
      accessToken: twTok.access_token,
      expiresAtMs: expMs,
      expiresAtIso: new Date(expMs).toISOString(),
      payload: updatedPayload,
      metadata: meta,
    };
  })().finally(() => {
    inflightProviderRefreshes.delete(cacheKey);
  });

  inflightProviderRefreshes.set(cacheKey, flight);
  return flight;
}

async function getUserYouTubeAccessToken(
  env: Env,
  accountId: string,
  userId: string,
  options: { requireSourceVault?: boolean } = {},
): Promise<string | null> {
  if (!accountId.trim()) return null;
  try {
    const res = await getValidProviderToken(env, {
      userId,
      provider: "youtube",
      accountId,
      requireSourceVault: options.requireSourceVault,
    });
    return res?.accessToken ?? null;
  } catch (err) {
    if (err instanceof ProviderAuthError && (err.code === "needs_reauth" || err.code === "not_connected" || err.code === "ambiguous_owner")) {
      return null;
    }
    throw err;
  }
}

/**
 * Load a connected Meta account's page/user graph token for publication stats.
 * Ownership is enforced by userId filter and AAD v2 vault verification.
 */
async function loadMetaGraphToken(
  env: Env,
  userId: string,
  accountId: string,
  provider: "instagram" | "facebook" = "instagram",
): Promise<{ token: string; payload: Record<string, unknown> } | null> {
  try {
    const res = await getValidProviderToken(env, { userId, provider, accountId });
    if (res) return { token: res.accessToken, payload: res.payload };
  } catch (err) {
    if (err instanceof ProviderAuthError && err.code === "needs_reauth") throw err;
  }
  const altProvider = provider === "instagram" ? "facebook" : "instagram";
  try {
    const alt = await getValidProviderToken(env, { userId, provider: altProvider, accountId });
    if (alt) return { token: alt.accessToken, payload: alt.payload };
  } catch (err) {
    if (err instanceof ProviderAuthError && err.code === "needs_reauth") throw err;
  }
  return null;
}

/**
 * Fetch engagement stats for a published meta post/target. Like the YouTube
 * videos().list path, this never needs the raw credential on the PC: the worker
 * resolves the encrypted token and performs the graph read on trusted infra.
 */
async function fetchMetaPublicationStats(
  env: Env,
  userId: string,
  accountId: string,
  remoteId: string,
  provider: "instagram" | "facebook",
): Promise<{ views: number | null; likes: number | null; comments: number | null }> {
  const cred = await loadMetaGraphToken(env, userId, accountId, provider);
  if (!cred) throw new Error("no usable Meta credential for this publication");
  const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const bearerHeaders = { Authorization: `Bearer ${cred.token}` };

  if (provider === "instagram") {
    const mRes = await fetch(
      `https://graph.facebook.com/v21.0/${encodeURIComponent(remoteId)}?fields=media_type,like_count,comments_count`,
      { headers: bearerHeaders, signal: AbortSignal.timeout(8000) },
    );
    if (!mRes.ok) throw new Error(`Instagram media lookup failed: ${mRes.status}`);
    const media = await mRes.json() as Record<string, unknown>;
    let views: number | null = null;
    if (media.media_type === "VIDEO" || media.media_type === "REELS") {
      const pRes = await fetch(
        `https://graph.facebook.com/v21.0/${encodeURIComponent(remoteId)}?fields=play_count`,
        { headers: bearerHeaders, signal: AbortSignal.timeout(8000) },
      );
      if (pRes.ok) {
        const playJson = await pRes.json() as Record<string, unknown>;
        views = num(playJson.play_count);
      }
    }
    return { views, likes: num(media.like_count), comments: num(media.comments_count) };
  }

  const pRes = await fetch(
    `https://graph.facebook.com/v21.0/${encodeURIComponent(remoteId)}?fields=likes.summary(total_count).limit(0),comments.summary(total_count).limit(0)`,
    { headers: bearerHeaders, signal: AbortSignal.timeout(8000) },
  );
  if (!pRes.ok) throw new Error(`Facebook post lookup failed: ${pRes.status}`);
  const post = await pRes.json() as Record<string, unknown>;
  const summaryCount = (obj: unknown): unknown =>
    (obj as { summary?: { total_count?: unknown } } | null)?.summary?.total_count;
  let views: number | null = null;
  const iRes = await fetch(
    `https://graph.facebook.com/v21.0/${encodeURIComponent(remoteId)}/insights?metric=post_video_views`,
    { headers: bearerHeaders, signal: AbortSignal.timeout(8000) },
  );
  if (iRes.ok) {
    const insights = await iRes.json() as { data?: Array<{ values?: Array<{ value?: unknown }> }> };
    const first = Array.isArray(insights.data) ? insights.data[0] : null;
    const value = first && Array.isArray(first.values) ? first.values[0]?.value : null;
    if (typeof value === "number" && Number.isFinite(value)) views = value;
  }
  return { views, likes: num(summaryCount(post.likes)), comments: num(summaryCount(post.comments)) };
}

async function getDecryptedUserGeminiKey(
  env: Env,
  userId: string,
): Promise<{ apiKey: string | null; keyPrefix: string | null }> {
  const keyRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_get_gemini_key`, {
    method: "POST",
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ p_user_id: userId }),
    signal: AbortSignal.timeout(8000),
  });
  if (!keyRes.ok) {
    throw new Error(`gemini key lookup failed: ${keyRes.status}`);
  }
  const encKey = (await keyRes.json()) as string | null;
  if (!encKey || typeof encKey !== "string") {
    return { apiKey: null, keyPrefix: null };
  }
  const aad = buildVaultAad(userId, "gemini", "default");
  const dec = await decryptTokenWithMigration(env.TOKEN_VAULT_KEY, encKey, aad);
  if (dec.needsMigration) {
    const migratedEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, dec.plaintext, aad);
    const casUrl = new URL(`${env.SUPABASE_URL}/rest/v1/gemini_connections`);
    casUrl.searchParams.set("user_id", `eq.${userId}`);
    casUrl.searchParams.set("api_key_encrypted", `eq.${encKey}`);
    await fetch(casUrl.toString(), {
      method: "PATCH",
      headers: {
        apikey: env.SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({ api_key_encrypted: migratedEnc }),
    }).catch(() => null);
  }
  const apiKey = dec.plaintext.trim();
  const keyPrefix = apiKey.length >= 12 ? `${apiKey.slice(0, 8)}...${apiKey.slice(-4)}` : null;
  return { apiKey: apiKey || null, keyPrefix };
}

const PUBLICATION_STATS_TTL_MS = 15 * 60 * 1000;

interface CloudPublicationRow {
  id: string;
  clip_id?: string | null;
  local_clip_id?: string | null;
  platform: string;
  account_id: string;
  remote_id: string | null;
  remote_url: string | null;
  published_at: string | null;
  publish_mode: string | null;
  view_count: number | null;
  like_count: number | null;
  comment_count: number | null;
  stats_updated_at: string | null;
  stats_checked_at: string | null;
}

function parseMetric(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
}

function publicationPayload(row: CloudPublicationRow): Record<string, unknown> {
  const updated = Date.parse(row.stats_updated_at ?? "");
  const checked = Date.parse(row.stats_checked_at ?? "");
  const statsStale = !Number.isFinite(updated) || (Number.isFinite(checked) && checked > updated);
  return {
    id: row.id,
    platform: row.platform,
    remote_id: row.remote_id,
    remote_url: row.remote_url,
    published_at: row.published_at,
    publish_mode: row.publish_mode,
    stats: {
      views: row.view_count,
      likes: row.like_count,
      comments: row.comment_count,
    },
    stats_updated_at: row.stats_updated_at,
    stats_stale: statsStale,
  };
}

function publicationStatsAreFresh(row: CloudPublicationRow): boolean {
  const checked = Date.parse(row.stats_checked_at ?? "");
  return Number.isFinite(checked) && Date.now() - checked < PUBLICATION_STATS_TTL_MS;
}

// ---------- auth ----------
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Constant-time string comparison for bearer secrets. Length comparison is
// intentionally outside the loop; token lengths are not secret material.
export function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export function parseConfiguredOwnerAllowlist(env: Env): string[] | null {
  const ownerRaw = (env.PC_OWNER_USER_IDS ?? "").trim();
  const owners = ownerRaw.split(",").map(s => s.trim().toLowerCase()).filter(Boolean);
  if (owners.length === 0 || owners.some(owner => !UUID_RE.test(owner))) {
    return null;
  }
  return owners;
}

export function authenticatePcWorkerScope(
  env: Env,
  request: Request,
): { owners: string[]; scopedUserId: string | null } | null {
  if (!isPcWorkerAuth(env, request)) return null;
  const owners = parseConfiguredOwnerAllowlist(env);
  if (!owners) return null;
  const uidHeader = request.headers.get("X-Dispatch-User-Id")?.trim() || "";
  if (uidHeader) {
    if (!UUID_RE.test(uidHeader) || !owners.includes(uidHeader.toLowerCase())) {
      return null;
    }
    return { owners, scopedUserId: uidHeader.toLowerCase() };
  }
  return { owners, scopedUserId: null };
}

export async function authenticate(env: Env, request: Request): Promise<{ userId: string; email: string } | null> {
  const auth = request.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) return null;
  const token = auth.slice(7);
  if (env.DISPATCH_WORKER_TOKEN && timingSafeEqual(token, env.DISPATCH_WORKER_TOKEN)) {
    const uid = request.headers.get("X-Dispatch-User-Id");
    if (!uid || !UUID_RE.test(uid)) return null;
    // D13 (audit 2026-10-27): a worker-token bearer may act for a USER identity
    // ONLY when that user is an explicitly authorized PC owner.
    // Audit 2026-10-28: fail CLOSED when the allowlist is missing or malformed.
    // A permissive fallback let one leaked worker token impersonate every user.
    const owners = parseConfiguredOwnerAllowlist(env);
    if (!owners) {
      console.log(JSON.stringify({
        audit: true,
        error: "PC_OWNER_USER_IDS is missing or malformed; worker-token request rejected",
        route: new URL(request.url).pathname,
        ts: new Date().toISOString(),
      }));
      return null;
    }
    if (!owners.includes(uid.toLowerCase())) {
      console.log(JSON.stringify({
        audit: true,
        error: "worker-token request for unauthorized user",
        route: new URL(request.url).pathname,
        ts: new Date().toISOString(),
      }));
      return null;
    }
    console.log(JSON.stringify({ audit: true, impersonated: true, route: new URL(request.url).pathname, uid, ts: new Date().toISOString() }));
    return { userId: uid, email: "" };
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

/**
 * Probe YouTube video duration in seconds via public watch page metadata.
 */



/**
 * Probe YouTube video title via public oEmbed endpoint.
 */
export async function probeYouTubeTitle(videoId: string): Promise<string | null> {
  if (!/^[A-Za-z0-9_-]{6,20}$/.test(videoId)) return null;
  try {
    const res = await fetch(`https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=${videoId}&format=json`, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      },
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return null;
    const data = await res.json() as { title?: string };
    return typeof data?.title === "string" && data.title.trim() ? data.title.trim() : null;
  } catch {
    return null;
  }
}

export async function probeYouTubeDuration(videoId: string): Promise<number | null> {
  if (!/^[A-Za-z0-9_-]{6,20}$/.test(videoId)) return null;
  try {
    const res = await fetch(`https://www.youtube.com/watch?v=${videoId}`, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
      },
      signal: AbortSignal.timeout(3500),
    });
    if (!res.ok) return null;
    const text = await res.text();
    const matchSec = text.match(/"lengthSeconds":"(\d+)"/);
    if (matchSec) return parseInt(matchSec[1], 10);
    const matchMs = text.match(/"approxDurationMs":"(\d+)"/);
    if (matchMs) return Math.round(parseInt(matchMs[1], 10) / 1000);
    return null;
  } catch {
    return null;
  }
}

const R2_MAX_CHUNK_BYTES = 8 * 1024 * 1024; // 8 MiB (32 * 256 KiB for YouTube resumable alignment)
const X_APPEND_CHUNK_BYTES = 5 * 1024 * 1024; // 5 MiB (<= 8 MiB, X media APPEND cap)
const LINKEDIN_API_VERSION = "202501";

type PublishPlatform = "youtube" | "instagram" | "linkedin" | "twitter" | "facebook";

function normalizePublishPlatform(raw: unknown): PublishPlatform | null {
  const s = String(raw ?? "").trim().toLowerCase();
  if (s === "youtube" || s === "youtube_shorts") return "youtube";
  if (s === "instagram") return "instagram";
  if (s === "linkedin") return "linkedin";
  if (s === "twitter" || s === "x") return "twitter";
  if (s === "facebook") return "facebook";
  return null;
}

function safePublishError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  return raw
    .replace(/Bearer\s+[A-Za-z0-9._-]+/gi, "Bearer [redacted]")
    .replace(/OAuth\s+[A-Za-z0-9._-]+/gi, "OAuth [redacted]")
    .replace(/access_token=[^&\s]+/gi, "access_token=[redacted]")
    .slice(0, 400);
}

async function readResponseBoundedBytes(res: Response, maxBytes = R2_MAX_CHUNK_BYTES): Promise<Uint8Array> {
  if (!res.body) {
    const buf = new Uint8Array(await res.arrayBuffer());
    if (buf.byteLength > maxBytes) {
      throw new Error(`Response body exceeds max allowed size (${buf.byteLength} > ${maxBytes})`);
    }
    return buf;
  }
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value || value.byteLength === 0) continue;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel().catch(() => {});
        throw new Error(`Streamed body exceeds max allowed size (${total} > ${maxBytes})`);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out;
}

type R2SizeProbe = {
  size: number;
  firstObj?: R2ObjectBody | null;
  legacyBytes?: Uint8Array;
};

async function getR2ObjectSize(env: Env, key: string): Promise<R2SizeProbe> {
  if (typeof env.BUCKET.head === "function") {
    const head = await env.BUCKET.head(key);
    if (!head) {
      throw new Error(`R2 video object not found for key: ${key}`);
    }
    return { size: Number(head.size || 0) };
  }
  const firstObj = await env.BUCKET.get(key, { range: { offset: 0, length: R2_MAX_CHUNK_BYTES } });
  if (!firstObj) {
    throw new Error(`R2 video object not found for key: ${key}`);
  }
  const maybeSize = (firstObj as unknown as { size?: unknown }).size;
  if (typeof maybeSize === "number" && Number.isFinite(maybeSize)) {
    return { size: maybeSize, firstObj };
  }
  if (firstObj.body) {
    const reader = firstObj.body.getReader();
    let total = 0;
    const parts: Uint8Array[] = [];
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!value || value.byteLength === 0) continue;
        total += value.byteLength;
        if (total <= R2_MAX_CHUNK_BYTES) {
          parts.push(value);
        }
      }
    } finally {
      reader.releaseLock();
    }
    if (total <= R2_MAX_CHUNK_BYTES) {
      const merged = new Uint8Array(total);
      let pos = 0;
      for (const p of parts) {
        merged.set(p, pos);
        pos += p.byteLength;
      }
      return { size: total, legacyBytes: merged };
    }
    return { size: total };
  }
  const legacyObj = firstObj as unknown as { arrayBuffer?: () => Promise<ArrayBuffer> };
  if (typeof legacyObj.arrayBuffer === "function") {
    const bytes = new Uint8Array(await legacyObj.arrayBuffer());
    return { size: bytes.byteLength, legacyBytes: bytes };
  }
  throw new Error(`R2 video object ${key} has neither size nor body stream`);
}

function concatUint8Chunks(chunks: Uint8Array[], totalBytes: number): Uint8Array {
  if (chunks.length === 1 && chunks[0].byteLength === totalBytes) {
    return chunks[0];
  }
  const out = new Uint8Array(totalBytes);
  let pos = 0;
  for (const c of chunks) {
    out.set(c, pos);
    pos += c.byteLength;
  }
  return out;
}

async function* streamR2RangeChunks(
  env: Env,
  key: string,
  startOffset: number,
  totalRangeBytes: number,
  maxChunkBytes: number,
  probe?: R2SizeProbe,
): AsyncGenerator<{ chunk: Uint8Array; offset: number; length: number }> {
  const chunkLimit = Math.max(1, Math.min(maxChunkBytes, R2_MAX_CHUNK_BYTES));
  if (totalRangeBytes <= 0) return;

  if (probe?.legacyBytes) {
    const endLimit = Math.min(startOffset + totalRangeBytes, probe.legacyBytes.byteLength);
    let pos = startOffset;
    while (pos < endLimit) {
      const nextEnd = Math.min(pos + chunkLimit, endLimit);
      const slice = probe.legacyBytes.subarray(pos, nextEnd);
      yield { chunk: slice, offset: pos, length: slice.byteLength };
      pos = nextEnd;
    }
    return;
  }

  let cursor = startOffset;
  const targetEnd = startOffset + totalRangeBytes;

  while (cursor < targetEnd) {
    const reqLen = Math.min(chunkLimit, targetEnd - cursor);
    let obj: R2ObjectBody | null = null;
    if (cursor === 0 && probe?.firstObj) {
      obj = probe.firstObj;
      probe.firstObj = null;
    } else {
      obj = await env.BUCKET.get(key, { range: { offset: cursor, length: reqLen } });
    }
    if (!obj) {
      throw new Error(`R2 object missing while streaming range at offset ${cursor}: ${key}`);
    }
    if (!obj.body) {
      const maybeBuf = obj as unknown as { arrayBuffer?: () => Promise<ArrayBuffer> };
      if (typeof maybeBuf.arrayBuffer === "function") {
        const bytes = new Uint8Array(await maybeBuf.arrayBuffer());
        const slice = bytes.byteLength > reqLen && !(obj as { range?: unknown }).range
          ? bytes.subarray(cursor, cursor + reqLen)
          : bytes.subarray(0, Math.min(bytes.byteLength, reqLen));
        if (slice.byteLength === 0) break;
        yield { chunk: slice, offset: cursor, length: slice.byteLength };
        cursor += slice.byteLength;
        continue;
      }
      throw new Error(`R2 object missing readable body at offset ${cursor}`);
    }

    const reader = obj.body.getReader();
    const hasExplicitRange = Boolean((obj as unknown as { range?: unknown }).range);
    let acc: Uint8Array[] = [];
    let accLen = 0;
    let streamBytesSeen = 0;
    let skippedPrefix = 0;
    const prefixToSkip = !hasExplicitRange && cursor > 0 ? cursor : 0;

    try {
      while (cursor < targetEnd) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!value || value.byteLength === 0) continue;

        let buf = value;
        if (skippedPrefix < prefixToSkip) {
          const needSkip = prefixToSkip - skippedPrefix;
          if (buf.byteLength <= needSkip) {
            skippedPrefix += buf.byteLength;
            continue;
          }
          buf = buf.subarray(needSkip);
          skippedPrefix += needSkip;
        }

        let bufPos = 0;
        while (bufPos < buf.byteLength && cursor < targetEnd) {
          const remainingInTarget = targetEnd - cursor;
          const spaceInChunk = Math.min(chunkLimit - accLen, remainingInTarget - accLen);
          if (spaceInChunk <= 0) break;
          const take = Math.min(spaceInChunk, buf.byteLength - bufPos);
          acc.push(buf.subarray(bufPos, bufPos + take));
          accLen += take;
          bufPos += take;
          streamBytesSeen += take;

          if (accLen >= chunkLimit || cursor + accLen >= targetEnd) {
            const outChunk = concatUint8Chunks(acc, accLen);
            yield { chunk: outChunk, offset: cursor, length: outChunk.byteLength };
            cursor += outChunk.byteLength;
            acc = [];
            accLen = 0;
          }
        }
      }
      if (accLen > 0 && cursor < targetEnd) {
        const outChunk = concatUint8Chunks(acc, accLen);
        yield { chunk: outChunk, offset: cursor, length: outChunk.byteLength };
        cursor += outChunk.byteLength;
        streamBytesSeen += outChunk.byteLength;
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }

    if (streamBytesSeen === 0) break;
  }
}

function createR2RangeStream(
  env: Env,
  key: string,
  startOffset: number,
  totalRangeBytes: number,
  maxChunkBytes: number,
  probe?: R2SizeProbe,
): ReadableStream<Uint8Array> {
  const iter = streamR2RangeChunks(env, key, startOffset, totalRangeBytes, maxChunkBytes, probe);
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      const next = await iter.next();
      if (next.done) {
        controller.close();
      } else {
        controller.enqueue(next.value.chunk);
      }
    },
    async cancel() {
      await iter.return?.(undefined);
    },
  });
}

function percentEncodeOAuth1(str: string): string {
  return encodeURIComponent(str).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

async function buildOAuth1AuthorizationHeader(
  method: string,
  urlStr: string,
  creds: { apiKey: string; apiSecret: string; oauthToken: string; oauthSecret: string },
): Promise<string> {
  const parsed = new URL(urlStr);
  const baseUrl = `${parsed.origin}${parsed.pathname}`;
  const nonceBytes = new Uint8Array(16);
  crypto.getRandomValues(nonceBytes);
  const nonce = Array.from(nonceBytes, (b) => b.toString(16).padStart(2, "0")).join("");
  const timestamp = String(Math.floor(Date.now() / 1000));

  const oauthParams: Record<string, string> = {
    oauth_consumer_key: creds.apiKey,
    oauth_nonce: nonce,
    oauth_signature_method: "HMAC-SHA1",
    oauth_timestamp: timestamp,
    oauth_token: creds.oauthToken,
    oauth_version: "1.0",
  };

  const allPairs: Array<[string, string]> = [];
  for (const [k, v] of Object.entries(oauthParams)) {
    allPairs.push([percentEncodeOAuth1(k), percentEncodeOAuth1(v)]);
  }
  parsed.searchParams.forEach((v, k) => {
    allPairs.push([percentEncodeOAuth1(k), percentEncodeOAuth1(v)]);
  });
  allPairs.sort((a, b) => (a[0] === b[0] ? (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0) : (a[0] < b[0] ? -1 : 1)));

  const paramString = allPairs.map(([k, v]) => `${k}=${v}`).join("&");
  const signatureBase = `${method.toUpperCase()}&${percentEncodeOAuth1(baseUrl)}&${percentEncodeOAuth1(paramString)}`;
  const signingKey = `${percentEncodeOAuth1(creds.apiSecret)}&${percentEncodeOAuth1(creds.oauthSecret)}`;

  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(signingKey),
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"],
  );
  const sigBuf = await crypto.subtle.sign("HMAC", cryptoKey, new TextEncoder().encode(signatureBase));
  const sigBytes = new Uint8Array(sigBuf);
  let bin = "";
  for (let i = 0; i < sigBytes.length; i++) bin += String.fromCharCode(sigBytes[i]);
  const signature = btoa(bin);

  const headerParts = Object.entries({ ...oauthParams, oauth_signature: signature })
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${percentEncodeOAuth1(k)}="${percentEncodeOAuth1(v)}"`);
  return `OAuth ${headerParts.join(", ")}`;
}

async function resolvePublicationClipRef(
  env: Env,
  userId: string,
  rawClipId: string,
): Promise<{ cloudClipId: string | null; localClipId: string | null }> {
  const svcHeaders = {
    apikey: env.SUPABASE_SERVICE_ROLE_KEY,
    Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
  };
  const clipRef = rawClipId.trim();
  if (!clipRef) return { cloudClipId: null, localClipId: null };

  try {
    const lookupUrl = new URL(`${env.SUPABASE_URL}/rest/v1/clips_cloud`);
    lookupUrl.searchParams.set("user_id", `eq.${userId}`);
    if (UUID_RE.test(clipRef)) {
      lookupUrl.searchParams.set("or", `(id.eq.${clipRef},local_clip_id.eq.${clipRef})`);
    } else {
      lookupUrl.searchParams.set("local_clip_id", `eq.${clipRef}`);
    }
    lookupUrl.searchParams.set("select", "id,local_clip_id");
    lookupUrl.searchParams.set("limit", "1");
    const res = await fetch(lookupUrl.toString(), { headers: svcHeaders, signal: AbortSignal.timeout(8000) });
    if (res.ok) {
      const rows = (await res.json()) as Array<{ id?: string; local_clip_id?: string | null }>;
      if (Array.isArray(rows) && rows.length > 0 && rows[0]?.id) {
        return {
          cloudClipId: String(rows[0].id),
          localClipId: rows[0].local_clip_id ? String(rows[0].local_clip_id) : (!UUID_RE.test(clipRef) ? clipRef : null),
        };
      }
    }
  } catch {}

  return UUID_RE.test(clipRef)
    ? { cloudClipId: clipRef, localClipId: null }
    : { cloudClipId: null, localClipId: clipRef };
}

async function upsertPublicationRow(
  env: Env,
  svc: Record<string, string>,
  params: {
    userId: string;
    clipRef: { cloudClipId: string | null; localClipId: string | null };
    platform: PublishPlatform;
    accountId: string;
    remoteId: string;
    remoteUrl: string | null;
    publishMode: string;
    publishedAt: string;
  },
): Promise<void> {
  const payload: Record<string, unknown> = {
    user_id: params.userId,
    platform: params.platform,
    account_id: params.accountId,
    remote_id: params.remoteId,
    remote_url: params.remoteUrl,
    status: "published",
    publish_mode: params.publishMode,
    published_at: params.publishedAt,
  };
  if (params.clipRef.cloudClipId) {
    payload.clip_id = params.clipRef.cloudClipId;
  } else {
    payload.clip_id = null;
  }
  if (params.clipRef.localClipId) {
    payload.local_clip_id = params.clipRef.localClipId;
  }

  const endpoint = params.clipRef.cloudClipId
    ? `${env.SUPABASE_URL}/rest/v1/publications?on_conflict=clip_id,platform,account_id`
    : `${env.SUPABASE_URL}/rest/v1/publications`;
  const headers: Record<string, string> = params.clipRef.cloudClipId
    ? { ...svc, Prefer: "resolution=merge-duplicates,return=minimal" }
    : { ...svc, Prefer: "return=minimal" };

  await fetch(endpoint, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  }).catch(() => {});
}

function isMetaAuthError(status: number, bodyJson: Record<string, unknown>): boolean {
  if (status === 401 || status === 403) return true;
  const errObj = bodyJson?.error as { code?: number; type?: string } | undefined;
  return Boolean(
    errObj &&
    (errObj.code === 190 || errObj.code === 102 || errObj.type === "OAuthException"),
  );
}

/**
 * Drain queued social publishing jobs directly from Cloudflare Worker without PC dependency.
 * Streams R2 video chunks (<= 8 MiB) without full-file buffering, tracks per-target state
 * transitions in publishing_job_targets, and enforces ProviderAuthError isolation per target.
 */
export async function drainPublishingQueue(
  env: Env,
  options: { pollIntervalMs?: number } = {},
): Promise<{ processed: number; succeeded: number; failed: number }> {
  const svc = {
    apikey: env.SUPABASE_SERVICE_ROLE_KEY,
    Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
    "Content-Type": "application/json",
  };
  const pollIntervalMs = typeof options.pollIntervalMs === "number" && options.pollIntervalMs >= 0
    ? options.pollIntervalMs
    : 1500;

  let processed = 0;
  let succeeded = 0;
  let failed = 0;

  type PublishingJobRow = {
    id: string;
    user_id: string;
    clip_id: string;
    title: string;
    description?: string;
    platforms: string[] | string;
    account_ids?: Record<string, string | string[]> | null;
    publish_mode: "private" | "unlisted" | "public";
    r2_key?: string | null;
    rendered_fingerprint?: string | null;
    platform_results?: Record<string, unknown>;
  };

  type TargetRow = {
    id?: string;
    job_id: string;
    user_id: string;
    platform: string;
    account_id: string;
    status: string;
    attempt_count?: number;
    remote_id?: string | null;
    remote_url?: string | null;
    last_error?: string | null;
    upload_session_url?: string | null;
    synthetic?: boolean;
  };

  const updateTargetState = async (
    target: TargetRow,
    status: "queued" | "uploading" | "processing" | "published" | "failed" | "blocked_needs_auth",
    extra: {
      remoteId?: string | null;
      remoteUrl?: string | null;
      lastError?: string | null;
      uploadSessionUrl?: string | null;
      nextRetryAt?: string | null;
    } = {},
  ): Promise<void> => {
    target.status = status;
    if (extra.remoteId !== undefined) target.remote_id = extra.remoteId;
    if (extra.remoteUrl !== undefined) target.remote_url = extra.remoteUrl;
    if (extra.lastError !== undefined) target.last_error = extra.lastError;
    if (target.synthetic || !target.id) return;

    await rpcRaw(env, "cloud_update_publishing_target", {
      p_target_id: target.id,
      p_status: status,
      p_remote_id: extra.remoteId ?? null,
      p_remote_url: extra.remoteUrl ?? null,
      p_last_error: extra.lastError ?? null,
      p_upload_session_url: extra.uploadSessionUrl ?? null,
      p_next_retry_at: extra.nextRetryAt ?? null,
    }).catch(() => {});
  };

  try {
    // 1. Atomically claim jobs using the SKIP LOCKED RPC, with fallback to PostgREST representation return
    let queuedJobs: PublishingJobRow[] = [];

    try {
      const claimRpcRows = (await rpcRaw(env, "cloud_claim_publishing_jobs", {
        p_limit: 5,
        p_stale_seconds: 600,
      })) as PublishingJobRow[] | null;
      if (Array.isArray(claimRpcRows)) {
        queuedJobs = claimRpcRows;
      }
    } catch {
      // Fallback for environments before migration 0028 is applied:
      const queueRes = await fetch(
        `${env.SUPABASE_URL}/rest/v1/publishing_jobs?or=(status.eq.queued,and(status.eq.processing,updated_at.lt.${new Date(Date.now() - 600000).toISOString()}))&order=created_at.asc&limit=5`,
        { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
      );
      if (queueRes.ok) {
        const candidates = (await queueRes.json()) as PublishingJobRow[];
        for (const candidate of candidates) {
          const claimPatch = await fetch(
            `${env.SUPABASE_URL}/rest/v1/publishing_jobs?id=eq.${encodeURIComponent(candidate.id)}&status=in.(queued,processing)`,
            {
              method: "PATCH",
              headers: {
                ...svc,
                Prefer: "return=representation",
              },
              body: JSON.stringify({ status: "processing", updated_at: new Date().toISOString() }),
            }
          );
          if (claimPatch.ok) {
            const updatedRows = (await claimPatch.json()) as PublishingJobRow[];
            if (Array.isArray(updatedRows) && updatedRows.length > 0) {
              queuedJobs.push(updatedRows[0]);
            }
          }
        }
      }
    }

    if (!Array.isArray(queuedJobs) || queuedJobs.length === 0) {
      return { processed: 0, succeeded: 0, failed: 0 };
    }

    for (const job of queuedJobs) {
      processed++;
      const results: Record<string, unknown> = { ...(job.platform_results || {}) };
      let anySuccess = false;
      let anyPending = false;
      let lastErrorMessage: string | null = null;
      let claimedTargets: TargetRow[] = [];

      try {
        const expectedFinalPrefix = `final/${job.user_id}/`;
        let r2Key = typeof job.r2_key === "string" ? job.r2_key.trim() : null;
        if (r2Key) {
          if (r2Key.includes("..") || !r2Key.startsWith(expectedFinalPrefix)) {
            throw new Error("Forbidden: job r2_key does not belong to job owner final prefix");
          }
          const verifyRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/media_objects?user_id=eq.${job.user_id}&final_key=eq.${encodeURIComponent(r2Key)}&status=eq.active&select=id,user_id,kind,final_key&limit=1`,
            { headers: svc },
          );
          if (verifyRes.ok) {
            const verifyRows = (await verifyRes.json()) as Array<{ user_id?: string; kind?: string }>;
            if (verifyRows.length > 0) {
              const row = verifyRows[0];
              if (row.user_id && row.user_id !== job.user_id) {
                throw new Error("Forbidden: media object belongs to a different user");
              }
              if (row.kind && row.kind !== "clip_video") {
                throw new Error(`Invalid media asset kind for publishing: ${row.kind}`);
              }
            }
          }
        } else {
          const mediaUrl = new URL(`${env.SUPABASE_URL}/rest/v1/media_objects`);
          mediaUrl.searchParams.set("user_id", `eq.${job.user_id}`);
          mediaUrl.searchParams.set("status", "eq.active");
          mediaUrl.searchParams.set("kind", "eq.clip_video");
          mediaUrl.searchParams.set("clip_id", `eq.${job.clip_id}`);
          mediaUrl.searchParams.set("order", "created_at.desc");
          mediaUrl.searchParams.set("limit", "1");
          const mediaRes = await fetch(mediaUrl.toString(), { headers: svc });
          if (mediaRes.ok) {
            const mediaRows = (await mediaRes.json()) as Array<{ final_key?: string | null; kind?: string }>;
            if (
              mediaRows.length > 0 &&
              mediaRows[0].final_key &&
              mediaRows[0].final_key.startsWith(expectedFinalPrefix) &&
              (!mediaRows[0].kind || mediaRows[0].kind === "clip_video")
            ) {
              r2Key = mediaRows[0].final_key;
            }
          }
        }

        if (!r2Key) {
          throw new Error("No owned rendered clip_video R2 object found for publishing job");
        }

        const r2Probe = await getR2ObjectSize(env, r2Key);
        const totalSize = r2Probe.size;
        if (totalSize <= 0) {
          throw new Error("R2 video object is empty (0 bytes)");
        }

        const clipRef = await resolvePublicationClipRef(env, job.user_id, String(job.clip_id));

        // 2. Claim per-target rows from publishing_job_targets (0031), or synthesize when pre-0031
        let existingTargets: TargetRow[] = [];
        let targetRpcSupported = true;
        try {
          const claimed = (await rpcRaw(env, "cloud_claim_publishing_targets", {
            p_job_id: job.id,
            p_limit: 20,
            p_max_attempts: 5,
          })) as TargetRow[] | null;
          if (Array.isArray(claimed)) {
            claimedTargets = claimed;
          }
        } catch {
          targetRpcSupported = false;
        }

        if (targetRpcSupported) {
          const tListRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/publishing_job_targets?job_id=eq.${encodeURIComponent(job.id)}&select=id,job_id,user_id,platform,account_id,status,attempt_count,remote_id,remote_url,last_error`,
            { headers: svc },
          ).catch(() => null);
          if (tListRes && tListRes.ok) {
            const fetched = (await tListRes.json().catch(() => [])) as TargetRow[];
            if (Array.isArray(fetched) && fetched.length > 0) {
              existingTargets = fetched;
              if (fetched.some((t) => t.status === "published")) {
                anySuccess = true;
              }
            }
          }
        }

        if (claimedTargets.length === 0 && existingTargets.length === 0) {
          const rawPlatforms = Array.isArray(job.platforms)
            ? job.platforms
            : (typeof job.platforms === "string" ? job.platforms.split(",").map((s) => s.trim()).filter(Boolean) : ["youtube"]);
          const acctMap = (job.account_ids && typeof job.account_ids === "object") ? job.account_ids : {};
          const seenKeys = new Set<string>();
          for (const rawPlat of rawPlatforms) {
            const norm = normalizePublishPlatform(rawPlat);
            if (!norm) {
              const badKey = String(rawPlat || "").trim().toLowerCase();
              if (badKey) results[badKey] = { status: "skipped_unsupported", platform: badKey };
              continue;
            }
            const rawAccts = acctMap[norm] ?? (norm === "twitter" ? acctMap.x : undefined);
            const acctList = Array.isArray(rawAccts)
              ? rawAccts.map((a) => String(a ?? "").trim()).filter(Boolean)
              : (typeof rawAccts === "string" && rawAccts.trim() ? [rawAccts.trim()] : [""]);
            const effectiveAccts = acctList.length > 0 ? acctList : [""];
            for (const aid of effectiveAccts) {
              const dedupeKey = `${norm}:${aid}`;
              if (seenKeys.has(dedupeKey)) continue;
              seenKeys.add(dedupeKey);
              claimedTargets.push({
                job_id: job.id,
                user_id: job.user_id,
                platform: norm,
                account_id: aid,
                status: "uploading",
                synthetic: true,
              });
            }
          }
        }

        const platformCounts = new Map<string, number>();
        for (const t of claimedTargets) {
          const norm = normalizePublishPlatform(t.platform) ?? t.platform.toLowerCase();
          platformCounts.set(norm, (platformCounts.get(norm) ?? 0) + 1);
        }

        const recordTargetResult = (normPlatform: PublishPlatform, accountId: string, entry: Record<string, unknown>) => {
          const multiForPlatform = (platformCounts.get(normPlatform) ?? 0) > 1;
          if (!multiForPlatform || !(normPlatform in results)) {
            results[normPlatform] = entry;
          }
          if (multiForPlatform || accountId) {
            const perTargetKey = `${normPlatform}:${accountId || "default"}`;
            const targetBucket = (results.targets && typeof results.targets === "object" && !Array.isArray(results.targets))
              ? (results.targets as Record<string, unknown>)
              : {};
            targetBucket[perTargetKey] = entry;
            results.targets = targetBucket;
          }
        };

        for (const target of claimedTargets) {
          const normPlatform = normalizePublishPlatform(target.platform);
          const requestedAccountId = String(target.account_id || "").trim();

          if (!normPlatform) {
            const rawPlat = String(target.platform || "").toLowerCase();
            await updateTargetState(target, "failed", { lastError: `Unsupported publishing platform '${rawPlat}'` });
            results[rawPlat] = { status: "skipped_unsupported", platform: rawPlat };
            continue;
          }

          if (target.remote_id && target.status === "published") {
            anySuccess = true;
            continue;
          }

          try {
            if (normPlatform === "youtube") {
              const tok = await getValidProviderToken(env, {
                userId: job.user_id,
                provider: "youtube",
                accountId: requestedAccountId,
                requireSourceVault: false,
              });
              if (!tok) {
                const msg = requestedAccountId
                  ? `YouTube account '${requestedAccountId}' not connected`
                  : "No connected YouTube account found";
                await updateTargetState(target, "blocked_needs_auth", { lastError: msg });
                recordTargetResult("youtube", requestedAccountId, { status: "blocked_needs_auth", error: msg });
                lastErrorMessage = msg;
                continue;
              }
              const accountId = tok.accountId || requestedAccountId;
              const accessToken = tok.accessToken;

              const initRes = await fetch(
                "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                {
                  method: "POST",
                  headers: {
                    Authorization: `Bearer ${accessToken}`,
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": String(totalSize),
                  },
                  body: JSON.stringify({
                    snippet: {
                      title: job.title.slice(0, 100),
                      description: (job.description || "").slice(0, 5000),
                      categoryId: "22",
                    },
                    status: {
                      privacyStatus: ["private", "unlisted", "public"].includes(job.publish_mode) ? job.publish_mode : "private",
                      selfDeclaredMadeForKids: false,
                    },
                  }),
                },
              );
              if (initRes.status === 401 || initRes.status === 403) {
                const errText = await initRes.text().catch(() => "");
                throw new ProviderAuthError("needs_reauth", 401, "youtube", `YouTube authorization failed (${initRes.status}): ${errText.slice(0, 160)}`, accountId);
              }
              if (!initRes.ok) {
                const errText = await initRes.text().catch(() => "");
                throw new Error(`YouTube resumable session init failed (${initRes.status}): ${errText.slice(0, 200)}`);
              }

              const uploadUrl = initRes.headers.get("Location") || initRes.headers.get("location");
              if (!uploadUrl) {
                throw new Error("YouTube API did not return resumable upload Location header");
              }

              await updateTargetState(target, "uploading", { uploadSessionUrl: uploadUrl });

              let ytResult: { id?: string } | null = null;
              for await (const { chunk, offset, length } of streamR2RangeChunks(env, r2Key, 0, totalSize, R2_MAX_CHUNK_BYTES, r2Probe)) {
                const isMultiChunk = totalSize > R2_MAX_CHUNK_BYTES;
                const putHeaders: Record<string, string> = {
                  "Content-Type": "video/mp4",
                  "Content-Length": String(length),
                };
                if (isMultiChunk) {
                  putHeaders["Content-Range"] = `bytes ${offset}-${offset + length - 1}/${totalSize}`;
                }
                const uploadRes = await fetch(uploadUrl, {
                  method: "PUT",
                  headers: putHeaders,
                  body: chunk,
                });
                if (uploadRes.status === 401 || uploadRes.status === 403) {
                  const errText = await uploadRes.text().catch(() => "");
                  throw new ProviderAuthError("needs_reauth", 401, "youtube", `YouTube upload authorization failed (${uploadRes.status}): ${errText.slice(0, 160)}`, accountId);
                }
                const isFinal = offset + length >= totalSize;
                if (!isFinal && uploadRes.status === 308) {
                  continue;
                }
                if (!uploadRes.ok) {
                  const errText = await uploadRes.text().catch(() => "");
                  throw new Error(`YouTube video upload failed (${uploadRes.status}): ${errText.slice(0, 200)}`);
                }
                ytResult = (await uploadRes.json().catch(() => ({}))) as { id?: string };
                if (ytResult?.id) break;
              }

              const videoId = ytResult?.id;
              if (!videoId) {
                throw new Error("YouTube video uploaded but no video ID returned");
              }
              const remoteUrl = `https://youtube.com/shorts/${videoId}`;

              await updateTargetState(target, "processing", { remoteId: videoId, remoteUrl });

              // Best-effort status sanity check: fail only if YouTube explicitly rejected/failed.
              try {
                const stRes = await fetch(
                  `https://www.googleapis.com/youtube/v3/videos?part=status&id=${encodeURIComponent(videoId)}`,
                  { headers: { Authorization: `Bearer ${accessToken}` }, signal: AbortSignal.timeout(8000) },
                );
                if (stRes.ok) {
                  const stJson = (await stRes.json().catch(() => ({}))) as {
                    items?: Array<{ status?: { uploadStatus?: string; rejectionReason?: string } }>;
                  };
                  const upStatus = stJson.items?.[0]?.status?.uploadStatus;
                  if (upStatus === "failed" || upStatus === "rejected") {
                    throw new Error(`YouTube processing ${upStatus}: ${stJson.items?.[0]?.status?.rejectionReason || "unknown"}`);
                  }
                }
              } catch (stErr) {
                if (stErr instanceof Error && stErr.message.startsWith("YouTube processing ")) {
                  throw stErr;
                }
              }

              const publishedAt = new Date().toISOString();
              await updateTargetState(target, "published", { remoteId: videoId, remoteUrl });
              recordTargetResult("youtube", accountId, {
                status: "published",
                account_id: accountId,
                remote_id: videoId,
                remote_url: remoteUrl,
                published_at: publishedAt,
              });
              anySuccess = true;
              await upsertPublicationRow(env, svc, {
                userId: job.user_id,
                clipRef,
                platform: "youtube",
                accountId,
                remoteId: videoId,
                remoteUrl,
                publishMode: job.publish_mode,
                publishedAt,
              });
            } else if (normPlatform === "instagram") {
              let igCred = await getValidProviderToken(env, {
                userId: job.user_id,
                provider: "instagram",
                accountId: requestedAccountId,
              });
              if (!igCred && !requestedAccountId) {
                const legacyMeta = await loadMetaGraphToken(env, job.user_id, "", "instagram");
                if (legacyMeta?.token) {
                  const fallbackId = String(legacyMeta.payload.ig_user_id || legacyMeta.payload.page_id || legacyMeta.payload.user_id || "");
                  igCred = {
                    provider: "instagram",
                    accountId: fallbackId,
                    rowId: fallbackId,
                    accountName: null,
                    accountEmail: null,
                    accessToken: legacyMeta.token,
                    expiresAtMs: Date.now() + 3600_000,
                    expiresAtIso: new Date(Date.now() + 3600_000).toISOString(),
                    payload: legacyMeta.payload,
                    metadata: {},
                  };
                }
              }
              if (!igCred) {
                const msg = requestedAccountId
                  ? `Instagram account '${requestedAccountId}' not connected`
                  : "No connected Instagram account found";
                await updateTargetState(target, "blocked_needs_auth", { lastError: msg });
                recordTargetResult("instagram", requestedAccountId, { status: "blocked_needs_auth", error: msg });
                lastErrorMessage = msg;
                continue;
              }

              const accountId = igCred.accountId || requestedAccountId;
              const igGraphBase = (
                typeof igCred.payload.api_base === "string" && igCred.payload.api_base.startsWith("https://")
                  ? igCred.payload.api_base
                  : "https://graph.facebook.com/v21.0"
              ).replace(/\/+$/, "");
              const igUserId = String(
                igCred.payload.ig_user_id || igCred.payload.page_id || igCred.payload.user_id || accountId,
              );
              const presignedVideoUrl = await presign(env, "GET", r2Key);
              const captionText = `${job.title}${job.description ? `\n\n${job.description}` : ""}`.slice(0, 2200);

              const containerRes = await fetch(
                `${igGraphBase}/${encodeURIComponent(igUserId)}/media`,
                {
                  method: "POST",
                  headers: {
                    Authorization: `Bearer ${igCred.accessToken}`,
                    "Content-Type": "application/json",
                  },
                  body: JSON.stringify({
                    media_type: "REELS",
                    video_url: presignedVideoUrl,
                    caption: captionText,
                  }),
                },
              );
              const containerBody = (await containerRes.json().catch(() => ({}))) as Record<string, unknown>;
              if (isMetaAuthError(containerRes.status, containerBody)) {
                throw new ProviderAuthError("needs_reauth", 401, "instagram", "Instagram authorization expired or revoked; reconnect required", accountId);
              }
              if (!containerRes.ok) {
                throw new Error(`Instagram container creation failed (${containerRes.status}): ${JSON.stringify(containerBody).slice(0, 200)}`);
              }
              const creationId = typeof containerBody.id === "string" ? containerBody.id : "";
              if (!creationId) throw new Error("No creation ID from Instagram media API");

              await updateTargetState(target, "processing", { remoteId: creationId });

              let containerReady = false;
              for (let poll = 0; poll < 15; poll++) {
                const stRes = await fetch(
                  `${igGraphBase}/${encodeURIComponent(creationId)}?fields=status_code,status`,
                  { headers: { Authorization: `Bearer ${igCred.accessToken}` } },
                );
                const stData = (await stRes.json().catch(() => ({}))) as Record<string, unknown>;
                if (isMetaAuthError(stRes.status, stData)) {
                  throw new ProviderAuthError("needs_reauth", 401, "instagram", "Instagram authorization expired during container processing", accountId);
                }
                if (stRes.ok) {
                  const code = String(stData.status_code || "").toUpperCase();
                  if (code === "FINISHED" || code === "PUBLISHED") {
                    containerReady = true;
                    break;
                  }
                  if (code === "ERROR" || code === "EXPIRED") {
                    throw new Error(`Instagram container processing error: ${String(stData.status || code)}`);
                  }
                }
                if (poll < 14 && pollIntervalMs > 0) {
                  await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
                }
              }

              if (!containerReady) {
                throw new Error(`Instagram container ${creationId} did not reach FINISHED status before timeout`);
              }

              const publishRes = await fetch(
                `${igGraphBase}/${encodeURIComponent(igUserId)}/media_publish`,
                {
                  method: "POST",
                  headers: {
                    Authorization: `Bearer ${igCred.accessToken}`,
                    "Content-Type": "application/json",
                  },
                  body: JSON.stringify({ creation_id: creationId }),
                },
              );
              const pubData = (await publishRes.json().catch(() => ({}))) as Record<string, unknown>;
              if (isMetaAuthError(publishRes.status, pubData)) {
                throw new ProviderAuthError("needs_reauth", 401, "instagram", "Instagram authorization expired before media_publish", accountId);
              }
              if (!publishRes.ok) {
                throw new Error(`Instagram media publish failed (${publishRes.status}): ${JSON.stringify(pubData).slice(0, 200)}`);
              }

              const igMediaId = typeof pubData.id === "string" && pubData.id ? pubData.id : creationId;
              let permalink = `https://www.instagram.com/reel/${igMediaId}`;
              try {
                const pRes = await fetch(
                  `${igGraphBase}/${encodeURIComponent(igMediaId)}?fields=permalink`,
                  { headers: { Authorization: `Bearer ${igCred.accessToken}` } },
                );
                if (pRes.ok) {
                  const pJson = (await pRes.json().catch(() => ({}))) as { permalink?: string };
                  if (typeof pJson.permalink === "string" && pJson.permalink) {
                    permalink = pJson.permalink;
                  }
                }
              } catch {}

              const publishedAt = new Date().toISOString();
              await updateTargetState(target, "published", { remoteId: igMediaId, remoteUrl: permalink });
              recordTargetResult("instagram", accountId, {
                status: "published",
                account_id: accountId,
                remote_id: igMediaId,
                remote_url: permalink,
                published_at: publishedAt,
              });
              anySuccess = true;
              await upsertPublicationRow(env, svc, {
                userId: job.user_id,
                clipRef,
                platform: "instagram",
                accountId,
                remoteId: igMediaId,
                remoteUrl: permalink,
                publishMode: job.publish_mode,
                publishedAt,
              });
            } else if (normPlatform === "linkedin") {
              const liToken = await getValidProviderToken(env, {
                userId: job.user_id,
                provider: "linkedin",
                accountId: requestedAccountId,
              });
              if (!liToken) {
                const msg = requestedAccountId
                  ? `LinkedIn account '${requestedAccountId}' not connected`
                  : "No connected LinkedIn account found";
                await updateTargetState(target, "blocked_needs_auth", { lastError: msg });
                recordTargetResult("linkedin", requestedAccountId, { status: "blocked_needs_auth", error: msg });
                lastErrorMessage = msg;
                continue;
              }
              const accessToken = liToken.accessToken;
              const accountId = liToken.accountId || requestedAccountId;
              const rawUrn = typeof liToken.payload.author_urn === "string"
                ? liToken.payload.author_urn
                : (typeof liToken.payload.account_id === "string" ? liToken.payload.account_id : accountId);
              const authorUrn = rawUrn.startsWith("urn:li:") ? rawUrn : `urn:li:person:${rawUrn}`;
              const liHeaders = {
                Authorization: `Bearer ${accessToken}`,
                "Linkedin-Version": LINKEDIN_API_VERSION,
                "X-Restli-Protocol-Version": "2.0.0",
                "Content-Type": "application/json",
              };

              const initRes = await fetch("https://api.linkedin.com/rest/videos?action=initializeUpload", {
                method: "POST",
                headers: liHeaders,
                body: JSON.stringify({
                  initializeUploadRequest: {
                    owner: authorUrn,
                    fileSizeBytes: totalSize,
                    uploadCaptions: false,
                    uploadThumbnail: false,
                  },
                }),
              });
              if (initRes.status === 401 || initRes.status === 403) {
                const errText = await initRes.text().catch(() => "");
                throw new ProviderAuthError("needs_reauth", 401, "linkedin", `LinkedIn authorization failed (${initRes.status}): ${errText.slice(0, 160)}`, accountId);
              }
              if (!initRes.ok) {
                const errText = await initRes.text().catch(() => "");
                throw new Error(`LinkedIn initializeUpload failed (${initRes.status}): ${errText.slice(0, 200)}`);
              }

              const initJson = (await initRes.json().catch(() => ({}))) as {
                value?: {
                  video?: string;
                  uploadToken?: string;
                  uploadInstructions?: Array<{ uploadUrl?: string; firstByte?: number; lastByte?: number }>;
                };
              };
              const val = initJson.value || {};
              const videoUrn = typeof val.video === "string" ? val.video : "";
              const uploadToken = typeof val.uploadToken === "string" ? val.uploadToken : "";
              const instructions = Array.isArray(val.uploadInstructions) ? val.uploadInstructions : [];
              if (!videoUrn || instructions.length === 0 || !instructions[0]?.uploadUrl) {
                throw new Error("LinkedIn initializeUpload did not return video URN and uploadInstructions");
              }

              await updateTargetState(target, "uploading", { uploadSessionUrl: instructions[0].uploadUrl });

              const uploadedPartIds: string[] = [];
              for (const inst of instructions) {
                if (!inst?.uploadUrl) continue;
                const firstByte = typeof inst.firstByte === "number" ? Math.max(0, inst.firstByte) : 0;
                const lastByte = typeof inst.lastByte === "number" ? Math.min(totalSize - 1, inst.lastByte) : totalSize - 1;
                const partLen = Math.max(0, lastByte - firstByte + 1);
                if (partLen === 0) continue;

                const putBody: BodyInit = partLen <= R2_MAX_CHUNK_BYTES
                  ? (await (async () => {
                      const chunks: Uint8Array[] = [];
                      let got = 0;
                      for await (const c of streamR2RangeChunks(env, r2Key, firstByte, partLen, R2_MAX_CHUNK_BYTES, r2Probe)) {
                        chunks.push(c.chunk);
                        got += c.length;
                      }
                      return concatUint8Chunks(chunks, got);
                    })())
                  : createR2RangeStream(env, r2Key, firstByte, partLen, R2_MAX_CHUNK_BYTES, r2Probe);

                const putRes = await fetch(inst.uploadUrl, {
                  method: "PUT",
                  headers: {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": String(partLen),
                  },
                  body: putBody,
                });
                if (!putRes.ok) {
                  const errText = await putRes.text().catch(() => "");
                  throw new Error(`LinkedIn binary part upload failed (${putRes.status}): ${errText.slice(0, 200)}`);
                }
                const etag = (putRes.headers.get("etag") || putRes.headers.get("ETag") || "").replace(/"/g, "").trim();
                if (etag) uploadedPartIds.push(etag);
              }

              const finRes = await fetch("https://api.linkedin.com/rest/videos?action=finalizeUpload", {
                method: "POST",
                headers: liHeaders,
                body: JSON.stringify({
                  finalizeUploadRequest: {
                    video: videoUrn,
                    uploadToken,
                    uploadedPartIds,
                  },
                }),
              });
              if (finRes.status === 401 || finRes.status === 403) {
                throw new ProviderAuthError("needs_reauth", 401, "linkedin", "LinkedIn authorization expired on finalizeUpload", accountId);
              }
              if (!finRes.ok) {
                const errText = await finRes.text().catch(() => "");
                throw new Error(`LinkedIn finalizeUpload failed (${finRes.status}): ${errText.slice(0, 200)}`);
              }

              await updateTargetState(target, "processing", { remoteId: videoUrn });

              // Poll video status briefly until AVAILABLE (when status endpoint is supported)
              for (let poll = 0; poll < 6; poll++) {
                const vRes = await fetch(
                  `https://api.linkedin.com/rest/videos/${encodeURIComponent(videoUrn)}`,
                  { headers: liHeaders },
                );
                if (vRes.status === 401 || vRes.status === 403) {
                  throw new ProviderAuthError("needs_reauth", 401, "linkedin", "LinkedIn authorization expired while checking video status", accountId);
                }
                if (!vRes.ok) break;
                const vJson = (await vRes.json().catch(() => ({}))) as { status?: string };
                const st = String(vJson.status || "").toUpperCase();
                if (!st || st === "AVAILABLE") break;
                if (st === "PROCESSING_FAILED") {
                  throw new Error(`LinkedIn video processing failed for ${videoUrn}`);
                }
                if (poll < 5 && pollIntervalMs > 0) {
                  await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
                }
              }

              const liRes = await fetch("https://api.linkedin.com/rest/posts", {
                method: "POST",
                headers: liHeaders,
                body: JSON.stringify({
                  author: authorUrn,
                  commentary: `${job.title}${job.description ? `\n\n${job.description}` : ""}`.slice(0, 3000),
                  visibility: job.publish_mode === "public" ? "PUBLIC" : "CONNECTIONS",
                  distribution: {
                    feedDistribution: "MAIN_FEED",
                    targetEntities: [],
                    thirdPartyDistributionChannels: [],
                  },
                  content: {
                    media: {
                      id: videoUrn,
                      title: job.title.slice(0, 200),
                    },
                  },
                  lifecycleState: "PUBLISHED",
                  isReshareDisabledByAuthor: false,
                }),
              });
              if (liRes.status === 401 || liRes.status === 403) {
                const errText = await liRes.text().catch(() => "");
                throw new ProviderAuthError("needs_reauth", 401, "linkedin", `LinkedIn post authorization failed (${liRes.status}): ${errText.slice(0, 160)}`, accountId);
              }
              if (!liRes.ok) {
                const errText = await liRes.text().catch(() => "");
                throw new Error(`LinkedIn post failed (${liRes.status}): ${errText.slice(0, 200)}`);
              }

              const liBodyText = await liRes.text().catch(() => "");
              let liJson: { id?: string } = {};
              if (liBodyText.trim()) {
                try { liJson = JSON.parse(liBodyText) as { id?: string }; } catch {}
              }
              const postUrn = liRes.headers.get("x-restli-id")
                || liRes.headers.get("X-RestLi-Id")
                || liRes.headers.get("x-linkedin-id")
                || liJson.id
                || videoUrn;
              const postUrl = `https://www.linkedin.com/feed/update/${postUrn}`;
              const publishedAt = new Date().toISOString();

              await updateTargetState(target, "published", { remoteId: postUrn, remoteUrl: postUrl });
              recordTargetResult("linkedin", accountId, {
                status: "published",
                account_id: accountId,
                remote_id: postUrn,
                remote_url: postUrl,
                published_at: publishedAt,
              });
              anySuccess = true;
              await upsertPublicationRow(env, svc, {
                userId: job.user_id,
                clipRef,
                platform: "linkedin",
                accountId,
                remoteId: postUrn,
                remoteUrl: postUrl,
                publishMode: job.publish_mode,
                publishedAt,
              });
            } else if (normPlatform === "twitter") {
              const twToken = await getValidProviderToken(env, {
                userId: job.user_id,
                provider: "twitter",
                accountId: requestedAccountId,
              });
              if (!twToken) {
                const msg = requestedAccountId
                  ? `X (Twitter) account '${requestedAccountId}' not connected`
                  : "No connected X (Twitter) account found";
                await updateTargetState(target, "blocked_needs_auth", { lastError: msg });
                recordTargetResult("twitter", requestedAccountId, { status: "blocked_needs_auth", error: msg });
                lastErrorMessage = msg;
                continue;
              }
              const accountId = twToken.accountId || requestedAccountId;
              const p = twToken.payload;
              const hasOAuth1 = Boolean(
                typeof p.api_key === "string" && p.api_key &&
                typeof p.api_secret === "string" && p.api_secret &&
                typeof p.oauth_token === "string" && p.oauth_token &&
                typeof p.oauth_secret === "string" && p.oauth_secret,
              );
              const oauth1Creds = hasOAuth1
                ? {
                    apiKey: String(p.api_key),
                    apiSecret: String(p.api_secret),
                    oauthToken: String(p.oauth_token),
                    oauthSecret: String(p.oauth_secret),
                  }
                : null;
              const bearerAccess = twToken.accessToken;
              const xAuthHeader = async (method: string, reqUrl: string): Promise<string> => {
                if (bearerAccess && !oauth1Creds) return `Bearer ${bearerAccess}`;
                if (oauth1Creds && !bearerAccess) return buildOAuth1AuthorizationHeader(method, reqUrl, oauth1Creds);
                return bearerAccess ? `Bearer ${bearerAccess}` : buildOAuth1AuthorizationHeader(method, reqUrl, oauth1Creds!);
              };

              let mediaId = "";
              let mode: "v2_rest" | "v2_command" | "v1" = bearerAccess ? "v2_rest" : "v1";

              if (mode === "v2_rest") {
                const initUrl = "https://api.x.com/2/media/upload/initialize";
                const initRes = await fetch(initUrl, {
                  method: "POST",
                  headers: {
                    Authorization: await xAuthHeader("POST", initUrl),
                    "Content-Type": "application/json",
                  },
                  body: JSON.stringify({
                    total_bytes: totalSize,
                    media_type: "video/mp4",
                    media_category: "tweet_video",
                  }),
                });
                if (initRes.status === 401 || initRes.status === 403) {
                  if (oauth1Creds) {
                    mode = "v1";
                  } else {
                    const errText = await initRes.text().catch(() => "");
                    throw new ProviderAuthError("needs_reauth", 401, "twitter", `X authorization failed (${initRes.status}): ${errText.slice(0, 160)}`, accountId);
                  }
                } else if (initRes.status === 404 || initRes.status === 405) {
                  mode = "v2_command";
                } else if (!initRes.ok) {
                  const errText = await initRes.text().catch(() => "");
                  throw new Error(`X media INIT failed (${initRes.status}): ${errText.slice(0, 200)}`);
                } else {
                  const initJson = (await initRes.json().catch(() => ({}))) as { data?: { id?: string }; media_id_string?: string };
                  mediaId = String(initJson.data?.id || initJson.media_id_string || "");
                }
              }

              if (!mediaId && (mode === "v2_command" || mode === "v1")) {
                const cmdUrl = mode === "v2_command"
                  ? "https://api.x.com/2/media/upload"
                  : "https://upload.twitter.com/1.1/media/upload.json";
                const authHdr = oauth1Creds && mode === "v1"
                  ? await buildOAuth1AuthorizationHeader("POST", cmdUrl, oauth1Creds)
                  : await xAuthHeader("POST", cmdUrl);
                const initRes = await fetch(cmdUrl, {
                  method: "POST",
                  headers: {
                    Authorization: authHdr,
                    "Content-Type": "application/x-www-form-urlencoded",
                  },
                  body: new URLSearchParams({
                    command: "INIT",
                    media_type: "video/mp4",
                    total_bytes: String(totalSize),
                    media_category: "tweet_video",
                  }),
                });
                if (initRes.status === 401 || initRes.status === 403) {
                  const errText = await initRes.text().catch(() => "");
                  throw new ProviderAuthError("needs_reauth", 401, "twitter", `X media INIT auth failed (${initRes.status}): ${errText.slice(0, 160)}`, accountId);
                }
                if (!initRes.ok) {
                  const errText = await initRes.text().catch(() => "");
                  throw new Error(`X media INIT failed (${initRes.status}): ${errText.slice(0, 200)}`);
                }
                const initJson = (await initRes.json().catch(() => ({}))) as { data?: { id?: string }; media_id_string?: string };
                mediaId = String(initJson.data?.id || initJson.media_id_string || "");
              }

              if (!mediaId) {
                throw new Error("X media INIT did not return media_id");
              }

              // Step 2: APPEND chunks (<= 5 MiB)
              let segmentIndex = 0;
              for await (const { chunk } of streamR2RangeChunks(env, r2Key, 0, totalSize, X_APPEND_CHUNK_BYTES, r2Probe)) {
                const appendUrl = mode === "v2_rest"
                  ? `https://api.x.com/2/media/upload/${encodeURIComponent(mediaId)}/append`
                  : (mode === "v2_command" ? "https://api.x.com/2/media/upload" : "https://upload.twitter.com/1.1/media/upload.json");
                const form = new FormData();
                if (mode !== "v2_rest") {
                  form.append("command", "APPEND");
                  form.append("media_id", mediaId);
                }
                form.append("segment_index", String(segmentIndex));
                form.append("media", new Blob([chunk], { type: "video/mp4" }), "clip.mp4");

                const authHdr = oauth1Creds && mode === "v1"
                  ? await buildOAuth1AuthorizationHeader("POST", appendUrl, oauth1Creds)
                  : await xAuthHeader("POST", appendUrl);
                const appRes = await fetch(appendUrl, {
                  method: "POST",
                  headers: { Authorization: authHdr },
                  body: form,
                });
                if (appRes.status === 401 || appRes.status === 403) {
                  throw new ProviderAuthError("needs_reauth", 401, "twitter", "X media APPEND authorization failed", accountId);
                }
                if (!appRes.ok) {
                  const errText = await appRes.text().catch(() => "");
                  throw new Error(`X media APPEND failed (${appRes.status}): ${errText.slice(0, 200)}`);
                }
                segmentIndex++;
              }

              // Step 3: FINALIZE
              const finUrl = mode === "v2_rest"
                ? `https://api.x.com/2/media/upload/${encodeURIComponent(mediaId)}/finalize`
                : (mode === "v2_command" ? "https://api.x.com/2/media/upload" : "https://upload.twitter.com/1.1/media/upload.json");
              const finAuth = oauth1Creds && mode === "v1"
                ? await buildOAuth1AuthorizationHeader("POST", finUrl, oauth1Creds)
                : await xAuthHeader("POST", finUrl);
              const finRes = await fetch(finUrl, {
                method: "POST",
                headers: mode === "v2_rest"
                  ? { Authorization: finAuth }
                  : { Authorization: finAuth, "Content-Type": "application/x-www-form-urlencoded" },
                body: mode === "v2_rest" ? undefined : new URLSearchParams({ command: "FINALIZE", media_id: mediaId }),
              });
              if (finRes.status === 401 || finRes.status === 403) {
                throw new ProviderAuthError("needs_reauth", 401, "twitter", "X media FINALIZE authorization failed", accountId);
              }
              if (!finRes.ok) {
                const errText = await finRes.text().catch(() => "");
                throw new Error(`X media FINALIZE failed (${finRes.status}): ${errText.slice(0, 200)}`);
              }
              const finJson = (await finRes.json().catch(() => ({}))) as {
                processing_info?: { state?: string; check_after_secs?: number; error?: { message?: string } };
                data?: { processing_info?: { state?: string; check_after_secs?: number; error?: { message?: string } } };
              };

              await updateTargetState(target, "processing", { remoteId: mediaId });

              let procInfo = finJson.processing_info ?? finJson.data?.processing_info;
              for (let poll = 0; poll < 15 && procInfo && (procInfo.state === "pending" || procInfo.state === "in_progress"); poll++) {
                const waitMs = pollIntervalMs > 0 ? Math.min(pollIntervalMs, (procInfo.check_after_secs ?? 2) * 1000) : 0;
                if (waitMs > 0) await new Promise((resolve) => setTimeout(resolve, waitMs));
                const statusBase = mode === "v1"
                  ? "https://upload.twitter.com/1.1/media/upload.json"
                  : "https://api.x.com/2/media/upload";
                const statusUrl = `${statusBase}?command=STATUS&media_id=${encodeURIComponent(mediaId)}`;
                const stAuth = oauth1Creds && mode === "v1"
                  ? await buildOAuth1AuthorizationHeader("GET", statusUrl, oauth1Creds)
                  : await xAuthHeader("GET", statusUrl);
                const stRes = await fetch(statusUrl, { headers: { Authorization: stAuth } });
                if (stRes.status === 401 || stRes.status === 403) {
                  throw new ProviderAuthError("needs_reauth", 401, "twitter", "X media STATUS authorization failed", accountId);
                }
                if (!stRes.ok) break;
                const stJson = (await stRes.json().catch(() => ({}))) as typeof finJson;
                procInfo = stJson.processing_info ?? stJson.data?.processing_info;
                if (procInfo?.state === "failed") {
                  throw new Error(`X media processing failed: ${procInfo.error?.message || "unknown"}`);
                }
              }
              if (procInfo && (procInfo.state === "pending" || procInfo.state === "in_progress")) {
                throw new Error(`X media ${mediaId} timed out processing`);
              }

              // Step 4: Create Tweet v2 with attached media_ids
              const tweetText = `${job.title}${job.description ? `\n\n${job.description}` : ""}`.slice(0, 280);
              const tweetUrl = "https://api.twitter.com/2/tweets";
              const tweetAuth = bearerAccess
                ? `Bearer ${bearerAccess}`
                : await buildOAuth1AuthorizationHeader("POST", tweetUrl, oauth1Creds!);
              const twRes = await fetch(tweetUrl, {
                method: "POST",
                headers: {
                  Authorization: tweetAuth,
                  "Content-Type": "application/json",
                },
                body: JSON.stringify({
                  text: tweetText,
                  media: { media_ids: [mediaId] },
                }),
              });
              if (twRes.status === 401 || twRes.status === 403) {
                const errText = await twRes.text().catch(() => "");
                throw new ProviderAuthError("needs_reauth", 401, "twitter", `X tweet authorization failed (${twRes.status}): ${errText.slice(0, 160)}`, accountId);
              }
              if (!twRes.ok) {
                const errText = await twRes.text().catch(() => "");
                throw new Error(`X tweet failed (${twRes.status}): ${errText.slice(0, 200)}`);
              }

              const twData = (await twRes.json().catch(() => ({}))) as { data?: { id?: string } };
              const tweetId = twData.data?.id;
              if (!tweetId) throw new Error("X tweet response missing data.id");
              const tweetUrlOut = `https://x.com/i/status/${tweetId}`;
              const publishedAt = new Date().toISOString();

              await updateTargetState(target, "published", { remoteId: tweetId, remoteUrl: tweetUrlOut });
              recordTargetResult("twitter", accountId, {
                status: "published",
                account_id: accountId,
                remote_id: tweetId,
                remote_url: tweetUrlOut,
                published_at: publishedAt,
              });
              anySuccess = true;
              await upsertPublicationRow(env, svc, {
                userId: job.user_id,
                clipRef,
                platform: "twitter",
                accountId,
                remoteId: tweetId,
                remoteUrl: tweetUrlOut,
                publishMode: job.publish_mode,
                publishedAt,
              });
            } else if (normPlatform === "facebook") {
              const fbToken = await getValidProviderToken(env, {
                userId: job.user_id,
                provider: "facebook",
                accountId: requestedAccountId,
              });
              if (!fbToken) {
                const msg = requestedAccountId
                  ? `Facebook Page '${requestedAccountId}' not connected`
                  : "No connected Facebook Page found";
                await updateTargetState(target, "blocked_needs_auth", { lastError: msg });
                recordTargetResult("facebook", requestedAccountId, { status: "blocked_needs_auth", error: msg });
                lastErrorMessage = msg;
                continue;
              }
              const pageToken = fbToken.accessToken;
              const accountId = fbToken.accountId || requestedAccountId;
              const pageId = String(fbToken.payload.page_id || fbToken.payload.account_id || accountId);
              const reelsEndpoint = `https://graph.facebook.com/v21.0/${encodeURIComponent(pageId)}/video_reels`;

              const startRes = await fetch(reelsEndpoint, {
                method: "POST",
                headers: {
                  Authorization: `Bearer ${pageToken}`,
                  "Content-Type": "application/json",
                },
                body: JSON.stringify({ upload_phase: "start" }),
              });
              const startData = (await startRes.json().catch(() => ({}))) as Record<string, unknown>;
              if (isMetaAuthError(startRes.status, startData)) {
                throw new ProviderAuthError("needs_reauth", 401, "facebook", "Facebook Page authorization expired or revoked; reconnect required", accountId);
              }
              if (!startRes.ok) {
                throw new Error(`Facebook Reels start failed (${startRes.status}): ${JSON.stringify(startData).slice(0, 200)}`);
              }
              const fbVideoId = typeof startData.video_id === "string" ? startData.video_id : "";
              const fbUploadUrl = typeof startData.upload_url === "string" ? startData.upload_url : "";
              if (!fbVideoId || !fbUploadUrl) {
                throw new Error("Facebook Reels start did not return video_id and upload_url");
              }

              await updateTargetState(target, "uploading", { uploadSessionUrl: fbUploadUrl, remoteId: fbVideoId });

              let upBody: BodyInit;
              if (totalSize <= R2_MAX_CHUNK_BYTES) {
                const chunks: Uint8Array[] = [];
                let got = 0;
                for await (const c of streamR2RangeChunks(env, r2Key, 0, totalSize, R2_MAX_CHUNK_BYTES, r2Probe)) {
                  chunks.push(c.chunk);
                  got += c.length;
                }
                upBody = concatUint8Chunks(chunks, got);
              } else {
                upBody = createR2RangeStream(env, r2Key, 0, totalSize, R2_MAX_CHUNK_BYTES, r2Probe);
              }

              const upRes = await fetch(fbUploadUrl, {
                method: "POST",
                headers: {
                  Authorization: `OAuth ${pageToken}`,
                  offset: "0",
                  file_size: String(totalSize),
                  "Content-Type": "application/octet-stream",
                  "Content-Length": String(totalSize),
                },
                body: upBody,
              });
              const upJson = (await upRes.json().catch(() => ({}))) as Record<string, unknown>;
              if (isMetaAuthError(upRes.status, upJson)) {
                throw new ProviderAuthError("needs_reauth", 401, "facebook", "Facebook Reel binary upload authorization failed", accountId);
              }
              if (!upRes.ok) {
                throw new Error(`Facebook Reel upload failed (${upRes.status}): ${JSON.stringify(upJson).slice(0, 200)}`);
              }

              const fbDesc = `${job.title}${job.description ? `\n\n${job.description}` : ""}`.slice(0, 5000);
              const finishRes = await fetch(reelsEndpoint, {
                method: "POST",
                headers: {
                  Authorization: `Bearer ${pageToken}`,
                  "Content-Type": "application/json",
                },
                body: JSON.stringify({
                  upload_phase: "finish",
                  video_id: fbVideoId,
                  video_state: "PUBLISHED",
                  description: fbDesc,
                }),
              });
              const finishJson = (await finishRes.json().catch(() => ({}))) as Record<string, unknown>;
              if (isMetaAuthError(finishRes.status, finishJson)) {
                throw new ProviderAuthError("needs_reauth", 401, "facebook", "Facebook Reel finish authorization failed", accountId);
              }
              if (!finishRes.ok) {
                throw new Error(`Facebook Reel finish failed (${finishRes.status}): ${JSON.stringify(finishJson).slice(0, 200)}`);
              }

              await updateTargetState(target, "processing", { remoteId: fbVideoId });

              for (let poll = 0; poll < 12; poll++) {
                const stRes = await fetch(
                  `https://graph.facebook.com/v21.0/${encodeURIComponent(fbVideoId)}?fields=status`,
                  { headers: { Authorization: `Bearer ${pageToken}` } },
                );
                const stData = (await stRes.json().catch(() => ({}))) as Record<string, unknown>;
                if (isMetaAuthError(stRes.status, stData)) {
                  throw new ProviderAuthError("needs_reauth", 401, "facebook", "Facebook authorization expired while polling Reel status", accountId);
                }
                if (stRes.ok) {
                  const stObj = (stData.status && typeof stData.status === "object" ? stData.status : {}) as {
                    video_status?: string;
                    processing_phase?: { status?: string };
                  };
                  const vStatus = String(stObj.video_status || "").toLowerCase();
                  const pStatus = String(stObj.processing_phase?.status || "").toLowerCase();
                  if (vStatus === "error" || pStatus === "error") {
                    throw new Error(`Facebook Reel processing failed: ${JSON.stringify(stObj).slice(0, 200)}`);
                  }
                  if (vStatus === "ready" || vStatus === "complete" || pStatus === "complete") {
                    break;
                  }
                }
                if (poll < 11 && pollIntervalMs > 0) {
                  await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
                }
              }

              const remoteUrl = `https://www.facebook.com/reel/${fbVideoId}`;
              const publishedAt = new Date().toISOString();
              await updateTargetState(target, "published", { remoteId: fbVideoId, remoteUrl });
              recordTargetResult("facebook", accountId, {
                status: "published",
                account_id: accountId,
                remote_id: fbVideoId,
                remote_url: remoteUrl,
                published_at: publishedAt,
              });
              anySuccess = true;
              await upsertPublicationRow(env, svc, {
                userId: job.user_id,
                clipRef,
                platform: "facebook",
                accountId,
                remoteId: fbVideoId,
                remoteUrl,
                publishMode: job.publish_mode,
                publishedAt,
              });
            }
          } catch (targetErr) {
            const msg = safePublishError(targetErr);
            if (targetErr instanceof ProviderAuthError && targetErr.code === "needs_reauth") {
              await updateTargetState(target, "blocked_needs_auth", { lastError: msg });
              recordTargetResult(normPlatform, requestedAccountId, {
                status: "blocked_needs_auth",
                needs_reauth: true,
                error: msg,
              });
            } else {
              await updateTargetState(target, "failed", { lastError: msg });
              recordTargetResult(normPlatform, requestedAccountId, {
                status: "failed",
                error: msg,
              });
            }
            lastErrorMessage = msg;
          }
        }

        if (!anySuccess && existingTargets.some((t) => t.status === "queued" || t.status === "processing")) {
          anyPending = true;
        }

        const finalStatus = anySuccess ? "published" : (anyPending ? "processing" : "failed");
        await fetch(
          `${env.SUPABASE_URL}/rest/v1/publishing_jobs?id=eq.${encodeURIComponent(job.id)}`,
          {
            method: "PATCH",
            headers: svc,
            body: JSON.stringify({
              status: finalStatus,
              platform_results: results,
              last_error: anySuccess ? null : (lastErrorMessage || "All publishing targets failed"),
              completed_at: anySuccess ? new Date().toISOString() : null,
              updated_at: new Date().toISOString(),
            }),
          }
        );

        if (anySuccess) succeeded++;
        else if (!anyPending) failed++;
      } catch (jobErr) {
        const errorStr = safePublishError(jobErr);
        for (const t of claimedTargets) {
          if (!t.synthetic && t.id && t.status !== "published") {
            await updateTargetState(t, "failed", { lastError: errorStr });
          }
        }
        await fetch(
          `${env.SUPABASE_URL}/rest/v1/publishing_jobs?id=eq.${encodeURIComponent(job.id)}`,
          {
            method: "PATCH",
            headers: svc,
            body: JSON.stringify({
              status: "failed",
              last_error: errorStr,
              updated_at: new Date().toISOString(),
            }),
          }
        ).catch(() => {});
        failed++;
      }
    }
  } catch (drainErr) {
    console.error("drainPublishingQueue encountered error:", drainErr);
  }

  return { processed, succeeded, failed };
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
  return timingSafeEqual(auth.slice(7), env.DISPATCH_WORKER_TOKEN);
}

/**
 * Long-lived Instagram tokens expire after 60 days unless refreshed. Refresh
 * every instagram_login connection older than 30 days so publishing never
 * silently stops; failures are logged and retried on the next cron tick.
 */
async function refreshInstagramLoginTokens(env: Env): Promise<void> {
  const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
  const listRes = await fetch(
    `${env.SUPABASE_URL}/rest/v1/connected_accounts?provider=eq.instagram&select=id,user_id,account_id,credentials_encrypted,metadata`,
    { headers: svc },
  );
  if (!listRes.ok) {
    console.error("Instagram token refresh: could not list accounts", listRes.status);
    return;
  }
  const rows = await listRes.json() as ConnectedAccountRow[];
  const refreshAfterMs = 30 * 24 * 60 * 60 * 1000;
  const expiryWarnWindowMs = 7 * 24 * 60 * 60 * 1000;
  for (const row of rows) {
    try {
      if (!row.credentials_encrypted || !row.user_id) continue;
      if (row.metadata?.needs_reauth === true) continue;
      const resolvedAccountId = String(row.account_id || row.id);
      const aad = buildVaultAad(row.user_id, "instagram", resolvedAccountId);
      const dec = await decryptTokenWithMigration(env.TOKEN_VAULT_KEY, row.credentials_encrypted, aad);
      if (!dec.plaintext.startsWith("{")) continue;
      const creds = JSON.parse(dec.plaintext) as Record<string, unknown>;
      if (creds.auth_mode !== "instagram_login" || typeof creds.access_token !== "string") continue;

      const expiresAtMs = typeof creds.token_expires_at === "string" ? Date.parse(creds.token_expires_at) : Number.NaN;
      const lastRefresh = Date.parse(
        (creds.token_refreshed_at as string) || (creds.token_obtained_at as string) || "",
      );
      const nearExpiry = Number.isFinite(expiresAtMs) && expiresAtMs - Date.now() < expiryWarnWindowMs;
      const dueByAge = !Number.isFinite(lastRefresh) || Date.now() - lastRefresh >= refreshAfterMs;
      if (!nearExpiry && !dueByAge) {
        if (dec.needsMigration) {
          const migratedEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, JSON.stringify(creds), aad);
          await casUpdateConnectedAccountCredentials(env, row, row.credentials_encrypted, migratedEnc).catch(() => null);
        }
        continue;
      }

      const refreshRes = await fetch("https://graph.instagram.com/refresh_access_token", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "ig_refresh_token",
          access_token: creds.access_token,
        }),
      });
      if (!refreshRes.ok) {
        if (refreshRes.status === 400 || refreshRes.status === 401) {
          await markConnectedAccountNeedsReauth(env, row, `ig_cron_refresh_${refreshRes.status}`);
        }
        console.error("Instagram token refresh failed for", row.id, refreshRes.status);
        continue;
      }
      const refreshed = await refreshRes.json() as { access_token?: string; expires_in?: number };
      if (!refreshed.access_token) continue;

      const now = new Date();
      const updated = {
        ...creds,
        access_token: refreshed.access_token,
        token_refreshed_at: now.toISOString(),
        token_expires_at: new Date(now.getTime() + (refreshed.expires_in ?? 5184000) * 1000).toISOString(),
      };
      const enc = await encryptTokenV2(env.TOKEN_VAULT_KEY, JSON.stringify(updated), aad);
      const cas = await casUpdateConnectedAccountCredentials(env, row, row.credentials_encrypted, enc);
      if (!cas.updated && !cas.conflict) {
        console.error("Instagram token refresh: could not persist", row.id);
      } else if (cas.updated) {
        console.log("Instagram token refreshed for", row.id);
      }
    } catch (err) {
      console.error("Instagram token refresh error:", err instanceof Error ? err.message : "unknown error");
    }
  }
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
  async fetch(request: Request, env: Env, ctx?: ExecutionContext): Promise<Response> {
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

    // ---- Public Cached Avatars (Permanent, CORS-enabled) ----
    if (url.pathname.startsWith("/avatars/") && (request.method === "GET" || request.method === "HEAD")) {
      const key = decodeURIComponent(url.pathname.slice("/avatars/".length));
      if (!key || key.includes("..")) return json(400, { error: "invalid avatar key" });
      let obj = await env.BUCKET.get("avatars/" + key);

      // On-demand cache fill only from the account's own stored public avatar URL.
      // Never decrypt credentials, traverse other providers, or mutate DB rows on
      // an unauthenticated public avatar GET, and never interpolate accountId into or=().
      if (!obj && key.startsWith("instagram_") && key.endsWith(".jpg")) {
        const accountId = key.replace(/^instagram_/, "").replace(/\.jpg$/, "");
        if (!/^[A-Za-z0-9._-]{1,128}$/.test(accountId)) {
          return json(400, { error: "invalid avatar account id" });
        }
        try {
          const accUrl = new URL(`${env.SUPABASE_URL}/rest/v1/connected_accounts`);
          accUrl.searchParams.set("account_id", `eq.${accountId}`);
          accUrl.searchParams.set("provider", "eq.instagram");
          accUrl.searchParams.set("select", "avatar_url");
          accUrl.searchParams.set("limit", "1");
          const accRes = await fetch(accUrl.toString(), {
            headers: {
              apikey: env.SUPABASE_SERVICE_ROLE_KEY,
              Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
            },
          });
          if (accRes.ok) {
            const rows = (await accRes.json()) as Array<{ avatar_url?: string }>;
            const accRow = rows[0];
            if (accRow?.avatar_url && !accRow.avatar_url.includes(url.origin)) {
              const directRes = await fetch(accRow.avatar_url, {
                headers: {
                  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                  "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                },
              });
              if (directRes.ok) {
                const fetchedBytes = await readResponseBoundedBytes(directRes, R2_MAX_CHUNK_BYTES);
                if (fetchedBytes.byteLength > 0) {
                  await env.BUCKET.put("avatars/" + key, fetchedBytes, {
                    httpMetadata: { contentType: "image/jpeg" },
                  });
                  obj = await env.BUCKET.get("avatars/" + key);
                }
              }
            }
          }
        } catch (e) {
          console.error("On-demand Instagram avatar fetch failed:", e instanceof Error ? e.message : String(e));
        }
      }

      if (obj) {
        const headers = new Headers();
        headers.set("Content-Type", obj.httpMetadata?.contentType || "image/jpeg");
        headers.set("Cache-Control", "public, max-age=604800, immutable");
        headers.set("Access-Control-Allow-Origin", "*");
        return new Response(request.method === "HEAD" ? null : obj.body, { status: 200, headers });
      }
      return json(404, { error: "avatar not found" });
    }

    // ---- Direct Media Streaming (HTTP Range + CORS for HTML5 video playback) ----
    if (request.method === "OPTIONS" && url.pathname.match(/^\/media\/[0-9a-fA-F-]{32,36}\/stream$/i)) {
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
          "Access-Control-Allow-Headers": "Range, Content-Type, Authorization",
          "Access-Control-Max-Age": "86400",
        },
      });
    }

    if ((request.method === "GET" || request.method === "HEAD") && url.pathname.match(/^\/media\/[0-9a-fA-F-]{32,36}\/stream$/i)) {
      const rawMediaId = url.pathname.split("/")[2];
      const mediaObjectId = rawMediaId.length === 32 && !rawMediaId.includes("-")
        ? rawMediaId.replace(/^([0-9a-f]{8})([0-9a-f]{4})([0-9a-f]{4})([0-9a-f]{4})([0-9a-f]{12})$/i, "$1-$2-$3-$4-$5")
        : rawMediaId;
      let finalKey: string | null = null;
      let mimeType: string = "video/mp4";

      // 1. FAST PATH (<0.1ms): Edge-verified encrypted streaming ticket bound to mediaObjectId
      const sig = url.searchParams.get("sig");
      if (sig) {
        try {
          const dec = await decryptTokenWithMigration(env.TOKEN_VAULT_KEY, sig, `media_stream|${mediaObjectId}`);
          if (dec.plaintext.startsWith("{")) {
            const ticket = JSON.parse(dec.plaintext) as { k?: string; u?: string; m?: string; e?: number };
            if (ticket.k && ticket.e && Date.now() < ticket.e) {
              finalKey = ticket.k;
              if (ticket.m) mimeType = ticket.m;
            }
          }
        } catch {}
      }

      // 2. FALLBACK: Header-authenticated Supabase Auth + DB lookup (never ?token= query auth)
      if (!finalKey) {
        const user = await authenticate(env, request);
        if (!user) return json(401, { error: "unauthorized" });

        const obj = await selectOne<MediaObj>(env, "media_objects", mediaObjectId, "user_id,status,final_key,byte_count,mime_type");
        if (!obj || obj.user_id !== user.userId) return json(403, { error: "forbidden" });
        if (obj.status !== "active" || !obj.final_key) return json(409, { error: "media not active" });
        finalKey = obj.final_key;
        if (obj.mime_type) mimeType = obj.mime_type;
      }

      const r2Object = await env.BUCKET.get(finalKey, {
        range: request.headers,
      });
      if (!r2Object) return json(404, { error: "media file not found in storage" });

      const headers = new Headers();
      r2Object.writeHttpMetadata(headers);
      headers.set("etag", r2Object.httpEtag);
      headers.set("Access-Control-Allow-Origin", "*");
      headers.set("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS");
      headers.set("Access-Control-Allow-Headers", "Range, Content-Type, Authorization");
      headers.set("Access-Control-Expose-Headers", "Content-Range, Content-Length, Accept-Ranges, Content-Type, ETag");
      headers.set("Accept-Ranges", "bytes");
      headers.set("Cache-Control", "public, max-age=604800, s-maxage=604800, immutable");

      const mime = mimeType || headers.get("Content-Type") || "video/mp4";
      headers.set("Content-Type", mime);

      if (request.method === "HEAD") {
        headers.set("Content-Length", String(r2Object.size));
        return new Response(null, { status: 200, headers });
      }

      if (r2Object.range) {
        const range = r2Object.range as { offset: number; length: number };
        headers.set("Content-Range", `bytes ${range.offset}-${range.offset + range.length - 1}/${r2Object.size}`);
        headers.set("Content-Length", String(range.length));
        return new Response(r2Object.body, { status: 206, headers });
      }

      headers.set("Content-Length", String(r2Object.size));
      return new Response(r2Object.body, { status: 200, headers });
    }
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
      const rawAccounts = Array.isArray(accounts) ? (accounts as any[]) : [];
      const enrichedAccounts = await Promise.all(rawAccounts.map(async (acc) => {
        if (acc.provider === "instagram" && acc.account_id) {
          const cachedKey = `avatars/instagram_${acc.account_id}.jpg`;
          const cachedUrl = `${url.origin}/${cachedKey}`;
          if (acc.avatar_url !== cachedUrl) {
            const head = await env.BUCKET.head(cachedKey);
            if (head) {
              fetch(`${env.SUPABASE_URL}/rest/v1/connected_accounts?id=eq.${acc.id}`, {
                method: "PATCH",
                headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
                body: JSON.stringify({ avatar_url: cachedUrl }),
              }).catch(() => null);
              return { ...acc, avatar_url: cachedUrl };
            }
          }
        }
        return acc;
      }));
      return json(200, { accounts: enrichedAccounts });
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
      const enc = await encryptToken(
        env.TOKEN_VAULT_KEY,
        credString,
        buildVaultAad(user.userId, normalizedProvider, accountId),
      );

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
        const uploadDate = new Date();
        const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
        const month = months[uploadDate.getMonth()];
        const day = uploadDate.getDate();
        const year = uploadDate.getFullYear();
        let hours = uploadDate.getHours();
        const ampm = hours >= 12 ? "PM" : "AM";
        hours = hours % 12;
        hours = hours ? hours : 12;
        const minutes = String(uploadDate.getMinutes()).padStart(2, "0");
        const title = `${month} ${day}, ${year} · ${hours}:${minutes} ${ampm} — Recording Part ${body.sequence_number}`;
        const shortSessionId = sessionId.slice(-6);
        const description = `Preserved recording segment for AI highlight extraction.\nRecorded via Dispatch Mobile Studio.\n\nSession: #${shortSessionId} · Part ${body.sequence_number}\nID: ${dispatchId}`;
        const sha = typeof body.sha256 === "string" ? body.sha256.slice(0, 128) : "";
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

    if (url.pathname === "/youtube/videos/delete" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" }, reqOrigin);
      const body = await request.json().catch(() => ({})) as { video_id?: string; account_id?: string };
      const videoId = typeof body.video_id === "string" ? body.video_id.trim() : "";
      const accountId = typeof body.account_id === "string" ? body.account_id.trim() : "";
      if (!/^[A-Za-z0-9_-]{11}$/.test(videoId) || (accountId && !/^[0-9a-f-]{36}$/i.test(accountId))) {
        return json(400, { error: "invalid delete request" }, reqOrigin);
      }
      // AUTHORITATIVE PROVENANCE GUARD:
      // Deleting a YouTube video is strictly permitted ONLY if Dispatch can prove
      // this authenticated user uploaded that video as a native camera recording (source_type = 'camera').
      // Route B imported YouTube links or any non-camera videos are strictly blocked (403 Forbidden).
      const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
      const jobCheckRes = await fetch(
        `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=eq.${user.userId}&or=(video_id.eq.${encodeURIComponent(videoId)},youtube_url.ilike.*${encodeURIComponent(videoId)}*)&select=id,source_type,session_id&limit=5`,
        { headers: svc }
      );
      if (!jobCheckRes.ok) {
        return json(502, { error: `provenance verification failed: ${jobCheckRes.status}` }, reqOrigin);
      }
      const matchingJobs = await jobCheckRes.json() as Array<{ id: string; source_type?: string; session_id?: string }>;
      const isVerifiedCameraUpload = matchingJobs.some(j => j.source_type === "camera" || (j.session_id && j.session_id.startsWith("sess_")));
      if (!isVerifiedCameraUpload) {
        return json(403, {
          deleted: false,
          error: "forbidden: only Dispatch camera recordings can be deleted from YouTube. Imported YouTube links cannot be removed from YouTube."
        }, reqOrigin);
      }

      // Only the user's own vault accounts are ever used; real ownership is
      // enforced by YouTube itself (a non-owned video id returns 403, a
      // missing one 404, and both leave the caller's data untouched).
      const listRes = await fetch(
        `${env.SUPABASE_URL}/rest/v1/connected_accounts?user_id=eq.${user.userId}&provider=eq.youtube&select=id&limit=10`,
        { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } });
      if (!listRes.ok) return json(502, { error: `account lookup failed: ${listRes.status}` }, reqOrigin);
      const accounts = await listRes.json() as Array<{ id: string }>;
      const candidates = accountId ? [accountId] : accounts.map(a => a.id).slice(0, 5);
      if (candidates.length === 0) return json(409, { error: "no linked YouTube account" }, reqOrigin);
      let lastError = "";
      for (const acc of candidates) {
        try {
          const accessToken = await getUserYouTubeAccessToken(env, acc, user.userId);
          if (!accessToken) { lastError = "account disconnected"; continue; }
          const delRes = await fetch(`https://www.googleapis.com/youtube/v3/videos?id=${encodeURIComponent(videoId)}`, {
            method: "DELETE",
            headers: { Authorization: `Bearer ${accessToken}` },
            signal: AbortSignal.timeout(8000),
          });
          if (delRes.status === 204) return json(200, { deleted: true }, reqOrigin);
          if (delRes.status === 404) return json(200, { deleted: true, note: "video already missing" }, reqOrigin);
          lastError = `youtube_http_${delRes.status}`;
        } catch (error) {
          lastError = error instanceof Error ? error.message : "unknown error";
        }
      }
      return json(200, { deleted: false, error: lastError || "delete failed" }, reqOrigin);
    }

    if (url.pathname === "/youtube/duration" && request.method === "GET") {
      const vId = (url.searchParams.get("video_id") || "").trim();
      if (!vId) return json(400, { error: "missing video_id" }, '*');
      const [dur, title] = await Promise.all([
        probeYouTubeDuration(vId),
        probeYouTubeTitle(vId),
      ]);
      const patchObj: Record<string, unknown> = {};
      if (dur && dur > 0) patchObj.duration = dur;
      if (title) patchObj.source_title = title;
      if (Object.keys(patchObj).length > 0) {
        fetch(`${env.SUPABASE_URL}/rest/v1/processing_jobs?or=(video_id.eq.${encodeURIComponent(vId)},youtube_url.ilike.*${encodeURIComponent(vId)}*)`, {
          method: "PATCH",
          headers: {
            apikey: env.SUPABASE_SERVICE_ROLE_KEY,
            Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(patchObj),
        }).catch(() => null);
      }
      if (dur && dur > 0) {
        return json(200, { duration: dur, title: title || null }, '*');
      }
      return json(200, { duration: null, title: title || null }, '*');
    }

    // ---- Per-User Gemini API Keys Vault ----
    if (url.pathname === "/keys/gemini" && request.method === "GET") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const includeKey = url.searchParams.get("include_key") === "true";
      if (includeKey) {
        const keyRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_get_gemini_key`, {
          method: "POST",
          headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
          body: JSON.stringify({ p_user_id: user.userId }),
        });
        if (keyRes.ok) {
          const encKey = await keyRes.json() as string | null;
          if (encKey && typeof encKey === "string") {
            try {
              const aad = buildVaultAad(user.userId, "gemini", "default");
              const dec = await decryptTokenWithMigration(env.TOKEN_VAULT_KEY, encKey, aad);
              if (dec.needsMigration) {
                const migratedEnc = await encryptTokenV2(env.TOKEN_VAULT_KEY, dec.plaintext, aad);
                const casUrl = new URL(`${env.SUPABASE_URL}/rest/v1/gemini_connections`);
                casUrl.searchParams.set("user_id", `eq.${user.userId}`);
                casUrl.searchParams.set("api_key_encrypted", `eq.${encKey}`);
                await fetch(casUrl.toString(), {
                  method: "PATCH",
                  headers: {
                    apikey: env.SUPABASE_SERVICE_ROLE_KEY,
                    Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
                    "content-type": "application/json",
                  },
                  body: JSON.stringify({ api_key_encrypted: migratedEnc }),
                }).catch(() => null);
              }
              return json(200, { configured: true, api_key: dec.plaintext });
            } catch (e) {
              console.warn("Failed to decrypt gemini key:", e instanceof Error ? e.message : String(e));
            }
          }
        }
      }
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
      const enc = await encryptToken(
        env.TOKEN_VAULT_KEY,
        apiKey,
        buildVaultAad(user.userId, "gemini", "default"),
      );
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

      if ((provider === "google" || provider === "youtube") && !env.YOUTUBE_CLIENT_ID) {
        return json(400, {
          error: "Google / YouTube OAuth is not configured on the cloud worker (missing YOUTUBE_CLIENT_ID).",
          unconfigured: true
        });
      }
      if (provider === "instagram" && !env.INSTAGRAM_LOGIN_CLIENT_ID) {
        return json(400, {
          error: "Instagram OAuth is not configured on the cloud worker (missing INSTAGRAM_LOGIN_CLIENT_ID).",
          unconfigured: true
        });
      }
      if (provider === "facebook" && !env.FACEBOOK_CLIENT_ID && !env.INSTAGRAM_CLIENT_ID) {
        return json(400, {
          error: "Facebook OAuth is not configured on the cloud worker (missing FACEBOOK_CLIENT_ID).",
          unconfigured: true
        });
      }
      if (provider === "linkedin" && !env.LINKEDIN_CLIENT_ID) {
        return json(400, {
          error: "LinkedIn OAuth is not configured on the cloud worker (missing LINKEDIN_CLIENT_ID).",
          unconfigured: true
        });
      }
      if (provider === "twitter" && !env.TWITTER_CLIENT_ID) {
        return json(400, {
          error: "X (Twitter) OAuth is not configured on the cloud worker (missing TWITTER_CLIENT_ID).",
          unconfigured: true
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

    // Helper: resolve user ID from single-use ticket or Authorization header (never ?token= query auth)
    async function resolveOAuthUser(): Promise<string | null> {
      const ticket = url.searchParams.get("ticket");
      if (ticket) {
        return await consumeOAuthState("ticket:" + ticket);
      }
      const user = await authenticate(env, request);
      return user ? user.userId : null;
    }

    /**
     * Providers report a refused/aborted consent by redirecting back with
     * `error` instead of `code`. Consume the single-use state (the attempt is
     * finished, so it must not be replayable) and show the provider's own
     * reason — otherwise the page claims the code is missing and the real
     * cause is lost.
     */
    async function rejectProviderError(providerLabel: string): Promise<Response> {
      const state = url.searchParams.get("state") ?? "";
      if (state) await consumeOAuthState(state);
      const providerError = url.searchParams.get("error") ?? "";
      const reason = (
        url.searchParams.get("error_description")
        || url.searchParams.get("error_reason")
        || providerError
      ).slice(0, 200);
      console.warn(`OAuth ${providerLabel} consent failed: ${providerError || "unknown"}`);
      return renderOAuthResultHtml(
        "Sign-in Not Completed",
        reason
          ? `${providerLabel} did not grant access: ${reason}`
          : `${providerLabel} did not grant access. Please retry from Dispatch Settings.`,
        true
      );
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
      if (url.searchParams.get("error")) return await rejectProviderError("Google");
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

      let email: string | null = null;
      try {
        const payload = JSON.parse(atob((tok.id_token as string).split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
        email = typeof payload.email === "string" ? payload.email : null;
      } catch { /* id_token optional */ }

      let channelTitle = "YouTube Channel";
      let channelId: string | null = null;
      let avatarUrl: string | null = null;
      try {
        const chRes = await fetch("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", {
          headers: { Authorization: `Bearer ${tok.access_token}` },
        });
        if (chRes.ok) {
          const chData = await chRes.json() as { items?: Array<{ id: string; snippet?: { title?: string; thumbnails?: { default?: { url?: string } } } }> };
          if (chData.items && chData.items.length > 0 && chData.items[0].id) {
            channelId = chData.items[0].id;
            channelTitle = chData.items[0].snippet?.title || channelTitle;
            avatarUrl = chData.items[0].snippet?.thumbnails?.default?.url || null;
          }
        }
      } catch { /* channel fetch error */ }

      if (!channelId) {
        return renderOAuthResultHtml("YouTube Channel Not Found", "Failed to retrieve your YouTube channel identity from Google. Please ensure your Google account has an active YouTube channel.", true);
      }

      const issuedAt = new Date();
      const ytExpiresIn = typeof tok.expires_in === "number" ? tok.expires_in : 3600;
      const ytPayload = {
        refresh_token: tok.refresh_token as string,
        ...(typeof tok.access_token === "string" && tok.access_token ? { access_token: tok.access_token } : {}),
        token_obtained_at: issuedAt.toISOString(),
        token_expires_at: new Date(issuedAt.getTime() + ytExpiresIn * 1000).toISOString(),
      };
      const enc = await encryptToken(
        env.TOKEN_VAULT_KEY,
        JSON.stringify(ytPayload),
        buildVaultAad(uid, "youtube", channelId),
      );

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

      return renderOAuthResultHtml("YouTube Connected", `Channel "${channelTitle}" is connected to Dispatch. You can return to the app.`, false, "youtube");
    }

    // 2. Instagram Start & Callback — Instagram API with Instagram Login.
    // The user signs in with Instagram itself; no Facebook account or Page is
    // involved, so the Facebook app's role/review gating does not apply.
    if (url.pathname === "/oauth/instagram/start" && request.method === "GET") {
      try {
        const uid = await resolveOAuthUser();
        if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization ticket expired or already used. Please retry from Dispatch Settings.", true);
        const state = crypto.randomUUID() + crypto.randomUUID();
        await rpc(env, "cloud_put_oauth_state", { p_state: state, p_user_id: uid });
        const clientId = env.INSTAGRAM_LOGIN_CLIENT_ID || "";
        if (!clientId) {
          return renderOAuthResultHtml("Configuration Required", "Instagram login is not configured on the cloud worker (missing INSTAGRAM_LOGIN_CLIENT_ID).", true, "instagram");
        }
        return Response.redirect(buildInstagramLoginAuthorizeUrl(clientId, "https://dispatch-cloud.resolviaai.workers.dev/oauth/instagram/callback", state), 302);
      } catch (err: any) {
        console.error("Instagram OAuth start error:", err);
        return renderOAuthResultHtml("Connection Error", err?.message || "Failed to start Instagram sign-in. Please retry from Dispatch.", true);
      }
    }

    if (url.pathname === "/oauth/instagram/callback" && request.method === "GET") {
      if (url.searchParams.get("error")) return await rejectProviderError("Instagram");
      const code = url.searchParams.get("code") ?? "";
      const state = url.searchParams.get("state") ?? "";
      if (!code || !state) return renderOAuthResultHtml("Missing Code/State", "Authorization code or state was missing.", true);
      const uid = await consumeOAuthState(state);
      if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization session expired or was already used. Please retry from Dispatch.", true);

      const clientId = env.INSTAGRAM_LOGIN_CLIENT_ID || "";
      const clientSecret = env.INSTAGRAM_LOGIN_CLIENT_SECRET || "";
      if (!clientId || !clientSecret) {
        return renderOAuthResultHtml("Configuration Required", "Instagram login is not configured on the cloud worker.", true, "instagram");
      }
      const redirectUri = "https://dispatch-cloud.resolviaai.workers.dev/oauth/instagram/callback";

      const tokRes = await fetch("https://api.instagram.com/oauth/access_token", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: clientId, client_secret: clientSecret, grant_type: "authorization_code", redirect_uri: redirectUri, code,
        }),
      });
      const tok = await tokRes.json() as { access_token?: string; user_id?: number | string; error_message?: string };
      if (!tokRes.ok || !tok.access_token) {
        return renderOAuthResultHtml("Instagram Auth Failed", `Token exchange failed: ${(tok.error_message || JSON.stringify(tok)).slice(0, 100)}`, true, "instagram");
      }

      // Exchange the short-lived token for the 60-day long-lived token. A worker
      // cron refreshes it every 30 days, so the connection does not lapse.
      let longToken = tok.access_token;
      let expiresIn = 3600;
      try {
        const longRes = await fetch("https://graph.instagram.com/access_token", {
          method: "POST",
          headers: { "content-type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            grant_type: "ig_exchange_token",
            client_secret: clientSecret,
            access_token: longToken,
          }),
        });
        if (longRes.ok) {
          const longData = await longRes.json() as { access_token?: string; expires_in?: number };
          if (longData.access_token) {
            longToken = longData.access_token;
            if (typeof longData.expires_in === "number") expiresIn = longData.expires_in;
          }
        }
      } catch { /* keep the short-lived token; the refresh cron can still extend it */ }

      const meRes = await fetch("https://graph.instagram.com/v21.0/me?fields=user_id,username,account_type,profile_picture_url", {
        headers: { Authorization: `Bearer ${longToken}` },
      });
      const me = await meRes.json() as {
        user_id?: string; id?: string; username?: string; account_type?: string;
        profile_picture_url?: string; error?: { message?: string };
      };
      const igUserId = me.user_id || me.id || (tok.user_id != null ? String(tok.user_id) : "");
      if (!meRes.ok || !igUserId || !me.username) {
        return renderOAuthResultHtml(
          "Instagram Auth Failed",
          `Could not read the Instagram profile: ${(me.error?.message || "unknown error").slice(0, 120)}`,
          true,
          "instagram"
        );
      }

      const issuedAt = new Date();
      const credPayload = {
        access_token: longToken,
        ig_user_id: igUserId,
        username: "@" + me.username,
        account_type: me.account_type || "",
        auth_mode: "instagram_login",
        api_base: "https://graph.instagram.com/v21.0",
        token_obtained_at: issuedAt.toISOString(),
        token_expires_at: new Date(issuedAt.getTime() + expiresIn * 1000).toISOString(),
      };
      const enc = await encryptToken(
        env.TOKEN_VAULT_KEY,
        JSON.stringify(credPayload),
        buildVaultAad(uid, "instagram", igUserId),
      );

      let avatarUrl = me.profile_picture_url || null;
      if (avatarUrl) {
        try {
          const imgRes = await fetch(avatarUrl);
          if (imgRes.ok) {
            const imgBytes = await readResponseBoundedBytes(imgRes, R2_MAX_CHUNK_BYTES);
            const avatarKey = `avatars/instagram_${igUserId}.jpg`;
            await env.BUCKET.put(avatarKey, imgBytes, {
              httpMetadata: { contentType: "image/jpeg" },
            });
            avatarUrl = `${url.origin}/avatars/instagram_${igUserId}.jpg`;
          }
        } catch (e) {
          console.warn("Failed to cache Instagram avatar to R2:", e);
        }
      }

      const storeRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_connected_account`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({
          p_user_id: uid, p_provider: "instagram", p_account_id: igUserId, p_account_name: "@" + me.username,
          p_account_email: null, p_avatar_url: avatarUrl, p_credentials_encrypted: enc,
          p_metadata: { ig_user_id: igUserId, username: "@" + me.username, auth_mode: "instagram_login", account_type: me.account_type || "" },
        }),
      });
      if (!storeRes.ok) {
        const errText = await storeRes.text().catch(() => "");
        console.error("Failed to store Instagram account:", storeRes.status, errText);
        return renderOAuthResultHtml("Database Error", `Failed to save Instagram account: ${errText.slice(0, 150)}`, true, "instagram");
      }

      return renderOAuthResultHtml("Instagram Connected", `Account "@${me.username}" connected successfully! You can return to Dispatch.`, false, "instagram");
    }

    // 3. Facebook Start & Callback
    if (url.pathname === "/oauth/facebook/start" && request.method === "GET") {
      try {
        const uid = await resolveOAuthUser();
        if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization ticket expired or already used. Please retry from Dispatch Settings.", true);
        const state = crypto.randomUUID() + crypto.randomUUID();
        await rpc(env, "cloud_put_oauth_state", { p_state: state, p_user_id: uid });
        const clientId = env.FACEBOOK_CLIENT_ID || env.INSTAGRAM_CLIENT_ID || "";
        if (!clientId) {
          return renderOAuthResultHtml("Configuration Required", "Facebook Client ID is not configured on the cloud worker.", true, "facebook");
        }
        return Response.redirect(buildFacebookAuthorizeUrl(clientId, "https://dispatch-cloud.resolviaai.workers.dev/oauth/facebook/callback", state), 302);
      } catch (err: any) {
        console.error("Facebook OAuth start error:", err);
        return renderOAuthResultHtml("Connection Error", err?.message || "Failed to start Facebook sign-in. Please retry from Dispatch.", true);
      }
    }

    if (url.pathname === "/oauth/facebook/callback" && request.method === "GET") {
      if (url.searchParams.get("error")) return await rejectProviderError("Facebook");
      const code = url.searchParams.get("code") ?? "";
      const state = url.searchParams.get("state") ?? "";
      if (!code || !state) return renderOAuthResultHtml("Missing Code/State", "Authorization code or state was missing.", true);
      const uid = await consumeOAuthState(state);
      if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization session expired or was already used. Please retry from Dispatch.", true);

      const clientId = env.FACEBOOK_CLIENT_ID || env.INSTAGRAM_CLIENT_ID || "";
      const clientSecret = env.FACEBOOK_CLIENT_SECRET || env.INSTAGRAM_CLIENT_SECRET || "";
      const redirectUri = "https://dispatch-cloud.resolviaai.workers.dev/oauth/facebook/callback";

      const tokRes = await fetch("https://graph.facebook.com/v21.0/oauth/access_token", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: clientId, client_secret: clientSecret, redirect_uri: redirectUri, code,
        }),
      });
      const tok = await tokRes.json() as Record<string, unknown>;
      if (!tokRes.ok || !tok.access_token) {
        return renderOAuthResultHtml("Facebook Auth Failed", `Token exchange failed: ${JSON.stringify(tok).slice(0, 100)}`, true);
      }

      let longToken = tok.access_token as string;
      let fbExpiresIn = typeof tok.expires_in === "number" ? tok.expires_in : 5184000;
      try {
        const longRes = await fetch("https://graph.facebook.com/v21.0/oauth/access_token", {
          method: "POST",
          headers: { "content-type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            grant_type: "fb_exchange_token", client_id: clientId, client_secret: clientSecret, fb_exchange_token: longToken,
          }),
        });
        if (longRes.ok) {
          const longData = await longRes.json() as Record<string, unknown>;
          if (longData.access_token) longToken = longData.access_token as string;
          if (typeof longData.expires_in === "number") fbExpiresIn = longData.expires_in;
        }
      } catch { /* fallback */ }

      // Dispatch publishes Reels to Facebook Pages, so Pages (with their Page
      // tokens) are mandatory. Store ALL admined Pages instead of blindly picking data[0].
      const pages: Array<{ id: string; title: string; accessToken: string | null; avatar: string | null }> = [];
      let pagesReadable = true;
      try {
        const pagesRes = await fetch("https://graph.facebook.com/v21.0/me/accounts?fields=id,name,access_token,picture{url}", {
          headers: { Authorization: `Bearer ${longToken}` },
        });
        pagesReadable = pagesRes.ok;
        if (pagesRes.ok) {
          const pagesData = await pagesRes.json() as {
            data?: Array<{
              id: string;
              name: string;
              access_token?: string;
              picture?: { data?: { url?: string } };
            }>;
          };
          if (Array.isArray(pagesData.data)) {
            for (const row of pagesData.data) {
              if (!row?.id || !row?.name) continue;
              pages.push({
                id: row.id,
                title: row.name,
                accessToken: row.access_token || null,
                avatar: row.picture?.data?.url || null,
              });
            }
          }
        }
      } catch (err) {
        pagesReadable = false;
        console.error("Facebook page lookup failed:", err instanceof Error ? err.message : "unknown error");
      }

      if (!pagesReadable) {
        return renderOAuthResultHtml("Facebook Unavailable", "Facebook did not return your Pages. Please retry in a moment.", true, "facebook");
      }
      if (pages.length === 0) {
        return renderOAuthResultHtml(
          "Facebook Page Required",
          "No Facebook Page is available for this login. Dispatch publishes Reels to a Page — make sure you admin a Page, then retry.",
          true,
          "facebook"
        );
      }

      const issuedAt = new Date();
      const tokenExpiresAtIso = new Date(issuedAt.getTime() + fbExpiresIn * 1000).toISOString();
      const storedNames: string[] = [];
      let lastDbError = "";

      for (const page of pages) {
        const credPayload = {
          access_token: page.accessToken || longToken,
          user_access_token: longToken,
          page_id: page.id,
          account_id: page.id,
          token_obtained_at: issuedAt.toISOString(),
          token_expires_at: tokenExpiresAtIso,
        };
        const enc = await encryptToken(
          env.TOKEN_VAULT_KEY,
          JSON.stringify(credPayload),
          buildVaultAad(uid, "facebook", page.id),
        );

        const storeRes = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_store_connected_account`, {
          method: "POST",
          headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
          body: JSON.stringify({
            p_user_id: uid, p_provider: "facebook", p_account_id: page.id, p_account_name: page.title,
            p_account_email: null, p_avatar_url: page.avatar, p_credentials_encrypted: enc,
            p_metadata: { page_id: page.id, title: page.title },
          }),
        });
        if (!storeRes.ok) {
          const errText = await storeRes.text().catch(() => "");
          lastDbError = errText;
          console.error("Failed to store Facebook page:", page.id, storeRes.status, errText);
          continue;
        }
        storedNames.push(page.title);
      }

      if (storedNames.length === 0) {
        return renderOAuthResultHtml("Database Error", `Failed to save Facebook account: ${lastDbError.slice(0, 150)}`, true);
      }

      const summary = storedNames.length === 1
        ? `Page "${storedNames[0]}" connected successfully! You can return to Dispatch.`
        : `${storedNames.length} Facebook Pages (${storedNames.slice(0, 3).join(", ")}${storedNames.length > 3 ? "…" : ""}) connected successfully! You can return to Dispatch.`;
      return renderOAuthResultHtml("Facebook Connected", summary, false, "facebook");
    }

    // 4. LinkedIn Start & Callback
    if (url.pathname === "/oauth/linkedin/start" && request.method === "GET") {
      try {
        const uid = await resolveOAuthUser();
        if (!uid) return renderOAuthResultHtml("Session Expired", "Authorization ticket expired or already used. Please retry from Dispatch Settings.", true);
        const state = crypto.randomUUID() + crypto.randomUUID();
        await rpc(env, "cloud_put_oauth_state", { p_state: state, p_user_id: uid });
        const clientId = env.LINKEDIN_CLIENT_ID || "";
        if (!clientId) {
          return renderOAuthResultHtml("Configuration Required", "LinkedIn Client ID is not configured on the cloud worker.", true, "linkedin");
        }
        return Response.redirect(buildLinkedInAuthorizeUrl(clientId, "https://dispatch-cloud.resolviaai.workers.dev/oauth/linkedin/callback", state), 302);
      } catch (err: any) {
        console.error("LinkedIn OAuth start error:", err);
        return renderOAuthResultHtml("Connection Error", err?.message || "Failed to start LinkedIn sign-in. Please retry from Dispatch.", true);
      }
    }

    if (url.pathname === "/oauth/linkedin/callback" && request.method === "GET") {
      if (url.searchParams.get("error")) return await rejectProviderError("LinkedIn");
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

      const liIssuedAt = new Date();
      const liExpiresIn = typeof tok.expires_in === "number" ? tok.expires_in : 5184000;
      const credPayload = {
        access_token: tok.access_token,
        refresh_token: tok.refresh_token ?? null,
        author_urn: authorUrn,
        account_id: authorUrn,
        token_obtained_at: liIssuedAt.toISOString(),
        token_expires_at: new Date(liIssuedAt.getTime() + liExpiresIn * 1000).toISOString(),
      };
      const enc = await encryptToken(
        env.TOKEN_VAULT_KEY,
        JSON.stringify(credPayload),
        buildVaultAad(uid, "linkedin", authorUrn),
      );

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

      return renderOAuthResultHtml("LinkedIn Connected", `Profile "${profileName}" connected successfully! You can return to Dispatch.`, false, "linkedin");
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
        const encryptedState = await encryptToken(
          env.TOKEN_VAULT_KEY,
          `${rawState}:::${codeVerifier}`,
          "oauth_pkce|twitter|state",
        );
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
      if (url.searchParams.get("error")) return await rejectProviderError("X (Twitter)");
      const code = url.searchParams.get("code") ?? "";
      const stateParam = url.searchParams.get("state") ?? "";
      if (!code || !stateParam) return renderOAuthResultHtml("Missing Code/State", "Authorization code or state was missing.", true);

      let rawState = "";
      let codeVerifier = "";
      try {
        const dec = await decryptTokenWithMigration(
          env.TOKEN_VAULT_KEY,
          stateParam,
          "oauth_pkce|twitter|state",
        );
        const parts = dec.plaintext.split(":::");
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

      const twIssuedAt = new Date();
      const twExpiresIn = typeof tok.expires_in === "number" ? tok.expires_in : 7200;
      const credPayload = {
        access_token: tok.access_token,
        refresh_token: tok.refresh_token ?? null,
        bearer_token: tok.access_token,
        account_id: twitterId,
        username: twitterHandle,
        token_obtained_at: twIssuedAt.toISOString(),
        token_expires_at: new Date(twIssuedAt.getTime() + twExpiresIn * 1000).toISOString(),
      };
      const enc = await encryptToken(
        env.TOKEN_VAULT_KEY,
        JSON.stringify(credPayload),
        buildVaultAad(uid, "twitter", twitterId),
      );

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

      return renderOAuthResultHtml("X (Twitter) Connected", `Account "${twitterHandle}" connected successfully! You can return to Dispatch.`, false, "twitter");
    }

    // ---- Remote processing jobs (authenticated app clients, any network) ----
    if (url.pathname === "/jobs/submit" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const body = await request.json().catch(() => ({})) as {
        url?: string;
        source_type?: string;
        session_id?: string | null;
      };
      const ytUrl = (body.url ?? "").trim();
      if (!/^https:\/\/(www\.)?(youtube\.com\/(watch\?v=|shorts\/)|youtu\.be\/)[A-Za-z0-9_-]{6,20}/.test(ytUrl))
        return json(400, { error: "invalid YouTube URL" });
      const rawSourceType = typeof body.source_type === "string" ? body.source_type.trim() : "";
      const pSourceType = ["camera", "youtube_import", "direct_upload"].includes(rawSourceType)
        ? rawSourceType
        : "youtube_import";
      const rawSessionId = typeof body.session_id === "string" ? body.session_id.trim().slice(0, 128) : "";
      const pSessionId = rawSessionId || null;
      const sub = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cloud_submit_job`, {
        method: "POST",
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({
          p_user_id: user.userId,
          p_url: ytUrl,
          p_source_type: pSourceType,
          p_session_id: pSessionId,
        }),
      });
      if (!sub.ok) return json(502, { error: `submit failed: ${sub.status}` });
      const subBody = await sub.json() as unknown;
      const arr = Array.isArray(subBody) ? subBody as Array<{ id: string; status: string }> : [];
      if (arr[0]?.id) {
        const jobId = arr[0].id;
        const vIdMatch = ytUrl.match(/(?:v=|\/shorts\/|\/embed\/|youtu\.be\/)([A-Za-z0-9_-]{6,20})/);
        const vId = vIdMatch ? vIdMatch[1] : null;
        if (vId) {
          const probeTask = (async () => {
            const [dur, title] = await Promise.all([
              probeYouTubeDuration(vId),
              probeYouTubeTitle(vId),
            ]);
            const patchObj: Record<string, unknown> = {};
            if (dur && dur > 0) patchObj.duration = dur;
            if (title) patchObj.source_title = title;
            if (Object.keys(patchObj).length > 0) {
              await fetch(`${env.SUPABASE_URL}/rest/v1/processing_jobs?id=eq.${jobId}`, {
                method: "PATCH",
                headers: {
                  apikey: env.SUPABASE_SERVICE_ROLE_KEY,
                  Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
                  "Content-Type": "application/json",
                },
                body: JSON.stringify(patchObj),
              }).catch(() => null);
            }
          })().catch(() => null);
          ctx?.waitUntil(probeTask);
        }
      }
      return json(200, {
        job_id: arr[0]?.id ?? null,
        status: arr[0]?.status ?? "queued",
        source_type: pSourceType,
        session_id: pSessionId,
      });
    }
    if (url.pathname === "/template/default") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });

      if (request.method === "GET") {
        const res = await fetch(
          `${env.SUPABASE_URL}/rest/v1/profiles?user_id=eq.${user.userId}&select=default_template`,
          { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
        );
        if (!res.ok) return json(502, { error: "failed to fetch template" });
        const rows = await res.json() as Array<{ default_template?: unknown }>;
        const tpl = rows[0]?.default_template || null;
        return json(200, { template: tpl });
      }

      if (request.method === "POST") {
        const body = await readJson<Record<string, unknown>>(request);
        const patchRes = await fetch(
          `${env.SUPABASE_URL}/rest/v1/profiles?user_id=eq.${user.userId}`,
          {
            method: "PATCH",
            headers: {
              apikey: env.SUPABASE_SERVICE_ROLE_KEY,
              Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ default_template: body }),
          }
        );
        if (!patchRes.ok) return json(502, { error: "failed to save template" });
        return json(200, { status: "success", template: body });
      }
    }

    if (url.pathname === "/jobs/status" && request.method === "GET") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const res = await fetch(
        `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=eq.${user.userId}&status=neq.cancelled&select=id,youtube_url,status,stage,video_id,last_error,created_at,source,upload_object_id,source_title,duration,session_id&order=created_at.desc&limit=100`,
        { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } });
      if (!res.ok) return json(502, { error: `status failed: ${res.status}` });
      return json(200, { jobs: await res.json() });
    }
    if (url.pathname === "/jobs/cancel" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const body = await request.json().catch(() => ({})) as { video_id?: string; url?: string; media_object_id?: string };
      const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
      // Direct jobs: cancel by the uploaded object they reference. The PATCH
      // re-asserts status=queued + source=direct so a job already claimed by the
      // PC is left alone instead of corrupting an in-flight download.
      if (typeof body.media_object_id === "string" && UUID_RE.test(body.media_object_id)) {
        const patchRes = await fetch(
          `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=eq.${user.userId}&status=eq.queued&source=eq.direct&upload_object_id=eq.${body.media_object_id}`,
          { method: "PATCH", headers: { ...svc, "Content-Type": "application/json", Prefer: "return=representation" }, body: JSON.stringify({ status: "cancelled" }) });
        if (!patchRes.ok) return json(502, { error: `cancel failed: ${patchRes.status}` });
        const patched = await patchRes.json() as unknown;
        const cancelled = Array.isArray(patched) ? (patched as Array<unknown>).length : 0;
        return json(200, { cancelled });
      }
      let videoId = typeof body.video_id === "string" ? body.video_id.trim() : "";
      if (!videoId && typeof body.url === "string") {
        const m = body.url.match(/(?:v=|\/shorts\/|\/embed\/|youtu\.be\/)([A-Za-z0-9_-]{11})(?:[?&#/]|$)/);
        if (m) videoId = m[1];
      }
      if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) return json(400, { error: "invalid video id" });
      // Fetch this user's queued rows first and match canonically, so the cancel
      // can never hit another user's job and cannot wildcard over unrelated URLs.
      const listRes = await fetch(
        `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=eq.${user.userId}&status=eq.queued&select=id,youtube_url`,
        { headers: svc });
      if (!listRes.ok) return json(502, { error: `cancel lookup failed: ${listRes.status}` });
      const rows = await listRes.json() as Array<{ id: string; youtube_url: string }>;
      const eid = videoId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const urlRe = new RegExp(`(?:[?&]v=|/shorts/|/embed/|youtu\\.be/)${eid}([?&#/]|$)`);
      const matchingIds = rows
        .filter(r => urlRe.test(String(r.youtube_url ?? "")))
        .map(r => r.id);
      if (matchingIds.length === 0) return json(200, { cancelled: 0 });
      // The PATCH re-asserts status=queued so a job claimed by the PC between
      // the read and the write is left alone instead of corrupting an in-flight run.
      const patchRes = await fetch(
        `${env.SUPABASE_URL}/rest/v1/processing_jobs?id=in.(${matchingIds.join(",")})&user_id=eq.${user.userId}&status=eq.queued`,
        { method: "PATCH", headers: { ...svc, "Content-Type": "application/json", Prefer: "return=representation" }, body: JSON.stringify({ status: "cancelled" }) });
      if (!patchRes.ok) return json(502, { error: `cancel failed: ${patchRes.status}` });
      const patched = await patchRes.json() as unknown;
      const cancelled = Array.isArray(patched) ? (patched as Array<unknown>).length : 0;
      return json(200, { cancelled });
    }

    if (url.pathname === "/sessions/delete" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const body = await request.json().catch(() => ({})) as { session_id?: string; video_id?: string; dispatch_id?: string; job_id?: string };
      const rawCandidates = [body.video_id, body.session_id, body.dispatch_id, body.job_id]
        .map(s => typeof s === "string" ? s.trim() : "")
        .filter(Boolean);
      if (rawCandidates.length === 0) return json(400, { error: "missing id" });
      const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };

      const conditions: string[] = [];
      for (const rawId of rawCandidates) {
        conditions.push(`id.eq.${encodeURIComponent(rawId)}`);
        conditions.push(`video_id.eq.${encodeURIComponent(rawId)}`);
        conditions.push(`session_id.eq.${encodeURIComponent(rawId)}`);
        conditions.push(`upload_object_id.eq.${encodeURIComponent(rawId)}`);
        if (rawId.length >= 6) {
          conditions.push(`youtube_url.ilike.*${encodeURIComponent(rawId)}*`);
        }
      }
      const query = `user_id=eq.${user.userId}&or=(${conditions.join(",")})`;
      const delJobRes = await fetch(`${env.SUPABASE_URL}/rest/v1/processing_jobs?${query}`, {
        method: "DELETE",
        headers: svc,
      });

      const clipConditions = rawCandidates.flatMap(id => [
        `session_id.eq.${encodeURIComponent(id)}`,
        `youtube_video_id.eq.${encodeURIComponent(id)}`
      ]);
      await fetch(
        `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${user.userId}&or=(${clipConditions.join(",")})`,
        { method: "DELETE", headers: svc }
      ).catch(() => null);

      return json(200, { ok: true, deleted: delJobRes.ok });
    }

    // ---- Direct video uploads (Route C): submit a staged clip_video object for
    // processing. The row is created only after /complete, so a queued direct job
    // always references a fully-uploaded, quota-accounted R2 object.
    if (url.pathname === "/jobs/direct" && request.method === "POST") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const body = await readJson<{ media_object_id?: string; title?: string }>(request);
      if (!body?.media_object_id || !UUID_RE.test(body.media_object_id)) {
        return json(400, { error: "media_object_id required" });
      }
      const title = typeof body.title === "string" && body.title.trim() ? body.title.trim().slice(0, 200) : null;
      try {
        const row = await rpc<{ id: string; status: string }>(env, "cloud_submit_direct_job", {
          p_user_id: user.userId,
          p_media_object_id: body.media_object_id,
          p_title: title,
        });
        if (!row?.id) return json(502, { error: "submit returned no job" });
        return json(200, { job_id: row.id, status: row.status ?? "queued", source: "direct" });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        const status = msg.includes("SUBMIT_DIRECT: unknown") ? 404 : 502;
        return json(status, { error: msg });
      }
    }

    const CLIPS_CLOUD_HYDRATE_SELECT =
      "id,local_clip_id,title,status,start_time,end_time,ai_suggested_start_time,ai_suggested_end_time,range_revision,render_stale,caption_data,caption_revision,cloud_updated_at,reject_requested_at,reject_purge_after,source_orientation,source_aspect_ratio,layout_mode,available_layout_modes,session_id,youtube_video_id,project_document,segments_revision,active_state_revision,kept_duration,can_undo,can_redo,media_fingerprint,render_fingerprint";

    async function hydrateCloudClipBatch(
      userIdForHydrate: string,
      rawClips: Array<Record<string, unknown>>,
    ) {
      const clipIdCandidates = Array.from(
        new Set(
          rawClips.flatMap((c) => [String(c.local_clip_id || ""), String(c.id || "")]).filter(Boolean),
        ),
      );
      const localClipIds = postgrestInList(clipIdCandidates);
      const cloudClipIds = postgrestInList(rawClips.map((c) => String(c.id)));
      let jobsRes: Response | null = null;
      let mediaRes: Response | null = null;
      let editWindowsRes: Response | null = null;
      let publicationsRes: Response | null = null;
      if (rawClips.length > 0) {
        const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
        const pubFilter = localClipIds
          ? `or=(clip_id.in.(${cloudClipIds}),local_clip_id.in.(${localClipIds}))`
          : `clip_id=in.(${cloudClipIds})`;
        [jobsRes, mediaRes, editWindowsRes, publicationsRes] = await Promise.all([
          fetch(
            `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=eq.${userIdForHydrate}&select=id,status,video_id,session_id,source_title&order=created_at.desc&limit=100`,
            { headers: svc },
          ).catch(() => null),
          fetch(
            `${env.SUPABASE_URL}/rest/v1/media_objects?user_id=eq.${userIdForHydrate}&status=eq.active&clip_id=in.(${cloudClipIds})&select=id,clip_id,kind,mime_type,final_key&order=created_at.desc,id.desc`,
            { headers: svc },
          ).catch(() => null),
          fetch(
            `${env.SUPABASE_URL}/rest/v1/edit_windows?user_id=eq.${userIdForHydrate}&status=eq.ready&local_clip_id=in.(${localClipIds})&select=id,local_clip_id,window_start,window_end,media_object_id,transcript_status&order=created_at.desc`,
            { headers: svc },
          ).catch(() => null),
          fetch(
            `${env.SUPABASE_URL}/rest/v1/publications?user_id=eq.${userIdForHydrate}&${pubFilter}&status=eq.published&select=id,clip_id,local_clip_id,platform,account_id,remote_id,remote_url,published_at,publish_mode,view_count,like_count,comment_count,stats_updated_at,stats_checked_at&order=published_at.asc`,
            { headers: svc },
          ).catch(() => null),
        ]);
      }
      const mediaDegraded = !mediaRes || !mediaRes.ok;
      const rawJobs = jobsRes && jobsRes.ok ? await jobsRes.json() : [];
      const userJobs = Array.isArray(rawJobs) ? (rawJobs as Array<Record<string, unknown>>) : [];
      const windowsDegraded = !editWindowsRes || !editWindowsRes.ok;
      const publicationsDegraded = !publicationsRes || !publicationsRes.ok;
      const rawMedia = mediaRes && mediaRes.ok ? await mediaRes.json() : [];
      const activeMedia = Array.isArray(rawMedia) ? (rawMedia as Array<Record<string, unknown>>) : [];
      const rawEw = editWindowsRes && editWindowsRes.ok ? await editWindowsRes.json() : [];
      const readyWindows = Array.isArray(rawEw) ? (rawEw as Array<Record<string, unknown>>) : [];
      const rawPublications = publicationsRes && publicationsRes.ok ? await publicationsRes.json() : [];
      const publicationsByClip = new Map<string, CloudPublicationRow[]>();
      for (const publication of (Array.isArray(rawPublications) ? rawPublications as CloudPublicationRow[] : [])) {
        for (const key of [String(publication.clip_id ?? ""), String(publication.local_clip_id ?? "")]) {
          if (!key) continue;
          const rows = publicationsByClip.get(key) ?? [];
          if (!rows.some((r) => r.id === publication.id)) {
            rows.push(publication);
          }
          publicationsByClip.set(key, rows);
        }
      }

      const transcriptMap = new Map<string, Array<Record<string, unknown>>>();
      if (readyWindows.length > 0) {
        const windowIds = readyWindows.map((w) => String(w.id)).slice(0, 100);
        if (windowIds.length > 0) {
          try {
            const trRes = await fetch(
              `${env.SUPABASE_URL}/rest/v1/window_transcripts?window_id=in.(${windowIds.map((id) => `"${id}"`).join(",")})&select=window_id,words`,
              { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } },
            );
            if (trRes.ok) {
              const rows = await trRes.json() as Array<{ window_id: string; words: unknown }>;
              for (const row of rows) {
                const words = Array.isArray(row?.words) ? (row.words as Array<Record<string, unknown>>) : [];
                if (words.length > 0) transcriptMap.set(String(row.window_id), words);
              }
            }
          } catch {}
        }
      }

      const buildCloudCaptions = (clip: Record<string, unknown>, clipEw: Record<string, unknown> | null): { captions: Record<string, unknown> | undefined; source: string | undefined; ready: boolean } => {
        const savedCaption = clip.caption_data;
        if (savedCaption && typeof savedCaption === "object" && !Array.isArray(savedCaption)) {
          const savedGroups = (savedCaption as Record<string, unknown>).groups;
          if (Array.isArray(savedGroups) && savedGroups.length > 0) {
            return { captions: savedCaption as Record<string, unknown>, source: "edited", ready: true };
          }
        }
        if (!clipEw) return { captions: undefined, source: undefined, ready: false };
        const words = transcriptMap.get(String(clipEw.id));
        if (!words || words.length === 0) return { captions: undefined, source: undefined, ready: false };
        const windowStart = Number(clipEw.window_start || 0);
        const windowEnd = Number(clipEw.window_end || 0);
        const startWindow = windowEnd > windowStart ? windowStart : 0;
        const relative = words
          .map((word) => ({
            w: String(word.word ?? word.w ?? word.text ?? ""),
            start: Number(word.start) - startWindow,
            end: Number(word.end) - startWindow,
          }))
          .filter((word) => Number.isFinite(word.start) && Number.isFinite(word.end) && word.end > 0);
        if (relative.length === 0) return { captions: undefined, source: undefined, ready: false };
        const clipEnd = Number(clip.end_time || 0);
        const clipStart = Number(clip.start_time || 0);
        const spanDuration = Math.max(0.1, windowEnd - startWindow, clipEnd - clipStart);
        return {
          captions: {
            words: relative.filter((word) => word.start < spanDuration),
            span_duration: spanDuration,
          },
          source: "window_seed",
          ready: true,
        };
      };

      const mapClipItem = async (c: Record<string, unknown>, targetStatus: string) => {
        const localId = String(c.local_clip_id || c.id);
        const cloudClipId = String(c.id);
        const matchingJob = userJobs.find((j) =>
          (c.youtube_video_id && (j.video_id === c.youtube_video_id || String(j.youtube_url || "").includes(String(c.youtube_video_id)))) ||
          (c.session_id && j.session_id === c.session_id),
        );
        const clipEw = readyWindows.find(
          (w) => String(w.local_clip_id) === localId || String(w.local_clip_id) === cloudClipId,
        ) ?? null;
        const ewMo = clipEw
          ? activeMedia.find((m) => String(m.id) === String(clipEw.media_object_id)) ?? null
          : null;
        const renderedMo = activeMedia.find((m) => String(m.clip_id) === cloudClipId && m.kind === "clip_video") ?? null;
        const thumbMo = activeMedia.find((m) => String(m.clip_id) === cloudClipId && m.kind === "thumbnail") ?? null;

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

        const { captions, source, ready: captionsReady } = buildCloudCaptions(c, clipEw);
        const hasVideo = Boolean(videoUrl || editWindowUrl);
        const ewStartRaw = clipEw?.window_start;
        const ewEndRaw = clipEw?.window_end;
        const editWindowStart = ewStartRaw != null && Number.isFinite(Number(ewStartRaw)) ? Number(ewStartRaw) : undefined;
        const editWindowEnd = ewEndRaw != null && Number.isFinite(Number(ewEndRaw)) ? Number(ewEndRaw) : undefined;
        const clipPubs = publicationsByClip.get(cloudClipId) ?? publicationsByClip.get(localId) ?? [];

        return {
          id: localId,
          cloud_id: c.id,
          session_id: c.session_id,
          youtube_video_id: c.youtube_video_id as string | undefined,
          source_title: (matchingJob?.source_title as string | undefined) || undefined,
          title: c.title,
          status: targetStatus,
          start_time: Number(c.start_time || 0),
          end_time: Number(c.end_time || 0),
          ai_suggested_start_time: Number(c.ai_suggested_start_time || c.start_time || 0),
          ai_suggested_end_time: Number(c.ai_suggested_end_time || c.end_time || 0),
          duration: (c.kept_duration != null && Number(c.kept_duration) > 0) ? Number(c.kept_duration) : Math.max(0, Number(c.end_time || 0) - Number(c.start_time || 0)),
          kept_duration: c.kept_duration != null ? Number(c.kept_duration) : undefined,
          project_document: c.project_document as Record<string, unknown> | undefined,
          segments_revision: Number(c.segments_revision || 0),
          active_state_revision: Number(c.active_state_revision || 0),
          can_undo: Boolean(c.can_undo),
          can_redo: Boolean(c.can_redo),
          media_fingerprint: c.media_fingerprint as string | undefined,
          render_fingerprint: c.render_fingerprint as string | undefined,
          range_revision: Number(c.range_revision || 0),
          is_render_stale: Boolean(c.render_stale),
          source_orientation: (c.source_orientation as string) || "landscape",
          source_aspect_ratio: (c.source_aspect_ratio as string) || "16:9",
          layout_mode: (c.layout_mode as string) || "fit_blur",
          available_layout_modes: c.available_layout_modes as unknown[],
          created_at: c.cloud_updated_at,
          video_url: videoUrl,
          source_video_url: undefined,
          thumbnail_url: thumbUrl,
          edit_window_url: editWindowUrl,
          edit_window_start: editWindowStart,
          edit_window_end: editWindowEnd,
          publications: clipPubs.map(publicationPayload),
          published_at: (c.published_at as string) || ((clipPubs.find((p) => p.published_at)?.published_at) as string | undefined),
          captions,
          captions_source: source,
          captions_ready: captionsReady,
          captions_revision: Number(c.caption_revision || 0),
          media_missing: !mediaDegraded && !hasVideo ? true : undefined,
          is_cloud: true,
        };
      };

      return {
        mapClipItem,
        userJobs,
        mediaDegraded,
        windowsDegraded,
        publicationsDegraded,
        jobsDegraded: !jobsRes || !jobsRes.ok,
      };
    }

    // ---- Cloud Clips (authenticated clients on any network / cellular) ----
    if (url.pathname === "/clips" && request.method === "GET") {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });

      const limitRaw = Number(url.searchParams.get("limit") ?? 50);
      const offsetRaw = Number(url.searchParams.get("offset") ?? 0);
      const limit = Math.min(Math.max(Number.isFinite(limitRaw) ? Math.floor(limitRaw) : 50, 1), 200);
      const offset = Math.min(Math.max(Number.isFinite(offsetRaw) ? Math.floor(offsetRaw) : 0, 0), 100000);

      const clipsRes = await fetch(
        `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${user.userId}&select=${CLIPS_CLOUD_HYDRATE_SELECT}&order=cloud_updated_at.desc&limit=${limit}&offset=${offset}`,
        { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
      );
      if (!clipsRes.ok) {
        const text = await clipsRes.text().catch(() => "");
        return json(502, { error: `clip listing failed: HTTP ${clipsRes.status}`, detail: text.slice(0, 200) });
      }
      const parsedClips = await clipsRes.json() as unknown;
      if (!Array.isArray(parsedClips)) {
        return json(502, { error: "clip listing failed: unexpected response shape" });
      }
      const rawClips = parsedClips as Array<Record<string, unknown>>;

      const {
        mapClipItem,
        userJobs,
        mediaDegraded,
        windowsDegraded,
        publicationsDegraded,
        jobsDegraded,
      } = await hydrateCloudClipBatch(user.userId, rawClips);

      const tombstoned = new Set<string>();
      try {
        const cmdRes = await rpcRaw(env, "cloud_list_live_clip_commands", { p_user_id: user.userId });
        if (Array.isArray(cmdRes)) {
          for (const cmd of cmdRes as Array<{ local_clip_id: string; kind: string }>) {
            if (cmd?.kind === "reject" && cmd?.local_clip_id) tombstoned.add(String(cmd.local_clip_id));
          }
        }
      } catch {}

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

      const nowMs = Date.now();
      const rejected = rawClips
        .filter(c => c.status === "rejected")
        .map(c => {
          const purgeAfter = typeof c.reject_purge_after === "string" ? c.reject_purge_after : null;
          const purgeMs = purgeAfter ? Date.parse(purgeAfter) : NaN;
          return {
            id: String(c.local_clip_id || c.id),
            cloud_id: String(c.id),
            title: typeof c.title === "string" ? c.title : "Rejected clip",
            status: "rejected",
            rejected_at: c.reject_requested_at ?? null,
            purge_after: purgeAfter,
            retention_expired: !Number.isFinite(purgeMs) || purgeMs <= nowMs,
            reason: null,
            is_cloud: true,
          };
        })
        .filter(c => !c.retention_expired);

      const queuedJobs = userJobs.filter(j => !j.status || j.status === "queued");
      return json(200, {
        ready,
        recent_approved,
        rejected,
        queue_stats: { queued_count: Array.isArray(queuedJobs) ? queuedJobs.length : 0, daily_quota_used: 0, daily_quota_max: 10 },
        cloud_mode: true,
        media_degraded: mediaDegraded ? true : undefined,
        windows_degraded: windowsDegraded ? true : undefined,
        jobs_degraded: jobsDegraded ? true : undefined,
        publications_degraded: publicationsDegraded ? true : undefined,
      });
    }

    const singleClipMatch = url.pathname.match(/^\/clips\/([^/]+)$/);
    if (request.method === "GET" && singleClipMatch) {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const clipRef = decodeURIComponent(singleClipMatch[1]).trim();
      if (!clipRef || clipRef.length > 128) return json(400, { error: "invalid clip id" });

      const lookupUrl = new URL(`${env.SUPABASE_URL}/rest/v1/clips_cloud`);
      lookupUrl.searchParams.set("user_id", `eq.${user.userId}`);
      if (UUID_RE.test(clipRef)) {
        lookupUrl.searchParams.set("or", `(id.eq.${clipRef},local_clip_id.eq.${clipRef})`);
      } else {
        lookupUrl.searchParams.set("local_clip_id", `eq.${clipRef}`);
      }
      lookupUrl.searchParams.set("select", CLIPS_CLOUD_HYDRATE_SELECT);
      lookupUrl.searchParams.set("limit", "1");

      const clipRes = await fetch(lookupUrl.toString(), {
        headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` },
      });
      if (!clipRes.ok) {
        return json(502, { error: `clip lookup failed: HTTP ${clipRes.status}` });
      }
      const rows = (await clipRes.json().catch(() => [])) as Array<Record<string, unknown>>;
      if (!Array.isArray(rows) || rows.length === 0) {
        return json(404, { error: "clip not found" });
      }
      const row = rows[0];
      const { mapClipItem } = await hydrateCloudClipBatch(user.userId, [row]);
      const normalizedStatus = (row.status === "ready_review" || row.status === "ready") ? "ready" : String(row.status || "ready");
      const hydrated = await mapClipItem(row, normalizedStatus);
      return json(200, { ...hydrated, cloud_mode: true });
    }

    const clipDeleteMatch = url.pathname.match(/^\/clips\/([^/]+)\/(?:delete|purge)$/);
    if ((request.method === "POST" || request.method === "DELETE") && clipDeleteMatch) {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const clipId = decodeURIComponent(clipDeleteMatch[1]);
      const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
      const delRes = await fetch(
        `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${user.userId}&or=(id.eq.${encodeURIComponent(clipId)},local_clip_id.eq.${encodeURIComponent(clipId)})`,
        { method: "DELETE", headers: svc }
      );
      return json(200, { ok: true, deleted: delRes.ok });
    }

    const cloudPublicationsMatch = url.pathname.match(/^\/clips\/([^/]+)\/publications$/);
    if (request.method === "GET" && cloudPublicationsMatch) {
      const user = await authenticate(env, request);
      if (!user) return json(401, { error: "unauthorized" });
      const cloudClipId = decodeURIComponent(cloudPublicationsMatch[1]);
      if (!UUID_RE.test(cloudClipId)) return json(400, { error: "invalid clip id" });

      const svcHeaders = {
        apikey: env.SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      };
      const response = await fetch(
        `${env.SUPABASE_URL}/rest/v1/publications?user_id=eq.${user.userId}&clip_id=eq.${cloudClipId}&status=eq.published&select=id,clip_id,platform,account_id,remote_id,remote_url,published_at,publish_mode,view_count,like_count,comment_count,stats_updated_at,stats_checked_at&order=published_at.asc`,
        { headers: svcHeaders, signal: AbortSignal.timeout(8000) }
      );
      if (!response.ok) {
        const text = await response.text().catch(() => "");
        return json(502, { error: `publication lookup failed: ${response.status}`, detail: text.slice(0, 200) });
      }
      const rows = await response.json() as CloudPublicationRow[];
      const accessTokens = new Map<string, string | null>();

      for (const publication of rows) {
        if (!publication.platform?.startsWith("youtube") || publicationStatsAreFresh(publication)) continue;
        const checkedAt = new Date().toISOString();
        const remoteId = String(publication.remote_id ?? "").trim();
        const metricPatch: Record<string, number> = {};
        if (remoteId) {
          try {
            let accessToken = accessTokens.get(publication.account_id);
            if (accessToken === undefined) {
              accessToken = await getUserYouTubeAccessToken(
                env, publication.account_id, user.userId, { requireSourceVault: false }
              );
              accessTokens.set(publication.account_id, accessToken);
            }
            if (accessToken) {
              const statsResponse = await fetch(
                `https://www.googleapis.com/youtube/v3/videos?part=statistics&id=${encodeURIComponent(remoteId)}`,
                { headers: { Authorization: `Bearer ${accessToken}` }, signal: AbortSignal.timeout(8000) }
              );
              if (statsResponse.ok) {
                const statsBody = await statsResponse.json() as {
                  items?: Array<{ statistics?: Record<string, unknown> }>;
                };
                const statistics = statsBody.items?.[0]?.statistics;
                if (statistics) {
                  const views = parseMetric(statistics.viewCount);
                  const likes = parseMetric(statistics.likeCount);
                  const comments = parseMetric(statistics.commentCount);
                  // Missing/hidden counters must not erase values we already know.
                  if (views !== null) metricPatch.view_count = views;
                  if (likes !== null) metricPatch.like_count = likes;
                  if (comments !== null) metricPatch.comment_count = comments;
                }
              }
            }
          } catch (error) {
            console.warn("Publication stats fetch failed", publication.id, error instanceof Error ? error.message : "unknown");
          }
        }

        const updated = Object.keys(metricPatch).length > 0;
        const patchBody = updated
          ? {
              ...metricPatch,
              stats_updated_at: checkedAt,
              stats_checked_at: checkedAt,
            }
          : { stats_checked_at: checkedAt };
        try {
          const patch = await fetch(
            `${env.SUPABASE_URL}/rest/v1/publications?id=eq.${encodeURIComponent(publication.id)}&user_id=eq.${user.userId}`,
            {
              method: "PATCH",
              headers: { ...svcHeaders, "Content-Type": "application/json", Prefer: "return=minimal" },
              body: JSON.stringify(patchBody),
              signal: AbortSignal.timeout(5000),
            }
          );
          if (patch.ok) {
            if (updated) {
              if ("view_count" in metricPatch) publication.view_count = metricPatch.view_count;
              if ("like_count" in metricPatch) publication.like_count = metricPatch.like_count;
              if ("comment_count" in metricPatch) publication.comment_count = metricPatch.comment_count;
              publication.stats_updated_at = checkedAt;
            }
            publication.stats_checked_at = checkedAt;
          }
        } catch {
          // Cached values remain valid when the cache write fails.
        }
      }

      return json(200, { publications: rows.map(publicationPayload) });
    }

    // ---- PC-scoped Remote Processing Jobs Broker (no SUPABASE_SERVICE_ROLE_KEY on PC) ----
    if (url.pathname.startsWith("/sync/pc/remote-jobs/") && request.method === "POST") {
      const pcScope = authenticatePcWorkerScope(env, request);
      if (!pcScope) return json(403, { error: "PC worker auth required" });
      const rl = checkPcBrokerRateLimit(`pc:jobs:${pcScope.scopedUserId ?? "worker"}`, 180, 60_000);
      if (!rl.allowed) {
        return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
      }
      const svcHeaders = {
        apikey: env.SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
        "content-type": "application/json",
      };

      if (url.pathname === "/sync/pc/remote-jobs/claim") {
        const body = await readJson<{ worker_id?: string }>(request);
        const workerId = typeof body?.worker_id === "string" && body.worker_id.trim()
          ? body.worker_id.trim().slice(0, 128)
          : "owner-pc-1";
        try {
          const rows = await rpcRaw(env, "cloud_claim_job", { p_worker_id: workerId });
          const row = Array.isArray(rows) ? (rows[0] as Record<string, unknown> | undefined) : (rows as Record<string, unknown> | null);
          const claimedJobId = typeof row?.job_id === "string" ? row.job_id : (typeof row?.id === "string" ? row.id : "");
          if (!row || !claimedJobId) return json(200, { job: null });
          const jobUserId = String(row.user_id || "").toLowerCase();
          if (!jobUserId || !pcScope.owners.includes(jobUserId) || (pcScope.scopedUserId && pcScope.scopedUserId !== jobUserId)) {
            return json(403, { error: "claimed job user_id is outside PC_OWNER_USER_IDS allowlist" });
          }
          return json(200, {
            job: {
              job_id: claimedJobId,
              user_id: String(row.user_id),
              youtube_url: String(row.youtube_url || ""),
              source: typeof row.source === "string" && row.source ? row.source : "youtube",
              upload_object_id: typeof row.upload_object_id === "string" ? row.upload_object_id : "",
              source_title: typeof row.source_title === "string" ? row.source_title : "",
            },
          });
        } catch (e) {
          return json(502, { error: `remote job claim failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (url.pathname === "/sync/pc/remote-jobs/stage") {
        const body = await readJson<{ job_id?: string; stage?: string; video_id?: string; duration?: number | null }>(request);
        const jobId = typeof body?.job_id === "string" ? body.job_id.trim() : "";
        const stage = typeof body?.stage === "string" ? body.stage.trim() : "";
        if (!jobId || !UUID_RE.test(jobId) || !stage) {
          return json(400, { error: "valid UUID job_id and stage required" });
        }
        try {
          const checkRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/processing_jobs?id=eq.${encodeURIComponent(jobId)}&select=user_id&limit=1`,
            { headers: svcHeaders },
          );
          if (checkRes.ok) {
            const checkRows = (await checkRes.json()) as Array<{ user_id?: string }>;
            if (checkRows[0]?.user_id && !pcScope.owners.includes(checkRows[0].user_id.toLowerCase())) {
              return json(403, { error: "job belongs to user outside PC_OWNER_USER_IDS" });
            }
          }
          const cleanStage = stage.slice(0, 64);
          const patchBody: Record<string, unknown> = {
            stage: cleanStage,
            last_error: cleanStage !== "done" ? `stage:${cleanStage}` : "",
          };
          if (typeof body?.video_id === "string" && body.video_id) {
            patchBody.video_id = body.video_id;
          }
          if (typeof body?.duration === "number" && Number.isFinite(body.duration) && body.duration > 0) {
            patchBody.duration = Math.round(body.duration * 100) / 100;
          }
          const patchRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/processing_jobs?id=eq.${encodeURIComponent(jobId)}`,
            {
              method: "PATCH",
              headers: svcHeaders,
              body: JSON.stringify(patchBody),
            },
          );
          if (!patchRes.ok) return json(502, { error: `remote job stage failed: ${patchRes.status}` });
          return json(200, { ok: true });
        } catch (e) {
          return json(502, { error: `remote job stage failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (url.pathname === "/sync/pc/remote-jobs/finish") {
        const body = await readJson<{ job_id?: string; status?: string; video_id?: string; error?: string }>(request);
        const jobId = typeof body?.job_id === "string" ? body.job_id.trim() : "";
        const status = typeof body?.status === "string" ? body.status.trim() : "";
        if (!jobId || !UUID_RE.test(jobId) || !status) {
          return json(400, { error: "valid UUID job_id and status required" });
        }
        try {
          const checkRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/processing_jobs?id=eq.${encodeURIComponent(jobId)}&select=user_id&limit=1`,
            { headers: svcHeaders },
          );
          if (checkRes.ok) {
            const checkRows = (await checkRes.json()) as Array<{ user_id?: string }>;
            if (checkRows[0]?.user_id && !pcScope.owners.includes(checkRows[0].user_id.toLowerCase())) {
              return json(403, { error: "job belongs to user outside PC_OWNER_USER_IDS" });
            }
          }
          await rpcRaw(env, "cloud_finish_job", {
            p_job_id: jobId,
            p_status: status,
            p_video_id: typeof body?.video_id === "string" ? body.video_id : "",
            p_error: typeof body?.error === "string" ? body.error.slice(0, 500) : "",
          });
          return json(200, { ok: true });
        } catch (e) {
          return json(502, { error: `remote job finish failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (url.pathname === "/sync/pc/remote-jobs/stage-by-video") {
        const body = await readJson<{ video_id?: string; stage?: string }>(request);
        const videoId = typeof body?.video_id === "string" ? body.video_id.trim() : "";
        const stage = typeof body?.stage === "string" ? body.stage.trim() : "";
        if (!videoId || !stage) return json(400, { error: "video_id and stage required" });
        const ownerFilter = pcScope.scopedUserId
          ? `eq.${pcScope.scopedUserId}`
          : `in.(${pcScope.owners.join(",")})`;
        const cleanStage = stage.slice(0, 64);
        const res = await fetch(
          `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=${ownerFilter}&video_id=eq.${encodeURIComponent(videoId)}&status=neq.failed`,
          {
            method: "PATCH",
            headers: svcHeaders,
            body: JSON.stringify({
              stage: cleanStage,
              last_error: cleanStage !== "done" ? `stage:${cleanStage}` : "",
            }),
          },
        );
        if (!res.ok) return json(502, { error: `stage-by-video update failed: ${res.status}` });
        return json(200, { ok: true });
      }

      if (url.pathname === "/sync/pc/remote-jobs/title-by-video") {
        const body = await readJson<{ video_id?: string; title?: string }>(request);
        const videoId = typeof body?.video_id === "string" ? body.video_id.trim() : "";
        const title = typeof body?.title === "string" ? body.title.trim() : "";
        if (!videoId || !title) return json(400, { error: "video_id and title required" });
        const ownerFilter = pcScope.scopedUserId
          ? `eq.${pcScope.scopedUserId}`
          : `in.(${pcScope.owners.join(",")})`;
        const res = await fetch(
          `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=${ownerFilter}&video_id=eq.${encodeURIComponent(videoId)}`,
          {
            method: "PATCH",
            headers: svcHeaders,
            body: JSON.stringify({ source_title: title.slice(0, 200) }),
          },
        );
        if (!res.ok) return json(502, { error: `title-by-video update failed: ${res.status}` });
        return json(200, { ok: true });
      }

      if (url.pathname === "/sync/pc/remote-jobs/account-for-video") {
        const body = await readJson<{ video_id?: string; user_id?: string | null }>(request);
        const videoId = typeof body?.video_id === "string" ? body.video_id.trim() : "";
        if (!videoId) return json(400, { error: "video_id required" });
        const reqUserId = typeof body?.user_id === "string" && UUID_RE.test(body.user_id.trim())
          ? body.user_id.trim().toLowerCase()
          : pcScope.scopedUserId;
        if (reqUserId && !pcScope.owners.includes(reqUserId)) {
          return json(403, { error: "user_id outside PC_OWNER_USER_IDS" });
        }
        const userFilter = reqUserId ? `eq.${reqUserId}` : `in.(${pcScope.owners.join(",")})`;
        const pubRes = await fetch(
          `${env.SUPABASE_URL}/rest/v1/publications?user_id=${userFilter}&remote_id=eq.${encodeURIComponent(videoId)}&platform=eq.youtube&select=account_id&limit=1`,
          { headers: svcHeaders },
        );
        if (pubRes.ok) {
          const pubRows = (await pubRes.json()) as Array<{ account_id?: string }>;
          if (pubRows[0]?.account_id) {
            return json(200, { account_id: pubRows[0].account_id });
          }
        }
        if (reqUserId) {
          const accRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/connected_accounts?user_id=eq.${reqUserId}&provider=eq.youtube&select=account_id,id&limit=2`,
            { headers: svcHeaders },
          );
          if (accRes.ok) {
            const accRows = (await accRes.json()) as Array<{ account_id?: string; id?: string }>;
            if (accRows.length === 1) {
              return json(200, { account_id: accRows[0].account_id || accRows[0].id || null });
            }
          }
        }
        return json(200, { account_id: null });
      }

      return json(404, { error: "not found" });
    }

    const actor = await authenticate(env, request);
    if (!actor) return json(401, { error: "unauthorized" });
    const userId = actor.userId;

    if (request.method === "POST" && url.pathname === "/reserve") {
        const body = await readJson<{ kind: string; mime_type: string; expected_bytes: number; client_token: string; clip_id?: string }>(request);
        if (!body?.client_token) return json(400, { error: "client_token required" });
        const effectiveKind = body.kind === "render" ? "clip_video" : body.kind;
        const invalid = validateReserveInput(effectiveKind, body.mime_type, body.expected_bytes);
        if (invalid) return json(400, { error: invalid });
        try {
          const r = await rpc<{ reservation_id: string; staging_key: string; media_object_id: string }>(env, "cloud_reserve_upload", {
            p_user_id: userId, p_kind: effectiveKind, p_mime: body.mime_type,
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
        const obj = await selectOne<MediaObj>(env, "media_objects", body.media_object_id, "user_id,status,final_key,byte_count,mime_type");
        if (!obj || obj.user_id !== userId) return json(403, { error: "forbidden" });
        if (obj.status !== "active") return json(409, { error: `media not active (${obj.status})` });

        let duration: number | null = null;
        try {
          const jobRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/processing_jobs?user_id=eq.${userId}&or=(upload_object_id.eq.${body.media_object_id},video_id.eq.dir_${body.media_object_id})&select=duration&limit=1`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          if (jobRes.ok) {
            const jobs = await jobRes.json() as Array<{ duration?: number }>;
            if (jobs[0]?.duration) duration = Number(jobs[0].duration);
          }
        } catch {}

        // Edge-signed fast-stream ticket bound to media_object_id via AAD
        const ticketPayload = {
          k: obj.final_key,
          u: userId,
          m: obj.mime_type || "video/mp4",
          e: Date.now() + (24 * 3600 * 1000),
        };
        const streamSig = await encryptToken(
          env.TOKEN_VAULT_KEY,
          JSON.stringify(ticketPayload),
          `media_stream|${body.media_object_id}`,
        );

        return json(200, {
          url: `${url.origin}/media/${body.media_object_id}/stream?sig=${encodeURIComponent(streamSig)}`,
          presigned_url: await presign(env, "GET", obj.final_key as string),
          expires_in: PRESIGN_EXPIRES,
          byte_count: obj.byte_count,
          duration,
        });
      }

      // Cloud storage manager: the user's active video objects, with the title
      // and status of the clip each one backs (if any).
      if (request.method === "GET" && url.pathname === "/media") {
        const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
        // The meter sums every active row, so the list must too: a single capped
        // page made 156 objects look like 100 and the bytes never added up.
        const pageSize = 1000;
        const maxPages = 10;
        const rows: Array<{ id: string; kind: string; byte_count: number | null; created_at: string; clip_id: string | null }> = [];
        let truncated = false;
        for (let page = 0; page < maxPages; page++) {
          const listRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/media_objects?user_id=eq.${userId}&status=eq.active&select=id,kind,byte_count,created_at,clip_id&order=created_at.desc&limit=${pageSize}&offset=${page * pageSize}`,
            { headers: svc });
          if (!listRes.ok) return json(502, { error: `media list failed: ${listRes.status}` });
          const batch = await listRes.json() as typeof rows;
          rows.push(...batch);
          if (batch.length < pageSize) break;
          if (page === maxPages - 1) truncated = true;
        }
        const clipIds = Array.from(new Set(rows.map(r => r.clip_id).filter((v): v is string => Boolean(v))));
        let clipsById: Record<string, { title: string | null; local_clip_id: string; status: string }> = {};
        if (clipIds.length > 0) {
          const clipRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?id=in.(${clipIds.map(encodeURIComponent).join(",")})&select=id,title,local_clip_id,status`,
            { headers: svc });
          if (clipRes.ok) {
            const clips = await clipRes.json() as Array<{ id: string; title: string | null; local_clip_id: string; status: string }>;
            clipsById = Object.fromEntries(clips.map(c => [c.id, { title: c.title, local_clip_id: c.local_clip_id, status: c.status }]));
          }
        }
        return json(200, {
          total_bytes: rows.reduce((sum, r) => sum + (r.byte_count ?? 0), 0),
          truncated: truncated || undefined,
          items: rows.map(r => ({
            id: r.id,
            kind: r.kind,
            size: r.byte_count ?? 0,
            created_at: r.created_at,
            clip_id: r.clip_id,
            local_clip_id: r.clip_id ? clipsById[r.clip_id]?.local_clip_id ?? null : null,
            title: r.clip_id ? clipsById[r.clip_id]?.title ?? null : null,
            clip_status: r.clip_id ? clipsById[r.clip_id]?.status ?? null : null,
          })),
        });
      }

      // Delete one cloud file. Clip-backed objects go through the durable PC
      // command (the PC owns the clip and would re-sync it otherwise); bare
      // objects are removed from R2 + the DB immediately. Platform posts are
      // never touched by this endpoint.
      const mediaDeleteMatch = url.pathname.match(/^\/media\/([0-9a-f-]{36})\/delete$/i);
      if (request.method === "POST" && mediaDeleteMatch) {
        const mediaId = mediaDeleteMatch[1];
        const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
        const obj = await selectOne<{ id: string; user_id: string; status: string; clip_id: string | null; final_key: string | null; staging_key: string | null }>(
          env, "media_objects", mediaId, "id,user_id,status,clip_id,final_key,staging_key");
        if (!obj || obj.user_id !== userId) return json(403, { error: "forbidden" });
        if (obj.status !== "active") return json(409, { error: `media not active (${obj.status})` });
        if (obj.clip_id) {
          const clip = await selectOne<{ local_clip_id: string; status: string }>(env, "clips_cloud", obj.clip_id, "local_clip_id,status");
          if (!clip?.local_clip_id) return json(404, { error: "clip not found" });
          if (clip.status === "rejected") return json(200, { queued: true, already: true });
          try {
            const queued = await rpc<{ command_id: string; state: string; purge_after: string | null }>(env, "cloud_enqueue_clip_command", {
              p_user_id: userId,
              p_local_clip_id: clip.local_clip_id,
              p_kind: "reject",
              p_payload: { reason: "Deleted from cloud storage" },
            });
            return json(200, { queued: true, state: queued?.state ?? "pending", purge_after: queued?.purge_after ?? null });
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            return json(502, { error: `failed to queue delete: ${msg}` });
          }
        }
        for (const key of [obj.final_key, obj.staging_key]) {
          if (key) await env.BUCKET.delete(key);
        }
        const delRes = await fetch(`${env.SUPABASE_URL}/rest/v1/media_objects?id=eq.${mediaId}&user_id=eq.${userId}`, {
          method: "DELETE", headers: svc });
        if (!delRes.ok) return json(502, { error: `media row delete failed: ${delRes.status}` });
        return json(200, { deleted: true });
      }

            // Project Document Timeline Editing (Stage 2 Mobile Endpoints):
      // Single authoritative document, CAS optimistic concurrency, and persistent undo/redo.
      if (request.method === "GET" && url.pathname.startsWith("/clips/") && url.pathname.endsWith("/timeline")) {
        const localClipId = decodeURIComponent(url.pathname.slice("/clips/".length, -"/timeline".length));
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        try {
          const res = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&or=(local_clip_id.eq.${encodeURIComponent(localClipId)},id.eq.${encodeURIComponent(localClipId)})&select=id,project_document,segments_revision,active_state_revision,can_undo,can_redo,media_fingerprint,render_fingerprint,rendered_fingerprint,render_stale&limit=1`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          const rows = res.ok ? ((await res.json()) as Array<Record<string, unknown>>) : [];
          if (!rows || rows.length === 0) return json(404, { error: "clip not found" });
          const row = rows[0];
          return json(200, {
            clip: row,
            document: row.project_document,
            project_document: row.project_document,
            revision: row.segments_revision,
            segments_revision: row.segments_revision,
            active_state_revision: row.active_state_revision,
            can_undo: row.can_undo,
            can_redo: row.can_redo,
          });
        } catch (e) {
          return json(502, { error: `timeline fetch failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (request.method === "PUT" && url.pathname.startsWith("/clips/") && url.pathname.endsWith("/timeline")) {
        const localClipId = decodeURIComponent(url.pathname.slice("/clips/".length, -"/timeline".length));
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });

        // Resolve UUID to local_clip_id if target was passed as cloud UUID or local_clip_id
        let actualLocalClipId = localClipId;
        try {
          const lookupRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&or=(local_clip_id.eq.${encodeURIComponent(localClipId)},id.eq.${encodeURIComponent(localClipId)})&select=id,local_clip_id&limit=1`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          if (lookupRes.ok) {
            const rows = (await lookupRes.json()) as Array<{ id: string; local_clip_id?: string }>;
            if (rows && rows.length > 0 && rows[0].local_clip_id) {
              actualLocalClipId = rows[0].local_clip_id;
            }
          }
        } catch {}
        const body = await readJson<{
          document?: Record<string, unknown>;
          project_document?: Record<string, unknown>;
          base_revision?: number;
          expected_revision?: number;
          operation_id?: string;
          action_type?: string;
          action_metadata?: Record<string, unknown>;
        }>(request);

        const doc = body?.project_document ?? body?.document;
        if (!doc || typeof doc !== "object") return json(400, { error: "project document required" });
        const operationId = typeof body?.operation_id === "string" && body.operation_id.trim() ? body.operation_id.trim() : crypto.randomUUID();
        const actionType = typeof body?.action_type === "string" ? body.action_type : "edit";
        const baseRev = typeof body?.expected_revision === "number"
          ? Math.floor(body.expected_revision)
          : (typeof body?.base_revision === "number" ? Math.floor(body.base_revision) : null);

        // Validate segments and kept duration server-side
        const timeline = doc.timeline as Record<string, unknown> | undefined;
        const segCheck = validateTimelineSegments(timeline?.segments);
        if (!segCheck.valid) {
          return json(422, { error: segCheck.error });
        }

        // Standardize canonical segments representation
        if (timeline) {
          timeline.kept_duration = segCheck.keptDuration;
          if (segCheck.canonicalSegments) {
            timeline.segments = segCheck.canonicalSegments;
          }
        }

        // Server-Side Trusted Fingerprint Recomputation
        const sourceAssetId = String(doc.source_asset_id || localClipId);
        const mediaFp = computeMediaFingerprint(sourceAssetId, timeline?.segments);
        const layoutMode = (doc.layout as Record<string, unknown> | undefined)?.mode ? String((doc.layout as Record<string, unknown>).mode) : "portrait_9_16";
        const cropAnchor = typeof (doc.layout as Record<string, unknown> | undefined)?.crop_anchor_x === "number" ? Number((doc.layout as Record<string, unknown>).crop_anchor_x) : 0.5;
        const renderFp = computeRenderFingerprint(mediaFp, layoutMode, cropAnchor, doc.tracks as unknown[]);

        // Inject trusted metadata
        doc.metadata = {
          ...(typeof doc.metadata === "object" && doc.metadata !== null ? doc.metadata : {}),
          media_fingerprint: mediaFp,
          render_fingerprint: renderFp,
          action_type: actionType,
        };

        try {
          const result = await rpc<{ revision: number; conflict: boolean; current_document: unknown }>(env, "cloud_put_project_document", {
            p_user_id: userId,
            p_local_clip_id: actualLocalClipId,
            p_document: doc,
            p_base_revision: baseRev,
            p_operation_id: operationId,
            p_action_type: actionType,
            p_action_metadata: body?.action_metadata ?? null,
            p_author: "mobile",
          });

          if (result?.conflict) {
            return json(409, {
              error: "timeline_conflict",
              revision: result.revision,
              current_document: result.current_document,
              project_document: result.current_document,
              document: result.current_document,
            });
          }
          return json(200, {
            ok: true,
            revision: result?.revision ?? 0,
            document: doc,
            project_document: doc,
          });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          return json(502, { error: `timeline save failed: ${msg}` });
        }
      }

      if (request.method === "POST" && url.pathname.startsWith("/clips/") && url.pathname.endsWith("/timeline/undo")) {
        const localClipId = decodeURIComponent(url.pathname.slice("/clips/".length, -"/timeline/undo".length));
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        let actualLocalClipId = localClipId;
        try {
          const lookupRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&or=(local_clip_id.eq.${encodeURIComponent(localClipId)},id.eq.${encodeURIComponent(localClipId)})&select=id,local_clip_id&limit=1`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          if (lookupRes.ok) {
            const rows = (await lookupRes.json()) as Array<{ id: string; local_clip_id?: string }>;
            if (rows && rows.length > 0 && rows[0].local_clip_id) {
              actualLocalClipId = rows[0].local_clip_id;
            }
          }
        } catch {}
        const body = await readJson<{ operation_id?: string }>(request);
        const opId = typeof body?.operation_id === "string" && body.operation_id.trim() ? body.operation_id.trim() : crypto.randomUUID();
        try {
          const result = await rpc<{ revision: number; conflict: boolean; current_document: unknown }>(env, "cloud_undo_project_document", {
            p_user_id: userId,
            p_local_clip_id: actualLocalClipId,
            p_operation_id: opId,
          });
          return json(200, {
            ok: true,
            revision: result?.revision,
            document: result?.current_document,
            project_document: result?.current_document,
          });
        } catch (e) {
          return json(400, { error: `undo failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (request.method === "POST" && url.pathname.startsWith("/clips/") && url.pathname.endsWith("/timeline/redo")) {
        const localClipId = decodeURIComponent(url.pathname.slice("/clips/".length, -"/timeline/redo".length));
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        let actualLocalClipId = localClipId;
        try {
          const lookupRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&or=(local_clip_id.eq.${encodeURIComponent(localClipId)},id.eq.${encodeURIComponent(localClipId)})&select=id,local_clip_id&limit=1`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          if (lookupRes.ok) {
            const rows = (await lookupRes.json()) as Array<{ id: string; local_clip_id?: string }>;
            if (rows && rows.length > 0 && rows[0].local_clip_id) {
              actualLocalClipId = rows[0].local_clip_id;
            }
          }
        } catch {}
        const body = await readJson<{ operation_id?: string }>(request);
        const opId = typeof body?.operation_id === "string" && body.operation_id.trim() ? body.operation_id.trim() : crypto.randomUUID();
        try {
          const result = await rpc<{ revision: number; conflict: boolean; current_document: unknown }>(env, "cloud_redo_project_document", {
            p_user_id: userId,
            p_local_clip_id: actualLocalClipId,
            p_operation_id: opId,
          });
          return json(200, {
            ok: true,
            revision: result?.revision,
            document: result?.current_document,
            project_document: result?.current_document,
          });
        } catch (e) {
          return json(400, { error: `redo failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      // Direct Mobile-to-Cloud Publishing Queue:
      // Phone submits rendered clip for multi-platform publishing without PC dependency.
      if (request.method === "POST" && url.pathname === "/publish/queue") {
        const body = await readJson<{
          clip_id: string;
          title: string;
          description?: string;
          platforms?: string | string[];
          publish_mode?: "private" | "unlisted" | "public";
          r2_key?: string;
          rendered_fingerprint?: string;
          account_ids?: Record<string, string | string[]>;
        }>(request);

        if (!body?.clip_id || typeof body.clip_id !== "string" || !body.clip_id.trim()) {
          return json(400, { error: "clip_id required" });
        }
        if (!body?.title || typeof body.title !== "string" || !body.title.trim()) {
          return json(400, { error: "title required" });
        }

        const clipRef = body.clip_id.trim();
        const svcHeaders = {
          apikey: env.SUPABASE_SERVICE_ROLE_KEY,
          Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
        };

        // Enforce authenticated caller ownership of clip_id (never enqueue for foreign clips).
        const clipLookupUrl = new URL(`${env.SUPABASE_URL}/rest/v1/clips_cloud`);
        clipLookupUrl.searchParams.set("user_id", `eq.${userId}`);
        if (UUID_RE.test(clipRef)) {
          clipLookupUrl.searchParams.set("or", `(id.eq.${clipRef},local_clip_id.eq.${clipRef})`);
        } else {
          clipLookupUrl.searchParams.set("local_clip_id", `eq.${clipRef}`);
        }
        clipLookupUrl.searchParams.set("select", "id,user_id,local_clip_id,media_object_id");
        clipLookupUrl.searchParams.set("limit", "1");
        const clipRes = await fetch(clipLookupUrl.toString(), { headers: svcHeaders });
        if (!clipRes.ok) {
          return json(502, { error: `clip ownership lookup failed: ${clipRes.status}` });
        }
        const clipRows = (await clipRes.json()) as Array<{
          id: string;
          user_id?: string;
          local_clip_id?: string;
          media_object_id?: string | null;
        }>;
        const ownedClip = clipRows[0];
        if (!ownedClip || (ownedClip.user_id && ownedClip.user_id !== userId)) {
          return json(403, { error: "forbidden: clip_id is not owned by authenticated caller" });
        }

        // Enforce owned final/${userId}/ R2 prefix & kind === 'clip_video' (never publish
        // foreign objects, thumbnails, or raw edit_window proxies).
        const expectedFinalPrefix = `final/${userId}/`;
        const requestedR2Key = typeof body.r2_key === "string" ? body.r2_key.trim() : "";
        let verifiedR2Key: string | null = null;

        if (requestedR2Key) {
          if (requestedR2Key.includes("..") || !requestedR2Key.startsWith(expectedFinalPrefix)) {
            return json(403, { error: "forbidden: r2_key must belong to authenticated user's final prefix" });
          }
          const moUrl = new URL(`${env.SUPABASE_URL}/rest/v1/media_objects`);
          moUrl.searchParams.set("user_id", `eq.${userId}`);
          moUrl.searchParams.set("final_key", `eq.${requestedR2Key}`);
          moUrl.searchParams.set("status", "eq.active");
          moUrl.searchParams.set("select", "id,user_id,clip_id,kind,final_key");
          moUrl.searchParams.set("limit", "2");
          const moRes = await fetch(moUrl.toString(), { headers: svcHeaders });
          if (!moRes.ok) {
            return json(502, { error: `media verification failed: ${moRes.status}` });
          }
          const moRows = (await moRes.json()) as Array<{
            id: string;
            user_id?: string;
            clip_id?: string | null;
            kind?: string;
            final_key?: string | null;
          }>;
          const mo = moRows[0];
          if (!mo || (mo.user_id && mo.user_id !== userId) || !mo.final_key) {
            return json(403, { error: "forbidden: r2_key does not match an active owned media object" });
          }
          if (mo.kind !== "clip_video") {
            return json(422, { error: `only rendered clip_video assets can be published (got ${mo.kind ?? "unknown"})` });
          }
          if (mo.clip_id && mo.clip_id !== ownedClip.id) {
            return json(403, { error: "forbidden: r2_key belongs to a different clip" });
          }
          verifiedR2Key = mo.final_key;
        } else {
          const moUrl = new URL(`${env.SUPABASE_URL}/rest/v1/media_objects`);
          moUrl.searchParams.set("user_id", `eq.${userId}`);
          moUrl.searchParams.set("status", "eq.active");
          moUrl.searchParams.set("kind", "eq.clip_video");
          moUrl.searchParams.set("clip_id", `eq.${ownedClip.id}`);
          moUrl.searchParams.set("order", "created_at.desc");
          moUrl.searchParams.set("limit", "1");
          moUrl.searchParams.set("select", "id,user_id,kind,final_key");
          const moRes = await fetch(moUrl.toString(), { headers: svcHeaders });
          if (!moRes.ok) {
            return json(502, { error: `media lookup failed: ${moRes.status}` });
          }
          let moRows = (await moRes.json()) as Array<{
            id: string;
            user_id?: string;
            kind?: string;
            final_key?: string | null;
          }>;
          if (moRows.length === 0 && ownedClip.media_object_id) {
            const directMoUrl = new URL(`${env.SUPABASE_URL}/rest/v1/media_objects`);
            directMoUrl.searchParams.set("id", `eq.${ownedClip.media_object_id}`);
            directMoUrl.searchParams.set("user_id", `eq.${userId}`);
            directMoUrl.searchParams.set("status", "eq.active");
            directMoUrl.searchParams.set("kind", "eq.clip_video");
            directMoUrl.searchParams.set("select", "id,user_id,kind,final_key");
            directMoUrl.searchParams.set("limit", "1");
            const directMoRes = await fetch(directMoUrl.toString(), { headers: svcHeaders });
            if (directMoRes.ok) {
              moRows = (await directMoRes.json()) as typeof moRows;
            }
          }
          const candidate = moRows[0];
          if (
            !candidate?.final_key ||
            candidate.kind !== "clip_video" ||
            !candidate.final_key.startsWith(expectedFinalPrefix)
          ) {
            return json(409, { error: "no rendered clip_video object available for publishing" });
          }
          verifiedR2Key = candidate.final_key;
        }

        const rawPlatforms = body.platforms;
        const rawList = Array.isArray(rawPlatforms)
          ? rawPlatforms
          : (typeof rawPlatforms === "string" ? rawPlatforms.split(",").map((p) => p.trim()).filter(Boolean) : ["youtube"]);
        const platformsList = Array.from(
          new Set(
            rawList
              .map((p) => normalizePublishPlatform(p))
              .filter((p): p is PublishPlatform => Boolean(p)),
          ),
        );
        if (platformsList.length === 0) {
          return json(400, { error: "at least one supported publishing platform is required" });
        }

        const normalizedAccountIds: Record<string, string | string[]> = {};
        if (body.account_ids && typeof body.account_ids === "object" && !Array.isArray(body.account_ids)) {
          for (const [rawKey, rawVal] of Object.entries(body.account_ids)) {
            const normPlat = normalizePublishPlatform(rawKey);
            if (!normPlat) continue;
            if (Array.isArray(rawVal)) {
              const cleaned = Array.from(new Set(rawVal.map((v) => String(v ?? "").trim()).filter(Boolean)));
              if (cleaned.length > 0) {
                const existing = normalizedAccountIds[normPlat];
                const merged = Array.isArray(existing)
                  ? Array.from(new Set([...existing, ...cleaned]))
                  : (typeof existing === "string" && existing ? Array.from(new Set([existing, ...cleaned])) : cleaned);
                normalizedAccountIds[normPlat] = merged;
              }
            } else if (typeof rawVal === "string" && rawVal.trim()) {
              const cleaned = rawVal.trim();
              const existing = normalizedAccountIds[normPlat];
              if (Array.isArray(existing)) {
                if (!existing.includes(cleaned)) existing.push(cleaned);
              } else if (typeof existing === "string" && existing && existing !== cleaned) {
                normalizedAccountIds[normPlat] = [existing, cleaned];
              } else {
                normalizedAccountIds[normPlat] = cleaned;
              }
            }
          }
        }

        const publishMode = ["private", "unlisted", "public"].includes(String(body.publish_mode)) ? body.publish_mode! : "private";

        try {
          const rows = await rpcRaw(env, "cloud_enqueue_publishing_job", {
            p_user_id: userId,
            p_clip_id: ownedClip.id,
            p_title: body.title.trim().slice(0, 100),
            p_description: typeof body.description === "string" ? body.description.trim().slice(0, 5000) : "",
            p_platforms: platformsList,
            p_publish_mode: publishMode,
            p_r2_key: verifiedR2Key,
            p_rendered_fingerprint: body.rendered_fingerprint ?? null,
            p_account_ids: normalizedAccountIds,
          });

          const job = Array.isArray(rows) && rows.length > 0 ? (rows[0] as { id: string; status: string }) : null;
          ctx?.waitUntil(drainPublishingQueue(env));
          return json(200, {
            ok: true,
            job_id: job?.id,
            status: job?.status ?? "queued",
            clip_id: ownedClip.id,
            r2_key: verifiedR2Key,
            platforms: platformsList,
            account_ids: normalizedAccountIds,
          });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          return json(502, { error: `publish enqueue failed: ${msg}` });
        }
      }

      if (request.method === "GET" && url.pathname.startsWith("/publish/status/")) {
        const targetClipId = decodeURIComponent(url.pathname.slice("/publish/status/".length)).trim();
        if (!targetClipId || targetClipId.length > 128) return json(400, { error: "invalid clip id" });
        try {
          const svcHeaders = {
            apikey: env.SUPABASE_SERVICE_ROLE_KEY,
            Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
          };
          const clipLookupUrl = new URL(`${env.SUPABASE_URL}/rest/v1/clips_cloud`);
          clipLookupUrl.searchParams.set("user_id", `eq.${userId}`);
          if (UUID_RE.test(targetClipId)) {
            clipLookupUrl.searchParams.set("or", `(id.eq.${targetClipId},local_clip_id.eq.${targetClipId})`);
          } else {
            clipLookupUrl.searchParams.set("local_clip_id", `eq.${targetClipId}`);
          }
          clipLookupUrl.searchParams.set("select", "id,local_clip_id");
          clipLookupUrl.searchParams.set("limit", "1");
          const clipRes = await fetch(clipLookupUrl.toString(), { headers: svcHeaders });
          const clipRows = clipRes.ok ? ((await clipRes.json()) as Array<{ id?: string; local_clip_id?: string }>) : [];
          const resolvedClipId = clipRows[0]?.id ? String(clipRows[0].id) : "";

          const jobUrl = new URL(`${env.SUPABASE_URL}/rest/v1/publishing_jobs`);
          jobUrl.searchParams.set("user_id", `eq.${userId}`);
          if (resolvedClipId && resolvedClipId !== targetClipId && /^[A-Za-z0-9._-]+$/.test(targetClipId)) {
            jobUrl.searchParams.set("clip_id", `in.(${resolvedClipId},${targetClipId})`);
          } else {
            jobUrl.searchParams.set("clip_id", `eq.${resolvedClipId || targetClipId}`);
          }
          jobUrl.searchParams.set("order", "created_at.desc");
          jobUrl.searchParams.set("limit", "10");

          const res = await fetch(jobUrl.toString(), { headers: svcHeaders });
          const rows = res.ok ? ((await res.json()) as Array<Record<string, unknown>>) : [];
          const jobList = Array.isArray(rows) ? rows : [];
          if (jobList.length > 0) {
            const jobIds = postgrestInList(jobList.map((j) => String(j.id ?? "")));
            if (jobIds) {
              const tRes = await fetch(
                `${env.SUPABASE_URL}/rest/v1/publishing_job_targets?user_id=eq.${userId}&job_id=in.(${jobIds})&select=id,job_id,platform,account_id,status,attempt_count,remote_id,remote_url,last_error,updated_at`,
                { headers: svcHeaders },
              ).catch(() => null);
              if (tRes && tRes.ok) {
                const targets = (await tRes.json().catch(() => [])) as Array<Record<string, unknown>>;
                if (Array.isArray(targets)) {
                  const byJob = new Map<string, Array<Record<string, unknown>>>();
                  for (const t of targets) {
                    const jid = String(t.job_id ?? "");
                    if (!jid) continue;
                    const arr = byJob.get(jid) ?? [];
                    arr.push(t);
                    byJob.set(jid, arr);
                  }
                  for (const j of jobList) {
                    const attached = byJob.get(String(j.id ?? ""));
                    if (attached && attached.length > 0) {
                      j.targets = attached;
                    }
                  }
                }
              }
            }
          }
          return json(200, { ok: true, clip_id: resolvedClipId || targetClipId, jobs: jobList });
        } catch (e) {
          return json(502, { error: `publish status fetch failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      // Source video staging URL: returns presigned GET URL for phone to download source video
      // if not available locally, enabling true phone-only editing and rendering.
      // Never serves kind='thumbnail'; prefers edit_window proxy or clip_video, owned by caller.
      if (request.method === "GET" && url.pathname.startsWith("/clips/") && url.pathname.endsWith("/source-url")) {
        const localClipId = decodeURIComponent(url.pathname.slice("/clips/".length, -"/source-url".length));
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        try {
          const svc = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
          type SourceMediaRow = { id: string; kind?: string; final_key: string | null; byte_count: number | null; mime_type: string; user_id?: string };
          const pickSourceMediaObj = (rows: SourceMediaRow[]): SourceMediaRow | null => {
            const valid = (Array.isArray(rows) ? rows : []).filter(
              (r) => r && r.final_key && r.kind !== "thumbnail" && (!r.user_id || r.user_id === userId),
            );
            return (
              valid.find((r) => r.kind === "edit_window")
              ?? valid.find((r) => r.kind === "clip_video")
              ?? valid[0]
              ?? null
            );
          };

          let mediaObj: SourceMediaRow | null = null;
          let resolvedLocalClipRef = localClipId;
          if (UUID_RE.test(localClipId)) {
            const directRes = await fetch(
              `${env.SUPABASE_URL}/rest/v1/media_objects?user_id=eq.${userId}&status=eq.active&kind=neq.thumbnail&clip_id=eq.${encodeURIComponent(localClipId)}&select=id,kind,final_key,byte_count,mime_type,user_id&order=created_at.desc&limit=10`,
              { headers: svc },
            );
            if (directRes.ok) {
              mediaObj = pickSourceMediaObj((await directRes.json()) as SourceMediaRow[]);
            }
          }

          if (!mediaObj) {
            const clipLookup = new URL(`${env.SUPABASE_URL}/rest/v1/clips_cloud`);
            clipLookup.searchParams.set("user_id", `eq.${userId}`);
            if (UUID_RE.test(localClipId)) {
              clipLookup.searchParams.set("or", `(id.eq.${localClipId},local_clip_id.eq.${localClipId})`);
            } else {
              clipLookup.searchParams.set("local_clip_id", `eq.${localClipId}`);
            }
            clipLookup.searchParams.set("select", "id,local_clip_id,media_object_id");
            clipLookup.searchParams.set("limit", "1");
            const clipRes = await fetch(clipLookup.toString(), { headers: svc });
            if (clipRes.ok) {
              const clipRows = (await clipRes.json()) as Array<{ id: string; local_clip_id?: string | null; media_object_id: string | null }>;
              if (clipRows && clipRows.length > 0) {
                const clipRow = clipRows[0];
                if (clipRow.local_clip_id) resolvedLocalClipRef = String(clipRow.local_clip_id);
                if (clipRow.id) {
                  const moRes = await fetch(
                    `${env.SUPABASE_URL}/rest/v1/media_objects?user_id=eq.${userId}&status=eq.active&kind=neq.thumbnail&clip_id=eq.${encodeURIComponent(clipRow.id)}&select=id,kind,final_key,byte_count,mime_type,user_id&order=created_at.desc&limit=10`,
                    { headers: svc },
                  );
                  if (moRes.ok) {
                    mediaObj = pickSourceMediaObj((await moRes.json()) as SourceMediaRow[]);
                  }
                }
                if (!mediaObj && clipRow.media_object_id) {
                  const moRes = await fetch(
                    `${env.SUPABASE_URL}/rest/v1/media_objects?id=eq.${encodeURIComponent(clipRow.media_object_id)}&user_id=eq.${userId}&status=eq.active&kind=neq.thumbnail&select=id,kind,final_key,byte_count,mime_type,user_id&limit=1`,
                    { headers: svc },
                  );
                  if (moRes.ok) {
                    mediaObj = pickSourceMediaObj((await moRes.json()) as SourceMediaRow[]);
                  }
                }
              }
            }
          }

          if (!mediaObj || !mediaObj.final_key) {
            return json(404, { error: "source media not found in cloud storage" });
          }

          let editWindowStart: number | undefined = undefined;
          let editWindowEnd: number | undefined = undefined;
          try {
            const ewUrl = new URL(`${env.SUPABASE_URL}/rest/v1/edit_windows`);
            ewUrl.searchParams.set("user_id", `eq.${userId}`);
            ewUrl.searchParams.set("status", "eq.ready");
            ewUrl.searchParams.set("or", `(media_object_id.eq.${mediaObj.id},local_clip_id.eq.${resolvedLocalClipRef})`);
            ewUrl.searchParams.set("select", "window_start,window_end");
            ewUrl.searchParams.set("order", "created_at.desc");
            ewUrl.searchParams.set("limit", "1");
            const ewRes = await fetch(ewUrl.toString(), { headers: svc });
            if (ewRes.ok) {
              const ewRows = (await ewRes.json().catch(() => [])) as Array<{ window_start?: number; window_end?: number }>;
              if (Array.isArray(ewRows) && ewRows.length > 0) {
                if (ewRows[0].window_start != null && Number.isFinite(Number(ewRows[0].window_start))) {
                  editWindowStart = Number(ewRows[0].window_start);
                }
                if (ewRows[0].window_end != null && Number.isFinite(Number(ewRows[0].window_end))) {
                  editWindowEnd = Number(ewRows[0].window_end);
                }
              }
            }
          } catch {}

          const presignedGet = await presign(env, "GET", mediaObj.final_key);
          return json(200, {
            ok: true,
            clip_id: localClipId,
            media_object_id: mediaObj.id,
            source_url: presignedGet,
            byte_count: mediaObj.byte_count,
            mime_type: mediaObj.mime_type,
            edit_window_start: editWindowStart,
            edit_window_end: editWindowEnd,
            expires_in: PRESIGN_EXPIRES,
          });
        } catch (e) {
          return json(502, { error: `failed to generate source url: ${e instanceof Error ? e.message : e}` });
        }
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
      // Read-only consistency report for the authenticated owner (no writes).
      if (request.method === "GET" && url.pathname === "/sync/pc/consistency") {
        if (!isPcWorkerAuth(env, request)) return json(403, { error: "PC worker auth required" });
        try {
          const report = await rpcRaw(env, "cloud_consistency_report", { p_user_id: userId });
          return json(200, { report: report ?? null });
        } catch (e) {
          return json(502, { error: `consistency report failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      // Publication-stats proxy for Meta destinations: the PC has no Meta token
      // (it never leaves Cloudflare), so the PC asks the worker to read the
      // graph on its behalf. Ownership: cloud_get_credentials_by_account_id is
      // bound to the token's X-Dispatch-User-Id.
      if (request.method === "POST" && url.pathname === "/sync/pc/publication-stats") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const body = await readJson<{ provider?: unknown; account_id?: unknown; remote_id?: unknown }>(request);
        if (!body) return json(400, { error: "invalid JSON body" });
        const provider = body.provider;
        if (provider !== "instagram" && provider !== "facebook") {
          return json(400, { error: "provider must be instagram or facebook" });
        }
        const accountId = typeof body.account_id === "string" ? body.account_id.trim() : "";
        const remoteId = typeof body.remote_id === "string" ? body.remote_id.trim() : "";
        if (!accountId || !remoteId) return json(400, { error: "account_id and remote_id are required" });
        try {
          const stats = await fetchMetaPublicationStats(env, userId, accountId, remoteId, provider);
          return json(200, { found: true, stats });
        } catch (e) {
          return json(502, { error: `meta stats failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (request.method === "POST" && url.pathname === "/sync/pc/clips") {
        if (!isPcWorkerAuth(env, request)) return json(403, { error: "PC worker auth required" });
        const body = await readJson<{
          local_clip_id: string; title: string; status: string;
          start_time: number; end_time: number;
          ai_suggested_start_time?: number; ai_suggested_end_time?: number; range_revision?: number;
          source_orientation?: string; source_aspect_ratio?: string; layout_mode?: string;
          available_layout_modes?: unknown; caption_data?: unknown; caption_revision?: number; session_id?: string; youtube_video_id?: string;
          publications?: Array<{
            platform?: string;
            account_id?: string;
            remote_id?: string;
            remote_url?: string;
            published_at?: string;
            publish_mode?: string;
          }>;
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
                source_orientation: body?.source_orientation || "landscape",
                source_aspect_ratio: body?.source_aspect_ratio || "16:9",
                layout_mode: body?.layout_mode || "fit_blur",
                available_layout_modes: body?.available_layout_modes || null,
                caption_data: body?.caption_data || null,
                caption_revision: Number(body?.caption_revision || 0),
                ...(body?.session_id && UUID_RE.test(body.session_id) ? { session_id: body.session_id } : {}),
                ...(body?.youtube_video_id ? { youtube_video_id: body.youtube_video_id } : (body?.session_id ? { youtube_video_id: body.session_id } : {})),
              }),
            }
          );
          if (!upsertRes.ok) {
            const text = await upsertRes.text().catch(() => "");
            return json(502, { error: `clips_cloud upsert failed: ${upsertRes.status} ${text.slice(0, 200)}` });
          }
          const upsertRows = await upsertRes.json() as Array<{ id: string }>;
          const cloudClipId = upsertRows[0]?.id ?? null;

          // Published destinations ride along with the clip sync. Metrics are
          // deliberately not overwritten here; the stats endpoint owns them.
          let publicationsSynced = 0;
          let publicationsError: string | null = null;
          if (cloudClipId && Array.isArray(body?.publications)) {
            const unique = new Map<string, NonNullable<typeof body.publications>[number]>();
            for (const publication of body.publications) {
              const rawPlatform = String(publication?.platform ?? "").toLowerCase();
              if (!rawPlatform.startsWith("youtube")) continue;
              // The cloud CHECK constraint stores the provider, not the format.
              const platform = "youtube";
              const accountRef = String(publication?.account_id ?? "").trim().slice(0, 200);
              const remoteId = String(publication?.remote_id ?? "").trim().slice(0, 128);
              if (!accountRef || !remoteId) continue;
              unique.set(`${platform}:${accountRef}`, { ...publication, platform, account_id: accountRef, remote_id: remoteId });
            }
            for (const publication of unique.values()) {
              const remoteUrl = String(publication?.remote_url ?? "").slice(0, 2048);
              const publishedAtRaw = String(publication?.published_at ?? "");
              const publishedAtMs = Date.parse(publishedAtRaw);
              if (!Number.isFinite(publishedAtMs)) {
                publicationsError = publicationsError ?? "publication skipped: invalid published_at";
                continue;
              }
              const publishedAt = new Date(publishedAtMs).toISOString();
              const publishMode = ["private", "unlisted", "public"].includes(String(publication?.publish_mode))
                ? String(publication?.publish_mode)
                : null;
              const pubRes = await fetch(
                `${env.SUPABASE_URL}/rest/v1/publications?on_conflict=clip_id,platform,account_id`,
                {
                  method: "POST",
                  headers: {
                    apikey: env.SUPABASE_SERVICE_ROLE_KEY,
                    Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
                    "Content-Type": "application/json",
                    Prefer: "resolution=merge-duplicates,return=minimal",
                  },
                  body: JSON.stringify({
                    clip_id: cloudClipId,
                    user_id: userId,
                    platform: String(publication.platform),
                    account_id: String(publication.account_id),
                    remote_id: String(publication.remote_id),
                    remote_url: remoteUrl.startsWith("https://") || remoteUrl.startsWith("http://") ? remoteUrl : null,
                    published_at: publishedAt,
                    publish_mode: publishMode,
                    status: "published",
                  }),
                }
              );
              if (pubRes.ok) {
                publicationsSynced++;
              } else if (!publicationsError) {
                const text = await pubRes.text().catch(() => "");
                publicationsError = `publication upsert failed: ${pubRes.status} ${text.slice(0, 160)}`;
              }
            }
          }

          return json(200, {
            cloud_clip_id: cloudClipId,
            publications_synced: publicationsSynced,
            publications_error: publicationsError ?? undefined,
          });
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
              `${env.SUPABASE_URL}/rest/v1/edit_windows?user_id=eq.${userId}&local_clip_id=eq.${encodeURIComponent(localClipId)}&status=eq.ready&select=id,media_object_id,window_start,window_end,transcript_status,render_fingerprint&order=created_at.asc&limit=1`,
              { headers: svc }
            ),
          ]);
          const clipRows = clipRes.ok ? (await clipRes.json() as Array<Record<string, unknown>>) : [];
          const clipRow = Array.isArray(clipRows) ? clipRows[0] : null;
          const windowRows = windowRes.ok ? (await windowRes.json() as Array<Record<string, unknown>>) : [];
          const windowRow = Array.isArray(windowRows) ? windowRows[0] : undefined;
          return json(200, {
            local_clip_id: localClipId,
            cloud_clip_id: (clipRow && typeof clipRow.id === "string") ? clipRow.id : null,
            tombstoned: Boolean(clipRow && clipRow.status === "rejected" && clipRow.reject_purge_after),
            reject_requested_at: clipRow ? clipRow.reject_requested_at : null,
            reject_purge_after: clipRow ? clipRow.reject_purge_after : null,
            already_staged: Boolean(windowRow),
            window_id: windowRow?.id ?? null,
            media_object_id: windowRow?.media_object_id ?? null,
            window_start: windowRow?.window_start ?? null,
            window_end: windowRow?.window_end ?? null,
            transcript_status: windowRow?.transcript_status ?? null,
            render_fingerprint: windowRow?.render_fingerprint ?? null,
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
          render_fingerprint?: string; only_if_missing?: boolean; window_id?: string;
        }>(request);
        const renderFingerprint = typeof body?.render_fingerprint === "string"
          ? body.render_fingerprint.slice(0, 200)
          : null;
        const localClipId = String(body?.local_clip_id ?? "");
        const mediaObjectId = String(body?.media_object_id ?? "");
        if (!localClipId || localClipId.length > 128) return json(400, { error: "invalid clip id" });
        if (!mediaObjectId || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(mediaObjectId)) {
          return json(400, { error: "media_object_id required" });
        }
        // Canonical word shape: { word|w|text, start, end } in SOURCE-relative
        // seconds. Normalize here so Python/TS/SQL agree on one field.
        const rawWords = Array.isArray(body?.words) ? body!.words : [];
        const words = rawWords
          .map((entry) => {
            const wordText = String((entry as Record<string, unknown>)?.word
              ?? (entry as Record<string, unknown>)?.w
              ?? (entry as Record<string, unknown>)?.text
              ?? "").trim();
            return {
              word: wordText,
              start: Number((entry as Record<string, unknown>)?.start),
              end: Number((entry as Record<string, unknown>)?.end),
            };
          })
          .filter((entry) => entry.word.length > 0
            && Number.isFinite(entry.start) && Number.isFinite(entry.end)
            && entry.end > entry.start);
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
                  transcript_status: words.length > 0 ? "ready" : "missing",
                  render_fingerprint: renderFingerprint,
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

          // Idempotent re-registration. Window identity is the row's primary key,
          // never its float bounds: PostgREST renders float8 with 15 significant
          // digits, so a value read back from the state endpoint can be a
          // different double than the one stored (318.04 vs 318.03999999999996)
          // and an exact-equality lookup would insert a duplicate window.
          let windowId: string | null = null;
          let reused = false;
          const explicitWindowId = typeof body?.window_id === "string" ? body.window_id.trim() : "";
          if (UUID_RE.test(explicitWindowId)) {
            const ownedRes = await fetch(
              `${env.SUPABASE_URL}/rest/v1/edit_windows?id=eq.${explicitWindowId}&user_id=eq.${userId}&status=eq.ready&select=id&limit=1`,
              { headers: svc }
            );
            const ownedRows = ownedRes.ok ? (await ownedRes.json() as Array<{ id: string }>) : [];
            if (Array.isArray(ownedRows) && ownedRows.length > 0) {
              windowId = ownedRows[0].id;
              reused = true;
            }
          }
          if (!windowId) {
            const siblingsRes = await fetch(
              `${env.SUPABASE_URL}/rest/v1/edit_windows?user_id=eq.${userId}&local_clip_id=eq.${encodeURIComponent(localClipId)}&status=eq.ready&select=id,window_start,window_end&order=created_at.asc`,
              { headers: svc }
            );
            const siblings = siblingsRes.ok ? (await siblingsRes.json() as Array<{ id: string; window_start: number; window_end: number }>) : [];
            const wantStart = Number(body?.window_start ?? 0);
            const wantEnd = Number(body?.window_end ?? 0);
            // Tolerant bounds match (sub-frame) for first-time staging of a clip
            // whose window row was created from the same local floats.
            const match = (Array.isArray(siblings) ? siblings : []).find(
              (row) => Math.abs(Number(row.window_start) - wantStart) < 0.005
                && Math.abs(Number(row.window_end) - wantEnd) < 0.005
            );
            if (match) {
              windowId = match.id;
              reused = true;
            } else {
              windowId = await insertWindow();
            }
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
            // Reused windows must point at the NEW proxy when the render changed.
            if (reused) {
              const beforeRes = await fetch(
                `${env.SUPABASE_URL}/rest/v1/edit_windows?id=eq.${encodeURIComponent(windowId)}&select=media_object_id`,
                { headers: svc }
              );
              const beforeRows = beforeRes.ok ? (await beforeRes.json() as Array<{ media_object_id: string | null }>) : [];
              const previousMediaId = beforeRows[0]?.media_object_id ?? null;

              const reusePatch = await fetch(
                `${env.SUPABASE_URL}/rest/v1/edit_windows?id=eq.${encodeURIComponent(windowId)}&user_id=eq.${userId}`,
                {
                  method: "PATCH",
                  headers: patchHeaders,
                  body: JSON.stringify({ media_object_id: mediaObjectId, render_fingerprint: renderFingerprint }),
                }
              );
              if (!reusePatch.ok) return json(502, { error: `window media replacement failed: ${reusePatch.status}` });

              // The bytes this window used to own would otherwise stay
              // status='active' forever and keep counting against the quota:
              // one five-second clip accumulated 37 superseded proxies and
              // 1.2 GB. Reclaim only when no other live window still points at
              // the same object, and only for edit_window proxies, so a wrong
              // guess costs a re-stage rather than an unplayable clip.
              if (previousMediaId && previousMediaId !== mediaObjectId) {
                try {
                  const holdersRes = await fetch(
                    `${env.SUPABASE_URL}/rest/v1/edit_windows?media_object_id=eq.${encodeURIComponent(previousMediaId)}&status=eq.ready&id=neq.${encodeURIComponent(windowId)}&select=id&limit=1`,
                    { headers: svc }
                  );
                  const holders = holdersRes.ok ? (await holdersRes.json() as Array<{ id: string }>) : [];
                  if (!Array.isArray(holders) || holders.length === 0) {
                    const old = await selectOne<MediaObj>(env, "media_objects", previousMediaId, "id,user_id,status,kind,staging_key,final_key");
                    if (old && old.user_id === userId && old.kind === "edit_window"
                        && old.status !== "deleted" && old.status !== "deletion_pending") {
                      await rpc(env, "cloud_mark_deletion", { p_media_id: previousMediaId });
                      if (old.final_key) await env.BUCKET.delete(old.final_key);
                      if (old.staging_key) await env.BUCKET.delete(old.staging_key).catch(() => {});
                      await rpc(env, "cloud_confirm_deletion", { p_media_id: previousMediaId });
                    }
                  }
                } catch {
                  // Reclamation is cleanup, never a reason to fail a sync.
                }
              }
            }
            if (windowId && words.length > 0) {
              if (body?.only_if_missing === true) {
                const existingRes = await fetch(
                  `${env.SUPABASE_URL}/rest/v1/window_transcripts?window_id=eq.${encodeURIComponent(windowId)}&select=words&limit=1`,
                  { headers: svc }
                );
                const existingRows = existingRes.ok ? (await existingRes.json() as Array<{ words?: unknown }>) : [];
                const existingWords = Array.isArray(existingRows[0]?.words) ? (existingRows[0]!.words as unknown[]) : [];
                if (existingWords.length > 0) {
                  return json(200, {
                    window_id: windowId, cloud_clip_id: cloudClipId,
                    window_reused: reused, media_linked: true,
                    transcripts_stored: true, transcript_skipped: "existing",
                    transcript_status: "ready",
                  });
                }
              }
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
                // Transcripts seed offline caption editing. Surface the failure
                // honestly and mark readiness failed so the PC retries.
                await fetch(
                  `${env.SUPABASE_URL}/rest/v1/edit_windows?id=eq.${encodeURIComponent(windowId)}`,
                  { method: "PATCH", headers: patchHeaders, body: JSON.stringify({ transcript_status: "failed", transcript_error: `store failed: ${trRes.status}` }) }
                ).catch(() => null);
                return json(200, {
                  window_id: windowId, cloud_clip_id: cloudClipId,
                  window_reused: reused, media_linked: true, transcripts_stored: false,
                  transcript_status: "failed",
                });
              }
              transcriptsStored = true;
              await fetch(
                `${env.SUPABASE_URL}/rest/v1/edit_windows?id=eq.${encodeURIComponent(windowId)}`,
                { method: "PATCH", headers: patchHeaders, body: JSON.stringify({ transcript_status: "ready", transcript_error: null }) }
              ).catch(() => null);
            }
          }
          return json(200, {
            window_id: windowId,
            cloud_clip_id: cloudClipId,
            window_reused: reused,
            media_linked: true,
            transcripts_stored: transcriptsStored,
            transcript_status: transcriptsStored ? "ready" : (words.length > 0 ? "failed" : "missing"),
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

      // Headless PC pre-render sweep: pull higher revisions of project documents
      if (request.method === "GET" && url.pathname === "/sync/pc/timeline") {
        if (!isPcWorkerAuth(env, request)) return json(403, { error: "PC worker auth required" });
        try {
          const res = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&segments_revision=gt.0&select=local_clip_id,project_document,segments_revision,active_state_revision,media_fingerprint,render_fingerprint,rendered_fingerprint,render_stale&order=cloud_updated_at.desc&limit=200`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          const rows = res.ok ? ((await res.json()) as Array<Record<string, unknown>>) : [];
          const timelines = (Array.isArray(rows) ? rows : []).map((r) => ({
            ...r,
            clip_id: r.local_clip_id,
            local_clip_id: r.local_clip_id,
          }));
          return json(200, { status: "ok", timelines, clips: timelines });
        } catch (e) {
          return json(502, { error: `timeline sync failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      // Headless PC verified render completion: confirms R2 upload and updates rendered_fingerprint
      if (request.method === "POST" && url.pathname === "/sync/pc/render-complete") {
        if (!isPcWorkerAuth(env, request)) return json(403, { error: "PC worker auth required" });
        const body = await readJson<{
          local_clip_id?: string;
          clip_id?: string;
          render_fingerprint?: string;
          rendered_fingerprint?: string;
          r2_key?: string;
          byte_count?: number;
          duration?: number;
        }>(request);

        const localClipId = body?.local_clip_id ?? body?.clip_id;
        const renderFp = body?.rendered_fingerprint ?? body?.render_fingerprint;

        if (!localClipId || !renderFp) {
          return json(400, { error: "missing required clip_id / render_fingerprint" });
        }

        // Verify R2 object existence and size if r2_key and byte_count are provided
        if (body?.r2_key && env.BUCKET && typeof body?.byte_count === "number") {
          try {
            const obj = await env.BUCKET.head(body.r2_key);
            if (!obj) {
              return json(404, { error: `rendered object not found in R2: ${body.r2_key}` });
            }
            if (obj.size !== body.byte_count) {
              return json(400, { error: `R2 object size mismatch: expected ${body.byte_count}, got ${obj.size}` });
            }
          } catch (e) {
            return json(502, { error: `R2 verification failed: ${e instanceof Error ? e.message : e}` });
          }
        }

        // Update clips_cloud rendered_fingerprint atomically
        try {
          // Check target clip
          const clipRes = await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?user_id=eq.${userId}&local_clip_id=eq.${encodeURIComponent(localClipId)}&select=id,render_fingerprint&limit=1`,
            { headers: { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` } }
          );
          const clipRows = clipRes.ok ? ((await clipRes.json()) as Array<{ id: string; render_fingerprint: string }>) : [];
          if (!clipRows || clipRows.length === 0) return json(404, { error: "clip not found" });

          const clip = clipRows[0];
          // Check if render_fingerprint matches (split-only cuts preserve fingerprint!)
          const isFresh = clip.render_fingerprint === renderFp;

          await fetch(
            `${env.SUPABASE_URL}/rest/v1/clips_cloud?id=eq.${clip.id}`,
            {
              method: "PATCH",
              headers: {
                apikey: env.SUPABASE_SERVICE_ROLE_KEY,
                Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
                "content-type": "application/json",
              },
              body: JSON.stringify({
                rendered_fingerprint: renderFp,
                render_stale: !isFresh,
                cloud_updated_at: new Date().toISOString(),
              }),
            }
          );

          return json(200, { ok: true, render_stale: !isFresh, rendered_fingerprint: renderFp });
        } catch (e) {
          return json(502, { error: `render commit failed: ${e instanceof Error ? e.message : e}` });
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

      // ---- Scoped PC OAuth / YouTube / Gemini Broker (worker-token + owner allowlist ONLY) ----
      // PC never holds SUPABASE_SERVICE_ROLE_KEY, DISPATCH_TOKEN_VAULT_KEY, raw refresh_tokens,
      // YOUTUBE_CLIENT_SECRET, or raw user Gemini API keys.
      if (request.method === "POST" && url.pathname === "/sync/pc/oauth/access-token") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:oauth:${userId}`, 120, 60_000);
        if (!rl.allowed) {
          return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        }
        const body = await readJson<{
          provider?: unknown;
          account_id?: unknown;
          force_refresh?: unknown;
          require_source_vault?: unknown;
        }>(request);
        const provider = typeof body?.provider === "string" ? body.provider.trim() : "";
        const accountId = typeof body?.account_id === "string" ? body.account_id.trim() : "";
        if (!provider) return json(400, { error: "provider is required" });
        try {
          const tok = await getValidProviderToken(env, {
            userId,
            provider,
            accountId,
            forceRefresh: body?.force_refresh === true,
            requireSourceVault: body?.require_source_vault !== false,
          });
          if (!tok) {
            return json(404, { error: `No connected ${provider} account found`, code: "not_connected" });
          }
          // Strictly strip refresh_token, user_access_token, client_secret, and any vault secrets.
          return json(200, {
            ok: true,
            provider: tok.provider,
            account_id: tok.accountId,
            row_id: tok.rowId,
            access_token: tok.accessToken,
            expires_at: tok.expiresAtIso,
            ttl_seconds: tok.expiresAtMs !== null ? Math.max(0, Math.floor((tok.expiresAtMs - Date.now()) / 1000)) : null,
            account_name: tok.accountName,
            account_email: tok.accountEmail,
            page_id: typeof tok.payload.page_id === "string" ? tok.payload.page_id : null,
            ig_user_id: typeof tok.payload.ig_user_id === "string" ? tok.payload.ig_user_id : null,
            author_urn: typeof tok.payload.author_urn === "string" ? tok.payload.author_urn : null,
            api_base: typeof tok.payload.api_base === "string" ? tok.payload.api_base : null,
            auth_mode: typeof tok.payload.auth_mode === "string" ? tok.payload.auth_mode : null,
            username: typeof tok.payload.username === "string" ? tok.payload.username : null,
            open_id: typeof tok.payload.open_id === "string" ? tok.payload.open_id : null,
          });
        } catch (err) {
          if (err instanceof ProviderAuthError) {
            return json(err.status, {
              error: err.message,
              code: err.code,
              needs_reauth: err.code === "needs_reauth",
              provider: err.provider,
              account_id: err.accountId ?? null,
            });
          }
          return json(502, { error: `oauth broker failed: ${err instanceof Error ? err.message : String(err)}` });
        }
      }

      if (request.method === "POST" && url.pathname === "/sync/pc/youtube/channel") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:yt:${userId}`, 120, 60_000);
        if (!rl.allowed) {
          return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        }
        const body = await readJson<{ channel_id?: string | null }>(request);
        const requestedChannelId = typeof body?.channel_id === "string" ? body.channel_id.trim() : "";
        const ytChannelRe = /^(UC[A-Za-z0-9_-]{6,}|ch-[A-Za-z0-9_-]+|channel-[A-Za-z0-9_-]+)$/;
        const lookupUrl = new URL(`${env.SUPABASE_URL}/rest/v1/connected_accounts`);
        lookupUrl.searchParams.set("user_id", `eq.${userId}`);
        lookupUrl.searchParams.set("provider", "eq.youtube");
        lookupUrl.searchParams.set("select", "id,account_id,account_name,account_email,metadata,is_verified");
        const res = await fetch(lookupUrl.toString(), {
          headers: {
            apikey: env.SUPABASE_SERVICE_ROLE_KEY,
            Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
          },
          signal: AbortSignal.timeout(8000),
        });
        if (!res.ok) return json(502, { error: `youtube channel lookup failed: ${res.status}` });
        const rows = (await res.json()) as Array<{
          id?: string;
          account_id?: string | null;
          account_name?: string | null;
          account_email?: string | null;
          metadata?: Record<string, unknown> | null;
          is_verified?: boolean | null;
        }>;
        const resolveChannelId = (r: (typeof rows)[number]): string | null => {
          const raw = typeof r.account_id === "string" ? r.account_id.trim() : "";
          const metaCid = r.metadata && typeof r.metadata.channel_id === "string" ? r.metadata.channel_id.trim() : "";
          if (raw && !raw.includes("@") && !UUID_RE.test(raw) && ytChannelRe.test(raw)) return raw;
          if (metaCid && !metaCid.includes("@") && !UUID_RE.test(metaCid) && ytChannelRe.test(metaCid)) return metaCid;
          return null;
        };
        const valid = rows
          .filter((r) => r.is_verified !== false && resolveChannelId(r) !== null)
          .map((r) => ({
            row_id: String(r.id || ""),
            channel_id: String(resolveChannelId(r)),
            channel_title: String(r.account_name || ""),
            account_email: String(r.account_email || ""),
            needs_reauth: r.metadata?.needs_reauth === true,
          }));
        if (requestedChannelId) {
          const hit = valid.find((v) => v.channel_id === requestedChannelId);
          if (hit) {
            return json(200, { status: hit.needs_reauth ? "needs_reauth" : "connected", user_id: userId, ...hit, reason: "explicit_channel" });
          }
          return json(200, { status: "not_found", user_id: userId, channel_id: null, reason: "channel_not_connected_to_user" });
        }
        if (valid.length === 1) {
          const hit = valid[0];
          return json(200, { status: hit.needs_reauth ? "needs_reauth" : "connected", user_id: userId, ...hit, reason: "single_connected_channel" });
        }
        if (valid.length > 1) {
          return json(200, {
            status: "ambiguous_owner",
            user_id: userId,
            channel_id: null,
            channels: valid,
            reason: "multiple_channels_require_explicit_selection",
          });
        }
        if (rows.length > 0) {
          const rawLegacyId = String(rows[0].account_id || "").trim();
          const legacyType = rawLegacyId.includes("@") ? "email" : (UUID_RE.test(rawLegacyId) ? "UUID" : "legacy");
          return json(200, {
            status: "legacy_reauth_required",
            user_id: userId,
            channel_id: rawLegacyId || null,
            account_name: String(rows[0].account_name || ""),
            account_email: String(rows[0].account_email || ""),
            row_id: String(rows[0].id || ""),
            legacy_id_type: legacyType,
            reason: "legacy_account_needs_reconnect",
          });
        }
        return json(200, { status: "not_found", user_id: userId, channel_id: null, reason: "no_connected_youtube_account" });
      }

      if (request.method === "POST" && url.pathname === "/sync/pc/youtube/reconcile") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:yt:${userId}`, 120, 60_000);
        if (!rl.allowed) return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        const body = await readJson<{ account_id?: string; idempotency_key?: string; max_results?: number }>(request);
        const accountId = typeof body?.account_id === "string" ? body.account_id.trim() : "";
        const idempotencyKey = typeof body?.idempotency_key === "string" ? body.idempotency_key.trim() : "";
        if (!idempotencyKey) return json(400, { error: "idempotency_key required" });
        const maxResults = Math.min(10, Math.max(1, Number(body?.max_results ?? 5) || 5));
        try {
          let tok = await getValidProviderToken(env, { userId, provider: "youtube", accountId, requireSourceVault: false });
          if (!tok) return json(404, { error: "No connected YouTube account", code: "not_connected" });
          const searchUrl = new URL("https://www.googleapis.com/youtube/v3/search");
          searchUrl.searchParams.set("part", "snippet");
          searchUrl.searchParams.set("forMine", "true");
          searchUrl.searchParams.set("type", "video");
          searchUrl.searchParams.set("q", `dispatch_idempotency_key:${idempotencyKey}`);
          searchUrl.searchParams.set("maxResults", String(maxResults));
          let ytRes = await fetch(searchUrl.toString(), {
            headers: { Authorization: `Bearer ${tok.accessToken}` },
            signal: AbortSignal.timeout(8000),
          });
          if (ytRes.status === 401) {
            tok = await getValidProviderToken(env, { userId, provider: "youtube", accountId, forceRefresh: true, requireSourceVault: false });
            if (tok) {
              ytRes = await fetch(searchUrl.toString(), {
                headers: { Authorization: `Bearer ${tok.accessToken}` },
                signal: AbortSignal.timeout(8000),
              });
            }
          }
          if (!ytRes.ok) {
            return json(502, { error: `youtube reconcile upstream failed: ${ytRes.status}` });
          }
          const ytData = (await ytRes.json()) as { items?: unknown[] };
          return json(200, { ok: true, items: Array.isArray(ytData.items) ? ytData.items : [] });
        } catch (err) {
          if (err instanceof ProviderAuthError) {
            return json(err.status, { error: err.message, code: err.code, needs_reauth: err.code === "needs_reauth" });
          }
          return json(502, { error: `youtube reconcile failed: ${err instanceof Error ? err.message : String(err)}` });
        }
      }

      if (request.method === "POST" && url.pathname === "/sync/pc/youtube/status") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:yt:${userId}`, 120, 60_000);
        if (!rl.allowed) return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        const body = await readJson<{ account_id?: string | null; video_ids?: string[] }>(request);
        const ids = Array.isArray(body?.video_ids)
          ? body.video_ids.map((v) => String(v || "").trim()).filter((v) => /^[A-Za-z0-9_-]{6,20}$/.test(v)).slice(0, 50)
          : [];
        if (ids.length === 0) return json(200, { ok: true, videos: {} });
        const accountId = typeof body?.account_id === "string" ? body.account_id.trim() : "";
        try {
          let tok = await getValidProviderToken(env, { userId, provider: "youtube", accountId, requireSourceVault: false });
          if (!tok) return json(404, { error: "No connected YouTube account", code: "not_connected" });
          const videosUrl = new URL("https://www.googleapis.com/youtube/v3/videos");
          videosUrl.searchParams.set("part", "snippet,status,processingDetails,contentDetails");
          videosUrl.searchParams.set("id", ids.join(","));
          let ytRes = await fetch(videosUrl.toString(), {
            headers: { Authorization: `Bearer ${tok.accessToken}` },
            signal: AbortSignal.timeout(8000),
          });
          if (ytRes.status === 401) {
            tok = await getValidProviderToken(env, { userId, provider: "youtube", accountId, forceRefresh: true, requireSourceVault: false });
            if (tok) {
              ytRes = await fetch(videosUrl.toString(), {
                headers: { Authorization: `Bearer ${tok.accessToken}` },
                signal: AbortSignal.timeout(8000),
              });
            }
          }
          if (!ytRes.ok) {
            return json(502, { error: `youtube videos status failed: ${ytRes.status}` });
          }
          const ytJson = (await ytRes.json()) as { items?: Array<Record<string, unknown>> };
          const videos: Record<string, unknown> = {};
          for (const item of ytJson.items || []) {
            const vid = typeof item.id === "string" ? item.id : "";
            if (!vid) continue;
            const statusObj = (item.status || {}) as Record<string, unknown>;
            const procObj = (item.processingDetails || {}) as Record<string, unknown>;
            const snippetObj = (item.snippet || {}) as Record<string, unknown>;
            const prog = (procObj.processingProgress || {}) as Record<string, unknown>;
            const total = Number(prog.partsTotal || 0);
            const processed = Number(prog.partsProcessed || 0);
            const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : null;
            videos[vid] = {
              video_id: vid,
              privacy_status: statusObj.privacyStatus ?? null,
              upload_status: statusObj.uploadStatus ?? null,
              rejection_reason: statusObj.rejectionReason ?? null,
              failure_reason: statusObj.failureReason ?? null,
              processing_status: procObj.processingStatus ?? null,
              processing_progress: pct,
              published_at: snippetObj.publishedAt ?? null,
              title: snippetObj.title ?? null,
              status: statusObj,
              processingDetails: procObj,
              contentDetails: item.contentDetails ?? {},
            };
          }
          return json(200, { ok: true, videos });
        } catch (err) {
          if (err instanceof ProviderAuthError) {
            return json(err.status, { error: err.message, code: err.code, needs_reauth: err.code === "needs_reauth" });
          }
          return json(502, { error: `youtube status failed: ${err instanceof Error ? err.message : String(err)}` });
        }
      }

      if (request.method === "POST" && url.pathname === "/sync/pc/youtube/upload-session") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:yt:${userId}`, 60, 60_000);
        if (!rl.allowed) return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        const body = await readJson<{
          account_id?: string;
          metadata?: Record<string, unknown>;
          content_length?: number;
          content_type?: string;
        }>(request);
        const accountId = typeof body?.account_id === "string" ? body.account_id.trim() : "";
        if (!body?.metadata || typeof body.metadata !== "object") {
          return json(400, { error: "metadata object is required" });
        }
        try {
          const tok = await getValidProviderToken(env, { userId, provider: "youtube", accountId, requireSourceVault: false });
          if (!tok) return json(404, { error: "No connected YouTube account", code: "not_connected" });
          const reqHeaders: Record<string, string> = {
            Authorization: `Bearer ${tok.accessToken}`,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": typeof body.content_type === "string" ? body.content_type : "video/mp4",
          };
          if (typeof body.content_length === "number" && body.content_length > 0) {
            reqHeaders["X-Upload-Content-Length"] = String(Math.round(body.content_length));
          }
          const initRes = await fetch(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
            {
              method: "POST",
              headers: reqHeaders,
              body: JSON.stringify(body.metadata),
              signal: AbortSignal.timeout(12000),
            },
          );
          if (!initRes.ok) {
            return json(502, { error: `youtube resumable init failed: ${initRes.status}` });
          }
          const uploadUrl = initRes.headers.get("Location") || initRes.headers.get("location");
          if (!uploadUrl) return json(502, { error: "youtube resumable init missing Location header" });
          return json(200, { ok: true, upload_url: uploadUrl });
        } catch (err) {
          if (err instanceof ProviderAuthError) {
            return json(err.status, { error: err.message, code: err.code, needs_reauth: err.code === "needs_reauth" });
          }
          return json(502, { error: `youtube upload session failed: ${err instanceof Error ? err.message : String(err)}` });
        }
      }

      if (request.method === "POST" && url.pathname === "/sync/pc/youtube/snippet") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:yt:${userId}`, 60, 60_000);
        if (!rl.allowed) return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        const body = await readJson<{
          account_id?: string;
          video_id?: string;
          title?: string;
          description?: string;
          category_id?: string;
        }>(request);
        const videoId = typeof body?.video_id === "string" ? body.video_id.trim() : "";
        if (!/^[A-Za-z0-9_-]{6,20}$/.test(videoId) || typeof body?.title !== "string" || !body.title.trim()) {
          return json(400, { error: "valid video_id and title are required" });
        }
        const accountId = typeof body?.account_id === "string" ? body.account_id.trim() : "";
        try {
          const tok = await getValidProviderToken(env, { userId, provider: "youtube", accountId, requireSourceVault: false });
          if (!tok) return json(404, { error: "No connected YouTube account", code: "not_connected" });
          const authHdr = { Authorization: `Bearer ${tok.accessToken}` };
          const curRes = await fetch(`https://www.googleapis.com/youtube/v3/videos?part=snippet&id=${encodeURIComponent(videoId)}`, {
            headers: authHdr,
            signal: AbortSignal.timeout(8000),
          });
          if (!curRes.ok) return json(502, { error: `youtube snippet fetch failed: ${curRes.status}` });
          const curJson = (await curRes.json()) as { items?: Array<{ snippet?: Record<string, unknown> }> };
          const existing = curJson.items?.[0]?.snippet;
          if (!existing) return json(404, { error: "video not found on YouTube" });
          const updatedSnippet = {
            ...existing,
            title: body.title.trim().slice(0, 100),
            description: typeof body.description === "string" ? body.description.slice(0, 5000) : (existing.description ?? ""),
            categoryId: typeof body.category_id === "string" && body.category_id ? body.category_id : (existing.categoryId ?? "22"),
          };
          const putRes = await fetch("https://www.googleapis.com/youtube/v3/videos?part=snippet", {
            method: "PUT",
            headers: { ...authHdr, "Content-Type": "application/json" },
            body: JSON.stringify({ id: videoId, snippet: updatedSnippet }),
            signal: AbortSignal.timeout(10000),
          });
          if (!putRes.ok) return json(502, { error: `youtube snippet update failed: ${putRes.status}` });
          return json(200, { ok: true, video_id: videoId });
        } catch (err) {
          if (err instanceof ProviderAuthError) {
            return json(err.status, { error: err.message, code: err.code, needs_reauth: err.code === "needs_reauth" });
          }
          return json(502, { error: `youtube snippet failed: ${err instanceof Error ? err.message : String(err)}` });
        }
      }

      if (request.method === "POST" && url.pathname === "/sync/pc/disconnect") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        try {
          await rpcRaw(env, "cloud_delete_refresh_token", { p_user_id: userId });
          for (const k of providerTokenCache.keys()) {
            if (k.startsWith(`${userId}:`)) providerTokenCache.delete(k);
          }
          return json(200, { ok: true });
        } catch (e) {
          return json(502, { error: `disconnect failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (request.method === "GET" && url.pathname === "/sync/pc/gemini/status") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:gemini:${userId}`, 120, 60_000);
        if (!rl.allowed) return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        try {
          const { apiKey, keyPrefix } = await getDecryptedUserGeminiKey(env, userId);
          return json(200, { configured: Boolean(apiKey), key_prefix: keyPrefix });
        } catch (e) {
          return json(502, { error: `gemini status check failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (request.method === "POST" && url.pathname === "/sync/pc/gemini/upload") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:gemini:${userId}`, 60, 60_000);
        if (!rl.allowed) return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        const body = await readJson<{
          mime_type?: string;
          num_bytes?: number;
          display_name?: string;
        }>(request);
        const mimeType = typeof body?.mime_type === "string" && body.mime_type.trim() ? body.mime_type.trim() : "video/mp4";
        const numBytes = Number(body?.num_bytes ?? 0);
        if (!Number.isFinite(numBytes) || numBytes <= 0) {
          return json(400, { error: "num_bytes > 0 is required" });
        }
        try {
          const { apiKey } = await getDecryptedUserGeminiKey(env, userId);
          if (!apiKey) return json(404, { error: "Gemini key not configured", code: "gemini_key_missing" });
          const startRes = await fetch("https://generativelanguage.googleapis.com/upload/v1beta/files?uploadType=resumable", {
            method: "POST",
            headers: {
              "x-goog-api-key": apiKey,
              "X-Goog-Upload-Protocol": "resumable",
              "X-Goog-Upload-Command": "start",
              "X-Goog-Upload-Header-Content-Length": String(Math.round(numBytes)),
              "X-Goog-Upload-Header-Content-Type": mimeType,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ file: { display_name: typeof body?.display_name === "string" ? body.display_name.slice(0, 128) : "dispatch_media" } }),
            signal: AbortSignal.timeout(12000),
          });
          if (!startRes.ok) {
            return json(502, { error: `gemini resumable upload init failed: ${startRes.status}` });
          }
          const uploadUrl = startRes.headers.get("x-goog-upload-url") || startRes.headers.get("X-Goog-Upload-URL");
          if (!uploadUrl) return json(502, { error: "gemini upload init missing upload url" });
          return json(200, { ok: true, upload_url: uploadUrl });
        } catch (e) {
          return json(502, { error: `gemini upload init failed: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (request.method === "GET" && url.pathname === "/sync/pc/gemini/file-status") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:gemini:${userId}`, 120, 60_000);
        if (!rl.allowed) return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        const fileName = (url.searchParams.get("name") || "").trim();
        if (!/^files\/[a-zA-Z0-9._-]+$/.test(fileName)) {
          return json(400, { error: "invalid gemini file name" });
        }
        try {
          const { apiKey } = await getDecryptedUserGeminiKey(env, userId);
          if (!apiKey) return json(404, { error: "Gemini key not configured", code: "gemini_key_missing" });
          const gRes = await fetch(`https://generativelanguage.googleapis.com/v1beta/${fileName}`, {
            headers: { "x-goog-api-key": apiKey },
            signal: AbortSignal.timeout(8000),
          });
          if (!gRes.ok) return json(502, { error: `gemini file status failed: ${gRes.status}` });
          return json(200, await gRes.json());
        } catch (e) {
          return json(502, { error: `gemini file status error: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (request.method === "DELETE" && url.pathname === "/sync/pc/gemini/file") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:gemini:${userId}`, 60, 60_000);
        if (!rl.allowed) return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        const fileName = (url.searchParams.get("name") || "").trim();
        if (!/^files\/[a-zA-Z0-9._-]+$/.test(fileName)) {
          return json(400, { error: "invalid gemini file name" });
        }
        try {
          const { apiKey } = await getDecryptedUserGeminiKey(env, userId);
          if (!apiKey) return json(404, { error: "Gemini key not configured", code: "gemini_key_missing" });
          const delRes = await fetch(`https://generativelanguage.googleapis.com/v1beta/${fileName}`, {
            method: "DELETE",
            headers: { "x-goog-api-key": apiKey },
            signal: AbortSignal.timeout(8000),
          });
          return json(delRes.ok ? 200 : 502, { ok: delRes.ok });
        } catch (e) {
          return json(502, { error: `gemini file delete error: ${e instanceof Error ? e.message : e}` });
        }
      }

      if (request.method === "POST" && url.pathname === "/sync/pc/gemini/generate") {
        if (!isPcWorkerAuth(env, request) || !request.headers.get("X-Dispatch-User-Id")) {
          return json(403, { error: "PC worker auth required" });
        }
        const rl = checkPcBrokerRateLimit(`pc:gemini:${userId}`, 60, 60_000);
        if (!rl.allowed) return json(429, { error: "rate limit exceeded", retry_after: rl.retryAfterSeconds });
        const body = await readJson<{
          model?: string;
          contents?: unknown;
          generationConfig?: unknown;
          systemInstruction?: unknown;
          safetySettings?: unknown;
          tools?: unknown;
        }>(request);
        if (!body?.contents) return json(400, { error: "contents is required" });
        const model = typeof body.model === "string" && /^[a-zA-Z0-9._-]{1,80}$/.test(body.model.trim())
          ? body.model.trim()
          : "gemini-2.5-flash";
        try {
          const { apiKey } = await getDecryptedUserGeminiKey(env, userId);
          if (!apiKey) return json(404, { error: "Gemini key not configured", code: "gemini_key_missing" });
          const payload: Record<string, unknown> = { contents: body.contents };
          if (body.generationConfig !== undefined) payload.generationConfig = body.generationConfig;
          if (body.systemInstruction !== undefined) payload.systemInstruction = body.systemInstruction;
          if (body.safetySettings !== undefined) payload.safetySettings = body.safetySettings;
          if (body.tools !== undefined) payload.tools = body.tools;
          const gRes = await fetch(
            `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`,
            {
              method: "POST",
              headers: {
                "x-goog-api-key": apiKey,
                "Content-Type": "application/json",
              },
              body: JSON.stringify(payload),
              signal: AbortSignal.timeout(45000),
            },
          );
          const respJson = (await gRes.json().catch(() => ({}))) as Record<string, unknown>;
          if (!gRes.ok) {
            return json(gRes.status >= 400 && gRes.status < 500 ? gRes.status : 502, {
              error: `gemini generateContent failed: ${gRes.status}`,
              upstream: respJson,
            });
          }
          return json(200, respJson);
        } catch (e) {
          return json(502, { error: `gemini proxy error: ${e instanceof Error ? e.message : e}` });
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
      const isOauth = typeof url !== "undefined" && url.pathname.startsWith("/oauth");
      const wantsHtml = request.headers.get("accept")?.includes("text/html");
      if (isOauth && wantsHtml) {
        return renderOAuthResultHtml("Temporary Service Error", (e instanceof Error ? e.message : String(e)) || "An unexpected error occurred. Please retry from Dispatch.", true);
      }
      return json(500, { error: `worker error: ${e instanceof Error ? e.message : String(e)}` }, "*");
    }
  },

  async scheduled(_event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    // Scheduled cleanup (see [triggers] in wrangler.toml): abandoned staging,
    // promotion leftovers, and the staging-watch sweep run unattended.
    ctx.waitUntil(runCleanup(env).then((r) => console.log(`scheduled cleanup: ${r.status}`)));
    ctx.waitUntil(refreshInstagramLoginTokens(env));
    ctx.waitUntil(drainPublishingQueue(env));
  },
};
