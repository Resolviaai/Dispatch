import React, { useState, useEffect } from 'react';
import { CameraScreen } from './screens/CameraScreen';
import { RecordingsScreen } from './screens/RecordingsScreen';
import { ClipsScreen } from './screens/ClipsScreen';
import { SettingsScreen } from './screens/SettingsScreen';
import { BottomNav, TabType } from './components/BottomNav';
import { RecordingItem, Bridge, apiUrl } from './bridge';
import { Smartphone, Monitor, Wifi, Battery } from 'lucide-react';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<TabType>('record');
  const [isMobileFrame, setIsMobileFrame] = useState<boolean>(true);
  const [pcHost, setPcHost] = useState<string>('localhost:8000');
  
  const [recordings, setRecordings] = useState<RecordingItem[]>([]);
  const [isYouTubeConnected, setIsYouTubeConnected] = useState<boolean>(false);
  const [isEditorOpen, setIsEditorOpen] = useState<boolean>(false);

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

  // Check real YouTube OAuth status
  useEffect(() => {
    const checkYouTube = async () => {
      try {
        const res = await fetch(apiUrl('/api/integrations/status', pcHost));
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
    };
    checkYouTube();
    const interval = setInterval(checkYouTube, 6000);
    return () => clearInterval(interval);
  }, []);

  // Poll recordings: from Android bridge if running on device, or from backend /api/recordings on web
  useEffect(() => {
    if (Bridge.isAvailable()) {
      const loadBridgeRecordings = () => {
        const items = Bridge.getRecordings();
        if (items && items.length > 0) {
          setRecordings(items);
        }
      };
      loadBridgeRecordings();
      const interval = setInterval(loadBridgeRecordings, 4000);
      return () => clearInterval(interval);
    } else {
      const loadBackendRecordings = async () => {
        try {
          const res = await fetch(apiUrl('/api/recordings', pcHost));
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
          const blob = await resp.blob();
          const formData = new FormData();
          formData.append('file', blob, 'web_recording.webm');
          const res = await fetch(apiUrl('/api/recordings/upload', pcHost), {
            method: 'POST',
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
      }
    }
  };

  return (
    <div className="h-[100dvh] w-full bg-studio text-text-main flex flex-col items-center overflow-hidden p-0 md:py-2 md:px-3 font-sans">
      
      {/* DESKTOP REVIEW CONTROL HEADER (Layer 1: Surface 100) */}
      <header className="hidden md:flex shrink-0 items-center justify-between w-full max-w-5xl mb-1.5 px-3 py-1.5 rounded-xl bg-surface-100 border border-border shadow-sm">
        <div className="flex items-center gap-2.5">
          <div className="w-2.5 h-2.5 rounded-full bg-success shadow-[0_0_8px_#22C55E]" />
          <div>
            <span className="text-xs font-semibold tracking-tight text-text-main">DISPATCH · CREATOR ENGINE & STUDIO</span>
            <span className="text-[10px] font-mono text-text-secondary block">Single UI Source of Truth · Full PC Review & Mobile Shell</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsMobileFrame(true)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-mono transition-all active:scale-[0.98] ${
              isMobileFrame 
                ? 'bg-primary/15 border border-primary/50 text-[#60A5FA] font-medium shadow-hero-sm' 
                : 'text-text-secondary hover:text-text-main hover:bg-surface-200 border border-transparent'
            }`}
          >
            <Smartphone size={13} />
            POCO C65 (393×852)
          </button>

          <button
            onClick={() => setIsMobileFrame(false)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-mono transition-all active:scale-[0.98] ${
              !isMobileFrame 
                ? 'bg-primary/15 border border-primary/50 text-[#60A5FA] font-medium shadow-hero-sm' 
                : 'text-text-secondary hover:text-text-main hover:bg-surface-200 border border-transparent'
            }`}
          >
            <Monitor size={13} />
            Laptop / PC Studio (Wide)
          </button>
        </div>
      </header>

      {/* PRODUCT INTERFACE CONTAINER (Layer 1 Elevated Window / Phone Body) */}
      <div className="flex-1 min-h-0 w-full flex items-center justify-center overflow-hidden">
        <main
          className={`relative flex flex-col bg-studio overflow-hidden transition-all duration-200 ${
            isMobileFrame
              ? 'w-full h-full md:w-[393px] md:h-full md:max-h-[852px] md:rounded-[36px] md:border md:border-border md:shadow-[0_20px_50px_rgba(0,0,0,0.8)]'
              : 'w-full max-w-5xl h-full md:max-h-[92vh] md:rounded-2xl md:border md:border-border shadow-2xl'
          }`}
        >
          {/* MOBILE HARDWARE NOTCH & STATUS BAR */}
          {isMobileFrame && (
            <div className="shrink-0 h-9 px-5 flex items-center justify-between text-xs font-mono text-text-secondary select-none z-30 bg-surface-100/90 border-b border-border/40 backdrop-blur">
              <span className="font-semibold text-[12px] text-text-main">{timeStr || '12:00'}</span>
              <div className="w-20 h-3.5 rounded-full bg-studio mx-auto hidden md:block border border-border/40" />
              <div className="flex items-center gap-1.5">
                <Wifi size={12} className="text-text-secondary" />
                <div className="flex items-center text-[10px] gap-0.5 tabular-nums">
                  <span>94%</span>
                  <Battery size={12} className="text-success" />
                </div>
              </div>
            </div>
          )}

        {/* ACTIVE SCREEN VIEWPORT */}
        <div className="flex-1 min-h-0 overflow-hidden relative">
          {currentTab === 'record' && (
            <CameraScreen 
              onRecordingComplete={handleRecordingComplete} 
              onRecordingStatusUpdate={handleRecordingStatusUpdate}
            />
          )}

          {currentTab === 'sessions' && (
            <RecordingsScreen 
              recordings={recordings} 
              isYouTubeConnected={isYouTubeConnected}
              onNavigateToSettings={() => setCurrentTab('settings')}
              onRetryUpload={handleRetryUpload} 
            />
          )}

          {currentTab === 'clips' && (
            <ClipsScreen 
              pcHost={pcHost} 
              isMobileFrame={isMobileFrame} 
              onEditorOpenChange={setIsEditorOpen} 
            />
          )}

          {currentTab === 'settings' && (
            <SettingsScreen pcHost={pcHost} onPcHostChange={setPcHost} />
          )}
        </div>

        {/* BOTTOM NAVIGATION DECK: Hidden inside Editor for dedicated workspace */}
        {!isEditorOpen && (
          <BottomNav currentTab={currentTab} onTabChange={setCurrentTab} />
        )}

        {/* HOME INDICATOR BAR (Mobile View) */}
        {isMobileFrame && (
          <div className={`h-4 ${isEditorOpen ? 'bg-[#16191E]' : 'bg-surface-100'} flex items-center justify-center shrink-0`}>
            <div className="w-32 h-1 rounded-full bg-surface-300" />
          </div>
        )}
      </main>
      </div>

    </div>
  );
};
