import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { StudioHeader } from './StudioHeader';
import { QueuePane } from './QueuePane';
import { PreviewStage } from './PreviewStage';
import { CaptionDock } from './CaptionDock';
import { InspectorPane } from './InspectorPane';
import { KeyboardShortcutsModal } from './KeyboardShortcutsModal';
import { BatchConfirmModal } from './BatchConfirmModal';
import { ClipLibraryNotice } from './ClipLibraryNotice';
import type { ClipLibraryLoadState } from './ClipLibraryNotice';
import { parseClipListResponse } from './clipLibrary';
import {
  ClipItem,
  SystemStatus,
  IntegrationsStatus,
  LayoutMode,
} from './types';
import { CaptionTrack, CaptionGroup, StyleConfig, LayoutConfig } from '../captions/types';
import { applySectionStylesToTrack, StyleSection } from '../captions/applyToAll';
import { normalizeCaptionTrack, resolveStudioClip, trackForClip } from '../captions/normalizeTrack';
import { apiUrl, getAuthHeaders } from '../bridge';

interface StudioWorkstationProps {
  pcHost: string;
  onSwitchToMobileFrame: () => void;
}

export const StudioWorkstation: React.FC<StudioWorkstationProps> = ({
  pcHost,
  onSwitchToMobileFrame,
}) => {
  // --- Data States ---
  const [clips, setClips] = useState<ClipItem[]>([]);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [clipLoadState, setClipLoadState] = useState<ClipLibraryLoadState>('loading');
  const [clipLoadError, setClipLoadError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [track, setTrack] = useState<CaptionTrack | null>(null);
  const [captionClipId, setCaptionClipId] = useState<string | null>(null);
  const [captionLoadState, setCaptionLoadState] = useState<'idle' | 'loading' | 'success' | 'empty' | 'error'>('idle');
  const [captionLoadError, setCaptionLoadError] = useState<string | null>(null);
  const [captionRetryCount, setCaptionRetryCount] = useState(0);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);

  // --- Telemetry States ---
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationsStatus | null>(null);
  const [publishMode, setPublishMode] = useState<'private' | 'public'>('private');

  // --- Playback States ---
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [duration, setDuration] = useState<number>(30);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);

  // --- Layout & Workspace Dimensions ---
  const [isLeftCollapsed, setIsLeftCollapsed] = useState<boolean>(() => {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem('dispatch_left_collapsed') === 'true';
    }
    return false;
  });
  const [isRightCollapsed, setIsRightCollapsed] = useState<boolean>(() => {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem('dispatch_right_collapsed') === 'true';
    }
    return false;
  });
  const [isFocusMode, setIsFocusMode] = useState<boolean>(false);
  const [leftWidth, setLeftWidth] = useState<number>(310);
  const [rightWidth, setRightWidth] = useState<number>(380);

  // --- Modal & Async Action States ---
  const [isShortcutsOpen, setIsShortcutsOpen] = useState<boolean>(false);
  const [batchModal, setBatchModal] = useState<{ isOpen: boolean; actionType: 'approve' | 'reject' }>({
    isOpen: false,
    actionType: 'approve',
  });
  const [isApproving, setIsApproving] = useState<boolean>(false);
  const [isRejecting, setIsRejecting] = useState<boolean>(false);
  const [isSavingCaptions, setIsSavingCaptions] = useState<boolean>(false);
  const [isRendering, setIsRendering] = useState<boolean>(false);
  const [isBatchProcessing, setIsBatchProcessing] = useState<boolean>(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const prevCollapsedStateRef = useRef<{ left: boolean; right: boolean }>({ left: false, right: false });
  const metadataDraftsRef = useRef<Record<string, Partial<ClipItem>>>({});
  const clipFetchControllerRef = useRef<AbortController | null>(null);
  const clipFetchSucceededRef = useRef(false);
  const clipListHasItemsRef = useRef(false);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3000);
  };

  // 1. Fetch Clips (preserves local metadata drafts across 6s background polling)
  const fetchClips = useCallback(async (force = false) => {
    if (clipFetchControllerRef.current) {
      if (!force) return;
      clipFetchControllerRef.current.abort();
    }
    const controller = new AbortController();
    clipFetchControllerRef.current = controller;
    let timedOut = false;
    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 15_000);
    try {
      const res = await fetch(apiUrl('/api/clips', pcHost), { signal: controller.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to load clips`);
      const data: unknown = await res.json();
      const allClips = parseClipListResponse(data);
      if (controller.signal.aborted) return;
      const mergedClips = allClips.map((clip) => {
        const draft = metadataDraftsRef.current[clip.id];
        return draft ? { ...clip, ...draft } : clip;
      });
      setClips(mergedClips);
      clipFetchSucceededRef.current = true;
      clipListHasItemsRef.current = mergedClips.length > 0;
      setClipLoadState('loaded');
      setClipLoadError(null);
      // If no clip selected or selected clip no longer exists, default to first clip
      setSelectedClipId(prev => {
        if (prev && mergedClips.some(c => c.id === prev)) return prev;
        return mergedClips.length > 0 ? mergedClips[0].id : null;
      });
    } catch (err) {
      if ((controller.signal.aborted || (err as DOMException)?.name === 'AbortError') && !timedOut) return;
      if (!clipFetchSucceededRef.current || !clipListHasItemsRef.current) setClipLoadState('error');
      setClipLoadError(timedOut
        ? 'The PC clip library did not respond within 15 seconds. Check the connection and retry.'
        : err instanceof Error ? err.message : 'Network communication failure');
      console.warn('Failed to fetch clips:', err);
    } finally {
      window.clearTimeout(timeoutId);
      if (clipFetchControllerRef.current === controller) clipFetchControllerRef.current = null;
    }
  }, [pcHost]);

  // 2. Fetch System Status & Integrations
  const fetchTelemetry = useCallback(async () => {
    try {
      const [resStatus, resInteg] = await Promise.all([
        fetch(apiUrl('/api/status', pcHost)),
        fetch(apiUrl('/api/integrations/status', pcHost)),
      ]);
      if (resStatus.ok) {
        const sData = await resStatus.json();
        setSystemStatus(sData);
        if (sData.publish_mode) {
          setPublishMode(sData.publish_mode);
        }
      }
      if (resInteg.ok) {
        const iData = await resInteg.json();
        setIntegrations(iData);
      }
    } catch {
      // ignore offline telemetry errors
    }
  }, [pcHost]);

  // Initial and recurring poll
  useEffect(() => {
    clipFetchSucceededRef.current = false;
    clipListHasItemsRef.current = false;
    setClipLoadState('loading');
    setClipLoadError(null);
    fetchClips();
    fetchTelemetry();
    const interval = setInterval(() => {
      fetchClips();
      fetchTelemetry();
    }, 6000);
    return () => {
      clearInterval(interval);
      clipFetchControllerRef.current?.abort();
      clipFetchControllerRef.current = null;
    };
  }, [fetchClips, fetchTelemetry]);

  // 3. Derived current selected clip
  const currentClip = useMemo(() => {
    return resolveStudioClip(clips, selectedClipId);
  }, [clips, selectedClipId]);
  const visibleTrack = trackForClip(currentClip?.id || null, captionClipId, track);

  // 4. Fetch CaptionTrack for selected clip
  useEffect(() => {
    if (!currentClip) {
      setTrack(null);
      setCaptionClipId(null);
      setCaptionLoadState('idle');
      setCaptionLoadError(null);
      return;
    }

    const clipId = currentClip.id;
    const controller = new AbortController();
    setTrack(null);
    setCaptionClipId(null);
    setCaptionLoadState('loading');
    setCaptionLoadError(null);
    setSelectedGroupId(null);

    fetch(apiUrl(`/api/clips/${clipId}/captions`, pcHost), { signal: controller.signal })
      .then(async (res) => {
        if (res.status === 404) return { groups: [] };
        if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to load captions`);
        return res.json();
      })
      .then((raw) => {
        if (controller.signal.aborted) return;
        const normalized = normalizeCaptionTrack(raw, clipId, currentClip.duration || 40);
        setTrack(normalized);
        setCaptionClipId(clipId);
        setCaptionLoadState(normalized.groups.length > 0 ? 'success' : 'empty');
        setSelectedGroupId(normalized.groups[0]?.id || null);
      })
      .catch((err: unknown) => {
        if ((err as DOMException)?.name === 'AbortError') return;
        const message = err instanceof Error ? err.message : 'Network communication failure';
        console.warn('Captions unavailable for clip:', err);
        setCaptionLoadError(message);
        setCaptionLoadState('error');
      });

    setCurrentTime(0);
    setIsPlaying(false);

    return () => controller.abort();
  }, [currentClip?.id, currentClip?.duration, pcHost, captionRetryCount]);

  const handleRetryCaptionLoad = () => {
    setCaptionLoadState('loading');
    setCaptionRetryCount((count) => count + 1);
  };

  // Responsive collapsing at narrower viewports
  useEffect(() => {
    const handleResize = () => {
      const w = window.innerWidth;
      if (w < 1180 && !isRightCollapsed) {
        setIsRightCollapsed(true);
      }
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [isRightCollapsed]);

  // Focus Mode Toggle (collapses or restores both panes)
  const handleToggleFocusMode = () => {
    if (!isFocusMode) {
      prevCollapsedStateRef.current = { left: isLeftCollapsed, right: isRightCollapsed };
      setIsLeftCollapsed(true);
      setIsRightCollapsed(true);
      setIsFocusMode(true);
    } else {
      setIsLeftCollapsed(prevCollapsedStateRef.current.left);
      setIsRightCollapsed(prevCollapsedStateRef.current.right);
      setIsFocusMode(false);
    }
  };

  // Keyboard Navigation & Shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Strict rule: NEVER trigger shortcuts when typing in inputs/textareas
      const tag = (e.target as HTMLElement)?.tagName?.toLowerCase();
      const isInput = tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target as HTMLElement)?.isContentEditable;
      if (isInput) return;

      // Invariant: NEVER trigger single-key actions when modifier keys (Ctrl, Cmd, Alt) are held
      // Prevents Ctrl+R (reload), Ctrl+A (select all), Ctrl+F (find) from triggering approve/reject/focus!
      const hasModifier = e.ctrlKey || e.metaKey || e.altKey;
      if (hasModifier) return;

      if (e.key === ' ' || e.code === 'Space') {
        e.preventDefault();
        setIsPlaying(prev => !prev);
      } else if (e.key === 'j' || e.key === 'J') {
        e.preventDefault();
        setCurrentTime(prev => Math.max(0, prev - 5));
      } else if (e.key === 'k' || e.key === 'K') {
        e.preventDefault();
        setIsPlaying(false);
      } else if (e.key === 'l' || e.key === 'L') {
        e.preventDefault();
        setCurrentTime(prev => Math.min(duration, prev + 5));
      } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
        e.preventDefault();
        if (clips.length > 0 && selectedClipId) {
          const idx = clips.findIndex(c => c.id === selectedClipId);
          if (idx > 0) {
            setSelectedClipId(clips[idx - 1].id);
          }
        }
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
        e.preventDefault();
        if (clips.length > 0 && selectedClipId) {
          const idx = clips.findIndex(c => c.id === selectedClipId);
          if (idx >= 0 && idx < clips.length - 1) {
            setSelectedClipId(clips[idx + 1].id);
          }
        }
      } else if (e.key === 'a' || e.key === 'A') {
        e.preventDefault();
        handleApproveCurrentClip();
      } else if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        handleRejectCurrentClip();
      } else if (e.key === 'f' || e.key === 'F') {
        e.preventDefault();
        handleToggleFocusMode();
      } else if (e.key === '?') {
        e.preventDefault();
        setIsShortcutsOpen(prev => !prev);
      } else if (e.key === 'Escape') {
        if (isShortcutsOpen) setIsShortcutsOpen(false);
        if (batchModal.isOpen) setBatchModal({ isOpen: false, actionType: 'approve' });
        if (isFocusMode) handleToggleFocusMode();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [clips, selectedClipId, duration, isFocusMode, isShortcutsOpen, batchModal.isOpen]);

  // --- Mutating Operations ---

  // 1. Approve Selected Clip
  const handleApproveCurrentClip = async () => {
    if (!currentClip || isApproving) return;
    if (isSavingCaptions || !visibleTrack || captionClipId !== currentClip.id || captionLoadState === 'loading' || captionLoadState === 'error') {
      showToast('Load captions successfully before approving this clip.');
      return;
    }
    setIsApproving(true);
    try {
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/approve`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          title: currentClip.title,
          hashtags: currentClip.hashtags,
          publish_mode: publishMode,
          platforms: currentClip.platform_targets || 'youtube',
          layout_mode: currentClip.layout_mode,
          caption_track: visibleTrack,
        }),
      });
      if (res.ok) {
        delete metadataDraftsRef.current[currentClip.id];
        showToast(`Clip Approved: Queued into Publishing Outbox (${publishMode})`);
        setClips(prev => prev.map(c => c.id === currentClip.id ? { ...c, status: 'approved' } : c));
      } else {
        showToast(`Approval failed (HTTP ${res.status})`);
      }
    } catch (err: any) {
      showToast(`Error approving clip: ${err?.message || 'Network error'}`);
    } finally {
      setIsApproving(false);
    }
  };

  // 2. Reject Selected Clip
  const handleRejectCurrentClip = async (reason = 'User rejected') => {
    if (!currentClip || isRejecting) return;
    setIsRejecting(true);
    try {
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/reject`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ reason }),
      });
      if (res.ok) {
        delete metadataDraftsRef.current[currentClip.id];
        showToast('Clip marked as Rejected');
        setClips(prev => prev.map(c => c.id === currentClip.id ? { ...c, status: 'rejected' } : c));
      } else {
        showToast(`Rejection failed (HTTP ${res.status})`);
      }
    } catch (err: any) {
      showToast(`Error rejecting clip: ${err?.message || 'Network error'}`);
    } finally {
      setIsRejecting(false);
    }
  };

  // 3. Change Framing / Layout Mode
  const handleLayoutChange = async (mode: LayoutMode) => {
    if (!currentClip) return;
    // Optimistic update
    setClips(prev => prev.map(c => c.id === currentClip.id ? { ...c, layout_mode: mode } : c));
    try {
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/layout`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ layout_mode: mode }),
      });
      if (res.ok) {
        const data = await res.json();
        showToast(`Framing mode updated to ${mode}`);
        if (data.clip) {
          setClips(prev => prev.map(c => c.id === currentClip.id ? { ...c, ...data.clip } : c));
        }
      }
    } catch (err) {
      console.warn('Failed to update layout mode:', err);
    }
  };

  // 4. Save Captions
  const handleSaveCaptions = async () => {
    if (!currentClip || !track || isSavingCaptions) return;
    setIsSavingCaptions(true);
    try {
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/captions`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(track),
      });
      if (res.ok) {
        showToast('Captions & Subtitle styling persisted');
      } else {
        showToast(`Save captions failed (HTTP ${res.status})`);
      }
    } catch (err: any) {
      showToast(`Error saving captions: ${err?.message || 'Network error'}`);
    } finally {
      setIsSavingCaptions(false);
    }
  };

  // 5. Trigger Atomic FFmpeg Re-render
  const handleTriggerRender = async () => {
    if (!currentClip || isRendering) return;
    setIsRendering(true);
    try {
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/render`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ layout_mode: currentClip.layout_mode }),
      });
      if (res.ok) {
        const data = await res.json();
        showToast('FFmpeg atomic render complete!');
        if (data.video_url) {
          setClips(prev => prev.map(c => c.id === currentClip.id ? {
            ...c,
            video_url: data.video_url,
            is_render_stale: false
          } : c));
        }
      } else {
        showToast(`Render failed (HTTP ${res.status})`);
      }
    } catch (err: any) {
      showToast(`Render error: ${err?.message || 'Server error'}`);
    } finally {
      setIsRendering(false);
    }
  };

  // 6. Update Clip Metadata Draft (local cache update with persistent protection from 6s polling)
  const handleUpdateClipMetadata = (patch: Partial<ClipItem>) => {
    if (!currentClip) return;
    metadataDraftsRef.current[currentClip.id] = {
      ...(metadataDraftsRef.current[currentClip.id] || {}),
      ...patch,
    };
    setClips(prev => prev.map(c => c.id === currentClip.id ? { ...c, ...patch } : c));
  };

  // 7. Update Track Style (edits affect only the selected group, leaving others untouched)
  const handleUpdateTrackStyle = (stylePatch: Partial<StyleConfig>) => {
    if (!track) return;
    const targetGroupId = selectedGroupId || (track.groups.find(g => !g.type || g.type === 'caption' || g.type === 'text')?.id) || (track.groups.length > 0 ? track.groups[0].id : null);
    if (!targetGroupId) return;
    const updatedGroups = track.groups.map(g => {
      if (g.id !== targetGroupId) return g;
      return {
        ...g,
        style: { ...(g.style || track.default_style), ...stylePatch },
      };
    });
    setTrack({
      ...track,
      groups: updatedGroups,
    });
  };

  const handleUpdateTrackLayout = (layoutPatch: Partial<LayoutConfig>) => {
    if (!track) return;
    const targetGroupId = selectedGroupId || (track.groups.find(g => !g.type || g.type === 'caption' || g.type === 'text')?.id) || (track.groups.length > 0 ? track.groups[0].id : null);
    if (!targetGroupId) return;
    const updatedGroups = track.groups.map(g => {
      if (g.id !== targetGroupId) return g;
      return {
        ...g,
        layout: { ...(g.layout || track.default_layout), ...layoutPatch },
      };
    });
    setTrack({
      ...track,
      groups: updatedGroups,
    });
  };

  const handleApplySectionToAll = (section: StyleSection): number => {
    if (!track) return 0;
    const { nextTrack, affectedCount } = applySectionStylesToTrack(
      track,
      selectedGroupId,
      section
    );
    setTrack(nextTrack);
    return affectedCount;
  };

  // 8. Update Track Group
  const handleUpdateTrackGroup = (groupId: string, patch: Partial<CaptionGroup>) => {
    if (!track) return;
    const updatedGroups = track.groups.map(g => g.id === groupId ? { ...g, ...patch } : g);
    setTrack({ ...track, groups: updatedGroups });
  };

  // --- Batch Operations ---
  const handleToggleSelectId = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSelectAllVisible = (ids: string[]) => {
    const isAll = ids.every(id => selectedIds.has(id));
    if (isAll) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(ids));
    }
  };

  const handleSelectByScoreThreshold = (threshold: number) => {
    const matchIds = clips.filter(c => c.virality_score >= threshold && c.status === 'ready_review').map(c => c.id);
    setSelectedIds(new Set(matchIds));
    showToast(`Selected ${matchIds.length} ready clips with score ≥ ${threshold}`);
  };

  const handleExecuteBatchConfirm = async () => {
    const targetClips = clips.filter(c => selectedIds.has(c.id));
    if (targetClips.length === 0) return;

    setIsBatchProcessing(true);
    let successCount = 0;

    for (const clipItem of targetClips) {
      try {
        if (batchModal.actionType === 'approve') {
          const res = await fetch(apiUrl(`/api/clips/${clipItem.id}/approve`, pcHost), {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({
              title: clipItem.title,
              hashtags: clipItem.hashtags,
              publish_mode: publishMode,
              platforms: clipItem.platform_targets || 'youtube',
              layout_mode: clipItem.layout_mode,
            }),
          });
          if (res.ok) successCount++;
        } else {
          const res = await fetch(apiUrl(`/api/clips/${clipItem.id}/reject`, pcHost), {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ reason: 'Batch rejection' }),
          });
          if (res.ok) successCount++;
        }
      } catch {
        // continue batch
      }
    }

    setIsBatchProcessing(false);
    setBatchModal({ isOpen: false, actionType: 'approve' });
    setSelectedIds(new Set());
    fetchClips();
    showToast(`Batch ${batchModal.actionType}: ${successCount} of ${targetClips.length} clips processed`);
  };

  return (
    <div className="h-[100dvh] w-full bg-[#0D0F12] text-[#F3F4F6] flex flex-col overflow-hidden font-sans select-none">
      {/* 1. COMPACT GLOBAL HEADER */}
      <StudioHeader
        systemStatus={systemStatus}
        integrations={integrations}
        selectedClip={currentClip}
        totalClipsCount={clips.length}
        readyClipsCount={clips.filter(c => c.status === 'ready_review').length}
        isFocusMode={isFocusMode}
        onToggleFocusMode={handleToggleFocusMode}
        onOpenShortcuts={() => setIsShortcutsOpen(true)}
        onSwitchToMobileFrame={onSwitchToMobileFrame}
        publishMode={publishMode}
        onTogglePublishMode={setPublishMode}
      />

      {/* 2. THREE-PANE DESKTOP WORKSTATION GRID */}
      <div className="flex-1 min-h-0 w-full flex overflow-hidden">
        {/* LEFT PANE: MEDIA QUEUE */}
        <QueuePane
          clips={clips}
          selectedClipId={selectedClipId}
          onSelectClip={(c) => setSelectedClipId(c.id)}
          selectedIds={selectedIds}
          onToggleSelectId={handleToggleSelectId}
          onSelectAllVisible={handleSelectAllVisible}
          onClearSelection={() => setSelectedIds(new Set())}
          onSelectByScoreThreshold={handleSelectByScoreThreshold}
          onRequestBatchApprove={() => setBatchModal({ isOpen: true, actionType: 'approve' })}
          onRequestBatchReject={() => setBatchModal({ isOpen: true, actionType: 'reject' })}
          isCollapsed={isLeftCollapsed}
          onToggleCollapse={() => {
            const next = !isLeftCollapsed;
            setIsLeftCollapsed(next);
            if (typeof localStorage !== 'undefined') localStorage.setItem('dispatch_left_collapsed', String(next));
          }}
          width={leftWidth}
          pcHost={pcHost}
        />

        {/* CENTER PANE: VIDEO PREVIEW STAGE + DOCKED CAPTION TIMELINE */}
        <div className="flex-1 min-w-0 h-full flex flex-col bg-[#0D0F12] overflow-hidden">
          {!currentClip ? (
            <div className="flex flex-1 items-center justify-center">
              <ClipLibraryNotice state={clipLoadState} error={clipLoadError} onRetry={() => { void fetchClips(true); }} />
            </div>
          ) : (
            <>
              {clipLoadError && (
                <div className="flex min-h-[44px] items-center justify-between gap-3 border-b border-border bg-surface-100 px-3 text-[12px] text-text-muted" role="status">
                  <span>Clip list refresh failed: {clipLoadError}</span>
                  <button type="button" onClick={() => { void fetchClips(true); }} className="min-h-[44px] px-3 text-text-main underline">Retry</button>
                </div>
              )}
          {/* Top Video Stage */}
          <PreviewStage
            clip={currentClip}
            track={visibleTrack}
            currentTime={currentTime}
            duration={duration}
            isPlaying={isPlaying}
            onTimeUpdate={setCurrentTime}
            onDurationChange={setDuration}
            onTogglePlay={() => setIsPlaying(prev => !prev)}
            onSeek={setCurrentTime}
            onUpdateTrackGroup={handleUpdateTrackGroup}
            selectedGroupId={selectedGroupId}
            onSelectGroup={setSelectedGroupId}
            onLayoutChange={handleLayoutChange}
            onTriggerRender={handleTriggerRender}
            isRendering={isRendering}
            pcHost={pcHost}
            onRetryClip={() => { void fetchClips(true); }}
          />

          {/* Bottom Docked Caption Timeline */}
          <CaptionDock
            track={visibleTrack}
            currentTime={currentTime}
            duration={duration}
            onSeek={setCurrentTime}
            onUpdateTrack={setTrack}
            selectedGroupId={selectedGroupId}
            onSelectGroup={setSelectedGroupId}
            captionLoadState={captionLoadState}
            captionLoadError={captionLoadError}
            onRetryCaptions={handleRetryCaptionLoad}
          />
            </>
          )}
        </div>

        {/* RIGHT PANE: INSPECTOR & PUBLISHING */}
        <InspectorPane
          clip={currentClip}
          track={visibleTrack}
          integrations={integrations}
          onUpdateClipMetadata={handleUpdateClipMetadata}
          onSaveCaptions={handleSaveCaptions}
          onUpdateTrackStyle={handleUpdateTrackStyle}
          onUpdateTrackLayout={handleUpdateTrackLayout}
          onApplyStyleToAll={handleApplySectionToAll}
          onApproveClip={handleApproveCurrentClip}
          onRejectClip={handleRejectCurrentClip}
          isApproving={isApproving}
          isRejecting={isRejecting}
          isSavingCaptions={isSavingCaptions}
          publishMode={publishMode}
          isCollapsed={isRightCollapsed}
          onToggleCollapse={() => {
            const next = !isRightCollapsed;
            setIsRightCollapsed(next);
            if (typeof localStorage !== 'undefined') localStorage.setItem('dispatch_right_collapsed', String(next));
          }}
          width={rightWidth}
        />
      </div>

      {/* TOAST NOTIFICATION DECK */}
      {toastMessage && (
        <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-xl bg-[#1C1F24] border border-[#2563EB]/50 text-white text-xs font-medium shadow-2xl animate-in slide-in-from-bottom-2 duration-150 flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-[#2563EB] shadow-hero-sm animate-pulse" />
          <span>{toastMessage}</span>
        </div>
      )}

      {/* KEYBOARD SHORTCUTS MODAL */}
      <KeyboardShortcutsModal
        isOpen={isShortcutsOpen}
        onClose={() => setIsShortcutsOpen(false)}
      />

      {/* BATCH CONFIRMATION MODAL */}
      <BatchConfirmModal
        isOpen={batchModal.isOpen}
        onClose={() => setBatchModal({ isOpen: false, actionType: 'approve' })}
        actionType={batchModal.actionType}
        selectedClips={clips.filter(c => selectedIds.has(c.id))}
        onConfirm={handleExecuteBatchConfirm}
        isProcessing={isBatchProcessing}
        publishMode={publishMode}
      />
    </div>
  );
};
