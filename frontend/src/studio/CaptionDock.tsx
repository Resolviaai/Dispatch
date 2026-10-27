import React, { useState, useRef, useMemo } from 'react';
import {
  ZoomIn,
  ZoomOut,
  Type,
  Check,
  Edit2,
  Clock,
  Sparkles,
  Layers,
  RotateCw,
} from 'lucide-react';
import { CaptionTrack, CaptionGroup, Word } from '../captions/types';

interface CaptionDockProps {
  track: CaptionTrack | null;
  currentTime: number;
  duration: number;
  onSeek: (seconds: number) => void;
  onUpdateTrack: (track: CaptionTrack) => void;
  selectedGroupId: string | null;
  onSelectGroup: (groupId: string | null) => void;
  captionLoadState?: 'idle' | 'loading' | 'success' | 'empty' | 'error';
  captionLoadError?: string | null;
  onRetryCaptions?: () => void;
}

export const CaptionDock: React.FC<CaptionDockProps> = ({
  track,
  currentTime,
  duration,
  onSeek,
  onUpdateTrack,
  selectedGroupId,
  onSelectGroup,
  captionLoadState = 'idle',
  captionLoadError,
  onRetryCaptions,
}) => {
  const [zoom, setZoom] = useState<number>(65); // px per second
  const [editingWordId, setEditingWordId] = useState<string | null>(null);
  const [editText, setEditText] = useState<string>('');
  const containerRef = useRef<HTMLDivElement>(null);

  const safeDuration = Math.max(1, duration || 30);
  const totalWidth = Math.max(800, Math.round(safeDuration * zoom));

  // Time ruler ticks (every 5 seconds)
  const ticks = useMemo(() => {
    const list: number[] = [];
    for (let t = 0; t <= safeDuration; t += 5) {
      list.push(t);
    }
    return list;
  }, [safeDuration]);

  // Handle word text edit commit
  const handleSaveWordText = (groupId: string, wordId: string) => {
    if (!track) return;
    const updatedGroups = track.groups.map(group => {
      if (group.id !== groupId) return group;
      const updatedWords = group.words.map(w => {
        if (w.id === wordId) {
          return { ...w, text: editText.trim() };
        }
        return w;
      });
      return { ...group, words: updatedWords };
    });

    onUpdateTrack({
      ...track,
      groups: updatedGroups,
    });
    setEditingWordId(null);
  };

  const handleStartEditWord = (word: Word, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingWordId(word.id);
    setEditText(word.text);
  };

  const handleRulerClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const targetTime = Math.max(0, Math.min(safeDuration, clickX / zoom));
    onSeek(targetTime);
  };

  return (
    <div className="h-[175px] min-h-[175px] max-h-[175px] w-full bg-[#16181C] border-t border-[#2E3239] flex flex-col select-none shrink-0 z-30">
      {/* TIMELINE TOOLBAR */}
      <div className="h-8 px-4 bg-[#1C1F24] border-b border-[#2E3239] flex items-center justify-between text-xs text-[#A3AAB5] shrink-0">
        <div className="flex items-center gap-2">
          <Type size={13} className="text-[#2563EB]" />
          <span className="text-[11px] font-bold uppercase tracking-wider text-[#F3F4F6]">
            Caption Timeline (Whisper Timestamps)
          </span>
          <span className="text-[10px] font-mono text-[#747D8A]">
            {track?.groups?.length || 0} phrase segments
          </span>
        </div>

        {/* Zoom Controls */}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setZoom(prev => Math.max(30, prev - 15))}
            className="p-1 rounded text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#23262C] transition-all"
            title="Zoom Out Timeline"
          >
            <ZoomOut size={13} />
          </button>
          <span className="text-[10px] font-mono text-[#747D8A] w-8 text-center">
            {zoom}px
          </span>
          <button
            type="button"
            onClick={() => setZoom(prev => Math.min(160, prev + 15))}
            className="p-1 rounded text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#23262C] transition-all"
            title="Zoom In Timeline"
          >
            <ZoomIn size={13} />
          </button>
        </div>
      </div>

      {/* HORIZONTAL TIMELINE SCROLL AREA */}
      {captionLoadState === 'loading' && (
        <div role="status" className="h-7 shrink-0 px-3 flex items-center gap-2 bg-[#1C1F24] border-b border-[#2E3239] text-[#A3AAB5] text-xs">
          <RotateCw size={12} className="animate-spin text-primary" />
          <span>Loading captions…</span>
        </div>
      )}
      {captionLoadState === 'error' && (
        <div role="alert" className="min-h-11 shrink-0 px-3 flex items-center justify-between gap-2 bg-[#1C1F24] border-b border-[#2E3239]">
          <span className="text-xs text-[#A3AAB5] min-w-0">{captionLoadError || 'Captions unavailable.'}</span>
          <button
            type="button"
            onClick={onRetryCaptions}
            className="min-h-11 px-3 rounded-lg bg-[#23262C] border border-[#2E3239] text-xs font-medium text-[#F3F4F6] hover:bg-[#2E3239] active:scale-[0.98] transition-all shrink-0"
          >
            Retry captions
          </button>
        </div>
      )}
      <div
        ref={containerRef}
        className="flex-1 min-h-0 overflow-x-auto overflow-y-hidden relative bg-[#16181C]"
      >
        <div style={{ width: `${totalWidth}px` }} className="h-full relative flex flex-col">
          {/* TIME RULER */}
          <div
            onClick={handleRulerClick}
            className="h-6 w-full border-b border-[#242830] bg-[#1C1F24]/50 relative cursor-pointer"
          >
            {ticks.map((t) => (
              <div
                key={t}
                style={{ left: `${t * zoom}px` }}
                className="absolute top-0 h-full flex flex-col justify-between border-l border-[#2E3239] pl-1"
              >
                <span className="text-[9px] font-mono tabular-nums text-[#747D8A]">
                  {Math.floor(t / 60)}:{(t % 60).toString().padStart(2, '0')}
                </span>
                <div className="w-px h-1.5 bg-[#2E3239]" />
              </div>
            ))}
          </div>

          {/* WORD BLOCKS CANVAS */}
          <div className="flex-1 relative p-2 overflow-hidden">
            {track?.groups?.map((group) => {
              const isGroupActive = currentTime >= group.start && currentTime <= group.end;
              const isGroupSelected = selectedGroupId === group.id;

              return (
                <div
                  key={group.id}
                  style={{
                    left: `${group.start * zoom}px`,
                    width: `${Math.max(40, (group.end - group.start) * zoom)}px`,
                  }}
                  onClick={() => {
                    onSelectGroup(group.id);
                    onSeek(group.start);
                  }}
                  className={`absolute top-2 h-14 rounded-lg border p-1.5 flex flex-wrap items-center gap-1 cursor-pointer transition-all ${
                    isGroupActive
                      ? 'bg-[#2563EB]/20 border-[#2563EB] shadow-hero-sm z-10'
                      : isGroupSelected
                      ? 'bg-[#1C1F24] border-[#2563EB]/60 z-10'
                      : 'bg-[#23262C] border-[#2E3239] hover:border-[#3E434D]'
                  }`}
                  title={`[${group.start.toFixed(1)}s - ${group.end.toFixed(1)}s] Click to seek`}
                >
                  {group.words.map((w) => {
                    const isWordActive = currentTime >= w.start && currentTime <= w.end;
                    const isEditing = editingWordId === w.id;

                    if (isEditing) {
                      return (
                        <div
                          key={w.id}
                          className="flex items-center gap-0.5 bg-[#0D0F12] border border-[#2563EB] rounded px-1 py-0.5 z-20"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <input
                            type="text"
                            autoFocus
                            value={editText}
                            onChange={(e) => setEditText(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') handleSaveWordText(group.id, w.id);
                              if (e.key === 'Escape') setEditingWordId(null);
                            }}
                            className="bg-transparent text-xs text-[#F3F4F6] w-20 focus:outline-none"
                          />
                          <button
                            type="button"
                            onClick={() => handleSaveWordText(group.id, w.id)}
                            className="text-[#10B981] hover:text-white"
                          >
                            <Check size={12} />
                          </button>
                        </div>
                      );
                    }

                    return (
                      <span
                        key={w.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          onSeek(w.start);
                        }}
                        onDoubleClick={(e) => handleStartEditWord(w, e)}
                        className={`text-[11px] font-medium px-1.5 py-0.5 rounded transition-all cursor-pointer ${
                          isWordActive
                            ? 'bg-[#FFE600] text-black font-bold shadow-sm scale-105'
                            : 'text-[#F3F4F6] hover:bg-[#1C1F24] hover:text-[#60A5FA]'
                        }`}
                        title={`${w.text} (${w.start.toFixed(2)}s - ${w.end.toFixed(2)}s) · Double-click to edit text`}
                      >
                        {w.text}
                      </span>
                    );
                  })}
                </div>
              );
            })}
          </div>

          {/* PLAYHEAD INDICATOR */}
          <div
            style={{ left: `${currentTime * zoom}px` }}
            className="absolute top-0 bottom-0 w-px bg-[#F43F5E] shadow-[0_0_8px_#F43F5E] pointer-events-none z-30"
          >
            <div className="w-2.5 h-2.5 -ml-1 rounded-full bg-[#F43F5E] shadow-sm" />
          </div>
        </div>
      </div>
    </div>
  );
};
