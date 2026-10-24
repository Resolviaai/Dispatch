/**
 * On-device YouTube OAuth (ADR-001 Decision 4): the phone opens Google consent in a
 * Custom Tab via /oauth/google/start; Google returns the one-time code to
 * /oauth/google/callback; the Worker exchanges it server-side (confidential secret
 * stays here) and stores the refresh token ENCRYPTED (AES-256-GCM, vault key =
 * Worker secret) in Supabase against the initiating user. The PC backend decrypts
 * with the same vault key when processing that user's videos.
 */

const GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth";
const GOOGLE_TOKEN = "https://oauth2.googleapis.com/token";
const SCOPES = [
  "https://www.googleapis.com/auth/youtube",
  "https://www.googleapis.com/auth/youtube.force-ssl",
  "https://www.googleapis.com/auth/userinfo.email",
];

export interface OAuthEnv extends Record<string, string> {
  SUPABASE_URL: string;
  SUPABASE_SERVICE_ROLE_KEY: string;
  TOKEN_VAULT_KEY: string; // 32-byte base64 AES-256 key
  YOUTUBE_CLIENT_ID: string;
  YOUTUBE_CLIENT_SECRET: string;
  OAUTH_REDIRECT_URI: string; // https://<worker>/oauth/google/callback
}

export function b64url(bytes: Uint8Array): string {
  let bin = "";
  bytes.forEach(b => bin += String.fromCharCode(b));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function aesKey(vaultKeyB64: string): Promise<CryptoKey> {
  const raw = Uint8Array.from(atob(vaultKeyB64), c => c.charCodeAt(0));
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

/** AES-256-GCM; output = base64url(iv || ciphertext). */
export async function encryptToken(vaultKeyB64: string, plaintext: string): Promise<string> {
  const key = await aesKey(vaultKeyB64);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv },
    key, new TextEncoder().encode(plaintext));
  return b64url(new Uint8Array([...iv, ...new Uint8Array(ct)]));
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
    prompt: "consent",
    include_granted_scopes: "true",
    scope: SCOPES.join(" "),
    state,
  });
  return `${GOOGLE_AUTH}?${q.toString()}`;
}
