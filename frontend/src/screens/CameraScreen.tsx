import React, { useState, useEffect, useRef } from 'react';
import { 
  Zap, 
  ZapOff, 
  Mic, 
  MicOff, 
  SwitchCamera, 
  Circle, 
  Square,
  AlertCircle,
  Video,
  CheckCircle2
} from 'lucide-react';
import { Bridge, RecordingItem } from '../bridge';

interface CameraScreenProps {
  onRecordingComplete?: (item: RecordingItem) => void;
  onRecordingStatusUpdate?: (segmentId: string, status: string, extra?: Partial<RecordingItem>) => void;
}

export const CameraScreen: React.FC<CameraScreenProps> = ({ 
  onRecordingComplete,
  onRecordingStatusUpdate 
}) => {
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [torchActive, setTorchActive] = useState(false);
  const [micActive, setMicActive] = useState(true);
  const [zoomLevel, setZoomLevel] = useState<number>(1.0);
  const [lensOptions] = useState<number[]>([1.0, 2.0, 3.0]);
  const [cameraFacing, setCameraFacing] = useState<'back' | 'front'>('back');
  const [hasWebcam, setHasWebcam] = useState<boolean>(false);
  const [webcamError, setWebcamError] = useState<string | null>(null);
  const [recentSavedSegment, setRecentSavedSegment] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const timerRef = useRef<any>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedBlobsRef = useRef<Blob[]>([]);

  // Synchronize native recording status when running inside Dispatch Android WebView
  useEffect(() => {
    if (Bridge.isAvailable()) {
      const syncRecordingState = () => {
        const recording = Bridge.isRecording();
        setIsRecording(recording);
      };
      syncRecordingState();
      const interval = setInterval(syncRecordingState, 1000);
      return () => clearInterval(interval);
    }
  }, []);

  // Initialize webcam for PC browser preview
  useEffect(() => {
    if (!Bridge.isAvailable()) {
      startBrowserWebcam();
    }

    return () => {
      stopBrowserWebcam();
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [cameraFacing]);

  const startBrowserWebcam = async () => {
    stopBrowserWebcam();
    try {
      if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: cameraFacing === 'front' ? 'user' : 'environment' },
          audio: micActive
        });
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          videoRef.current.play().catch(() => {});
        }
        setHasWebcam(true);
        setWebcamError(null);
      }
    } catch (err: any) {
      console.warn('Webcam preview unavailable:', err);
      setHasWebcam(false);
      setWebcamError(err.message || 'Webcam access restricted');
    }
  };

  const stopBrowserWebcam = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
  };

  const handleToggleTorch = () => {
    const nextState = !torchActive;
    setTorchActive(nextState);
    Bridge.toggleTorch();
  };

  const handleToggleMic = () => {
    const nextState = !micActive;
    setMicActive(nextState);
    Bridge.toggleMic();
  };

  const handleSwitchCamera = () => {
    const nextFacing = cameraFacing === 'back' ? 'front' : 'back';
    setCameraFacing(nextFacing);
    Bridge.switchCamera();
  };

  const handleSetZoom = (lvl: number) => {
    setZoomLevel(lvl);
    Bridge.setZoom(lvl);
  };

  const handleStartRecording = () => {
    setIsRecording(true);
    setRecordingSeconds(0);
    setRecentSavedSegment(null);
    Bridge.startRecording();

    recordedBlobsRef.current = [];
    if (streamRef.current) {
      try {
        const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
          ? 'video/webm;codecs=vp9'
          : MediaRecorder.isTypeSupported('video/webm')
          ? 'video/webm'
          : 'video/mp4';
        const mr = new MediaRecorder(streamRef.current, { mimeType });
        mr.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) {
            recordedBlobsRef.current.push(e.data);
          }
        };
        mr.start(500);
        mediaRecorderRef.current = mr;
      } catch (err) {
        console.warn('MediaRecorder error:', err);
      }
    }

    timerRef.current = setInterval(() => {
      setRecordingSeconds(prev => prev + 1);
    }, 1000);
  };

  const handleStopRecording = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    const finalSeconds = recordingSeconds;
    setIsRecording(false);
    Bridge.stopRecording();

    if (Bridge.isAvailable()) {
      setRecentSavedSegment(`Recording finalized (${finalSeconds}s) · Processing segment`);
      setTimeout(() => {
        setRecentSavedSegment(null);
      }, 4000);
      return;
    }

    const segmentTimestamp = Date.now();
    const segmentId = `seg_${segmentTimestamp}`;

    const completeSegment = (videoUrl?: string, realBytes?: number) => {
      const segmentName = `dispatch_${new Date(segmentTimestamp).toISOString().replace(/[:.]/g, '')}_seq1.mp4`;
      setRecentSavedSegment(`${segmentName} (${finalSeconds}s) finalized · ready to preview`);

      if (onRecordingComplete) {
        onRecordingComplete({
          segmentId,
          sessionId: `sess_${segmentTimestamp}`,
          sequenceNumber: 1,
          filename: segmentName,
          filepath: `/storage/emulated/0/DCIM/Dispatch/${segmentName}`,
          fileSizeBytes: realBytes || Math.max(1024 * 1024 * 2, finalSeconds * 2.5 * 1024 * 1024),
          sha256Hash: `hash_${segmentTimestamp}`,
          status: 'QUEUED_FOR_UPLOAD',
          createdAt: segmentTimestamp,
          finalizedAt: segmentTimestamp,
          durationSeconds: Math.max(1, finalSeconds),
          videoUrl: videoUrl
        });
      }

      setTimeout(() => {
        setRecentSavedSegment(null);
      }, 4500);
    };

    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.onstop = async () => {
        const mimeType = mediaRecorderRef.current?.mimeType || 'video/webm';
        const blob = new Blob(recordedBlobsRef.current, { type: mimeType });
        const videoBlobUrl = URL.createObjectURL(blob);
        completeSegment(videoBlobUrl, blob.size);

        // Upload directly to PC pipeline so it immediately enters review studio
        setRecentSavedSegment('Uploading recording to PC pipeline...');
        if (onRecordingStatusUpdate) {
          onRecordingStatusUpdate(segmentId, 'UPLOADING');
        }
        try {
          const formData = new FormData();
          formData.append('file', blob, 'web_recording.webm');
          const res = await fetch('/api/recordings/upload', {
            method: 'POST',
            body: formData,
          });
          if (res.ok) {
            const data = await res.json();
            setRecentSavedSegment('✓ Recording uploaded! Clip ready in Clips tab.');
            if (onRecordingStatusUpdate) {
              onRecordingStatusUpdate(segmentId, 'UPLOADED_TO_YOUTUBE', {
                videoUrl: data.video_url || videoBlobUrl,
                sessionId: data.session_id,
              });
            }
          } else {
            const errText = await res.text();
            setRecentSavedSegment(`Upload failed (${res.status})`);
            if (onRecordingStatusUpdate) {
              onRecordingStatusUpdate(segmentId, 'FAILED_RECORDING', {
                errorMessage: `Upload failed: ${res.status}`
              });
            }
          }
        } catch (e: any) {
          console.warn('Direct upload error:', e);
          setRecentSavedSegment('Upload connection failed');
          if (onRecordingStatusUpdate) {
            onRecordingStatusUpdate(segmentId, 'FAILED_RECORDING', {
              errorMessage: e?.message || 'Connection error'
            });
          }
        }
      };
      mediaRecorderRef.current.stop();
    } else {
      completeSegment();
    }
  };

  const formatTime = (totalSec: number) => {
    const m = Math.floor(totalSec / 60).toString().padStart(2, '0');
    const s = (totalSec % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  };

  return (
    <div className={`relative w-full h-full flex flex-col justify-between ${Bridge.isAvailable() ? 'bg-transparent' : 'bg-studio'} text-text-main overflow-hidden select-none font-sans`}>
      
      {/* CAMERA VIEWFINDER AREA */}
      <div className={`absolute inset-0 z-0 flex items-center justify-center ${Bridge.isAvailable() ? 'bg-transparent' : 'bg-black'} overflow-hidden`}>
        {hasWebcam ? (
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className={`w-full h-full object-cover transition-transform duration-200 ${
              cameraFacing === 'front' ? 'scale-x-[-1]' : ''
            }`}
            style={{ transform: `scale(${zoomLevel})` }}
          />
        ) : Bridge.isAvailable() ? null : (
          <div className="flex flex-col items-center justify-center p-6 text-center text-text-secondary">
            <div className="w-16 h-16 rounded-2xl bg-surface-100 border border-border flex items-center justify-center mb-3 shadow-inner">
              <Video className="w-8 h-8 text-primary" />
            </div>
            <p className="text-sm font-semibold text-text-main">Browser Preview Mode</p>
            <p className="text-xs text-text-secondary mt-1 max-w-[260px] leading-relaxed">
              Connect a webcam or open in Dispatch Android app for live CameraX hardware stream.
            </p>
            {webcamError && (
              <span className="text-[10px] text-text-muted mt-2 font-mono">
                Web preview active ({webcamError})
              </span>
            )}
          </div>
        )}

        {/* Viewfinder Grid Overlay with Cinema Reticle */}
        <div className="absolute inset-0 pointer-events-none grid grid-cols-3 grid-rows-3 border border-white/[0.04]">
          <div className="border-r border-b border-white/[0.06]" />
          <div className="border-r border-b border-white/[0.06]" />
          <div className="border-b border-white/[0.06]" />
          <div className="border-r border-b border-white/[0.06]" />
          <div className="border-r border-b border-white/[0.06] flex items-center justify-center">
            {/* Center Focus Reticle */}
            <div className="w-12 h-12 border border-white/20 rounded-lg flex items-center justify-center">
              <div className="w-1.5 h-1.5 rounded-full bg-white/40" />
            </div>
          </div>
          <div className="border-b border-white/[0.06]" />
          <div className="border-r border-b border-white/[0.06]" />
          <div className="border-r border-b border-white/[0.06]" />
          <div />
        </div>
      </div>

      {/* TOP CONTROLS BAR */}
      <div className="relative z-10 flex items-center justify-between px-5 pt-4 pb-2 bg-gradient-to-b from-black/85 via-black/45 to-transparent">
        {/* Left: Quality & Recording Status & Audio VU */}
        <div className="flex items-center gap-2">
          {isRecording ? (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-danger/20 border border-danger/40 backdrop-blur shadow-[0_0_14px_rgba(239,68,68,0.3)]">
              <span className="w-2.5 h-2.5 rounded-full bg-danger animate-pulse" />
              <span className="text-xs font-mono tabular-nums font-bold text-rose-300 tracking-wider">
                REC {formatTime(recordingSeconds)}
              </span>
              {micActive && (
                <div className="flex items-end gap-0.5 h-3 ml-0.5">
                  <span className="w-0.5 h-2 bg-success rounded-full animate-pulse" />
                  <span className="w-0.5 h-3 bg-success rounded-full animate-pulse" />
                  <span className="w-0.5 h-1.5 bg-success rounded-full animate-pulse" />
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-surface-100/90 border border-border backdrop-blur">
              <span className="w-2 h-2 rounded-full bg-success" />
              <span className="text-[11px] font-mono tabular-nums text-text-secondary font-medium">1080P 30FPS</span>
              {micActive && (
                <div className="flex items-end gap-0.5 h-2.5 opacity-60">
                  <span className="w-0.5 h-1.5 bg-text-muted rounded-full" />
                  <span className="w-0.5 h-2.5 bg-text-muted rounded-full" />
                  <span className="w-0.5 h-1 bg-text-muted rounded-full" />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right: Quick Toggles (Torch, Mic, Flip) - Biomechanical 44px hitboxes */}
        <div className="flex items-center gap-2">
          <button
            onClick={handleToggleTorch}
            disabled={cameraFacing === 'front'}
            aria-label="Toggle Torch"
            className={`w-11 h-11 rounded-full flex items-center justify-center backdrop-blur transition-all active:scale-[0.98] ${
              torchActive 
                ? 'bg-amber-400 text-black shadow-[0_0_12px_rgba(251,191,36,0.5)]' 
                : 'bg-surface-100/80 border border-border text-text-main hover:bg-surface-200 disabled:opacity-30'
            }`}
          >
            {torchActive ? <Zap size={18} fill="currentColor" /> : <ZapOff size={18} />}
          </button>

          <button
            onClick={handleToggleMic}
            aria-label="Toggle Microphone"
            className={`w-11 h-11 rounded-full flex items-center justify-center backdrop-blur transition-all active:scale-[0.98] ${
              micActive 
                ? 'bg-surface-100/80 border border-border text-text-main hover:bg-surface-200' 
                : 'bg-danger text-white shadow-[0_0_12px_rgba(239,68,68,0.4)]'
            }`}
          >
            {micActive ? <Mic size={18} /> : <MicOff size={18} />}
          </button>

          <button
            onClick={handleSwitchCamera}
            aria-label="Switch Camera"
            className="w-11 h-11 rounded-full flex items-center justify-center bg-surface-100/80 border border-border text-text-main hover:bg-surface-200 backdrop-blur transition-all active:scale-[0.98]"
          >
            <SwitchCamera size={18} />
          </button>
        </div>
      </div>

      {/* NOTIFICATION TOAST */}
      {recentSavedSegment && (
        <div className="relative z-10 mx-4 my-2 p-3 rounded-xl bg-surface-100 border border-border backdrop-blur flex items-center gap-2.5 shadow-xl animate-in fade-in slide-in-from-top-2">
          <CheckCircle2 size={16} className="text-success shrink-0" />
          <span className="text-[11px] font-mono tabular-nums text-text-main truncate">
            {recentSavedSegment}
          </span>
        </div>
      )}

      {/* BOTTOM CONTROLS DECK */}
      <div className="relative z-10 flex flex-col items-center pb-6 pt-2 bg-gradient-to-t from-black/95 via-black/60 to-transparent">
        
        {/* Zoom Selector Pills (0.6x, 1x, 2x, 3x) */}
        <div className="flex items-center gap-1.5 p-1 rounded-full bg-surface-100/90 border border-border backdrop-blur mb-6 shadow-md">
          {lensOptions.map((lvl) => {
            const isSelected = zoomLevel === lvl;
            return (
              <button
                key={lvl}
                onClick={() => handleSetZoom(lvl)}
                className={`min-w-[44px] min-h-[32px] py-1 px-3 rounded-full text-xs font-mono tabular-nums transition-all active:scale-[0.98] ${
                  isSelected 
                    ? 'bg-primary text-white font-semibold shadow-hero-sm' 
                    : 'text-text-secondary hover:text-text-main'
                }`}
              >
                {lvl}x
              </button>
            );
          })}
        </div>

        {/* Big Shutter / Record Button */}
        <div className="flex items-center justify-center">
          {isRecording ? (
            <button
              onClick={handleStopRecording}
              aria-label="Stop Recording"
              className="relative w-20 h-20 rounded-full flex items-center justify-center border-4 border-danger/90 bg-danger/20 backdrop-blur transition-all active:scale-[0.98] shadow-[0_0_28px_rgba(239,68,68,0.5)]"
            >
              <span className="w-8 h-8 rounded-md bg-danger transition-all hover:bg-rose-400 shadow-md" />
            </button>
          ) : (
            <button
              onClick={handleStartRecording}
              aria-label="Start Recording"
              className="relative w-20 h-20 rounded-full flex items-center justify-center border-4 border-white/80 bg-white/10 backdrop-blur transition-all active:scale-[0.98] hover:border-white shadow-xl"
            >
              <span className="w-14 h-14 rounded-full bg-danger transition-all hover:bg-rose-500 shadow-md" />
            </button>
          )}
        </div>

        {/* Context Help Text */}
        <div className="mt-3 text-center">
          <span className="text-[11px] font-mono text-text-muted">
            {isRecording ? 'Tap square to stop & finalize chunk' : 'Tap to start recording'}
          </span>
        </div>
      </div>

    </div>
  );
};
