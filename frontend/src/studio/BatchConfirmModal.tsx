import React from 'react';
import { X, CheckCircle2, AlertTriangle, Send, Trash2 } from 'lucide-react';
import { ClipItem } from './types';

interface BatchConfirmModalProps {
  isOpen: boolean;
  onClose: () => void;
  actionType: 'approve' | 'reject';
  selectedClips: ClipItem[];
  onConfirm: () => Promise<void>;
  isProcessing: boolean;
  publishMode: 'private' | 'public';
}

export const BatchConfirmModal: React.FC<BatchConfirmModalProps> = ({
  isOpen,
  onClose,
  actionType,
  selectedClips,
  onConfirm,
  isProcessing,
  publishMode,
}) => {
  if (!isOpen || selectedClips.length === 0) return null;

  const isApprove = actionType === 'approve';

  return (
    <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="w-full max-w-lg rounded-2xl bg-[#16181C] border border-[#2E3239] shadow-2xl overflow-hidden select-none">
        {/* Header */}
        <div className="px-4 py-3 bg-[#1C1F24] border-b border-[#2E3239] flex items-center justify-between">
          <div className="flex items-center gap-2">
            {isApprove ? (
              <CheckCircle2 size={16} className="text-[#2563EB]" />
            ) : (
              <AlertTriangle size={16} className="text-[#F43F5E]" />
            )}
            <h3 className="text-xs font-bold uppercase tracking-wider text-[#F3F4F6]">
              {isApprove ? `Batch Approve ${selectedClips.length} Clips` : `Batch Reject ${selectedClips.length} Clips`}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isProcessing}
            className="p-1 rounded text-[#747D8A] hover:text-[#F3F4F6] hover:bg-[#23262C]"
          >
            <X size={15} />
          </button>
        </div>

        {/* Body */}
        <div className="p-4 space-y-3">
          <p className="text-xs text-[#A3AAB5]">
            {isApprove
              ? `You are about to approve ${selectedClips.length} clips and enqueue them into the publishing outbox (${
                  publishMode === 'private' ? 'Safe Mode: Private Drafts' : 'Auto Public'
                }).`
              : `You are about to reject ${selectedClips.length} clips. They will be marked as rejected and removed from the active review queue.`}
          </p>

          {/* Affected Clips Preview */}
          <div className="max-h-48 overflow-y-auto rounded-lg bg-[#23262C] border border-[#2E3239] p-2 space-y-1">
            {selectedClips.map((c) => (
              <div
                key={c.id}
                className="flex items-center justify-between text-xs py-1 px-1.5 rounded bg-[#16181C]/70"
              >
                <span className="text-[#F3F4F6] truncate max-w-[280px]" title={c.title || c.hook}>
                  {c.title || c.hook || c.id}
                </span>
                <span className="font-mono text-[10px] text-[#747D8A]">
                  Score: {c.virality_score} · {Math.round(c.duration)}s
                </span>
              </div>
            ))}
          </div>

          {isApprove && (
            <div className="p-2.5 rounded-lg bg-[#2563EB]/10 border border-[#2563EB]/30 text-xs text-[#60A5FA] flex items-start gap-2">
              <Send size={14} className="shrink-0 mt-0.5" />
              <span>
                Each clip will be rendered with its current caption styles and queued to configured social destinations.
              </span>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="px-4 py-3 bg-[#1C1F24] border-t border-[#2E3239] flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={isProcessing}
            className="px-3 py-1.5 rounded-lg bg-[#23262C] hover:bg-[#2E3239] border border-[#2E3239] text-xs text-[#A3AAB5] hover:text-[#F3F4F6] transition-all"
          >
            Cancel
          </button>

          <button
            type="button"
            onClick={onConfirm}
            disabled={isProcessing}
            className={`px-4 py-1.5 rounded-lg text-xs font-semibold text-white shadow-hero-sm transition-all active:scale-95 disabled:opacity-50 flex items-center gap-1.5 ${
              isApprove ? 'bg-[#2563EB] hover:bg-[#1D4ED8]' : 'bg-[#F43F5E] hover:bg-[#E11D48]'
            }`}
          >
            {isProcessing ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                <span>Processing...</span>
              </>
            ) : isApprove ? (
              <>
                <CheckCircle2 size={13} />
                <span>Confirm & Approve All</span>
              </>
            ) : (
              <>
                <Trash2 size={13} />
                <span>Confirm Rejection</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
