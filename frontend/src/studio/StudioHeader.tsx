import React from 'react';
import {
  Sparkles,
  HardDrive,
  Maximize2,
  Minimize2,
  HelpCircle,
  Smartphone,
  ShieldCheck,
  ShieldAlert,
  Radio,
  CheckCircle2,
  XCircle,
  Layers
} from 'lucide-react';
import { SystemStatus, IntegrationsStatus, ClipItem } from './types';

interface StudioHeaderProps {
  systemStatus: SystemStatus | null;
  integrations: IntegrationsStatus | null;
  selectedClip: ClipItem | null;
  totalClipsCount: number;
  readyClipsCount: number;
  isFocusMode: boolean;
  onToggleFocusMode: () => void;
  onOpenShortcuts: () => void;
  onSwitchToMobileFrame: () => void;
  publishMode: 'private' | 'public';
  onTogglePublishMode: (mode: 'private' | 'public') => void;
}

export const StudioHeader: React.FC<StudioHeaderProps> = ({
  systemStatus,
  integrations,
  selectedClip,
  totalClipsCount,
  readyClipsCount,
  isFocusMode,
  onToggleFocusMode,
  onOpenShortcuts,
  onSwitchToMobileFrame,
  publishMode,
  onTogglePublishMode,
}) => {
  const isYouTubeConnected = Boolean(integrations?.youtube?.connected);
  const ytChannel = integrations?.youtube?.channel_title || (isYouTubeConnected ? 'Connected' : 'Offline');
  const isGeminiConfigured = Boolean(integrations?.gemini?.configured);

  const diskFree = systemStatus ? `${systemStatus.disk_free_gb} GB` : '-- GB';
  const diskPercent = systemStatus ? `${systemStatus.disk_percent_used}%` : '--%';

  return (
    <header className="h-[54px] min-h-[54px] w-full bg-[#16181C] border-b border-[#2E3239] px-4 flex items-center justify-between select-none z-40 shrink-0">
      {/* LEFT BRAND & CONTEXT */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md bg-[#2563EB] flex items-center justify-center text-white shadow-hero-sm font-mono font-bold text-xs tracking-tighter">
            D
          </div>
          <div className="flex flex-col">
            <div className="flex items-center gap-1.5">
              <span className="text-[13px] font-bold tracking-tight text-[#F3F4F6] font-mono">
                DISPATCH
              </span>
              <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-[#23262C] border border-[#2E3239] text-[#A3AAB5]">
                PC Studio
              </span>
            </div>
          </div>
        </div>

        <div className="h-4 w-px bg-[#2E3239] mx-1 hidden sm:block" />

        {/* Selected Clip Context */}
        {selectedClip ? (
          <div className="hidden lg:flex items-center gap-2 text-xs">
            <span className="text-[#747D8A]">Reviewing:</span>
            <span className="font-semibold text-[#F3F4F6] max-w-[220px] truncate" title={selectedClip.title || selectedClip.hook}>
              {selectedClip.title || selectedClip.hook || selectedClip.id}
            </span>
            <span className="text-[11px] font-mono text-[#A3AAB5] px-1.5 py-0.5 rounded bg-[#23262C] border border-[#2E3239]">
              {selectedClip.duration ? `${Math.round(selectedClip.duration)}s` : '--'}
            </span>
            <span className="text-[11px] font-mono font-semibold px-1.5 py-0.5 rounded bg-[#2563EB]/15 border border-[#2563EB]/40 text-[#60A5FA]">
              Score {selectedClip.virality_score}
            </span>
          </div>
        ) : (
          <div className="hidden lg:flex items-center gap-2 text-xs text-[#747D8A]">
            <span>{totalClipsCount} clips in queue</span>
            {readyClipsCount > 0 && (
              <span className="text-[#EAB308] font-mono font-medium">({readyClipsCount} awaiting review)</span>
            )}
          </div>
        )}
      </div>

      {/* RIGHT TELEMETRY & ACTIONS */}
      <div className="flex items-center gap-2 sm:gap-3">
        {/* Safe Publishing Mode Toggle */}
        <div className="flex items-center bg-[#23262C] p-0.5 rounded-lg border border-[#2E3239] text-xs">
          <button
            type="button"
            onClick={() => onTogglePublishMode('private')}
            className={`px-2 py-1 rounded-md text-[11px] font-medium transition-all ${
              publishMode === 'private'
                ? 'bg-[#16181C] text-[#F3F4F6] shadow-sm font-semibold'
                : 'text-[#747D8A] hover:text-[#A3AAB5]'
            }`}
            title="Safe Mode: Videos uploaded as Private drafts"
          >
            <span className="flex items-center gap-1">
              <ShieldCheck size={12} className={publishMode === 'private' ? 'text-[#10B981]' : ''} />
              Private Draft
            </span>
          </button>
          <button
            type="button"
            onClick={() => onTogglePublishMode('public')}
            className={`px-2 py-1 rounded-md text-[11px] font-medium transition-all ${
              publishMode === 'public'
                ? 'bg-[#2563EB] text-white shadow-sm font-semibold'
                : 'text-[#747D8A] hover:text-[#A3AAB5]'
            }`}
            title="Direct Mode: Auto public publishing"
          >
            <span className="flex items-center gap-1">
              <Radio size={12} />
              Auto Public
            </span>
          </button>
        </div>

        {/* Disk & Storage Status */}
        <div
          className="hidden xl:flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#23262C] border border-[#2E3239] text-[11px] font-mono text-[#A3AAB5]"
          title={`Total Disk Used: ${diskPercent} (${diskFree} free)`}
        >
          <HardDrive size={13} className="text-[#747D8A]" />
          <span>{diskFree} free</span>
          <span className="text-[#747D8A] text-[10px]">({diskPercent})</span>
        </div>

        {/* Gemini Status */}
        <div
          className={`hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[11px] font-mono ${
            isGeminiConfigured
              ? 'bg-[#10B981]/10 border-[#10B981]/30 text-[#10B981]'
              : 'bg-[#23262C] border-[#2E3239] text-[#747D8A]'
          }`}
          title={isGeminiConfigured ? 'Gemini AI Vision & Highlights Connected' : 'Gemini Key Not Configured'}
        >
          <Sparkles size={13} />
          <span>{isGeminiConfigured ? 'Gemini Active' : 'No Gemini'}</span>
        </div>

        {/* YouTube Status Badge */}
        <div
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[11px] font-medium max-w-[160px] truncate ${
            isYouTubeConnected
              ? 'bg-[#2563EB]/10 border-[#2563EB]/40 text-[#60A5FA]'
              : 'bg-[#F43F5E]/10 border-[#F43F5E]/30 text-[#F43F5E]'
          }`}
          title={isYouTubeConnected ? `YouTube Channel: ${ytChannel}` : 'YouTube OAuth Disconnected'}
        >
          <span className={`w-2 h-2 rounded-full shrink-0 ${isYouTubeConnected ? 'bg-[#10B981]' : 'bg-[#F43F5E]'}`} />
          <span className="truncate">{ytChannel}</span>
        </div>

        {/* Focus Mode Toggle */}
        <button
          type="button"
          onClick={onToggleFocusMode}
          className={`p-1.5 rounded-lg border transition-all active:scale-95 ${
            isFocusMode
              ? 'bg-[#2563EB] border-[#2563EB] text-white shadow-hero-sm'
              : 'bg-[#23262C] border-[#2E3239] text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#2E3239]'
          }`}
          title={isFocusMode ? 'Exit Focus Mode (F)' : 'Enter Focus Mode (F) - Hide side panes'}
        >
          {isFocusMode ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
        </button>

        {/* Keyboard Shortcuts Help */}
        <button
          type="button"
          onClick={onOpenShortcuts}
          className="p-1.5 rounded-lg bg-[#23262C] border border-[#2E3239] text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#2E3239] transition-all active:scale-95"
          title="Keyboard Shortcuts (?)"
        >
          <HelpCircle size={15} />
        </button>

        {/* Phone Frame Simulator Switch */}
        <button
          type="button"
          onClick={onSwitchToMobileFrame}
          className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#23262C] border border-[#2E3239] text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#2E3239] text-[11px] font-mono transition-all active:scale-95"
          title="Switch to POCO C65 Mobile Shell Preview"
        >
          <Smartphone size={13} />
          <span>Mobile Frame</span>
        </button>
      </div>
    </header>
  );
};
