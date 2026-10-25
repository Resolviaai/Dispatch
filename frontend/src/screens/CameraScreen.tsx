import { Slider } from '../components/ui/slider';
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
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
  Pause,
  Play,
  Plus,
  RotateCcw,
  Camera,
  Loader2
} from 'lucide-react';
import { Bridge, RecordingItem, ExposureInfo, CameraStateEvent, FocusResultEvent, ExposureResultEvent, triggerHaptic } from '../bridge';
import { getDefaultLensPref } from './SettingsScreen';
import {
  applyExposureResult,
  applyFocusResult,
  applyNativeLockState,
  applyUnlockResult,
  beginPointerSession,
  crossedIntegerZoomBoundary,
  endPointerSession,
  exposureIndexForPointerMove,
  FocusExposureState,
  isCameraLifecycleTransition,
  markLockAttemptTimedOut,
  PointerSession,
  shouldUnlockAndRefocusOnTap,
  sliderFractionFromZoomRatio,
  zoomRatioFromSliderFraction,
} from '../camera/cameraInteractionState';

interface CameraScreenProps {
  onRecordingComplete?: (item: RecordingItem) => void;
  onRecordingStatusUpdate?: (segmentId: string, status: string, extra?: Partial<RecordingItem>) => void;
}

/**
 * Continuous zoom slider shown only while recording. Idle recording-off state
 * keeps the discrete zoom pills; recording swaps them for this slider.
 */
/**
 * Continuous zoom slider shown only while recording.
 * Studio-grade, high-contrast, frosted glass capsule with preset jump chips,
 * optical graduation marks, glowing digital readout, and tactile micro-ticks.
 */
const RecordingZoomSlider: React.FC<{
  minZoom: number;
  maxZoom: number;
  ratio: number;
  onChange: (ratio: number) => void;
}> = ({ minZoom, maxZoom, ratio, onChange }) => {
  const lastHapticRatioRef = useRef(ratio);
  const effectiveMin = Math.max(0.5, Number.isFinite(minZoom) ? minZoom : 1.0);
  const effectiveMax = Math.max(effectiveMin + 2, Number.isFinite(maxZoom) && maxZoom > effectiveMin ? maxZoom : 5.0);

  const handleValueChange = (vals: number[]) => {
    const nextRatio = Math.round(vals[0] * 10) / 10;
    if (Math.abs(Math.floor(nextRatio * 2) - Math.floor(lastHapticRatioRef.current * 2)) >= 1) {
      triggerHaptic('zoom_tick');
      lastHapticRatioRef.current = nextRatio;
    }
    onChange(nextRatio);
  };

  return (
    <div
      className="mb-6 flex w-[280px] h-11 items-center gap-2.5 px-3.5 rounded-full bg-black/65 border border-white/20 backdrop-blur-md shadow-[0_2px_12px_rgba(0,0,0,0.5)]"
      data-interactive
    >
      {/* 1x Quick Snap Chip */}
      <button
        type="button"
        onClick={() => {
          triggerHaptic('lens_switch');
          onChange(1.0);
        }}
        className={`min-w-[34px] h-7 px-2 rounded-full text-xs font-mono font-medium transition-all active:scale-95 flex items-center justify-center ${
          Math.abs(ratio - 1.0) < 0.2
            ? 'bg-primary text-white font-semibold shadow-hero-glow'
            : 'text-white/80 hover:text-white bg-white/5'
        }`}
      >
        1x
      </button>

      {/* shadcn / Radix Slider */}
      <Slider
        min={effectiveMin}
        max={effectiveMax}
        step={0.1}
        value={[ratio]}
        onValueChange={handleValueChange}
        className="flex-1"
      />

      {/* Precision Digital Readout & Step Chip */}
      <button
        type="button"
        onClick={() => {
          triggerHaptic('lens_switch');
          const target = ratio < 2.0 ? 2.0 : ratio < 3.0 ? 3.0 : ratio < effectiveMax ? effectiveMax : 1.0;
          onChange(target);
        }}
        className="min-w-[42px] h-7 px-2 rounded-full bg-white/10 hover:bg-white/20 text-white font-mono text-xs font-semibold tabular-nums active:scale-95 transition-all flex items-center justify-center"
        title="Tap to cycle zoom presets (1x, 2x, 3x, 5x)"
      >
        {ratio.toFixed(1)}x
      </button>
    </div>
  );
};

export const CameraScreen: React.FC<CameraScreenProps> = ({ 
  onRecordingComplete,
  onRecordingStatusUpdate 
}) => {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
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
  const cameraStatusRef = useRef(cameraStatus);

  // Focus & Exposure states
  const [focusState, setFocusState] = useState<FocusExposureState | null>(null);
  const [isIndicatorVisible, setIsIndicatorVisible] = useState(false);
  const [isDraggingExposure, setIsDraggingExposure] = useState(false);
  const [exposureIndex, setExposureIndex] = useState(0);
  const [exposureError, setExposureError] = useState<string | null>(null);
  const [exposureInfo, setExposureInfo] = useState<ExposureInfo>({
    minIndex: 0,
    maxIndex: 0,
    currentIndex: 0,
    stepEv: 0.5,
    isSupported: false,
  });
  const exposureInfoRef = useRef(exposureInfo);
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
  const lockTimeoutRef = useRef<any>(null);
  const unlockTimeoutRef = useRef<any>(null);
  const isLongPressHandledRef = useRef(false);
  const pointerSessionRef = useRef<PointerSession | null>(null);
  const pointerCaptureRef = useRef<{ pointerId: number; target: HTMLElement } | null>(null);
  const lastExposureRequestRef = useRef<number | null>(null);
  const lastSunTapTimeRef = useRef<number>(0);
  const lastTriggerIdRef = useRef(0);
  const isPausedRef = useRef(false);
  const lastZoomIntegerRef = useRef(1);
  const chainedUnlockFocusRef = useRef<number | null>(null);
  const [lockDimmed, setLockDimmed] = useState(false);

  // Apply the user's default camera choice (Settings → Recording) once, when the
  // camera is first ready. A ref guard ensures we never fight a manual flip.
  const appliedDefaultLensRef = useRef(false);
  useEffect(() => {
    if (appliedDefaultLensRef.current) return;
    if (cameraStatus.status !== 'ready') return;
    appliedDefaultLensRef.current = true;
    const wanted = getDefaultLensPref();
    if (wanted === 'front' && cameraFacing === 'back') {
      setCameraFacing('front');
      Bridge.switchCamera();
    }
  }, [cameraStatus.status, cameraFacing]);

  // Synchronize native recording status when running inside Dispatch Android WebView
  useEffect(() => {
    if (Bridge.isAvailable()) {
      const syncRecordingState = () => {
        const recording = Bridge.isRecording();
        setIsRecording(recording);
        const paused = recording && Bridge.isRecordingPaused();
        if (paused !== isPausedRef.current) {
          isPausedRef.current = paused;
          setIsPaused(paused);
        }
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
        const previous = cameraStatusRef.current;
        cameraStatusRef.current = data;
        setCameraStatus(data);
        setCameraFacing(data.lensFacing);
        if (data.isAudioEnabled !== undefined) {
          setMicActive(data.isAudioEnabled);
        }
        if (data.exposure) {
          exposureInfoRef.current = data.exposure;
          setExposureInfo(data.exposure);
          setExposureIndex(data.exposure.currentIndex);
        }
        if (isCameraLifecycleTransition(previous, data)) {
          if (lockTimeoutRef.current) clearTimeout(lockTimeoutRef.current);
          if (unlockTimeoutRef.current) clearTimeout(unlockTimeoutRef.current);
          setFocusState(null);
          setIsIndicatorVisible(false);
        } else if (typeof data.isAeAfLocked === 'boolean') {
          setFocusState((prev) => {
            if (!prev && (data.isAeAfLocked || data.isAeAfLockUncertain)) {
              const x = (viewfinderRef.current?.clientWidth || window.innerWidth) / 2;
              const y = (viewfinderRef.current?.clientHeight || window.innerHeight) / 2;
              return {
                x, y, screenNormX: 0.5, screenNormY: 0.5,
                status: data.isAeAfLockUncertain ? 'uncertain' : 'locked', triggerId: Date.now(), isLocked: data.isAeAfLocked && !data.isAeAfLockUncertain,
                hasAf: data.isAfLocked ?? false, hasAe: data.isAeLocked ?? false, timestamp: Date.now(),
                error: data.isAeAfLockUncertain ? 'Camera lock state is uncertain; unlock to recover safely.' : undefined,
              };
            }
            return applyNativeLockState(prev, data.isAeAfLocked, data);
          });
          setIsIndicatorVisible(data.isAeAfLocked || !!data.isAeAfLockUncertain);
        }
      } else if (event.type === 'focus_result') {
        const result = event.data as FocusResultEvent;
        if (result.triggerId === chainedUnlockFocusRef.current) {
          chainedUnlockFocusRef.current = null;
        }
        if (result.triggerId === lastTriggerIdRef.current && lockTimeoutRef.current) {
          clearTimeout(lockTimeoutRef.current);
          lockTimeoutRef.current = null;
        }
        setFocusState(prev => {
          const next = applyFocusResult(prev, result);
          if (next?.status === 'locked' || next?.status === 'uncertain') {
            if (fadeTimeoutRef.current) clearTimeout(fadeTimeoutRef.current);
          } else if (next?.status === 'success') {
            startFadeTimer(2800);
          } else if (next?.status === 'failed') {
            startFadeTimer(2200);
          }
          return next;
        });
      } else if (event.type === 'exposure_result') {
        const result = event.data as ExposureResultEvent;
        if (result.success) {
          const applied = applyExposureResult(exposureInfoRef.current.currentIndex, result);
          setExposureIndex(applied.index);
          setExposureError(applied.error);
        } else {
          const actual = Bridge.getExposureInfo();
          if (actual) {
            exposureInfoRef.current = actual;
            setExposureInfo(actual);
            setExposureIndex(actual.currentIndex);
          }
          const rejected = applyExposureResult(actual?.currentIndex ?? exposureInfoRef.current.currentIndex, result);
          setExposureError(rejected.error);
          lastExposureRequestRef.current = null;
          startFadeTimer(4500);
        }
      } else if (event.type === 'unlock_result') {
        // A chained unlock+focus tap owns the reticle until its focus_result lands.
        if (chainedUnlockFocusRef.current !== null) return;
        const success = event.data?.success === true;
        if (unlockTimeoutRef.current) clearTimeout(unlockTimeoutRef.current);
        unlockTimeoutRef.current = null;
        setFocusState(prev => {
          const next = applyUnlockResult(prev, success);
          if (!success && (next?.isLocked || next?.status === 'uncertain')) setIsIndicatorVisible(true);
          return next;
        });
        if (success) {
          setIsIndicatorVisible(false);
          startFadeTimer(1500);
        }
      }
    });

    // Initial camera status query
    const initStatus = Bridge.getCameraStatus();
    if (initStatus) {
      cameraStatusRef.current = initStatus;
      setCameraStatus(initStatus);
      setCameraFacing(initStatus.lensFacing);
      exposureInfoRef.current = initStatus.exposure;
      setExposureInfo(initStatus.exposure);
      setExposureIndex(initStatus.exposure.currentIndex);
      if (initStatus.isAudioEnabled !== undefined) {
        setMicActive(initStatus.isAudioEnabled);
      }
      if (initStatus.isAeAfLocked || initStatus.isAeAfLockUncertain) {
        const x = (viewfinderRef.current?.clientWidth || window.innerWidth) / 2;
        const y = (viewfinderRef.current?.clientHeight || window.innerHeight) / 2;
        const triggerId = Date.now();
        setFocusState({ x, y, screenNormX: 0.5, screenNormY: 0.5, status: initStatus.isAeAfLockUncertain ? 'uncertain' : 'locked', triggerId, isLocked: initStatus.isAeAfLocked && !initStatus.isAeAfLockUncertain, hasAf: initStatus.isAfLocked ?? false, hasAe: initStatus.isAeLocked ?? false, timestamp: triggerId, error: initStatus.isAeAfLockUncertain ? 'Camera lock state is uncertain; unlock to recover safely.' : undefined });
        setIsIndicatorVisible(true);
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

  // Reliable stream attachment callback ref: guarantees stream connects when conditional video element mounts
  const attachStreamToVideo = useCallback((videoEl: HTMLVideoElement | null) => {
    videoRef.current = videoEl;
    if (videoEl && streamRef.current) {
      if (videoEl.srcObject !== streamRef.current) {
        videoEl.srcObject = streamRef.current;
        videoEl.play().catch(() => {});
      }
    }
  }, []);

  // Post-mount synchronization effect for stream attachment
  useEffect(() => {
    if (hasWebcam && videoRef.current && streamRef.current) {
      if (videoRef.current.srcObject !== streamRef.current) {
        videoRef.current.srcObject = streamRef.current;
        videoRef.current.play().catch(() => {});
      }
    }
  }, [hasWebcam]);

  // Reliable camera hardware capability synchronization (exposure, zoom, facing)
  const syncCameraHardwareState = useCallback(() => {
    const info = Bridge.getExposureInfo();
    if (info) {
      exposureInfoRef.current = info;
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
      if (lockTimeoutRef.current) clearTimeout(lockTimeoutRef.current);
      if (unlockTimeoutRef.current) clearTimeout(unlockTimeoutRef.current);
    };
  }, []);

  // Once a lock is confirmed, keep the reticle at full opacity for one second,
  // then settle it to ~55% so it stays visible without dominating the preview.
  useEffect(() => {
    const locked = focusState?.isLocked === true && focusState.status === 'locked';
    setLockDimmed(false);
    if (!locked) return;
    const t = setTimeout(() => setLockDimmed(true), 1000);
    return () => clearTimeout(t);
  }, [focusState?.triggerId, focusState?.isLocked, focusState?.status]);

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

  const requestExposureIndex = (index: number) => {
    lastExposureRequestRef.current = index;
    setExposureError(null);
    Bridge.setExposureIndex(index);
  };

  const nextTriggerId = () => {
    const triggerId = Math.max(Date.now(), lastTriggerIdRef.current + 1);
    lastTriggerIdRef.current = triggerId;
    return triggerId;
  };

  const finishLockAttempt = (triggerId: number, error: string) => {
    setFocusState((prev) => markLockAttemptTimedOut(prev, triggerId) || prev);
    if (unlockTimeoutRef.current) clearTimeout(unlockTimeoutRef.current);
    unlockTimeoutRef.current = setTimeout(() => {
      const status = Bridge.getCameraStatus();
      if (status) {
        cameraStatusRef.current = status;
        setCameraStatus(status);
        setCameraFacing(status.lensFacing);
        setFocusState((prev) => {
          if (status.isAeAfLocked || status.isAeAfLockUncertain) {
            return applyNativeLockState(prev, status.isAeAfLocked, status);
          }
          return prev?.triggerId === triggerId
            ? { ...prev, status: 'failed', isLocked: false, error }
            : prev;
        });
        setIsIndicatorVisible(!!status.isAeAfLocked || !!status.isAeAfLockUncertain);
      } else {
        setFocusState((prev) => prev?.triggerId === triggerId
          ? { ...prev, status: 'failed', isLocked: false, error }
          : prev);
      }
      unlockTimeoutRef.current = null;
      startFadeTimer(4500);
    }, 1600);
  };

  const handleExposureSliderPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    e.stopPropagation();
    e.preventDefault();

    const freshExposure = Bridge.getExposureInfo() || exposureInfoRef.current;
    exposureInfoRef.current = freshExposure;
    setExposureInfo(freshExposure);
    if (!freshExposure.isSupported) {
      setExposureError('Exposure compensation is not supported by this camera');
      return;
    }

    if (focusState?.status === 'locking' || focusState?.status === 'unlocking' || focusState?.status === 'uncertain') return;
    capturePointer(e, true, freshExposure.currentIndex);
    isLongPressHandledRef.current = false;
    lastExposureRequestRef.current = freshExposure.currentIndex;
    setIsDraggingExposure(true);
    if (fadeTimeoutRef.current) clearTimeout(fadeTimeoutRef.current);

    const now = Date.now();
    if (now - lastSunTapTimeRef.current < 300) {
      requestExposureIndex(0);
      lastSunTapTimeRef.current = 0;
    } else {
      lastSunTapTimeRef.current = now;
    }
  };

  const capturePointer = (event: React.PointerEvent<HTMLElement>, isExposureSlider: boolean, startIndex: number) => {
    pointerSessionRef.current = beginPointerSession(event.pointerId, event.clientX, event.clientY, startIndex, isExposureSlider);
    pointerCaptureRef.current = { pointerId: event.pointerId, target: event.currentTarget };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      pointerCaptureRef.current = null;
    }
  };

  const releasePointerCapture = (pointerId: number) => {
    const capture = pointerCaptureRef.current;
    pointerSessionRef.current = endPointerSession(pointerSessionRef.current, pointerId);
    if (!capture || capture.pointerId !== pointerId) return;
    pointerCaptureRef.current = null;
    try {
      if (capture.target.hasPointerCapture(pointerId)) capture.target.releasePointerCapture(pointerId);
    } catch { }
  };

  const clearPointerInteraction = (pointerId: number, releaseCapture: boolean) => {
    const session = pointerSessionRef.current;
    if (!session || session.pointerId !== pointerId) return null;
    if (releaseCapture) releasePointerCapture(pointerId);
    else {
      pointerSessionRef.current = endPointerSession(session, pointerId);
      pointerCaptureRef.current = null;
    }
    if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current);
    longPressTimerRef.current = null;
    setIsDraggingExposure(false);
    return session;
  };

  const handleViewfinderPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;

    const freshExposure = Bridge.getExposureInfo() || exposureInfoRef.current;
    exposureInfoRef.current = freshExposure;
    setExposureInfo(freshExposure);

    const rect = e.currentTarget.getBoundingClientRect();
    const bounds = { width: rect.width, height: rect.height };
    setViewfinderBounds(bounds);
    const localX = e.clientX - rect.left;
    const localY = e.clientY - rect.top;
    const screenNormX = Bridge.isAvailable() && window.innerWidth > 0
      ? Math.max(0, Math.min(1, e.clientX / window.innerWidth))
      : Math.max(0, Math.min(1, localX / rect.width));
    const screenNormY = Bridge.isAvailable() && window.innerHeight > 0
      ? Math.max(0, Math.min(1, e.clientY / window.innerHeight))
      : Math.max(0, Math.min(1, localY / rect.height));

    const isTouchingSlider = isIndicatorVisible && focusState && freshExposure.isSupported && checkIsNearSlider(localX, localY, focusState, bounds);
    const lockInProgress = focusState?.status === 'locking' || focusState?.status === 'unlocking' || focusState?.status === 'uncertain';
    if (lockInProgress || (focusState?.isLocked && !isTouchingSlider)) return;

    capturePointer(e, !!isTouchingSlider, freshExposure.currentIndex);
    isLongPressHandledRef.current = false;
    if (fadeTimeoutRef.current) clearTimeout(fadeTimeoutRef.current);
    if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current);

    if (isTouchingSlider) {
      lastExposureRequestRef.current = freshExposure.currentIndex;
      setIsDraggingExposure(true);
      const now = Date.now();
      if (now - lastSunTapTimeRef.current < 300) {
        requestExposureIndex(0);
        lastSunTapTimeRef.current = 0;
      } else {
        lastSunTapTimeRef.current = now;
      }
      return;
    }

    longPressTimerRef.current = setTimeout(() => {
      const activeSession = pointerSessionRef.current;
      if (!activeSession || activeSession.pointerId !== e.pointerId || activeSession.isExposureSlider) return;
      isLongPressHandledRef.current = true;
      if (typeof navigator !== 'undefined' && navigator.vibrate) {
        try { navigator.vibrate(50); } catch { }
      }

      const triggerId = nextTriggerId();
      setFocusState({
        x: localX, y: localY, screenNormX, screenNormY,
        status: 'locking', triggerId, isLocked: false,
        hasAf: true, hasAe: true, timestamp: triggerId,
      });
      setIsIndicatorVisible(true);
      try {
        triggerHaptic('lock');
      const accepted = Bridge.lockAeAfAtPoint(screenNormX, screenNormY, triggerId);
        if (!accepted) {
          setFocusState((prev) => prev?.triggerId === triggerId
            ? { ...prev, status: 'failed', error: 'Camera rejected the AE/AF lock request' }
            : prev);
          startFadeTimer(2200);
          return;
        }
        lockTimeoutRef.current = setTimeout(() => {
          lockTimeoutRef.current = null;
          finishLockAttempt(triggerId, 'Lock confirmation timed out; camera focus state could not be confirmed');
        }, 5000);
      } catch (err) {
        setFocusState((prev) => prev?.triggerId === triggerId
          ? { ...prev, status: 'failed', error: err instanceof Error ? err.message : 'Camera rejected the AE/AF lock request' }
          : prev);
        startFadeTimer(2200);
      }
    }, 400);
  };

  const handleViewfinderPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const session = pointerSessionRef.current;
    if (!session || session.pointerId !== e.pointerId) return;

    const dx = e.clientX - session.originX;
    const dy = e.clientY - session.originY;
    if (Math.hypot(dx, dy) > 8 && longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }

    const targetIndex = exposureIndexForPointerMove(
      session,
      e.pointerId,
      e.clientX,
      e.clientY,
      exposureInfoRef.current
    );
    if (targetIndex !== null && targetIndex !== lastExposureRequestRef.current) {
      requestExposureIndex(targetIndex);
    }
  };

  // Single tap on a locked/uncertain reticle: unlock and move focus to the tapped
  // point, matching the native camera contract.
  const handleTapUnlockAndFocus = (e: React.PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const localX = e.clientX - rect.left;
    const localY = e.clientY - rect.top;
    const screenNormX = Bridge.isAvailable() && window.innerWidth > 0
      ? Math.max(0, Math.min(1, e.clientX / window.innerWidth))
      : Math.max(0, Math.min(1, localX / rect.width));
    const screenNormY = Bridge.isAvailable() && window.innerHeight > 0
      ? Math.max(0, Math.min(1, e.clientY / window.innerHeight))
      : Math.max(0, Math.min(1, localY / rect.height));
    const triggerId = nextTriggerId();
    chainedUnlockFocusRef.current = triggerId;
    Bridge.unlockAndFocus(screenNormX, screenNormY, triggerId);
    setFocusState({ x: localX, y: localY, screenNormX, screenNormY, status: 'focusing', triggerId, isLocked: false, hasAf: true, hasAe: true, timestamp: triggerId });
    setIsIndicatorVisible(true);
    setTimeout(() => {
      setFocusState((current) => {
        if (current && current.triggerId === triggerId && current.status === 'focusing') {
          startFadeTimer(2800);
          return { ...current, status: 'success' };
        }
        return current;
      });
    }, 3000);
  };

  const handleViewfinderPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const session = clearPointerInteraction(e.pointerId, true);
    if (!session) {
      // While locked, pointer-down intentionally opens no gesture session; the tap
      // lands here and must unlock + refocus (unless it hit a real control).
      if (shouldUnlockAndRefocusOnTap(focusState)) {
        const target = e.target as HTMLElement | null;
        if (!target?.closest('button, [data-interactive]')) {
          handleTapUnlockAndFocus(e);
        }
      }
      return;
    }

    if (session.isExposureSlider) {
      if (!focusState?.isLocked) startFadeTimer(2800);
      return;
    }
    if (isLongPressHandledRef.current) {
      isLongPressHandledRef.current = false;
      return;
    }
    if (focusState?.isLocked || focusState?.status === 'locking' || focusState?.status === 'unlocking' || focusState?.status === 'uncertain') return;

    const rect = e.currentTarget.getBoundingClientRect();
    const localX = e.clientX - rect.left;
    const localY = e.clientY - rect.top;
    const screenNormX = Bridge.isAvailable() && window.innerWidth > 0
      ? Math.max(0, Math.min(1, e.clientX / window.innerWidth))
      : Math.max(0, Math.min(1, localX / rect.width));
    const screenNormY = Bridge.isAvailable() && window.innerHeight > 0
      ? Math.max(0, Math.min(1, e.clientY / window.innerHeight))
      : Math.max(0, Math.min(1, localY / rect.height));
    const triggerId = nextTriggerId();
    triggerHaptic('tick');
    Bridge.focusOnPoint(screenNormX, screenNormY, triggerId);
    setFocusState({ x: localX, y: localY, screenNormX, screenNormY, status: 'focusing', triggerId, isLocked: false, hasAf: true, hasAe: true, timestamp: triggerId });
    setIsIndicatorVisible(true);
    setTimeout(() => {
      setFocusState((current) => {
        if (current && current.triggerId === triggerId && current.status === 'focusing') {
          startFadeTimer(2800);
          return { ...current, status: 'success' };
        }
        return current;
      });
    }, 3000);
  };

  const handleViewfinderPointerCancel = (e: React.PointerEvent<HTMLDivElement>) => {
    const session = clearPointerInteraction(e.pointerId, false);
    if (session) isLongPressHandledRef.current = false;
  };

  const handleViewfinderLostPointerCapture = (e: React.PointerEvent<HTMLDivElement>) => {
    const session = clearPointerInteraction(e.pointerId, false);
    if (session) isLongPressHandledRef.current = false;
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
    triggerHaptic('recording_start');
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

    isPausedRef.current = false;
    setIsPaused(false);
    timerRef.current = setInterval(() => {
      if (!isPausedRef.current) setRecordingSeconds(prev => prev + 1);
    }, 1000);
  };

  const handleStopRecording = () => {
    triggerHaptic('recording_stop');
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    const finalSeconds = recordingSeconds;
    setIsRecording(false);
    isPausedRef.current = false;
    setIsPaused(false);
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

  const handlePauseToggle = () => {
    triggerHaptic('toggle');
    if (!isRecording) return;
    const next = !isPaused;
    isPausedRef.current = next;
    setIsPaused(next);
    if (next) Bridge.pauseRecording();
    else Bridge.resumeRecording();
    const mr = mediaRecorderRef.current;
    if (mr) {
      try {
        if (next && mr.state === 'recording') mr.pause();
        else if (!next && mr.state === 'paused') mr.resume();
      } catch { }
    }
  };

  const handleSliderZoom = (nextRatio: number) => {
    setZoomLevel(nextRatio);
    Bridge.setZoom(nextRatio);
    if (crossedIntegerZoomBoundary(lastZoomIntegerRef.current, nextRatio)) {
      triggerHaptic('zoom_tick');
    }
    lastZoomIntegerRef.current = Math.round(nextRatio);
  };

  const formatTime = (totalSec: number) => {
    const m = Math.floor(totalSec / 60).toString().padStart(2, '0');
    const s = (totalSec % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  };

  // Compute EV EV display string (e.g. +1.0 EV, 0.0 EV, -0.5 EV)

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
        @keyframes reticleLockPop {
          0% { transform: scale(1); }
          45% { transform: scale(1.13); }
          100% { transform: scale(1); }
        }
      `}</style>
      
      {/* CAMERA VIEWFINDER AREA WITH TOUCH-TO-FOCUS & EXPOSURE GESTURES */}
      <div 
        ref={viewfinderRef}
        onPointerDown={handleViewfinderPointerDown}
        onPointerMove={handleViewfinderPointerMove}
        onPointerUp={handleViewfinderPointerUp}
        onPointerCancel={handleViewfinderPointerCancel}
        onLostPointerCapture={handleViewfinderLostPointerCapture}
        style={{ touchAction: 'none' }}
        className={`absolute inset-0 z-0 flex items-center justify-center ${Bridge.isAvailable() ? 'bg-transparent' : 'bg-black'} overflow-hidden cursor-crosshair`}
      >
        {/* Desktop Web Preview */}
        {hasWebcam ? (
          <video
            ref={attachStreamToVideo}
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
              Tap to focus, drag exposure, double-tap the sun to reset, or hold to lock AE/AF.
            </p>
            {webcamError && (
              <span className="text-xs text-text-muted mt-2 font-mono">
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
            {/* Center Plus Marker */}
            <Plus size={18} strokeWidth={1.5} className="text-white/40" />
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

              const isFocusing = focusState.status === 'focusing' || focusState.status === 'locking';
              const isLocked = focusState.status === 'locked' || focusState.isLocked;
              const isLockUncertain = focusState.status === 'uncertain';
              // Native camera behaviour: a failed tap is silent — the reticle never
              // turns red; only a confirmed lock changes it to amber.
              const reticleColor = isLocked ? '#F59E0B' : '#FACC15';
              
              return (
                <>
                  {/* Focus Reticle Square with 4 Corner Brackets */}
                  <div 
                    key={focusState.triggerId}
                    className="absolute pointer-events-none"
                    style={{
                      left: `${clampedX}px`,
                      top: `${clampedY}px`,
                      width: `${boxSize}px`,
                      height: `${boxSize}px`,
                      transform: `translate(-50%, -50%) scale(${isDraggingExposure ? 0.95 : 1})`,
                      transition: 'transform 150ms ease',
                    }}
                  >
                    <div
                      className={`w-full h-full ${
                        isFocusing
                          ? 'animate-[reticlePulse_600ms_ease-in-out_infinite]'
                          : isLocked
                          ? 'animate-[reticleLockPop_320ms_cubic-bezier(0.34,1.56,0.64,1)]'
                          : 'animate-[reticlePop_140ms_ease-out]'
                      }`}
                      style={{
                        opacity: isLocked && lockDimmed ? 0.55 : 1,
                        transition: 'opacity 500ms ease',
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
                        
                        {/* Center Dot (hidden while locked; the lock icon takes the center) */}
                        {!isLocked && <circle cx="32" cy="32" r="1.5" fill={reticleColor} />}
                      </svg>

                      {/* Lock icon centered inside the reticle once the lock is confirmed */}
                      {isLocked && !isFocusing && (
                        <div className="absolute inset-0 flex items-center justify-center">
                          <Lock size={15} strokeWidth={2.4} className="text-amber-300 drop-shadow-[0_1px_3px_rgba(0,0,0,0.9)]" />
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Exposure Slider Track & Sun */}
                  {exposureInfo.isSupported && (
                    <div 
                      data-interactive
                      className="absolute pointer-events-auto cursor-ns-resize"
                      style={{
                        left: `${trackX - 16}px`,
                        top: `${trackTop - 10}px`,
                        width: '32px',
                        height: `${trackHeight + 20}px`,
                      }}
                      onPointerDown={handleExposureSliderPointerDown}
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
                      </div>
                    </div>
                  )}
                  {exposureError && (
                    <div
                      role="status"
                      className="absolute pointer-events-none left-1/2 bottom-5 -translate-x-1/2 rounded-lg chip-danger border bg-surface px-3 py-2 text-center text-xs font-medium"
                    >
                      {exposureError}
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
            <p className="text-xs text-text-secondary mt-1">
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
            <p className="text-sm text-text-secondary mt-2 max-w-[280px] leading-relaxed">
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
            <p className="text-sm text-text-secondary mt-2 max-w-[280px] leading-relaxed">
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
        className="relative z-10 flex flex-col px-5 pt-4 pb-2 bg-transparent gap-2"
      >
        <div className="flex items-center justify-between">
          {/* Left: Quality & Recording Status & Audio VU */}
          <div className="flex items-center gap-2">
            {isRecording ? (
              <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border backdrop-blur ${
                isPaused
                  ? 'bg-amber-500/20 border-amber-400/40 shadow-[0_0_14px_rgba(245,158,11,0.3)]'
                  : 'bg-danger/20 border-danger/40 shadow-[0_0_14px_rgba(239,68,68,0.3)]'
              }`}>
                <span className={`w-2.5 h-2.5 rounded-full ${isPaused ? 'bg-amber-400' : 'bg-danger animate-pulse'}`} />
                <span className={`text-xs font-mono tabular-nums font-bold tracking-wider ${isPaused ? 'text-amber-300' : 'text-rose-300'}`}>
                  {isPaused ? 'PAUSED' : 'REC'} {formatTime(recordingSeconds)}
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
              <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-full bg-black/65 border border-white/20 backdrop-blur-md shadow-[0_2px_8px_rgba(0,0,0,0.5)] text-white">
                <span className="w-2 h-2 rounded-full bg-success drop-shadow-[0_0_4px_rgba(34,197,94,0.8)]" />
                <span className="text-xs tabular-nums font-semibold tracking-wide drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)]">1080P 30FPS</span>
                {micActive && (
                  <div className="flex items-end gap-0.5 h-2.5 opacity-80">
                    <span className="w-0.5 h-1.5 bg-white rounded-full" />
                    <span className="w-0.5 h-2.5 bg-white rounded-full" />
                    <span className="w-0.5 h-1 bg-white rounded-full" />
                  </div>
                )}
              </div>
            )}
          </div>

                    {/* Right: Quick Toggles (Switch camera on left, Flashlight on rightmost) */}
          <div className="flex items-center gap-2">
            {!isRecording && (
              <button
                onClick={() => {
                  triggerHaptic('lens_switch');
                  handleSwitchCamera();
                }}
                aria-label="Switch Camera"
                className="w-11 h-11 rounded-full flex items-center justify-center bg-black/65 border border-white/20 text-white hover:bg-black/75 backdrop-blur-md shadow-[0_2px_8px_rgba(0,0,0,0.5)] transition-all active:scale-[0.95]"
              >
                <SwitchCamera size={18} className="drop-shadow-[0_1px_2px_rgba(0,0,0,0.9)]" />
              </button>
            )}

            <button
              onClick={() => {
                triggerHaptic('toggle');
                handleToggleTorch();
              }}
              disabled={cameraFacing === 'front'}
              aria-label="Toggle Torch"
              className={`w-11 h-11 rounded-full flex items-center justify-center backdrop-blur-md border transition-all active:scale-[0.95] ${
                torchActive 
                  ? 'bg-amber-400 text-black border-amber-300 shadow-[0_0_14px_rgba(251,191,36,0.6)]' 
                  : 'bg-black/65 border-white/20 text-white shadow-[0_2px_8px_rgba(0,0,0,0.5)] hover:bg-black/75 disabled:opacity-30'
              }`}
            >
              {torchActive ? (
                <Zap size={18} fill="currentColor" className="drop-shadow-[0_1px_2px_rgba(0,0,0,0.5)]" />
              ) : (
                <ZapOff size={18} className="drop-shadow-[0_1px_2px_rgba(0,0,0,0.9)]" />
              )}
            </button>
          </div>
        </div>

        {/* Audio Disabled Warning Banner (When camera works but mic permission denied) */}
        {cameraStatus.isAudioEnabled === false && (
          <div className="flex items-center justify-between px-3 py-1.5 rounded-lg chip-warning border text-xs font-medium animate-in fade-in">
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
          <span className="text-sm tabular-nums text-text-main truncate">
            {recentSavedSegment}
          </span>
        </div>
      )}

      {/* BOTTOM CONTROLS DECK */}
      <div className="relative z-10 flex flex-col items-center pb-6 pt-2 bg-transparent">
        
        {/* Zoom: slider while recording, discrete pills while idle */}
        {isRecording ? (
          <RecordingZoomSlider
            minZoom={cameraStatus.zoom?.minZoom || 1}
            maxZoom={cameraStatus.zoom?.maxZoom || 5}
            ratio={zoomLevel}
            onChange={handleSliderZoom}
          />
        ) : (
        <div className="flex items-center gap-1.5 p-1 rounded-full bg-black/65 border border-white/20 backdrop-blur-md mb-6 shadow-[0_2px_12px_rgba(0,0,0,0.5)]">
          {lensOptions.map((lvl) => {
            const isSelected = Math.abs(zoomLevel - lvl) < 0.3;
            return (
              <button
                key={lvl}
                onClick={() => {
                  triggerHaptic('lens_switch');
                  handleSetZoom(lvl);
                }}
                className={`min-w-[44px] min-h-[34px] py-1 px-3 rounded-full text-sm tabular-nums transition-all active:scale-[0.98] ${
                  isSelected 
                    ? 'bg-primary text-white font-semibold shadow-hero-glow drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)]' 
                    : 'text-white/80 hover:text-white drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)]'
                }`}
              >
                {lvl}x
              </button>
            );
          })}
        </div>
        )}

        {/* Big Shutter / Record Button with Pause Control */}
        <div className="flex items-center justify-center gap-6">
          {isRecording && (
            <button
              onClick={handlePauseToggle}
              aria-label={isPaused ? 'Resume recording' : 'Pause recording'}
              className={`w-14 h-14 rounded-full flex items-center justify-center backdrop-blur-xl border transition-all active:scale-95 shadow-lg ${
                isPaused
                  ? 'bg-white/20 hover:bg-white/30 border-2 border-white text-white shadow-[0_0_18px_rgba(255,255,255,0.4)]'
                  : 'bg-black/60 hover:bg-black/80 border border-white/30 text-white shadow-[0_2px_12px_rgba(0,0,0,0.5)]'
              }`}
            >
              {isPaused ? (
                <Play size={20} className="fill-white text-white translate-x-0.5 drop-shadow-[0_1px_3px_rgba(0,0,0,0.5)]" />
              ) : (
                <div className="flex items-center justify-center gap-1.5 pointer-events-none">
                  <span className="w-1.5 h-5 rounded-full bg-white shadow-sm" />
                  <span className="w-1.5 h-5 rounded-full bg-white shadow-sm" />
                </div>
              )}
            </button>
          )}

          {isRecording ? (
            <button
              onClick={handleStopRecording}
              aria-label="Stop Recording"
              className="relative w-20 h-20 rounded-full flex items-center justify-center border-4 border-white/90 bg-black/30 backdrop-blur-md transition-all active:scale-95 hover:border-white shadow-xl"
            >
              <span className="w-8 h-8 rounded-md bg-danger shadow-[0_0_12px_rgba(239,68,68,0.7)] hover:bg-rose-500 transition-colors" />
            </button>
          ) : (
            <button
              onClick={handleStartRecording}
              aria-label="Start Recording"
              className="relative w-20 h-20 rounded-full flex items-center justify-center border-4 border-white/80 bg-white/10 backdrop-blur transition-all active:scale-95 hover:border-white shadow-xl"
            >
              <span className="w-14 h-14 rounded-full bg-danger transition-all hover:bg-rose-500 shadow-md" />
            </button>
          )}

          {isRecording && <div className="w-14 h-14 pointer-events-none" aria-hidden="true" />}
        </div>
      </div>

    </div>
  );
};
