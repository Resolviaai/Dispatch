import React, { useState, useEffect } from 'react';
import {
  Settings as SettingsIcon,
  Youtube,
  LogOut,
  ShieldAlert,
  Monitor,
  Moon,
  Sun,
  Check,
  UserRound,
  Camera,
  Wifi,
  ExternalLink,
} from 'lucide-react';
import { Bridge, StorageInfo, apiUrl } from '../bridge';
import { getThemePreference, setThemePreference, ThemePreference } from '../theme';

interface SettingsScreenProps {
  pcHost?: string;
}

const DEFAULT_LENS_KEY = 'dispatch_default_lens';

export function getDefaultLensPref(): 'back' | 'front' {
  if (typeof localStorage === 'undefined') return 'back';
  return localStorage.getItem(DEFAULT_LENS_KEY) === 'front' ? 'front' : 'back';
}

export function setDefaultLensPref(lens: 'back' | 'front') {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(DEFAULT_LENS_KEY, lens);
  }
}

/**
 * Settings (Design System v3 §2.7.6) — a control panel, not a data sheet.
 * Every section carries an actionable control: theme picker, default camera,
 * account sign-out, YouTube connect/disconnect, PC address with live test,
 * upload network toggle. Static facts are compressed to one metadata line.
 */
export const SettingsScreen: React.FC<SettingsScreenProps> = ({ pcHost = 'localhost:8000' }) => {
  const [youtubeState, setYoutubeState] = useState<{
    connected: boolean;
    channelTitle: string;
    channelId: string;
  }>({
    connected: false,
    channelTitle: '',
    channelId: '',
  });

  const [isChecking, setIsChecking] = useState(false);
  const [isStartingAuth, setIsStartingAuth] = useState(false);
  const [wifiOnly, setWifiOnly] = useState(true);
  const [storageInfo, setStorageInfo] = useState<StorageInfo | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [themePref, setThemePref] = useState<ThemePreference>(() => getThemePreference());
  const [defaultLens, setDefaultLens] = useState<'back' | 'front'>(() => getDefaultLensPref());
  const isNativeAndroid = Bridge.isAvailable();

  const fetchYouTubeStatus = async () => {
    setIsChecking(true);
    if (Bridge.isAvailable()) {
      const status = Bridge.getYouTubeStatus();
      setYoutubeState({
        connected: status.connected,
        channelTitle: status.channelTitle || '',
        channelId: status.account || '',
      });
      setIsChecking(false);
      return;
    }
    try {
      const res = await fetch(apiUrl('/api/integrations/status', pcHost));
      if (res.ok) {
        const data = await res.json();
        setYoutubeState({
          connected: !!data.youtube?.connected,
          channelTitle: data.youtube?.channel_title || '',
          channelId: data.youtube?.channel_id || '',
        });
        if (!data.youtube?.connected && data.youtube?.error) {
          setFeedback(data.youtube.error);
        }
      }
    } catch {
      // Bridge fallback on phone
      if (Bridge.isAvailable()) {
        const status = Bridge.getYouTubeStatus();
        setYoutubeState({
          connected: status.connected,
          channelTitle: status.channelTitle || '',
          channelId: status.account || '',
        });
      }
    } finally {
      setIsChecking(false);
    }
  };

  useEffect(() => {
    fetchYouTubeStatus();
    setWifiOnly(Bridge.getWifiOnly());
    setStorageInfo(Bridge.getStorageInfo());
  }, [pcHost]);

  const handleConnectYouTube = async () => {
    if (Bridge.isAvailable()) {
      // On-device OAuth: Custom Tab to the Dispatch Worker's Google consent flow.
      // The Supabase access token (short-lived) binds the authorization to this user.
      const { currentSession } = await import('../auth/supabase');
      const sess = await currentSession();
      if (!sess?.access_token) {
        setFeedback('Sign in first, then connect YouTube.');
        return;
      }
      const openTab: any = (window as any).DispatchBridge?.openCustomTab;
      const url = `https://dispatch-cloud.resolviaai.workers.dev/oauth/google/start?token=${encodeURIComponent(sess.access_token)}`;
      let opened = false;
      if (typeof openTab === 'function') {
        try {
          opened = openTab(url) !== false;
        } catch {
          opened = false;
        }
      }
      if (!opened) {
        // Fallback for devices without a Custom Tab handler: system browser.
        try {
          opened = !!window.open(url, '_blank');
        } catch {
          opened = false;
        }
      }
      setFeedback(
        opened
          ? 'Google sign-in opened. Approve access, then return here.'
          : 'Could not open the browser on this device. Check that a browser is installed and retry.'
      );
      setTimeout(fetchYouTubeStatus, 8000);
      return;
    }

    setIsStartingAuth(true);
    setFeedback(null);
    try {
      const res = await fetch(apiUrl('/api/integrations/youtube/start-auth', pcHost), { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Dispatch could not start Google sign-in.');
      }
      setFeedback(data.message || 'Google sign-in is starting.');

      for (let attempt = 0; attempt < 150; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        const statusResponse = await fetch(apiUrl('/api/integrations/youtube/auth-status', pcHost));
        const authState = await statusResponse.json();
        if (!statusResponse.ok) {
          throw new Error(authState.detail || 'Could not check Google sign-in status.');
        }
        if (authState.status === 'authenticated') {
          setFeedback(authState.message || 'YouTube account connected and verified.');
          await fetchYouTubeStatus();
          return;
        }
        if (authState.status === 'failed') {
          setFeedback(authState.message || 'Google sign-in failed. Check the PC and retry.');
          return;
        }
        if (authState.status === 'awaiting_authorization') {
          setFeedback('Google sign-in opened. Complete authorization in the PC browser…');
        }
      }
      setFeedback('Google sign-in is still waiting. Complete it in the PC browser, then check the connection again.');
    } catch (e: any) {
      setFeedback(e.message || 'Google sign-in failed. Check the PC and retry.');
    } finally {
      setIsStartingAuth(false);
    }
  };


  const handleDisconnectYouTube = () => {
    if (Bridge.isAvailable()) {
      Bridge.disconnectYouTube();
    }
    setYoutubeState({ connected: false, channelTitle: '', channelId: '' });
    setFeedback('YouTube disconnected.');
  };

  const [accountEmail, setAccountEmail] = useState('');
  useEffect(() => {
    import('../auth/supabase').then(({ currentSession }) =>
      currentSession().then(sess => setAccountEmail(sess?.user?.email ?? '')));
  }, []);

  const handleSignOut = async () => {
    const { signOut } = await import('../auth/supabase');
    await signOut(); // AuthGate flips to the sign-in screen automatically
  };

  const themeOptions: { id: ThemePreference; label: string; desc: string; icon: React.ReactNode }[] = [
    { id: 'device', label: 'Use device theme', desc: 'Follows your phone\u2019s setting', icon: <Monitor size={16} /> },
    { id: 'dark', label: 'Dark', desc: 'Studio charcoal', icon: <Moon size={16} /> },
    { id: 'light', label: 'Light', desc: 'Bright daylight', icon: <Sun size={16} /> },
  ];

  const handleThemeChange = (pref: ThemePreference) => {
    setThemePref(pref);
    setThemePreference(pref); // instant recolour, no reload
  };

  const handleLensChange = (lens: 'back' | 'front') => {
    setDefaultLens(lens);
    setDefaultLensPref(lens); // applied next time the Record screen opens
  };

  const storageUsedPct = storageInfo?.available && Number.isFinite(storageInfo.freeGb) && Number.isFinite(storageInfo.totalGb) && storageInfo.totalGb! > 0
    ? Math.max(0, Math.min(100, Math.round(((storageInfo.totalGb! - storageInfo.freeGb!) / storageInfo.totalGb!) * 100)))
    : null;

  return (
    <div className="w-full h-full flex flex-col bg-canvas text-text-main overflow-hidden select-none">

      {/* SCREEN HEADER — "Where am I?" */}
      <div className="shrink-0 px-4 pt-4 pb-3 border-b border-border bg-canvas">
        <h1 className="text-lg font-semibold tracking-tight text-text-main flex items-center gap-2">
          <SettingsIcon size={19} className="text-primary" />
          Settings
        </h1>
        <p className="text-xs text-text-muted mt-0.5">
          Controls for how Dispatch looks, records, and syncs
        </p>
      </div>

      {/* CONTENT SCROLL AREA — single-direction vertical flow */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-6">

        {/* FEEDBACK BANNER */}
        {feedback && (
          <div role="status" className="p-3 rounded-xl bg-surface border border-border text-sm text-text-secondary">
            {feedback}
          </div>
        )}

        {/* 1. APPEARANCE */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-3">
          <h2 className="text-base font-semibold tracking-tight text-text-main">Appearance</h2>
          <div className="space-y-2" role="radiogroup" aria-label="Theme">
            {themeOptions.map((opt) => {
              const isSelected = themePref === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  role="radio"
                  aria-checked={isSelected}
                  onClick={() => handleThemeChange(opt.id)}
                  className={`w-full min-h-[44px] flex items-center justify-between gap-3 px-3 py-2.5 rounded-xl border text-left transition-all active:scale-[0.98] ${
                    isSelected
                      ? 'bg-primary-soft border-primary/50 text-text-main'
                      : 'bg-surface-2 border-border text-text-secondary hover:text-text-main hover:border-border-strong'
                  }`}
                >
                  <span className="flex items-center gap-3 min-w-0">
                    <span className={isSelected ? 'text-primary' : 'text-text-muted'}>{opt.icon}</span>
                    <span className="min-w-0">
                      <span className="block text-sm font-medium">{opt.label}</span>
                      <span className="block text-xs text-text-muted">{opt.desc}</span>
                    </span>
                  </span>
                  {isSelected && <Check size={16} className="text-primary shrink-0" strokeWidth={2.5} />}
                </button>
              );
            })}
          </div>
        </section>

        {/* 2. RECORDING — real controls, facts compressed */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-3">
          <h2 className="text-base font-semibold tracking-tight text-text-main">Recording</h2>

          {/* Default camera control */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="flex items-center gap-2 text-sm font-medium text-text-main">
                <Camera size={15} className="text-text-muted" />
                Default camera
              </span>
              <span className="text-xs text-text-muted">Opens with the Record screen</span>
            </div>
            <div className="flex items-center p-1 rounded-xl bg-surface-2 border border-border" role="radiogroup" aria-label="Default camera">
              {(['back', 'front'] as const).map((lens) => {
                const isSelected = defaultLens === lens;
                return (
                  <button
                    key={lens}
                    type="button"
                    role="radio"
                    aria-checked={isSelected}
                    onClick={() => handleLensChange(lens)}
                    className={`flex-1 min-h-[36px] rounded-lg text-sm font-medium transition-all active:scale-[0.98] ${
                      isSelected ? 'bg-primary text-white font-semibold' : 'text-text-secondary hover:text-text-main'
                    }`}
                  >
                    {lens === 'back' ? 'Back camera' : 'Front camera'}
                  </button>
                );
              })}
            </div>
          </div>

          <p className="text-xs text-text-muted tabular-nums">
            Captures 1080p FHD · 30&nbsp;FPS · 10-minute auto-saved segments · AAC 48&nbsp;kHz
          </p>
        </section>

        {/* 3. ACCOUNT */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-3">
          <h2 className="text-base font-semibold tracking-tight text-text-main">Account</h2>
          <div className="flex items-center gap-3">
            <span className="w-10 h-10 rounded-full bg-primary-soft border border-primary/30 flex items-center justify-center text-primary shrink-0">
              <UserRound size={19} />
            </span>
            <div className="min-w-0">
              <p className="text-xs text-text-muted">Signed in as</p>
              <p className="text-sm font-medium text-text-main truncate">{accountEmail || '…'}</p>
            </div>
          </div>
          <button onClick={handleSignOut}
            className="w-full min-h-[44px] rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-sm font-medium text-text-secondary hover:text-danger transition-all duration-150 active:scale-[0.98] flex items-center justify-center gap-2">
            <LogOut size={15} />
            Sign out
          </button>
        </section>

        {/* 4. YOUTUBE */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold tracking-tight text-text-main">YouTube</h2>

            {youtubeState.connected ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border chip-success">
                <span className="w-1.5 h-1.5 rounded-full bg-current" />
                Connected
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border chip-danger">
                <span className="w-1.5 h-1.5 rounded-full bg-current" />
                Not connected
              </span>
            )}
          </div>

          {youtubeState.connected ? (
            <div className="space-y-2 pt-1">
              <div className="p-3 rounded-xl bg-surface-2 border border-border flex items-center justify-between gap-3 text-sm">
                <span className="text-text-muted shrink-0">Channel</span>
                <span className="text-status-success font-medium truncate">
                  {youtubeState.channelTitle || 'Dispatch Authenticated'}
                </span>
              </div>

              <button
                onClick={handleDisconnectYouTube}
                className="w-full min-h-[44px] py-2.5 px-3 rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-text-secondary hover:text-danger text-sm flex items-center justify-center gap-2 transition-all duration-150 active:scale-[0.98]"
              >
                <LogOut size={15} />
                <span>Disconnect Account</span>
              </button>
            </div>
          ) : isNativeAndroid ? (
            <div className="space-y-3 pt-1">
              <p className="text-sm text-text-secondary leading-relaxed">
                Connect your own YouTube account with Google sign-in on this phone. Recordings upload to your channel as unlisted for PC processing.
              </p>
              <button
                onClick={handleConnectYouTube}
                disabled={isChecking}
                className="w-full min-h-[44px] py-2.5 px-3 rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 transition-all duration-150 active:scale-[0.98] shadow-hero-glow disabled:opacity-50"
              >
                <Youtube size={16} />
                <span>Connect YouTube (Google sign-in)</span>
              </button>
              <div className="rounded-xl bg-surface-2 border border-border p-3 flex gap-2">
                <ShieldAlert size={16} className="shrink-0 text-primary mt-0.5" />
                <p className="text-xs text-text-muted leading-relaxed">
                  You approve access in Google&rsquo;s own screen. Tokens stay encrypted on the PC backend — never over plain HTTP.
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-2 pt-1">
              <button
                onClick={handleConnectYouTube}
                disabled={isStartingAuth}
                className="w-full min-h-[44px] py-2.5 px-3 rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 transition-all duration-150 active:scale-[0.98] shadow-hero-glow disabled:opacity-50"
              >
                <Youtube size={16} />
                <span>{isStartingAuth ? 'Google sign-in in progress…' : 'Connect YouTube (OAuth)'}</span>
              </button>
              <p className="text-xs text-text-muted text-center">
                Opens official Google OAuth login. No manual tokens required.
              </p>
            </div>
          )}
        </section>

        {/* 5. UPLOADS — network behavior toggle */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-3">
          <h2 className="text-base font-semibold tracking-tight text-text-main">Uploads</h2>

          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <span className="flex items-center gap-2 text-sm text-text-main">
                <Wifi size={15} className="text-text-muted" />
                Upload over Wi-Fi automatically
              </span>
              <span className="text-xs text-text-muted block mt-0.5">
                Restricts uploads to unmetered network (never uses mobile data)
              </span>
            </div>

            {/* Pill toggle (44px hitbox) */}
            <button
              onClick={() => {
                const nextVal = !wifiOnly;
                setWifiOnly(nextVal);
                Bridge.setWifiOnly(nextVal);
              }}
              role="switch"
              aria-checked={wifiOnly}
              className="w-12 h-11 flex items-center justify-center active:scale-[0.98] transition-all duration-150"
              title="Toggle Wi-Fi upload"
            >
              <div className={`w-11 h-6 rounded-full transition-colors duration-150 p-0.5 flex items-center ${
                wifiOnly ? 'bg-primary justify-end' : 'bg-surface-3 border border-border justify-start'
              }`}>
                <div className="w-5 h-5 rounded-full bg-white shadow-sm" />
              </div>
            </button>
          </div>
        </section>

        {/* 6. STORAGE */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold tracking-tight text-text-main">Storage</h2>
            <span className="text-xs font-mono tabular-nums text-text-muted">
              {storageInfo?.available && Number.isFinite(storageInfo.freeGb) && Number.isFinite(storageInfo.totalGb)
                ? <>{storageInfo.freeGb!.toFixed(1)}&nbsp;GB free / {storageInfo.totalGb!.toFixed(1)}&nbsp;GB</>
                : 'Unavailable'}
            </span>
          </div>

          {storageUsedPct !== null ? (
            <>
              <div className="w-full h-2 rounded-full bg-surface-3 overflow-hidden mt-1">
                <div
                  className="h-full bg-primary rounded-full transition-all duration-300"
                  style={{ width: `${storageUsedPct}%` }}
                />
              </div>
              <span className="text-xs text-text-muted block">
                {storageUsedPct}% used · App-specific recording storage
              </span>
            </>
          ) : (
            <p className="text-xs text-text-muted" role="status">
              {storageInfo?.error || 'Storage information is loading.'}
            </p>
          )}
        </section>

        {/* 7. ABOUT — one line of truth */}
        <section className="rounded-2xl bg-surface border border-border p-4 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-base font-semibold tracking-tight text-text-main">About</h2>
            <p className="text-xs text-text-muted mt-0.5">
              Dispatch — record once, publish clips. Your phone records, your PC renders, your accounts publish.
            </p>
          </div>
          <a
            href="https://github.com/Resolviaai/Dispatch/issues"
            target="_blank"
            rel="noreferrer"
            className="min-h-[44px] px-3 rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-sm font-medium text-text-secondary hover:text-text-main flex items-center gap-1.5 shrink-0 transition-all active:scale-[0.98]"
          >
            <ExternalLink size={14} />
            Report issue
          </a>
        </section>

      </div>
    </div>
  );
};
