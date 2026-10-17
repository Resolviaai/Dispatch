import React, { useState, useMemo } from 'react';
import {
  Search,
  SlidersHorizontal,
  ChevronDown,
  ChevronRight,
  CheckSquare,
  Square,
  Sparkles,
  CheckCircle2,
  XCircle,
  Clock,
  Film,
  PanelLeftClose,
  PanelLeftOpen,
  X,
  Layers,
  ArrowUpDown
} from 'lucide-react';
import { ClipItem, QueueFilter, QueueSort, SessionGroupData } from './types';
import { apiUrl } from '../bridge';

interface QueuePaneProps {
  clips: ClipItem[];
  selectedClipId: string | null;
  onSelectClip: (clip: ClipItem) => void;
  selectedIds: Set<string>;
  onToggleSelectId: (id: string, e: React.MouseEvent) => void;
  onSelectAllVisible: (ids: string[]) => void;
  onClearSelection: () => void;
  onSelectByScoreThreshold: (threshold: number) => void;
  onRequestBatchApprove: () => void;
  onRequestBatchReject: () => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  width: number;
  pcHost: string;
}

export const QueuePane: React.FC<QueuePaneProps> = ({
  clips,
  selectedClipId,
  onSelectClip,
  selectedIds,
  onToggleSelectId,
  onSelectAllVisible,
  onClearSelection,
  onSelectByScoreThreshold,
  onRequestBatchApprove,
  onRequestBatchReject,
  isCollapsed,
  onToggleCollapse,
  width,
  pcHost,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [filter, setFilter] = useState<QueueFilter>('all');
  const [sort, setSort] = useState<QueueSort>('score');
  const [collapsedSessions, setCollapsedSessions] = useState<Record<string, boolean>>({});

  // Filter clips
  const filteredClips = useMemo(() => {
    let result = [...clips];

    // Status filter
    if (filter === 'ready') {
      result = result.filter(c => c.status === 'ready_review');
    } else if (filter === 'approved') {
      result = result.filter(c => c.status === 'approved');
    } else if (filter === 'published') {
      result = result.filter(c => c.status === 'published');
    } else if (filter === 'rejected') {
      result = result.filter(c => c.status === 'rejected');
    }

    // Search query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(c =>
        (c.title && c.title.toLowerCase().includes(q)) ||
        (c.hook && c.hook.toLowerCase().includes(q)) ||
        (c.session_id && c.session_id.toLowerCase().includes(q)) ||
        (c.hashtags && c.hashtags.toLowerCase().includes(q))
      );
    }

    // Sort
    result.sort((a, b) => {
      if (sort === 'score') {
        return (b.virality_score || 0) - (a.virality_score || 0);
      }
      if (sort === 'newest') {
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      }
      if (sort === 'oldest') {
        return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      }
      if (sort === 'duration') {
        return (b.duration || 0) - (a.duration || 0);
      }
      return 0;
    });

    return result;
  }, [clips, filter, searchQuery, sort]);

  // Group by session
  const sessionGroups: SessionGroupData[] = useMemo(() => {
    const groupsMap = new Map<string, ClipItem[]>();
    for (const clip of filteredClips) {
      const sId = clip.session_id || 'unassigned_session';
      if (!groupsMap.has(sId)) {
        groupsMap.set(sId, []);
      }
      groupsMap.get(sId)!.push(clip);
    }

    const groups: SessionGroupData[] = [];
    groupsMap.forEach((gClips, sId) => {
      const firstClip = gClips[0];
      const title = sId === 'unassigned_session'
        ? 'Direct Inbox Recordings'
        : `Session ${sId.replace(/^sess_/, '').slice(0, 8)}`;
      groups.push({
        sessionId: sId,
        title,
        createdAt: firstClip.created_at,
        clips: gClips,
      });
    });

    return groups;
  }, [filteredClips]);

  const toggleSessionCollapse = (sessionId: string) => {
    setCollapsedSessions(prev => ({
      ...prev,
      [sessionId]: !prev[sessionId]
    }));
  };

  const counts = useMemo(() => {
    return {
      all: clips.length,
      ready: clips.filter(c => c.status === 'ready_review').length,
      approved: clips.filter(c => c.status === 'approved').length,
      published: clips.filter(c => c.status === 'published').length,
      rejected: clips.filter(c => c.status === 'rejected').length,
    };
  }, [clips]);

  const allVisibleIds = useMemo(() => filteredClips.map(c => c.id), [filteredClips]);
  const isAllVisibleSelected = allVisibleIds.length > 0 && allVisibleIds.every(id => selectedIds.has(id));

  if (isCollapsed) {
    return (
      <aside className="w-10 min-w-[40px] h-full bg-[#16181C] border-r border-[#2E3239] flex flex-col items-center py-3 select-none shrink-0 transition-all">
        <button
          type="button"
          onClick={onToggleCollapse}
          className="p-1.5 rounded-md text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#23262C] transition-all"
          title="Expand Queue Pane"
        >
          <PanelLeftOpen size={16} />
        </button>
        <div className="mt-8 [writing-mode:vertical-rl] rotate-180 text-[11px] font-mono uppercase tracking-wider text-[#747D8A]">
          Queue ({clips.length})
        </div>
      </aside>
    );
  }

  return (
    <aside
      style={{ width: `${width}px` }}
      className="h-full bg-[#16181C] border-r border-[#2E3239] flex flex-col select-none shrink-0 overflow-hidden"
    >
      {/* QUEUE HEADER & SEARCH */}
      <div className="p-3 border-b border-[#2E3239] space-y-2.5 shrink-0 bg-[#16181C]">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Layers size={15} className="text-[#2563EB]" />
            <h2 className="text-xs font-bold uppercase tracking-wider text-[#F3F4F6]">
              Review Queue
            </h2>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full bg-[#23262C] border border-[#2E3239] text-[#A3AAB5]">
              {clips.length}
            </span>
          </div>
          <button
            type="button"
            onClick={onToggleCollapse}
            className="p-1 rounded text-[#747D8A] hover:text-[#F3F4F6] hover:bg-[#23262C] transition-all"
            title="Collapse Queue (Collapse to icon rail)"
          >
            <PanelLeftClose size={15} />
          </button>
        </div>

        {/* Search Field */}
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-2.5 text-[#747D8A]" />
          <input
            type="text"
            placeholder="Search clips or hooks..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full h-8 pl-8 pr-7 text-xs rounded-lg bg-[#23262C] border border-[#2E3239] text-[#F3F4F6] placeholder-[#747D8A] focus:outline-none focus:border-[#2563EB] transition-all"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="absolute right-2 top-2 text-[#747D8A] hover:text-[#F3F4F6]"
            >
              <X size={13} />
            </button>
          )}
        </div>

        {/* Filter Pills */}
        <div className="flex items-center gap-1 overflow-x-auto no-scrollbar pt-0.5">
          {(['all', 'ready', 'approved', 'published', 'rejected'] as QueueFilter[]).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`px-2 py-1 rounded-md text-[11px] font-medium transition-all shrink-0 flex items-center gap-1 ${
                filter === f
                  ? 'bg-[#2563EB] text-white shadow-hero-sm font-semibold'
                  : 'bg-[#23262C] border border-[#2E3239] text-[#A3AAB5] hover:text-[#F3F4F6]'
              }`}
            >
              <span className="capitalize">{f}</span>
              <span className={`text-[10px] font-mono ${filter === f ? 'text-white/80' : 'text-[#747D8A]'}`}>
                {counts[f]}
              </span>
            </button>
          ))}
        </div>

        {/* Sort & Quick Threshold Selection */}
        <div className="flex items-center justify-between text-[11px] text-[#A3AAB5] pt-0.5">
          <div className="flex items-center gap-1.5">
            <ArrowUpDown size={12} className="text-[#747D8A]" />
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as QueueSort)}
              className="bg-[#23262C] border border-[#2E3239] rounded px-1.5 py-0.5 text-[11px] text-[#F3F4F6] focus:outline-none focus:border-[#2563EB]"
            >
              <option value="score">Score (High-Low)</option>
              <option value="newest">Newest First</option>
              <option value="oldest">Oldest First</option>
              <option value="duration">Duration</option>
            </select>
          </div>

          <button
            type="button"
            onClick={() => onSelectByScoreThreshold(85)}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#23262C] hover:bg-[#2563EB]/15 border border-[#2E3239] hover:border-[#2563EB]/40 text-[#60A5FA] text-[10px] font-mono font-medium transition-all"
            title="Auto-select all ready clips with AI Score >= 85"
          >
            <Sparkles size={10} />
            <span>Score ≥ 85</span>
          </button>
        </div>
      </div>

      {/* BATCH ACTION TOOLBAR (Visible when >= 1 item selected) */}
      {selectedIds.size > 0 && (
        <div className="bg-[#1C1F24] border-b border-[#2563EB]/40 p-2.5 flex items-center justify-between text-xs animate-in slide-in-from-top-1 duration-150 shrink-0">
          <div className="flex items-center gap-1.5">
            <span className="font-mono font-semibold text-[#60A5FA]">{selectedIds.size}</span>
            <span className="text-[#A3AAB5]">selected</span>
          </div>

          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={onRequestBatchApprove}
              className="px-2 py-1 rounded bg-[#2563EB] hover:bg-[#1D4ED8] text-white font-medium text-[11px] shadow-hero-sm transition-all active:scale-95 flex items-center gap-1"
            >
              <CheckCircle2 size={12} />
              <span>Approve ({selectedIds.size})</span>
            </button>

            <button
              type="button"
              onClick={onRequestBatchReject}
              className="px-2 py-1 rounded bg-[#23262C] hover:bg-[#F43F5E]/20 border border-[#2E3239] hover:border-[#F43F5E]/50 text-[#F43F5E] text-[11px] transition-all active:scale-95"
            >
              <span>Reject</span>
            </button>

            <button
              type="button"
              onClick={onClearSelection}
              className="p-1 rounded text-[#747D8A] hover:text-[#F3F4F6]"
              title="Clear selection"
            >
              <X size={13} />
            </button>
          </div>
        </div>
      )}

      {/* QUEUE LIST VIEW (Scrollable) */}
      <div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-3">
        {filteredClips.length === 0 ? (
          <div className="h-48 flex flex-col items-center justify-center text-center p-4 text-[#747D8A]">
            <Film size={28} className="mb-2 text-[#2E3239]" />
            <p className="text-xs font-semibold text-[#A3AAB5]">No clips found</p>
            <p className="text-[11px] mt-1 max-w-[200px]">
              {searchQuery ? 'Try clearing your search query' : 'Record or upload footage to generate AI clips.'}
            </p>
          </div>
        ) : (
          sessionGroups.map((group) => {
            const isCollapsed = Boolean(collapsedSessions[group.sessionId]);
            return (
              <div key={group.sessionId} className="rounded-lg bg-[#16181C] border border-[#242830] overflow-hidden">
                {/* Session Header */}
                <button
                  type="button"
                  onClick={() => toggleSessionCollapse(group.sessionId)}
                  className="w-full px-2.5 py-1.5 bg-[#1C1F24] hover:bg-[#23262C] flex items-center justify-between text-left transition-colors border-b border-[#242830]"
                >
                  <div className="flex items-center gap-1.5 min-w-0">
                    {isCollapsed ? <ChevronRight size={13} className="text-[#747D8A]" /> : <ChevronDown size={13} className="text-[#747D8A]" />}
                    <span className="text-[11px] font-semibold text-[#F3F4F6] truncate">
                      {group.title}
                    </span>
                    <span className="text-[10px] font-mono text-[#747D8A]">
                      ({group.clips.length})
                    </span>
                  </div>

                  <span className="text-[10px] font-mono text-[#747D8A] shrink-0">
                    {group.createdAt ? new Date(group.createdAt).toLocaleDateString([], { month: 'short', day: 'numeric' }) : ''}
                  </span>
                </button>

                {/* Session Clips Stack */}
                {!isCollapsed && (
                  <div className="p-1 space-y-1">
                    {group.clips.map((clip) => {
                      const isSelected = selectedClipId === clip.id;
                      const isChecked = selectedIds.has(clip.id);
                      const thumb = clip.thumbnail_url
                        ? (clip.thumbnail_url.startsWith('http') ? clip.thumbnail_url : apiUrl(clip.thumbnail_url, pcHost))
                        : null;

                      return (
                        <div
                          key={clip.id}
                          onClick={() => onSelectClip(clip)}
                          className={`group relative flex items-start gap-2.5 p-1.5 rounded-md cursor-pointer transition-all border ${
                            isSelected
                              ? 'bg-[#1C1F24] border-[#2563EB]/50 border-l-2 border-l-[#2563EB] shadow-sm'
                              : 'bg-[#16181C] border-transparent hover:bg-[#1C1F24] hover:border-[#2E3239]'
                          }`}
                        >
                          {/* Selection Checkbox */}
                          <button
                            type="button"
                            onClick={(e) => onToggleSelectId(clip.id, e)}
                            className="mt-1 text-[#747D8A] hover:text-[#60A5FA] shrink-0 transition-colors"
                            title={isChecked ? 'Deselect clip' : 'Select clip for batch review'}
                          >
                            {isChecked ? (
                              <CheckSquare size={14} className="text-[#2563EB]" />
                            ) : (
                              <Square size={14} className="opacity-40 group-hover:opacity-100" />
                            )}
                          </button>

                          {/* 9:16 Thumbnail */}
                          <div className="relative w-12 h-18 rounded bg-[#23262C] overflow-hidden shrink-0 border border-[#2E3239] flex items-center justify-center">
                            {thumb ? (
                              <img
                                src={thumb}
                                alt=""
                                className="w-full h-full object-cover"
                                loading="lazy"
                              />
                            ) : (
                              <Film size={16} className="text-[#747D8A]" />
                            )}
                            <div className="absolute bottom-0.5 right-0.5 px-1 py-0.2 rounded bg-black/75 text-[9px] font-mono text-white tabular-nums">
                              {Math.round(clip.duration)}s
                            </div>
                          </div>

                          {/* Clip Metadata */}
                          <div className="flex-1 min-w-0 pr-1">
                            <h3
                              className={`text-xs font-semibold leading-tight line-clamp-1 ${
                                isSelected ? 'text-[#F3F4F6]' : 'text-[#EDEDED] group-hover:text-[#F3F4F6]'
                              }`}
                              title={clip.title || clip.hook}
                            >
                              {clip.title || clip.hook || 'Untitled Highlight'}
                            </h3>

                            <p className="text-[11px] text-[#747D8A] line-clamp-1 mt-0.5" title={clip.hook}>
                              {clip.hook || clip.description || 'No hook summary'}
                            </p>

                            <div className="flex items-center gap-1.5 mt-1.5">
                              {/* Score Badge */}
                              <span
                                className={`text-[10px] font-mono font-semibold px-1.5 py-0.2 rounded border ${
                                  clip.virality_score >= 85
                                    ? 'bg-[#2563EB]/15 border-[#2563EB]/40 text-[#60A5FA]'
                                    : 'bg-[#23262C] border-[#2E3239] text-[#A3AAB5]'
                                }`}
                              >
                                {clip.virality_score}
                              </span>

                              {/* Status Badge */}
                              <span
                                className={`text-[10px] px-1.5 py-0.2 rounded border capitalize ${
                                  clip.status === 'ready_review'
                                    ? 'bg-[#EAB308]/10 border-[#EAB308]/30 text-[#EAB308]'
                                    : clip.status === 'approved'
                                    ? 'bg-[#10B981]/10 border-[#10B981]/30 text-[#10B981]'
                                    : clip.status === 'published'
                                    ? 'bg-[#2563EB]/10 border-[#2563EB]/30 text-[#60A5FA]'
                                    : 'bg-[#23262C] border-[#2E3239] text-[#747D8A]'
                                }`}
                              >
                                {clip.status === 'ready_review' ? 'Ready' : clip.status}
                              </span>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* QUEUE FOOTER */}
      <div className="p-2.5 border-t border-[#2E3239] bg-[#16181C] flex items-center justify-between text-[11px] text-[#747D8A] shrink-0 font-mono">
        <button
          type="button"
          onClick={() => onSelectAllVisible(allVisibleIds)}
          className="hover:text-[#F3F4F6] transition-colors"
        >
          {isAllVisibleSelected ? 'Deselect All' : `Select All (${filteredClips.length})`}
        </button>
        <span>{filteredClips.length} clips shown</span>
      </div>
    </aside>
  );
};
