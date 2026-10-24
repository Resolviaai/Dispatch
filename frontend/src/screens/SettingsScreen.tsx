import React, { useState, useEffect } from 'react';
import { 
  Settings as SettingsIcon, 
  Youtube, 
  Wifi, 
  LogOut, 
  Sliders, 
  HardDrive,
  Copy,
  KeyRound,
  ShieldAlert,
  RefreshCw
} from 'lucide-react';
import { Bridge, StorageInfo, apiUrl } from '../bridge';

interface SettingsScreenProps {
  pcHost?: string;
  onPcHostChange?: (host: string) => void;
}

export const SettingsScreen: React.FC<SettingsScreenProps> = ({ pcHost = 'localhost:8000', onPcHostChange }) => {
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
  const [inputHost, setInputHost] = useState(pcHost);
  const [pairingCode, setPairingCode] = useState('');
  const [acknowledgeHttpRisk, setAcknowledgeHttpRisk] = useState(false);
  const [isCreatingPairingCode, setIsCreatingPairingCode] = useState(false);
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

  useEffect(() => setInputHost(pcHost), [pcHost]);

  const handleConnectYouTube = async () => {
    if (Bridge.isAvailable()) {
      // 2026-10-24 architecture pivot: YouTube sign-in happens ON THIS PHONE via
      // Google OAuth; the PC never transfers credentials or pairing codes.
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
      if (typeof openTab === 'function') {
        openTab(url);
        setFeedback('Google sign-in opened. Approve access, then return here.');
        setTimeout(fetchYouTubeStatus, 8000);
        return;
      }
      window.open(url, '_blank');
      setFeedback('Google sign-in opened in your browser. Approve access, then return here.');
      setTimeout(fetchYouTubeStatus, 8000);
      return;
      setFeedback('On-device Google sign-in arrives in the next app update. PC credential transfer has been removed for security.');
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

  const handleCreatePairingCode = async () => {
    setIsCreatingPairingCode(true);
    setFeedback(null);
    try {
      const res = await fetch(apiUrl('/api/integrations/youtube/pairing-code', pcHost), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ understands_http_risk: acknowledgeHttpRisk }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Could not create a pairing code.');
      }
      setPairingCode(data.code);
      setFeedback('Pairing code created. Enter it on the phone within five minutes.');
    } catch (e: any) {
      setFeedback(e.message || 'Could not create a pairing code.');
    } finally {
      setIsCreatingPairingCode(false);
    }
  };

  const handleDisconnectYouTube = () => {
    if (Bridge.isAvailable()) {
      Bridge.disconnectYouTube();
    }
    setYoutubeState({ connected: false, channelTitle: '', channelId: '' });
    setFeedback('YouTube disconnected.');
  };

  const [isSavingHost, setIsSavingHost] = useState(false);

  const handleSaveHost = async () => {
    const clean = inputHost.trim().replace(/^https?:\/\//, '').replace(/\/$/, '');
    if (!clean) return;

    setIsSavingHost(true);
    setFeedback(`Testing connection to ${clean}...`);

    // Synchronize to native EncryptedSharedPreferences and WebView localStorage
    Bridge.setPcHost(clean);
    if (onPcHostChange) onPcHostChange(clean);

    try {
      const res = await Bridge.pingPc(clean);
      if (res.success) {
        setFeedback(`Connected to PC Studio at ${clean} (Online)`);
      } else {
        setFeedback(`Saved ${clean}. Ping: ${res.message}. Ensure PC server is running and on same Wi-Fi.`);
      }
    } catch {
      setFeedback(`PC Studio Host updated to ${clean}`);
    } finally {
      setIsSavingHost(false);
    }
  };

  return (
    <div className="w-full h-full flex flex-col bg-studio text-text-main overflow-hidden select-none">
      
      {/* SCREEN HEADER */}
      <div className="shrink-0 px-5 pt-4 pb-3 border-b border-border bg-studio flex items-center justify-between">
        <div>
          <h1 className="text-base font-semibold tracking-tight text-text-main flex items-center gap-2">
            <SettingsIcon size={18} className="text-primary" />
            Settings
          </h1>
          <p className="text-[11px] font-mono text-text-muted mt-0.5">
            Cloud transport &amp; recording preferences
          </p>
        </div>
      </div>

      {/* CONTENT SCROLL AREA - Single-direction vertical flow */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        
        {/* FEEDBACK BANNER */}
        {feedback && (
          <div className="p-3 rounded-lg bg-surface-200 border border-primary/40 text-xs font-mono text-blue-300">
            {feedback}
          </div>
        )}

        {/* 1. YOUTUBE CONNECTION CARD (Layer 1 Surface) */}
        <div className="rounded-xl bg-surface-100 border border-border p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Youtube size={18} className="text-red-500" />
              <span className="text-xs font-semibold text-text-main">YouTube Connection</span>
            </div>

            {/* REAL AUTH STATUS BADGE */}
            {youtubeState.connected ? (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-mono tabular-nums bg-emerald-500/15 border border-emerald-500/30 text-emerald-300">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_#34D399]" />
                Connected
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-mono tabular-nums bg-red-500/15 border border-red-500/30 text-red-300">
                <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                Not connected
              </span>
            )}
          </div>

          <p className="text-xs text-text-muted leading-relaxed">
            Recordings upload directly as private/unlisted videos tagged <code className="text-primary font-mono">[DISPATCH]</code> for PC pipeline ingestion.
          </p>

          {youtubeState.connected ? (
            <div className="space-y-2 pt-1">
              <div className="p-3 rounded-lg bg-surface-200 border border-border flex items-center justify-between text-xs font-mono">
                <span className="text-text-muted">Channel:</span>
                <span className="text-emerald-300 font-semibold truncate max-w-[200px]">
                  {youtubeState.channelTitle || 'Dispatch Authenticated'}
                </span>
              </div>

              <button
                onClick={handleDisconnectYouTube}
                className="w-full min-h-[44px] py-2.5 px-3 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border hover:border-red-500/30 text-text-muted hover:text-red-400 font-mono text-xs flex items-center justify-center gap-2 transition-all duration-150 active:scale-[0.98]"
              >
                <LogOut size={14} />
                <span>Disconnect Account</span>
              </button>
            </div>
          ) : isNativeAndroid ? (
            <div className="space-y-3 pt-1">
              <label className="block text-xs font-mono text-text-main" htmlFor="pc-host">
                PC address
              </label>
              <input
                id="pc-host"
                value={inputHost}
                onChange={(event) => setInputHost(event.target.value)}
                placeholder="192.168.0.200:8000"
                autoCapitalize="none"
                autoCorrect="off"
                className="w-full min-h-[44px] rounded-lg bg-surface-200 border border-border px-3 text-xs font-mono text-text-main placeholder:text-text-muted focus:outline-none focus:border-primary"
              />
              <button
                type="button"
                onClick={handleSaveHost}
                disabled={isSavingHost}
                className="w-full min-h-[44px] rounded-lg bg-surface-200 hover:bg-surface-300 border border-border text-text-main font-mono text-xs transition-all duration-150 active:scale-[0.98] disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {isSavingHost ? <RefreshCw size={14} className="animate-spin text-primary" /> : null}
                <span>{isSavingHost ? 'Verifying Host...' : 'Save PC address'}</span>
              </button>

              <label className="block text-xs font-mono text-text-main" htmlFor="youtube-pairing-code">
                One-time code from PC Settings
              </label>
              <input
                id="youtube-pairing-code"
                value={pairingCode}
                onChange={(event) => setPairingCode(event.target.value.replace(/\D/g, '').slice(0, 4))}
                placeholder="4-digit code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={4}
                className="w-full min-h-[44px] rounded-lg bg-surface-200 border border-border px-3 text-center text-lg font-mono tabular-nums tracking-[0.3em] font-semibold text-text-main placeholder:text-text-muted"
              />
              <div className="rounded-lg bg-surface-200 border border-border p-3 flex gap-2">
                <ShieldAlert size={16} className="shrink-0 text-warning" />
                <p className="text-[10px] font-mono text-text-muted leading-relaxed">
                  Pair only on a trusted network. This HTTP connection is not encrypted; the code authorizes sending your YouTube refresh token and client secret to this phone.
                </p>
              </div>
              <button
                onClick={handleConnectYouTube}
                disabled={pairingCode.length !== 4}
                className="w-full min-h-[44px] py-2.5 px-3 rounded-lg bg-primary hover:bg-primary-hover disabled:opacity-50 disabled:cursor-not-allowed text-white font-mono text-xs font-semibold flex items-center justify-center gap-2 transition-all duration-150 active:scale-[0.98] shadow-hero-glow"
              >
                <KeyRound size={16} />
                <span>Pair this phone</span>
              </button>
            </div>
          ) : (
            <div className="space-y-2 pt-1">
              <button
                onClick={handleConnectYouTube}
                disabled={isStartingAuth}
                className="w-full min-h-[44px] py-2.5 px-3 rounded-lg bg-primary hover:bg-primary-hover text-white font-mono text-xs font-semibold flex items-center justify-center gap-2 transition-all duration-150 active:scale-[0.98] shadow-hero-glow"
              >
                <Youtube size={16} />
                <span>{isStartingAuth ? 'Google sign-in in progress…' : 'Connect YouTube (OAuth)'}</span>
              </button>
              <p className="text-[10px] text-text-muted text-center font-mono">
                Opens official Google OAuth login. No manual tokens required.
              </p>
            </div>
          )}

          {youtubeState.connected && !isNativeAndroid && (
            <div className="mt-3 rounded-lg bg-surface-200 border border-border p-3 space-y-2.5">
              <div className="flex items-center gap-2">
                <KeyRound size={16} className="text-primary" />
                <span className="text-xs font-semibold text-text-main">Connect the phone</span>
              </div>
              <p className="text-[10px] font-mono text-text-muted leading-relaxed">
                The one-time code expires in five minutes and works once. HTTP does not encrypt this transfer: the phone receives your YouTube refresh token and client secret. Use a trusted local network only.
              </p>
              <label className="flex min-h-[44px] items-center gap-2 text-[10px] font-mono text-text-muted">
                <input
                  type="checkbox"
                  checked={acknowledgeHttpRisk}
                  onChange={(event) => setAcknowledgeHttpRisk(event.target.checked)}
                  className="h-4 w-4 accent-blue-600"
                />
                I understand this credential transfer is unencrypted.
              </label>
              <button
                onClick={handleCreatePairingCode}
                disabled={!acknowledgeHttpRisk || isCreatingPairingCode}
                className="w-full min-h-[44px] rounded-lg bg-primary hover:bg-primary-hover disabled:opacity-50 disabled:cursor-not-allowed text-white font-mono text-xs font-semibold flex items-center justify-center gap-2 transition-all duration-150 active:scale-[0.98] shadow-hero-glow"
              >
                <KeyRound size={16} />
                {isCreatingPairingCode ? 'Creating code…' : 'Create phone pairing code'}
              </button>
              {pairingCode && (
                <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-surface-100 px-3 py-2">
                  <span className="text-lg font-mono font-semibold tabular-nums tracking-[0.2em] text-text-main">{pairingCode}</span>
                  <button
                    onClick={() => {
                      navigator.clipboard?.writeText(pairingCode);
                      setFeedback('Pairing code copied. Enter it on the phone now.');
                    }}
                    className="min-h-[44px] min-w-[44px] flex items-center justify-center rounded-lg text-text-muted hover:bg-surface-200 transition-all duration-150 active:scale-[0.98]"
                    aria-label="Copy pairing code"
                  >
                    <Copy size={16} />
                  </button>
                  <span className="text-[10px] font-mono text-text-muted">Use within five minutes</span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* 2. NETWORK & UPLOAD BEHAVIOR */}
        <div className="rounded-xl bg-surface-100 border border-border p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Wifi size={16} className="text-primary" />
            <span className="text-xs font-semibold text-text-main">Network &amp; Upload</span>
          </div>

          <div className="flex items-center justify-between pt-1">
            <div className="pr-4">
              <span className="text-xs font-mono text-text-main block">
                Upload over Wi-Fi automatically
              </span>
              <span className="text-[10px] font-mono text-text-muted block mt-0.5">
                Restricts uploads to unmetered network (never uses mobile data)
              </span>
            </div>

            {/* 44px Hitbox Toggle */}
            <button
              onClick={() => {
                const nextVal = !wifiOnly;
                setWifiOnly(nextVal);
                Bridge.setWifiOnly(nextVal);
              }}
              className="w-12 h-11 flex items-center justify-center active:scale-[0.98] transition-all duration-150"
              title="Toggle Wi-Fi upload"
            >
              <div className={`w-11 h-6 rounded-full transition-colors duration-150 p-0.5 flex items-center ${
                wifiOnly ? 'bg-primary justify-end' : 'bg-surface-300 justify-start'
              }`}>
                <div className="w-5 h-5 rounded-full bg-white shadow-sm" />
              </div>
            </button>
          </div>
        </div>

        {/* 3. CAPTURE SPECIFICATIONS */}
        <div className="rounded-xl bg-surface-100 border border-border p-4 space-y-2.5">
          <div className="flex items-center gap-2">
            <Sliders size={16} className="text-primary" />
            <span className="text-xs font-semibold text-text-main">Recording Profile</span>
          </div>

          <div className="space-y-1.5 text-xs font-mono tabular-nums text-text-muted pt-1">
            <div className="flex justify-between p-2.5 rounded-lg bg-surface-200 border border-border/40">
              <span>Resolution</span>
              <span className="text-text-main font-medium">1080p FHD · 30&nbsp;FPS</span>
            </div>
            <div className="flex justify-between p-2.5 rounded-lg bg-surface-200 border border-border/40">
              <span>Segment Roll</span>
              <span className="text-text-main font-medium">Seamless boundary</span>
            </div>
            <div className="flex justify-between p-2.5 rounded-lg bg-surface-200 border border-border/40">
              <span>Audio Codec</span>
              <span className="text-text-main font-medium">AAC 48&nbsp;kHz Stereo</span>
            </div>
          </div>
        </div>

        {/* 4. POCO C65 DEVICE STORAGE */}
        <div className="rounded-xl bg-surface-100 border border-border p-4 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <HardDrive size={16} className="text-primary" />
              <span className="text-xs font-semibold text-text-main">Storage</span>
            </div>
            <span className="text-xs font-mono tabular-nums text-text-muted">
              {storageInfo?.available && Number.isFinite(storageInfo.freeGb) && Number.isFinite(storageInfo.totalGb)
                ? <>{storageInfo.freeGb!.toFixed(1)}&nbsp;GB free / {storageInfo.totalGb!.toFixed(1)}&nbsp;GB</>
                : 'Unavailable'}
            </span>
          </div>

          {storageInfo?.available && Number.isFinite(storageInfo.freeGb) && Number.isFinite(storageInfo.totalGb) && storageInfo.totalGb! > 0 ? (
            <div className="w-full h-2 rounded-full bg-surface-300 overflow-hidden mt-1">
              <div
                className="h-full bg-primary rounded-full transition-all duration-300"
                style={{ width: `${Math.max(0, Math.min(100, Math.round(((storageInfo.totalGb! - storageInfo.freeGb!) / storageInfo.totalGb!) * 100)))}%` }}
              />
            </div>
          ) : (
            <p className="text-[10px] text-text-muted" role="status">
              {storageInfo?.error || 'Storage information is loading.'}
            </p>
          )}
          <span className="text-[10px] font-mono text-text-muted block pt-1">
            Location: App-specific recording storage
          </span>
        </div>

      </div>
    </div>
  );
};
