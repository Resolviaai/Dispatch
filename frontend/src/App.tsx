import { useBackHandler } from './navigation/backHandler';
import React, { useState, useEffect } from 'react';
import { AuthGate } from './auth/AuthGate';
import { CameraScreen } from './screens/CameraScreen';
import { RecordingsScreen } from './screens/RecordingsScreen';
import { ClipsScreen } from './screens/ClipsScreen';
import { BottomNav, TabType } from './components/BottomNav';
import { RecordingItem, Bridge, apiUrl, getAuthHeaders } from './bridge';
import { Smartphone, Monitor, Wifi, Battery } from 'lucide-react';
const StudioWorkstation = React.lazy(() => import('./studio/StudioWorkstation').then(m => ({ default: m.StudioWorkstation })));
const SettingsScreen = React.lazy(() => import('./screens/SettingsScreen').then(m => ({ default: m.SettingsScreen })));
import { ScreenErrorBoundary } from './components/ScreenErrorBoundary';
import { ActivityProvider } from './context/ActivityContext';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<TabType>('record');
  const [tabHistory, setTabHistory] = useState<TabType[]>(['record']);

  const handleTabChange = (tab: TabType) => {
    if (tab !== currentTab) {
      setTabHistory(prev => {
        const filtered = prev.filter(t => t !== tab);
        return [...filtered, tab];
      });
    }
    setCurrentTab(tab);
    if (tab === 'record' && Bridge.isAvailable()) {
      Bridge.retryCameraInit();
    }
  };

  useBackHandler('app_tab_history', 10, () => {
    if (tabHistory.length > 1) {
      const nextHistory = [...tabHistory];
      nextHistory.pop();
      const prevTab = nextHistory[nextHistory.length - 1] || 'record';
      setTabHistory(nextHistory);
      setCurrentTab(prevTab);
      if (prevTab === 'record' && Bridge.isAvailable()) {
        Bridge.retryCameraInit();
      }
      return true;
    } else if (currentTab !== 'record') {
      setCurrentTab('record');
      setTabHistory(['record']);
      if (Bridge.isAvailable()) {
        Bridge.retryCameraInit();
      }
      return true;
    }
    return false;
  });
  const [pcHost, setPcHost] = useState<string>(() => {
    if (typeof window !== 'undefined') {
      const native = (window as any).DispatchBridge?.getPcHost?.()?.trim();
      if (native) return native;
    }
    if (typeof localStorage !== 'undefined') {
      const saved = localStorage.getItem('dispatch_pc_host');
      if (saved) return saved;
    }
    if (typeof window !== 'undefined' && (
      window.location.protocol === 'file:' ||
      window.location.hostname === 'appassets.androidplatform.net' ||
      !!(window as any).DispatchBridge
    )) {
      return '';
    }
    if (typeof window !== 'undefined' && window.location.protocol.startsWith('http') && window.location.host) {
      return window.location.host;
    }
    return '';
  });
  const [isMobileFrame, setIsMobileFrame] = useState<boolean>(() => {
    if (typeof window !== 'undefined') {
      if ((window as any).DispatchBridge) return true;
      const saved = localStorage.getItem('dispatch_view_mode');
      if (saved === 'mobile') return true;
      if (saved === 'desktop') return false;
      return window.innerWidth < 1024;
    }
    return false;
  });
  
  const [recordings, setRecordings] = useState<RecordingItem[]>([]);
  const [isYouTubeConnected, setIsYouTubeConnected] = useState<boolean>(false);
  const [isEditorOpen, setIsEditorOpen] = useState<boolean>(false);
  const [sessionFilter, setSessionFilter] = useState<string | null>(null);

  const handleNavigateToClips = (sessionId?: string) => {
    setSessionFilter(sessionId || null);
    setCurrentTab('clips');
  };

  const [isPcOnline, setIsPcOnline] = useState<boolean>(false);

  // Autonomous Connection Healthcheck & Dynamic Self-Healing
  useEffect(() => {
    let isCancelled = false;

    const probeConnection = async () => {
      // 1. Probe the current configured pcHost
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2500);
        const res = await fetch(apiUrl('/api/status', pcHost), { signal: controller.signal, headers: getAuthHeaders() });
        clearTimeout(timeoutId);
        if (res.ok) {
          if (!isCancelled) setIsPcOnline(true);
          return;
        }
      } catch {
        // Current host did not respond
      }

      if (!isCancelled) setIsPcOnline(false);

      // 2. If running on mobile/native WebView and current host failed, probe static LAN anchor
    };

    probeConnection();
    const interval = setInterval(probeConnection, 8000);
    return () => {
      isCancelled = true;
      clearInterval(interval);
    };
  }, [pcHost]);

  // Current system time for phone top bar
  const [timeStr, setTimeStr] = useState<string>('');

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setTimeStr(now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    };
    updateTime();
    const interval = setInterval(updateTime, 10000);
    return () => clearInterval(interval);
  }, []);

  // Manage native camera transparency state
  useEffect(() => {
    if (typeof document !== 'undefined') {
      if (Bridge.isAvailable() && currentTab === 'record') {
        document.documentElement.classList.add('native-camera-preview');
      } else {
        document.documentElement.classList.remove('native-camera-preview');
      }
    }
  }, [currentTab]);

  // Check real YouTube OAuth status (adaptive polling, paused when app is in background)
  useEffect(() => {
    let timerId: any = null;
    let isCancelled = false;

    const checkYouTube = async () => {
      if (document.hidden) {
        schedule(30000);
        return;
      }
      try {
        const res = await fetch(apiUrl('/api/integrations/status', pcHost), { headers: getAuthHeaders() });
        if (res.ok) {
          const data = await res.json();
          setIsYouTubeConnected(!!data.youtube?.connected);
        }
      } catch {
        if (Bridge.isAvailable()) {
          const status = Bridge.getYouTubeStatus();
          setIsYouTubeConnected(status.connected);
        }
      }
      if (!isCancelled) {
        schedule(isYouTubeConnected ? 60000 : 25000);
      }
    };

    const schedule = (ms: number) => {
      clearTimeout(timerId);
      timerId = setTimeout(checkYouTube, ms);
    };

    const handleVisibility = () => {
      if (!document.hidden) {
        checkYouTube();
      }
    };

    checkYouTube();
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      isCancelled = true;
      clearTimeout(timerId);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [pcHost, isYouTubeConnected]);

  // Poll recordings: from Android bridge if running on device, or from backend /api/recordings on web
  useEffect(() => {
    if (Bridge.isAvailable()) {
      const loadBridgeRecordings = () => {
        const items = Bridge.getRecordings();
        if (items) {
          // Truthful states: empty result CLEARS the stale list; in-flight uploads
          // (status UPLOADING) are preserved until they appear in the native list.
          setRecordings(prev => {
            const inFlight = prev.filter(p => p.status === 'UPLOADING' && !items.some(d => d.segmentId === p.segmentId));
            return [...inFlight, ...items];
          });
        }
      };
      loadBridgeRecordings();
      const interval = setInterval(loadBridgeRecordings, 4000);
      return () => clearInterval(interval);
    } else {
      const loadBackendRecordings = async () => {
        try {
          const res = await fetch(apiUrl('/api/recordings', pcHost), { headers: getAuthHeaders() });
          if (res.ok) {
            const data = await res.json();
            if (Array.isArray(data)) {
              setRecordings(prev => {
                // Keep in-flight recordings (e.g. status UPLOADING) that might not be in DB yet
                const inFlight = prev.filter(p => p.status === 'UPLOADING' && !data.some(d => d.segmentId === p.segmentId));
                return [...inFlight, ...data];
              });
            }
          }
        } catch {
          // ignore offline
        }
      };
      loadBackendRecordings();
      const interval = setInterval(loadBackendRecordings, 5000);
      return () => clearInterval(interval);
    }
  }, [pcHost]);

  const handleRecordingComplete = (item: RecordingItem) => {
    setRecordings(prev => [item, ...prev]);
  };

  const handleRecordingStatusUpdate = (segmentId: string, status: string, extra?: Partial<RecordingItem>) => {
    setRecordings(prev => prev.map(rec => {
      if (rec.segmentId === segmentId) {
        return { ...rec, status, ...(extra || {}) };
      }
      return rec;
    }));
  };

  const handleRetryUpload = async (segmentId: string) => {
    if (Bridge.isAvailable()) {
      Bridge.retryUpload(segmentId);
    }
    setRecordings(prev => prev.map(rec => {
      if (rec.segmentId === segmentId) {
        return { ...rec, status: 'UPLOADING' };
      }
      return rec;
    }));

    if (!Bridge.isAvailable()) {
      const item = recordings.find(r => r.segmentId === segmentId);
      if (item?.videoUrl) {
        try {
          const vUrl = item.videoUrl.startsWith('http') ? item.videoUrl : apiUrl(item.videoUrl, pcHost);
          const resp = await fetch(vUrl);
          if (!resp.ok) {
            throw new Error(`Media fetch failed (HTTP ${resp.status})`);
          }
          const blob = await resp.blob();
          const formData = new FormData();
          formData.append('file', blob, 'web_recording.webm');
          const authHeaders = getAuthHeaders();
          const { 'Content-Type': _, ...headersWithoutContentType } = authHeaders;
          const res = await fetch(apiUrl('/api/recordings/upload', pcHost), {
            method: 'POST',
            headers: headersWithoutContentType,
            body: formData,
          });
          if (res.ok) {
            const data = await res.json();
            handleRecordingStatusUpdate(segmentId, 'UPLOADED_TO_YOUTUBE', {
              videoUrl: data.video_url || item.videoUrl,
              sessionId: data.session_id,
            });
          } else {
            handleRecordingStatusUpdate(segmentId, 'FAILED_RECORDING', {
              errorMessage: `Retry HTTP ${res.status}`
            });
          }
        } catch (e: any) {
          handleRecordingStatusUpdate(segmentId, 'FAILED_RECORDING', {
            errorMessage: e?.message || 'Retry failed'
          });
        }
      } else {
        handleRecordingStatusUpdate(segmentId, 'FAILED_RECORDING', {
          errorMessage: 'Recording media URL is unavailable'
        });
      }
    }
  };

  const isNative = Bridge.isAvailable();

  // Desktop Review Studio Workstation (Primary Experience for PC)
  if (!isNative && !isMobileFrame) {
    return (
      <ScreenErrorBoundary title="Studio could not render" resetKey={pcHost}>
        <React.Suspense fallback={<div className="h-full w-full bg-studio flex items-center justify-center text-text-muted">Loading Studio...</div>}>
          <StudioWorkstation
            pcHost={pcHost}
            onSwitchToMobileFrame={() => {
              setIsMobileFrame(true);
              if (typeof localStorage !== 'undefined') {
                localStorage.setItem('dispatch_view_mode', 'mobile');
              }
            }}
          />
        </React.Suspense>
      </ScreenErrorBoundary>
    );
  }

  // Email/password gate: everything below renders only for an authenticated user.
  return (
    <AuthGate>{(session) => (
      <ActivityProvider pcHost={pcHost}>
        <div className={`h-[100dvh] w-full ${isNative && currentTab === 'record' ? 'bg-transparent' : 'bg-studio'} text-text-main flex flex-col items-center overflow-hidden p-0 md:py-2 md:px-3 font-sans`}>
      
      {/* DESKTOP REVIEW CONTROL HEADER (Layer 1: Surface) */}
      <header className="hidden md:flex shrink-0 items-center justify-between w-full max-w-5xl mb-1.5 px-3 py-1.5 rounded-xl bg-surface-100 border border-border">
        <div className="flex items-center gap-2.5">
          <div className="w-2.5 h-2.5 rounded-full bg-success shadow-[0_0_8px_rgba(34,197,94,0.6)]" />
          <div>
            <span className="text-sm font-semibold tracking-tight text-text-main">Dispatch · Creator Engine &amp; Studio</span>
            <span className="text-xs text-text-secondary block">Single UI source of truth · Full PC review &amp; mobile shell</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              setIsMobileFrame(true);
              if (typeof localStorage !== 'undefined') localStorage.setItem('dispatch_view_mode', 'mobile');
            }}
            className={`flex items-center gap-1.5 min-h-[36px] px-2.5 rounded-lg text-sm transition-all active:scale-[0.98] ${
              isMobileFrame
                ? 'bg-primary-soft border border-primary/50 text-text-main font-semibold'
                : 'text-text-secondary hover:text-text-main hover:bg-surface-200 border border-transparent'
            }`}
          >
            <Smartphone size={14} />
            POCO C65 (393×852)
          </button>

          <button
            onClick={() => {
              setIsMobileFrame(false);
              if (typeof localStorage !== 'undefined') localStorage.setItem('dispatch_view_mode', 'desktop');
            }}
            className={`flex items-center gap-1.5 min-h-[36px] px-2.5 rounded-lg text-sm transition-all active:scale-[0.98] ${
              !isMobileFrame
                ? 'bg-primary-soft border border-primary/50 text-text-main font-semibold'
                : 'text-text-secondary hover:text-text-main hover:bg-surface-200 border border-transparent'
            }`}
          >
            <Monitor size={14} />
            Laptop / PC Studio (Wide)
          </button>
        </div>
      </header>

      {/* PRODUCT INTERFACE CONTAINER (Layer 1 Elevated Window / Phone Body) */}
      <div className="flex-1 min-h-0 w-full flex items-center justify-center overflow-hidden">
        <main
          className={`relative flex flex-col ${isNative && currentTab === 'record' ? 'bg-transparent' : 'bg-studio'} overflow-hidden transition-all duration-200 ${
            isMobileFrame
              ? 'w-full h-full md:w-[393px] md:h-full md:max-h-[852px] md:rounded-[36px] md:border md:border-border md:shadow-[0_20px_50px_rgba(0,0,0,0.8)]'
              : 'w-full max-w-5xl h-full md:max-h-[92vh] md:rounded-2xl md:border md:border-border shadow-2xl'
          }`}
        >
          {/* MOBILE HARDWARE NOTCH & STATUS BAR (Only shown in PC desktop browser preview, hidden on Android device) */}
          {isMobileFrame && !isNative && (
            <div className="shrink-0 h-9 px-5 flex items-center justify-between text-xs text-text-secondary select-none z-30 bg-surface-100/90 border-b border-white/[0.06] backdrop-blur">
              <span className="font-semibold text-text-main tabular-nums">{timeStr || '12:00'}</span>
              <div className="w-20 h-3.5 rounded-full bg-canvas mx-auto hidden md:block border border-white/[0.06]" />
              <div className="flex items-center gap-1.5">
                <Wifi size={13} className="text-text-secondary" />
                <Battery size={15} className="text-success" />
              </div>
            </div>
          )}

        {/* ACTIVE SCREEN VIEWPORT */}
        <div className="flex-1 min-h-0 overflow-hidden relative">
          {currentTab === 'record' && (
            <CameraScreen 
              onRecordingComplete={handleRecordingComplete} 
              onRecordingStatusUpdate={handleRecordingStatusUpdate}
              onNativeRecordingStopped={() => handleTabChange('sessions')}
            />
          )}

          {/* Kept mounted across tab switches: cache, editor state and in-flight
              reconciliation survive without a refetch-on-return. */}
          <div className={currentTab === 'sessions' ? 'h-full' : 'hidden'} aria-hidden={currentTab !== 'sessions'}>
            <ScreenErrorBoundary title="Sessions could not render" resetKey={`sessions:${pcHost}`}>
              <RecordingsScreen 
                recordings={recordings} 
                isYouTubeConnected={isYouTubeConnected}
                onNavigateToSettings={() => setCurrentTab('settings')}
                onRetryUpload={handleRetryUpload} 
                onNavigateToClips={handleNavigateToClips}
                onNavigateToRecord={() => handleTabChange('record')}
                onDeleteSession={(sessionId) => setRecordings(prev => prev.filter(r => (r.sessionId || 'direct') !== sessionId && r.segmentId !== sessionId))}
                pcHost={pcHost}
                isPcOnline={isPcOnline}
              />
            </ScreenErrorBoundary>
          </div>

          {/* Kept mounted across tab switches: cache, editor state and in-flight
              reconciliation survive without a refetch-on-return. */}
          <div className={currentTab === 'clips' ? 'h-full' : 'hidden'} aria-hidden={currentTab !== 'clips'}>
            <ScreenErrorBoundary title="Clip editor could not render" resetKey={`clips:${pcHost}`}>
              <ClipsScreen
                pcHost={pcHost}
                isMobileFrame={isMobileFrame}
                isActive={currentTab === 'clips'}
                onEditorOpenChange={setIsEditorOpen}
                isPcOnline={isPcOnline}
                sessionFilter={sessionFilter}
                onClearSessionFilter={() => setSessionFilter(null)}
              />
            </ScreenErrorBoundary>
          </div>

          {currentTab === 'settings' && (
            <React.Suspense fallback={<div className="h-full w-full bg-studio flex items-center justify-center text-text-muted">Loading Settings...</div>}>
              <SettingsScreen pcHost={pcHost} onEditorOpenChange={setIsEditorOpen} />
            </React.Suspense>
          )}
        </div>

        {/* BOTTOM NAVIGATION DECK: Hidden inside Editor for dedicated workspace */}
        {!isEditorOpen && (
          <BottomNav currentTab={currentTab} onTabChange={handleTabChange} />
        )}

        {/* HOME INDICATOR BAR (Only in desktop browser preview, hidden on Android device) */}
        {isMobileFrame && !isNative && (
          <div className={`h-4 ${isEditorOpen ? 'bg-canvas' : 'bg-surface-100'} flex items-center justify-center shrink-0`}>
            <div className="w-32 h-1 rounded-full bg-surface-300" />
          </div>
        )}
      </main>
      </div>
    </div>
    </ActivityProvider>
    )}</AuthGate>
  );
};
