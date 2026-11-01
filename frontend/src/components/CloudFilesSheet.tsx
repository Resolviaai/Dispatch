import React, { useCallback, useEffect, useState } from 'react';
import { X, Eye, Trash2, Cloud, RotateCw } from 'lucide-react';
import { WORKER_BASE } from '../accounts/accountsService';

export interface CloudFileItem {
  id: string;
  kind: string;
  size: number;
  created_at: string;
  clip_id: string | null;
  local_clip_id: string | null;
  title: string | null;
  clip_status: string | null;
}

interface CloudFilesSheetProps {
  isOpen: boolean;
  onClose: () => void;
  jwtToken: string | null;
  onChanged?: () => void;
}

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 MB';
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? new Date(ms).toLocaleDateString() : '';
}

export const CloudFilesSheet: React.FC<CloudFilesSheetProps> = ({
  isOpen,
  onClose,
  jwtToken,
  onChanged,
}) => {
  const [items, setItems] = useState<CloudFileItem[]>([]);
  const [totalBytes, setTotalBytes] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [viewUrl, setViewUrl] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!jwtToken) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${WORKER_BASE}/media`, {
        headers: { Authorization: `Bearer ${jwtToken}` },
        cache: 'no-store',
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error || `Could not load cloud files (HTTP ${res.status}).`);
      setItems(Array.isArray(data?.items) ? data.items : []);
      setTotalBytes(Number.isFinite(Number(data?.total_bytes)) ? Number(data.total_bytes) : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load cloud files.');
    } finally {
      setLoading(false);
    }
  }, [jwtToken]);

  useEffect(() => {
    if (!isOpen) {
      setViewUrl(null);
      setConfirmId(null);
      return;
    }
    void load();
  }, [isOpen, load]);

  if (!isOpen) return null;

  const handleView = async (item: CloudFileItem) => {
    if (!jwtToken) return;
    setWorkingId(item.id);
    setError(null);
    try {
      const res = await fetch(`${WORKER_BASE}/media-url`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${jwtToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ media_object_id: item.id }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok || !data?.url) throw new Error(data?.error || `Could not open this file (HTTP ${res.status}).`);
      setViewUrl(data.url);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open this file.');
    } finally {
      setWorkingId(null);
    }
  };

  const handleDelete = async (item: CloudFileItem) => {
    if (!jwtToken) return;
    setWorkingId(item.id);
    setError(null);
    try {
      const res = await fetch(`${WORKER_BASE}/media/${encodeURIComponent(item.id)}/delete`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${jwtToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error || `Delete failed (HTTP ${res.status}).`);
      setItems(prev => prev.filter(row => row.id !== item.id));
      setTotalBytes(prev => (prev == null ? prev : Math.max(0, prev - (item.size || 0))));
      setConfirmId(null);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed.');
    } finally {
      setWorkingId(null);
    }
  };

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-[130] flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/80 backdrop-blur-sm animate-in fade-in select-none"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-t-3xl sm:rounded-2xl bg-canvas border border-border p-4 pb-8 sm:pb-4 shadow-2xl max-h-[85vh] flex flex-col overflow-hidden animate-in slide-in-from-bottom-4 duration-200"
      >
        <div className="w-10 h-1 rounded-full bg-surface-300/60 mx-auto -mt-1 mb-1 sm:hidden shrink-0" />

        <div className="flex items-center justify-between pb-2 shrink-0">
          <div className="flex items-center gap-2">
            <Cloud size={16} className="text-text-muted" />
            <h3 className="text-sm font-semibold tracking-tight text-text-main">Cloud files</h3>
            <span className="text-[11px] text-text-muted tabular-nums font-mono">
              {items.length}{totalBytes != null ? ` · ${formatSize(totalBytes)}` : ''}
            </span>
          </div>
          <div className="flex items-center gap-1 -mr-2">
            <button
              type="button"
              onClick={() => { void load(); }}
              disabled={loading}
              aria-label="Refresh cloud files"
              className="w-11 h-11 rounded-full flex items-center justify-center text-text-muted hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
            >
              <RotateCw size={15} className={loading ? 'animate-spin text-primary' : ''} />
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

        <div className="flex-1 overflow-y-auto border-t border-white/[0.06]">
          {error && (
            <p className="py-2 text-xs text-danger leading-relaxed">{error}</p>
          )}

          {!error && !loading && items.length === 0 && (
            <p className="py-10 text-center text-xs text-text-muted">No cloud files.</p>
          )}

          {items.map((item) => (
            <div key={item.id} className="py-2.5 border-b border-white/[0.06] last:border-b-0">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-text-main truncate">
                    {item.title || (item.kind === 'thumbnail' ? 'Thumbnail' : item.kind === 'edit_window' ? 'Edit window' : 'Untitled clip')}
                  </p>
                  <p className="text-[11px] text-text-muted tabular-nums mt-0.5">
                    {item.kind} · {formatSize(item.size)}
                    {item.created_at ? ` · ${formatDate(item.created_at)}` : ''}
                    {item.clip_status ? ` · ${item.clip_status}` : ''}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    onClick={() => { void handleView(item); }}
                    disabled={workingId === item.id}
                    aria-label="View"
                    className="w-11 h-11 rounded-xl flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all disabled:opacity-50"
                  >
                    <Eye size={16} />
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmId(item.id)}
                    disabled={workingId === item.id}
                    aria-label="Delete"
                    className="w-11 h-11 rounded-xl flex items-center justify-center text-text-secondary hover:text-danger hover:bg-danger/10 active:scale-[0.95] transition-all disabled:opacity-50"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {confirmId === item.id && (
                <div className="mt-2 rounded-xl bg-surface-2 border border-border p-3 space-y-2">
                  <p className="text-xs text-text-secondary leading-relaxed">
                    Delete this cloud copy permanently? Published posts stay online. If your PC is offline,
                    space is freed when it reconnects.
                  </p>
                  <div className="flex items-center justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => setConfirmId(null)}
                      className="min-h-[44px] px-4 rounded-xl bg-surface-3 text-xs font-medium text-text-secondary active:scale-[0.98] transition-all"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={() => { void handleDelete(item); }}
                      disabled={workingId === item.id}
                      className="min-h-[44px] px-4 rounded-xl bg-danger text-white text-xs font-semibold active:scale-[0.98] transition-all disabled:opacity-60"
                    >
                      {workingId === item.id ? 'Deleting…' : 'Delete'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {viewUrl && (
        <div
          onClick={(e) => { e.stopPropagation(); setViewUrl(null); }}
          className="fixed inset-0 z-[140] bg-black/90 flex items-center justify-center p-4"
        >
          <video controls autoPlay playsInline src={viewUrl} className="max-h-full max-w-full rounded-xl" />
        </div>
      )}
    </div>
  );
};
