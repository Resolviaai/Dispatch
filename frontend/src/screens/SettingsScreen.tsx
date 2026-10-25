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
  Sparkles,
  Key,
  Trash2,
  Plus,
  RefreshCw,
  Share2,
} from 'lucide-react';
import { Bridge, StorageInfo, apiUrl } from '../bridge';
import { getThemePreference, setThemePreference, ThemePreference } from '../theme';
import {
  ConnectedAccount,
  GeminiKeyStatus,
  listConnectedAccounts,
  deleteConnectedAccount,
  getGeminiKeyStatus,
  saveGeminiKey,
  deleteGeminiKey,
  initGoogleOAuth,
} from '../accounts/accountsService';

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

export const SettingsScreen: React.FC<SettingsScreenProps> = ({ pcHost = 'localhost:8000' }) => {
  const [accounts, setAccounts] = useState<ConnectedAccount[]>([]);
  const [isLoadingAccounts, setIsLoadingAccounts] = useState(false);
  const [geminiStatus, setGeminiStatus] = useState<GeminiKeyStatus>({ configured: false, key_prefix: null });
  const [geminiKeyInput, setGeminiKeyInput] = useState('');
  const [isSavingGemini, setIsSavingGemini] = useState(false);
  const [showGeminiInput, setShowGeminiInput] = useState(false);

  const [wifiOnly, setWifiOnly] = useState(true);
  const [storageInfo, setStorageInfo] = useState<StorageInfo | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [themePref, setThemePref] = useState<ThemePreference>(() => getThemePreference());
  const [defaultLens, setDefaultLens] = useState<'back' | 'front'>(() => getDefaultLensPref());
  const [accountEmail, setAccountEmail] = useState('');
  const [jwtToken, setJwtToken] = useState<string | null>(null);

  useEffect(() => {
    import('../auth/supabase').then(({ currentSession }) =>
      currentSession().then(sess => {
        setAccountEmail(sess?.user?.email ?? '');
        setJwtToken(sess?.access_token ?? null);
      }));
  }, []);

  const refreshAccounts = async () => {
    if (!jwtToken) return;
    setIsLoadingAccounts(true);
    try {
      const list = await listConnectedAccounts(jwtToken);
      setAccounts(list);
      const gemini = await getGeminiKeyStatus(jwtToken);
      setGeminiStatus(gemini);
    } finally {
      setIsLoadingAccounts(false);
    }
  };

  useEffect(() => {
    if (jwtToken) {
      refreshAccounts();
    }
    setWifiOnly(Bridge.getWifiOnly());
    setStorageInfo(Bridge.getStorageInfo());
  }, [jwtToken, pcHost]);

  const handleConnectYouTube = async () => {
    if (!jwtToken) {
      setFeedback('Sign in first, then connect your YouTube account.');
      return;
    }
    setFeedback('Opening Google sign-in...');
    try {
      const { url } = await initGoogleOAuth(jwtToken);
      const opened = Bridge.openCustomTab(url);
      setFeedback(
        opened
          ? 'Google sign-in opened. Select your account and approve access, then return here.'
          : 'Could not open browser. Check that Chrome is installed and retry.'
      );
      setTimeout(refreshAccounts, 8000);
    } catch (e: any) {
      setFeedback(e.message || 'Failed to start Google sign-in.');
    }
  };

  const handleDisconnectAccount = async (account: ConnectedAccount) => {
    if (!jwtToken) return;
    const ok = await deleteConnectedAccount(jwtToken, account.id);
    if (ok) {
      setAccounts(prev => prev.filter(a => a.id !== account.id));
      setFeedback(`${account.account_name} disconnected.`);
    } else {
      setFeedback(`Could not disconnect ${account.account_name}.`);
    }
  };

  const handleSaveGeminiKey = async () => {
    if (!jwtToken || !geminiKeyInput.trim()) return;
    setIsSavingGemini(true);
    setFeedback(null);
    try {
      const res = await saveGeminiKey(jwtToken, geminiKeyInput.trim());
      if (res.saved) {
        setGeminiStatus({ configured: true, key_prefix: res.key_prefix || null });
        setGeminiKeyInput('');
        setShowGeminiInput(false);
        setFeedback('Personal Gemini API key saved securely in your vault.');
      } else {
        setFeedback(res.error || 'Failed to save Gemini API key.');
      }
    } finally {
      setIsSavingGemini(false);
    }
  };

  const handleDeleteGeminiKey = async () => {
    if (!jwtToken) return;
    const ok = await deleteGeminiKey(jwtToken);
    if (ok) {
      setGeminiStatus({ configured: false, key_prefix: null });
      setFeedback('Personal Gemini API key removed.');
    }
  };

  const handleSignOut = async () => {
    const { signOut } = await import('../auth/supabase');
    await signOut();
  };

  const themeOptions: { id: ThemePreference; label: string; desc: string; icon: React.ReactNode }[] = [
    { id: 'device', label: 'Use device theme', desc: 'Follows your phone’s setting', icon: <Monitor size={16} /> },
    { id: 'dark', label: 'Dark', desc: 'Studio charcoal', icon: <Moon size={16} /> },
    { id: 'light', label: 'Light', desc: 'Bright daylight', icon: <Sun size={16} /> },
  ];

  const handleThemeChange = (pref: ThemePreference) => {
    setThemePref(pref);
    setThemePreference(pref);
  };

  const handleLensChange = (lens: 'back' | 'front') => {
    setDefaultLens(lens);
    setDefaultLensPref(lens);
  };

  const storageUsedPct = storageInfo?.available && Number.isFinite(storageInfo.freeGb) && Number.isFinite(storageInfo.totalGb) && storageInfo.totalGb! > 0
    ? Math.max(0, Math.min(100, Math.round(((storageInfo.totalGb! - storageInfo.freeGb!) / storageInfo.totalGb!) * 100)))
    : null;

  const youtubeAccounts = accounts.filter(a => a.provider === 'youtube');
  const instagramAccounts = accounts.filter(a => a.provider === 'instagram');
  const linkedinAccount = accounts.find(a => a.provider === 'linkedin');
  const twitterAccount = accounts.find(a => a.provider === 'twitter');

  return (
    <div className="w-full h-full flex flex-col bg-canvas text-text-main overflow-hidden select-none">

      {/* SCREEN HEADER */}
      <div className="shrink-0 px-4 pt-4 pb-3 border-b border-border bg-canvas">
        <h1 className="text-lg font-semibold tracking-tight text-text-main flex items-center gap-2">
          <SettingsIcon size={19} className="text-primary" />
          Settings
        </h1>
        <p className="text-xs text-text-muted mt-0.5">
          Controls for how Dispatch looks, records, and syncs
        </p>
      </div>

      {/* CONTENT SCROLL AREA */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-6">

        {/* FEEDBACK BANNER */}
        {feedback && (
          <div role="status" className="p-3 rounded-xl bg-surface border border-border text-sm text-text-secondary flex items-start justify-between gap-2">
            <span>{feedback}</span>
            <button onClick={() => setFeedback(null)} className="text-xs text-text-muted hover:text-text-main">Dismiss</button>
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

        {/* 2. RECORDING */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-3">
          <h2 className="text-base font-semibold tracking-tight text-text-main">Recording</h2>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2 text-sm text-text-main">
                <Camera size={15} className="text-text-muted" />
                Default camera lens
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

        {/* 4. CONNECTED ACCOUNTS (Single service of truth) */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold tracking-tight text-text-main">Connected Accounts</h2>
              <p className="text-xs text-text-muted mt-0.5">Publishing destinations and identity binding</p>
            </div>
            <button
              onClick={refreshAccounts}
              disabled={isLoadingAccounts}
              className="p-2 rounded-lg bg-surface-2 hover:bg-surface-3 border border-border text-text-secondary active:scale-[0.98]"
              title="Refresh connected accounts"
            >
              <RefreshCw size={14} className={isLoadingAccounts ? 'animate-spin' : ''} />
            </button>
          </div>

          {/* YouTube Accounts */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-text-secondary">
              <span className="flex items-center gap-1.5"><Youtube size={14} className="text-danger" /> YouTube Channels</span>
              <span className="font-mono">{youtubeAccounts.length} connected</span>
            </div>

            {youtubeAccounts.length > 0 ? (
              <div className="space-y-2">
                {youtubeAccounts.map((acc) => (
                  <div key={acc.id} className="p-3 rounded-xl bg-surface-2 border border-border flex items-center justify-between gap-3 text-sm">
                    <div className="flex items-center gap-2.5 min-w-0">
                      {acc.avatar_url ? (
                        <img src={acc.avatar_url} alt="" className="w-8 h-8 rounded-full border border-border shrink-0 object-cover" />
                      ) : (
                        <div className="w-8 h-8 rounded-full bg-surface-3 flex items-center justify-center text-text-muted shrink-0">
                          <Youtube size={15} />
                        </div>
                      )}
                      <div className="min-w-0">
                        <p className="font-medium text-text-main truncate">{acc.account_name}</p>
                        <p className="text-xs text-text-muted truncate font-mono">{acc.account_email || acc.account_id}</p>
                      </div>
                    </div>
                    <button
                      onClick={() => handleDisconnectAccount(acc)}
                      className="px-2.5 py-1.5 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors"
                    >
                      Disconnect
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-text-muted italic">No YouTube accounts connected yet.</p>
            )}

            <button
              onClick={handleConnectYouTube}
              className="w-full min-h-[44px] py-2 px-3 rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-sm font-medium text-text-main flex items-center justify-center gap-2 transition-all active:scale-[0.98]"
            >
              <Plus size={15} />
              <span>{youtubeAccounts.length > 0 ? 'Connect Another YouTube Channel' : 'Connect YouTube (Google Sign-In)'}</span>
            </button>
          </div>

          {/* Social Platforms Notice */}
          <div className="pt-2 border-t border-border space-y-2">
            <p className="text-xs text-text-muted leading-relaxed">
              Dispatch supports multiple YouTube &amp; Instagram destinations, and single LinkedIn and X destinations. Connecting an account binds it directly to your publishing outbox.
            </p>
          </div>
        </section>

        {/* 5. PER-USER GEMINI API KEY */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles size={17} className="text-primary" />
              <h2 className="text-base font-semibold tracking-tight text-text-main">Gemini AI Key</h2>
            </div>
            {geminiStatus.configured ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border chip-success">
                <span className="w-1.5 h-1.5 rounded-full bg-current" />
                Active
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border chip-warning">
                Using Shared Engine
              </span>
            )}
          </div>

          <p className="text-xs text-text-muted leading-relaxed">
            Provide your own Google Gemini API key to power highlight selection and word-level timestamps without shared host rate limits. Your key is encrypted with AES-256-GCM in the cloud vault.
          </p>

          {geminiStatus.configured ? (
            <div className="p-3 rounded-xl bg-surface-2 border border-border flex items-center justify-between gap-3 text-sm">
              <div className="flex items-center gap-2 min-w-0">
                <Key size={15} className="text-primary shrink-0" />
                <span className="font-mono text-xs text-text-main truncate">{geminiStatus.key_prefix}</span>
              </div>
              <button
                onClick={handleDeleteGeminiKey}
                className="px-2.5 py-1.5 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors"
              >
                Remove
              </button>
            </div>
          ) : showGeminiInput ? (
            <div className="space-y-2 pt-1">
              <input
                type="password"
                value={geminiKeyInput}
                onChange={(e) => setGeminiKeyInput(e.target.value)}
                placeholder="AIzaSy..."
                className="w-full min-h-[44px] rounded-xl bg-surface-2 border border-border px-3 text-sm font-mono text-text-main outline-none focus:border-primary"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={handleSaveGeminiKey}
                  disabled={isSavingGemini || !geminiKeyInput.trim()}
                  className="flex-1 min-h-[40px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 active:scale-[0.98] disabled:opacity-50"
                >
                  {isSavingGemini ? 'Saving...' : 'Save Gemini Key'}
                </button>
                <button
                  onClick={() => { setShowGeminiInput(false); setGeminiKeyInput(''); }}
                  className="min-h-[40px] px-3 rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-sm text-text-secondary active:scale-[0.98]"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setShowGeminiInput(true)}
              className="w-full min-h-[44px] py-2 px-3 rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-sm font-medium text-text-main flex items-center justify-center gap-2 transition-all active:scale-[0.98]"
            >
              <Key size={15} />
              <span>Add Personal Gemini API Key</span>
            </button>
          )}
        </section>

        {/* 6. UPLOADS — network behavior toggle */}
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

        {/* 7. STORAGE */}
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

        {/* 8. ABOUT */}
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
