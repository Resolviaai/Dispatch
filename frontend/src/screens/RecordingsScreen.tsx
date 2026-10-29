import React, { useState, useEffect, useMemo } from 'react';
import {
  Film,
  RotateCw,
  ExternalLink,
  ChevronLeft,
  AlertCircle,
  X,
  Sparkles,
  Play,
  Youtube,
  CheckCircle2,
  Copy,
  Trash2,
  Camera,
  MoreVertical,
  Check
} from 'lucide-react';
import { Bridge, RecordingItem, apiUrl, getAuthHeaders } from '../bridge';
import { WORKER_BASE } from '../accounts/accountsService';
import { currentSession } from '../auth/supabase';

interface YouTubeInboxItem {
  video_id: string;
  session_id?: string;
  dispatch_id?: string;
  title?: string;
  status: string;
  last_error?: string | null;
  transcript_source?: string | null;
  discovered_at?: string;
  duration?: number;
}

export interface SessionGroup {
  sessionId: string;
  createdAt: number;
  totalDuration: number;
  totalBytes: number;
  segments: RecordingItem[];
  clipCount: number;
  approvedClipCount: number;
  /**
   * Raw native segment status string (FAILed_*, UPLOADING, PROCESSING, ...).
   * Drives per-session badges; session-level label is derived from this.
   */
  status: string;
  youtubeVideoId?: string;
}

export interface UnifiedSession {
  id: string;
  title: string;
  origin: 'phone' | 'youtube';
  createdAt: number;
  totalDuration: number;
  totalBytes: number;
  status: 'ready' | 'processing' | 'queued' | 'failed' | 'uploading';
  /** Raw native status behind a 'failed' session, e.g. FAILED_YOUTUBE_PROCESSING. */
  failureStatus?: string | undefined;
  clipCount: number;
  youtubeVideoId?: string;
  videoUrl?: string;
  thumbnailUrl?: string;
  errorMessage?: string | null;
  segmentsCount?: number;
  rawNative?: SessionGroup;
  rawInbox?: YouTubeInboxItem;
}

type ViewMode = 'library' | 'detail';
type FilterStatus = 'all' | 'ready' | 'processing' | 'queued' | 'failed';

interface RecordingsScreenProps {
  recordings: RecordingItem[];
  isYouTubeConnected: boolean;
  onNavigateToSettings: () => void;
  onRetryUpload: (segmentId: string) => void;
  onNavigateToClips?: (sessionId?: string) => void;
  onNavigateToRecord?: () => void;
  onDeleteSession?: (sessionId: string) => void;
  pcHost?: string;
}

export const RecordingsScreen: React.FC<RecordingsScreenProps> = ({
  recordings,
  isYouTubeConnected,
  onNavigateToSettings,
  onRetryUpload,
  onNavigateToClips,
  onNavigateToRecord,
  onDeleteSession,
  pcHost
}) => {
  // Navigation & View state
  const [viewMode, setViewMode] = useState<ViewMode>('library');
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterStatus>('all');
  const [isOptionsModalOpen, setIsOptionsModalOpen] = useState(false);
  const [showConfirmDelete, setShowConfirmDelete] = useState<UnifiedSession | null>(null);
  const [videoPlaybackError, setVideoPlaybackError] = useState<string | null>(null);

  useEffect(() => {
    setVideoPlaybackError(null);
  }, [selectedSessionId]);

  // Connection status (LAN vs Cloud Relay)
  const [pcStatus, setPcStatus] = useState<{
    online: boolean;
    checking: boolean;
    isCloudMode: boolean;
  }>({ online: false, checking: false, isCloudMode: false });

  // YouTube Ingest URL State
  const [importUrl, setImportUrl] = useState('');
  const [importState, setImportState] = useState<'idle' | 'submitting' | 'queued' | 'error'>('idle');
  const [importError, setImportError] = useState<string | null>(null);

  // Clips & Inbox items
  const [clips, setClips] = useState<{ id: string; session_id?: string; chunk_id?: string; status: string; thumbnail_url?: string }[]>([]);
  const [inboxItems, setInboxItems] = useState<YouTubeInboxItem[]>([]);
  const [toastMessage, setToastMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const showToast = (msg: string, type: 'success' | 'error' = 'success') => {
    setToastMessage({ text: msg, type });
    setTimeout(() => setToastMessage(null), 3000);
  };

  // Helper: check PC Studio and Cloud connectivity
  const checkPcStudio = async () => {
    setPcStatus(prev => ({ ...prev, checking: true }));
    let lanOnline = false;

    if (pcHost) {
      try {
        const res = await fetch(apiUrl('/api/status', pcHost), {
          signal: AbortSignal.timeout(2000),
          headers: getAuthHeaders()
        });
        // Any HTTP response proves the PC is reachable; a 401/403 for callers
        // without a pairing token is still "online" (trust model changed with
        // the LAN-trust flag on the PC; do not gate reachability on res.ok).
        lanOnline = true;
      } catch {
        lanOnline = false;
      }
    }

    if (lanOnline) {
      setPcStatus({ online: true, checking: false, isCloudMode: false });
    } else {
      const sess = await currentSession().catch(() => null);
      setPcStatus({ online: false, checking: false, isCloudMode: !!sess?.access_token });
    }
  };

  useEffect(() => {
    checkPcStudio();
    const interval = setInterval(checkPcStudio, 8000);
    return () => clearInterval(interval);
  }, [pcHost]);

  // Fetch Clips to enrich session items with real clip counts
  const fetchClips = async () => {
    try {
      if (pcHost && pcStatus.online) {
        const res = await fetch(apiUrl('/api/clips', pcHost), { headers: getAuthHeaders() });
        if (res.ok) {
          const data = await res.json();
          const ready = Array.isArray(data.ready) ? data.ready : [];
          const approved = Array.isArray(data.recent_approved) ? data.recent_approved : [];
          setClips([...ready, ...approved]);
          return;
        }
      }
      // Cloud fallback (for cellular / remote)
      const sess = await currentSession().catch(() => null);
      if (sess?.access_token) {
        const res = await fetch(`${WORKER_BASE}/clips`, {
          headers: { Authorization: `Bearer ${sess.access_token}` },
          signal: AbortSignal.timeout(4000)
        });
        if (res.ok) {
          const data = await res.json();
          const ready = Array.isArray(data.ready) ? data.ready : [];
          const approved = Array.isArray(data.recent_approved) ? data.recent_approved : [];
          setClips([...ready, ...approved]);
        }
      }
    } catch {
      /* keep cached clips */
    }
  };

  useEffect(() => {
    fetchClips();
    const interval = setInterval(fetchClips, 6000);
    return () => clearInterval(interval);
  }, [pcHost, pcStatus.online]);

  // Fetch YouTube inbox items
  const fetchInbox = async () => {
    try {
      if (pcHost && pcStatus.online) {
        const res = await fetch(apiUrl('/api/youtube/inbox', pcHost), { headers: getAuthHeaders() });
        if (res.ok) {
          const data = await res.json();
          setInboxItems(Array.isArray(data.items) ? data.items : []);
          return;
        }
      }
      // Cloud fallback when PC is offline / remote
      const sess = await currentSession().catch(() => null);
      if (sess?.access_token) {
        const res = await fetch(`${WORKER_BASE}/jobs/status`, {
          headers: { Authorization: `Bearer ${sess.access_token}` },
          signal: AbortSignal.timeout(4000)
        });
        if (res.ok) {
          const data = await res.json();
          const jobs = Array.isArray(data.jobs) ? data.jobs : [];
          const items: YouTubeInboxItem[] = jobs.map((j: any) => ({
            video_id: j.video_id || (j.youtube_url ? j.youtube_url.split('v=')[1]?.slice(0, 11) : j.id),
            session_id: j.session_id,
            dispatch_id: j.id,
            title: j.youtube_url,
            status: j.status === 'completed' ? 'CLIPS_CREATED' : j.status === 'processing' ? 'PROCESSING' : j.status === 'failed' ? 'FAILED' : 'QUEUED',
            last_error: j.last_error,
            discovered_at: j.created_at,
          }));
          setInboxItems(items);
        }
      }
    } catch {
      /* keep cached items */
    }
  };

  useEffect(() => {
    fetchInbox();
    const interval = setInterval(fetchInbox, 6000);
    return () => clearInterval(interval);
  }, [pcHost, pcStatus.online]);

  // Group native recordings into session groups
  const nativeSessions: SessionGroup[] = useMemo(() => {
    const map = new Map<string, RecordingItem[]>();
    recordings.forEach((r) => {
      const sid = r.sessionId || 'direct';
      if (!map.has(sid)) map.set(sid, []);
      map.get(sid)!.push(r);
    });

    const groups: SessionGroup[] = [];
    map.forEach((items, sid) => {
      items.sort((a, b) => b.createdAt - a.createdAt);
      const totalDur = items.reduce((acc, it) => acc + (it.durationSeconds || 0), 0);
      const totalBytes = items.reduce((acc, it) => acc + (it.fileSizeBytes || 0), 0);
      const ytId = items.find(it => it.youtubeVideoId)?.youtubeVideoId;

      // Holistic session status across all constituent segments
      const statuses = items.map(it => it.status);
      let sessionStatus = 'QUEUED_FOR_UPLOAD';
      if (statuses.some(st => st.startsWith('FAILED_') || st.startsWith('ERROR_'))) {
        sessionStatus = statuses.find(st => st.startsWith('FAILED_') || st.startsWith('ERROR_'))!;
      } else if (statuses.some(st => st === 'UPLOADING')) {
        sessionStatus = 'UPLOADING';
      } else if (statuses.some(st => st === 'PROCESSING' || st === 'UPLOADED_AWAITING_PROCESSING' || st === 'UPLOADED_TO_YOUTUBE' || st === 'PROCESSED_ON_YOUTUBE')) {
        sessionStatus = 'PROCESSING';
      } else if (statuses.every(st => st === 'VERIFIED_BY_LAPTOP')) {
        sessionStatus = 'VERIFIED_BY_LAPTOP';
      } else if (statuses.some(st => st === 'RECORDING')) {
        sessionStatus = 'RECORDING';
      }

      groups.push({
        sessionId: sid,
        createdAt: items[0].createdAt,
        totalDuration: totalDur,
        totalBytes,
        segments: items,
        clipCount: 0,
        approvedClipCount: 0,
        status: sessionStatus,
        youtubeVideoId: ytId ?? undefined
      });
    });

    groups.sort((a, b) => b.createdAt - a.createdAt);
    return groups;
  }, [recordings]);

  // Clean raw titles (strip internal [DISPATCH] prefixes)
  const cleanTitle = (raw: string): string => {
    return raw.replace(/^\[DISPATCH\]\s*[-:–—]?\s*/i, '').trim() || raw;
  };

  const formatDuration = (sec: number) => {
    const s = Math.round(sec);
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return `${m.toString().padStart(2, '0')}:${rem.toString().padStart(2, '0')}`;
  };

  const formatBytes = (bytes: number) => {
    if (!bytes || bytes <= 0) return '0\u00a0B';
    const mb = bytes / 1_000_000;
    if (mb >= 1000) {
      return `${(mb / 1000).toFixed(2)}\u00a0GB`;
    }
    return `${mb.toFixed(1)}\u00a0MB`;
  };

  const formatSessionDate = (timestamp: number) => {
    const date = new Date(timestamp);
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (isToday) return `Today at ${timeStr}`;
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) return `Yesterday at ${timeStr}`;
    return `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })} at ${timeStr}`;
  };

  // UNIFIED SESSIONS MODEL: Merges native phone recordings and YouTube inbox items
  const unifiedSessions: UnifiedSession[] = useMemo(() => {
    const list: UnifiedSession[] = [];
    const seenYtIds = new Set<string>();
    const seenSessionIds = new Set<string>();
    const seenSegmentIds = new Set<string>();

    // 1. Add native sessions from camera
    nativeSessions.forEach(ns => {
      seenSessionIds.add(ns.sessionId);
      if (ns.youtubeVideoId) seenYtIds.add(ns.youtubeVideoId);
      ns.segments.forEach(seg => {
        seenSegmentIds.add(seg.segmentId);
        if (seg.youtubeVideoId) seenYtIds.add(seg.youtubeVideoId);
      });

      const ytId = ns.youtubeVideoId || ns.segments.find(s => s.youtubeVideoId)?.youtubeVideoId;

      const sessClips = clips.filter(c =>
        c.session_id === ns.sessionId ||
        c.chunk_id === ns.sessionId ||
        (ytId && (c.session_id === ytId || c.chunk_id === ytId))
      );
      const clipCount = sessClips.length;

      const allStatuses = ns.segments.map(s => s.status);
      let status: UnifiedSession['status'] = 'queued';
      let failureStatus: string | null = null;
      if (clipCount > 0) {
        status = 'ready';
      } else if (allStatuses.some(st => st.startsWith('FAILED_') || st.startsWith('ERROR_'))) {
        failureStatus = allStatuses.find(st => st.startsWith('FAILED_') || st.startsWith('ERROR_')) || null;
        status = 'failed';
      } else if (allStatuses.some(st => st === 'UPLOADING' || st === 'WAITING_FOR_YOUTUBE')) {
        status = 'uploading';
      } else if (allStatuses.some(st => st === 'PROCESSING' || st === 'UPLOADED_AWAITING_PROCESSING' || st === 'UPLOADED_TO_YOUTUBE' || st === 'PROCESSED_ON_YOUTUBE')) {
        // UPLOADED_AWAITING_PROCESSING previously fell through to 'queued' forever;
        // it is a real processing state and must read as such.
        status = 'processing';
      } else if (allStatuses.every(st => st === 'VERIFIED_BY_LAPTOP')) {
        status = 'ready';
      } else {
        status = 'queued';
      }

      list.push({
        id: ns.sessionId,
        title: formatSessionDate(ns.createdAt),
        origin: 'phone',
        createdAt: ns.createdAt,
        totalDuration: ns.totalDuration,
        totalBytes: ns.totalBytes,
        status,
        failureStatus: failureStatus ?? undefined,
        clipCount,
        youtubeVideoId: ytId ?? undefined,
        videoUrl: ns.segments[0]?.videoUrl,
        // Prefer the phone's own poster: PC-side clips have no thumbnail at all
        // for recordings that have not been processed yet.
        thumbnailUrl: ns.segments[0]?.thumbnailUrl || sessClips[0]?.thumbnail_url || undefined,
        segmentsCount: ns.segments.length,
        rawNative: ns
      });
    });

    // 2. Add imported YouTube sessions from inbox
    inboxItems.forEach(item => {
      if (seenYtIds.has(item.video_id)) return;
      if (item.session_id && seenSessionIds.has(item.session_id)) return;
      if (item.dispatch_id && seenSegmentIds.has(item.dispatch_id)) return;

      const itemClips = clips.filter(c =>
        c.session_id === item.video_id ||
        c.chunk_id === item.video_id
      );
      const clipCount = itemClips.length;

      let status: UnifiedSession['status'] = 'queued';
      if (clipCount > 0 || item.status === 'CLIPS_CREATED' || item.status === 'COMPLETED') {
        status = 'ready';
      } else if (item.status === 'FAILED' || item.status === 'STALLED_YOUTUBE_PROCESSING') {
        status = 'failed';
      } else if (item.status === 'DISCOVERED') {
        status = 'queued';
      } else {
        status = 'processing';
      }

      const displayTitle = item.title ? cleanTitle(item.title) : `YouTube ${item.video_id}`;

      list.push({
        id: item.video_id,
        title: displayTitle,
        origin: 'youtube',
        createdAt: item.discovered_at ? new Date(item.discovered_at).getTime() : Date.now(),
        totalDuration: item.duration || 0,
        totalBytes: 0,
        status,
        clipCount,
        youtubeVideoId: item.video_id,
        errorMessage: item.last_error,
        rawInbox: item
      });
    });

    list.sort((a, b) => b.createdAt - a.createdAt);
    return list;
  }, [nativeSessions, inboxItems, clips]);

  // Active Session Detail item
  const currentSessionDetail = useMemo(() => {
    if (!selectedSessionId) return null;
    return unifiedSessions.find(s => s.id === selectedSessionId) || null;
  }, [unifiedSessions, selectedSessionId]);

  // Tab Filtering (Clean, zero search overhead)
  const filteredSessions = useMemo(() => {
    return unifiedSessions.filter(s => {
      if (filter === 'ready') return s.status === 'ready';
      if (filter === 'processing') return s.status === 'processing';
      if (filter === 'queued') return s.status === 'queued' || s.status === 'uploading';
      if (filter === 'failed') return s.status === 'failed';
      return true;
    });
  }, [unifiedSessions, filter]);

  // Counts for tabs
  const readyCount = unifiedSessions.filter(s => s.status === 'ready').length;
  const processingCount = unifiedSessions.filter(s => s.status === 'processing').length;
  const queuedCount = unifiedSessions.filter(s => s.status === 'queued' || s.status === 'uploading').length;
  const failedCount = unifiedSessions.filter(s => s.status === 'failed').length;

  // YouTube Link Extraction & Submission
  const extractVideoId = (raw: string): string | null => {
    const cleaned = raw.trim();
    if (/^[a-zA-Z0-9_-]{11}$/.test(cleaned)) return cleaned;
    const match = cleaned.match(/(?:v=|\/|youtu\.be\/|embed\/|shorts\/)([a-zA-Z0-9_-]{11})(?:\?|&|$)/);
    return match ? match[1] : null;
  };

  const handleImportSubmit = async (rawOverride?: string) => {
    const value = (rawOverride ?? importUrl).trim();
    const videoId = extractVideoId(value);
    if (!videoId) {
      setImportState('error');
      setImportError('Enter a valid YouTube video link or ID.');
      return;
    }
    setImportState('submitting');
    setImportError(null);

    try {
      const sess = await currentSession().catch(() => null);
      let res: Response;
      if (sess?.access_token) {
        res = await fetch(`${WORKER_BASE}/jobs/submit`, {
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

      if (res.ok) {
        setImportState('queued');
        setImportUrl('');
        showToast('Video added! Your PC will create clips.');
        fetchInbox();
      } else {
        const body = await res.json().catch(() => ({}));
        setImportState('error');
        setImportError(body.error || `Failed to add video (${res.status})`);
      }
    } catch (e: any) {
      setImportState('error');
      setImportError(e.message || 'Network error.');
    } finally {
      setTimeout(() => setImportState('idle'), 3000);
    }
  };

  const handleCopy = (text: string, label: string) => {
    navigator.clipboard?.writeText(text);
    showToast(`${label} copied`);
  };

  const handleReprocessSession = (session: UnifiedSession) => {
    if (session.origin === 'phone' && !session.youtubeVideoId) {
      // Nothing exists on YouTube yet, so "reprocess" must retry the recording upload.
      onRetryUpload(session.id);
      showToast('Upload retry queued. Processing follows automatically.');
      return;
    }
    if (session.youtubeVideoId) {
      handleImportSubmit(`https://youtu.be/${session.youtubeVideoId}`);
      return;
    }
    showToast('This session has no uploaded source to re-process.', 'error');
  };

  const countPendingUploads = (session: UnifiedSession): number => {
    if (session.origin !== 'phone') return 0;
    const pending = new Set([
      'QUEUED_FOR_UPLOAD',
      'UPLOADING',
      'WAITING_FOR_YOUTUBE',
      'FAILED_RETRY',
      'FAILED_YOUTUBE_PROCESSING',
    ]);
    return session.rawNative?.segments.filter(segment => pending.has(segment.status)).length ?? 0;
  };

  // =========================================================================
  // VIEW: SESSION DETAILS PAGE (Full Screen workstation)
  // =========================================================================
  const renderDetailView = () => {
    if (!currentSessionDetail) return null;
    const s = currentSessionDetail;
    const ytId = s.youtubeVideoId;
    const isReady = s.status === 'ready';
    const isProcessing = s.status === 'processing';
    const isFailed = s.status === 'failed';

    return (
      <div className="w-full h-full flex flex-col bg-canvas overflow-hidden">
        {/* TOP BAR — 56px, ghost circular buttons */}
        <header className="shrink-0 h-14 px-2 border-b border-border bg-canvas z-20 flex items-center justify-between gap-2">
          <div className="flex items-center gap-1 min-w-0">
            <button
              type="button"
              onClick={() => {
                setViewMode('library');
                setSelectedSessionId(null);
              }}
              className="w-11 h-11 rounded-full flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
              title="Back"
              aria-label="Back"
            >
              <ChevronLeft size={20} />
            </button>
            <div className="min-w-0">
              <h2 className="text-base font-semibold tracking-tight text-text-main truncate leading-tight">
                {s.title}
              </h2>
              <div className="text-xs text-text-muted truncate tabular-nums flex items-center gap-1.5">
                <span>{s.origin === 'phone' ? 'Camera Recording' : 'YouTube Video'}</span>
                <span>&middot;</span>
                <span className="font-mono">#{s.id.slice(0, 8)}</span>
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setIsOptionsModalOpen(true)}
            className="w-11 h-11 rounded-full flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
            title="Options"
            aria-label="Options"
          >
            <MoreVertical size={18} />
          </button>
        </header>

        {/* DETAIL MAIN VIEWPORT */}
        <main className="flex-1 min-h-0 flex flex-col px-4 pt-3 pb-4 gap-4 overflow-y-auto">
          {/* VIDEO / MEDIA STAGE */}
          <div className="w-full aspect-video rounded-xl bg-black overflow-hidden border border-border relative flex items-center justify-center shrink-0 shadow-sm">
            {s.videoUrl && !videoPlaybackError ? (
              <video
                src={s.videoUrl.startsWith('http') ? s.videoUrl : apiUrl(s.videoUrl, pcHost)}
                // Without a poster the stage stays blank until the user presses play,
                // because a <video> will not paint a frame from the local interceptor.
                poster={s.thumbnailUrl
                  ? (s.thumbnailUrl.startsWith('http')
                    ? s.thumbnailUrl
                    : apiUrl(s.thumbnailUrl, pcHost))
                  : undefined}
                controls
                playsInline
                onError={(e) => {
                  // A missing MediaError.code means the resource never loaded at all
                  // (interceptor/network level) rather than failing to decode, so the
                  // element state is the only thing that distinguishes the two.
                  const el = e.currentTarget as HTMLVideoElement;
                  const code = el.error?.code ?? 'none';
                  setVideoPlaybackError(
                    `Media error ${code} · readyState ${el.readyState} · networkState ${el.networkState}. `
                    + 'The file is still on this device.'
                  );
                }}
                className="w-full h-full object-contain"
                preload="metadata"
              />
            ) : videoPlaybackError ? (
              // Previously a playback error fell through to the "Waiting for PC"
              // placeholder, which blamed the PC for a local player failure.
              <div className="text-center p-6 text-text-muted space-y-2">
                <AlertCircle size={28} className="mx-auto text-danger" />
                <p className="text-xs font-semibold text-danger">Playback failed</p>
                <p className="text-[11px] leading-relaxed">{videoPlaybackError}</p>
                <div className="flex items-center justify-center gap-2 pt-1">
                  <button
                    type="button"
                    onClick={() => Bridge.openNativePreview(s.id)}
                    className="min-h-[38px] px-3.5 rounded-lg bg-primary text-white text-xs font-semibold flex items-center gap-1.5 active:scale-[0.98] transition-all"
                  >
                    <Play size={12} className="fill-white" />
                    <span>Play in Phone Player</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setVideoPlaybackError(null)}
                    className="min-h-[38px] px-3 rounded-lg bg-surface-2 border border-border text-text-main text-xs font-semibold active:scale-[0.98] transition-all"
                  >
                    Try again
                  </button>
                </div>
              </div>
            ) : ytId ? (
              <div className="w-full h-full relative group">
                <img
                  src={`https://img.youtube.com/vi/${ytId}/hqdefault.jpg`}
                  alt="Video thumbnail"
                  className="w-full h-full object-cover"
                />
                <a
                  href={`https://youtu.be/${ytId}`}
                  target="_blank"
                  rel="noreferrer"
                  className="absolute inset-0 bg-black/40 hover:bg-black/50 transition-colors flex flex-col items-center justify-center text-white gap-2"
                >
                  <div className="w-12 h-12 rounded-full bg-danger flex items-center justify-center shadow-lg group-hover:scale-105 active:scale-95 transition-transform">
                    <Play size={20} className="fill-white translate-x-0.5" />
                  </div>
                  <span className="text-xs font-semibold flex items-center gap-1 bg-black/70 px-2.5 py-1 rounded-full">
                    <span>Watch on YouTube</span>
                    <ExternalLink size={11} />
                  </span>
                </a>
              </div>
            ) : (
              <div className="text-center p-6 text-text-muted space-y-2">
                <div className="w-12 h-12 rounded-full bg-surface-2 flex items-center justify-center mx-auto text-text-secondary">
                  <Film size={22} />
                </div>
                <p className="text-xs font-medium text-text-secondary">
                  {isProcessing ? 'Making clips on PC…' : 'Waiting for PC'}
                </p>
                <p className="text-[11px] text-text-muted max-w-[260px] mx-auto">
                  Your PC will automatically create vertical clips from this video.
                </p>
              </div>
            )}
            {s.origin === 'phone' && (
              <button
                type="button"
                onClick={() => Bridge.openNativePreview(s.id)}
                className="absolute top-2 right-2 px-2.5 py-1 rounded-lg bg-black/80 hover:bg-black text-white text-[11px] font-medium flex items-center gap-1.5 border border-white/15 active:scale-95 transition-all shadow-md z-10"
                title="Play in Phone Player"
              >
                <Play size={10} className="fill-white" />
                <span>Native Player</span>
              </button>
            )}
          </div>

          {/* FAILED STATUS BANNER WITH DIRECT RETRY */}
          {s.status === 'failed' && (
            <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs space-y-2">
              <div className="flex items-center gap-2 font-semibold text-danger">
                <AlertCircle size={15} />
                <span>{s.failureStatus === 'FAILED_YOUTUBE_PROCESSING' ? 'YouTube Processing Failed' : 'Processing Issue'}</span>
              </div>
              <p className="text-[11px] text-text-secondary leading-relaxed">
                {s.errorMessage || (s.failureStatus === 'FAILED_YOUTUBE_PROCESSING'
                  ? 'The upload reached your channel, but YouTube could not post-process the media (unsupported codec or corrupt file). Retrying re-sends it from the phone.'
                  : `This video could not be processed${s.failureStatus ? ` (${s.failureStatus})` : ''}. Tap below to retry.`)}
              </p>
              <button
                type="button"
                onClick={() => {
                  if (s.origin === 'phone') {
                    onRetryUpload(s.id);
                    showToast('Retrying upload to YouTube…');
                  } else if (s.youtubeVideoId) {
                    handleImportSubmit(`https://youtu.be/${s.youtubeVideoId}`);
                  }
                }}
                className="min-h-[38px] px-3.5 rounded-lg bg-danger/20 hover:bg-danger/30 text-danger text-xs font-semibold flex items-center gap-1.5 active:scale-[0.98] transition-all"
              >
                <RotateCw size={13} />
                <span>Retry Video</span>
              </button>
            </div>
          )}

          {/* COMPACT METRICS ROW */}
          <div className="flex items-center justify-between p-3 rounded-xl bg-surface-2 border border-border text-xs">
            <div className="space-y-0.5">
              <span className="text-[10px] uppercase font-semibold text-text-muted block">Duration</span>
              <span className="font-mono font-semibold tabular-nums text-text-main">
                {formatDuration(s.totalDuration)}
              </span>
            </div>

            <div className="h-6 w-px bg-border" />

            <div className="space-y-0.5 text-center">
              <span className="text-[10px] uppercase font-semibold text-text-muted block">Storage</span>
              <span className="font-mono font-semibold tabular-nums text-text-main">
                {s.totalBytes > 0 ? formatBytes(s.totalBytes) : 'YouTube'}
              </span>
            </div>

            <div className="h-6 w-px bg-border" />

            <div className="space-y-0.5 text-right">
              <span className="text-[10px] uppercase font-semibold text-text-muted block">Status</span>
              <span className={`text-xs font-semibold tabular-nums ${
                isReady ? 'text-status-success'
                  : isProcessing ? 'text-status-warning'
                  : isFailed ? 'text-danger'
                  : 'text-text-secondary'
              }`}>
                {/* The old ternary had no failed branch, so a failed upload still
                    read "Queued" while the banner above said it had failed. The raw
                    FAILED_* code is shown because the app carries no other reason. */}
                {isReady
                  ? `Ready (${s.clipCount} clips)`
                  : isProcessing
                    ? 'Processing'
                    : isFailed
                      ? `Failed${s.failureStatus ? ` · ${s.failureStatus}` : ''}`
                      : s.status === 'uploading'
                        ? 'Uploading'
                        : 'Queued'}
              </span>
            </div>
          </div>

          {/* PRIMARY HERO CTA & WORKSTATION ACTIONS */}
          <div className="space-y-2.5 pt-1">
            {isReady ? (
              <button
                type="button"
                onClick={() => {
                  const sid = s.id;
                  setViewMode('library');
                  setSelectedSessionId(null);
                  if (onNavigateToClips) {
                    onNavigateToClips(sid);
                  }
                }}
                className="w-full min-h-[46px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 shadow-hero-glow active:scale-[0.98] transition-all"
              >
                <Sparkles size={16} />
                <span>View {s.clipCount}&nbsp;{s.clipCount === 1 ? 'Clip' : 'Clips'}</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  checkPcStudio();
                  fetchInbox();
                  fetchClips();
                  showToast('Checking PC status…');
                }}
                className="w-full min-h-[44px] rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-text-main text-xs font-semibold flex items-center justify-center gap-2 active:scale-[0.98] transition-all"
              >
                <RotateCw size={14} className={pcStatus.checking ? 'animate-spin' : ''} />
                <span>Check PC Status</span>
              </button>
            )}

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  const text = s.youtubeVideoId ? `https://youtu.be/${s.youtubeVideoId}` : s.id;
                  handleCopy(text, 'Link');
                }}
                className="flex-1 min-h-[44px] px-3 rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-xs font-medium text-text-main flex items-center justify-center gap-1.5 active:scale-[0.98] transition-all"
              >
                <Copy size={13} />
                <span>Copy Link</span>
              </button>

              <button
                type="button"
                onClick={() => handleReprocessSession(s)}
                className="flex-1 min-h-[44px] px-3 rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-xs font-medium text-text-main flex items-center justify-center gap-1.5 active:scale-[0.98] transition-all"
              >
                <RotateCw size={13} />
                <span>{s.origin === 'phone' && !s.youtubeVideoId ? 'Retry Upload' : 'Re-process'}</span>
              </button>

              <button
                type="button"
                onClick={() => setShowConfirmDelete(s)}
                className="min-h-[44px] px-3.5 rounded-xl bg-surface-2 hover:bg-danger/20 hover:text-danger border border-border text-xs font-medium text-text-secondary flex items-center justify-center gap-1.5 active:scale-[0.98] transition-all"
                title="Delete Session"
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        </main>
      </div>
    );
  };

  const handleDeleteSession = async (session?: UnifiedSession | null) => {
    const target = session || showConfirmDelete || currentSessionDetail;
    if (!target) {
      setShowConfirmDelete(null);
      return;
    }
    try {
      if (target.origin === 'phone') {
        const ok = Bridge.deleteSession(target.id);
        if (!ok) {
          showToast(
            target.status === 'uploading'
              ? 'This recording is uploading right now. Try deleting it again after the upload stops.'
              : 'Could not delete the local recording. Retry in a moment.',
            'error'
          );
          return;
        }
      }
      if (pcHost) {
        try {
          const res = await fetch(apiUrl(`/api/youtube/inbox/${target.id}`, pcHost), {
            method: 'DELETE',
            headers: getAuthHeaders()
          });
          if (!res.ok && target.origin === 'youtube') {
            showToast('Could not delete session from PC.', 'error');
            return;
          }
        } catch (e) {
          if (target.origin === 'youtube') {
            showToast('Could not reach PC to delete session.', 'error');
            return;
          }
        }
      }
      setInboxItems(prev => prev.filter(i => i.video_id !== target.id && (target.rawInbox ? i.video_id !== target.rawInbox.video_id : true)));
      if (onDeleteSession) {
        onDeleteSession(target.id);
      }
      showToast('Session deleted.', 'success');
    } catch (err) {
      console.error('Delete session error:', err);
      showToast('Could not delete session.', 'error');
    } finally {
      setShowConfirmDelete(null);
      if (selectedSessionId === target.id) {
        setViewMode('library');
        setSelectedSessionId(null);
      }
    }
  };

  // =========================================================================
  // VIEW: SESSIONS LIBRARY (Minimal, No Separator Lines, Clean Order)
  // =========================================================================
  const renderLibraryView = () => (
    <div className="w-full h-full flex flex-col bg-canvas overflow-hidden">
      {/* 1. HEADER — title + metadata, refresh button */}
      <header className="shrink-0 px-4 pt-4 pb-2 bg-canvas z-20">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-lg font-semibold tracking-tight text-text-main leading-tight">
                Sessions
              </h1>
              {pcStatus.online ? (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 text-[11px] font-semibold tracking-wide">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                  PC Online
                </span>
              ) : pcStatus.isCloudMode ? (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-primary/15 border border-primary/30 text-primary text-[11px] font-medium">
                  <span className="w-2 h-2 rounded-full bg-primary" />
                  Cloud Active
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-surface-2 border border-border text-text-muted text-[11px]">
                  <span className="w-1.5 h-1.5 rounded-full bg-text-muted" />
                  PC Offline
                </span>
              )}
            </div>
            <p className="text-xs text-text-muted tabular-nums truncate flex items-center gap-1.5">
              <span>{readyCount}&nbsp;ready</span>
              <span>&middot;</span>
              <span>{unifiedSessions.length}&nbsp;total</span>
            </p>
          </div>

          <button
            type="button"
            onClick={() => {
              checkPcStudio();
              fetchClips();
              fetchInbox();
            }}
            disabled={pcStatus.checking}
            aria-label="Refresh sessions"
            className="w-11 h-11 rounded-full flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all -mr-2"
          >
            <RotateCw size={18} className={pcStatus.checking ? 'animate-spin' : ''} />
          </button>
        </div>
      </header>

      {/* 2. YOUTUBE URL PASTE BOX (Above the filter chips, simple plain English) */}
      <div className="shrink-0 px-4 pt-1 pb-2 bg-canvas">
        <div className="flex items-center gap-2 p-1.5 pl-3 rounded-xl bg-surface-2 border border-border focus-within:border-primary transition-all">
          <Youtube size={18} className="text-danger shrink-0" />
          <input
            type="url"
            value={importUrl}
            onChange={e => setImportUrl(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') handleImportSubmit();
            }}
            placeholder="Paste YouTube link…"
            className="flex-1 bg-transparent text-xs text-text-main placeholder:text-text-muted outline-none min-w-0"
          />
          <button
            type="button"
            onClick={() => handleImportSubmit()}
            disabled={!importUrl.trim() || importState === 'submitting'}
            className="min-h-[36px] px-4 rounded-lg bg-primary hover:bg-primary-hover text-white text-xs font-semibold active:scale-[0.98] transition-all shrink-0 flex items-center gap-1 shadow-sm disabled:opacity-40"
          >
            {importState === 'submitting' && (
              <RotateCw size={13} className="animate-spin" />
            )}
            <span>Add</span>
          </button>
        </div>
        {importError && (
          <p className="mt-1.5 text-xs text-danger flex items-center gap-1">
            <AlertCircle size={12} />
            <span>{importError}</span>
          </p>
        )}
      </div>

      {/* 3. FILTER CHIPS (Positioned cleanly BELOW the YouTube URL box) */}
      <div className="shrink-0 px-4 pb-3 bg-canvas">
        <div className="flex items-center gap-2 overflow-x-auto no-scrollbar">
          {[
            { id: 'all' as FilterStatus, label: 'All', count: unifiedSessions.length },
            { id: 'ready' as FilterStatus, label: 'Ready', count: readyCount },
            { id: 'processing' as FilterStatus, label: 'Processing', count: processingCount },
            { id: 'queued' as FilterStatus, label: 'Queued', count: queuedCount },
            ...(failedCount > 0 ? [{ id: 'failed' as FilterStatus, label: 'Failed', count: failedCount }] : []),
          ].map(chip => {
            const isSelected = filter === chip.id;
            return (
              <button
                key={chip.id}
                type="button"
                onClick={() => setFilter(chip.id)}
                aria-pressed={isSelected}
                className={`h-8 shrink-0 px-3 rounded-lg text-[13px] font-medium flex items-center gap-1.5 active:scale-[0.98] transition-all ${
                  isSelected
                    ? 'chip-selected'
                    : 'bg-surface-2 text-text-secondary hover:text-text-main'
                }`}
              >
                <span>{chip.label}</span>
                <span className="tabular-nums text-xs opacity-70">{chip.count}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* TOAST NOTIFICATION STRIP */}
      {toastMessage && (
        <div className={`shrink-0 px-4 py-2 border-b flex items-center justify-between gap-3 animate-in fade-in ${
          toastMessage.type === 'error'
            ? 'bg-rose-500/10 border-rose-500/20 text-rose-300'
            : 'bg-surface-2 border-border text-text-main'
        }`}>
          <span className="text-xs truncate flex items-center gap-1.5">
            {toastMessage.type === 'error' ? (
              <AlertCircle size={13} className="text-danger shrink-0" />
            ) : (
              <CheckCircle2 size={13} className="text-status-success shrink-0" />
            )}
            <span>{toastMessage.text}</span>
          </span>
          <button
            type="button"
            onClick={() => setToastMessage(null)}
            className="text-text-muted hover:text-text-main"
          >
            <X size={13} />
          </button>
        </div>
      )}

      {/* 4. SESSIONS LIST — completely free of separator lines */}
      <main className="flex-1 min-h-0 overflow-y-auto px-4 pb-24 space-y-1">
        {filteredSessions.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center p-6 text-center select-none my-12">
            <div className="w-16 h-16 rounded-full bg-surface-2 flex items-center justify-center mb-4 text-text-muted">
              <Film size={24} />
            </div>
            <h2 className="text-base font-semibold tracking-tight text-text-main">
              No sessions found
            </h2>
            <p className="text-sm text-text-secondary mt-1.5 max-w-[280px]">
              Record with your camera or paste a YouTube link above to create clips.
            </p>
            {onNavigateToRecord && (
              <button
                type="button"
                onClick={onNavigateToRecord}
                className="mt-5 min-h-[44px] px-5 rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold active:scale-[0.98] transition-all flex items-center gap-2 shadow-hero-glow"
              >
                <Camera size={15} />
                <span>Start Recording</span>
              </button>
            )}
          </div>
        ) : (
          filteredSessions.map(session => {
            const ytId = session.youtubeVideoId;
            const isReady = session.status === 'ready';
            const isProcessing = session.status === 'processing';

            return (
              <div
                key={session.id}
                onClick={() => {
                  setSelectedSessionId(session.id);
                  setViewMode('detail');
                }}
                className="group w-full py-2.5 flex items-start gap-3 cursor-pointer hover:bg-surface-2/40 active:bg-surface-2 transition-colors rounded-xl px-1 select-none"
              >
                {/* 16:9 Thumbnail Stage */}
                <div className="relative w-[132px] aspect-video rounded-lg bg-black overflow-hidden shrink-0 flex items-center justify-center">
                  <Film size={16} className="text-white/40" />
                  {ytId ? (
                    <img
                      src={`https://img.youtube.com/vi/${ytId}/mqdefault.jpg`}
                      alt=""
                      className="absolute inset-0 w-full h-full object-cover"
                      loading="lazy"
                      onError={e => {
                        (e.target as HTMLElement).style.display = 'none';
                      }}
                    />
                  ) : session.thumbnailUrl ? (
                    <img
                      src={session.thumbnailUrl.startsWith('http') ? session.thumbnailUrl : apiUrl(session.thumbnailUrl, pcHost)}
                      alt=""
                      className="absolute inset-0 w-full h-full object-cover"
                      loading="lazy"
                      onError={e => {
                        (e.target as HTMLElement).style.display = 'none';
                      }}
                    />
                  ) : session.videoUrl ? (
                    <video
                      // A <video preload="metadata"> never paints a frame until it is
                      // asked to seek, so the list showed a blank tile for every local
                      // recording. The #t media fragment makes the WebView decode that
                      // frame and keep it up as the poster.
                      src={`${session.videoUrl.startsWith('http') ? session.videoUrl : apiUrl(session.videoUrl, pcHost)}#t=0.1`}
                      className="absolute inset-0 w-full h-full object-cover"
                      preload="metadata"
                      muted
                      playsInline
                    />
                  ) : null}

                  {/* Origin badge: icon-only so repeated source labels don't clutter every row. */}
                  <span
                    className="absolute top-1 left-1 w-4 h-4 rounded bg-black/85 text-white flex items-center justify-center"
                    title={session.origin === 'phone' ? 'Camera recording' : 'YouTube video'}
                    role="img"
                    aria-label={session.origin === 'phone' ? 'Camera recording' : 'YouTube video'}
                  >
                    {session.origin === 'phone'
                      ? <Camera size={10} />
                      : <Youtube size={10} className="text-danger" />}
                  </span>

                  {/* Duration badge */}
                  <span className="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-black/85 font-mono tabular-nums text-[10px] font-semibold text-white">
                    {formatDuration(session.totalDuration)}
                  </span>
                </div>

                {/* Title + Metadata */}
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-semibold text-text-main leading-snug line-clamp-2">
                    {session.title}
                  </h3>

                  <div className="mt-1 flex items-center gap-1.5 text-xs text-text-muted tabular-nums truncate">
                    <span>{session.totalBytes > 0 ? formatBytes(session.totalBytes) : 'YouTube'}</span>
                    <span>&middot;</span>
                    {isReady ? (
                      <span className="text-status-success font-medium">Ready</span>
                    ) : isProcessing ? (
                      <span className="text-status-warning font-medium">Processing</span>
                    ) : session.status === 'failed' ? (
                      <span className="text-danger font-medium">Failed</span>
                    ) : (
                      <span>Queued</span>
                    )}
                  </div>
                </div>

                {/* Kebab — circular ghost button */}
                <button
                  type="button"
                  onClick={e => {
                    e.stopPropagation();
                    setSelectedSessionId(session.id);
                    setIsOptionsModalOpen(true);
                  }}
                  aria-label="Options"
                  className="-mr-1 w-11 h-11 shrink-0 rounded-full flex items-center justify-center text-text-muted hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
                >
                  <MoreVertical size={18} />
                </button>
              </div>
            );
          })
        )}
      </main>
    </div>
  );

  return (
    <div className="w-full h-full">
      {viewMode === 'library' && renderLibraryView()}
      {viewMode === 'detail' && renderDetailView()}

      {/* OPTIONS MODAL (100% Solid, Opaque Layer 1 Surface) */}
      {isOptionsModalOpen && currentSessionDetail && (
        <div className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
          <div className="w-full max-w-sm rounded-2xl bg-surface border border-border p-4 space-y-2 shadow-2xl animate-in fade-in">
            <div className="flex items-center justify-between pb-2 border-b border-border">
              <h3 className="text-sm font-semibold text-text-main truncate pr-2">
                {currentSessionDetail.title}
              </h3>
              <button
                type="button"
                onClick={() => setIsOptionsModalOpen(false)}
                className="w-8 h-8 rounded-full flex items-center justify-center text-text-muted hover:text-text-main shrink-0"
              >
                <X size={14} />
              </button>
            </div>

            {currentSessionDetail.origin === 'phone' && (
              <button
                type="button"
                onClick={() => {
                  Bridge.openNativePreview(currentSessionDetail.id);
                  setIsOptionsModalOpen(false);
                }}
                className="w-full min-h-[44px] px-3 rounded-xl bg-surface-2 hover:bg-surface-3 text-xs font-medium text-text-main flex items-center gap-2 transition-all active:scale-[0.98]"
              >
                <Play size={14} className="text-primary fill-primary" />
                <span>Play in Native Video Player</span>
              </button>
            )}

            <button
              type="button"
              onClick={() => {
                const text = currentSessionDetail.youtubeVideoId
                  ? `https://youtu.be/${currentSessionDetail.youtubeVideoId}`
                  : currentSessionDetail.id;
                handleCopy(text, 'Link');
                setIsOptionsModalOpen(false);
              }}
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-2 hover:bg-surface-3 text-xs font-medium text-text-main flex items-center gap-2 transition-all active:scale-[0.98]"
            >
              <Copy size={14} />
              <span>Copy Link</span>
            </button>

            {currentSessionDetail.youtubeVideoId && (
              <a
                href={`https://youtu.be/${currentSessionDetail.youtubeVideoId}`}
                target="_blank"
                rel="noreferrer"
                onClick={() => setIsOptionsModalOpen(false)}
                className="w-full min-h-[44px] px-3 rounded-xl bg-surface-2 hover:bg-surface-3 text-xs font-medium text-text-main flex items-center gap-2 transition-all active:scale-[0.98]"
              >
                <ExternalLink size={14} />
                <span>Open on YouTube</span>
              </a>
            )}

            <button
              type="button"
              onClick={() => {
                handleReprocessSession(currentSessionDetail);
                setIsOptionsModalOpen(false);
              }}
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-2 hover:bg-surface-3 text-xs font-medium text-text-main flex items-center gap-2 transition-all active:scale-[0.98]"
            >
              <RotateCw size={14} />
              <span>
                {currentSessionDetail.origin === 'phone' && !currentSessionDetail.youtubeVideoId
                  ? 'Retry Upload'
                  : 'Re-process'}
              </span>
            </button>

            <button
              type="button"
              onClick={() => {
                setShowConfirmDelete(currentSessionDetail);
                setIsOptionsModalOpen(false);
              }}
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-2 hover:bg-danger/15 hover:text-danger text-xs font-medium text-danger flex items-center gap-2 transition-all active:scale-[0.98]"
            >
              <Trash2 size={14} />
              <span>Delete Session</span>
            </button>
          </div>
        </div>
      )}

      {/* CONFIRM DELETE DIALOG (100% Solid, Opaque Layer 1 Surface) */}
      {showConfirmDelete && (
        <div className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
          <div className="w-full max-w-sm rounded-2xl bg-surface border border-border p-5 space-y-3 shadow-2xl animate-in fade-in">
            <div className="flex items-center gap-2 text-danger font-semibold text-sm">
              <AlertCircle size={16} />
              <span>Delete Session?</span>
            </div>
            <p className="text-xs text-text-secondary leading-relaxed">
              Delete <strong className="text-text-main">{showConfirmDelete.title}</strong> from Sessions? Your existing clips will stay saved in the Clips tab.
            </p>
            {countPendingUploads(showConfirmDelete) > 0 && (
              <p className="text-xs leading-relaxed rounded-xl chip-warning border px-3 py-2">
                {showConfirmDelete.status === 'uploading'
                  ? 'This recording is currently uploading. Deleting will be refused until the upload stops.'
                  : 'This recording has not finished uploading. Deleting it will permanently remove the local recording and cancel the pending upload.'}
              </p>
            )}
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowConfirmDelete(null)}
                className="min-h-[44px] px-4 rounded-xl bg-surface-2 border border-border text-xs font-medium text-text-main active:scale-[0.98] hover:bg-surface-3 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => handleDeleteSession(showConfirmDelete)}
                disabled={showConfirmDelete.status === 'uploading'}
                className="min-h-[44px] px-4 rounded-xl bg-danger hover:bg-red-600 text-white text-xs font-semibold active:scale-[0.98] shadow-sm transition-colors disabled:opacity-40"
              >
                {countPendingUploads(showConfirmDelete) > 0 ? 'Delete Anyway' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
