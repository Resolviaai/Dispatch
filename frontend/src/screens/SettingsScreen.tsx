import React, { useState, useEffect } from 'react';
import { 
  Settings as SettingsIcon, 
  Youtube, 
  Wifi, 
  LogOut, 
  Sliders, 
  HardDrive 
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
  const [storageInfo, setStorageInfo] = useState<StorageInfo>({ freeGb: 48, totalGb: 128 });
  const [feedback, setFeedback] = useState<string | null>(null);
  const [inputHost, setInputHost] = useState(pcHost);

  const fetchYouTubeStatus = async () => {
    setIsChecking(true);
    try {
      const res = await fetch(apiUrl('/api/integrations/status', pcHost));
      if (res.ok) {
        const data = await res.json();
        setYoutubeState({
          connected: !!data.youtube?.connected,
          channelTitle: data.youtube?.channel_title || '',
          channelId: data.youtube?.channel_id || '',
        });
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
    if (Bridge.isAvailable()) {
      setStorageInfo(Bridge.getStorageInfo());
    }
  }, [pcHost]);

  const handleConnectYouTube = async () => {
    setIsStartingAuth(true);
    setFeedback(null);
    try {
      const res = await fetch(apiUrl('/api/integrations/youtube/start-auth', pcHost), { method: 'POST' });
      const data = await res.json();
      setFeedback(data.message || 'Browser login window started.');
      setTimeout(() => fetchYouTubeStatus(), 5000);
    } catch (e: any) {
      if (Bridge.isAvailable()) {
        const bridgeRes = Bridge.connectYouTube(pcHost);
        setFeedback(bridgeRes.message);
      } else {
        setFeedback(`OAuth launch failed: ${e.message}`);
      }
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

  const handleSaveHost = () => {
    const clean = inputHost.trim().replace(/^https?:\/\//, '').replace(/\/$/, '');
    if (clean) {
      localStorage.setItem('dispatch_pc_host', clean);
      if (onPcHostChange) onPcHostChange(clean);
      setFeedback(`PC Studio Host updated to ${clean}`);
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
          ) : (
            <div className="space-y-2 pt-1">
              <button
                onClick={handleConnectYouTube}
                disabled={isStartingAuth}
                className="w-full min-h-[44px] py-2.5 px-3 rounded-lg bg-primary hover:bg-primary-hover text-white font-mono text-xs font-semibold flex items-center justify-center gap-2 transition-all duration-150 active:scale-[0.98] shadow-hero-glow"
              >
                <Youtube size={16} />
                <span>{isStartingAuth ? 'Opening Browser Login…' : 'Connect YouTube (OAuth)'}</span>
              </button>
              <p className="text-[10px] text-text-muted text-center font-mono">
                Opens official Google OAuth login. No manual tokens required.
              </p>
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
              onClick={() => setWifiOnly(!wifiOnly)}
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
              {storageInfo.freeGb}&nbsp;GB free / {storageInfo.totalGb}&nbsp;GB
            </span>
          </div>

          <div className="w-full h-2 rounded-full bg-surface-300 overflow-hidden mt-1">
            <div 
              className="h-full bg-primary rounded-full transition-all duration-300" 
              style={{ width: `${Math.round(((storageInfo.totalGb - storageInfo.freeGb) / storageInfo.totalGb) * 100)}%` }}
            />
          </div>
          <span className="text-[10px] font-mono text-text-muted block pt-1">
            Location: Internal Storage / DCIM / Dispatch
          </span>
        </div>

      </div>
    </div>
  );
};
