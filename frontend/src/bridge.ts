export interface RecordingItem {
  segmentId: string;
  sessionId: string;
  sequenceNumber: number;
  filename: string;
  filepath: string;
  fileSizeBytes: number;
  sha256Hash: string;
  status: string; // 'RECORDED', 'QUEUED_FOR_UPLOAD', 'UPLOADING', 'UPLOADED_TO_YOUTUBE', 'FAILED_RECORDING'
  youtubeVideoId?: string | null;
  errorMessage?: string | null;
  createdAt: number;
  finalizedAt?: number | null;
  durationSeconds?: number;
  videoUrl?: string;
  clipCount?: number;
  approvedClipCount?: number;
  chunkStatus?: string;
  sessionStatus?: string;
}

export interface YouTubeStatus {
  connected: boolean;
  channelTitle?: string;
  account?: string;
}

export interface StorageInfo {
  available: boolean;
  freeGb?: number;
  totalGb?: number;
  freeBytes?: number;
  totalBytes?: number;
  error?: string;
}

export interface ExposureInfo {
  minIndex: number;
  maxIndex: number;
  currentIndex: number;
  stepEv: number;
  isSupported: boolean;
}

export interface ZoomInfo {
  minZoom: number;
  maxZoom: number;
  currentZoom: number;
}

export interface CameraStateEvent {
  status: 'initializing' | 'ready' | 'permission_required' | 'error';
  error?: string | null;
  lensFacing: 'back' | 'front';
  isRecording: boolean;
  isRecordingPaused?: boolean;
  isTorchEnabled: boolean;
  isAeAfLocked: boolean;
  isAeLocked?: boolean;
  isAfLocked?: boolean;
  isAeAfLockUncertain?: boolean;
  isAudioEnabled: boolean;
  exposure: ExposureInfo;
  zoom: ZoomInfo;
}

export interface FocusResultEvent {
  triggerId: number;
  success: boolean;
  isLocked: boolean;
  hasAf?: boolean;
  hasAe?: boolean;
  lockStateUncertain?: boolean;
  normX?: number;
  normY?: number;
  error?: string;
}

export interface ExposureResultEvent {
  success: boolean;
  index?: number;
  ev?: number;
  error?: string;
}

export type CameraEventListener = (event: { type: string; data: any }) => void;

declare global {
  interface Window {
    onDispatchCameraEvent?: (type: string, data: any) => void;
    DispatchBridge?: {
      startRecording: () => void;
      stopRecording: () => void;
      isRecording: () => boolean;
      isRecordingPaused?: () => boolean;
      pauseRecording?: () => void;
      resumeRecording?: () => void;
      switchCamera: () => void;
      toggleTorch: () => boolean;
      toggleMic: () => boolean;
      setZoom: (ratio: number) => number;
      getZoomInfo?: () => string;
      focusOnPoint?: (normX: number, normY: number, triggerId?: number) => void;
      lockAeAfAtPoint?: (normX: number, normY: number, triggerId?: number) => boolean;
      unlockAeAf?: () => void;
      unlockAndFocus?: (normX: number, normY: number, triggerId?: number) => void;
      setExposureIndex?: (index: number) => number;
      getExposureInfo?: () => string;
      getCameraStatus?: () => string;
      retryCameraInit?: () => void;
      getRecordings: () => string;
      getYouTubeStatus: () => string;
      connectYouTube: (pcHost: string, pairingCode: string) => string;
      disconnectYouTube: () => void;
      retryUpload: (segmentId: string) => void;
      getStorageInfo: () => string;
      pingPc: (pcHost: string) => string;
      getWifiOnly: () => boolean;
      setWifiOnly: (enabled: boolean) => void;
      getPcHost?: () => string;
      setPcHost?: (pcHost: string) => boolean;
      getAuthToken?: () => string;
      openCustomTab?: (url: string) => boolean;
      deleteSession?: (sessionId: string) => boolean;
      performHaptic?: (type: string) => boolean;
      setCurrentUserId?: (userId: string | null) => boolean;
      getCurrentUserId?: () => string | null;
      clearLegacyRecordings?: () => boolean;
    };
  }
}

const cameraListeners: Set<CameraEventListener> = new Set();

if (typeof window !== 'undefined') {
  window.onDispatchCameraEvent = (type: string, data: any) => {
    cameraListeners.forEach(listener => {
      try {
        listener({ type, data });
      } catch (e) {
        console.error('Camera listener error:', e);
      }
    });
  };

  window.addEventListener('dispatch-camera-event', ((e: CustomEvent) => {
    if (e.detail) {
      cameraListeners.forEach(listener => {
        try {
          listener(e.detail);
        } catch (err) {
          console.error('Camera listener error:', err);
        }
      });
    }
  }) as EventListener);
}

export type HapticFeedbackType =
  | 'recording_start'
  | 'recording_stop'
  | 'zoom_tick'
  | 'lens_switch'
  | 'toggle'
  | 'lock'
  | 'tick';

export function triggerHaptic(type: HapticFeedbackType = 'tick') {
  try {
    if (typeof window !== 'undefined' && window.DispatchBridge?.performHaptic) {
      window.DispatchBridge.performHaptic(type);
    }
  } catch {}

  try {
    if (typeof navigator !== 'undefined' && navigator.vibrate) {
      switch (type) {
        case 'recording_start':
          navigator.vibrate(25);
          break;
        case 'recording_stop':
          navigator.vibrate([15, 40, 20]);
          break;
        case 'zoom_tick':
          navigator.vibrate(5);
          break;
        case 'lens_switch':
          navigator.vibrate(10);
          break;
        case 'toggle':
          navigator.vibrate(12);
          break;
        case 'lock':
          navigator.vibrate([20, 40, 15]);
          break;
        default:
          navigator.vibrate(8);
      }
    }
  } catch {}
}

export const Bridge = {
  performHaptic(type: HapticFeedbackType = 'tick'): boolean {
    triggerHaptic(type);
    return true;
  },
  isAvailable(): boolean {
    return typeof window !== 'undefined' && (
      !!(window as any).DispatchBridge ||
      window.location.hostname === 'appassets.androidplatform.net'
    );
  },

  isRecording(): boolean {
    if (window.DispatchBridge?.isRecording) {
      return window.DispatchBridge.isRecording();
    }
    return false;
  },

  isRecordingPaused(): boolean {
    if (window.DispatchBridge?.isRecordingPaused) {
      return window.DispatchBridge.isRecordingPaused();
    }
    return false;
  },

  pauseRecording(): void {
    if (window.DispatchBridge?.pauseRecording) {
      window.DispatchBridge.pauseRecording();
    } else {
      console.log('[Mock Bridge] pauseRecording');
    }
  },

  resumeRecording(): void {
    if (window.DispatchBridge?.resumeRecording) {
      window.DispatchBridge.resumeRecording();
    } else {
      console.log('[Mock Bridge] resumeRecording');
    }
  },

  startRecording(): void {
    if (window.DispatchBridge?.startRecording) {
      window.DispatchBridge.startRecording();
    } else {
      console.log('[Mock Bridge] startRecording');
    }
  },

  stopRecording(): void {
    if (window.DispatchBridge?.stopRecording) {
      window.DispatchBridge.stopRecording();
    } else {
      console.log('[Mock Bridge] stopRecording');
    }
  },

  switchCamera(): void {
    if (window.DispatchBridge?.switchCamera) {
      window.DispatchBridge.switchCamera();
    } else {
      console.log('[Mock Bridge] switchCamera');
    }
  },

  toggleTorch(): boolean {
    if (window.DispatchBridge?.toggleTorch) {
      return window.DispatchBridge.toggleTorch();
    }
    console.log('[Mock Bridge] toggleTorch');
    return false;
  },

  toggleMic(): boolean {
    if (window.DispatchBridge?.toggleMic) {
      return window.DispatchBridge.toggleMic();
    }
    console.log('[Mock Bridge] toggleMic');
    return true;
  },

  setZoom(ratio: number): number {
    if (window.DispatchBridge?.setZoom) {
      return window.DispatchBridge.setZoom(ratio);
    }
    console.log('[Mock Bridge] setZoom', ratio);
    return ratio;
  },

  getZoomInfo(): ZoomInfo {
    if (window.DispatchBridge?.getZoomInfo) {
      try {
        return JSON.parse(window.DispatchBridge.getZoomInfo());
      } catch {}
    }
    return { minZoom: 1.0, maxZoom: 5.0, currentZoom: 1.0 };
  },

  onCameraEvent(listener: CameraEventListener): () => void {
    cameraListeners.add(listener);
    return () => {
      cameraListeners.delete(listener);
    };
  },

  focusOnPoint(normX: number, normY: number, triggerId = Date.now()): void {
    if (window.DispatchBridge?.focusOnPoint) {
      window.DispatchBridge.focusOnPoint(normX, normY, triggerId);
    } else {
      console.log('[Mock Bridge] focusOnPoint', normX, normY);
      setTimeout(() => {
        cameraListeners.forEach(l => l({
          type: 'focus_result',
          data: { triggerId, success: true, isLocked: false, hasAf: true, hasAe: true, normX, normY }
        }));
      }, 300);
    }
  },

  lockAeAfAtPoint(normX: number, normY: number, triggerId = Date.now()): boolean {
    if (window.DispatchBridge?.lockAeAfAtPoint) {
      return window.DispatchBridge.lockAeAfAtPoint(normX, normY, triggerId) ?? true;
    }
    console.log('[Mock Bridge] lockAeAfAtPoint', normX, normY);
    setTimeout(() => {
      cameraListeners.forEach(l => l({
        type: 'focus_result',
        data: { triggerId, success: true, isLocked: true, hasAf: true, hasAe: true, normX, normY }
      }));
    }, 350);
    return true;
  },

  unlockAeAf(): void {
    if (window.DispatchBridge?.unlockAeAf) {
      window.DispatchBridge.unlockAeAf();
    } else {
      console.log('[Mock Bridge] unlockAeAf');
      setTimeout(() => {
        cameraListeners.forEach(l => l({
          type: 'unlock_result',
          data: { success: true }
        }));
      }, 100);
    }
  },

  unlockAndFocus(normX: number, normY: number, triggerId = Date.now()): void {
    if (window.DispatchBridge?.unlockAndFocus) {
      window.DispatchBridge.unlockAndFocus(normX, normY, triggerId);
      return;
    }
    console.log('[Mock Bridge] unlockAndFocus', normX, normY);
    setTimeout(() => {
      cameraListeners.forEach(l => l({
        type: 'focus_result',
        data: { triggerId, success: true, isLocked: false, hasAf: true, hasAe: true, normX, normY }
      }));
    }, 320);
  },

  retryCameraInit(): void {
    if (window.DispatchBridge?.retryCameraInit) {
      window.DispatchBridge.retryCameraInit();
    } else {
      console.log('[Mock Bridge] retryCameraInit');
    }
  },

  getCameraStatus(): CameraStateEvent | null {
    if (window.DispatchBridge?.getCameraStatus) {
      try {
        return JSON.parse(window.DispatchBridge.getCameraStatus());
      } catch {}
    }
    return null;
  },

  setExposureIndex(index: number): number {
    if (window.DispatchBridge?.setExposureIndex) {
      return window.DispatchBridge.setExposureIndex(index);
    }
    return index;
  },

  getExposureInfo(): ExposureInfo {
    if (window.DispatchBridge?.getExposureInfo) {
      try {
        return JSON.parse(window.DispatchBridge.getExposureInfo());
      } catch {
        // fallback
      }
    }
    return { minIndex: -4, maxIndex: 4, currentIndex: 0, stepEv: 0.5, isSupported: true };
  },

  getRecordings(): RecordingItem[] {
    if (window.DispatchBridge?.getRecordings) {
      try {
        const raw = window.DispatchBridge.getRecordings();
        return JSON.parse(raw);
      } catch (e) {
        console.error('Failed to parse recordings from bridge:', e);
        return [];
      }
    }
    return [];
  },

  getYouTubeStatus(): YouTubeStatus {
    if (window.DispatchBridge?.getYouTubeStatus) {
      try {
        const raw = window.DispatchBridge.getYouTubeStatus();
        return JSON.parse(raw);
      } catch (e) {
        console.error('Failed to parse YouTube status from bridge:', e);
        return { connected: false };
      }
    }
    return { connected: false };
  },

  connectYouTube(pcHost: string, pairingCode: string): { success: boolean; message: string } {
    if (window.DispatchBridge?.connectYouTube) {
      try {
        const res = window.DispatchBridge.connectYouTube(pcHost, pairingCode);
        return JSON.parse(res);
      } catch (e: any) {
        return { success: false, message: e.message || 'Connection error' };
      }
    }
    return { success: false, message: 'Native bridge unavailable' };
  },

  disconnectYouTube(): void {
    if (window.DispatchBridge?.disconnectYouTube) {
      window.DispatchBridge.disconnectYouTube();
    }
  },

  retryUpload(segmentId: string): void {
    if (window.DispatchBridge?.retryUpload) {
      window.DispatchBridge.retryUpload(segmentId);
    }
  },

  deleteSession(sessionId: string): boolean {
    if (window.DispatchBridge?.deleteSession) {
      try {
        return window.DispatchBridge.deleteSession(sessionId);
      } catch (e) {
        console.error('Failed to delete session via bridge:', e);
        return false;
      }
    }
    return false;
  },

  getStorageInfo(): StorageInfo {
    if (window.DispatchBridge?.getStorageInfo) {
      try {
        const raw = window.DispatchBridge.getStorageInfo();
        const info = JSON.parse(raw) as StorageInfo;
        if (info.available && Number.isFinite(info.freeGb) && Number.isFinite(info.totalGb)) return info;
        return { available: false, error: info.error || 'Storage information is unavailable' };
      } catch {
        return { available: false, error: 'Storage information could not be read' };
      }
    }
    return { available: false, error: 'Storage information is available only in the Android app' };
  },

  async pingPc(pcHost: string): Promise<{ success: boolean; message: string }> {
    const cleanHost = pcHost.trim().replace(/^https?:\/\//, '').replace(/\/$/, '');
    if (window.DispatchBridge?.pingPc) {
      try {
        const res = window.DispatchBridge.pingPc(cleanHost);
        return JSON.parse(res);
      } catch (e: any) {
        return { success: false, message: e.message || 'Error pinging PC' };
      }
    }
    try {
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), 3000);
      const res = await fetch(`http://${cleanHost}/api/status`, { mode: 'cors', signal: controller.signal });
      clearTimeout(t);
      if (res.ok) {
        return { success: true, message: `PC reachable (HTTP ${res.status})` };
      }
      return { success: false, message: `PC returned HTTP ${res.status}` };
    } catch (e: any) {
      return { success: false, message: e.message || 'Host unreachable' };
    }
  },

  setPcHost(pcHost: string): void {
    const clean = pcHost.trim().replace(/^https?:\/\//, '').replace(/\/$/, '');
    if (!clean) return;
    if (window.DispatchBridge?.setPcHost) {
      try {
        window.DispatchBridge.setPcHost(clean);
      } catch (e) {
        console.warn('Failed to call native setPcHost', e);
      }
    }
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('dispatch_pc_host', clean);
    }
  },

  getWifiOnly(): boolean {
    if (window.DispatchBridge?.getWifiOnly) {
      return window.DispatchBridge.getWifiOnly();
    }
    const saved = typeof localStorage !== 'undefined' ? localStorage.getItem('dispatch_wifi_only') : null;
    return saved !== null ? saved === 'true' : true;
  },

  setWifiOnly(enabled: boolean): void {
    if (window.DispatchBridge?.setWifiOnly) {
      window.DispatchBridge.setWifiOnly(enabled);
    }
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('dispatch_wifi_only', String(enabled));
    }
  },

  openCustomTab(url: string): boolean {
    if (window.DispatchBridge?.openCustomTab) {
      try {
        return window.DispatchBridge.openCustomTab(url);
      } catch (e) {
        console.warn('Failed to open custom tab', e);
      }
    }
    if (typeof window !== 'undefined') {
      window.open(url, '_blank');
      return true;
    }
    return false;
  },

  setCurrentUserId(userId: string | null): boolean {
    if (window.DispatchBridge?.setCurrentUserId) {
      try {
        return window.DispatchBridge.setCurrentUserId(userId);
      } catch (e) {
        console.warn('Failed to call setCurrentUserId', e);
      }
    }
    return false;
  },

  getCurrentUserId(): string | null {
    if (window.DispatchBridge?.getCurrentUserId) {
      try {
        return window.DispatchBridge.getCurrentUserId();
      } catch (e) {
        console.warn('Failed to call getCurrentUserId', e);
      }
    }
    return null;
  },

  clearLegacyRecordings(): boolean {
    if (window.DispatchBridge?.clearLegacyRecordings) {
      try {
        return window.DispatchBridge.clearLegacyRecordings();
      } catch (e) {
        console.warn('Failed to call clearLegacyRecordings', e);
      }
    }
    return false;
  }
};

export function getApiBaseUrl(pcHost?: string): string {
  if (typeof window !== 'undefined' && (
    window.location.protocol === 'file:' ||
    window.location.hostname === 'appassets.androidplatform.net' ||
    !!(window as any).DispatchBridge
  )) {
    const nativeHost = window.DispatchBridge?.getPcHost?.()?.trim() || null;
    const host = pcHost || nativeHost || (typeof localStorage !== 'undefined' ? localStorage.getItem('dispatch_pc_host') : null) || '';
    if (!host) return '';
    const cleanHost = host.replace(/^https?:\/\//, '').replace(/\/$/, '');
    return `http://${cleanHost}`;
  }
  return '';
}

export function apiUrl(path: string, pcHost?: string): string {
  const base = getApiBaseUrl(pcHost);
  if (!base) return path;
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${cleanPath}`;
}

export function getAuthHeaders(custom?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(custom || {}),
  };
  const token = typeof window !== 'undefined' ? (window as any).DispatchBridge?.getAuthToken?.() : null;
  if (token && token.trim().length > 0) {
    headers['x-auth-token'] = token.trim();
  }
  if (typeof window !== 'undefined' && window.localStorage) {
    try {
      for (let i = 0; i < window.localStorage.length; i++) {
        const key = window.localStorage.key(i);
        if (key && ((key.startsWith('sb-') && key.endsWith('-auth-token')) || key === 'supabase.auth.token')) {
          const raw = window.localStorage.getItem(key);
          if (raw) {
            const parsed = JSON.parse(raw);
            const accessToken = parsed.access_token || parsed?.currentSession?.access_token;
            if (accessToken && typeof accessToken === 'string') {
              headers['Authorization'] = `Bearer ${accessToken.trim()}`;
              break;
            }
          }
        }
      }
    } catch {
      // LocalStorage access may be restricted in some sandboxes
    }
  }
  return headers;
}
