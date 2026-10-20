import React, { useState, useMemo } from 'react';
import {
  Search,
  Replace,
  X,
  Check,
  Clock,
  Play,
  RotateCcw,
  Sparkles,
  ChevronRight,
  Edit2
} from 'lucide-react';
import { CaptionTrack, CaptionGroup, Word } from './types';
import { preserveWordTimingsOnEdit } from './timingPreservation';
import { downloadSrtFile } from './srtExport';

function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

interface TranscriptEditorProps {
  track: CaptionTrack | null;
  currentTime: number;
  selectedGroupId: string | null;
  onSeek: (seconds: number) => void;
  onSelectGroup: (groupId: string | null) => void;
  onUpdateGroup: (groupId: string, patch: Partial<CaptionGroup>, commitToHistory?: boolean) => void;
  onUpdateTrack?: (track: CaptionTrack, commitToHistory?: boolean) => void;
  onClose: () => void;
}

export const TranscriptEditor: React.FC<TranscriptEditorProps> = ({
  track,
  currentTime,
  selectedGroupId,
  onSeek,
  onSelectGroup,
  onUpdateGroup,
  onUpdateTrack,
  onClose,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [replaceQuery, setReplaceQuery] = useState('');
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null);
  const [editDraftText, setEditDraftText] = useState('');
  const [feedback, setFeedback] = useState<string | null>(null);

  const captionGroups = useMemo(() => {
    if (!track || !track.groups) return [];
    return track.groups
      .filter((g) => !g.type || g.type === 'caption' || g.type === 'text')
      .sort((a, b) => a.start - b.start);
  }, [track]);

  // Search matches calculation with safe literal regex matching
  const searchMatches = useMemo(() => {
    if (!searchQuery.trim()) return [];
    const query = searchQuery.trim().toLowerCase();
    const matches: { groupId: string; wordIdx: number; text: string }[] = [];

    captionGroups.forEach((grp) => {
      (grp.words || []).forEach((w, wIdx) => {
        if (w.text.toLowerCase().includes(query)) {
          matches.push({ groupId: grp.id, wordIdx: wIdx, text: w.text });
        }
      });
    });

    return matches;
  }, [captionGroups, searchQuery]);

  const handleStartEdit = (grp: CaptionGroup) => {
    setEditingGroupId(grp.id);
    const fullText = (grp.words || []).map((w) => w.text).join(' ');
    setEditDraftText(fullText);
  };

  const handleSaveEdit = (grp: CaptionGroup) => {
    const updatedWords = preserveWordTimingsOnEdit(
      grp.words || [],
      editDraftText,
      grp.start,
      grp.end,
      grp.id
    );

    onUpdateGroup(grp.id, { words: updatedWords }, true);
    setEditingGroupId(null);
    setFeedback('Segment updated with authentic word timestamps preserved.');
    setTimeout(() => setFeedback(null), 2500);
  };

  const handleBatchReplace = () => {
    if (!searchQuery.trim() || !track) return;
    const findStr = searchQuery.trim();
    const repStr = replaceQuery.trim();

    // Literal text matching: escape all regex characters so '.', '(', etc. match literally
    const escaped = escapeRegex(findStr);
    const regex = new RegExp(escaped, 'gi');

    let totalReplacements = 0;
    const affectedGroupIds = new Set<string>();

    const updatedGroups = track.groups.map((grp) => {
      if (grp.type && grp.type !== 'caption' && grp.type !== 'text') {
        return grp;
      }
      const origText = (grp.words || []).map((w) => w.text).join(' ');
      if (regex.test(origText)) {
        regex.lastIndex = 0;
        const nextText = origText.replace(regex, repStr);
        const updatedWords = preserveWordTimingsOnEdit(
          grp.words || [],
          nextText,
          grp.start,
          grp.end,
          grp.id
        );
        totalReplacements++;
        affectedGroupIds.add(grp.id);
        return {
          ...grp,
          words: updatedWords,
        };
      }
      return grp;
    });

    if (totalReplacements === 0) {
      setFeedback('No occurrences found to replace.');
      setTimeout(() => setFeedback(null), 2500);
      return;
    }

    // Atomic history commit: single undo step for the entire batch replace operation
    if (onUpdateTrack) {
      onUpdateTrack({ ...track, groups: updatedGroups }, true);
    } else {
      // Fallback: update groups without flooding history, committing on the final group
      const affectedList = Array.from(affectedGroupIds);
      affectedList.forEach((gid, idx) => {
        const grp = updatedGroups.find((g) => g.id === gid);
        if (grp) {
          const isLast = idx === affectedList.length - 1;
          onUpdateGroup(gid, { words: grp.words }, isLast);
        }
      });
    }

    setFeedback(`Replaced ${searchMatches.length} occurrences across ${totalReplacements} segments.`);
    setTimeout(() => setFeedback(null), 3000);
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex flex-col justify-end sm:justify-center items-center p-0 sm:p-4">
      <div className="w-full max-w-xl h-[88vh] sm:h-[80vh] bg-surface-100 border border-border rounded-t-2xl sm:rounded-2xl flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <header className="px-4 py-3 border-b border-border flex items-center justify-between bg-surface-200/50">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-primary" />
            <h2 className="text-sm font-semibold text-text-main">Transcript Editor</h2>
            <span className="text-[11px] font-mono text-text-muted">
              ({captionGroups.length} segments)
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-text-muted hover:text-text-main hover:bg-surface-200 active:scale-95 transition-all"
          >
            <X size={16} />
          </button>
        </header>

        {/* Search & Replace Toolbar */}
        <section className="p-3 border-b border-border bg-surface-200/30 space-y-2">
          <div className="flex items-center gap-2">
            <div className="flex-1 relative flex items-center">
              <Search size={14} className="absolute left-2.5 text-text-muted pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Find word or phrase (Hinglish/English)…"
                className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-surface-200 border border-border text-xs text-text-main focus:outline-none focus:border-primary"
              />
              {searchMatches.length > 0 && (
                <span className="absolute right-2.5 text-[10px] font-mono text-primary font-bold">
                  {searchMatches.length} matches
                </span>
              )}
            </div>
            {searchQuery && (
              <div className="flex-1 relative flex items-center">
                <Replace size={14} className="absolute left-2.5 text-text-muted pointer-events-none" />
                <input
                  type="text"
                  value={replaceQuery}
                  onChange={(e) => setReplaceQuery(e.target.value)}
                  placeholder="Replace with…"
                  className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-surface-200 border border-border text-xs text-text-main focus:outline-none focus:border-primary"
                />
              </div>
            )}
            {searchQuery && (
              <button
                type="button"
                onClick={handleBatchReplace}
                className="h-[34px] px-3 rounded-lg bg-primary hover:bg-primary-hover text-white text-xs font-semibold shrink-0 active:scale-95 transition-all"
              >
                Replace All
              </button>
            )}
          </div>
          {feedback && (
            <p className="text-[11px] font-medium text-primary animate-fade-in">{feedback}</p>
          )}
        </section>

        {/* Transcript Segments List */}
        <div className="flex-1 overflow-y-auto p-3 space-y-2.5 divide-y divide-border/30">
          {captionGroups.map((grp) => {
            const isSelected = selectedGroupId === grp.id;
            const isPlayingNow = currentTime >= grp.start && currentTime <= grp.end;
            const isEditing = editingGroupId === grp.id;

            return (
              <div
                key={grp.id}
                className={`pt-2.5 first:pt-0 rounded-xl p-2.5 transition-all ${
                  isSelected
                    ? 'bg-primary/10 border border-primary/40'
                    : isPlayingNow
                    ? 'bg-surface-200/60 border border-border'
                    : 'hover:bg-surface-200/30'
                }`}
              >
                {/* Segment Meta */}
                <div className="flex items-center justify-between mb-1.5">
                  <button
                    type="button"
                    onClick={() => {
                      onSeek(grp.start);
                      onSelectGroup(grp.id);
                    }}
                    className="flex items-center gap-1.5 text-[11px] font-mono text-text-muted hover:text-primary transition-colors"
                  >
                    <Play size={10} className={isPlayingNow ? 'text-primary fill-primary' : ''} />
                    <span>
                      {grp.start.toFixed(2)}s &ndash; {grp.end.toFixed(2)}s
                    </span>
                    <span className="text-[10px] text-text-muted">
                      ({(grp.end - grp.start).toFixed(1)}s)
                    </span>
                  </button>

                  <div className="flex items-center gap-1">
                    {!isEditing ? (
                      <button
                        type="button"
                        onClick={() => handleStartEdit(grp)}
                        className="px-2 py-0.5 rounded text-[11px] text-text-secondary hover:text-text-main hover:bg-surface-200 flex items-center gap-1 transition-colors"
                      >
                        <Edit2 size={11} />
                        <span>Edit</span>
                      </button>
                    ) : (
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => handleSaveEdit(grp)}
                          className="px-2 py-0.5 rounded bg-primary text-white text-[11px] font-semibold flex items-center gap-1 shadow-sm active:scale-95"
                        >
                          <Check size={11} />
                          <span>Done</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingGroupId(null)}
                          className="px-2 py-0.5 rounded bg-surface-200 text-text-muted hover:text-text-main text-[11px]"
                        >
                          Cancel
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                {/* Content */}
                {isEditing ? (
                  <textarea
                    rows={2}
                    value={editDraftText}
                    onChange={(e) => setEditDraftText(e.target.value)}
                    className="w-full p-2 rounded-lg bg-surface-200 border border-primary text-xs text-text-main font-sans focus:outline-none resize-none"
                    placeholder="Edit caption words…"
                  />
                ) : (
                  <p
                    onClick={() => {
                      onSeek(grp.start);
                      onSelectGroup(grp.id);
                    }}
                    className="text-xs leading-relaxed text-text-main cursor-pointer select-text"
                  >
                    {(grp.words || []).map((w, idx) => {
                      const isWordActive = currentTime >= w.start && currentTime <= w.end;
                      const isSearchMatch =
                        searchQuery && w.text.toLowerCase().includes(searchQuery.toLowerCase());

                      return (
                        <span
                          key={w.id || idx}
                          onClick={(e) => {
                            e.stopPropagation();
                            onSeek(w.start);
                            onSelectGroup(grp.id);
                          }}
                          className={`inline-block mr-1.5 px-1 py-0.5 rounded cursor-pointer transition-colors ${
                            isWordActive
                              ? 'bg-primary text-white font-bold'
                              : isSearchMatch
                              ? 'bg-yellow-400/30 text-yellow-300 font-semibold underline'
                              : 'hover:bg-surface-300 text-text-main'
                          }`}
                          title={`Word: "${w.text}" (${w.start.toFixed(2)}s - ${w.end.toFixed(2)}s)`}
                        >
                          {w.text}
                        </span>
                      );
                    })}
                  </p>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <footer className="p-3 border-t border-border bg-surface-200/50 flex items-center justify-between text-xs text-text-muted">
          <span>Tap any word to jump playhead to its start time</span>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg bg-surface-200 hover:bg-surface-300 text-text-main text-xs font-semibold active:scale-95 transition-all"
          >
            Done
          </button>
        </footer>
      </div>
    </div>
  );
};
