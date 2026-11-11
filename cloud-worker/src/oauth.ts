/**
 * Multi-Platform Social OAuth Handlers (YouTube, Instagram, Facebook, LinkedIn, X/Twitter).
 * Confidential client secrets remain on the Cloudflare Worker.
 * All tokens are encrypted using AES-256-GCM (vault key = Worker secret TOKEN_VAULT_KEY)
 * before being stored into Supabase connected_accounts.
 */

const GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth";
const GOOGLE_TOKEN = "https://oauth2.googleapis.com/token";
const GOOGLE_SCOPES = [
  "https://www.googleapis.com/auth/youtube",
  "https://www.googleapis.com/auth/youtube.force-ssl",
  "https://www.googleapis.com/auth/userinfo.email",
];

export interface OAuthEnv extends Record<string, unknown> {
  SUPABASE_URL: string;
  SUPABASE_SERVICE_ROLE_KEY: string;
  TOKEN_VAULT_KEY: string; // 32-byte base64 AES-256 key
  YOUTUBE_CLIENT_ID: string;
  YOUTUBE_CLIENT_SECRET: string;
  OAUTH_REDIRECT_URI: string; // https://<worker>/oauth/google/callback
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

export function b64url(bytes: Uint8Array): string {
  let bin = "";
  bytes.forEach(b => (bin += String.fromCharCode(b)));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function fromB64url(b64urlStr: string): Uint8Array {
  const pad = b64urlStr.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64urlStr.length % 4)) % 4);
  const bin = atob(pad);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}

async function aesKey(vaultKey: string): Promise<CryptoKey> {
  const trimmed = (vaultKey || "").trim();
  if (!trimmed) {
    throw new Error("TOKEN_VAULT_KEY is not configured");
  }
  let raw: Uint8Array | null = null;

  // 1. Base64 (AES-256 only: exactly 32 bytes)
  try {
    const pad = trimmed.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (trimmed.length % 4)) % 4);
    const bin = atob(pad);
    if (bin.length === 32) {
      raw = Uint8Array.from(bin, c => c.charCodeAt(0));
    }
  } catch {
    // not valid base64
  }

  // 2. Hex (AES-256 only: exactly 64 hex characters)
  if (!raw && /^[0-9a-fA-F]{64}$/.test(trimmed)) {
    const bytes = new Uint8Array(32);
    for (let i = 0; i < 64; i += 2) {
      bytes[i / 2] = parseInt(trimmed.substring(i, i + 2), 16);
    }
    raw = bytes;
  }

  // 3. Configured passphrase: derive a 256-bit key from the operator's chosen
  // secret. (The old PUBLIC constant fallback is what was removed; deriving
  // from an explicitly configured value preserves existing deployments.)
  if (!raw && trimmed) {
    const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(trimmed));
    raw = new Uint8Array(hash);
  }

  if (!raw) {
    throw new Error("TOKEN_VAULT_KEY must be a configured secret (32-byte base64, 64-hex, or passphrase)");
  }

  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

export interface VaultDecryptResult {
  plaintext: string;
  version: 1 | 2;
  needsMigration: boolean;
}

const VAULT_ENCODER = new TextEncoder();
const VAULT_DECODER = new TextDecoder();

/** Canonical Additional Authenticated Data (AAD) for vault_v: 2 ciphertexts. */
export function buildVaultAad(userId: string, provider: string, accountId: string): string {
  const u = String(userId || "").trim();
  const p = String(provider || "").trim().toLowerCase();
  const a = String(accountId || "").trim();
  if (!u || !p || !a) {
    throw new Error("Vault AAD requires non-empty userId, provider, and accountId");
  }
  return `${u}|${p}|${a}`;
}

/** AES-256-GCM v2 envelope bound to AAD (`${user_id}|${provider}|${account_id}`). */
export async function encryptTokenV2(
  vaultKeyB64: string,
  plaintext: string,
  aad: string,
): Promise<string> {
  if (!aad || !aad.trim()) {
    throw new Error("encryptTokenV2 requires non-empty AAD");
  }
  const key = await aesKey(vaultKeyB64);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: VAULT_ENCODER.encode(aad) },
    key,
    VAULT_ENCODER.encode(plaintext),
  );
  return JSON.stringify({
    vault_v: 2,
    iv: b64url(iv),
    ct: b64url(new Uint8Array(ct)),
  });
}

/** AES-256-GCM; uses vault_v: 2 when AAD is supplied, legacy v1 otherwise. */
export async function encryptToken(
  vaultKeyB64: string,
  plaintext: string,
  aad?: string,
): Promise<string> {
  if (typeof aad === "string" && aad.trim()) {
    return encryptTokenV2(vaultKeyB64, plaintext, aad);
  }
  const key = await aesKey(vaultKeyB64);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    VAULT_ENCODER.encode(plaintext),
  );
  return b64url(new Uint8Array([...iv, ...new Uint8Array(ct)]));
}

/**
 * Dual-read AES-256-GCM decryptor.
 * - `vault_v: 2` envelopes strictly require matching non-empty `expectedAad` (no downgrade).
 * - Legacy v1 payloads decrypt without AAD and signal `needsMigration: true` when `expectedAad` is supplied.
 */
export async function decryptTokenWithMigration(
  vaultKeyB64: string,
  ciphertext: string,
  expectedAad?: string,
): Promise<VaultDecryptResult> {
  const raw = String(ciphertext || "").trim();
  if (!raw) {
    throw new Error("Empty vault ciphertext");
  }
  const key = await aesKey(vaultKeyB64);

  if (raw.startsWith("{")) {
    let parsed: { vault_v?: unknown; iv?: unknown; ct?: unknown };
    try {
      parsed = JSON.parse(raw) as { vault_v?: unknown; iv?: unknown; ct?: unknown };
    } catch {
      throw new Error("Malformed vault envelope");
    }
    if (parsed.vault_v !== 2 || typeof parsed.iv !== "string" || typeof parsed.ct !== "string") {
      throw new Error(`Unsupported vault envelope version: ${String(parsed.vault_v)}`);
    }
    if (!expectedAad || !expectedAad.trim()) {
      throw new Error("Missing required AAD for vault_v: 2 decryption");
    }
    const iv = fromB64url(parsed.iv);
    const ct = fromB64url(parsed.ct);
    const pt = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv, additionalData: VAULT_ENCODER.encode(expectedAad) },
      key,
      ct,
    );
    return {
      plaintext: VAULT_DECODER.decode(pt),
      version: 2,
      needsMigration: false,
    };
  }

  const data = fromB64url(raw);
  if (data.length <= 12) {
    throw new Error("Invalid legacy vault ciphertext");
  }
  const iv = data.slice(0, 12);
  const ct = data.slice(12);
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct);
  return {
    plaintext: VAULT_DECODER.decode(pt),
    version: 1,
    needsMigration: Boolean(expectedAad && expectedAad.trim()),
  };
}

/** AES-256-GCM decrypt (enforces expectedAad for vault_v: 2 envelopes). */
export async function decryptToken(
  vaultKeyB64: string,
  ciphertextB64url: string,
  expectedAad?: string,
): Promise<string> {
  const res = await decryptTokenWithMigration(vaultKeyB64, ciphertextB64url, expectedAad);
  return res.plaintext;
}

export async function sha256B64Url(str: string): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(str));
  return b64url(new Uint8Array(hash));
}

export function generateRandomString(len = 64): string {
  const bytes = crypto.getRandomValues(new Uint8Array(len));
  return b64url(bytes);
}

/** Deterministic JSON parse helpers with strict validation. */
export function validateTokenResponse(body: Record<string, unknown>): string {
  const rt = typeof body.refresh_token === "string" ? body.refresh_token : "";
  if (!rt) return "no refresh_token in token response";
  if (rt.length < 20 || rt.length > 512) return "refresh_token size implausible";
  return "";
}

export function buildAuthorizeUrl(clientId: string, redirectUri: string, state: string): string {
  const q = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: "code",
    access_type: "offline",
    prompt: "consent select_account",
    include_granted_scopes: "true",
    scope: GOOGLE_SCOPES.join(" "),
    state,
  });
  return `${GOOGLE_AUTH}?${q.toString()}`;
}

/**
 * Instagram API with Instagram Login: the user signs in with Instagram itself.
 * No Facebook account, no Facebook Page, and no Page permissions are involved.
 */
export const INSTAGRAM_LOGIN_SCOPES = [
  "instagram_business_basic",
  "instagram_business_content_publish",
] as const;

export function buildInstagramLoginAuthorizeUrl(clientId: string, redirectUri: string, state: string): string {
  const q = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: INSTAGRAM_LOGIN_SCOPES.join(","),
    state,
  });
  return `https://www.instagram.com/oauth/authorize?${q.toString()}`;
}

export function buildFacebookAuthorizeUrl(clientId: string, redirectUri: string, state: string): string {
  const q = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: "pages_show_list,pages_read_engagement,pages_manage_posts",
    state,
  });
  return `https://www.facebook.com/v21.0/dialog/oauth?${q.toString()}`;
}

export function buildLinkedInAuthorizeUrl(clientId: string, redirectUri: string, state: string): string {
  const q = new URLSearchParams({
    response_type: "code",
    client_id: clientId,
    redirect_uri: redirectUri,
    state,
    scope: "openid profile email w_member_social",
  });
  return `https://www.linkedin.com/oauth/v2/authorization?${q.toString()}`;
}

export function buildTwitterAuthorizeUrl(clientId: string, redirectUri: string, state: string, codeChallenge: string): string {
  const q = new URLSearchParams({
    response_type: "code",
    client_id: clientId,
    redirect_uri: redirectUri,
    scope: "tweet.read tweet.write users.read offline.access",
    state,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
  });
  return `https://twitter.com/i/oauth2/authorize?${q.toString()}`;
}

export function escapeHtml(value: string): string {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch] as string));
}

export function renderOAuthResultHtml(title: string, message: string, isError = false, provider = ""): Response {
  const accentColor = isError ? "#EF4444" : "#22C55E";
  const iconSvg = isError
    ? `<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="${accentColor}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`
    : `<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="${accentColor}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`;

  const status = isError ? "error" : "success";
  const qParams = new URLSearchParams({
    status,
    provider: provider || "",
    message: message || title
  }).toString();

  const directDispatchUrl = `dispatch://oauth/callback?${qParams}`;
  const intentDispatchUrl = `intent://oauth/callback?${qParams}#Intent;scheme=dispatch;package=com.resolvia.dispatch;end`;

  const directDispatchJs = JSON.stringify(directDispatchUrl).replace(/</g, "\\u003c");
  const intentDispatchJs = JSON.stringify(intentDispatchUrl).replace(/</g, "\\u003c");

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title)} - Dispatch</title>
  <style>
    body {
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #161616;
      color: #F5F5F5;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
    }
    .card {
      background: #1C1C1C;
      border: 1px solid #2E2E2E;
      border-radius: 20px;
      padding: 32px 24px;
      max-width: 380px;
      margin: 20px;
      text-align: center;
      box-shadow: 0 12px 30px rgba(0,0,0,0.5);
    }
    .icon {
      margin-bottom: 20px;
      display: inline-flex;
      padding: 12px;
      border-radius: 50%;
      background: #232323;
    }
    h2 {
      margin: 0 0 12px 0;
      font-size: 20px;
      font-weight: 600;
    }
    p {
      margin: 0 0 24px 0;
      font-size: 14px;
      color: #A3A3A3;
      line-height: 1.5;
    }
    .btn {
      display: inline-block;
      width: 100%;
      box-sizing: border-box;
      padding: 12px;
      background: #2563EB;
      color: #FFFFFF;
      font-weight: 500;
      font-size: 14px;
      border-radius: 12px;
      text-decoration: none;
      cursor: pointer;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">${iconSvg}</div>
    <h2>${escapeHtml(title)}</h2>
    <p>${escapeHtml(message)}</p>
    <a href="${escapeHtml(directDispatchUrl)}" id="returnBtn" class="btn">Return to Dispatch</a>
    <p style="margin:14px 0 0 0;font-size:12px;">If the app does not open automatically, tap the button above or switch back using your recent apps.</p>
  </div>
  <script>
    (function() {
      setTimeout(function() {
        try {
          window.location.href = ${directDispatchJs};
        } catch (e) {
          try {
            window.location.href = ${intentDispatchJs};
          } catch (_) {}
        }
      }, 700);
    })();
  </script>
</body>
</html>`;

  return new Response(html, {
    status: isError ? 400 : 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "x-content-type-options": "nosniff",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
    },
  });
}
