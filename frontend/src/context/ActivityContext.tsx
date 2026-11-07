import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { currentSession } from '../auth/supabase';
import { WORKER_BASE } from '../accounts/accountsService';
import { apiUrl, getAuthHeaders, Bridge } from '../bridge';

export interface CloudJobItem {
  id: string;
  youtube_url?: string;
  status: string; // 'queued' | 'claimed' | 'processing' | 'completed' | 'done' | 'failed'
  stage?: string;
  last_error?: string;
  source_title?: string;
  created_at?: string;
}

export interface QueueBacklogStats {
  queued_count: number;
  daily_quota_used: number;
  daily_quota_max: number;
  quota_date?: string;
}

export interface ActivityNotification {
  id: string;
  text: string;
  type: 'info' | 'success' | 'warning' | 'error';
  timestamp: number;
  read?: boolean;
}

export interface DirectUploadState {
  fileName: string;
  fileSize: number;
  phase: 'idle' | 'checking' | 'uploading' | 'submitting' | 'done' | 'error';
  progressPct: number;
  error: string | null;
  startedAt: number;
}

interface ActivityContextValue {
  isNotificationCenterOpen: boolean;
  openNotificationCenter: () => void;
  closeNotificationCenter: () => void;
  toggleNotificationCenter: () => void;

  // Active items
  cloudJobs: CloudJobItem[];
  queueStats: QueueBacklogStats | null;
  notifications: ActivityNotification[];

  // Direct Upload State & Methods
  activeDirectUpload: DirectUploadState | null;
  startDirectUpload: (file: File) => Promise<boolean>;
  cancelDirectUpload: () => void;
  clearDirectUpload: () => void;

  // Counts & hasActiveActivity
  activeJobsCount: number;
  hasActiveActivity: boolean;

  // Mutations
  addNotification: (text: string, type?: 'info' | 'success' | 'warning' | 'error') => void;
  dismissNotification: (id: string) => void;
  clearAllNotifications: () => void;

  // Refresh trigger
  refreshActivity: () => Promise<void>;
  isRefreshing: boolean;
  lastRefreshedAt: number | null;
}

const ActivityContext = createContext<ActivityContextValue | null>(null);

function idempotencyToken(): string {
  const c = typeof crypto !== 'undefined' ? crypto : null;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

export const ActivityProvider: React.FC<{
  children: React.ReactNode;
  pcHost?: string;
}> = ({ children, pcHost = '' }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [cloudJobs, setCloudJobs] = useState<CloudJobItem[]>([]);
  const [queueStats, setQueueStats] = useState<QueueBacklogStats | null>(null);
  const [activeDirectUpload, setActiveDirectUpload] = useState<DirectUploadState | null>(null);
  const activeXhrRef = useRef<XMLHttpRequest | null>(null);

  const [notifications, setNotifications] = useState<ActivityNotification[]>(() => {
    try {
      const saved = localStorage.getItem('dispatch_activity_notifications');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed)) return parsed;
      }
    } catch {
      // ignore
    }
    return [];
  });
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<number | null>(null);

  // Sync notifications to localStorage
  useEffect(() => {
    try {
      localStorage.setItem('dispatch_activity_notifications', JSON.stringify(notifications.slice(0, 50)));
    } catch {
      // ignore
    }
  }, [notifications]);

  const addNotification = useCallback((text: string, type: 'info' | 'success' | 'warning' | 'error' = 'success') => {
    const item: ActivityNotification = {
      id: `notif_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
      text,
      type,
      timestamp: Date.now(),
      read: false,
    };
    setNotifications((prev) => [item, ...prev]);
  }, []);

  const dismissNotification = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }, []);

  const clearAllNotifications = useCallback(() => {
    setNotifications([]);
  }, []);

  const clearDirectUpload = useCallback(() => {
    setActiveDirectUpload(null);
  }, []);

  const cancelDirectUpload = useCallback(() => {
    if (activeXhrRef.current) {
      activeXhrRef.current.abort();
      activeXhrRef.current = null;
    }
    setActiveDirectUpload(null);
    addNotification('Direct upload was cancelled.', 'info');
  }, [addNotification]);

  const startDirectUpload = useCallback(async (file: File): Promise<boolean> => {
    const isMp4 = file.type === 'video/mp4' || /\.mp4$/i.test(file.name);
    if (!isMp4) {
      addNotification('Upload an MP4 video — other formats are not supported.', 'error');
      return false;
    }
    if (file.size <= 0) {
      addNotification('That file is empty.', 'error');
      return false;
    }

    const state: DirectUploadState = {
      fileName: file.name,
      fileSize: file.size,
      phase: 'checking',
      progressPct: 0,
      error: null,
      startedAt: Date.now(),
    };
    setActiveDirectUpload(state);

    const objectUrl = URL.createObjectURL(file);
    let capturedThumbnail: string | null = null;
    try {
      const durationSec = await new Promise<number>((resolve, reject) => {
        const probe = document.createElement('video');
        probe.preload = 'metadata';
        probe.muted = true;
        probe.playsInline = true;
        probe.onloadedmetadata = () => {
          probe.currentTime = Math.min(1.0, Math.max(0.1, (probe.duration || 1) / 4));
        };
        probe.onseeked = () => {
          try {
            const canvas = document.createElement('canvas');
            canvas.width = Math.min(probe.videoWidth || 360, 640);
            canvas.height = Math.round(canvas.width * ((probe.videoHeight || 640) / (probe.videoWidth || 360)));
            const ctx = canvas.getContext('2d');
            if (ctx && canvas.width > 0 && canvas.height > 0) {
              ctx.drawImage(probe, 0, 0, canvas.width, canvas.height);
              capturedThumbnail = canvas.toDataURL('image/jpeg', 0.85);
            }
          } catch {}
          resolve(probe.duration || 0);
        };
        probe.onerror = () => reject(new Error('Could not read this video.'));
        probe.src = objectUrl;
      });
      if (!Number.isFinite(durationSec) || durationSec <= 0) throw new Error('Could not read the video duration.');
      if (durationSec > 180.5) throw new Error('Videos can be at most 3 minutes long.');
    } catch (err: any) {
      URL.revokeObjectURL(objectUrl);
      const msg = err instanceof Error ? err.message : 'Could not read this video.';
      setActiveDirectUpload({ ...state, phase: 'error', error: msg });
      addNotification(msg, 'error');
      return false;
    }
    URL.revokeObjectURL(objectUrl);

    const session = await currentSession().catch(() => null);
    const token = session?.access_token;
    if (!token) {
      const msg = 'Sign in to upload a video.';
      setActiveDirectUpload({ ...state, phase: 'error', error: msg });
      addNotification(msg, 'error');
      return false;
    }

    try {
      setActiveDirectUpload((prev) => prev ? { ...prev, phase: 'uploading', progressPct: 0 } : null);
      const reserveRes = await fetch(`${WORKER_BASE}/reserve`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: 'clip_video',
          mime_type: 'video/mp4',
          expected_bytes: file.size,
          client_token: idempotencyToken(),
        }),
      });
      const reserve = await reserveRes.json().catch(() => null);
      if (!reserveRes.ok || !reserve?.upload_url || !reserve?.media_object_id) {
        throw new Error(reserve?.error || `Upload could not start (HTTP ${reserveRes.status}).`);
      }

      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        activeXhrRef.current = xhr;
        xhr.open('PUT', reserve.upload_url);
        xhr.setRequestHeader('Content-Type', 'video/mp4');
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            const pct = Math.round((event.loaded / event.total) * 100);
            setActiveDirectUpload((prev) => prev ? { ...prev, progressPct: pct } : null);
          }
        };
        xhr.onload = () => {
          activeXhrRef.current = null;
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve();
          } else {
            reject(new Error(`Upload failed (HTTP ${xhr.status}).`));
          }
        };
        xhr.onerror = () => {
          activeXhrRef.current = null;
          reject(new Error('Upload failed — check your connection.'));
        };
        xhr.onabort = () => {
          activeXhrRef.current = null;
          reject(new Error('Upload cancelled.'));
        };
        xhr.send(file);
      });

      setActiveDirectUpload((prev) => prev ? { ...prev, phase: 'submitting', progressPct: 100 } : null);
      const completeRes = await fetch(`${WORKER_BASE}/complete`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ reservation_id: reserve.reservation_id }),
      });
      const complete = await completeRes.json().catch(() => null);
      if (!completeRes.ok || !complete?.object_id) {
        throw new Error(complete?.error || `Upload could not be finalized (HTTP ${completeRes.status}).`);
      }

      if (capturedThumbnail) {
        const normObjId = complete.object_id.replace(/-/g, '');
        try {
          localStorage.setItem(`thumb_dir_${normObjId}`, capturedThumbnail);
          localStorage.setItem(`thumb_dir_${complete.object_id}`, capturedThumbnail);
          localStorage.setItem(`thumb_${complete.object_id}`, capturedThumbnail);
        } catch {}
      }

      const rawTitle = file.name.replace(/\.mp4$/i, '').trim();
      const jobRes = await fetch(`${WORKER_BASE}/jobs/direct`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ media_object_id: complete.object_id, title: rawTitle || 'Direct video upload' }),
      });
      const job = await jobRes.json().catch(() => null);
      if (!jobRes.ok || !job?.job_id) {
        throw new Error(job?.error || `Processing could not be queued (HTTP ${jobRes.status}).`);
      }

      setActiveDirectUpload((prev) => prev ? { ...prev, phase: 'done', progressPct: 100 } : null);
      addNotification(`Video uploaded: ${rawTitle || 'Direct upload'}. Ready for processing!`, 'success');
      Bridge.showSystemNotification('Dispatch Upload Complete', `${rawTitle || 'Video'} uploaded successfully! Processing on PC.`);
      
      // Auto-clear direct upload after 4 seconds
      setTimeout(() => {
        setActiveDirectUpload((prev) => (prev?.phase === 'done' ? null : prev));
      }, 4000);

      return true;
    } catch (err: any) {
      activeXhrRef.current = null;
      if (err.message === 'Upload cancelled.') {
        return false;
      }
      const msg = err instanceof Error ? err.message : 'Upload failed.';
      setActiveDirectUpload((prev) => prev ? { ...prev, phase: 'error', error: msg } : null);
      addNotification(msg, 'error');
      Bridge.showSystemNotification('Dispatch Upload Failed', msg);
      return false;
    }
  }, [addNotification]);

  const refreshActivity = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const sess = await currentSession().catch(() => null);
      const token =
        sess?.access_token ||
        (typeof window !== 'undefined'
          ? (window as any).DispatchBridge?.getAuthToken?.()
          : null);

      // 1. Fetch Cloud Jobs & Backlog from Worker if authenticated
      if (token) {
        const [jobsRes, clipsRes] = await Promise.allSettled([
          fetch(`${WORKER_BASE}/jobs/status`, {
            headers: { Authorization: `Bearer ${token}` },
            signal: AbortSignal.timeout(4000),
          }),
          fetch(`${WORKER_BASE}/clips?limit=1`, {
            headers: { Authorization: `Bearer ${token}` },
            signal: AbortSignal.timeout(4000),
          }),
        ]);

        if (jobsRes.status === 'fulfilled' && jobsRes.value.ok) {
          const jData = await jobsRes.value.json().catch(() => ({}));
          if (Array.isArray(jData.jobs)) {
            setCloudJobs(jData.jobs);
          }
        }

        if (clipsRes.status === 'fulfilled' && clipsRes.value.ok) {
          const cData = await clipsRes.value.json().catch(() => ({}));
          if (cData.queue_stats) {
            setQueueStats(cData.queue_stats);
          }
        }
      } else if (pcHost) {
        // PC inbox fallback
        const res = await fetch(apiUrl('/api/youtube/inbox', pcHost), {
          headers: getAuthHeaders(),
          signal: AbortSignal.timeout(3000),
        }).catch(() => null);
        if (res && res.ok) {
          const data = await res.json().catch(() => ({}));
          if (Array.isArray(data.items)) {
            setCloudJobs(
              data.items.map((it: any) => ({
                id: it.dispatch_id || it.video_id,
                youtube_url: it.title,
                status: it.status?.toLowerCase() || 'queued',
                stage: it.stage,
              }))
            );
          }
        }
      }
      setLastRefreshedAt(Date.now());
    } catch {
      // Network hiccup fail-soft
    } finally {
      setIsRefreshing(false);
    }
  }, [pcHost]);

  const activeJobsCount =
    cloudJobs.filter((j) => j.status === 'queued' || j.status === 'claimed' || j.status === 'processing').length +
    (activeDirectUpload && (activeDirectUpload.phase === 'uploading' || activeDirectUpload.phase === 'checking' || activeDirectUpload.phase === 'submitting') ? 1 : 0);

  const hasUnread = notifications.some((n) => !n.read);
  const hasBacklog = (queueStats?.queued_count ?? 0) > 0;
  const hasActiveActivity = activeJobsCount > 0 || hasBacklog || hasUnread;

  const hasActiveWorkRef = useRef(false);
  hasActiveWorkRef.current = activeJobsCount > 0 || hasBacklog;

  // Adaptive periodic polling for cloud jobs & backlog:
  // - Pauses when app is hidden / minimized (0 requests/sec, saves mobile battery)
  // - Polls every 8s when jobs or uploads are actively processing
  // - Backs off to 35s when completely idle
  useEffect(() => {
    let timerId: any = null;
    let isCancelled = false;

    const tick = async () => {
      if (document.hidden) {
        schedule(30000);
        return;
      }
      await refreshActivity();
      if (!isCancelled) {
        schedule(hasActiveWorkRef.current ? 8000 : 35000);
      }
    };

    const schedule = (ms: number) => {
      clearTimeout(timerId);
      timerId = setTimeout(tick, ms);
    };

    const handleVisibility = () => {
      if (!document.hidden) {
        tick();
      }
    };

    tick();
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      isCancelled = true;
      clearTimeout(timerId);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [refreshActivity]);

  const openNotificationCenter = useCallback(() => {
    setIsOpen(true);
    // Mark notifications as read
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }, []);

  const closeNotificationCenter = useCallback(() => {
    setIsOpen(false);
  }, []);

  const toggleNotificationCenter = useCallback(() => {
    if (isOpen) {
      closeNotificationCenter();
    } else {
      openNotificationCenter();
    }
  }, [isOpen, openNotificationCenter, closeNotificationCenter]);

  return (
    <ActivityContext.Provider
      value={{
        isNotificationCenterOpen: isOpen,
        openNotificationCenter,
        closeNotificationCenter,
        toggleNotificationCenter,
        cloudJobs,
        queueStats,
        notifications,
        activeDirectUpload,
        startDirectUpload,
        cancelDirectUpload,
        clearDirectUpload,
        activeJobsCount,
        hasActiveActivity,
        addNotification,
        dismissNotification,
        clearAllNotifications,
        refreshActivity,
        isRefreshing,
        lastRefreshedAt,
      }}
    >
      {children}
    </ActivityContext.Provider>
  );
};

export function useActivity(): ActivityContextValue {
  const ctx = useContext(ActivityContext);
  if (!ctx) {
    throw new Error('useActivity must be used within an ActivityProvider');
  }
  return ctx;
}
