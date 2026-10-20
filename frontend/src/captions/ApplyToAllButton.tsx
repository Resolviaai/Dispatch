import React, { useState } from 'react';
import { CheckCheck } from 'lucide-react';
import { StyleSection } from './applyToAll';

export interface ApplyToAllButtonProps {
  section: StyleSection;
  sectionLabel: string;
  onApply: (section: StyleSection) => number;
  className?: string;
  label?: string;
  successLabel?: string;
}

export const ApplyToAllButton: React.FC<ApplyToAllButtonProps> = ({
  section,
  sectionLabel,
  onApply,
  className = '',
  label,
  successLabel,
}) => {
  const [appliedCount, setAppliedCount] = useState<number | null>(null);

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    const count = onApply(section);
    setAppliedCount(count);
    setTimeout(() => {
      setAppliedCount(null);
    }, 1800);
  };

  const isApplied = appliedCount !== null;

  return (
    <div className={`pt-2.5 pb-1 ${className}`}>
      <button
        type="button"
        onClick={handleClick}
        className={`w-full h-10 px-3 rounded-xl border flex items-center justify-center gap-2 text-xs font-semibold transition-all active:scale-[0.98] ${
          isApplied
            ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-400 shadow-sm'
            : 'bg-surface-200 border-border text-text-secondary hover:text-text-main hover:bg-surface-300'
        }`}
        title={`Apply ${sectionLabel} styling to all eligible captions in this clip`}
      >
        {isApplied ? (
          <>
            <CheckCheck size={15} className="text-emerald-400 shrink-0" />
            <span className="truncate">
              {successLabel || (label ? `Applied to all (${appliedCount})` : `Applied ${sectionLabel} to all (${appliedCount})`)}
            </span>
          </>
        ) : (
          <>
            <CheckCheck size={14} className="text-text-muted shrink-0" />
            <span className="truncate">{label || `Apply ${sectionLabel} to All Text`}</span>
          </>
        )}
      </button>
    </div>
  );
};
