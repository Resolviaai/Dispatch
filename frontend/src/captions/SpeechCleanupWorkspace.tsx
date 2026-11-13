import React, { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import {
  Scissors,
  X,
  Play,
  Check,
  ChevronDown,
  ChevronUp,
  RotateCw,
  VolumeX,
  RotateCcw,
  Pause,
  Wind,
  AlertCircle,
  Zap,
} from 'lucide-react';
import { FilmstripData } from './filmstrip';
import { MiniFilmstrip, computeContextWindow } from './MiniFilmstrip';

export interface ContextPreviewRange {
  start: number;
  end: number;
  cutStart?: number;
  cutEnd?: number;
  isCut?: boolean;
  proposalId?: string;
}

export interface AutoCutProposal {
  id: string;
  type:
    | 'filler'
    | 'filler_word'
    | 'stutter'
    | 'false_start'
    | 'dead_air'
    | 'breath'
    | 'cough'
    | 'audio_defect'
    | 'repetition'
    | string;
  source_start: number;
  source_end: number;
  duration: number;
  reason: string;
  confidence: number;
  decision: 'AUTO-REMOVE' | 'REVIEW' | 'PRESERVE';
  userOverride?: 'accept' | 'reject';
  word?: string;
}

export type AutoCutFilterCategory = 'all' | 'AUTO-REMOVE' | 'REVIEW' | 'PRESERVE';
export type EffectiveDecision = 'cut' | 'keep' | 'review';

export interface SpeechCleanupWorkspaceProps {
  proposals: AutoCutProposal[];
  filter: AutoCutFilterCategory;
  onFilterChange: (newFilter: AutoCutFilterCategory) => void;
  filmstrip: FilmstripData | null;
  currentTime: number;
  sourceTime?: number;
  duration: number;
  clipStart?: number;
  clipEnd?: number;
  isPlaying?: boolean;
  previewingProposalId?: string | null;
  onPreviewContext: (range: ContextPreviewRange) => void;
  onSeekSourceTime?: (sourceTime: number) => void;
  onToggleDecision: (proposalId: string, choice: 'cut' | 'keep') => void;
  onUpdateProposalRange?: (proposalId: string, newStart: number, newEnd: number) => void;
  waveformSamples?: Array<{ time: number; amplitude: number }>;
  transcriptWords?: Array<{ start: number; end: number }>;
  onApply: () => void;
  onClose: () => void;
  isApplying?: boolean;
  isLoading?: boolean;
  onReanalyze?: () => void;
}

export function formatCutTimestamp(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '00:00.00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const cs = Math.floor((seconds % 1) * 100);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${cs.toString().padStart(2, '0')}`;
}

export function formatCutDuration(durationSeconds: number): string {
  const d = Math.abs(durationSeconds);
  return `\u2212${d.toFixed(1)}s`;
}

export function computeTranscriptSignature(
  clipId: string,
  clipStart: number,
  clipEnd: number,
  words: Array<{ start: number; end: number; text?: string }>
): string {
  const len = words.length;
  const first = len > 0 ? Number(words[0]?.start ?? 0).toFixed(2) : '0';
  const last = len > 0 ? Number(words[len - 1]?.end ?? 0).toFixed(2) : '0';
  return `${clipId}:${clipStart.toFixed(2)}-${clipEnd.toFixed(2)}:${len}:${first}-${last}`;
}

export function readAutoCutCache(
  clipId: string,
  signature: string
): AutoCutProposal[] | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    const raw = localStorage.getItem(`dispatch_autocut_${clipId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.signature === signature && Array.isArray(parsed.proposals)) {
      return parsed.proposals;
    }
  } catch (err) {
    console.warn('Failed to read AutoCut cache:', err);
  }
  return null;
}

export function writeAutoCutCache(
  clipId: string,
  signature: string,
  proposals: AutoCutProposal[]
): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(
      `dispatch_autocut_${clipId}`,
      JSON.stringify({ signature, proposals, timestamp: Date.now() })
    );
  } catch (err) {
    console.warn('Failed to write AutoCut cache:', err);
  }
}

export function clearAutoCutCache(clipId: string): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.removeItem(`dispatch_autocut_${clipId}`);
  } catch (err) {
    console.warn('Failed to clear AutoCut cache:', err);
  }
}

export function getEffectiveDecision(proposal: AutoCutProposal): EffectiveDecision {
  if (proposal.userOverride === 'accept') {
    return 'cut';
  }
  if (proposal.userOverride === 'reject') {
    return 'keep';
  }
  if (proposal.decision === 'AUTO-REMOVE') {
    return 'cut';
  }
  if (proposal.decision === 'PRESERVE') {
    return 'keep';
  }
  return 'review';
}

export function getProposalPresentation(proposal: AutoCutProposal): {
  title: string;
  icon: React.ReactNode;
} {
  const rawType = (proposal.type || 'filler').toLowerCase();
  const reason = proposal.reason || '';

  if (rawType === 'dead_air' || rawType === 'pause') {
    return {
      title: 'Pause',
      icon: <Pause size={13} className="text-text-muted" />,
    };
  }

  if (rawType === 'filler' || rawType === 'filler_word') {
    const wordMatch =
      proposal.word ||
      reason.match(/'([^']+)'/)?.[1] ||
      reason.match(/"([^"]+)"/)?.[1];
    const wordLabel = wordMatch ? ` "${wordMatch}"` : '';
    return {
      title: `Filler${wordLabel}`,
      icon: <VolumeX size={13} className="text-text-muted" />,
    };
  }

  if (
    rawType === 'false_start' ||
    rawType === 'stutter' ||
    rawType === 'repetition'
  ) {
    return {
      title: 'False start',
      icon: <RotateCcw size={13} className="text-text-muted" />,
    };
  }

  if (rawType === 'breath') {
    return {
      title: 'Breath',
      icon: <Wind size={13} className="text-text-muted" />,
    };
  }

  if (rawType === 'cough') {
    return {
      title: 'Cough',
      icon: <AlertCircle size={13} className="text-text-muted" />,
    };
  }

  if (rawType === 'audio_defect' || rawType === 'clipping') {
    return {
      title: 'Audio click',
      icon: <Zap size={13} className="text-text-muted" />,
    };
  }

  // Safe fallback for unknown future proposal types
  const formatted = rawType
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
  return {
    title: formatted,
    icon: <Scissors size={13} className="text-text-muted" />,
  };
}

interface DetectionCardProps {
  proposal: AutoCutProposal;
  isExpanded: boolean;
  onToggleExpand: (id: string) => void;
  filmstrip: FilmstripData | null;
  clipDuration: number;
  clipStart: number;
  clipEnd: number;
  currentTime: number;
  sourceTime?: number;
  previewingProposalId: string | null;
  onPreviewContext: (range: ContextPreviewRange) => void;
  onSeekSourceTime?: (sourceTime: number) => void;
  onToggleDecision: (proposalId: string, choice: 'cut' | 'keep') => void;
  onUpdateProposalRange?: (proposalId: string, newStart: number, newEnd: number) => void;
  waveformSamples?: Array<{ time: number; amplitude: number }>;
  transcriptWords?: Array<{ start: number; end: number }>;
}

const DetectionCard: React.FC<DetectionCardProps> = React.memo(
  ({
    proposal,
    isExpanded,
    onToggleExpand,
    filmstrip,
    clipDuration,
    clipStart,
    clipEnd,
    currentTime,
    sourceTime,
    previewingProposalId,
    onPreviewContext,
    onSeekSourceTime,
    onToggleDecision,
    onUpdateProposalRange,
    waveformSamples,
    transcriptWords,
  }) => {
    const effective = getEffectiveDecision(proposal);
    const { title, icon } = getProposalPresentation(proposal);
    const isPreviewingThis = previewingProposalId === proposal.id;

    return (
      <div
        id={`detection-card-${proposal.id}`}
        className={`transition-colors border-l-2 ${
          effective === 'cut'
            ? 'border-l-primary'
            : effective === 'keep'
            ? 'border-l-transparent'
            : 'border-l-amber-500/70'
        } ${isExpanded ? 'bg-surface-2/40' : 'hover:bg-surface-2/20'}`}
      >
        {/* Collapsed Row Summary / Header Trigger - min-h-[44px] touch target */}
        <div
          onClick={() => onToggleExpand(proposal.id)}
          className="min-h-[44px] px-3 py-1.5 flex items-center justify-between gap-2.5 cursor-pointer select-none active:bg-surface-2/70 active:scale-[0.99] transition-all"
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onToggleExpand(proposal.id);
            }
          }}
          aria-expanded={isExpanded}
        >
          {/* Left Column: Type Title + Timestamp Range */}
          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="flex items-center gap-1.5">
              {icon}
              <span className="text-xs font-medium text-text-main truncate">
                {title}
              </span>
            </div>
            <span className="text-[11px] font-mono tabular-nums text-text-muted">
              {formatCutTimestamp(proposal.source_start)} &ndash;{' '}
              {formatCutTimestamp(proposal.source_end)}
            </span>
          </div>

          {/* Right Column: Duration + Decision Pill + Chevron */}
          <div className="flex items-center gap-2 shrink-0">
            <span className="font-mono tabular-nums text-xs font-semibold text-text-main">
              {formatCutDuration(proposal.duration)}
            </span>

            {/* Compact Decision Status Pill */}
            <span
              className={`text-[10px] px-2 py-0.5 rounded font-medium border ${
                effective === 'cut'
                  ? 'bg-primary/15 text-primary border-primary/25'
                  : effective === 'keep'
                  ? 'bg-surface-3 text-text-muted border-white/[0.08]'
                  : 'bg-amber-500/15 text-amber-300 border-amber-500/25'
              }`}
            >
              {effective === 'cut'
                ? 'Cut'
                : effective === 'keep'
                ? 'Keep'
                : 'Review'}
            </span>

            {/* Expand/Collapse Chevron */}
            <div className="text-text-muted">
              {isExpanded ? (
                <ChevronUp size={14} />
              ) : (
                <ChevronDown size={14} />
              )}
            </div>
          </div>
        </div>

        {/* 3. EXPANDED INSPECTION INTERACTION */}
        {isExpanded && (
          <div className="px-3 pb-3 pt-1.5 space-y-2.5 bg-surface-2/20 border-t border-white/[0.08] animate-in fade-in duration-150">
            {/* Miniature Filmstrip (60:40 Video + Audio Waveform + Interactive Trimming Levers) */}
            <MiniFilmstrip
              filmstrip={filmstrip}
              clipDuration={clipDuration}
              sourceStart={proposal.source_start}
              sourceEnd={proposal.source_end}
              currentTime={currentTime}
              sourceTime={sourceTime}
              clipStartOffset={clipStart}
              onSeek={onSeekSourceTime}
              onUpdateRange={(newStart, newEnd) => onUpdateProposalRange?.(proposal.id, newStart, newEnd)}
              waveformSamples={waveformSamples}
              transcriptWords={transcriptWords}
            />

            {/* Action Controls Row: Single Toggle Decision + Contextual Jump-Cut Preview */}
            <div className="flex items-center justify-between gap-2.5 pt-0.5">
              {/* Contextual Jump-Cut Preview Button */}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  const win = computeContextWindow(
                    proposal.source_start,
                    proposal.source_end,
                    clipEnd || clipDuration || 60,
                    2.5
                  );
                  onPreviewContext({
                    start: win.winStart,
                    end: win.winEnd,
                    cutStart: proposal.source_start,
                    cutEnd: proposal.source_end,
                    isCut: effective === 'cut',
                    proposalId: proposal.id,
                  });
                }}
                className={`h-11 min-h-[44px] px-3.5 rounded-xl border text-xs font-semibold flex items-center gap-1.5 active:scale-[0.97] transition-all ${
                  isPreviewingThis
                    ? 'bg-primary/20 border-primary text-primary shadow-hero-sm font-semibold'
                    : 'bg-surface-2 hover:bg-surface-3 border-white/[0.08] text-text-main'
                }`}
                title={
                  isPreviewingThis
                    ? 'Stop context preview'
                    : 'Audition section (2.5s before & after with seamless jump-cut)'
                }
              >
                {isPreviewingThis ? (
                  <>
                    <span className="w-2 h-2 rounded-full bg-primary animate-ping mr-0.5" />
                    <span>Playing…</span>
                  </>
                ) : (
                  <>
                    <Play size={11} className="fill-current text-primary" />
                    <span>Preview</span>
                  </>
                )}
              </button>

              {/* Single Decision Toggle: Cut vs Keep */}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleDecision(proposal.id, effective === 'cut' ? 'keep' : 'cut');
                }}
                className={`h-11 min-h-[44px] px-4 rounded-xl text-xs font-semibold flex items-center gap-1.5 active:scale-[0.97] transition-all border ${
                  effective === 'cut'
                    ? 'bg-primary text-white border-primary shadow-hero-glow'
                    : 'bg-surface-2 text-text-secondary hover:text-text-main border-white/[0.08]'
                }`}
                title={effective === 'cut' ? 'Marked for removal. Click to keep.' : 'Kept. Click to cut.'}
              >
                {effective === 'cut' ? (
                  <>
                    <Scissors size={12} />
                    <span>Cut</span>
                  </>
                ) : (
                  <>
                    <Check size={12} />
                    <span>Keep</span>
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    );
  },
  (prev, next) => {
    // Strict memoization for 60fps playback smoothness:
    if (!prev.isExpanded && !next.isExpanded) {
      return (
        prev.proposal === next.proposal &&
        prev.previewingProposalId === next.previewingProposalId
      );
    }
    return (
      prev.proposal === next.proposal &&
      prev.isExpanded === next.isExpanded &&
      prev.currentTime === next.currentTime &&
      prev.previewingProposalId === next.previewingProposalId &&
      prev.filmstrip === next.filmstrip &&
      prev.clipDuration === next.clipDuration &&
      prev.clipEnd === next.clipEnd &&
      prev.waveformSamples === next.waveformSamples &&
      prev.transcriptWords === next.transcriptWords
    );
  }
);

export const SpeechCleanupWorkspace: React.FC<SpeechCleanupWorkspaceProps> = ({
  proposals,
  filter,
  onFilterChange,
  filmstrip,
  currentTime,
  sourceTime,
  duration,
  clipStart = 0,
  clipEnd = 60,
  isPlaying = false,
  previewingProposalId = null,
  onPreviewContext,
  onSeekSourceTime,
  onToggleDecision,
  onUpdateProposalRange,
  waveformSamples,
  transcriptWords,
  onApply,
  onClose,
  isApplying = false,
  isLoading = false,
  onReanalyze,
}) => {
  // Only ONE detection may be expanded at a time
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // 1. User manual interaction lock (prevents auto-follow from overriding user taps or trims)
  const userInteractingUntilRef = useRef<number>(0);

  const handleToggleExpand = useCallback((id: string) => {
    userInteractingUntilRef.current = Date.now() + 5000;
    setExpandedId((prev) => (prev === id ? null : id));
  }, []);

  const handleUpdateRangeWrapped = useCallback(
    (proposalId: string, newStart: number, newEnd: number) => {
      userInteractingUntilRef.current = Date.now() + 5000;
      onUpdateProposalRange?.(proposalId, newStart, newEnd);
    },
    [onUpdateProposalRange]
  );

  // Compute category counts
  const counts = useMemo(() => {
    return {
      all: proposals.length,
      'AUTO-REMOVE': proposals.filter((p) => p.decision === 'AUTO-REMOVE').length,
      REVIEW: proposals.filter((p) => p.decision === 'REVIEW').length,
      PRESERVE: proposals.filter((p) => p.decision === 'PRESERVE').length,
    };
  }, [proposals]);

  // Compute total cuts eligible to be applied
  const eligibleCutCount = useMemo(() => {
    return proposals.filter((p) => {
      const eff = getEffectiveDecision(p);
      return eff === 'cut';
    }).length;
  }, [proposals]);

  // Filtered proposal list
  const filteredProposals = useMemo(() => {
    if (filter === 'all') return proposals;
    return proposals.filter((p) => p.decision === filter);
  }, [proposals, filter]);

  // 2. Real-time timeline auto-follow during playback (with midpoint handoff between adjacent proposals)
  useEffect(() => {
    if (!isPlaying || Date.now() < userInteractingUntilRef.current || filteredProposals.length === 0) {
      return;
    }

    const effectiveSourceTime =
      sourceTime != null
        ? sourceTime
        : currentTime >= clipStart - 0.5 && currentTime <= clipEnd + 0.5
        ? currentTime
        : currentTime + clipStart;

    let targetId: string | null = null;

    for (let i = 0; i < filteredProposals.length; i++) {
      const p = filteredProposals[i];
      const prev = i > 0 ? filteredProposals[i - 1] : null;
      const next = i < filteredProposals.length - 1 ? filteredProposals[i + 1] : null;

      // Clean midpoint boundaries between adjacent proposals (zero flapping)
      const windowStart = prev
        ? Math.max(p.source_start - 2.5, (prev.source_end + p.source_start) / 2)
        : Math.max(clipStart, p.source_start - 2.5);

      const windowEnd = next
        ? Math.min(p.source_end + 1.5, (p.source_end + next.source_start) / 2)
        : Math.min(clipEnd, p.source_end + 1.5);

      if (effectiveSourceTime >= windowStart && effectiveSourceTime < windowEnd) {
        targetId = p.id;
        break;
      }
    }

    if (targetId !== expandedId) {
      setExpandedId(targetId);
    }
  }, [isPlaying, currentTime, sourceTime, clipStart, clipEnd, filteredProposals, expandedId]);

  // 3. Smooth auto-scroll when a proposal expands
  useEffect(() => {
    if (!expandedId) return;
    const el = document.getElementById(`detection-card-${expandedId}`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [expandedId]);

  return (
    <div
      onClick={(e) => e.stopPropagation()}
      className="w-full h-full flex flex-col bg-surface border-t border-white/[0.08] text-text-main select-none overflow-hidden"
    >
      {/* 1. COMPACT HEADER: Category Filters & Re-analyze Action */}
      <header className="shrink-0 h-9 px-2.5 border-b border-white/[0.08] bg-surface flex items-center justify-between gap-1.5">
        <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar flex-1 min-w-0">
          {(
            [
              { id: 'all', label: 'All', count: counts.all },
              { id: 'AUTO-REMOVE', label: 'Auto-cut', count: counts['AUTO-REMOVE'] },
              { id: 'REVIEW', label: 'Review', count: counts.REVIEW },
              { id: 'PRESERVE', label: 'Preserve', count: counts.PRESERVE },
            ] as const
          ).map((tab) => {
            const isActive = filter === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => onFilterChange(tab.id)}
                className={`h-6 px-2 rounded-lg text-[11px] font-medium flex items-center gap-1 transition-all shrink-0 active:scale-[0.98] border ${
                  isActive
                    ? 'bg-primary text-white border-primary shadow-hero-sm font-semibold'
                    : 'bg-surface-2 text-text-secondary hover:text-text-main hover:bg-surface-3 border-white/[0.08]'
                }`}
              >
                <span>{tab.label}</span>
                <span className="font-mono tabular-nums text-[10px] opacity-80">
                  ({tab.count})
                </span>
              </button>
            );
          })}
        </div>

        {onReanalyze && (
          <button
            type="button"
            onClick={onReanalyze}
            disabled={isLoading || isApplying}
            className="h-6 px-2 rounded-lg text-[11px] font-medium flex items-center gap-1 transition-all shrink-0 active:scale-[0.98] border border-white/[0.08] bg-surface-2 text-text-secondary hover:text-text-main hover:bg-surface-3 disabled:opacity-40"
            title="Re-analyze speech with Gemini Flash-Lite (bypass cache)"
            aria-label="Re-analyze speech"
          >
            <RotateCw size={11} className={isLoading ? 'animate-spin text-primary' : ''} />
            <span className="hidden xs:inline">Re-analyze</span>
          </button>
        )}
      </header>

      {/* 2. COMPACT DETECTION LIST */}
      <div className="flex-1 min-h-0 overflow-y-auto divide-y divide-white/[0.08]">
        {isLoading && filteredProposals.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-8 text-center text-text-muted gap-2 text-xs">
            <RotateCw size={18} className="animate-spin text-primary" />
            <span className="text-text-secondary font-medium">Analyzing speech with Gemini Flash-Lite…</span>
          </div>
        ) : filteredProposals.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-8 text-center text-text-muted text-xs gap-2">
            <p>No detections match the selected filter.</p>
            {onReanalyze && filter === 'all' && (
              <button
                type="button"
                onClick={onReanalyze}
                className="mt-1 h-7 px-3 rounded-lg border border-white/[0.08] bg-surface-2 hover:bg-surface-3 text-text-main text-xs font-medium flex items-center gap-1.5 active:scale-95 transition-all"
              >
                <RotateCw size={12} className={isLoading ? 'animate-spin' : ''} />
                <span>Run fresh analysis</span>
              </button>
            )}
          </div>
        ) : (
          filteredProposals.map((prop) => (
            <DetectionCard
              key={prop.id}
              proposal={prop}
              isExpanded={expandedId === prop.id}
              onToggleExpand={handleToggleExpand}
              filmstrip={filmstrip}
              // MiniFilmstrip computes its context window in source-absolute time,
              // so its clipDuration boundary must be the clip's source-space end.
              clipDuration={clipEnd}
              clipStart={clipStart}
              clipEnd={clipEnd}
              currentTime={currentTime}
              sourceTime={sourceTime}
              previewingProposalId={previewingProposalId}
              onPreviewContext={onPreviewContext}
              onSeekSourceTime={onSeekSourceTime}
              onToggleDecision={onToggleDecision}
              onUpdateProposalRange={handleUpdateRangeWrapped}
              waveformSamples={waveformSamples}
              transcriptWords={transcriptWords}
            />
          ))
        )}
      </div>

      {/* 4. STICKY BOTTOM ACTION BAR (Equal 50/50 CSS Grid - zero unevenness, 44px biomechanical hitboxes) */}
      <footer className="px-3 py-2 border-t border-white/[0.08] bg-surface grid grid-cols-2 gap-2.5 shrink-0 pb-[max(8px,env(safe-area-inset-bottom,0px))]">
        <button
          type="button"
          onClick={onClose}
          className="w-full h-11 min-h-[44px] px-3 rounded-xl bg-surface-2 hover:bg-surface-3 border border-white/[0.08] text-text-main text-xs font-semibold flex items-center justify-center active:scale-[0.98] transition-all"
        >
          Cancel
        </button>

        <button
          type="button"
          onClick={onApply}
          disabled={eligibleCutCount === 0 || isApplying}
          className="w-full h-11 min-h-[44px] px-3 rounded-xl bg-primary hover:bg-primary-hover text-white text-xs font-semibold flex items-center justify-center gap-1.5 active:scale-[0.98] transition-all shadow-hero-glow disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {isApplying ? (
            <RotateCw size={13} className="animate-spin" />
          ) : (
            <Scissors size={13} />
          )}
          <span>Apply Auto-Cut ({eligibleCutCount})</span>
        </button>
      </footer>
    </div>
  );
};
