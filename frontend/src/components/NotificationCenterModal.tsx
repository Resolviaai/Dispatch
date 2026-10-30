import React from 'react';
import {
  X,
  Bell,
  Cloud,
  Layers,
  RotateCw,
  CheckCircle2,
  AlertCircle,
  Clock,
  Trash2,
  ExternalLink,
} from 'lucide-react';
import { useActivity, CloudJobItem } from '../context/ActivityContext';

interface NotificationCenterModalProps {
  isOpen: boolean;
  onClose: () => void;
  onRefreshAll?: () => Promise<void> | void;
}

export const NotificationCenterModal: React.FC<NotificationCenterModalProps> = ({
  isOpen,
  onClose,
  onRefreshAll,
}) => {
  const {
    cloudJobs,
    queueStats,
    notifications,
    activeJobsCount,
    dismissNotification,
    clearAllNotifications,
    refreshActivity,
    isRefreshing,
  } = useActivity();

  if (!isOpen) return null;

  const handleManualRefresh = async () => {
    await Promise.allSettled([
      refreshActivity(),
      onRefreshAll ? Promise.resolve(onRefreshAll()) : Promise.resolve(),
    ]);
  };

  const activeJobs = cloudJobs.filter(
    (j) => j.status === 'queued' || j.status === 'claimed' || j.status === 'processing'
  );
  const completedJobs = cloudJobs.filter(
    (j) => j.status === 'completed' || j.status === 'done'
  );
  const failedJobs = cloudJobs.filter((j) => j.status === 'failed');

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-[120] flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/75 backdrop-blur-sm animate-in fade-in select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-t-3xl sm:rounded-2xl bg-canvas border border-border p-4 pb-8 sm:pb-4 space-y-4 shadow-2xl max-h-[85vh] flex flex-col overflow-hidden animate-in slide-in-from-bottom-4 duration-200"
      >
        {/* Mobile Sheet Drag Handle */}
        <div className="w-10 h-1 rounded-full bg-surface-300/60 mx-auto -mt-1 mb-1 sm:hidden shrink-0" />

        {/* Modal Header */}
        <div className="flex items-center justify-between pb-2 border-b border-border/70 shrink-0">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center text-primary">
              <Bell size={16} />
            </div>
            <div>
              <h3 className="text-sm font-semibold tracking-tight text-text-main flex items-center gap-2">
                <span>Activity &amp; Notifications</span>
                {activeJobsCount > 0 && (
                  <span className="w-2 h-2 rounded-full bg-primary animate-pulse" />
                )}
              </h3>
              <p className="text-[11px] text-text-muted">Background pipelines, queue &amp; confirmations</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={handleManualRefresh}
              disabled={isRefreshing}
              aria-label="Refresh Activity"
              className="w-9 h-9 rounded-full flex items-center justify-center text-text-muted hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
              title="Refresh status"
            >
              <RotateCw size={15} className={isRefreshing ? 'animate-spin text-primary' : ''} />
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="w-9 h-9 rounded-full flex items-center justify-center text-text-muted hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Scrollable Content Body */}
        <div className="flex-1 overflow-y-auto space-y-4 pr-1 -mr-1">

          {/* 1. ACTIVE CLOUD RENDERING / ANALYSIS JOBS */}
          <section className="space-y-2">
            <div className="flex items-center justify-between text-[11px] uppercase tracking-wider font-semibold text-text-secondary">
              <span className="flex items-center gap-1.5">
                <Cloud size={13} className="text-primary" />
                Cloud Processing Jobs
              </span>
              <span className="font-mono text-xs tabular-nums text-text-muted">
                {activeJobs.length} active
              </span>
            </div>

            {activeJobs.length > 0 ? (
              <div className="space-y-2">
                {activeJobs.map((job) => (
                  <div
                    key={job.id}
                    className="p-3 rounded-xl bg-primary-soft/40 border border-primary/25 space-y-1.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-text-main truncate max-w-[240px]">
                        {job.youtube_url || job.id}
                      </span>
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-primary/20 text-primary text-[10px] font-semibold uppercase tracking-wider shrink-0">
                        <RotateCw size={10} className="animate-spin" />
                        {job.status === 'claimed' ? 'Processing' : 'Queued'}
                      </span>
                    </div>
                    <p className="text-[11px] text-text-secondary flex items-center gap-1.5">
                      <Clock size={11} className="text-primary" />
                      <span>Analyzing speech &amp; AI highlights…</span>
                      {job.stage && <span className="font-mono text-[10px] opacity-75">({job.stage})</span>}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-3 rounded-xl bg-surface-2 border border-border text-xs text-text-muted flex items-center gap-2">
                <CheckCircle2 size={14} className="text-status-success shrink-0" />
                <span>No active cloud rendering jobs in progress.</span>
              </div>
            )}
          </section>

          {/* 2. DAILY CLOUD QUEUE BACKLOG STATUS */}
          {queueStats && (
            <section className="space-y-2">
              <div className="flex items-center justify-between text-[11px] uppercase tracking-wider font-semibold text-text-secondary">
                <span className="flex items-center gap-1.5">
                  <Layers size={13} className="text-status-warning" />
                  Cloud Queue Backlog
                </span>
                <span className="font-mono text-xs tabular-nums text-text-muted">
                  Quota {queueStats.daily_quota_used}/{queueStats.daily_quota_max}
                </span>
              </div>

              <div className="p-3 rounded-xl bg-surface-2 border border-border space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-text-secondary">Pending in Backlog:</span>
                  <span className="font-mono font-semibold tabular-nums text-text-main">
                    {queueStats.queued_count} item{queueStats.queued_count === 1 ? '' : 's'}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-text-secondary">Daily Allowance:</span>
                  <span className="font-mono tabular-nums text-text-muted">
                    {queueStats.daily_quota_used} used of {queueStats.daily_quota_max}
                  </span>
                </div>
                <div className="pt-1 border-t border-border/50 text-[11px] text-text-muted flex items-center justify-between">
                  <span>Auto-drains at midnight UTC</span>
                  {queueStats.quota_date && <span className="font-mono">{queueStats.quota_date}</span>}
                </div>
              </div>
            </section>
          )}

          {/* 3. RECENT ACTIVITY & SYSTEM CONFIRMATIONS */}
          <section className="space-y-2">
            <div className="flex items-center justify-between text-[11px] uppercase tracking-wider font-semibold text-text-secondary">
              <span className="flex items-center gap-1.5">
                <Bell size={13} className="text-text-muted" />
                Recent Confirmations
              </span>
              {notifications.length > 0 && (
                <button
                  type="button"
                  onClick={clearAllNotifications}
                  className="text-[11px] text-text-muted hover:text-text-main flex items-center gap-1 transition-colors"
                >
                  <Trash2 size={11} />
                  Clear all
                </button>
              )}
            </div>

            {notifications.length > 0 ? (
              <div className="space-y-1.5">
                {notifications.map((notif) => {
                  const isError = notif.type === 'error';
                  const isWarning = notif.type === 'warning';
                  const isSuccess = notif.type === 'success';

                  return (
                    <div
                      key={notif.id}
                      className={`p-2.5 rounded-xl border flex items-start justify-between gap-2.5 transition-all ${
                        isError
                          ? 'bg-rose-500/10 border-rose-500/25 text-rose-300'
                          : isWarning
                          ? 'bg-amber-500/10 border-amber-500/25 text-amber-300'
                          : isSuccess
                          ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-300'
                          : 'bg-surface-2 border-border text-text-main'
                      }`}
                    >
                      <div className="flex items-start gap-2 min-w-0">
                        {isError ? (
                          <AlertCircle size={14} className="text-danger shrink-0 mt-0.5" />
                        ) : isWarning ? (
                          <AlertCircle size={14} className="text-status-warning shrink-0 mt-0.5" />
                        ) : (
                          <CheckCircle2 size={14} className="text-status-success shrink-0 mt-0.5" />
                        )}
                        <div className="min-w-0">
                          <p className="text-xs font-medium leading-tight text-text-main break-words">
                            {notif.text}
                          </p>
                          <span className="text-[10px] text-text-muted tabular-nums mt-0.5 block">
                            {new Date(notif.timestamp).toLocaleTimeString([], {
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </span>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => dismissNotification(notif.id)}
                        className="text-text-muted hover:text-text-main p-1 rounded-lg shrink-0"
                        aria-label="Dismiss"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="p-3 rounded-xl bg-surface-2 border border-border text-xs text-text-muted text-center py-5">
                No recent confirmations or alerts.
              </div>
            )}
          </section>

          {/* 4. COMPLETED OR FAILED JOBS HISTORY (Compact list if any) */}
          {(completedJobs.length > 0 || failedJobs.length > 0) && (
            <section className="space-y-1.5 pt-1">
              <div className="text-[11px] uppercase tracking-wider font-semibold text-text-secondary">
                Job History
              </div>
              <div className="space-y-1 max-h-36 overflow-y-auto">
                {failedJobs.map((job) => (
                  <div
                    key={job.id}
                    className="p-2 rounded-lg bg-surface-2 border border-rose-500/20 text-xs flex items-center justify-between gap-2"
                  >
                    <span className="truncate text-text-secondary text-[11px]">
                      {job.youtube_url || job.id}
                    </span>
                    <span className="text-[10px] text-danger font-medium shrink-0">Failed</span>
                  </div>
                ))}
                {completedJobs.slice(0, 5).map((job) => (
                  <div
                    key={job.id}
                    className="p-2 rounded-lg bg-surface-2 border border-border text-xs flex items-center justify-between gap-2"
                  >
                    <span className="truncate text-text-secondary text-[11px]">
                      {job.youtube_url || job.id}
                    </span>
                    <span className="text-[10px] text-status-success font-medium shrink-0">Ready</span>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        {/* Modal Footer */}
        <div className="pt-2 border-t border-border flex items-center justify-between text-xs text-text-muted shrink-0">
          <span className="text-[11px]">Pull down on library lists to refresh</span>
          <button
            type="button"
            onClick={onClose}
            className="min-h-[36px] px-4 rounded-xl bg-surface-2 hover:bg-surface-3 text-text-main text-xs font-semibold active:scale-[0.98] transition-all border border-border"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};
