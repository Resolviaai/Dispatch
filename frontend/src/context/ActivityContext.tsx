import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { currentSession } from '../auth/supabase';
import { WORKER_BASE } from '../accounts/accountsService';
import { apiUrl, getAuthHeaders } from '../bridge';

export interface CloudJobItem {
  id: string;
  youtube_url?: string;
  status: string; // 'queued' | 'claimed' | 'processing' | 'completed' | 'done' | 'failed'
  stage?: string;
  last_error?: string;
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

interface ActivityContextValue {
  isNotificationCenterOpen: boolean;
  openNotificationCenter: () => void;
  closeNotificationCenter: () => void;
  toggleNotificationCenter: () => void;

  // Active items
  cloudJobs: CloudJobItem[];
  queueStats: QueueBacklogStats | null;
  notifications: ActivityNotification[];

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

export const ActivityProvider: React.FC<{
  children: React.ReactNode;
  pcHost?: string;
}> = ({ children, pcHost = '' }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [cloudJobs, setCloudJobs] = useState<CloudJobItem[]>([]);
  const [queueStats, setQueueStats] = useState<QueueBacklogStats | null>(null);
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

  const refreshActivity = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const sess = await currentSession().catch(() => null);
      const token = sess?.access_token;

      // 1. Fetch Cloud Jobs & Backlog from Worker if authenticated
      if (token) {
        const [jobsRes, clipsRes] = await Promise.allSettled([
          fetch(`${WORKER_BASE}/jobs/status`, {
            headers: { Authorization: `Bearer ${token}` },
            signal: AbortSignal.timeout(4000),
          }),
          fetch(`${WORKER_BASE}/clips`, {
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

  // Periodic polling for cloud jobs & backlog
  useEffect(() => {
    refreshActivity();
    const interval = setInterval(refreshActivity, 10000);
    return () => clearInterval(interval);
  }, [refreshActivity]);

  const activeJobsCount = cloudJobs.filter(
    (j) => j.status === 'queued' || j.status === 'claimed' || j.status === 'processing'
  ).length;

  const hasUnread = notifications.some((n) => !n.read);
  const hasBacklog = (queueStats?.queued_count ?? 0) > 0;
  const hasActiveActivity = activeJobsCount > 0 || hasBacklog || hasUnread;

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
