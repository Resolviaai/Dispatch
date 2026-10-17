import React from 'react';
import { X, Keyboard, Command } from 'lucide-react';

interface KeyboardShortcutsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

const SHORTCUTS = [
  { key: 'Space', desc: 'Toggle Video Playback (Play / Pause)' },
  { key: 'J', desc: 'Seek 5 seconds backward' },
  { key: 'K', desc: 'Pause video playback' },
  { key: 'L', desc: 'Seek 5 seconds forward' },
  { key: '← / ↑', desc: 'Select previous clip in queue' },
  { key: '→ / ↓', desc: 'Select next clip in queue' },
  { key: 'A', desc: 'Quick Approve selected clip' },
  { key: 'R', desc: 'Quick Reject selected clip' },
  { key: 'E', desc: 'Jump to Caption Style tab' },
  { key: 'F', desc: 'Toggle Focus Mode (Collapse both side panes)' },
  { key: '?', desc: 'Open / Close this shortcuts cheat sheet' },
  { key: 'Esc', desc: 'Close open dialogs or exit Focus Mode' },
];

export const KeyboardShortcutsModal: React.FC<KeyboardShortcutsModalProps> = ({
  isOpen,
  onClose,
}) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="w-full max-w-md rounded-2xl bg-[#16181C] border border-[#2E3239] shadow-2xl overflow-hidden select-none">
        {/* Header */}
        <div className="px-4 py-3 bg-[#1C1F24] border-b border-[#2E3239] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Keyboard size={16} className="text-[#2563EB]" />
            <h3 className="text-xs font-bold uppercase tracking-wider text-[#F3F4F6]">
              Workstation Shortcuts
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded text-[#747D8A] hover:text-[#F3F4F6] hover:bg-[#23262C]"
          >
            <X size={15} />
          </button>
        </div>

        {/* Shortcuts Table */}
        <div className="p-4 space-y-2 max-h-[70vh] overflow-y-auto">
          {SHORTCUTS.map((s) => (
            <div
              key={s.key}
              className="flex items-center justify-between py-1.5 px-2 rounded-lg bg-[#23262C]/50 border border-[#2E3239]/50 text-xs"
            >
              <span className="text-[#A3AAB5]">{s.desc}</span>
              <kbd className="px-2 py-0.5 rounded bg-[#16181C] border border-[#2E3239] text-[#F3F4F6] font-mono text-[11px] font-semibold shadow-sm">
                {s.key}
              </kbd>
            </div>
          ))}
        </div>

        {/* Footer Note */}
        <div className="px-4 py-2.5 bg-[#1C1F24] border-t border-[#2E3239] text-[10px] text-[#747D8A] flex items-center justify-between">
          <span>Shortcuts are paused when typing in text fields.</span>
          <kbd className="px-1.5 py-0.5 rounded bg-[#23262C] font-mono text-[9px] text-[#A3AAB5]">Esc to close</kbd>
        </div>
      </div>
    </div>
  );
};
