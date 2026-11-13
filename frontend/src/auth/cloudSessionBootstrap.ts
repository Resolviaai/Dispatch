import type { ConnectedAccount } from '../accounts/accountsService';

/**
 * Route A credential boundary: hands the Supabase session JWT plus the primary
 * YouTube vault account id to the native layer whenever the session loads or
 * refreshes, so background uploads can request scoped cloud upload sessions
 * without the PC being online and without locally stored OAuth. No-ops outside
 * the Android WebView.
 */

let pushedAt = 0;
let inFlight = false;

export function selectSourceVaultYouTubeAccount(
  accounts: ConnectedAccount[] | null | undefined
): ConnectedAccount | undefined {
  if (!Array.isArray(accounts)) return undefined;
  const eligible = accounts.filter(
    (account) => account.provider === 'youtube' && account.metadata?.is_source_vault !== false
  );
  return eligible.find((account) => account.metadata?.is_source_vault === true) || eligible[0];
}

function nativeBridge(): { pushCloudSession: (payload: string) => boolean } | null {
  if (typeof window === 'undefined') return null;
  const bridge = (window as any).DispatchBridge;
  return typeof bridge?.pushCloudSession === 'function' ? bridge : null;
}

export async function pushCloudSessionToNative(reason: string): Promise<void> {
  const bridge = nativeBridge();
  if (!bridge || inFlight || Date.now() - pushedAt < 5_000) return;
  inFlight = true;
  try {
    const [{ currentSession }, { listConnectedAccounts }] = await Promise.all([
      import('./supabase'),
      import('../accounts/accountsService'),
    ]);
    const session = await currentSession();
    if (!session) {
      // No session does NOT mean "signed out": it also occurs transiently
      // while Supabase restores the persisted session at WebView startup. The
      // 2026-10-29 race (an initial null push wiping a valid pairing mid-
      // restore) killed working sessions; clearing happens ONLY through the
      // explicit SIGNED_OUT event below, which now sends {"cleared": true}.
      // Do NOT arm the throttle here: pushedAt is updated only after a valid
      // session/account payload has actually been pushed to the bridge, so the
      // real SIGNED_IN push that follows is never blocked by a null-session probe.
      return;
    }
    const accounts = await listConnectedAccounts(session.access_token);
    if (accounts === null) {
      // Transient fetch failure: do not overwrite an active vault binding.
      return;
    }
    const youtube = selectSourceVaultYouTubeAccount(accounts);
    if (!youtube?.id) {
      return;
    }
    bridge.pushCloudSession(JSON.stringify({
      access_token: session.access_token,
      account_id: youtube.id,
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
  void import('./supabase').then(({ supabase }) => {
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
  }).catch(() => {});
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - pushedAt > 25_000) {
      void pushCloudSessionToNative('visible');
    }
  });
}
