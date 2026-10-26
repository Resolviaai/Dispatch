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
  Sparkles,
  Key,
  Trash2,
  Plus,
  RefreshCw,
  Share2,
  Sliders,
  Smartphone,
  Cloud,
  X as CloseIcon,
} from 'lucide-react';
import { TemplateStudio } from '../captions/TemplateStudio';
import { Bridge, StorageInfo, apiUrl } from '../bridge';
import { getThemePreference, setThemePreference, ThemePreference } from '../theme';
import {
  ConnectedAccount,
  GeminiKeyStatus,
  listConnectedAccounts,
  deleteConnectedAccount,
  initProviderOAuth,
  getGeminiKeyStatus,
  saveGeminiKey,
  deleteGeminiKey,
  initGoogleOAuth,
  updateAccountRoles,
  CloudStorageInfo,
  getCloudStorageInfo,
} from '../accounts/accountsService';

interface SettingsScreenProps {
  pcHost?: string;
  onEditorOpenChange?: (open: boolean) => void;
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

const InstagramIcon = () => (
  <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none">
    <defs>
      <linearGradient id="ig-grad-settings" x1="0%" y1="100%" x2="100%" y2="0%">
        <stop offset="0%" stopColor="#FED373" />
        <stop offset="25%" stopColor="#F15245" />
        <stop offset="60%" stopColor="#D92E7F" />
        <stop offset="100%" stopColor="#9B36B7" />
      </linearGradient>
    </defs>
    <rect width="24" height="24" rx="6" fill="url(#ig-grad-settings)" />
    <rect x="5.5" y="5.5" width="13" height="13" rx="3.5" stroke="white" strokeWidth="1.5" />
    <circle cx="12" cy="12" r="3" stroke="white" strokeWidth="1.5" />
    <circle cx="15.8" cy="8.2" r="0.8" fill="white" />
  </svg>
);

const LinkedInIcon = () => (
  <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none">
    <rect width="24" height="24" rx="6" fill="#0A66C2" />
    <path
      d="M7.5 9.5H5V17H7.5V9.5ZM6.25 8.2C7.05 8.2 7.7 7.55 7.7 6.75C7.7 5.95 7.05 5.3 6.25 5.3C5.45 5.3 4.8 5.95 4.8 6.75C4.8 7.55 5.45 8.2 6.25 8.2ZM19 17H16.5V13.1C16.5 12.15 16.48 10.95 15.15 10.95C13.8 10.95 13.6 12.02 13.6 13.03V17H11.1V9.5H13.5V10.55H13.53C13.87 9.9 14.7 9.25 15.9 9.25C18.45 9.25 18.95 10.9 18.95 13.05V17H19Z"
      fill="white"
    />
  </svg>
);

const XIcon = () => (
  <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none">
    <rect width="24" height="24" rx="6" fill="#18181B" stroke="#27272A" strokeWidth="1" />
    <path
      d="M14.7 6.5H16.8L12.2 11.75L17.6 18.5H13.35L10.02 14.15L6.2 18.5H4.1L8.98 12.92L3.8 6.5H8.16L11.16 10.47L14.7 6.5ZM13.96 17.22H15.12L7.5 7.72H6.25L13.96 17.22Z"
      fill="white"
    />
  </svg>
);

const FacebookIcon = () => (
  <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none">
    <rect width="24" height="24" rx="6" fill="#1877F2" />
    <path
      d="M16.5 12H14V20H10.5V12H9V9H10.5V7C10.5 5.3 11.8 4 13.5 4H16.5V7H14.5C14.2 7 14 7.2 14 7.5V9H16.5L16.5 12Z"
      fill="white"
    />
  </svg>
);

export const SettingsScreen: React.FC<SettingsScreenProps> = ({ pcHost = 'localhost:8000', onEditorOpenChange }) => {
  const [accounts, setAccounts] = useState<ConnectedAccount[]>([]);
  const [isLoadingAccounts, setIsLoadingAccounts] = useState(false);
  const [geminiStatus, setGeminiStatus] = useState<GeminiKeyStatus>({ configured: false, key_prefix: null });
  const [geminiKeyInput, setGeminiKeyInput] = useState('');
  const [isSavingGemini, setIsSavingGemini] = useState(false);
  const [showGeminiInput, setShowGeminiInput] = useState(false);

  // State for connected accounts and keys
  const [wifiOnly, setWifiOnly] = useState(true);
  const [storageInfo, setStorageInfo] = useState<StorageInfo | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [themePref, setThemePref] = useState<ThemePreference>(() => getThemePreference());
  const [defaultLens, setDefaultLens] = useState<'back' | 'front'>(() => getDefaultLensPref());
  const [accountEmail, setAccountEmail] = useState('');
  const [jwtToken, setJwtToken] = useState<string | null>(null);
  const [isTemplateEditorOpen, setIsTemplateEditorOpen] = useState(false);

  useEffect(() => {
    onEditorOpenChange?.(isTemplateEditorOpen);
    return () => onEditorOpenChange?.(false);
  }, [isTemplateEditorOpen, onEditorOpenChange]);
  const [cloudStorage, setCloudStorage] = useState<CloudStorageInfo | null>(null);
  const [isLoadingCloudStorage, setIsLoadingCloudStorage] = useState(false);

  const refreshCloudStorage = async () => {
    setIsLoadingCloudStorage(true);
    try {
      const info = await getCloudStorageInfo();
      setCloudStorage(info);
    } catch {
      // offline or unauthenticated
    } finally {
      setIsLoadingCloudStorage(false);
    }
  };

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

  const triggerOAuthPolling = () => {
    [2000, 4000, 7000, 11000, 16000, 22000, 30000].forEach(delay => {
      setTimeout(refreshAccounts, delay);
    });
  };

  useEffect(() => {
    if (jwtToken) {
      refreshAccounts();
    }
    setWifiOnly(Bridge.getWifiOnly());
    setStorageInfo(Bridge.getStorageInfo());
    refreshCloudStorage();

    const handleVisibility = () => {
      if (document.visibilityState === 'visible' && jwtToken) {
        refreshAccounts();
        refreshCloudStorage();
      }
    };
    const handleFocus = () => {
      if (jwtToken) {
        refreshAccounts();
        refreshCloudStorage();
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('focus', handleFocus);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('focus', handleFocus);
    };
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
      triggerOAuthPolling();
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

  const handleToggleRole = async (account: ConnectedAccount, role: 'source_vault' | 'publisher') => {
    if (!jwtToken) return;
    const currentVault = account.metadata?.is_source_vault !== false;
    const currentPub = account.metadata?.is_publisher !== false;
    const updated = role === 'source_vault'
      ? { is_source_vault: !currentVault }
      : { is_publisher: !currentPub };

    setAccounts(prev => prev.map(a => a.id === account.id ? {
      ...a,
      metadata: { ...(a.metadata || {}), ...updated }
    } : a));

    setFeedback(`Updated ${account.account_name} roles.`);
    await updateAccountRoles(jwtToken, account.id, updated);
  };

  const handleConnectProvider = async (provider: 'instagram' | 'linkedin' | 'twitter' | 'facebook') => {
    if (!jwtToken) {
      setFeedback('Sign in first, then connect your account.');
      return;
    }
    const label = provider === 'twitter' ? 'X (Twitter)' : provider === 'facebook' ? 'Facebook' : provider.charAt(0).toUpperCase() + provider.slice(1);
    setFeedback(`Opening ${label} sign-in...`);
    try {
      const res = await initProviderOAuth(jwtToken, provider);
      if (res.url) {
        const opened = Bridge.openCustomTab(res.url);
        setFeedback(
          opened
            ? `${label} sign-in opened. Approve access, then return here.`
            : 'Could not open browser. Please check that Chrome is installed and retry.'
        );
        triggerOAuthPolling();
      } else {
        setFeedback(res.error || `${label} connection could not be initiated.`);
      }
    } catch (e: any) {
      setFeedback(e.message || `Failed to initiate ${label} sign-in.`);
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
  const facebookAccounts = accounts.filter(a => a.provider === 'facebook');
  const linkedinAccount = accounts.find(a => a.provider === 'linkedin');
  const twitterAccount = accounts.find(a => a.provider === 'twitter');

  if (isTemplateEditorOpen) {
    return (
      <TemplateStudio
        onClose={() => setIsTemplateEditorOpen(false)}
        pcHost={pcHost}
      />
    );
  }

  return (
    <div className="w-full h-full flex flex-col bg-canvas text-text-main overflow-hidden select-none">

      {/* SCREEN HEADER */}
      <div className="shrink-0 px-4 pt-4 pb-2 border-b border-border bg-canvas">
        <h1 className="text-lg font-semibold tracking-tight text-text-main flex items-center gap-2">
          <SettingsIcon size={18} className="text-primary" />
          Settings
        </h1>
      </div>

      {/* CONTENT SCROLL AREA */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3.5">

        {/* FEEDBACK BANNER */}
        {feedback && (
          <div role="status" className="p-3 rounded-xl bg-surface border border-border text-sm text-text-secondary flex items-start justify-between gap-2">
            <span>{feedback}</span>
            <button onClick={() => setFeedback(null)} className="text-xs text-text-muted hover:text-text-main">Dismiss</button>
          </div>
        )}

        {/* 1. ACCOUNT PROFILE ROW */}
        <section className="rounded-2xl bg-surface border border-border p-3.5 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <span className="w-9 h-9 rounded-full bg-primary-soft border border-primary/30 flex items-center justify-center text-primary shrink-0">
              <UserRound size={17} />
            </span>
            <div className="min-w-0">
              <p className="text-xs text-text-muted leading-none">Account</p>
              <p className="text-sm font-medium text-text-main truncate mt-1">{accountEmail || '…'}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleSignOut}
            className="min-h-[36px] px-3 rounded-lg bg-surface-2 hover:bg-surface-3 border border-border text-xs font-medium text-text-secondary hover:text-danger active:scale-[0.98] transition-all flex items-center gap-1.5 shrink-0"
          >
            <LogOut size={13} />
            <span>Sign out</span>
          </button>
        </section>

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
        <section className="rounded-2xl bg-surface border border-border p-3.5 space-y-2.5">
          <h2 className="text-sm font-semibold tracking-tight text-text-main">Recording</h2>

          <div className="space-y-1.5">
            <span className="flex items-center gap-1.5 text-xs text-text-secondary">
              <Camera size={13} className="text-text-muted" />
              Default lens
            </span>
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
                    className={`flex-1 min-h-[34px] rounded-lg text-xs font-medium transition-all active:scale-[0.98] ${
                      isSelected ? 'bg-primary text-white font-semibold' : 'text-text-secondary hover:text-text-main'
                    }`}
                  >
                    {lens === 'back' ? 'Back' : 'Front'}
                  </button>
                );
              })}
            </div>
          </div>

          <p className="text-[11px] text-text-muted tabular-nums">
            1080p · 30&nbsp;FPS · 10m segments
          </p>
        </section>

        {/* 3. DEFAULT EDITING STYLE */}
        <section className="rounded-2xl bg-surface border border-border p-3.5 space-y-2.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sliders size={15} className="text-primary" />
              <h2 className="text-sm font-semibold tracking-tight text-text-main">Default Editing Style</h2>
            </div>
            <button
              type="button"
              onClick={() => setIsTemplateEditorOpen(true)}
              className="px-3 min-h-[36px] rounded-xl bg-primary hover:bg-primary-hover text-white text-xs font-semibold shadow-hero-glow active:scale-[0.98] transition-all flex items-center gap-1.5"
            >
              <Sliders size={13} />
              Customize
            </button>
          </div>
          <p className="text-xs text-text-secondary leading-relaxed">
            Customize the default framing, caption typography, active word highlight, and hook style applied to newly generated clips.
          </p>
        </section>

        {/* 4. CONNECTED ACCOUNTS (Single service of truth) */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold tracking-tight text-text-main">Connected Accounts</h2>
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
          <div className="space-y-2.5">
            <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-text-secondary">
              <span className="flex items-center gap-1.5"><Youtube size={14} className="text-danger" /> YouTube Channels</span>
              <span className="font-mono">{youtubeAccounts.length} connected</span>
            </div>



            {youtubeAccounts.length > 0 ? (
              <div className="space-y-2.5">
                {youtubeAccounts.map((acc) => {
                  const isSourceVault = acc.metadata?.is_source_vault !== false;
                  const isPublisher = acc.metadata?.is_publisher !== false;

                  return (
                    <div key={acc.id} className="p-3.5 rounded-xl bg-surface-2 border border-border space-y-2.5 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2.5 min-w-0">
                          {acc.avatar_url ? (
                            <img src={acc.avatar_url} alt="" className="w-8 h-8 rounded-full border border-border shrink-0 object-cover" />
                          ) : (
                            <div className="w-8 h-8 rounded-full bg-surface-3 flex items-center justify-center text-text-muted shrink-0">
                              <Youtube size={15} />
                            </div>
                          )}
                          <div className="min-w-0">
                            <p className="font-medium text-text-main truncate text-sm">{acc.account_name}</p>
                            {acc.account_id && acc.account_id !== acc.account_name && acc.account_email !== acc.account_name && (
                              <p className="text-xs text-text-muted truncate font-mono">{acc.account_email || acc.account_id}</p>
                            )}
                          </div>
                        </div>
                        <button
                          onClick={() => handleDisconnectAccount(acc)}
                          className="px-2.5 py-1.5 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors"
                        >
                          Disconnect
                        </button>
                      </div>

                      {/* Role Assignment Buttons */}
                      <div className="flex items-center gap-2 flex-wrap">
                        <button
                          type="button"
                          onClick={() => handleToggleRole(acc, 'source_vault')}
                          className={`min-h-[34px] px-2.5 rounded-lg border text-xs font-medium flex items-center gap-1.5 active:scale-[0.98] transition-all ${
                            isSourceVault
                              ? 'bg-primary/15 border-primary/40 text-primary'
                              : 'bg-surface-3 border-border text-text-muted hover:text-text-secondary'
                          }`}
                          title="Toggle whether raw footage from phone uploads to this channel"
                        >
                          <span>{isSourceVault ? '✓' : '+'}</span>
                          <span>Raw Vault</span>
                        </button>

                        <button
                          type="button"
                          onClick={() => handleToggleRole(acc, 'publisher')}
                          className={`min-h-[34px] px-2.5 rounded-lg border text-xs font-medium flex items-center gap-1.5 active:scale-[0.98] transition-all ${
                            isPublisher
                              ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-400'
                              : 'bg-surface-3 border-border text-text-muted hover:text-text-secondary'
                          }`}
                          title="Toggle whether Shorts can be published to this channel"
                        >
                          <span>{isPublisher ? '✓' : '+'}</span>
                          <span>Shorts Publisher</span>
                        </button>
                      </div>
                    </div>
                  );
                })}
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

          {/* Social Destinations */}
          <div className="pt-3 border-t border-border space-y-2.5">
            <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-text-secondary">
              <span className="flex items-center gap-1.5"><Share2 size={13} className="text-primary" /> Social Destinations</span>
            </div>

            <div className="space-y-2">
              {/* Instagram */}
              <div className="p-3 rounded-xl bg-surface-2 border border-border flex items-center justify-between gap-3 text-sm">
                <div className="flex items-center gap-2.5 min-w-0">
                  <div className="w-8 h-8 rounded-full bg-surface-3 flex items-center justify-center shrink-0">
                    <InstagramIcon />
                  </div>
                  <div className="min-w-0">
                    <p className="font-medium text-text-main truncate">Instagram Reels</p>
                    <p className="text-xs text-text-muted truncate">
                      {instagramAccounts.length > 0 ? instagramAccounts.map(a => a.account_name).join(', ') : 'Not connected'}
                    </p>
                  </div>
                </div>
                {instagramAccounts.length > 0 ? (
                  <button
                    onClick={() => handleDisconnectAccount(instagramAccounts[0])}
                    className="min-h-[36px] px-3 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    onClick={() => handleConnectProvider('instagram')}
                    className="min-h-[36px] px-3 rounded-lg bg-surface-3 hover:bg-surface-1 border border-border text-xs font-medium text-text-main transition-colors"
                  >
                    Connect
                  </button>
                )}
              </div>

              {/* Facebook */}
              <div className="p-3 rounded-xl bg-surface-2 border border-border flex items-center justify-between gap-3 text-sm">
                <div className="flex items-center gap-2.5 min-w-0">
                  <div className="w-8 h-8 rounded-full bg-surface-3 flex items-center justify-center shrink-0">
                    <FacebookIcon />
                  </div>
                  <div className="min-w-0">
                    <p className="font-medium text-text-main truncate">Facebook Pages</p>
                    <p className="text-xs text-text-muted truncate">
                      {facebookAccounts.length > 0 ? facebookAccounts.map(a => a.account_name).join(', ') : 'Not connected'}
                    </p>
                  </div>
                </div>
                {facebookAccounts.length > 0 ? (
                  <button
                    onClick={() => handleDisconnectAccount(facebookAccounts[0])}
                    className="min-h-[36px] px-3 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    onClick={() => handleConnectProvider('facebook')}
                    className="min-h-[36px] px-3 rounded-lg bg-surface-3 hover:bg-surface-1 border border-border text-xs font-medium text-text-main transition-colors"
                  >
                    Connect
                  </button>
                )}
              </div>

              {/* LinkedIn */}
              <div className="p-3 rounded-xl bg-surface-2 border border-border flex items-center justify-between gap-3 text-sm">
                <div className="flex items-center gap-2.5 min-w-0">
                  <div className="w-8 h-8 rounded-full bg-surface-3 flex items-center justify-center shrink-0">
                    <LinkedInIcon />
                  </div>
                  <div className="min-w-0">
                    <p className="font-medium text-text-main truncate">LinkedIn</p>
                    <p className="text-xs text-text-muted truncate">
                      {linkedinAccount ? linkedinAccount.account_name : 'Not connected'}
                    </p>
                  </div>
                </div>
                {linkedinAccount ? (
                  <button
                    onClick={() => handleDisconnectAccount(linkedinAccount)}
                    className="min-h-[36px] px-3 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    onClick={() => handleConnectProvider('linkedin')}
                    className="min-h-[36px] px-3 rounded-lg bg-surface-3 hover:bg-surface-1 border border-border text-xs font-medium text-text-main transition-colors"
                  >
                    Connect
                  </button>
                )}
              </div>

              {/* X (Twitter) */}
              <div className="p-3 rounded-xl bg-surface-2 border border-border flex items-center justify-between gap-3 text-sm">
                <div className="flex items-center gap-2.5 min-w-0">
                  <div className="w-8 h-8 rounded-full bg-surface-3 flex items-center justify-center shrink-0">
                    <XIcon />
                  </div>
                  <div className="min-w-0">
                    <p className="font-medium text-text-main truncate">X (Twitter)</p>
                    <p className="text-xs text-text-muted truncate">
                      {twitterAccount ? twitterAccount.account_name : 'Not connected'}
                    </p>
                  </div>
                </div>
                {twitterAccount ? (
                  <button
                    onClick={() => handleDisconnectAccount(twitterAccount)}
                    className="min-h-[36px] px-3 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    onClick={() => handleConnectProvider('twitter')}
                    className="min-h-[36px] px-3 rounded-lg bg-surface-3 hover:bg-surface-1 border border-border text-xs font-medium text-text-main transition-colors"
                  >
                    Connect
                  </button>
                )}
              </div>
            </div>
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

          <p className="text-[11px] text-text-muted leading-relaxed">
            For clip highlight detection · Encrypted in cloud vault
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
        <section className="rounded-2xl bg-surface border border-border p-3.5 space-y-2.5">
          <h2 className="text-sm font-semibold tracking-tight text-text-main">Uploads</h2>

          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <span className="flex items-center gap-2 text-xs font-medium text-text-main">
                <Wifi size={14} className="text-text-muted" />
                Wi-Fi only upload
              </span>
              <span className="text-[11px] text-text-muted block mt-0.5">
                Never uses mobile data
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
        <section className="rounded-2xl bg-surface border border-border p-3.5 space-y-3.5">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold tracking-tight text-text-main">Storage</h2>
          </div>

          {/* Device Local Storage */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs text-text-secondary">
                <Smartphone size={13} className="text-text-muted" />
                Device buffer
              </span>
              <span className="text-[11px] font-mono tabular-nums text-text-muted">
                {storageInfo?.available && Number.isFinite(storageInfo.freeGb) && Number.isFinite(storageInfo.totalGb)
                  ? <>{storageInfo.freeGb!.toFixed(1)}&nbsp;GB free / {storageInfo.totalGb!.toFixed(1)}&nbsp;GB</>
                  : 'Unavailable'}
              </span>
            </div>

            {storageUsedPct !== null ? (
              <>
                <div className="w-full h-2 rounded-full bg-surface-3 overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full transition-all duration-300"
                    style={{ width: `${storageUsedPct}%` }}
                  />
                </div>
                <div className="flex items-center justify-between text-[11px] text-text-muted">
                  <span>{storageUsedPct}% used · Local recordings</span>
                </div>
              </>
            ) : (
              <p className="text-xs text-text-muted" role="status">
                {storageInfo?.error || 'Local storage information is loading.'}
              </p>
            )}
          </div>

          {/* Recessed Subtle Divider */}
          <div className="h-px bg-border/60" />

          {/* Cloud Storage (Per-Account Quota) */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs text-text-secondary">
                <Cloud size={13} className="text-text-muted" />
                Cloud space (Account)
              </span>
              <span className="text-[11px] font-mono tabular-nums text-text-muted">
                {cloudStorage
                  ? <>{cloudStorage.freeGb.toFixed(1)}&nbsp;GB free / {cloudStorage.quotaGb.toFixed(1)}&nbsp;GB</>
                  : isLoadingCloudStorage ? 'Loading…' : accountEmail ? '2.0&nbsp;GB quota' : 'Sign in required'}
              </span>
            </div>

            {cloudStorage ? (
              <>
                <div className="w-full h-2 rounded-full bg-surface-3 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-300 ${
                      cloudStorage.percentUsed >= 90
                        ? 'bg-danger'
                        : cloudStorage.percentUsed >= 75
                        ? 'bg-warning'
                        : 'bg-primary'
                    }`}
                    style={{ width: `${cloudStorage.percentUsed}%` }}
                  />
                </div>
                <div className="flex items-center justify-between text-[11px] text-text-muted">
                  <span>{cloudStorage.percentUsed}% used · R2 & Cloud highlights</span>
                  {cloudStorage.percentUsed >= 90 && (
                    <span className="text-danger font-medium">Near limit</span>
                  )}
                </div>
              </>
            ) : (
              <p className="text-xs text-text-muted">
                {accountEmail ? 'Fetching cloud quota…' : 'Connect your account to track cloud storage.'}
              </p>
            )}
          </div>
        </section>

      </div>


    </div>
  );
};
