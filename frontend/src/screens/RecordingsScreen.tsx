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
  X,
  Sparkles,
  Clock,
  HardDrive,
  Camera,
  Play,
  Youtube,
  CheckCircle2,
  Search,
  Copy,
  Trash2,
  Laptop,
  Cloud,
  Layers,
  ArrowRight
} from 'lucide-react';
import { RecordingItem, apiUrl } from '../bridge';
import { WORKER_BASE } from '../accounts/accountsService';
import { currentSession } from '../auth/supabase';

interface YouTubeInboxItem {
  video_id: string;
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
  status: 'UPLOADED_TO_YOUTUBE' | 'PROCESSING' | 'QUEUED_FOR_UPLOAD' | 'FAILED_RETRY' | 'UPLOADING';
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
  clipCount: number;
  youtubeVideoId?: string;
  videoUrl?: string;
  errorMessage?: string | null;
  segmentsCount?: number;
  rawNative?: SessionGroup;
  rawInbox?: YouTubeInboxItem;
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
  // Navigation & Filter state
  const [filter, setFilter] = useState<'all' | 'ready' | 'processing' | 'queued'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [activeSessionDetail, setActiveSessionDetail] = useState<UnifiedSession | null>(null);
  const [showConfirmDelete, setShowConfirmDelete] = useState<UnifiedSession | null>(null);

  // PC Studio live connection status
  const [pcStatus, setPcStatus] = useState<{
    online: boolean;
    checking: boolean;
    lastPing?: number;
  }>({ online: false, checking: false });

  // YouTube import state
  const [showImportForm, setShowImportForm] = useState(false);
  const [importUrl, setImportUrl] = useState('');
  const [importState, setImportState] = useState<'idle' | 'submitting' | 'queued' | 'error'>('idle');
  const [importError, setImportError] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);

  // Clips & Inbox items
  const [clips, setClips] = useState<{ id: string; session_id?: string; chunk_id?: string; status: string }[]>([]);
  const [inboxItems, setInboxItems] = useState<YouTubeInboxItem[]>([]);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 4000);
  };

  // Helper: check PC Studio reachability
  const checkPcStudio = async () => {
    if (!pcHost) {
      setPcStatus({ online: false, checking: false });
      return;
    }
    setPcStatus(prev => ({ ...prev, checking: true }));
    try {
      const res = await fetch(apiUrl('/api/status', pcHost), {
        signal: AbortSignal.timeout(2500)
      });
      setPcStatus({ online: res.ok, checking: false, lastPing: Date.now() });
    } catch {
      setPcStatus({ online: false, checking: false, lastPing: Date.now() });
    }
  };

  useEffect(() => {
    checkPcStudio();
    const interval = setInterval(checkPcStudio, 6000);
    return () => clearInterval(interval);
  }, [pcHost]);

  // Fetch Clips to enrich session cards with real clip counts
  useEffect(() => {
    let isMounted = true;
    const fetchClips = async () => {
      try {
        if (pcHost && pcStatus.online) {
          const res = await fetch(apiUrl('/api/clips', pcHost));
          if (res.ok && isMounted) {
            const data = await res.json();
            const ready = Array.isArray(data.ready) ? data.ready : [];
            const approved = Array.isArray(data.recent_approved) ? data.recent_approved : [];
            setClips([...ready, ...approved]);
            return;
          }
        }
        // Cloud fallback
        const sess = await currentSession();
        if (sess?.access_token) {
          const res = await fetch(`${WORKER_BASE}/clips`, {
            headers: { Authorization: `Bearer ${sess.access_token}` }
          });
          if (res.ok && isMounted) {
            const data = await res.json();
            const cloudClips = Array.isArray(data.clips) ? data.clips : [];
            setClips(cloudClips);
          }
        }
      } catch {
        /* keep cached clips */
      }
    };
    fetchClips();
    const interval = setInterval(fetchClips, 5000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [pcHost, pcStatus.online]);

  // Fetch YouTube inbox items
  useEffect(() => {
    let isMounted = true;
    const fetchInbox = async () => {
      try {
        if (pcHost && pcStatus.online) {
          const res = await fetch(apiUrl('/api/youtube/inbox', pcHost));
          if (res.ok && isMounted) {
            const data = await res.json();
            setInboxItems(Array.isArray(data.items) ? data.items : []);
          }
        }
      } catch {
        /* keep cached items */
      }
    };
    fetchInbox();
    const interval = setInterval(fetchInbox, 6000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
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
      const latestStatus = items[0]?.status || 'QUEUED_FOR_UPLOAD';

      let status: SessionGroup['status'] = 'QUEUED_FOR_UPLOAD';
      if (items.some(it => it.status === 'UPLOADED_TO_YOUTUBE' || it.status === 'PROCESSED_ON_YOUTUBE')) {
        status = 'UPLOADED_TO_YOUTUBE';
      } else if (items.some(it => it.status === 'UPLOADING')) {
        status = 'UPLOADING';
      } else if (items.some(it => it.status === 'FAILED_RETRY')) {
        status = 'FAILED_RETRY';
      }

      groups.push({
        sessionId: sid,
        createdAt: items[0]?.createdAt || Date.now(),
        totalDuration: totalDur,
        totalBytes,
        segments: items,
        clipCount: 0,
        approvedClipCount: 0,
        status,
        youtubeVideoId: ytId
      });
    });

    return groups.sort((a, b) => b.createdAt - a.createdAt);
  }, [recordings]);

  // Format Helpers
  const formatDuration = (sec?: number) => {
    if (!sec || sec <= 0) return '00:00';
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

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0\u00a0B';
    const mb = bytes / (1024 * 1024);
    if (mb < 1) return `${Math.round(bytes / 1024)}\u00a0KB`;
    if (mb >= 1024) return `${(mb / 1024).toFixed(2)}\u00a0GB`;
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

  // UNIFIED SESSIONS MODEL: Merges native phone recordings and YouTube inbox imports
  const unifiedSessions: UnifiedSession[] = useMemo(() => {
    const list: UnifiedSession[] = [];
    const seenYtIds = new Set<string>();

    // 1. Add native sessions from camera
    nativeSessions.forEach(ns => {
      const ytId = ns.youtubeVideoId || ns.segments.find(s => s.youtubeVideoId)?.youtubeVideoId;
      if (ytId) seenYtIds.add(ytId);

      const sessClips = clips.filter(c =>
        c.session_id === ns.sessionId ||
        c.chunk_id === ns.sessionId ||
        (ytId && (c.session_id === ytId || c.chunk_id === ytId))
      );
      const clipCount = sessClips.length;

      let status: UnifiedSession['status'] = 'queued';
      if (clipCount > 0) {
        status = 'ready';
      } else if (ns.status === 'PROCESSING' || ns.status === 'UPLOADING') {
        status = 'processing';
      } else if (ns.status === 'FAILED_RETRY') {
        status = 'failed';
      } else if (ns.status === 'UPLOADED_TO_YOUTUBE') {
        status = 'processing'; // waiting for PC to extract clips
      }

      list.push({
        id: ns.sessionId,
        title: formatSessionDate(ns.createdAt),
        origin: 'phone',
        createdAt: ns.createdAt,
        totalDuration: ns.totalDuration,
        totalBytes: ns.totalBytes,
        status,
        clipCount,
        youtubeVideoId: ytId,
        videoUrl: ns.segments[0]?.videoUrl,
        segmentsCount: ns.segments.length,
        rawNative: ns
      });
    });

    // 2. Add imported YouTube sessions from inbox
    inboxItems.forEach(item => {
      if (seenYtIds.has(item.video_id)) return; // prevent duplicate if already represented

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

      list.push({
        id: item.video_id,
        title: item.title || `YouTube ${item.video_id}`,
        origin: 'youtube',
        createdAt: item.discovered_at ? new Date(item.discovered_at).getTime() : Date.now(),
        totalDuration: item.duration || 0,
        totalBytes: 0,
        status,
        clipCount: Math.max(clipCount, status === 'ready' ? 1 : 0),
        youtubeVideoId: item.video_id,
        errorMessage: item.last_error,
        rawInbox: item
      });
    });

    // Sort by newest first
    return list.sort((a, b) => b.createdAt - a.createdAt);
  }, [nativeSessions, inboxItems, clips]);

  // Filter & Search Logic
  const filteredSessions = useMemo(() => {
    return unifiedSessions.filter(s => {
      const matchFilter =
        filter === 'all'
          ? true
          : filter === 'ready'
          ? s.status === 'ready'
          : filter === 'processing'
          ? s.status === 'processing'
          : s.status === 'queued' || s.status === 'failed';

      const q = searchQuery.trim().toLowerCase();
      const matchSearch =
        !q ||
        s.title.toLowerCase().includes(q) ||
        s.id.toLowerCase().includes(q) ||
        (s.youtubeVideoId && s.youtubeVideoId.toLowerCase().includes(q));

      return matchFilter && matchSearch;
    });
  }, [unifiedSessions, filter, searchQuery]);

  // Counts for tabs
  const readyCount = unifiedSessions.filter(s => s.status === 'ready').length;
  const processingCount = unifiedSessions.filter(s => s.status === 'processing').length;
  const queuedCount = unifiedSessions.filter(s => s.status === 'queued' || s.status === 'failed').length;
  const totalFootageDuration = unifiedSessions.reduce((acc, s) => acc + s.totalDuration, 0);
  const totalStorageBytes = unifiedSessions.reduce((acc, s) => acc + s.totalBytes, 0);

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
      setImportNotice(null);
      setImportError('Enter a valid YouTube link (e.g. https://youtu.be/XXXXXXXXXXX) or 11-char ID.');
      return;
    }
    setImportState('submitting');
    setImportError(null);
    setImportNotice(null);

    try {
      const sess = await currentSession();
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
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: value })
        });
      }

      if (res.ok) {
        setImportState('queued');
        setImportUrl('');
        setImportNotice(`YouTube video ${videoId} submitted to PC queue.`);
        setShowImportForm(false);
        showToast('Video submitted! Your PC will fetch and extract clips.');
      } else {
        const body = await res.json().catch(() => ({}));
        setImportState('error');
        setImportError(body.error || `Import failed (${res.status})`);
      }
    } catch (e: any) {
      setImportState('error');
      setImportError(e.message || 'Network error submitting video.');
    }
  };

  const handleCopy = (text: string, label: string) => {
    navigator.clipboard?.writeText(text);
    showToast(`${label} copied to clipboard.`);
  };

  return (
    <div className="w-full h-full flex flex-col bg-canvas text-text-main overflow-hidden select-none">
      {/* 1. SCREEN HEADER */}
      <div className="shrink-0 px-4 pt-4 pb-3 border-b border-border bg-canvas space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Film size={19} className="text-primary shrink-0" />
              <h1 className="text-lg font-semibold tracking-tight text-text-main">Sessions</h1>
              <span className="px-2 py-0.5 rounded-full bg-surface-200 border border-border text-[11px] font-mono font-medium text-text-secondary">
                {unifiedSessions.length} total
              </span>
            </div>
            <p className="text-xs text-text-muted mt-0.5">
              Raw filming sessions and YouTube video ingest queue
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowImportForm(!showImportForm)}
              className="flex items-center gap-1.5 px-3 py-2 min-h-[44px] rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-xs font-medium text-text-main transition-all active:scale-[0.98]"
              title="Import YouTube Video"
            >
              <Youtube size={14} className="text-danger" />
              <span>Import URL</span>
            </button>
          </div>
        </div>

        {/* 2. PC STUDIO ONLINE / CLOUD RELAY BANNER (The rightful home for PC connection status) */}
        <div
          onClick={checkPcStudio}
          className={`p-3 rounded-xl border flex items-center justify-between gap-3 transition-colors cursor-pointer active:scale-[0.99] ${
            pcStatus.online
              ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400'
              : 'bg-surface-200/90 border-border text-text-secondary'
          }`}
          title="Tap to re-check PC Studio connection"
        >
          <div className="flex items-center gap-2.5 min-w-0">
            <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${
              pcStatus.online ? 'bg-emerald-500/20 text-emerald-400' : 'bg-surface-3 text-text-muted'
            }`}>
              {pcStatus.online ? <Laptop size={15} /> : <Cloud size={15} />}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold tracking-tight">
                  {pcStatus.online ? 'PC Studio Online (LAN Linked)' : 'Cloud Relay Mode (PC Standby)'}
                </span>
                <span className={`w-1.5 h-1.5 rounded-full ${pcStatus.online ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
              </div>
              <p className="text-[11px] text-text-muted truncate mt-0.5">
                {pcStatus.online
                  ? `Active at ${pcHost || 'localhost:8000'} — Ready to process raw footage`
                  : 'PC is offline. Sessions stay safely queued on device/cloud and auto-ingest when PC runs.'}
              </p>
            </div>
          </div>
          <button
            type="button"
            className="p-1 text-text-muted hover:text-text-main shrink-0"
            title="Refresh connection"
          >
            <RotateCw size={13} className={pcStatus.checking ? 'animate-spin' : ''} />
          </button>
        </div>

        {/* 3. Operational Metrics Strip */}
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-200 border border-border text-text-secondary">
            <Clock size={12} className="text-text-muted shrink-0" />
            <span className="font-mono tabular-nums">{formatTotalTime(totalFootageDuration)}&nbsp;raw</span>
          </div>

          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-200 border border-border text-text-secondary">
            <HardDrive size={12} className="text-text-muted shrink-0" />
            <span className="font-mono tabular-nums">{formatBytes(totalStorageBytes)}&nbsp;stored</span>
          </div>

          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-200 border border-border text-text-secondary">
            <Layers size={12} className="text-primary shrink-0" />
            <span className="font-mono tabular-nums">{readyCount}&nbsp;ready with clips</span>
          </div>
        </div>

        {/* 4. Search and Filter Chips Bar */}
        <div className="flex flex-col sm:flex-row gap-2 pt-1">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Search sessions by title, date, or ID…"
              className="w-full min-h-[36px] pl-8 pr-3 rounded-lg bg-surface-200 border border-border text-xs text-text-main placeholder:text-text-muted outline-none focus:border-primary"
            />
          </div>

          <div className="flex items-center gap-1.5 shrink-0" role="tablist">
            {[
              { id: 'all', label: `All (${unifiedSessions.length})` },
              { id: 'ready', label: `Clips Ready (${readyCount})` },
              { id: 'processing', label: `Processing (${processingCount})` },
              { id: 'queued', label: `Queued (${queuedCount})` },
            ].map(tab => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setFilter(tab.id as any)}
                className={`min-h-[36px] px-2.5 rounded-lg text-xs font-semibold tabular-nums transition-all active:scale-[0.98] border ${
                  filter === tab.id
                    ? 'bg-primary text-white border-primary shadow-sm'
                    : 'bg-surface-200 border-border text-text-secondary hover:text-text-main'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 5. OPTIONAL POP-DOWN IMPORT FORM */}
      {showImportForm && (
        <div className="mx-4 mt-3 p-3.5 rounded-2xl bg-surface-100 border border-border space-y-2.5 shadow-md">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Youtube size={15} className="text-danger" />
              <h2 className="text-xs font-semibold text-text-main">Import YouTube Link for Ingestion</h2>
            </div>
            <button
              onClick={() => setShowImportForm(false)}
              className="p-1 rounded-md text-text-muted hover:text-text-main"
            >
              <X size={14} />
            </button>
          </div>

          <form
            onSubmit={e => {
              e.preventDefault();
              handleImportSubmit();
            }}
            className="flex items-center gap-2"
          >
            <input
              type="text"
              inputMode="url"
              value={importUrl}
              onChange={e => {
                setImportUrl(e.target.value);
                if (importState === 'error') setImportState('idle');
              }}
              placeholder="https://youtube.com/watch?v=…"
              disabled={importState === 'submitting'}
              className="flex-1 min-w-0 min-h-[44px] px-3 py-2 rounded-lg bg-surface-200 border border-border text-xs font-mono text-text-main placeholder:text-text-muted focus:outline-none focus:border-primary disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={importState === 'submitting' || !importUrl.trim()}
              className="min-h-[44px] px-4 py-2 rounded-lg bg-primary hover:bg-primary-hover text-white text-xs font-semibold flex items-center gap-1.5 shrink-0 active:scale-[0.98] disabled:opacity-40"
            >
              {importState === 'submitting' ? <RotateCw size={13} className="animate-spin" /> : <Download size={13} />}
              <span>Import</span>
            </button>
          </form>

          {importError && (
            <p className="text-xs chip-danger border rounded-lg p-2 flex items-center gap-1.5">
              <AlertCircle size={13} className="shrink-0" />
              <span>{importError}</span>
            </p>
          )}
        </div>
      )}

      {/* TOAST FEEDBACK */}
      {toastMessage && (
        <div className="mx-4 mt-2 p-2.5 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 text-xs font-medium flex items-center gap-2">
          <CheckCircle2 size={14} />
          <span>{toastMessage}</span>
        </div>
      )}

      {/* 6. SESSIONS FEED (Single Unified Card Grid) */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {filteredSessions.length === 0 ? (
          <div className="h-full min-h-[260px] flex flex-col items-center justify-center text-center p-8 rounded-2xl bg-surface-100 border border-border">
            <div className="w-12 h-12 rounded-2xl bg-surface-200 border border-border flex items-center justify-center mb-3">
              <Film className="w-6 h-6 text-primary" />
            </div>
            <h2 className="text-sm font-semibold text-text-main">No Matching Sessions</h2>
            <p className="text-xs text-text-muted mt-1 max-w-[260px]">
              {searchQuery ? 'No sessions matched your search terms.' : 'Filmed camera recordings and imported YouTube videos will appear here.'}
            </p>
            {onNavigateToRecord && !searchQuery && (
              <button
                type="button"
                onClick={onNavigateToRecord}
                className="mt-4 min-h-[44px] px-4 py-2 rounded-xl bg-primary hover:bg-primary-hover text-white text-xs font-semibold flex items-center gap-2 active:scale-[0.98]"
              >
                <Camera size={14} />
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
                onClick={() => setActiveSessionDetail(session)}
                className="p-3 rounded-2xl bg-surface-100 hover:bg-surface-200/80 border border-border flex items-start gap-3 cursor-pointer select-none transition-all active:scale-[0.99]"
              >
                {/* 16:9 Aspect Ratio Thumbnail */}
                <div className="w-[110px] sm:w-[130px] aspect-video shrink-0 rounded-xl overflow-hidden bg-surface-200 border border-border relative flex items-center justify-center group">
                  {ytId ? (
                    <img
                      src={`https://img.youtube.com/vi/${ytId}/mqdefault.jpg`}
                      alt="Thumbnail"
                      className="w-full h-full object-cover"
                      loading="lazy"
                    />
                  ) : session.videoUrl ? (
                    <video
                      src={session.videoUrl.startsWith('http') ? session.videoUrl : apiUrl(session.videoUrl)}
                      className="w-full h-full object-cover"
                      preload="metadata"
                    />
                  ) : (
                    <Film size={20} className="text-text-muted" />
                  )}

                  {/* Play icon overlay on hover */}
                  <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                    <Play size={16} className="text-white fill-white" />
                  </div>

                  {/* Duration overlay badge */}
                  <span className="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-black/80 text-[10px] font-mono tabular-nums font-semibold text-white pointer-events-none">
                    {formatDuration(session.totalDuration)}
                  </span>
                </div>

                {/* Session Metadata & Status */}
                <div className="flex-1 min-w-0 flex flex-col justify-between self-stretch">
                  <div className="space-y-1">
                    <div className="flex items-start justify-between gap-1.5">
                      <h2 className="text-sm font-semibold text-text-main line-clamp-1 leading-snug">
                        {session.title}
                      </h2>
                      {/* Status Chip */}
                      {isReady ? (
                        <span className="px-2 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 shrink-0 flex items-center gap-1">
                          <CheckCircle2 size={10} />
                          <span>{session.clipCount}&nbsp;Clips</span>
                        </span>
                      ) : isProcessing ? (
                        <span className="px-2 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-amber-500/10 border border-amber-500/30 text-amber-400 shrink-0 flex items-center gap-1">
                          <RotateCw size={10} className="animate-spin" />
                          <span>Processing</span>
                        </span>
                      ) : session.status === 'failed' ? (
                        <span className="px-2 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-danger/10 border border-danger/30 text-danger shrink-0 flex items-center gap-1">
                          <AlertCircle size={10} />
                          <span>Needs Retry</span>
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-surface-200 border border-border text-text-muted shrink-0">
                          Queued
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-2 text-[11px] text-text-muted font-mono flex-wrap">
                      <span className="px-1.5 py-0.2 rounded bg-surface-200 text-text-secondary text-[10px]">
                        {session.origin === 'phone' ? 'Phone Camera' : 'YouTube Direct'}
                      </span>
                      {session.totalBytes > 0 && <span>{formatBytes(session.totalBytes)}</span>}
                      {ytId && <span>ID: {ytId.slice(0, 8)}…</span>}
                    </div>
                  </div>

                  {/* Bottom Action Prompt */}
                  <div className="flex items-center justify-between pt-1 text-xs">
                    <span className="text-[11px] text-text-muted truncate">
                      {isReady
                        ? 'Tap to inspect & open clips'
                        : isProcessing
                        ? 'AI transcription active on PC'
                        : 'Waiting for PC ingest'}
                    </span>
                    <span className="text-primary text-xs font-medium flex items-center gap-0.5 group-hover:translate-x-0.5 transition-transform">
                      <span>Details</span>
                      <ArrowRight size={11} />
                    </span>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* 7. INTERACTIVE SESSION DETAIL & CRUD BOTTOM SHEET */}
      {activeSessionDetail && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-end sm:items-center justify-center p-0 sm:p-4">
          <div className="w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-t-3xl sm:rounded-2xl bg-surface-100 border border-border p-5 space-y-4 shadow-2xl animate-in slide-in-from-bottom duration-200">
            {/* Modal Header */}
            <div className="flex items-start justify-between gap-3 border-b border-border pb-3">
              <div>
                <span className="text-[10px] uppercase tracking-wider font-semibold text-text-muted block">
                  {activeSessionDetail.origin === 'phone' ? 'Camera Recording Session' : 'Imported YouTube Video'}
                </span>
                <h2 className="text-base font-semibold text-text-main line-clamp-1 mt-0.5">
                  {activeSessionDetail.title}
                </h2>
              </div>
              <button
                type="button"
                onClick={() => setActiveSessionDetail(null)}
                className="w-8 h-8 rounded-full bg-surface-200 hover:bg-surface-300 flex items-center justify-center text-text-muted hover:text-text-main"
              >
                <X size={15} />
              </button>
            </div>

            {/* Video Player Preview */}
            <div className="w-full aspect-video rounded-xl bg-black overflow-hidden border border-border relative flex items-center justify-center">
              {activeSessionDetail.videoUrl ? (
                <video
                  src={activeSessionDetail.videoUrl.startsWith('http') ? activeSessionDetail.videoUrl : apiUrl(activeSessionDetail.videoUrl)}
                  controls
                  className="w-full h-full object-contain"
                  preload="metadata"
                />
              ) : activeSessionDetail.youtubeVideoId ? (
                <div className="w-full h-full relative group">
                  <img
                    src={`https://img.youtube.com/vi/${activeSessionDetail.youtubeVideoId}/hqdefault.jpg`}
                    alt="Preview"
                    className="w-full h-full object-cover"
                  />
                  <a
                    href={`https://youtu.be/${activeSessionDetail.youtubeVideoId}`}
                    target="_blank"
                    rel="noreferrer"
                    className="absolute inset-0 bg-black/50 flex flex-col items-center justify-center text-white gap-2"
                  >
                    <div className="w-12 h-12 rounded-full bg-danger flex items-center justify-center shadow-lg group-hover:scale-105 transition-transform">
                      <Play size={20} className="fill-white translate-x-0.5" />
                    </div>
                    <span className="text-xs font-semibold flex items-center gap-1">
                      <span>Watch Unlisted on YouTube</span>
                      <ExternalLink size={12} />
                    </span>
                  </a>
                </div>
              ) : (
                <div className="text-center p-4 text-text-muted space-y-1">
                  <Film size={28} className="mx-auto" />
                  <p className="text-xs">No direct video player preview available</p>
                </div>
              )}
            </div>

            {/* Session Stats Grid */}
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="p-2.5 rounded-xl bg-surface-200 border border-border">
                <span className="text-[10px] text-text-muted uppercase block">Duration</span>
                <span className="text-xs font-bold font-mono text-text-main">
                  {formatDuration(activeSessionDetail.totalDuration)}
                </span>
              </div>
              <div className="p-2.5 rounded-xl bg-surface-200 border border-border">
                <span className="text-[10px] text-text-muted uppercase block">Storage</span>
                <span className="text-xs font-bold font-mono text-text-main">
                  {activeSessionDetail.totalBytes > 0 ? formatBytes(activeSessionDetail.totalBytes) : 'YouTube Cloud'}
                </span>
              </div>
              <div className="p-2.5 rounded-xl bg-surface-200 border border-border">
                <span className="text-[10px] text-text-muted uppercase block">Clips Generated</span>
                <span className="text-xs font-bold font-mono text-primary">
                  {activeSessionDetail.clipCount}
                </span>
              </div>
            </div>

            {/* Status & Processing Description */}
            <div className="p-3 rounded-xl bg-surface-200 border border-border text-xs space-y-1">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-text-secondary">AI Editing Pipeline Status</span>
                <span className="font-mono text-[11px] text-text-muted uppercase">{activeSessionDetail.status}</span>
              </div>
              <p className="text-text-muted text-[11px] leading-relaxed">
                {activeSessionDetail.status === 'ready'
                  ? `AI transcription completed. ${activeSessionDetail.clipCount} high-engagement clips extracted and ready for captioning.`
                  : activeSessionDetail.status === 'processing'
                  ? 'PC Studio is currently transcribing audio and ranking viral highlights.'
                  : 'Session safely queued. When your PC Studio runs Dispatch, it will automatically download and render clips.'}
              </p>
            </div>

            {/* PRIMARY HERO CTA: View Clips in Studio */}
            {activeSessionDetail.clipCount > 0 ? (
              <button
                type="button"
                onClick={() => {
                  const sid = activeSessionDetail.id;
                  setActiveSessionDetail(null);
                  if (onNavigateToClips) {
                    onNavigateToClips(sid);
                  }
                }}
                className="w-full min-h-[46px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 shadow-hero-glow active:scale-[0.98] transition-all"
              >
                <Sparkles size={16} />
                <span>View {activeSessionDetail.clipCount} Clips in Review Studio</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  checkPcStudio();
                  showToast('Re-checking PC Studio queue status…');
                }}
                className="w-full min-h-[44px] rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-text-main text-xs font-semibold flex items-center justify-center gap-2 active:scale-[0.98] transition-all"
              >
                <RotateCw size={14} className={pcStatus.checking ? 'animate-spin' : ''} />
                <span>Check PC Studio Queue Status</span>
              </button>
            )}

            {/* SECONDARY CRUD ACTIONS */}
            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={() => {
                  const text = activeSessionDetail.youtubeVideoId
                    ? `https://youtu.be/${activeSessionDetail.youtubeVideoId}`
                    : activeSessionDetail.id;
                  handleCopy(text, 'Session reference');
                }}
                className="flex-1 min-h-[44px] px-3 rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-xs font-medium text-text-main flex items-center justify-center gap-1.5 active:scale-[0.98]"
              >
                <Copy size={13} />
                <span>Copy Link / ID</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  if (activeSessionDetail.youtubeVideoId) {
                    handleImportSubmit(`https://youtu.be/${activeSessionDetail.youtubeVideoId}`);
                  } else {
                    showToast('Re-processing native camera session…');
                  }
                }}
                className="flex-1 min-h-[44px] px-3 rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-xs font-medium text-text-main flex items-center justify-center gap-1.5 active:scale-[0.98]"
              >
                <RotateCw size={13} />
                <span>Re-process</span>
              </button>

              <button
                type="button"
                onClick={() => setShowConfirmDelete(activeSessionDetail)}
                className="min-h-[44px] px-3.5 rounded-xl bg-surface-200 hover:bg-danger/20 hover:text-danger border border-border text-xs font-medium text-text-secondary flex items-center justify-center gap-1.5 active:scale-[0.98]"
                title="Delete Session"
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 8. CONFIRM DELETE DIALOG */}
      {showConfirmDelete && (
        <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4">
          <div className="w-full max-w-sm rounded-2xl bg-surface-100 border border-border p-5 space-y-3 shadow-xl">
            <div className="flex items-center gap-2 text-danger font-semibold text-sm">
              <AlertCircle size={16} />
              <span>Dismiss Session?</span>
            </div>
            <p className="text-xs text-text-muted leading-relaxed">
              Remove <strong className="text-text-main">{showConfirmDelete.title}</strong> from your active sessions view. Any existing clips already generated remain safely stored in your Clips library.
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowConfirmDelete(null)}
                className="min-h-[44px] px-4 rounded-xl bg-surface-200 text-xs font-medium text-text-main"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  showToast('Session dismissed.');
                  setShowConfirmDelete(null);
                  setActiveSessionDetail(null);
                }}
                className="min-h-[44px] px-4 rounded-xl bg-danger text-white text-xs font-semibold"
              >
                Confirm Dismiss
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
