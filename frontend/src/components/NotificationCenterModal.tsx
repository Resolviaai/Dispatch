import React from 'react';
import {
  X,
  Bell,
  RotateCw,
  CheckCircle2,
  AlertCircle,
  Trash2,
} from 'lucide-react';
import { useActivity, CloudJobItem } from '../context/ActivityContext';

interface NotificationCenterModalProps {
  isOpen: boolean;
  onClose: () => void;
  onRefreshAll?: () => Promise<void> | void;
}

function relativeTime(iso?: string): string {
  if (!iso) return '';
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return '';
  const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (sec < 60) return 'now';
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`;
  return `${Math.floor(sec / 86400)}d`;
}

function jobTitle(job: CloudJobItem): string {
  if (job.source_title) return job.source_title;
  return job.youtube_url ? 'YouTube video' : 'Processing job';
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
    activeDirectUpload,
    cancelDirectUpload,
  } = useActivity();

  if (!isOpen) return null;

  const handleManualRefresh = async () => {
    await Promise.allSettled([
      refreshActivity(),
      onRefreshAll ? Promise.resolve(onRefreshAll()) : Promise.resolve(),
    ]);
  };

  const activeJobs = cloudJobs.filter(
    (job) => job.status === 'queued' || job.status === 'claimed' || job.status === 'processing'
  );
  const finishedJobs = cloudJobs
    .filter((job) => job.status === 'completed' || job.status === 'done' || job.status === 'failed')
    .slice(0, 5);

  // The same confirmation can be pushed more than once; show each message once.
  const uniqueNotifications = notifications
    .filter((notif, index, list) => list.findIndex((other) => other.text === notif.text) === index)
    .slice(0, 4);

  const queued = queueStats?.queued_count ?? 0;
  const quotaUsed = queueStats?.daily_quota_used ?? 0;
  const quotaMax = queueStats?.daily_quota_max ?? 10;
  const hasQueueInfo = queued > 0 || quotaUsed > 0;

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-[120] flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/75 backdrop-blur-sm animate-in fade-in select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-t-3xl sm:rounded-2xl bg-canvas border border-border p-4 pb-8 sm:pb-4 shadow-2xl max-h-[85vh] flex flex-col overflow-hidden animate-in slide-in-from-bottom-4 duration-200"
      >
        <div className="w-10 h-1 rounded-full bg-surface-300/60 mx-auto -mt-1 mb-1 sm:hidden shrink-0" />

        <div className="flex items-center justify-between pb-2 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <Bell size={16} className="text-text-muted shrink-0" />
            <h3 className="text-sm font-semibold tracking-tight text-text-main">Activity</h3>
            {activeJobsCount > 0 && (
              <span className="w-2 h-2 rounded-full bg-primary animate-pulse" />
            )}
          </div>
          <div className="flex items-center gap-1 -mr-2">
            <button
              type="button"
              onClick={handleManualRefresh}
              disabled={isRefreshing}
              aria-label="Refresh activity"
              className="w-11 h-11 rounded-full flex items-center justify-center text-text-muted hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
            >
              <RotateCw size={15} className={isRefreshing ? 'animate-spin text-primary' : ''} />
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="w-11 h-11 rounded-full flex items-center justify-center text-text-muted hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {/* Background Direct Upload Card if active */}
          {activeDirectUpload && activeDirectUpload.phase !== 'idle' && (
            <div className="mb-3 p-3 rounded-xl bg-surface-2 border border-border space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-text-main truncate max-w-[200px]">
                  {activeDirectUpload.fileName}
                </span>
                <span className="text-[11px] font-mono tabular-nums text-primary font-semibold">
                  {activeDirectUpload.phase === 'uploading'
                    ? `${activeDirectUpload.progressPct}%`
                    : activeDirectUpload.phase === 'checking'
                    ? 'Checking…'
                    : activeDirectUpload.phase === 'submitting'
                    ? 'Queueing…'
                    : activeDirectUpload.phase === 'done'
                    ? 'Uploaded!'
                    : 'Error'}
                </span>
              </div>
              {activeDirectUpload.phase === 'uploading' && (
                <div className="h-1.5 rounded-full bg-surface-3 overflow-hidden">
                  <div
                    className="h-full bg-primary transition-all duration-150"
                    style={{ width: `${activeDirectUpload.progressPct}%` }}
                  />
                </div>
              )}
              {activeDirectUpload.error && (
                <p className="text-[11px] text-danger leading-tight">{activeDirectUpload.error}</p>
              )}
              {(activeDirectUpload.phase === 'uploading' || activeDirectUpload.phase === 'checking') && (
                <div className="flex justify-end pt-1">
                  <button
                    type="button"
                    onClick={cancelDirectUpload}
                    className="text-[11px] text-danger hover:underline active:scale-95 transition-all"
                  >
                    Cancel upload
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Status: one line, only what is happening now. */}
          <div className="py-2.5 flex items-center gap-2 text-xs">
            {activeJobs.length > 0 ? (
              <>
                <RotateCw size={13} className="text-primary animate-spin shrink-0" />
                <span className="text-text-main font-medium">
                  {activeJobs.length} processing
                </span>
              </>
            ) : (
              <>
                <CheckCircle2 size={13} className="text-status-success shrink-0" />
                <span className="text-text-muted">Nothing running</span>
              </>
            )}
            {hasQueueInfo && (
              <span className="text-text-muted tabular-nums font-mono ml-auto">
                {queued} queued · {quotaUsed}/{quotaMax} today
              </span>
            )}
          </div>

          {activeJobs.length > 0 && (
            <div className="pb-2 space-y-1">
              {activeJobs.slice(0, 3).map((job) => (
                <div key={job.id} className="flex items-center justify-between gap-2 text-xs py-0.5">
                  <span className="text-text-secondary truncate">{jobTitle(job)}</span>
                  <span className="text-text-muted text-[10px] font-mono tabular-nums shrink-0">
                    {job.stage || (job.status === 'claimed' ? 'processing' : 'queued')}
                  </span>
                </div>
              ))}
            </div>
          )}

          {uniqueNotifications.length > 0 && (
            <section className="pt-3">
              <div className="flex items-center justify-between py-1.5">
                <span className="text-xs text-text-muted">Recent</span>
                <button
                  type="button"
                  onClick={clearAllNotifications}
                  className="text-xs text-text-muted hover:text-text-main flex items-center gap-1 transition-colors active:scale-[0.98]"
                >
                  <Trash2 size={11} />
                  Clear
                </button>
              </div>
              {uniqueNotifications.map((notif) => {
                const isError = notif.type === 'error';
                const isWarning = notif.type === 'warning';
                return (
                  <div
                    key={notif.id}
                    className="flex items-start gap-2.5 py-2"
                  >
                    {isError ? (
                      <AlertCircle size={13} className="text-danger shrink-0 mt-0.5" />
                    ) : isWarning ? (
                      <AlertCircle size={13} className="text-status-warning shrink-0 mt-0.5" />
                    ) : (
                      <CheckCircle2 size={13} className="text-status-success shrink-0 mt-0.5" />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="text-xs text-text-main leading-snug break-words">{notif.text}</p>
                      <span className="text-[10px] text-text-muted tabular-nums">
                        {new Date(notif.timestamp).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => dismissNotification(notif.id)}
                      className="w-8 h-8 -my-1 rounded-lg flex items-center justify-center text-text-muted hover:text-text-main shrink-0 active:scale-[0.95] transition-all"
                      aria-label="Dismiss"
                    >
                      <X size={12} />
                    </button>
                  </div>
                );
              })}
            </section>
          )}

          {finishedJobs.length > 0 && (
            <section className="pt-3">
              <div className="py-1.5 text-xs text-text-muted">History</div>
              {finishedJobs.map((job) => {
                const failed = job.status === 'failed';
                return (
                  <div
                    key={job.id}
                    className="flex items-center justify-between gap-2 py-2 text-xs"
                  >
                    <span className="text-text-secondary truncate">{jobTitle(job)}</span>
                    <span className="flex items-center gap-2 shrink-0">
                      <span className="text-[10px] text-text-muted font-mono tabular-nums">
                        {relativeTime(job.created_at)}
                      </span>
                      <span className={`text-[10px] font-medium ${failed ? 'text-danger' : 'text-status-success'}`}>
                        {failed ? 'Failed' : 'Ready'}
                      </span>
                    </span>
                  </div>
                );
              })}
            </section>
          )}

          {uniqueNotifications.length === 0 && finishedJobs.length === 0 && activeJobs.length === 0 && (
            <div className="py-10 text-center text-xs text-text-muted">
              No recent activity.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
