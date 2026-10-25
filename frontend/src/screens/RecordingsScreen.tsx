import React, { useState, useEffect, useMemo } from 'react';
import {
  Film,
  RotateCw,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  AlertCircle,
  Video,
  Download,
  Maximize2,
  X,
  Sparkles,
  Clock,
  HardDrive,
  Camera,
  Play,
  Youtube,
  CheckCircle2
} from 'lucide-react';
import { RecordingItem, apiUrl, getAuthHeaders } from '../bridge';

interface YouTubeInboxItem {
  video_id: string;
  title?: string;
  status: string;
  last_error?: string | null;
  transcript_source?: string | null;
  discovered_at?: string;
  duration?: number;
}

export const isUploadedSegment = (item: RecordingItem): boolean => {
  if (item.youtubeVideoId && item.youtubeVideoId.trim().length > 0) return true;
  return (
    item.status === 'UPLOADED_TO_YOUTUBE' ||
    item.status === 'PROCESSED_ON_YOUTUBE' ||
    item.status === 'UPLOADED_AWAITING_PROCESSING' ||
    item.status === 'VERIFIED_BY_LAPTOP'
  );
};

export interface SessionGroup {
  sessionId: string;
  createdAt: number;
  totalDuration: number;
  totalBytes: number;
  segments: RecordingItem[];
  clipCount: number;
  approvedClipCount: number;
  status: 'UPLOADED_TO_YOUTUBE' | 'PROCESSING' | 'QUEUED_FOR_UPLOAD' | 'FAILED_RETRY' | 'UPLOADING';
  youtubeVideoId?: string;
}

interface RecordingsScreenProps {
  recordings: RecordingItem[];
  isYouTubeConnected: boolean;
  onNavigateToSettings: () => void;
  onRetryUpload: (segmentId: string) => void;
  onNavigateToClips?: (sessionId?: string) => void;
  onNavigateToRecord?: () => void;
  pcHost?: string;
}

export const RecordingsScreen: React.FC<RecordingsScreenProps> = ({ 
  recordings, 
  isYouTubeConnected,
  onNavigateToSettings,
  onRetryUpload,
  onNavigateToClips,
  onNavigateToRecord,
  pcHost
}) => {
  const [filter, setFilter] = useState<'all' | 'queued' | 'uploaded'>('all');
  const [modalVideo, setModalVideo] = useState<{ url: string; title: string } | null>(null);
  const [expandedSessionIds, setExpandedSessionIds] = useState<Set<string>>(new Set());
  const [expandedDetailIds, setExpandedDetailIds] = useState<Set<string>>(new Set());
  const [importUrl, setImportUrl] = useState('');
  const [importState, setImportState] = useState<'idle' | 'submitting' | 'queued' | 'error'>('idle');
  const [importError, setImportError] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const [clipsStale, setClipsStale] = useState(false);
  const [inboxStale, setInboxStale] = useState(false);
  const [inboxItems, setInboxItems] = useState<YouTubeInboxItem[]>([]);

  // Mirrors the server-side video ID extraction (dispatch/youtube_inbox/catcher.py)
  const extractVideoId = (raw: string): string | null => {
    const cleaned = raw.trim();
    if (/^[a-zA-Z0-9_-]{11}$/.test(cleaned)) return cleaned;
    const match = cleaned.match(/(?:v=|\/|youtu\.be\/|embed\/|shorts\/)([a-zA-Z0-9_-]{11})(?:\?|&|$)/);
    return match ? match[1] : null;
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0\u00a0B';
    const mb = bytes / (1024 * 1024);
    if (mb < 1) return `${Math.round(bytes / 1024)}\u00a0KB`;
    if (mb >= 1024) return `${(mb / 1024).toFixed(2)}\u00a0GB`;
    return `${mb.toFixed(1)}\u00a0MB`;
  };

  const formatDuration = (sec?: number) => {
    if (!sec) return '00:00';
    const m = Math.floor(sec / 60).toString().padStart(2, '0');
    const s = Math.floor(sec % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  };

  const formatTotalTime = (totalSec: number) => {
    if (totalSec <= 0) return '0\u00a0s';
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = Math.floor(totalSec % 60);
    if (h > 0) return `${h}\u00a0h ${m}\u00a0m`;
    if (m > 0) return `${m}\u00a0m ${s}\u00a0s`;
    return `${s}\u00a0s`;
  };

  const formatSessionDate = (timestamp: number) => {
    const date = new Date(timestamp);
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (isToday) {
      return `Today at ${timeStr}`;
    }
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) {
      return `Yesterday at ${timeStr}`;
    }
    return `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })} at ${timeStr}`;
  };

  const formatTimeAgo = (timestamp: number) => {
    const diff = Math.floor((Date.now() - timestamp) / 1000);
    if (diff < 60) return `${diff}\u00a0s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}\u00a0m ago`;
    return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  // Fetch ready/approved clips to enrich session metadata with exact matching
  const [clips, setClips] = useState<{ id: string; session_id?: string; chunk_id?: string; status: string }[]>([]);

  useEffect(() => {
    if (!pcHost) return;
    let isMounted = true;
    const fetchClips = async () => {
      try {
        const res = await fetch(apiUrl('/api/clips', pcHost));
        if (res.ok && isMounted) {
          const data = await res.json();
          const ready = Array.isArray(data.ready) ? data.ready : [];
          const approved = Array.isArray(data.recent_approved) ? data.recent_approved : [];
          setClips([...ready, ...approved]);
          setClipsStale(false);
        }
      } catch {
        if (isMounted) setClipsStale(true); // keep cached data, but mark it stale
      }
    };
    fetchClips();
    const interval = setInterval(fetchClips, 4000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [pcHost]);

  // Poll the existing YouTube Inbox API so imported link statuses stay live
  useEffect(() => {
    if (!pcHost) return;
    let isMounted = true;
    const fetchInbox = async () => {
      try {
        const res = await fetch(apiUrl('/api/youtube/inbox', pcHost));
        if (res.ok && isMounted) {
          const data = await res.json();
          setInboxItems(Array.isArray(data.items) ? data.items : []);
          setInboxStale(false);
        }
      } catch {
        if (isMounted) setInboxStale(true); // keep cached data, but mark it stale
      }
    };
    fetchInbox();
    const interval = setInterval(fetchInbox, 5000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [pcHost]);

  const handleImportSubmit = async (rawOverride?: string) => {
    const value = (rawOverride ?? importUrl).trim();
    const videoId = extractVideoId(value);
    if (!videoId) {
      setImportState('error');
      setImportNotice(null);
      setImportError('Enter a valid YouTube link (e.g. https://youtu.be/XXXXXXXXXXX) or an 11-character video ID.');
      return;
    }
    setImportState('submitting');
    setImportError(null);
    setImportNotice(null);
    try {
      // ANY-NETWORK SUBMISSION: signed-in users submit through the cloud Worker,
      // so processing works from any network (the PC claims jobs from the cloud).
      // The direct PC path remains for the owner's LAN as a fallback.
      const { currentSession } = await import('../auth/supabase');
      const sess = await currentSession();
      let res: Response;
      if (sess?.access_token) {
        res = await fetch('https://dispatch-cloud.resolviaai.workers.dev/jobs/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${sess.access_token}` },
          body: JSON.stringify({ url: value })
        });
      } else {
        res = await fetch(apiUrl('/api/youtube/ingest', pcHost), {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify({ url: value })
        });
      }
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        // Truthful feedback only after the API responds; live progress appears in the imported list below.
        setImportState('queued');
        setImportNotice(
          data.status === 'already_processing'
            ? 'This video is already being processed — tracking it below.'
            : 'Accepted — queued in the cloud. Your PC picks it up automatically and processes it.'
        );
        if (!rawOverride) setImportUrl('');
      } else {
        setImportState('error');
        setImportError(data.detail || `Import rejected (HTTP ${res.status}).`);
      }
    } catch (e: any) {
      setImportState('error');
      setImportError(e?.message || 'Could not reach the PC. Check the connection and retry.');
    }
  };

  // Group recording segments by their authentic sessionId
  const sessions: SessionGroup[] = useMemo(() => {
    const map = new Map<string, RecordingItem[]>();
    
    for (const item of recordings) {
      const sId = (item.sessionId && item.sessionId.trim().length > 0) ? item.sessionId : item.segmentId;
      if (!map.has(sId)) {
        map.set(sId, []);
      }
      map.get(sId)!.push(item);
    }

    const groups: SessionGroup[] = [];

    for (const [sessionId, rawSegments] of map.entries()) {
      const segments = [...rawSegments].sort((a, b) => {
        if (a.sequenceNumber !== b.sequenceNumber) {
          return (a.sequenceNumber || 1) - (b.sequenceNumber || 1);
        }
        return (a.createdAt || 0) - (b.createdAt || 0);
      });

      const totalDuration = segments.reduce((sum, s) => sum + (s.durationSeconds || 0), 0);
      const totalBytes = segments.reduce((sum, s) => sum + (s.fileSizeBytes || 0), 0);
      const earliestCreatedAt = segments.reduce((min, s) => Math.min(min, s.createdAt || Date.now()), Date.now());

      const segmentIds = new Set(segments.map(s => s.segmentId));
      const matchingClips = clips.filter(c => {
        if (c.session_id && c.session_id === sessionId) return true;
        if (c.chunk_id && segmentIds.has(c.chunk_id)) return true;
        return false;
      });

      const dbClipCount = segments.reduce((sum, s) => sum + (s.clipCount || 0), 0);
      const clipCount = Math.max(dbClipCount, matchingClips.length);

      const dbApprovedCount = segments.reduce((sum, s) => sum + (s.approvedClipCount || 0), 0);
      const approvedMatchingCount = matchingClips.filter(c => c.status === 'approved' || c.status === 'published').length;
      const approvedClipCount = Math.max(dbApprovedCount, approvedMatchingCount);

      let status: SessionGroup['status'] = 'QUEUED_FOR_UPLOAD';
      const hasFailed = segments.some(s => s.status === 'FAILED_RETRY' || s.status === 'FAILED_RECORDING');
      const hasUploading = segments.some(s => s.status === 'UPLOADING');
      const allUploaded = segments.length > 0 && segments.every(s => isUploadedSegment(s));
      const hasProcessing = segments.some(s => s.status === 'PROCESSING' || s.status === 'TRANSCRIBING');

      if (hasFailed) {
        status = 'FAILED_RETRY';
      } else if (hasUploading) {
        status = 'UPLOADING';
      } else if (clipCount > 0 || allUploaded) {
        status = 'UPLOADED_TO_YOUTUBE';
      } else if (hasProcessing) {
        status = 'PROCESSING';
      } else {
        status = 'QUEUED_FOR_UPLOAD';
      }

      const youtubeVideoId = segments.find(s => s.youtubeVideoId)?.youtubeVideoId || undefined;

      groups.push({
        sessionId,
        createdAt: earliestCreatedAt,
        totalDuration,
        totalBytes,
        segments,
        clipCount,
        approvedClipCount,
        status,
        youtubeVideoId,
      });
    }

    groups.sort((a, b) => b.createdAt - a.createdAt);
    return groups;
  }, [recordings, clips]);

  // Auto-expand newest session if none expanded yet
  useEffect(() => {
    if (sessions.length > 0 && expandedSessionIds.size === 0) {
      setExpandedSessionIds(new Set([sessions[0].sessionId]));
    }
  }, [sessions, expandedSessionIds]);

  const toggleExpandSession = (sessionId: string) => {
    setExpandedSessionIds(prev => {
      const next = new Set(prev);
      if (next.has(sessionId)) {
        next.delete(sessionId);
      } else {
        next.add(sessionId);
      }
      return next;
    });
  };

  const toggleExpandDetail = (segmentId: string) => {
    setExpandedDetailIds(prev => {
      const next = new Set(prev);
      if (next.has(segmentId)) {
        next.delete(segmentId);
      } else {
        next.add(segmentId);
      }
      return next;
    });
  };

  const filteredSessions = useMemo(() => {
    if (filter === 'uploaded') {
      return sessions.filter(s => s.status === 'UPLOADED_TO_YOUTUBE');
    }
    if (filter === 'queued') {
      return sessions.filter(s => s.status !== 'UPLOADED_TO_YOUTUBE');
    }
    return sessions;
  }, [sessions, filter]);

  // Imported links = inbox records not produced by a native phone upload already shown in Sessions.
  // The youtube_inbox table stays the single source of truth; native videos keep their own cards.
  const importedItems = useMemo(() => {
    const nativeIds = new Set(recordings.map(r => r.youtubeVideoId).filter(Boolean) as string[]);
    return inboxItems.filter(item => !nativeIds.has(item.video_id));
  }, [inboxItems, recordings]);

  const queuedSessionsCount = sessions.filter(s => s.status !== 'UPLOADED_TO_YOUTUBE').length;
  const uploadedSessionsCount = sessions.filter(s => s.status === 'UPLOADED_TO_YOUTUBE').length;
  const totalStorageBytes = recordings.reduce((acc, r) => acc + (r.fileSizeBytes || 0), 0);
  const totalFootageDuration = recordings.reduce((acc, r) => acc + (r.durationSeconds || 0), 0);

  const renderSessionStatusBadge = (session: SessionGroup) => {    switch (session.status) {
      case 'UPLOADED_TO_YOUTUBE':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border chip-success">
            <span className="w-1.5 h-1.5 rounded-full bg-current" />
            Uploaded
          </span>
        );
      case 'UPLOADING':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border chip-info">
            <RotateCw size={11} className="animate-spin" />
            Uploading…
          </span>
        );
      case 'PROCESSING':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border chip-warning">
            <RotateCw size={11} className="animate-spin" />
            Processing…
          </span>
        );
      case 'FAILED_RETRY':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border chip-danger">
            <AlertCircle size={11} />
            Needs Retry
          </span>
        );
      case 'QUEUED_FOR_UPLOAD':
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border bg-surface-200 border-border text-text-muted">
            <span className="w-1.5 h-1.5 rounded-full bg-warning" />
            Queued
          </span>
        );
    }
  };

  const renderImportStatusBadge = (status: string) => {
    if (status === 'CLIPS_CREATED' || status === 'COMPLETED') {
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border chip-success shrink-0">
          <CheckCircle2 size={11} />
          Clips Ready
        </span>
      );
    }
    if (status === 'FAILED' || status === 'STALLED_YOUTUBE_PROCESSING') {
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border chip-danger shrink-0">
          <AlertCircle size={11} />
          {status === 'FAILED' ? 'Failed' : 'Stalled'}
        </span>
      );
    }
    if (status === 'DISCOVERED') {
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border bg-surface-200 border-border text-text-muted shrink-0">
          <span className="w-1.5 h-1.5 rounded-full bg-warning" />
          Queued
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border chip-warning shrink-0">
        <RotateCw size={11} className="animate-spin" />
        Processing…
      </span>
    );
  };

  return (
    <div className="w-full h-full flex flex-col bg-canvas text-text-main overflow-hidden select-none">
      
      {/* 1. SCREEN HEADER (Canvas layer) */}
      <div className="shrink-0 px-4 pt-4 pb-3 border-b border-border bg-canvas space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-text-main flex items-center gap-2">
              {(clipsStale || inboxStale) && (
                <span role="status" className="px-2 py-0.5 rounded-full border text-xs font-medium chip-warning">
                  Cached — refresh failed
                </span>
              )}
              <Film size={19} className="text-primary shrink-0" />
              <span>Sessions</span>
            </h1>
            <p className="text-xs text-text-muted mt-0.5 tabular-nums">
              Raw multi-segment footage filmed from mobile hardware
            </p>
          </div>

          {/* YouTube Connection Status Pill (44px hitbox) */}
          <button
            type="button"
            onClick={onNavigateToSettings}
            className="flex items-center gap-2 px-3 py-2 min-h-[44px] rounded-xl bg-surface-200 hover:bg-surface-3 border border-border text-sm text-text-secondary transition-all duration-150 active:scale-[0.98]"
            title="Manage YouTube Cloud Integration"
          >
            <span className={`w-2 h-2 rounded-full shrink-0 ${isYouTubeConnected ? 'bg-success' : 'bg-danger'}`} />
            <span className="font-medium">{isYouTubeConnected ? 'YouTube' : 'No YouTube'}</span>
          </button>
        </div>

        {/* Operational Metrics Bar: numeric chips */}
        <div className="flex items-center gap-2 flex-wrap">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-200 border border-border text-xs text-text-secondary">
            <Film size={12} className="text-primary shrink-0" />
            <span className="font-mono tabular-nums">{sessions.length}&nbsp;{sessions.length === 1 ? 'session' : 'sessions'}</span>
          </div>

          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-200 border border-border text-xs text-text-secondary">
            <Clock size={12} className="text-text-muted shrink-0" />
            <span className="font-mono tabular-nums">{formatTotalTime(totalFootageDuration)}&nbsp;footage</span>
          </div>

          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-200 border border-border text-xs text-text-secondary">
            <HardDrive size={12} className="text-text-muted shrink-0" />
            <span className="font-mono tabular-nums">{formatBytes(totalStorageBytes)}&nbsp;storage</span>
          </div>
        </div>

        {/* Filter Chips Bar (8px radius, All -> Queued -> Uploaded, YouTube Studio style) */}
        {sessions.length > 0 && (
          <div className="flex items-center gap-2" role="tablist" aria-label="Filter sessions">
            <button
              type="button"
              onClick={() => setFilter('all')}
              aria-selected={filter === 'all'}
              className={`min-h-[36px] py-1.5 px-3 rounded-lg text-xs font-semibold tabular-nums transition-all duration-150 active:scale-[0.98] border ${
                filter === 'all'
                  ? 'bg-primary text-white border-primary shadow-sm'
                  : 'bg-surface-200 border-border text-text-secondary hover:text-text-main'
              }`}
            >
              All ({sessions.length})
            </button>
            <button
              type="button"
              onClick={() => setFilter('queued')}
              aria-selected={filter === 'queued'}
              className={`min-h-[36px] py-1.5 px-3 rounded-lg text-xs font-semibold tabular-nums transition-all duration-150 active:scale-[0.98] border ${
                filter === 'queued'
                  ? 'bg-primary text-white border-primary shadow-sm'
                  : 'bg-surface-200 border-border text-text-secondary hover:text-text-main'
              }`}
            >
              Queued ({queuedSessionsCount})
            </button>
            <button
              type="button"
              onClick={() => setFilter('uploaded')}
              aria-selected={filter === 'uploaded'}
              className={`min-h-[36px] py-1.5 px-3 rounded-lg text-xs font-semibold tabular-nums transition-all duration-150 active:scale-[0.98] border ${
                filter === 'uploaded'
                  ? 'bg-primary text-white border-primary shadow-sm'
                  : 'bg-surface-200 border-border text-text-secondary hover:text-text-main'
              }`}
            >
              Uploaded ({uploadedSessionsCount})
            </button>
          </div>
        )}
      </div>

      {/* 2. YOUTUBE WARNING BANNER IF DISCONNECTED */}
      {!isYouTubeConnected && recordings.length > 0 && (
        <div 
          onClick={onNavigateToSettings}
          className="mx-4 mt-3 p-3 min-h-[44px] rounded-xl chip-warning border flex items-center justify-between cursor-pointer hover:opacity-85 transition-all duration-150 active:scale-[0.98]"
        >
          <div className="flex items-center gap-2 text-status-warning">
            <AlertCircle size={15} className="shrink-0" />
            <span className="text-[11px] font-mono">YouTube not connected. Chunks safely stored on device.</span>
          </div>
          <span className="text-sm font-semibold underline shrink-0 ml-2 text-status-warning">Connect</span>
        </div>
      )}

      {/* 3. SESSIONS SCROLL AREA - Single-direction vertical flow */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3.5">

        {/* ROUTE B ENTRY POINT: Import from YouTube (POST /api/youtube/ingest) */}
        <div className="rounded-2xl bg-surface-100 border border-border p-3.5 space-y-2.5">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-surface-200 border border-border flex items-center justify-center shrink-0">
              <Youtube size={15} className="text-danger" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-text-main">Import from YouTube</h2>
              <p className="text-xs text-text-muted leading-relaxed">
                Record with your normal camera, upload to YouTube, then paste the link here.
              </p>
            </div>
          </div>

          <form
            onSubmit={(e) => { e.preventDefault(); handleImportSubmit(); }}
            className="flex items-center gap-2"
          >
            <input
              type="text"
              inputMode="url"
              value={importUrl}
              onChange={(e) => {
                setImportUrl(e.target.value);
                if (importState === 'error') { setImportState('idle'); setImportError(null); }
                if (importState === 'queued') { setImportState('idle'); setImportNotice(null); }
              }}
              placeholder="https://youtube.com/watch?v=…"
              disabled={importState === 'submitting'}
              aria-label="YouTube video link"
              className="flex-1 min-w-0 min-h-[44px] px-3 py-2 rounded-lg bg-surface-200 border border-border text-sm font-mono text-text-main placeholder:text-text-muted focus:outline-none focus:border-primary disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={importState === 'submitting' || !importUrl.trim()}
              className="min-h-[44px] px-4 py-2 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border text-sm font-semibold text-text-main flex items-center gap-1.5 shrink-0 transition-all duration-150 active:scale-[0.98] disabled:opacity-50 disabled:active:scale-100"
            >
              {importState === 'submitting'
                ? <RotateCw size={13} className="animate-spin" />
                : <Download size={13} />}
              <span>{importState === 'submitting' ? 'Importing…' : 'Import session'}</span>
            </button>
          </form>

          {importState === 'error' && importError && (
            <p className="text-xs chip-danger border rounded-lg px-2.5 py-2 flex items-start gap-1.5">
              <AlertCircle size={13} className="shrink-0 mt-0.5" />
              <span className="break-words">{importError}</span>
            </p>
          )}
          {importState === 'queued' && importNotice && (
            <p className="text-xs chip-success border rounded-lg px-2.5 py-2 break-words">
              {importNotice}
            </p>
          )}
        </div>

        {/* IMPORTED YOUTUBE SESSIONS (existing youtube_inbox records, live status) */}
        {importedItems.length > 0 && (
          <div className="space-y-2">
            <span className="text-xs uppercase tracking-wider text-text-muted font-semibold">
              Imported YouTube Sessions ({importedItems.length})
            </span>
            {importedItems.map((item) => {
              const isFailed = item.status === 'FAILED' || item.status === 'STALLED_YOUTUBE_PROCESSING';
              const isReady = item.status === 'CLIPS_CREATED' || item.status === 'COMPLETED';
              return (
                <div key={item.video_id} className="rounded-xl bg-surface-100 border border-border p-3 space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h3 className="text-sm font-semibold text-text-main truncate">{item.title || `YouTube ${item.video_id}`}</h3>
                      <p className="text-xs font-mono tabular-nums text-text-muted mt-0.5">
                        ID: {item.video_id}{item.duration ? ` · ${formatDuration(item.duration)}` : ''}
                      </p>
                    </div>
                    {renderImportStatusBadge(item.status)}
                  </div>
                  {isFailed && (
                    <div className="space-y-2">
                      {item.last_error && (
                        <p className="text-xs font-mono chip-danger border rounded-lg px-2.5 py-2 break-words">
                          {item.last_error}
                        </p>
                      )}
                      <button
                        type="button"
                        onClick={() => handleImportSubmit(`https://youtu.be/${item.video_id}`)}
                        disabled={importState === 'submitting'}
                        className="min-h-[44px] px-3 py-2 rounded-lg chip-danger border text-xs font-medium hover:opacity-85 flex items-center gap-1.5 transition-all duration-150 active:scale-[0.98] disabled:opacity-50"
                      >
                        <RotateCw size={12} />
                        <span>Retry Import</span>
                      </button>
                    </div>
                  )}
                  {isReady && (
                    <p className="text-xs text-status-success">
                      Clips are in the review queue.
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {filteredSessions.length === 0 ? (
          /* HONEST EMPTY STATE (Clean & Minimalist) */
          <div className="h-full min-h-[280px] flex flex-col items-center justify-center text-center p-8 rounded-2xl bg-surface-100 border border-border">
            <div className="w-14 h-14 rounded-2xl bg-surface-200 border border-border flex items-center justify-center mb-4 shadow-inner">
              <Film className="w-7 h-7 text-primary" />
            </div>
            <h2 className="text-base font-semibold tracking-tight text-text-main">No Recorded Sessions Yet</h2>
            <p className="text-sm text-text-secondary mt-1.5 max-w-[280px] leading-relaxed">
              Continuous footage recorded from the Record tab will appear here, grouped into organized filming sessions.
            </p>
            <div className="mt-4 px-3 py-1.5 rounded-lg bg-surface-200 border border-border text-xs font-mono tabular-nums text-text-secondary">
              DCIM/Dispatch · 1080p FHD Capture
            </div>
            {onNavigateToRecord && (
              <button
                type="button"
                onClick={onNavigateToRecord}
                className="mt-5 min-h-[44px] px-5 py-2 rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold shadow-hero-glow flex items-center gap-2 active:scale-[0.98] transition-all"
              >
                <Camera size={15} />
                <span>Start Filming</span>
              </button>
            )}
          </div>
        ) : (
          /* YOUTUBE STUDIO ANATOMY: Flat divider-free rows separated by whitespace alone (py-3), 16:9 thumbnails (w-[132px] aspect-video) */
          filteredSessions.map((session) => {
            const isExpanded = expandedSessionIds.has(session.sessionId);
            const firstSegment = session.segments[0];
            const ytId = session.youtubeVideoId || firstSegment?.youtubeVideoId;

            return (
              <div
                key={session.sessionId}
                className="py-3 flex flex-col gap-2.5 transition-colors"
              >
                {/* STUDIO ROW (16:9 Thumbnail + Metadata + Hero Actions) */}
                <div
                  onClick={() => toggleExpandSession(session.sessionId)}
                  className="flex items-start gap-3.5 cursor-pointer select-none group"
                >
                  {/* 16:9 Thumbnail (w-[132px] aspect-video) */}
                  <div className="w-[132px] aspect-video shrink-0 rounded-lg overflow-hidden bg-surface-200 border border-border relative flex items-center justify-center">
                    {ytId ? (
                      <img
                        src={`https://img.youtube.com/vi/${ytId}/mqdefault.jpg`}
                        alt="Session thumbnail"
                        className="w-full h-full object-cover"
                        loading="lazy"
                      />
                    ) : firstSegment?.videoUrl ? (
                      <video
                        src={firstSegment.videoUrl.startsWith('http') ? firstSegment.videoUrl : apiUrl(firstSegment.videoUrl)}
                        className="w-full h-full object-cover"
                        preload="metadata"
                      />
                    ) : (
                      <Film size={22} className="text-text-muted" />
                    )}
                    {/* Duration badge */}
                    <span className="absolute bottom-1 right-1 px-1 py-0.5 rounded bg-black/80 text-[10px] font-mono tabular-nums font-semibold text-white pointer-events-none">
                      {formatDuration(session.totalDuration)}
                    </span>
                  </div>

                  {/* Metadata & Actions */}
                  <div className="flex-1 min-w-0 flex flex-col justify-between self-stretch">
                    <div>
                      <div className="flex items-start justify-between gap-2">
                        <h2 className="text-sm font-semibold text-text-main line-clamp-2 leading-tight group-hover:text-primary transition-colors">
                          {formatSessionDate(session.createdAt)}
                        </h2>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleExpandSession(session.sessionId);
                          }}
                          className="w-8 h-8 rounded-lg flex items-center justify-center text-text-muted hover:text-text-main hover:bg-surface-200 transition-colors shrink-0"
                          aria-label={isExpanded ? 'Collapse Session' : 'Expand Session'}
                        >
                          {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                        </button>
                      </div>

                      <div className="flex items-center gap-1.5 mt-1 text-xs font-mono tabular-nums text-text-muted flex-wrap">
                        <span>{session.segments.length}&nbsp;{session.segments.length === 1 ? 'segment' : 'segments'}</span>
                        <span>·</span>
                        <span>{formatBytes(session.totalBytes)}</span>
                      </div>
                    </div>

                    <div className="flex items-center justify-between gap-2 mt-2 pt-1">
                      {renderSessionStatusBadge(session)}

                      {session.clipCount > 0 && onNavigateToClips && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onNavigateToClips(session.sessionId);
                          }}
                          className="min-h-[36px] px-3 py-1.5 rounded-xl bg-primary hover:bg-primary-hover text-white text-xs font-semibold shadow-hero-glow flex items-center gap-1.5 shrink-0 transition-all active:scale-[0.98]"
                        >
                          <Sparkles size={12} />
                          <span>{session.clipCount} {session.clipCount === 1 ? 'Clip' : 'Clips'}</span>
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                {/* EXPANDED RAW FOOTAGE SECTION (Flat rows, whitespace separated, no divider lines) */}
                {isExpanded && (
                  <div className="pl-2 pt-2 space-y-3 animate-in fade-in duration-150">
                    <div className="flex items-center justify-between">
                      <span className="text-xs uppercase tracking-wider text-text-muted font-semibold">
                        Raw Footage ({session.segments.length}&nbsp;{session.segments.length === 1 ? 'segment' : 'segments'})
                      </span>
                      <span className="text-xs font-medium px-2 py-0.5 rounded-lg border chip-success">
                        Unedited Originals
                      </span>
                    </div>

                    <div className="space-y-3">
                      {session.segments.map((segment, idx) => {
                        const isDetailExpanded = expandedDetailIds.has(segment.segmentId);
                        const hasVideo = !!segment.videoUrl;

                        return (
                          <div
                            key={segment.segmentId}
                            className="p-2.5 rounded-xl bg-surface-100/60 flex flex-col gap-2.5"
                          >
                            <div className="flex items-start gap-3">
                              {/* 16:9 Thumbnail for raw video */}
                              <div className="w-[132px] aspect-video shrink-0 bg-black rounded-lg overflow-hidden border border-border relative flex items-center justify-center">
                                {hasVideo ? (
                                  <video
                                    src={segment.videoUrl!.startsWith('http') ? segment.videoUrl : apiUrl(segment.videoUrl!)}
                                    controls
                                    playsInline
                                    preload="metadata"
                                    className="w-full h-full object-contain"
                                  />
                                ) : segment.youtubeVideoId ? (
                                  <iframe
                                    src={`https://www.youtube-nocookie.com/embed/${segment.youtubeVideoId}`}
                                    className="w-full h-full border-0"
                                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                                    allowFullScreen
                                    title={segment.filename}
                                  />
                                ) : (
                                  <div className="w-full h-full flex flex-col items-center justify-center bg-surface-300 p-2 text-center">
                                    <Video size={16} className="text-text-muted mb-0.5" />
                                    <span className="text-[10px] font-mono tabular-nums text-text-muted">
                                      FHD 1080p
                                    </span>
                                  </div>
                                )}
                                <span className="absolute bottom-1 right-1 px-1 py-0.5 rounded bg-black/80 text-[10px] font-mono tabular-nums font-semibold text-white pointer-events-none">
                                  {formatDuration(segment.durationSeconds)}
                                </span>
                              </div>

                              <div className="flex-1 min-w-0 flex flex-col justify-between self-stretch">
                                <div>
                                  <div className="flex items-center gap-1.5">
                                    <span className="px-1.5 py-0.5 rounded-lg bg-surface-300 border border-border text-xs font-mono text-text-muted">
                                      #{segment.sequenceNumber || idx + 1}
                                    </span>
                                    <span className="text-xs font-mono font-medium text-text-main truncate">
                                      {segment.filename}
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-1.5 mt-1 text-xs font-mono tabular-nums text-text-muted">
                                    <span>{formatTimeAgo(segment.createdAt)}</span>
                                    <span>·</span>
                                    <span>{formatBytes(segment.fileSizeBytes)}</span>
                                  </div>
                                </div>

                                <div className="flex items-center gap-2 mt-2 flex-wrap">
                                  {hasVideo && (
                                    <a
                                      href={segment.videoUrl!.startsWith('http') ? segment.videoUrl : apiUrl(segment.videoUrl!)}
                                      download={segment.filename}
                                      className="flex items-center gap-1 px-2.5 py-1.5 min-h-[44px] rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-xs font-medium text-text-secondary hover:text-text-main transition-all active:scale-[0.98]"
                                      title="Download Original Raw Video"
                                    >
                                      <Download size={13} />
                                      <span>MP4</span>
                                    </a>
                                  )}
                                  {segment.youtubeVideoId && (
                                    <a
                                      href={`https://youtu.be/${segment.youtubeVideoId}`}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="flex items-center gap-1 px-2.5 py-1.5 min-h-[44px] rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-xs font-medium text-text-secondary hover:text-text-main transition-all active:scale-[0.98]"
                                    >
                                      <ExternalLink size={13} />
                                      <span>YouTube</span>
                                    </a>
                                  )}
                                  {segment.status === 'FAILED_RETRY' && (
                                    <button
                                      type="button"
                                      onClick={() => onRetryUpload(segment.segmentId)}
                                      className="flex items-center gap-1 px-2.5 py-1.5 min-h-[44px] rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-xs font-medium text-status-warning hover:opacity-85 active:scale-[0.98] transition-all"
                                    >
                                      <RotateCw size={12} />
                                      <span>Retry</span>
                                    </button>
                                  )}
                                  <button
                                    type="button"
                                    onClick={() => toggleExpandDetail(segment.segmentId)}
                                    className="min-h-[44px] px-2 py-1.5 rounded-xl text-text-muted hover:text-text-main flex items-center gap-1 text-xs transition-all active:scale-[0.98]"
                                  >
                                    <span>{isDetailExpanded ? 'Hide' : 'Info'}</span>
                                    {isDetailExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                                  </button>
                                </div>
                              </div>
                            </div>

                            {/* TECHNICAL DETAILS DRAWER */}
                            {isDetailExpanded && (
                              <div className="p-2.5 rounded-lg bg-surface-200 border border-border text-xs font-mono tabular-nums space-y-1 text-text-muted">
                                <div className="flex justify-between">
                                  <span>Segment ID:</span>
                                  <span className="text-text-main font-semibold">{segment.segmentId}</span>
                                </div>
                                <div className="flex justify-between">
                                  <span>Session ID:</span>
                                  <span className="text-text-main font-semibold">{segment.sessionId}</span>
                                </div>
                                <div className="flex justify-between truncate">
                                  <span>SHA256:</span>
                                  <span className="text-text-secondary truncate max-w-[200px]">
                                    {segment.sha256Hash || 'verified'}
                                  </span>
                                </div>
                                <div className="text-text-muted break-all pt-1 border-t border-border/40">
                                  Path: {segment.filepath}
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}

      </div>

      {/* 4. FULLSCREEN RAW SOURCE VIDEO MODAL */}
      {modalVideo && (
        <div 
          onClick={() => setModalVideo(null)}
          className="fixed inset-0 z-[100] bg-black/95 backdrop-blur-md flex flex-col items-center justify-center p-4 animate-in fade-in"
        >
          <div 
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-3xl bg-surface-100 border border-border rounded-2xl overflow-hidden shadow-2xl"
          >
            <div className="flex items-center justify-between p-3.5 border-b border-border">
              <span className="text-sm font-semibold text-text-main truncate pr-3">
                {modalVideo.title}
              </span>
              <button 
                type="button"
                onClick={() => setModalVideo(null)}
                className="w-11 h-11 flex items-center justify-center rounded-lg text-text-muted hover:text-text-main hover:bg-surface-200 transition-colors duration-150 active:scale-[0.98]"
              >
                <X size={18} />
              </button>
            </div>
            <div className="aspect-video bg-black flex items-center justify-center">
              <video 
                src={modalVideo.url} 
                controls 
                autoPlay 
                className="w-full h-full object-contain"
              />
            </div>
          </div>
        </div>
      )}

    </div>
  );
};
