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
  freeGb: number;
  totalGb: number;
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

declare global {
  interface Window {
    DispatchBridge?: {
      startRecording: () => void;
      stopRecording: () => void;
      isRecording: () => boolean;
      switchCamera: () => void;
      toggleTorch: () => boolean;
      toggleMic: () => boolean;
      setZoom: (ratio: number) => number;
      getZoomInfo?: () => string;
      focusOnPoint?: (normX: number, normY: number) => void;
      lockAeAfAtPoint?: (normX: number, normY: number) => boolean;
      unlockAeAf?: () => void;
      setExposureIndex?: (index: number) => number;
      getExposureInfo?: () => string;
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
    };
  }
}

export const Bridge = {
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

  focusOnPoint(normX: number, normY: number): void {
    if (window.DispatchBridge?.focusOnPoint) {
      window.DispatchBridge.focusOnPoint(normX, normY);
    } else {
      console.log('[Mock Bridge] focusOnPoint', normX, normY);
    }
  },

  lockAeAfAtPoint(normX: number, normY: number): boolean {
    if (window.DispatchBridge?.lockAeAfAtPoint) {
      return window.DispatchBridge.lockAeAfAtPoint(normX, normY);
    }
    console.log('[Mock Bridge] lockAeAfAtPoint', normX, normY);
    return true;
  },

  unlockAeAf(): void {
    if (window.DispatchBridge?.unlockAeAf) {
      window.DispatchBridge.unlockAeAf();
    } else {
      console.log('[Mock Bridge] unlockAeAf');
    }
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

  getStorageInfo(): StorageInfo {
    if (window.DispatchBridge?.getStorageInfo) {
      try {
        const raw = window.DispatchBridge.getStorageInfo();
        return JSON.parse(raw);
      } catch {
        return { freeGb: 48, totalGb: 128 };
      }
    }
    return { freeGb: 48, totalGb: 128 };
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
  }
};

export function getApiBaseUrl(pcHost?: string): string {
  if (typeof window !== 'undefined' && (
    window.location.protocol === 'file:' ||
    window.location.hostname === 'appassets.androidplatform.net' ||
    !!(window as any).DispatchBridge
  )) {
    const nativeHost = window.DispatchBridge?.getPcHost?.()?.trim() || null;
    const host = pcHost || nativeHost || (typeof localStorage !== 'undefined' ? localStorage.getItem('dispatch_pc_host') : null) || '192.168.0.200:8000';
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
  return headers;
}
