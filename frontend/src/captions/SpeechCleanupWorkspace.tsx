import React, { useState, useMemo } from 'react';
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
import { MiniFilmstrip } from './MiniFilmstrip';

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
  duration: number;
  clipStart?: number;
  clipEnd?: number;
  onPreviewContext: (range: { start: number; end: number }) => void;
  onSeekSourceTime?: (sourceTime: number) => void;
  onToggleDecision: (proposalId: string, choice: 'cut' | 'keep') => void;
  onApply: () => void;
  onClose: () => void;
  isApplying?: boolean;
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

export const SpeechCleanupWorkspace: React.FC<SpeechCleanupWorkspaceProps> = ({
  proposals,
  filter,
  onFilterChange,
  filmstrip,
  currentTime,
  duration,
  clipStart = 0,
  clipEnd = 60,
  onPreviewContext,
  onSeekSourceTime,
  onToggleDecision,
  onApply,
  onClose,
  isApplying = false,
}) => {
  // Only ONE detection may be expanded at a time
  const [expandedId, setExpandedId] = useState<string | null>(null);

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

  return (
    <div className="w-full h-full flex flex-col bg-surface border-t border-border text-text-main select-none overflow-hidden">
      {/* 1. COMPACT HEADER: Category Filters + Close (Unified single row) */}
      <header className="shrink-0 h-11 px-3 border-b border-border bg-surface flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar min-w-0">
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
                className={`h-7 px-2.5 rounded-lg text-xs font-medium flex items-center gap-1 transition-all shrink-0 active:scale-[0.98] border ${
                  isActive
                    ? 'bg-primary text-white border-primary shadow-hero-sm font-semibold'
                    : 'bg-surface-2 text-text-secondary hover:text-text-main hover:bg-surface-3 border-border'
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

        <button
          type="button"
          onClick={onClose}
          aria-label="Close Speech Cleanup"
          className="w-7 h-7 rounded-lg bg-surface-2 hover:bg-surface-3 border border-border flex items-center justify-center text-text-secondary hover:text-text-main active:scale-[0.95] transition-all shrink-0"
        >
          <X size={14} />
        </button>
      </header>

      {/* 2. COMPACT DETECTION LIST */}
      <div className="flex-1 min-h-0 overflow-y-auto divide-y divide-border">
        {filteredProposals.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-8 text-center text-text-muted text-xs">
            <p>No detections match the selected filter.</p>
          </div>
        ) : (
          filteredProposals.map((prop) => {
            const isExpanded = expandedId === prop.id;
            const effective = getEffectiveDecision(prop);
            const { title, icon } = getProposalPresentation(prop);

            return (
              <div
                key={prop.id}
                className={`transition-colors ${
                  isExpanded ? 'bg-surface-2/40' : 'hover:bg-surface-2/20'
                }`}
              >
                {/* Collapsed Row Summary / Header Trigger */}
                <div
                  onClick={() => setExpandedId(isExpanded ? null : prop.id)}
                  className="min-h-[44px] px-3 py-1.5 flex items-center justify-between gap-3 cursor-pointer select-none active:bg-surface-2/60 transition-colors"
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setExpandedId(isExpanded ? null : prop.id);
                    }
                  }}
                  aria-expanded={isExpanded}
                >
                  {/* Left Column: Type Title + Timestamp Range */}
                  <div className="flex flex-col gap-0.5 min-w-0">
                    <div className="flex items-center gap-1.5">
                      {icon}
                      <span className="text-sm font-medium text-text-main truncate">
                        {title}
                      </span>
                    </div>
                    <span className="text-xs font-mono tabular-nums text-text-muted">
                      {formatCutTimestamp(prop.source_start)} &ndash;{' '}
                      {formatCutTimestamp(prop.source_end)}
                    </span>
                  </div>

                  {/* Right Column: Duration + Decision Pill + Chevron */}
                  <div className="flex items-center gap-2.5 shrink-0">
                    <span className="font-mono tabular-nums text-sm font-semibold text-text-main">
                      {formatCutDuration(prop.duration)}
                    </span>

                    {/* Compact Decision Status Pill */}
                    <span
                      className={`text-xs px-2 py-0.5 rounded font-medium border ${
                        effective === 'cut'
                          ? 'bg-primary/15 text-primary border-primary/25'
                          : effective === 'keep'
                          ? 'bg-surface-3 text-text-muted border-border'
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
                        <ChevronUp size={16} />
                      ) : (
                        <ChevronDown size={16} />
                      )}
                    </div>
                  </div>
                </div>

                {/* 3. EXPANDED INSPECTION INTERACTION */}
                {isExpanded && (
                  <div className="px-3 pb-3 pt-1 space-y-2.5 bg-surface-2/20 border-t border-border/60 animate-in fade-in duration-150">
                    {/* Miniature Filmstrip (Captions Reference) */}
                    <MiniFilmstrip
                      filmstrip={filmstrip}
                      clipDuration={duration}
                      sourceStart={prop.source_start}
                      sourceEnd={prop.source_end}
                      currentTime={currentTime}
                      clipStartOffset={clipStart}
                      onSeek={onSeekSourceTime}
                    />

                    {/* Action Controls Row: Single Toggle Decision + Contextual Preview */}
                    <div className="flex items-center justify-between gap-3 pt-0.5">
                      {/* Contextual Preview Button */}
                      <button
                        type="button"
                        onClick={() =>
                          onPreviewContext({
                            start: Math.max(0, prop.source_start - 0.5),
                            end: Math.min(clipEnd || duration || 60, prop.source_end + 0.5),
                          })
                        }
                        className="min-h-[38px] px-3.5 rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-xs font-medium text-text-main flex items-center gap-1.5 active:scale-[0.98] transition-all"
                        title="Preview cut in context"
                      >
                        <Play size={12} className="fill-current text-primary" />
                        <span>Preview</span>
                      </button>

                      {/* Single Decision Toggle: Cut vs Keep */}
                      <button
                        type="button"
                        onClick={() => onToggleDecision(prop.id, effective === 'cut' ? 'keep' : 'cut')}
                        className={`min-h-[38px] px-4 rounded-xl text-xs font-semibold flex items-center gap-1.5 active:scale-[0.98] transition-all border ${
                          effective === 'cut'
                            ? 'bg-primary text-white border-primary shadow-hero-glow'
                            : 'bg-surface-2 text-text-secondary hover:text-text-main border-border'
                        }`}
                        title={effective === 'cut' ? 'Marked for removal. Click to keep.' : 'Kept. Click to cut.'}
                      >
                        {effective === 'cut' ? (
                          <>
                            <Scissors size={13} />
                            <span>Cut</span>
                          </>
                        ) : (
                          <>
                            <Check size={13} />
                            <span>Keep</span>
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* 4. STICKY BOTTOM ACTION BAR */}
      <footer className="px-3 py-2 border-t border-border bg-surface flex items-center gap-2.5 shrink-0 pb-[max(8px,env(safe-area-inset-bottom,0px))]">
        <button
          type="button"
          onClick={onClose}
          className="flex-1 min-h-[40px] rounded-xl bg-surface-2 hover:bg-surface-3 border border-border text-text-main text-sm font-medium active:scale-[0.98] transition-all"
        >
          Cancel
        </button>

        <button
          type="button"
          onClick={onApply}
          disabled={eligibleCutCount === 0 || isApplying}
          className="flex-2 min-h-[40px] px-4 rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 active:scale-[0.98] transition-all shadow-hero-glow disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {isApplying ? (
            <RotateCw size={14} className="animate-spin" />
          ) : (
            <Scissors size={14} />
          )}
          <span>Apply Auto-Cut ({eligibleCutCount})</span>
        </button>
      </footer>
    </div>
  );
};
