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
  Download,
  ExternalLink,
  AlertCircle,
  CheckCircle2,
} from 'lucide-react';
import { TemplateStudio } from '../captions/TemplateStudio';
import { checkAppUpdate, ReleaseManifest } from '../update/updateService';
import { Bridge, StorageInfo, apiUrl, getAuthHeaders } from '../bridge';
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

/** Profile image with the provider's own icon as a failure/unconnected fallback. */
const AccountAvatar: React.FC<{
  src?: string | null;
  fallback: React.ReactNode;
}> = ({ src, fallback }) => {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [src]);

  return (
    <div className="w-8 h-8 rounded-full bg-surface-3 border border-border flex items-center justify-center overflow-hidden shrink-0">
      {src && !failed ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          className="w-full h-full object-cover"
          onError={() => setFailed(true)}
        />
      ) : fallback}
    </div>
  );
};

export const SettingsScreen: React.FC<SettingsScreenProps> = ({ pcHost = 'localhost:8000', onEditorOpenChange }) => {
  const [accounts, setAccounts] = useState<ConnectedAccount[]>([]);
  const [isLoadingAccounts, setIsLoadingAccounts] = useState(false);
  const [geminiStatus, setGeminiStatus] = useState<GeminiKeyStatus>({ configured: false, key_prefix: null });
  const [geminiKeyInput, setGeminiKeyInput] = useState('');
  const [isSavingGemini, setIsSavingGemini] = useState(false);
  const [showGeminiInput, setShowGeminiInput] = useState(false);
  const [geminiError, setGeminiError] = useState<string | null>(null);

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
  // App update state
  const [appVersion, setAppVersion] = useState(() => Bridge.getAppVersionInfo());
  const [isCheckingUpdate, setIsCheckingUpdate] = useState(false);
  const [updateCheckStatus, setUpdateCheckStatus] = useState<'idle' | 'up_to_date' | 'available' | 'error'>('idle');
  const [availableUpdate, setAvailableUpdate] = useState<ReleaseManifest | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [lastCheckedTime, setLastCheckedTime] = useState<string | null>(() => {
    return typeof localStorage !== 'undefined' ? localStorage.getItem('dispatch_last_update_check') : null;
  });

  const handleCheckUpdates = async () => {
    setIsCheckingUpdate(true);
    setUpdateError(null);
    try {
      const res = await checkAppUpdate();
      setAppVersion(res.currentVersion);
      const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      setLastCheckedTime(nowStr);
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('dispatch_last_update_check', nowStr);
      }

      if (res.error) {
        setUpdateCheckStatus('error');
        setUpdateError(res.error);
      } else if (res.hasUpdate && res.latestManifest) {
        setAvailableUpdate(res.latestManifest);
        setUpdateCheckStatus('available');
      } else {
        setAvailableUpdate(null);
        setUpdateCheckStatus('up_to_date');
      }
    } catch (e: any) {
      setUpdateCheckStatus('error');
      setUpdateError(e?.message || 'Check failed');
    } finally {
      setIsCheckingUpdate(false);
    }
  };

  const handleDownloadUpdate = () => {
    if (availableUpdate) {
      const url = `https://github.com/Resolviaai/Dispatch/releases/download/${availableUpdate.releaseTag}/Dispatch.apk`;
      Bridge.openDownloadUrl(url);
    }
  };

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
    const cleanKey = geminiKeyInput.trim();
    if (!cleanKey) return;

    setGeminiError(null);
    if (!cleanKey.startsWith('AIza')) {
      setGeminiError('Invalid format: Gemini API key must start with AIza...');
      return;
    }
    if (cleanKey.length < 30) {
      setGeminiError('API key is too short. Please paste the full Gemini key.');
      return;
    }

    setIsSavingGemini(true);
    setFeedback(null);
    try {
      const { currentSession, supabase } = await import('../auth/supabase');
      let sess = await currentSession().catch(() => null);
      if (!sess?.access_token) {
        const refresh = await supabase.auth.getSession().catch(() => ({ data: { session: null } }));
        sess = refresh.data.session;
      }
      const token = sess?.access_token || jwtToken;

      let cloudSuccess = false;
      let cloudErrorMsg: string | null = null;
      let displayPrefix = `${cleanKey.slice(0, 8)}...${cleanKey.slice(-4)}`;

      if (token) {
        const res = await saveGeminiKey(token, cleanKey);
        if (res.saved) {
          cloudSuccess = true;
          displayPrefix = res.key_prefix || displayPrefix;
        } else {
          cloudErrorMsg = res.error || null;
        }
      }

      // Also sync directly to connected PC workstation
      let pcSuccess = false;
      try {
        const pcRes = await fetch(apiUrl('/api/integrations/gemini', pcHost), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify({ api_key: cleanKey })
        });
        if (pcRes.ok) {
          pcSuccess = true;
        }
      } catch {
        // PC offline
      }

      if (cloudSuccess || pcSuccess) {
        setGeminiStatus({ configured: true, key_prefix: displayPrefix });
        setGeminiKeyInput('');
        setShowGeminiInput(false);
        setGeminiError(null);
        const msg = cloudSuccess && pcSuccess
          ? 'Personal Gemini API key saved to your cloud vault and PC workstation.'
          : cloudSuccess
          ? 'Personal Gemini API key saved to your cloud vault.'
          : 'Gemini API key saved to your PC workstation.';
        setFeedback(msg);
      } else {
        setGeminiError(cloudErrorMsg || 'Could not save Gemini key. Please sign in or check your connection.');
      }
    } catch (e: any) {
      setGeminiError(e.message || 'An error occurred while saving.');
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
  const instagramAccount = instagramAccounts[0] ?? null;
  const facebookAccount = facebookAccounts[0] ?? null;

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

      {/* CONTENT SCROLL AREA — hierarchy from spacing + typography + hairlines,
          not from wrapping every group in another bordered card (anti
          "card-everything" guidance in dispatch-design-system-v3). */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-6">

        {/* FEEDBACK BANNER */}
        {feedback && (
          <div role="status" className="p-3 rounded-xl bg-surface border border-border text-sm text-text-secondary flex items-start justify-between gap-2">
            <span>{feedback}</span>
            <button onClick={() => setFeedback(null)} className="text-xs text-text-muted hover:text-text-main">Dismiss</button>
          </div>
        )}

        {/* ACCOUNT PROFILE ROW (borderless; separated by a hairline) */}
        <section className="pb-1 border-b border-border flex items-center justify-between gap-3">
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
            className="min-h-[44px] px-3 rounded-lg bg-surface-2 hover:bg-surface-3 border border-border text-xs font-medium text-text-secondary hover:text-danger active:scale-[0.98] transition-all flex items-center gap-1.5 shrink-0"
          >
            <LogOut size={13} />
            <span>Sign out</span>
          </button>
        </section>

        {/* 1. APPEARANCE — compact grouped selector, no outer card */}
        <section className="space-y-2.5">
          <h2 className="text-[10px] uppercase tracking-wider font-semibold text-text-secondary">Appearance</h2>
          <div className="grid grid-cols-3 gap-1 p-1 rounded-xl bg-surface-2 border border-border" role="radiogroup" aria-label="Theme">
            {themeOptions.map((opt) => {
              const isSelected = themePref === opt.id;
              const shortLabel = opt.id === 'device' ? 'Device' : opt.label;
              return (
                <button
                  key={opt.id}
                  type="button"
                  role="radio"
                  aria-checked={isSelected}
                  aria-label={opt.label}
                  title={opt.desc}
                  onClick={() => handleThemeChange(opt.id)}
                  className={`min-h-[44px] rounded-lg flex flex-col items-center justify-center gap-0.5 px-2 text-xs font-medium transition-all active:scale-[0.98] ${
                    isSelected
                      ? 'bg-surface border border-border-strong text-text-main shadow-sm'
                      : 'text-text-secondary hover:text-text-main'
                  }`}
                >
                  <span className={isSelected ? 'text-primary' : 'text-text-muted'}>{opt.icon}</span>
                  <span>{shortLabel}</span>
                </button>
              );
            })}
          </div>
        </section>

        {/* 2. RECORDING — one simple row with an inline lens control */}
        <section className="space-y-2.5">
          <h2 className="text-[10px] uppercase tracking-wider font-semibold text-text-secondary">Recording</h2>
          <div className="flex items-center justify-between gap-3">
            <span className="flex items-center gap-1.5 text-sm font-medium text-text-main shrink-0">
              <Camera size={14} className="text-text-muted" />
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
                    className={`min-h-[44px] px-4 rounded-lg text-xs font-medium transition-all active:scale-[0.98] ${
                      isSelected ? 'bg-primary text-white font-semibold' : 'text-text-secondary hover:text-text-main'
                    }`}
                  >
                    {lens === 'back' ? 'Back' : 'Front'}
                  </button>
                );
              })}
            </div>
          </div>
        </section>

        {/* 3. DEFAULT EDITING STYLE — heading + action, no redundant helper text */}
        <section className="flex items-center justify-between gap-3">
          <h2 className="text-[10px] uppercase tracking-wider font-semibold text-text-secondary flex items-center gap-1.5 shrink-0">
            <Sliders size={13} className="text-text-muted" />
            Default Editing Style
          </h2>
          <button
            type="button"
            onClick={() => setIsTemplateEditorOpen(true)}
            className="px-3 min-h-[44px] rounded-xl bg-primary hover:bg-primary-hover text-white text-xs font-semibold shadow-hero-glow active:scale-[0.98] transition-all flex items-center gap-1.5 shrink-0"
          >
            <Sliders size={13} />
            Customize
          </button>
        </section>

        {/* 4. CONNECTED ACCOUNTS (Single service of truth) — the one deliberate
            card on this screen: account entries, roles and disconnect actions
            belong together. Inner rows are hairline-separated, not nested cards. */}
        <section className="rounded-2xl bg-surface border border-border p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold tracking-tight text-text-main">Connected Accounts</h2>
            <button
              onClick={refreshAccounts}
              disabled={isLoadingAccounts}
              className="min-h-[44px] min-w-[44px] p-2 rounded-lg bg-surface-2 hover:bg-surface-3 border border-border text-text-secondary active:scale-[0.98] flex items-center justify-center"
              title="Refresh connected accounts"
            >
              <RefreshCw size={14} className={isLoadingAccounts ? 'animate-spin' : ''} />
            </button>
          </div>

          {/* YouTube Accounts */}
          <div className="space-y-2.5">
            <div className="flex items-center justify-between text-[10px] uppercase tracking-wider font-semibold text-text-secondary">
              <span className="flex items-center gap-1.5"><Youtube size={14} className="text-danger" /> YouTube Channels</span>
              <span className="font-mono">{youtubeAccounts.length} connected</span>
            </div>



            {youtubeAccounts.length > 0 ? (
              <div className="divide-y divide-border/60">
                {youtubeAccounts.map((acc) => {
                  const isSourceVault = acc.metadata?.is_source_vault !== false;
                  const isPublisher = acc.metadata?.is_publisher !== false;

                  return (
                    <div key={acc.id} className="py-3 first:pt-0 space-y-2.5 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2.5 min-w-0">
                          <AccountAvatar
                            src={acc.avatar_url}
                            fallback={<Youtube size={15} className="text-text-muted" />}
                          />
                          <div className="min-w-0">
                            <p className="font-medium text-text-main truncate text-sm">{acc.account_name}</p>
                            {acc.account_id && acc.account_id !== acc.account_name && acc.account_email !== acc.account_name && (
                              <p className="text-xs text-text-muted truncate font-mono">{acc.account_email || acc.account_id}</p>
                            )}
                          </div>
                        </div>
                        <button
                          onClick={() => handleDisconnectAccount(acc)}
                          className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors flex items-center shrink-0"
                        >
                          Disconnect
                        </button>
                      </div>

                      {/* Role Assignment Buttons */}
                      <div className="flex items-center gap-2 flex-wrap">
                        <button
                          type="button"
                          onClick={() => handleToggleRole(acc, 'source_vault')}
                          className={`min-h-[44px] px-3 rounded-lg border text-xs font-medium flex items-center gap-1.5 active:scale-[0.98] transition-all ${
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
                          className={`min-h-[44px] px-3 rounded-lg border text-xs font-medium flex items-center gap-1.5 active:scale-[0.98] transition-all ${
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
            <div className="flex items-center justify-between text-[10px] uppercase tracking-wider font-semibold text-text-secondary">
              <span className="flex items-center gap-1.5"><Share2 size={13} className="text-primary" /> Social Destinations</span>
            </div>

            <div>
              {/* Instagram */}
              <div className="py-3 flex items-center justify-between gap-3 text-sm">
                <div className="flex items-center gap-2.5 min-w-0">
                  <AccountAvatar
                    src={instagramAccount?.avatar_url}
                    fallback={<InstagramIcon />}
                  />
                  <div className="min-w-0">
                    <p className="font-medium text-text-main truncate">Instagram Reels</p>
                    <p className="text-xs text-text-muted truncate">
                      {instagramAccount ? instagramAccount.account_name : 'Not connected'}
                    </p>
                  </div>
                </div>
                {instagramAccount ? (
                  <button
                    onClick={() => handleDisconnectAccount(instagramAccount)}
                    className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors flex items-center shrink-0"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    onClick={() => handleConnectProvider('instagram')}
                    className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-surface-1 border border-border text-xs font-medium text-text-main transition-colors flex items-center shrink-0"
                  >
                    Connect
                  </button>
                )}
              </div>

              {/* Facebook */}
              <div className="py-3 flex items-center justify-between gap-3 text-sm">
                <div className="flex items-center gap-2.5 min-w-0">
                  <AccountAvatar
                    src={facebookAccount?.avatar_url}
                    fallback={<FacebookIcon />}
                  />
                  <div className="min-w-0">
                    <p className="font-medium text-text-main truncate">Facebook Pages</p>
                    <p className="text-xs text-text-muted truncate">
                      {facebookAccount ? facebookAccount.account_name : 'Not connected'}
                    </p>
                  </div>
                </div>
                {facebookAccount ? (
                  <button
                    onClick={() => handleDisconnectAccount(facebookAccount)}
                    className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors flex items-center shrink-0"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    onClick={() => handleConnectProvider('facebook')}
                    className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-surface-1 border border-border text-xs font-medium text-text-main transition-colors flex items-center shrink-0"
                  >
                    Connect
                  </button>
                )}
              </div>

              {/* LinkedIn */}
              <div className="py-3 flex items-center justify-between gap-3 text-sm">
                <div className="flex items-center gap-2.5 min-w-0">
                  <AccountAvatar
                    src={linkedinAccount?.avatar_url}
                    fallback={<LinkedInIcon />}
                  />
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
                    className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors flex items-center shrink-0"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    onClick={() => handleConnectProvider('linkedin')}
                    className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-surface-1 border border-border text-xs font-medium text-text-main transition-colors flex items-center shrink-0"
                  >
                    Connect
                  </button>
                )}
              </div>

              {/* X (Twitter) */}
              <div className="py-3 flex items-center justify-between gap-3 text-sm">
                <div className="flex items-center gap-2.5 min-w-0">
                  <AccountAvatar
                    src={twitterAccount?.avatar_url}
                    fallback={<XIcon />}
                  />
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
                    className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors flex items-center shrink-0"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    onClick={() => handleConnectProvider('twitter')}
                    className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-surface-1 border border-border text-xs font-medium text-text-main transition-colors flex items-center shrink-0"
                  >
                    Connect
                  </button>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* 5. PER-USER GEMINI API KEY — flat section; the status chip carries the
            state, no helper copy or wrapping card */}
        <section className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-[10px] uppercase tracking-wider font-semibold text-text-secondary flex items-center gap-1.5">
              <Sparkles size={13} className="text-text-muted" />
              Gemini AI Key
            </h2>
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

          {geminiStatus.configured ? (
            <div className="p-3 rounded-xl bg-surface-2 border border-border flex items-center justify-between gap-3 text-sm">
              <div className="flex items-center gap-2 min-w-0">
                <Key size={15} className="text-primary shrink-0" />
                <span className="font-mono text-xs text-text-main truncate">{geminiStatus.key_prefix}</span>
              </div>
              <button
                onClick={handleDeleteGeminiKey}
                className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-danger/20 hover:text-danger text-xs font-medium text-text-secondary transition-colors flex items-center"
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
              {geminiError && (
                <p className="text-xs text-danger flex items-center gap-1.5 pt-0.5">
                  <AlertCircle size={13} className="shrink-0" />
                  <span>{geminiError}</span>
                </p>
              )}
              <div className="flex items-center gap-2">
                <button
                  onClick={handleSaveGeminiKey}
                  disabled={isSavingGemini || !geminiKeyInput.trim()}
                  className="flex-1 min-h-[44px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 active:scale-[0.98] disabled:opacity-50"
                >
                  {isSavingGemini ? 'Saving...' : 'Save Gemini Key'}
                </button>
                <button
                  onClick={() => { setShowGeminiInput(false); setGeminiKeyInput(''); setGeminiError(null); }}
                  className="min-h-[44px] px-3 rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-sm text-text-secondary active:scale-[0.98]"
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

        {/* 6. UPLOADS — network behavior toggle, flat row */}
        <section className="space-y-2.5">
          <h2 className="text-[10px] uppercase tracking-wider font-semibold text-text-secondary">Uploads</h2>

          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <span className="flex items-center gap-2 text-sm font-medium text-text-main">
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

        {/* 7. STORAGE — flat section; usage bars are the content, no wrap card */}
        <section className="space-y-3.5">
          <h2 className="text-[10px] uppercase tracking-wider font-semibold text-text-secondary">Storage</h2>

          {/* Device Local Storage */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs text-text-secondary">
                <Smartphone size={13} className="text-text-muted" />
                Device storage
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
          <div className="h-px bg-border" />

          {/* Cloud Storage (Per-Account Quota) */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs text-text-secondary">
                <Cloud size={13} className="text-text-muted" />
                Cloud storage
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
                  <span>{cloudStorage.percentUsed}% used · Cloud files</span>
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

        {/* 8. APP UPDATES & ABOUT — flat section; status banners carry the color */}
        <section className="space-y-3.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h2 className="text-[10px] uppercase tracking-wider font-semibold text-text-secondary">App Updates</h2>
              <span className="text-[11px] font-mono tabular-nums text-text-muted px-2 py-0.5 rounded-full bg-surface-2 border border-border">
                v{appVersion.versionName} ({appVersion.versionCode})
              </span>
            </div>
            {lastCheckedTime && (
              <span className="text-[11px] text-text-muted">
                Checked {lastCheckedTime}
              </span>
            )}
          </div>

          {/* Status & Actions */}
          {updateCheckStatus === 'available' && availableUpdate ? (
            <div className="rounded-xl bg-primary/10 border border-primary/30 p-3.5 space-y-3">
              <div className="flex items-start justify-between gap-2">
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-primary-hover">Update Available</span>
                    <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary text-white">
                      v{availableUpdate.versionName}
                    </span>
                  </div>
                  <p className="text-xs text-text-muted leading-relaxed">
                    {availableUpdate.releaseNotes || 'New improvements and bug fixes available.'}
                  </p>
                </div>
                <span className="text-[11px] font-mono tabular-nums text-text-muted shrink-0 pt-0.5">
                  {(availableUpdate.apkSizeBytes / (1024 * 1024)).toFixed(1)}&nbsp;MB
                </span>
              </div>

              <div className="pt-1 flex flex-col gap-2">
                <button
                  onClick={handleDownloadUpdate}
                  className="w-full min-h-[44px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 shadow-hero-glow active:scale-[0.98] transition-all"
                >
                  <Download size={16} />
                  <span>Download &amp; Install Update</span>
                </button>

                <div className="flex items-center justify-between text-[11px] px-1">
                  <a
                    href={`https://github.com/Resolviaai/Dispatch/releases/tag/${availableUpdate.releaseTag}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-text-muted hover:text-primary transition-colors flex items-center gap-1"
                  >
                    <span>Release notes</span>
                    <ExternalLink size={11} />
                  </a>
                  <button
                    onClick={() => Bridge.openDownloadUrl('https://resolviaai.github.io/Dispatch/Dispatch.apk')}
                    className="text-primary hover:underline"
                  >
                    Direct mirror link
                  </button>
                </div>
              </div>
            </div>
          ) : updateCheckStatus === 'up_to_date' ? (
            <div className="rounded-xl bg-surface-2 border border-border p-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-full bg-success/15 text-success flex items-center justify-center shrink-0">
                  <CheckCircle2 size={15} />
                </div>
                <div>
                  <span className="text-xs font-medium text-text-main block">You're on the latest version</span>
                  <span className="text-[11px] text-text-muted font-mono tabular-nums block">
                    Dispatch v{appVersion.versionName} (build {appVersion.versionCode})
                  </span>
                </div>
              </div>
              <button
                onClick={handleCheckUpdates}
                disabled={isCheckingUpdate}
                className="min-h-[44px] px-3 rounded-lg bg-surface-3 hover:bg-surface-3/80 border border-border text-xs text-text-main flex items-center gap-1.5 active:scale-[0.98] transition-all disabled:opacity-50"
              >
                <RefreshCw size={12} className={isCheckingUpdate ? 'animate-spin' : ''} />
                <span>Check Again</span>
              </button>
            </div>
          ) : updateCheckStatus === 'error' ? (
            <div className="rounded-xl bg-danger/10 border border-danger/30 p-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <AlertCircle size={15} className="text-danger shrink-0" />
                <span className="text-xs text-danger">{updateError || 'Check failed. Verify network.'}</span>
              </div>
              <button
                onClick={handleCheckUpdates}
                className="min-h-[44px] px-3 rounded-lg bg-surface-2 border border-border text-xs text-text-main active:scale-[0.98] transition-all"
              >
                Retry
              </button>
            </div>
          ) : (
            <button
              onClick={handleCheckUpdates}
              disabled={isCheckingUpdate}
              className="w-full min-h-[44px] py-2 px-3 rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-sm font-medium text-text-main flex items-center justify-center gap-2 transition-all active:scale-[0.98] disabled:opacity-50"
            >
              <RefreshCw size={15} className={isCheckingUpdate ? 'animate-spin' : ''} />
              <span>{isCheckingUpdate ? 'Checking for updates…' : 'Check for Updates'}</span>
            </button>
          )}
        </section>

      </div>


    </div>
  );
};
