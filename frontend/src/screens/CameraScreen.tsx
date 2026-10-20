import React, { useState, useEffect, useRef, useCallback } from 'react';
import { 
  Zap, 
  ZapOff, 
  Mic, 
  MicOff, 
  SwitchCamera, 
  AlertCircle,
  Video,
  CheckCircle2,
  Sun,
  Lock,
  Unlock,
  RotateCcw,
  Camera,
  Loader2
} from 'lucide-react';
import { Bridge, RecordingItem, ExposureInfo, CameraStateEvent, FocusResultEvent } from '../bridge';

interface CameraScreenProps {
  onRecordingComplete?: (item: RecordingItem) => void;
  onRecordingStatusUpdate?: (segmentId: string, status: string, extra?: Partial<RecordingItem>) => void;
}

type FocusStatus = 'idle' | 'focusing' | 'success' | 'failed' | 'locked';

interface FocusExposureState {
  x: number;
  y: number;
  screenNormX: number;
  screenNormY: number;
  status: FocusStatus;
  triggerId: number;
  isLocked: boolean;
  hasAf: boolean;
  hasAe: boolean;
  timestamp: number;
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
  const [lensOptions, setLensOptions] = useState<number[]>([1.0, 2.0, 3.0]);
  const [cameraFacing, setCameraFacing] = useState<'back' | 'front'>('back');
  const [hasWebcam, setHasWebcam] = useState<boolean>(false);
  const [webcamError, setWebcamError] = useState<string | null>(null);
  const [recentSavedSegment, setRecentSavedSegment] = useState<string | null>(null);

  // Camera initialization and hardware status
  const [cameraStatus, setCameraStatus] = useState<CameraStateEvent>({
    status: Bridge.isAvailable() ? 'initializing' : 'ready',
    lensFacing: 'back',
    isRecording: false,
    isTorchEnabled: false,
    isAeAfLocked: false,
    isAudioEnabled: true,
    exposure: { minIndex: 0, maxIndex: 0, currentIndex: 0, stepEv: 0.5, isSupported: false },
    zoom: { minZoom: 1.0, maxZoom: 10.0, currentZoom: 1.0 },
  });

  // Focus & Exposure states
  const [focusState, setFocusState] = useState<FocusExposureState | null>(null);
  const [isIndicatorVisible, setIsIndicatorVisible] = useState(false);
  const [isDraggingExposure, setIsDraggingExposure] = useState(false);
  const [exposureIndex, setExposureIndex] = useState(0);
  const [exposureInfo, setExposureInfo] = useState<ExposureInfo>({
    minIndex: 0,
    maxIndex: 0,
    currentIndex: 0,
    stepEv: 0.5,
    isSupported: false,
  });
  const [viewfinderBounds, setViewfinderBounds] = useState<{ width: number; height: number }>({
    width: typeof window !== 'undefined' ? window.innerWidth : 360,
    height: typeof window !== 'undefined' ? window.innerHeight : 640
  });

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const timerRef = useRef<any>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedBlobsRef = useRef<Blob[]>([]);

  const viewfinderRef = useRef<HTMLDivElement | null>(null);
  const fadeTimeoutRef = useRef<any>(null);
  const longPressTimerRef = useRef<any>(null);
  const isLongPressHandledRef = useRef(false);
  const pointerDownPosRef = useRef<{ x: number; y: number } | null>(null);
  const startExposureIndexRef = useRef<number>(0);
  const isInteractingWithSliderRef = useRef<boolean>(false);
  const lastSunTapTimeRef = useRef<number>(0);

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

  // Subscribe to native camera lifecycle and focus events
  useEffect(() => {
    const unsub = Bridge.onCameraEvent((event) => {
      if (event.type === 'camera_state') {
        const data = event.data as CameraStateEvent;
        setCameraStatus(data);
        if (data.isAudioEnabled !== undefined) {
          setMicActive(data.isAudioEnabled);
        }
      } else if (event.type === 'focus_result') {
        const result = event.data as FocusResultEvent;
        setFocusState(prev => {
          if (!prev) return prev;
          // Apply if trigger matches or if it's the active focus event
          if (prev.triggerId === result.triggerId || !result.triggerId) {
            const isLocked = !!result.isLocked;
            const newStatus: FocusStatus = isLocked 
              ? (result.success ? 'locked' : 'failed')
              : (result.success ? 'success' : 'failed');

            if (newStatus === 'locked') {
              if (fadeTimeoutRef.current) clearTimeout(fadeTimeoutRef.current);
            } else if (newStatus === 'success') {
              startFadeTimer(2800);
            } else {
              startFadeTimer(1600);
            }

            return {
              ...prev,
              status: newStatus,
              isLocked,
              hasAf: result.hasAf ?? true,
              hasAe: result.hasAe ?? true
            };
          }
          return prev;
        });
      } else if (event.type === 'exposure_result') {
        if (typeof event.data?.appliedIndex === 'number') {
          setExposureIndex(event.data.appliedIndex);
        }
      } else if (event.type === 'unlock_result') {
        setFocusState(prev => {
          if (!prev) return null;
          return {
            ...prev,
            status: 'idle',
            isLocked: false
          };
        });
        startFadeTimer(1500);
      }
    });

    // Initial camera status query
    const initStatus = Bridge.getCameraStatus();
    if (initStatus) {
      setCameraStatus(initStatus);
      if (initStatus.isAudioEnabled !== undefined) {
        setMicActive(initStatus.isAudioEnabled);
      }
    }

    return () => {
      unsub();
    };
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
          video: { 
            facingMode: cameraFacing === 'front' ? 'user' : 'environment',
            width: { ideal: 1920 },
            height: { ideal: 1080 },
            frameRate: { ideal: 30 }
          },
          audio: micActive
        });
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          videoRef.current.play().catch(() => {});
        }
        setHasWebcam(true);
        setWebcamError(null);
        setCameraStatus((prev) => ({ ...prev, status: 'ready', isAudioEnabled: micActive }));
      }
    } catch (err: any) {
      console.warn('Webcam preview unavailable:', err);
      setHasWebcam(false);
      setWebcamError(err.message || 'Webcam access restricted');
      setCameraStatus((prev) => ({ ...prev, status: 'error', error: err.message, isAudioEnabled: false }));
    }
  };

  const stopBrowserWebcam = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
  };

  // Reliable camera hardware capability synchronization (exposure, zoom, facing)
  const syncCameraHardwareState = useCallback(() => {
    const info = Bridge.getExposureInfo();
    if (info) {
      setExposureInfo(info);
      setExposureIndex(info.currentIndex);
    }

    const zoomInfo = Bridge.getZoomInfo();
    if (zoomInfo.minZoom <= 0.75) {
      setLensOptions([0.6, 1.0, 2.0]);
    } else {
      setLensOptions([1.0, 2.0, 3.0]);
    }
  }, []);

  // Sync exposure info and zoom range whenever camera facing updates
  useEffect(() => {
    syncCameraHardwareState();
    const t1 = setTimeout(syncCameraHardwareState, 300);
    const t2 = setTimeout(syncCameraHardwareState, 750);
    const t3 = setTimeout(syncCameraHardwareState, 1500);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
  }, [cameraFacing, syncCameraHardwareState]);

  // Keep track of viewfinder bounds for coordinate clamping
  useEffect(() => {
    const updateBounds = () => {
      if (viewfinderRef.current) {
        const rect = viewfinderRef.current.getBoundingClientRect();
        setViewfinderBounds({ width: rect.width, height: rect.height });
      }
    };
    updateBounds();
    window.addEventListener('resize', updateBounds);
    return () => {
      window.removeEventListener('resize', updateBounds);
      if (fadeTimeoutRef.current) clearTimeout(fadeTimeoutRef.current);
      if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current);
    };
  }, []);

  const startFadeTimer = (ms = 2800) => {
    if (fadeTimeoutRef.current) clearTimeout(fadeTimeoutRef.current);
    fadeTimeoutRef.current = setTimeout(() => {
      setIsIndicatorVisible(false);
      setFocusState(prev => (prev?.isLocked ? prev : null));
    }, ms);
  };

  const checkIsNearSlider = (
    touchX: number,
    touchY: number,
    focus: FocusExposureState,
    bounds: { width: number; height: number }
  ) => {
    const boxSize = 64;
    const halfBox = boxSize / 2;
    const cw = bounds.width || (typeof window !== 'undefined' ? window.innerWidth : 360);
    const ch = bounds.height || (typeof window !== 'undefined' ? window.innerHeight : 640);
    const clampedX = Math.max(halfBox + 16, Math.min(cw - halfBox - 56, focus.x));
    const clampedY = Math.max(halfBox + 40, Math.min(ch - halfBox - 80, focus.y));

    const placeSliderOnLeft = (clampedX + halfBox + 48) > cw;
    const trackX = placeSliderOnLeft ? (clampedX - halfBox - 22) : (clampedX + halfBox + 22);
    const trackHeight = 100;
    const trackTop = clampedY - trackHeight / 2;
    const trackBottom = clampedY + trackHeight / 2;

    // Direct hit test on slider track & sun handle (44px touch bounding box)
    return Math.abs(touchX - trackX) <= 32 && touchY >= (trackTop - 20) && touchY <= (trackBottom + 20);
  };

  const handleUnlockAeAf = (e?: React.MouseEvent | React.PointerEvent) => {
    if (e) e.stopPropagation();
    Bridge.unlockAeAf();
    setFocusState(prev => {
      if (!prev) return null;
      return {
        ...prev,
        status: 'idle',
        isLocked: false
      };
    });
    startFadeTimer(1500);
  };

  const handleResetExposure = (e: React.PointerEvent | React.MouseEvent) => {
    e.stopPropagation();
    Bridge.setExposureIndex(0);
    setExposureIndex(0);
  };

  const handleViewfinderPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;

    // Refresh hardware exposure parameters dynamically on touch
    const freshExp = Bridge.getExposureInfo();
    if (freshExp) {
      setExposureInfo(freshExp);
    }

    const rect = e.currentTarget.getBoundingClientRect();
    const bounds = { width: rect.width, height: rect.height };
    setViewfinderBounds(bounds);

    const clientX = e.clientX;
    const clientY = e.clientY;
    const localX = clientX - rect.left;
    const localY = clientY - rect.top;

    // Hardware coordinate normalization:
    // When running inside Android WebView, PreviewView covers the full screen (window.innerWidth x window.innerHeight).
    // Normalizing directly to screen width/height ensures perfect 1:1 alignment with native PreviewView.meteringPointFactory.
    const screenNormX = Bridge.isAvailable() && window.innerWidth > 0
      ? Math.max(0, Math.min(1, clientX / window.innerWidth))
      : Math.max(0, Math.min(1, localX / rect.width));

    const screenNormY = Bridge.isAvailable() && window.innerHeight > 0
      ? Math.max(0, Math.min(1, clientY / window.innerHeight))
      : Math.max(0, Math.min(1, localY / rect.height));

    pointerDownPosRef.current = { x: clientX, y: clientY };
    isLongPressHandledRef.current = false;
    startExposureIndexRef.current = freshExp ? freshExp.currentIndex : exposureIndex;

    if (fadeTimeoutRef.current) clearTimeout(fadeTimeoutRef.current);
    if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current);

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {}

    // Check if touching dedicated exposure slider track or sun handle
    const isTouchingSlider = isIndicatorVisible && focusState && exposureInfo.isSupported && checkIsNearSlider(localX, localY, focusState, bounds);
    if (isTouchingSlider) {
      isInteractingWithSliderRef.current = true;
      setIsDraggingExposure(true);

      // Check double-tap on sun handle to quickly reset EV to 0
      const now = Date.now();
      if (now - lastSunTapTimeRef.current < 300) {
        Bridge.setExposureIndex(0);
        setExposureIndex(0);
        lastSunTapTimeRef.current = 0;
      } else {
        lastSunTapTimeRef.current = now;
      }
      return;
    }

    isInteractingWithSliderRef.current = false;

    // Setup Long-press (>= 400ms held within 8px touch slop) for AE/AF Lock
    longPressTimerRef.current = setTimeout(() => {
      isLongPressHandledRef.current = true;
      if (typeof navigator !== 'undefined' && navigator.vibrate) {
        try { navigator.vibrate(50); } catch {}
      }
      const triggerId = Date.now();
      Bridge.lockAeAfAtPoint(screenNormX, screenNormY, triggerId);

      setFocusState({
        x: localX,
        y: localY,
        screenNormX,
        screenNormY,
        status: 'focusing',
        triggerId,
        isLocked: true,
        hasAf: true,
        hasAe: true,
        timestamp: triggerId
      });
      setIsIndicatorVisible(true);
    }, 400);
  };

  const handleViewfinderPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!pointerDownPosRef.current) return;

    const dx = e.clientX - pointerDownPosRef.current.x;
    const dy = e.clientY - pointerDownPosRef.current.y;
    const dist = Math.hypot(dx, dy);

    // Cancel long press if moved past touch slop (8px)
    if (dist > 8 && longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }

    // EXPOSURE ADJUSTMENT LAW:
    // Exposure changes *only* via dedicated slider interaction.
    // Accidental vertical swipes on the empty viewfinder NEVER alter exposure.
    if (exposureInfo.isSupported && isInteractingWithSliderRef.current) {
      const totalDragDistancePx = 160;
      const range = Math.max(1, exposureInfo.maxIndex - exposureInfo.minIndex);
      const indexOffset = (-dy / totalDragDistancePx) * range;
      const targetIndex = Math.min(
        exposureInfo.maxIndex,
        Math.max(exposureInfo.minIndex, Math.round(startExposureIndexRef.current + indexOffset))
      );

      if (targetIndex !== exposureIndex) {
        Bridge.setExposureIndex(targetIndex);
        setExposureIndex(targetIndex);
      }
    }
  };

  const handleViewfinderPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }

    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
    } catch {}

    const wasDirectSlider = isInteractingWithSliderRef.current;
    isInteractingWithSliderRef.current = false;
    setIsDraggingExposure(false);

    if (wasDirectSlider) {
      pointerDownPosRef.current = null;
      if (!focusState?.isLocked) {
        startFadeTimer(2800);
      }
      return;
    }

    if (isLongPressHandledRef.current) {
      isLongPressHandledRef.current = false;
      pointerDownPosRef.current = null;
      return;
    }

    // Single Tap detected (Tap-to-Focus)
    if (pointerDownPosRef.current) {
      const rect = e.currentTarget.getBoundingClientRect();
      const localX = e.clientX - rect.left;
      const localY = e.clientY - rect.top;

      const screenNormX = Bridge.isAvailable() && window.innerWidth > 0
        ? Math.max(0, Math.min(1, e.clientX / window.innerWidth))
        : Math.max(0, Math.min(1, localX / rect.width));

      const screenNormY = Bridge.isAvailable() && window.innerHeight > 0
        ? Math.max(0, Math.min(1, e.clientY / window.innerHeight))
        : Math.max(0, Math.min(1, localY / rect.height));

      // If already locked, tap unlocks AE/AF first
      if (focusState?.isLocked) {
        Bridge.unlockAeAf();
      }

      const triggerId = Date.now();
      Bridge.focusOnPoint(screenNormX, screenNormY, triggerId);

      setFocusState({
        x: localX,
        y: localY,
        screenNormX,
        screenNormY,
        status: 'focusing',
        triggerId,
        isLocked: false,
        hasAf: true,
        hasAe: true,
        timestamp: triggerId
      });
      setIsIndicatorVisible(true);

      // Fallback timeout in case hardware/mock bridge doesn't fire callback
      setTimeout(() => {
        setFocusState(curr => {
          if (curr && curr.triggerId === triggerId && curr.status === 'focusing') {
            startFadeTimer(2800);
            return { ...curr, status: 'success' };
          }
          return curr;
        });
      }, 3000);
    }

    pointerDownPosRef.current = null;
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
    setIsIndicatorVisible(false);
    setFocusState(null);
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
            setRecentSavedSegment('Recording uploaded! Clip ready in Clips tab.');
            if (onRecordingStatusUpdate) {
              onRecordingStatusUpdate(segmentId, 'UPLOADED_TO_YOUTUBE', {
                videoUrl: data.video_url || videoBlobUrl,
                sessionId: data.session_id,
              });
            }
          } else {
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

  // Compute EV EV display string (e.g. +1.0 EV, 0.0 EV, -0.5 EV)
  const currentEvFormatted = (exposureIndex * exposureInfo.stepEv).toFixed(1);
  const evPrefix = exposureIndex > 0 ? '+' : '';

  return (
    <div className={`relative w-full h-full flex flex-col justify-between ${Bridge.isAvailable() ? 'bg-transparent' : 'bg-studio'} text-text-main overflow-hidden select-none font-sans`}>
      <style>{`
        @keyframes reticlePop {
          0% { transform: scale(1.25); opacity: 0.8; }
          100% { transform: scale(1); opacity: 1; }
        }
        @keyframes reticlePulse {
          0%, 100% { opacity: 0.9; transform: scale(1); }
          50% { opacity: 0.55; transform: scale(0.96); }
        }
      `}</style>
      
      {/* CAMERA VIEWFINDER AREA WITH TOUCH-TO-FOCUS & EXPOSURE GESTURES */}
      <div 
        ref={viewfinderRef}
        onPointerDown={handleViewfinderPointerDown}
        onPointerMove={handleViewfinderPointerMove}
        onPointerUp={handleViewfinderPointerUp}
        onPointerCancel={handleViewfinderPointerUp}
        style={{ touchAction: 'none' }}
        className={`absolute inset-0 z-0 flex items-center justify-center ${Bridge.isAvailable() ? 'bg-transparent' : 'bg-black'} overflow-hidden cursor-crosshair`}
      >
        {/* Desktop Web Preview */}
        {hasWebcam ? (
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className={`w-full h-full object-cover transition-transform duration-200 pointer-events-none ${
              cameraFacing === 'front' ? 'scale-x-[-1]' : ''
            }`}
            style={{ transform: `scale(${zoomLevel})` }}
          />
        ) : Bridge.isAvailable() ? null : (
          <div className="flex flex-col items-center justify-center p-6 text-center text-text-secondary pointer-events-none">
            <div className="w-16 h-16 rounded-2xl bg-surface-100 border border-border flex items-center justify-center mb-3 shadow-inner">
              <Video className="w-8 h-8 text-primary" />
            </div>
            <p className="text-sm font-semibold text-text-main">Browser Preview Mode</p>
            <p className="text-xs text-text-secondary mt-1 max-w-[260px] leading-relaxed">
              Tap screen to focus, drag vertical slider for exposure, or hold to lock AE/AF.
            </p>
            {webcamError && (
              <span className="text-[10px] text-text-muted mt-2 font-mono">
                Web preview active ({webcamError})
              </span>
            )}
          </div>
        )}

        {/* Viewfinder Grid Overlay with Cinema Crosshair */}
        <div className="absolute inset-0 pointer-events-none grid grid-cols-3 grid-rows-3 border border-white/[0.04]">
          <div className="border-r border-b border-white/[0.06]" />
          <div className="border-r border-b border-white/[0.06]" />
          <div className="border-b border-white/[0.06]" />
          <div className="border-r border-b border-white/[0.06]" />
          <div className="border-r border-b border-white/[0.06] flex items-center justify-center">
            {/* Center Focus Reticle Guide */}
            <div className="w-10 h-10 border border-white/15 rounded-lg flex items-center justify-center">
              <div className="w-1 h-1 rounded-full bg-white/30" />
            </div>
          </div>
          <div className="border-b border-white/[0.06]" />
          <div className="border-r border-b border-white/[0.06]" />
          <div className="border-r border-b border-white/[0.06]" />
          <div />
        </div>

        {/* Interactive Focus Reticle & Exposure Slider Overlay */}
        {isIndicatorVisible && focusState && (
          <div 
            className="absolute inset-0 pointer-events-none transition-opacity duration-300"
            style={{ opacity: isIndicatorVisible ? 1 : 0 }}
          >
            {(() => {
              const boxSize = 64;
              const halfBox = boxSize / 2;
              const cw = viewfinderBounds.width || (typeof window !== 'undefined' ? window.innerWidth : 360);
              const ch = viewfinderBounds.height || (typeof window !== 'undefined' ? window.innerHeight : 640);
              const clampedX = Math.max(halfBox + 16, Math.min(cw - halfBox - 56, focusState.x));
              const clampedY = Math.max(halfBox + 48, Math.min(ch - halfBox - 90, focusState.y));
              
              const placeSliderOnLeft = (clampedX + halfBox + 48) > cw;
              const trackX = placeSliderOnLeft ? (clampedX - halfBox - 22) : (clampedX + halfBox + 22);
              const trackHeight = 100;
              const trackTop = clampedY - trackHeight / 2;
              const trackBottom = clampedY + trackHeight / 2;
              
              const rangeSpan = Math.max(1, exposureInfo.maxIndex - exposureInfo.minIndex);
              const normExp = exposureInfo.isSupported
                ? (exposureIndex - exposureInfo.minIndex) / rangeSpan
                : 0.5;
              const sunY = trackBottom - normExp * (trackBottom - trackTop);

              const isFocusing = focusState.status === 'focusing';
              const isLocked = focusState.status === 'locked' || focusState.isLocked;
              const isFailed = focusState.status === 'failed';

              const reticleColor = isFailed 
                ? '#EF4444' 
                : isLocked 
                ? '#F59E0B' 
                : '#FACC15';
              
              return (
                <>
                  {/* AE/AF Lock Badge + Explicit Unlock Button */}
                  {isLocked && (
                    <div 
                      className="absolute transform -translate-x-1/2 flex items-center gap-1.5 pointer-events-auto"
                      style={{
                        left: `${clampedX}px`,
                        top: `${clampedY - halfBox - 36}px`,
                      }}
                    >
                      <div className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-400 text-black shadow-[0_2px_12px_rgba(0,0,0,0.8)] border border-amber-300 animate-in fade-in zoom-in-95 duration-150">
                        <Lock size={11} strokeWidth={2.8} className="text-black shrink-0" />
                        <span className="text-[10px] font-mono font-bold tracking-tight uppercase">AE/AF LOCK</span>
                      </div>

                      <button
                        onPointerDown={(e) => e.stopPropagation()}
                        onClick={handleUnlockAeAf}
                        aria-label="Unlock AE/AF"
                        className="flex items-center justify-center w-6 h-6 rounded-full bg-black/80 border border-white/30 text-white hover:bg-black active:scale-[0.96] shadow-md transition-all"
                      >
                        <Unlock size={11} strokeWidth={2.2} />
                      </button>
                    </div>
                  )}

                  {/* Focus Failure Toast */}
                  {isFailed && (
                    <div 
                      className="absolute transform -translate-x-1/2 flex items-center gap-1 px-2 py-0.5 rounded-full bg-rose-500/90 text-white shadow-lg pointer-events-none animate-in fade-in duration-150"
                      style={{
                        left: `${clampedX}px`,
                        top: `${clampedY - halfBox - 28}px`,
                      }}
                    >
                      <AlertCircle size={10} strokeWidth={2.5} />
                      <span className="text-[9px] font-mono font-bold uppercase tracking-wide">AF METERING FAILED</span>
                    </div>
                  )}

                  {/* Focus Reticle Square with 4 Corner Brackets */}
                  <div 
                    key={focusState.triggerId}
                    className={`absolute transform -translate-x-1/2 -translate-y-1/2 pointer-events-none transition-transform duration-150 ${
                      isDraggingExposure ? 'scale-95' : 'scale-100'
                    } ${isFocusing ? 'animate-[reticlePulse_600ms_ease-in-out_infinite]' : 'animate-[reticlePop_140ms_ease-out]'}`}
                    style={{
                      left: `${clampedX}px`,
                      top: `${clampedY}px`,
                      width: `${boxSize}px`,
                      height: `${boxSize}px`,
                    }}
                  >
                    <svg 
                      className="w-full h-full drop-shadow-[0_0_2px_rgba(0,0,0,0.9)]" 
                      viewBox="0 0 64 64" 
                      fill="none"
                    >
                      {/* Corner Brackets */}
                      {/* Top-Left */}
                      <path d="M 4 16 L 4 4 L 16 4" stroke={reticleColor} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
                      {/* Top-Right */}
                      <path d="M 48 4 L 60 4 L 60 16" stroke={reticleColor} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
                      {/* Bottom-Right */}
                      <path d="M 60 48 L 60 60 L 48 60" stroke={reticleColor} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
                      {/* Bottom-Left */}
                      <path d="M 16 60 L 4 60 L 4 48" stroke={reticleColor} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
                      
                      {/* Center Dot */}
                      <circle cx="32" cy="32" r="1.5" fill={reticleColor} />
                    </svg>
                  </div>

                  {/* Exposure Slider Track & Sun */}
                  {exposureInfo.isSupported && (
                    <div 
                      className="absolute pointer-events-auto cursor-ns-resize"
                      style={{
                        left: `${trackX - 16}px`,
                        top: `${trackTop - 10}px`,
                        width: '32px',
                        height: `${trackHeight + 20}px`,
                      }}
                      onPointerDown={(e) => {
                        e.stopPropagation();
                        isInteractingWithSliderRef.current = true;
                        setIsDraggingExposure(true);
                      }}
                    >
                      {/* Central Track Line */}
                      <div 
                        className="absolute pointer-events-none"
                        style={{
                          left: '15px',
                          top: '10px',
                          width: '2px',
                          height: `${trackHeight}px`,
                        }}
                      >
                        {/* Track line shadow */}
                        <div className="absolute inset-0 bg-black/70 rounded-full w-[3px] -left-[0.5px]" />
                        {/* Track line white foreground */}
                        <div 
                          className={`absolute inset-0 rounded-full transition-opacity ${
                            isDraggingExposure ? 'bg-white opacity-95 w-[2px]' : 'bg-white/70 opacity-70 w-[1.5px]'
                          }`} 
                        />
                        {/* Top and Bottom notch caps */}
                        <div className="absolute top-0 -left-[3px] w-2 h-[1.5px] bg-white/80 rounded-full shadow" />
                        <div className="absolute bottom-0 -left-[3px] w-2 h-[1.5px] bg-white/80 rounded-full shadow" />

                        {/* Sun Handle Icon */}
                        <div 
                          className={`absolute -translate-x-1/2 -translate-y-1/2 flex items-center justify-center transition-transform duration-75 ${
                            isDraggingExposure ? 'scale-125' : 'scale-100'
                          }`}
                          style={{
                            left: '1px',
                            top: `${sunY - trackTop}px`,
                          }}
                        >
                          <div className="relative w-8 h-8 flex items-center justify-center">
                            <Sun size={20} className="text-amber-400 drop-shadow-[0_1px_4px_rgba(0,0,0,0.9)] fill-amber-400/30" strokeWidth={2} />
                          </div>
                        </div>

                        {/* EV readout bubble while dragging or non-zero */}
                        {(isDraggingExposure || exposureIndex !== 0) && (
                          <div 
                            className="absolute -translate-y-1/2 px-1.5 py-0.5 rounded bg-black/85 border border-white/20 text-[9px] font-mono text-amber-300 tabular-nums shadow-lg pointer-events-none whitespace-nowrap"
                            style={{
                              left: placeSliderOnLeft ? '-42px' : '18px',
                              top: `${sunY - trackTop}px`,
                            }}
                          >
                            {evPrefix}{currentEvFormatted} EV
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </>
              );
            })()}
          </div>
        )}

        {/* Camera Hardware Initialization Overlay */}
        {cameraStatus.status === 'initializing' && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-black/80 backdrop-blur-sm p-6 text-center">
            <div className="w-14 h-14 rounded-2xl bg-surface-100 border border-border flex items-center justify-center mb-3 shadow-lg">
              <Loader2 className="w-7 h-7 text-primary animate-spin" />
            </div>
            <p className="text-sm font-semibold text-text-main">Initializing Camera</p>
            <p className="text-xs text-text-secondary mt-1 font-mono">
              Configuring 1080p FHD sensor…
            </p>
          </div>
        )}

        {/* Camera Permission Required Recovery Overlay */}
        {cameraStatus.status === 'permission_required' && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-black/90 backdrop-blur-md p-6 text-center">
            <div className="w-16 h-16 rounded-2xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-center mb-4 shadow-lg">
              <Camera className="w-8 h-8 text-amber-400" />
            </div>
            <h3 className="text-base font-semibold text-text-main">Camera Permission Required</h3>
            <p className="text-xs text-text-secondary mt-2 max-w-[280px] leading-relaxed">
              Dispatch requires camera permission to capture video sessions and generate instant creator clips.
            </p>
            <button
              onClick={() => Bridge.retryCameraInit()}
              className="mt-6 px-6 py-2.5 rounded-xl bg-primary text-white font-medium text-xs shadow-hero-glow hover:bg-blue-600 active:scale-[0.98] transition-all"
            >
              Grant Permission
            </button>
          </div>
        )}

        {/* Camera Initialization Error Recovery Overlay */}
        {cameraStatus.status === 'error' && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-black/90 backdrop-blur-md p-6 text-center">
            <div className="w-16 h-16 rounded-2xl bg-danger/10 border border-danger/30 flex items-center justify-center mb-4 shadow-lg">
              <AlertCircle className="w-8 h-8 text-danger" />
            </div>
            <h3 className="text-base font-semibold text-text-main">Camera Unavailable</h3>
            <p className="text-xs text-text-secondary mt-2 max-w-[280px] leading-relaxed">
              {cameraStatus.error || "The camera sensor could not be initialized. Another application might be using it."}
            </p>
            <button
              onClick={() => Bridge.retryCameraInit()}
              className="mt-6 flex items-center gap-2 px-5 py-2.5 rounded-xl bg-surface-200 border border-border text-text-main font-medium text-xs hover:bg-surface-300 active:scale-[0.98] transition-all"
            >
              <RotateCcw size={14} />
              Retry Camera
            </button>
          </div>
        )}
      </div>

      {/* TOP CONTROLS BAR */}
      <div 
        onPointerDown={(e) => e.stopPropagation()}
        onPointerMove={(e) => e.stopPropagation()}
        onPointerUp={(e) => e.stopPropagation()}
        className="relative z-10 flex flex-col px-5 pt-4 pb-2 bg-gradient-to-b from-black/85 via-black/45 to-transparent gap-2"
      >
        <div className="flex items-center justify-between">
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

        {/* Audio Disabled Warning Banner (When camera works but mic permission denied) */}
        {cameraStatus.isAudioEnabled === false && (
          <div className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-300 text-[11px] font-mono animate-in fade-in">
            <div className="flex items-center gap-1.5 truncate">
              <MicOff size={12} className="shrink-0" />
              <span className="truncate">Audio disabled (Microphone permission required)</span>
            </div>
            <button
              onClick={() => Bridge.retryCameraInit()}
              className="ml-2 text-white font-semibold underline underline-offset-2 shrink-0 hover:text-amber-200"
            >
              Enable
            </button>
          </div>
        )}
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
            {isRecording ? 'Tap square to stop & finalize chunk' : 'Tap to focus · Hold to lock AE/AF'}
          </span>
        </div>
      </div>

    </div>
  );
};
