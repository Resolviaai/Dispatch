import { supabase, currentSession } from './supabase';
import { listConnectedAccounts } from '../accounts/accountsService';

/**
 * Route A credential boundary: hands the Supabase session JWT plus the primary
 * YouTube vault account id to the native layer whenever the session loads or
 * refreshes, so background uploads can request scoped cloud upload sessions
 * without the PC being online and without locally stored OAuth. No-ops outside
 * the Android WebView.
 */

let pushedAt = 0;
let inFlight = false;

function nativeBridge(): { pushCloudSession: (payload: string) => boolean } | null {
  const bridge = (window as any).DispatchBridge;
  return typeof bridge?.pushCloudSession === 'function' ? bridge : null;
}

export async function pushCloudSessionToNative(reason: string): Promise<void> {
  const bridge = nativeBridge();
  if (!bridge || inFlight || Date.now() - pushedAt < 5_000) return;
  inFlight = true;
  try {
    const session = await currentSession();
    if (!session) {
      // No session does NOT mean "signed out": it also occurs transiently
      // while Supabase restores the persisted session at WebView startup. The
      // 2026-10-29 race (an initial null push wiping a valid pairing mid-
      // restore) killed working sessions; clearing happens ONLY through the
      // explicit SIGNED_OUT event below, which now sends {"cleared": true}.
      pushedAt = Date.now();
      return;
    }
    const accounts = await listConnectedAccounts(session.access_token);
    const youtube = accounts?.find((account) => account.provider === 'youtube');
    bridge.pushCloudSession(JSON.stringify({
      access_token: session.access_token,
      account_id: youtube?.id || '',
    }));
    pushedAt = Date.now();
  } catch (e) {
    console.warn(`Cloud session push (${reason}) failed:`, e);
  } finally {
    inFlight = false;
  }
}

export function startCloudSessionBootstrap(): void {
  if (!nativeBridge()) return;
  void pushCloudSessionToNative('initial');
  supabase.auth.onAuthStateChange((event) => {
    if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED') {
      void pushCloudSessionToNative('auth');
    }
    if (event === 'SIGNED_OUT') {
      try {
        nativeBridge()?.pushCloudSession(JSON.stringify({ cleared: true }));
      } catch {
        // Bridge may be gone during teardown
      }
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - pushedAt > 25_000) {
      void pushCloudSessionToNative('visible');
    }
  });
}
