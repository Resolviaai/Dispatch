import { supabase } from '../auth/supabase';

/**
 * Unified accounts and credentials service for Dispatch.
 * Connects to the Dispatch Worker control plane.
 */

export const WORKER_BASE = 'https://dispatch-cloud.resolviaai.workers.dev';

export interface ConnectedAccount {
  id: string;
  provider: 'youtube' | 'instagram' | 'linkedin' | 'twitter' | 'facebook';
  account_id: string;
  account_name: string;
  account_email?: string;
  avatar_url?: string;
  metadata?: Record<string, unknown>;
  is_verified?: boolean;
  created_at: string;
}

export interface GeminiKeyStatus {
  configured: boolean;
  key_prefix: string | null;
  updated_at?: string;
}

export async function listConnectedAccounts(jwtToken: string): Promise<ConnectedAccount[] | null> {
  try {
    const res = await fetch(`${WORKER_BASE}/accounts`, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json',
      },
    });
    if (!res.ok) return null;
    const data = await res.json() as { accounts?: ConnectedAccount[] };
    return Array.isArray(data.accounts) ? data.accounts : [];
  } catch {
    return null;
  }
}

export async function deleteConnectedAccount(jwtToken: string, accountId: string): Promise<boolean> {
  try {
    const res = await fetch(`${WORKER_BASE}/accounts/${accountId}`, {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json',
      },
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function getGeminiKeyStatus(jwtToken: string): Promise<GeminiKeyStatus> {
  try {
    const res = await fetch(`${WORKER_BASE}/keys/gemini`, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json',
      },
    });
    if (!res.ok) return { configured: false, key_prefix: null };
    return await res.json() as GeminiKeyStatus;
  } catch {
    return { configured: false, key_prefix: null };
  }
}

export async function saveGeminiKey(jwtToken: string, apiKey: string): Promise<{ saved: boolean; key_prefix?: string; error?: string }> {
  try {
    const res = await fetch(`${WORKER_BASE}/keys/gemini`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ api_key: apiKey }),
    });
    const data = await res.json() as { saved?: boolean; key_prefix?: string; error?: string };
    if (!res.ok) {
      return { saved: false, error: data.error || 'Failed to save Gemini key' };
    }
    return { saved: true, key_prefix: data.key_prefix };
  } catch (e) {
    return { saved: false, error: e instanceof Error ? e.message : 'Network error' };
  }
}

export async function deleteGeminiKey(jwtToken: string): Promise<boolean> {
  try {
    const res = await fetch(`${WORKER_BASE}/keys/gemini`, {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json',
      },
    });
    return res.ok;
  } catch {
    return false;
  }
}

export interface ConnectAccountParams {
  provider: 'youtube' | 'instagram' | 'linkedin' | 'twitter' | 'facebook';
  account_id: string;
  account_name: string;
  account_email?: string;
  avatar_url?: string;
  credentials: Record<string, unknown> | string;
  metadata?: Record<string, unknown>;
}

export async function connectAccount(
  jwtToken: string,
  params: ConnectAccountParams
): Promise<{ ok: boolean; id?: string; error?: string }> {
  try {
    const res = await fetch(`${WORKER_BASE}/accounts/connect`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(params),
    });
    const data = await res.json() as { ok?: boolean; id?: string; error?: string };
    if (!res.ok) {
      return { ok: false, error: data.error || `Connection failed: ${res.status}` };
    }
    return { ok: true, id: data.id };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'Network error' };
  }
}

export async function initProviderOAuth(jwtToken: string, provider: string): Promise<{ url?: string; error?: string }> {
  try {
    const res = await fetch(`${WORKER_BASE}/oauth/${encodeURIComponent(provider)}/init`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json',
      },
    });
    const data = await res.json() as { url?: string; error?: string };
    if (res.ok && data.url) {
      return { url: data.url };
    }
    return { error: data.error || `OAuth initiation failed: ${res.status}` };
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Network error' };
  }
}

export async function initGoogleOAuth(jwtToken: string): Promise<{ url: string }> {
  const res = await fetch(`${WORKER_BASE}/oauth/google/init`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${jwtToken}`,
      'Content-Type': 'application/json',
    },
  });
  if (res.ok) {
    const data = await res.json() as { url?: string };
    if (data.url) return { url: data.url };
  }
  const errData = await res.json().catch(() => ({})) as { error?: string };
  throw new Error(errData.error || `Google OAuth initialization failed (${res.status})`);
}

export async function updateAccountRoles(
  jwtToken: string,
  accountId: string,
  roles: { is_source_vault?: boolean; is_publisher?: boolean }
): Promise<boolean> {
  try {
    const res = await fetch(`${WORKER_BASE}/accounts/${accountId}`, {
      method: 'PATCH',
      headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(roles),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export interface CloudStorageInfo {
  quotaBytes: number;
  usedBytes: number;
  freeBytes: number;
  quotaGb: number;
  usedGb: number;
  freeGb: number;
  percentUsed: number;
}

export async function getCloudStorageInfo(): Promise<CloudStorageInfo | null> {
  try {
    const { data: profile, error: profError } = await supabase
      .from('profiles')
      .select('storage_quota_bytes')
      .maybeSingle();

    if (profError) {
      console.warn('Could not read user profile storage quota:', profError);
    }

    const { data: media, error: mediaError } = await supabase
      .from('media_objects')
      .select('byte_count')
      .eq('status', 'active');

    if (mediaError) {
      console.warn('Could not read user media objects:', mediaError);
    }

    const quotaBytes = Number(profile?.storage_quota_bytes || 2_000_000_000);
    const usedBytes = Array.isArray(media)
      ? media.reduce((sum, item) => sum + Number(item.byte_count || 0), 0)
      : 0;
    const freeBytes = Math.max(0, quotaBytes - usedBytes);

    const bytesPerGb = 1_000_000_000;
    const quotaGb = Math.round((quotaBytes / bytesPerGb) * 10) / 10;
    const usedGb = Math.round((usedBytes / bytesPerGb) * 100) / 100;
    const freeGb = Math.max(0, Math.round((freeBytes / bytesPerGb) * 10) / 10);
    const percentUsed = quotaBytes > 0 ? Math.min(100, Math.round((usedBytes / quotaBytes) * 100)) : 0;

    return {
      quotaBytes,
      usedBytes,
      freeBytes,
      quotaGb,
      usedGb,
      freeGb,
      percentUsed,
    };
  } catch (err) {
    console.warn('Failed to fetch cloud storage info:', err);
    return null;
  }
}
