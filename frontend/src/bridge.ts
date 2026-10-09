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

declare global {
  interface Window {
    DispatchBridge?: {
      startRecording: () => void;
      stopRecording: () => void;
      switchCamera: () => void;
      toggleTorch: () => boolean;
      toggleMic: () => boolean;
      setZoom: (ratio: number) => number;
      getRecordings: () => string;
      getYouTubeStatus: () => string;
      connectYouTube: (pcHost: string) => string;
      disconnectYouTube: () => void;
      retryUpload: (segmentId: string) => void;
      getStorageInfo: () => string;
      pingPc: (pcHost: string) => string;
    };
  }
}

export const Bridge = {
  isAvailable(): boolean {
    return typeof window !== 'undefined' && !!window.DispatchBridge;
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

  connectYouTube(pcHost: string): { success: boolean; message: string } {
    if (window.DispatchBridge?.connectYouTube) {
      try {
        const res = window.DispatchBridge.connectYouTube(pcHost);
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

  pingPc(pcHost: string): { success: boolean; message: string } {
    if (window.DispatchBridge?.pingPc) {
      try {
        const res = window.DispatchBridge.pingPc(pcHost);
        return JSON.parse(res);
      } catch (e: any) {
        return { success: false, message: e.message || 'Error pinging PC' };
      }
    }
    return { success: false, message: 'Native bridge unavailable' };
  }
};

export function getApiBaseUrl(pcHost?: string): string {
  if (typeof window !== 'undefined' && window.location.protocol === 'file:') {
    const host = pcHost || (typeof localStorage !== 'undefined' ? localStorage.getItem('dispatch_pc_host') : null) || '127.0.0.1:8000';
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
