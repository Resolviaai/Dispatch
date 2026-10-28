import React, { useState, useEffect } from 'react';
import {
  Sliders,
  Type,
  Share2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  PanelRightClose,
  PanelRightOpen,
  Save,
  Check,
  Send,
  Sparkles,
  Info,
  Clock,
  Layers,
  Palette
} from 'lucide-react';
import { ClipItem, IntegrationsStatus, LayoutMode } from './types';
import { CaptionTrack, StyleConfig, LayoutConfig } from '../captions/types';
import { StyleSection } from '../captions/applyToAll';
import { ApplyToAllButton } from '../captions/ApplyToAllButton';
import { apiUrl, getAuthHeaders } from '../bridge';

interface InspectorPaneProps {
  clip: ClipItem | null;
  track: CaptionTrack | null;
  integrations: IntegrationsStatus | null;
  onUpdateClipMetadata: (patch: Partial<ClipItem>) => void;
  onSaveCaptions: () => Promise<void>;
  onUpdateTrackStyle: (stylePatch: Partial<StyleConfig>) => void;
  onUpdateTrackLayout?: (layoutPatch: Partial<LayoutConfig>) => void;
  onApplyStyleToAll?: (section: StyleSection) => number;
  onApproveClip: () => Promise<void>;
  onRejectClip: (reason?: string) => Promise<void>;
  isApproving: boolean;
  isRejecting: boolean;
  isSavingCaptions: boolean;
  publishMode: 'private' | 'public';
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  width: number;
}

const CAPTION_PRESETS: Array<{
  id: string;
  name: string;
  font: string;
  color: string;
  highlight: string;
  outlineColor: string;
  outlineWidth: number;
}> = [
  { id: 'impact', name: 'Impact Bold', font: 'Impact', color: '#FFFFFF', highlight: '#FFE600', outlineColor: '#000000', outlineWidth: 3 },
  { id: 'montserrat', name: 'Montserrat Clean', font: 'Montserrat', color: '#FFFFFF', highlight: '#2563EB', outlineColor: '#000000', outlineWidth: 2 },
  { id: 'glow', name: 'Neon Glow', font: 'Poppins', color: '#FFFFFF', highlight: '#22C55E', outlineColor: '#0A3B1F', outlineWidth: 3 },
  { id: 'classic', name: 'Classic Sub', font: 'Inter', color: '#FFFFFF', highlight: '#FFD700', outlineColor: '#000000', outlineWidth: 2 },
  { id: 'headline', name: 'Oswald Heavy', font: 'Oswald', color: '#FFFFFF', highlight: '#F43F5E', outlineColor: '#000000', outlineWidth: 2.5 }
];

export const InspectorPane: React.FC<InspectorPaneProps> = ({
  clip,
  track,
  integrations,
  onUpdateClipMetadata,
  onSaveCaptions,
  onUpdateTrackStyle,
  onUpdateTrackLayout,
  onApplyStyleToAll,
  onApproveClip,
  onRejectClip,
  isApproving,
  isRejecting,
  isSavingCaptions,
  publishMode,
  isCollapsed,
  onToggleCollapse,
  width,
}) => {
  const [activeTab, setActiveTab] = useState<'metadata' | 'captions' | 'destinations'>('metadata');
  const [title, setTitle] = useState('');
  const [hook, setHook] = useState('');
  const [hashtags, setHashtags] = useState('');
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(['youtube']);
  const [rejectConfirmOpen, setRejectConfirmOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('Quality / Hook mismatch');
  const [isSavedNotice, setIsSavedNotice] = useState(false);

  // Sync internal form when selected clip changes
  useEffect(() => {
    if (clip) {
      setTitle(clip.title || '');
      setHook(clip.hook || '');
      setHashtags(clip.hashtags || '#Shorts #Dispatch');
      const platforms = (clip.platform_targets || 'youtube')
        .split(',')
        .map(p => p.trim())
        .filter(Boolean);
      setSelectedPlatforms(platforms.length > 0 ? platforms : ['youtube']);
      setRejectConfirmOpen(false);
    }
  }, [clip?.id]);

  const togglePlatform = (p: string) => {
    const next = selectedPlatforms.includes(p)
      ? selectedPlatforms.filter(item => item !== p)
      : [...selectedPlatforms, p];
    setSelectedPlatforms(next);
    onUpdateClipMetadata({ platform_targets: next.join(',') });
  };

  const handleApplyPreset = (preset: typeof CAPTION_PRESETS[0]) => {
    onUpdateTrackStyle({
      font_family: preset.font,
      primary_color: preset.color,
      highlight_color: preset.highlight,
      outline_color: preset.outlineColor,
      outline_width: preset.outlineWidth,
      outline_enabled: true,
    });
  };

  const handleSaveMetadataDraft = async () => {
    const clipId = clip?.id;
    const patch = {
      title: title.trim(),
      hook: hook.trim(),
      hashtags: hashtags.trim(),
      platform_targets: selectedPlatforms.join(','),
    };
    // D8 (audit 2026-10-27): persist through the revision-fenced PC endpoint
    // BEFORE reflecting locally. A failed save must never advertise success:
    // revert the draft to the clip's persisted values and skip the saved notice.
    if (clipId) {
      try {
        const res = await fetch(apiUrl(`/api/clips/${clipId}/metadata`), {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify({ ...patch, expected_range_revision: clip?.range_revision ?? 0 }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
      } catch (err) {
        console.warn('Metadata save failed; reverting draft to persisted values:', err);
        if (clip) {
          setTitle(clip.title || '');
          setHook(clip.hook || '');
          setHashtags(clip.hashtags || '#Shorts #Dispatch');
          const platforms = (clip.platform_targets || 'youtube').split(',').map(p => p.trim()).filter(Boolean);
          setSelectedPlatforms(platforms.length > 0 ? platforms : ['youtube']);
        }
        return;
      }
    }
    onUpdateClipMetadata(patch);
    setIsSavedNotice(true);
    setTimeout(() => setIsSavedNotice(false), 2000);
  };

  const currentStyle = track?.default_style || {
    font_family: 'Impact',
    font_size: 26,
    primary_color: '#FFFFFF',
    highlight_color: '#FFE600',
    outline_width: 2.5,
    outline_color: '#000000',
  };

  const currentLayout = track?.default_layout || {
    position_x: 0.5,
    position_y: 0.78,
    scale: 1.0,
  };

  const isYouTubeConnected = Boolean(integrations?.youtube?.connected);
  const ytChannel = integrations?.youtube?.channel_title || 'Not Connected';

  // Character counters
  const ytTitleLen = title.length;
  const isYtTitleOver = ytTitleLen > 100;
  const xTextLen = (title + ' ' + hashtags).length;
  const isXTextOver = xTextLen > 280;

  if (isCollapsed) {
    return (
      <aside className="w-10 min-w-[40px] h-full bg-[#16181C] border-l border-[#2E3239] flex flex-col items-center py-3 select-none shrink-0 transition-all">
        <button
          type="button"
          onClick={onToggleCollapse}
          className="p-1.5 rounded-md text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#23262C] transition-all"
          title="Expand Inspector Pane"
        >
          <PanelRightOpen size={16} />
        </button>
        <div className="mt-8 [writing-mode:vertical-rl] text-[11px] font-mono uppercase tracking-wider text-[#747D8A]">
          Inspector
        </div>
      </aside>
    );
  }

  return (
    <aside
      style={{ width: `${width}px` }}
      className="h-full bg-[#16181C] border-l border-[#2E3239] flex flex-col select-none shrink-0 overflow-hidden"
    >
      {/* INSPECTOR HEADER & TABS */}
      <div className="p-3 border-b border-[#2E3239] bg-[#16181C] shrink-0 space-y-2.5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sliders size={15} className="text-[#2563EB]" />
            <h2 className="text-xs font-bold uppercase tracking-wider text-[#F3F4F6]">
              Inspector & Publish
            </h2>
          </div>
          <button
            type="button"
            onClick={onToggleCollapse}
            className="p-1 rounded text-[#747D8A] hover:text-[#F3F4F6] hover:bg-[#23262C] transition-all"
            title="Collapse Inspector"
          >
            <PanelRightClose size={15} />
          </button>
        </div>

        {/* Tab Controls */}
        <div className="flex items-center bg-[#23262C] p-0.5 rounded-lg border border-[#2E3239] text-xs">
          <button
            type="button"
            onClick={() => setActiveTab('metadata')}
            className={`flex-1 py-1 rounded-md text-[11px] font-medium transition-all ${
              activeTab === 'metadata'
                ? 'bg-[#16181C] text-[#F3F4F6] shadow-sm font-semibold'
                : 'text-[#747D8A] hover:text-[#A3AAB5]'
            }`}
          >
            Metadata
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('captions')}
            className={`flex-1 py-1 rounded-md text-[11px] font-medium transition-all ${
              activeTab === 'captions'
                ? 'bg-[#16181C] text-[#F3F4F6] shadow-sm font-semibold'
                : 'text-[#747D8A] hover:text-[#A3AAB5]'
            }`}
          >
            Captions
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('destinations')}
            className={`flex-1 py-1 rounded-md text-[11px] font-medium transition-all ${
              activeTab === 'destinations'
                ? 'bg-[#16181C] text-[#F3F4F6] shadow-sm font-semibold'
                : 'text-[#747D8A] hover:text-[#A3AAB5]'
            }`}
          >
            Destinations
          </button>
        </div>
      </div>

      {/* INSPECTOR CONTENT (Scrollable) */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4">
        {!clip ? (
          <div className="h-48 flex flex-col items-center justify-center text-center text-[#747D8A]">
            <Info size={24} className="mb-2 text-[#2E3239]" />
            <p className="text-xs font-semibold text-[#A3AAB5]">No Clip Selected</p>
            <p className="text-[11px] mt-1 max-w-[200px]">
              Select a clip from the left queue to inspect and configure publishing.
            </p>
          </div>
        ) : activeTab === 'metadata' ? (
          /* TAB 1: METADATA EDITOR */
          <div className="space-y-3.5 animate-in fade-in duration-150">
            {/* Title Field */}
            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <label className="text-[11px] font-semibold text-[#A3AAB5]">Clip Title</label>
                <span
                  className={`text-[10px] font-mono tabular-nums ${
                    isYtTitleOver ? 'text-[#F43F5E] font-bold' : ytTitleLen > 80 ? 'text-[#EAB308]' : 'text-[#747D8A]'
                  }`}
                >
                  {ytTitleLen} / 100
                </span>
              </div>
              <input
                type="text"
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value);
                  onUpdateClipMetadata({ title: e.target.value });
                }}
                className="w-full h-8 px-2.5 text-xs rounded-lg bg-[#23262C] border border-[#2E3239] text-[#F3F4F6] focus:outline-none focus:border-[#2563EB]"
                placeholder="High-performing hook title..."
              />
              {isYtTitleOver && (
                <p className="text-[10px] text-[#F43F5E] flex items-center gap-1">
                  <AlertTriangle size={11} />
                  <span>Exceeds YouTube Shorts 100-character title limit</span>
                </p>
              )}
            </div>

            {/* Hook / Description */}
            <div className="space-y-1">
              <label className="text-[11px] font-semibold text-[#A3AAB5]">Hook / Core Value</label>
              <textarea
                rows={3}
                value={hook}
                onChange={(e) => {
                  setHook(e.target.value);
                  onUpdateClipMetadata({ hook: e.target.value });
                }}
                className="w-full p-2 text-xs rounded-lg bg-[#23262C] border border-[#2E3239] text-[#F3F4F6] focus:outline-none focus:border-[#2563EB] resize-none"
                placeholder="Summary hook that grabs attention..."
              />
            </div>

            {/* Hashtags */}
            <div className="space-y-1">
              <label className="text-[11px] font-semibold text-[#A3AAB5]">Hashtags</label>
              <input
                type="text"
                value={hashtags}
                onChange={(e) => {
                  setHashtags(e.target.value);
                  onUpdateClipMetadata({ hashtags: e.target.value });
                }}
                className="w-full h-8 px-2.5 text-xs rounded-lg bg-[#23262C] border border-[#2E3239] text-[#F3F4F6] focus:outline-none focus:border-[#2563EB] font-mono text-[11px]"
                placeholder="#Shorts #Creator #Dispatch"
              />
            </div>

            {/* Technical Metadata Card */}
            <div className="p-2.5 rounded-lg bg-[#1C1F24] border border-[#2E3239] space-y-2 text-xs">
              <span className="text-[10px] font-mono uppercase tracking-wider text-[#747D8A] block">
                Technical Blueprint
              </span>
              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <div>
                  <span className="text-[#747D8A] block">Duration</span>
                  <span className="font-mono text-[#F3F4F6]">{clip.duration.toFixed(1)}s</span>
                </div>
                <div>
                  <span className="text-[#747D8A] block">AI Score</span>
                  <span className="font-mono font-semibold text-[#60A5FA]">{clip.virality_score} / 100</span>
                </div>
                <div>
                  <span className="text-[#747D8A] block">Orientation</span>
                  <span className="font-mono text-[#F3F4F6] capitalize">{clip.source_orientation || 'Portrait'}</span>
                </div>
                <div>
                  <span className="text-[#747D8A] block">Framing</span>
                  <span className="font-mono text-[#F3F4F6]">{clip.layout_mode || 'fit_black'}</span>
                </div>
              </div>
            </div>

            {/* Save Draft Button */}
            <button
              type="button"
              onClick={handleSaveMetadataDraft}
              className="w-full py-1.5 rounded-lg bg-[#23262C] hover:bg-[#2E3239] border border-[#2E3239] text-xs text-[#F3F4F6] flex items-center justify-center gap-1.5 transition-all active:scale-95"
            >
              {isSavedNotice ? <Check size={13} className="text-[#10B981]" /> : <Save size={13} />}
              <span>{isSavedNotice ? 'Draft Changes Saved' : 'Save Draft'}</span>
            </button>
          </div>
        ) : activeTab === 'captions' ? (
          /* TAB 2: CAPTION STYLE & PRESETS */
          <div className="space-y-4 animate-in fade-in duration-150">
            {/* Quick Presets */}
            <div className="space-y-1.5">
              <label className="text-[11px] font-semibold text-[#A3AAB5] flex items-center gap-1">
                <Palette size={12} className="text-[#2563EB]" />
                <span>Caption Presets</span>
              </label>
              <div className="grid grid-cols-2 gap-1.5">
                {CAPTION_PRESETS.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => handleApplyPreset(p)}
                    className="p-2 rounded-lg bg-[#23262C] hover:bg-[#2E3239] border border-[#2E3239] text-left transition-all active:scale-95"
                  >
                    <span
                      className="text-xs font-bold block"
                      style={{ fontFamily: p.font, color: p.color }}
                    >
                      {p.name}
                    </span>
                    <span className="text-[10px] text-[#747D8A]">Font: {p.font}</span>
                  </button>
                ))}
              </div>
              {onApplyStyleToAll && (
                <ApplyToAllButton
                  section="templates"
                  sectionLabel="Preset"
                  onApply={onApplyStyleToAll}
                />
              )}
            </div>

            {/* Font Family Selector */}
            <div className="space-y-1">
              <label className="text-[11px] font-semibold text-[#A3AAB5]">Font Family</label>
              <select
                value={currentStyle.font_family}
                onChange={(e) => onUpdateTrackStyle({ font_family: e.target.value })}
                className="w-full h-8 px-2 text-xs rounded-lg bg-[#23262C] border border-[#2E3239] text-[#F3F4F6] focus:outline-none focus:border-[#2563EB]"
              >
                {['Impact', 'Montserrat', 'Poppins', 'Inter', 'Oswald', 'Arial Black'].map((f) => (
                  <option key={f} value={f}>{f}</option>
                ))}
              </select>
            </div>

            {/* Font Size Slider */}
            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <label className="text-[11px] font-semibold text-[#A3AAB5]">Font Size</label>
                <span className="text-[10px] font-mono text-[#F3F4F6]">{currentStyle.font_size || 26}px</span>
              </div>
              <input
                type="range"
                min={16}
                max={42}
                step={1}
                value={currentStyle.font_size || 26}
                onChange={(e) => onUpdateTrackStyle({ font_size: parseInt(e.target.value) })}
                className="w-full h-1 bg-[#23262C] accent-[#2563EB] cursor-pointer"
              />
              {onApplyStyleToAll && (
                <ApplyToAllButton
                  section="font"
                  sectionLabel="Font"
                  onApply={onApplyStyleToAll}
                />
              )}
            </div>

            {/* Color Pickers */}
            <div className="space-y-1.5">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="space-y-1">
                  <label className="text-[11px] text-[#A3AAB5]">Text Color</label>
                  <div className="flex items-center gap-2 bg-[#23262C] p-1.5 rounded-lg border border-[#2E3239]">
                    <input
                      type="color"
                      value={currentStyle.primary_color || '#FFFFFF'}
                      onChange={(e) => onUpdateTrackStyle({ primary_color: e.target.value })}
                      className="w-5 h-5 rounded cursor-pointer border-0 bg-transparent"
                    />
                    <span className="text-[10px] font-mono text-[#F3F4F6]">{currentStyle.primary_color || '#FFFFFF'}</span>
                  </div>
                </div>

                <div className="space-y-1">
                  <label className="text-[11px] text-[#A3AAB5]">Active Highlight</label>
                  <div className="flex items-center gap-2 bg-[#23262C] p-1.5 rounded-lg border border-[#2E3239]">
                    <input
                      type="color"
                      value={currentStyle.highlight_color || '#FFE600'}
                      onChange={(e) => onUpdateTrackStyle({ highlight_color: e.target.value })}
                      className="w-5 h-5 rounded cursor-pointer border-0 bg-transparent"
                    />
                    <span className="text-[10px] font-mono text-[#F3F4F6]">{currentStyle.highlight_color || '#FFE600'}</span>
                  </div>
                </div>
              </div>
              {onApplyStyleToAll && (
                <ApplyToAllButton
                  section="color"
                  sectionLabel="Color"
                  onApply={onApplyStyleToAll}
                />
              )}
            </div>

            {/* Position & Size */}
            <div className="space-y-1.5 pt-1">
              <div className="flex items-center justify-between text-xs">
                <label className="text-[11px] font-semibold text-[#A3AAB5]">Position & Size</label>
                <span className="text-[10px] text-[#747D8A]">Move on preview</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-[#A3AAB5]">Vertical (Y)</span>
                    <span className="font-mono text-[10px] text-[#F3F4F6]">
                      {Math.round((currentLayout.position_y ?? 0.78) * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={15}
                    max={90}
                    step={1}
                    value={Math.round((currentLayout.position_y ?? 0.78) * 100)}
                    onChange={(e) => onUpdateTrackLayout?.({ position_y: Number((Number(e.target.value) / 100).toFixed(2)) })}
                    className="w-full h-1 bg-[#23262C] accent-[#2563EB] cursor-pointer"
                  />
                </div>
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-[#A3AAB5]">Scale</span>
                    <span className="font-mono text-[10px] text-[#F3F4F6]">
                      {(currentLayout.scale ?? 1.0).toFixed(2)}x
                    </span>
                  </div>
                  <input
                    type="range"
                    min={60}
                    max={200}
                    step={5}
                    value={Math.round((currentLayout.scale ?? 1.0) * 100)}
                    onChange={(e) => onUpdateTrackLayout?.({ scale: Number((Number(e.target.value) / 100).toFixed(2)) })}
                    className="w-full h-1 bg-[#23262C] accent-[#2563EB] cursor-pointer"
                  />
                </div>
              </div>
              {onApplyStyleToAll && (
                <ApplyToAllButton
                  section="layout"
                  sectionLabel="Position & Size"
                  label="Apply to All Captions"
                  onApply={onApplyStyleToAll}
                />
              )}
            </div>

            {/* Save Captions Button */}
            <button
              type="button"
              onClick={onSaveCaptions}
              disabled={isSavingCaptions}
              className="w-full py-1.5 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white font-medium text-xs shadow-hero-sm transition-all active:scale-95 disabled:opacity-50 flex items-center justify-center gap-1.5"
            >
              <Save size={13} />
              <span>{isSavingCaptions ? 'Saving to Backend...' : 'Persist Caption Styles'}</span>
            </button>
          </div>
        ) : (
          /* TAB 3: PUBLISHING DESTINATIONS */
          <div className="space-y-3 animate-in fade-in duration-150">
            {/* YouTube Shorts Card */}
            <div
              onClick={() => togglePlatform('youtube')}
              className={`p-2.5 rounded-lg border cursor-pointer transition-all ${
                selectedPlatforms.includes('youtube')
                  ? 'bg-[#1C1F24] border-[#2563EB]/60 shadow-sm'
                  : 'bg-[#16181C] border-[#2E3239] opacity-70'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-5 h-5 rounded bg-[#FF0000] flex items-center justify-center text-white text-[10px] font-bold">
                    YT
                  </div>
                  <span className="text-xs font-semibold text-[#F3F4F6]">YouTube Shorts</span>
                </div>
                <input
                  type="checkbox"
                  checked={selectedPlatforms.includes('youtube')}
                  onChange={() => {}}
                  className="rounded accent-[#2563EB]"
                />
              </div>
              <div className="mt-2 text-[11px] flex items-center justify-between text-[#747D8A]">
                <span>Status: <strong className={isYouTubeConnected ? 'text-[#10B981]' : 'text-[#F43F5E]'}>{ytChannel}</strong></span>
                <span className="font-mono text-[10px]">Max 100 char</span>
              </div>
            </div>

            {/* Instagram Reels Card */}
            <div
              onClick={() => togglePlatform('instagram')}
              className={`p-2.5 rounded-lg border cursor-pointer transition-all ${
                selectedPlatforms.includes('instagram')
                  ? 'bg-[#1C1F24] border-[#2563EB]/60 shadow-sm'
                  : 'bg-[#16181C] border-[#2E3239] opacity-70'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-5 h-5 rounded bg-gradient-to-tr from-[#FED373] via-[#D92E7F] to-[#9B36B7] flex items-center justify-center text-white text-[10px] font-bold">
                    IG
                  </div>
                  <span className="text-xs font-semibold text-[#F3F4F6]">Instagram Reels</span>
                </div>
                <input
                  type="checkbox"
                  checked={selectedPlatforms.includes('instagram')}
                  onChange={() => {}}
                  className="rounded accent-[#2563EB]"
                />
              </div>
              <div className="mt-2 text-[11px] flex items-center justify-between text-[#747D8A]">
                <span>Auth: <strong className="text-[#A3AAB5]">Token (.env)</strong></span>
                <span className="font-mono text-[10px]">Max 2,200 char</span>
              </div>
            </div>

            {/* LinkedIn Video Card */}
            <div
              onClick={() => togglePlatform('linkedin')}
              className={`p-2.5 rounded-lg border cursor-pointer transition-all ${
                selectedPlatforms.includes('linkedin')
                  ? 'bg-[#1C1F24] border-[#2563EB]/60 shadow-sm'
                  : 'bg-[#16181C] border-[#2E3239] opacity-70'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-5 h-5 rounded bg-[#0A66C2] flex items-center justify-center text-white text-[10px] font-bold">
                    in
                  </div>
                  <span className="text-xs font-semibold text-[#F3F4F6]">LinkedIn</span>
                </div>
                <input
                  type="checkbox"
                  checked={selectedPlatforms.includes('linkedin')}
                  onChange={() => {}}
                  className="rounded accent-[#2563EB]"
                />
              </div>
              <div className="mt-2 text-[11px] flex items-center justify-between text-[#747D8A]">
                <span>Auth: <strong className="text-[#A3AAB5]">OAuth (.env)</strong></span>
                <span className="font-mono text-[10px]">Max 3,000 char</span>
              </div>
            </div>

            {/* X / Twitter Card */}
            <div
              onClick={() => togglePlatform('x')}
              className={`p-2.5 rounded-lg border cursor-pointer transition-all ${
                selectedPlatforms.includes('x')
                  ? 'bg-[#1C1F24] border-[#2563EB]/60 shadow-sm'
                  : 'bg-[#16181C] border-[#2E3239] opacity-70'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-5 h-5 rounded bg-[#18181B] border border-[#27272A] flex items-center justify-center text-white text-[10px] font-bold">
                    X
                  </div>
                  <span className="text-xs font-semibold text-[#F3F4F6]">X (Twitter)</span>
                </div>
                <input
                  type="checkbox"
                  checked={selectedPlatforms.includes('x')}
                  onChange={() => {}}
                  className="rounded accent-[#2563EB]"
                />
              </div>
              <div className="mt-2 text-[11px] flex items-center justify-between text-[#747D8A]">
                <span>Auth: <strong className="text-[#A3AAB5]">API Key (.env)</strong></span>
                <span className={`font-mono text-[10px] ${isXTextOver ? 'text-[#F43F5E] font-bold' : ''}`}>
                  {xTextLen} / 280
                </span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* STICKY BOTTOM ACTION DOCK */}
      {clip && (
        <div className="p-3 border-t border-[#2E3239] bg-[#16181C] shrink-0 space-y-2.5">
          {/* Privacy & Safe Mode Guarantee */}
          <div className="flex items-center justify-between text-[11px] text-[#A3AAB5]">
            <span>Publish Mode:</span>
            <span
              className={`font-semibold font-mono px-1.5 py-0.2 rounded border ${
                publishMode === 'private'
                  ? 'bg-[#10B981]/10 border-[#10B981]/30 text-[#10B981]'
                  : 'bg-[#2563EB]/10 border-[#2563EB]/30 text-[#60A5FA]'
              }`}
            >
              {publishMode === 'private' ? 'Private Draft' : 'Public Broadcast'}
            </span>
          </div>

          {/* Action Buttons Row */}
          <div className="flex items-center gap-2">
            {/* Reject Button */}
            <button
              type="button"
              onClick={() => setRejectConfirmOpen(!rejectConfirmOpen)}
              disabled={isRejecting || isApproving}
              className="flex-1 py-2 rounded-lg bg-[#23262C] hover:bg-[#F43F5E]/20 border border-[#2E3239] hover:border-[#F43F5E]/50 text-[#F43F5E] text-xs font-medium transition-all active:scale-95 disabled:opacity-40"
            >
              <span>Reject</span>
            </button>

            {/* Primary Hero CTA: Approve & Publish */}
            <button
              type="button"
              onClick={onApproveClip}
              disabled={isApproving || isRejecting}
              className="flex-[2] py-2 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-xs font-semibold shadow-hero-glow transition-all active:scale-[0.98] disabled:opacity-40 flex items-center justify-center gap-1.5"
            >
              {isApproving ? (
                <>
                  <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  <span>Enqueuing...</span>
                </>
              ) : (
                <>
                  <Send size={13} />
                  <span>Approve & Publish</span>
                </>
              )}
            </button>
          </div>

          {/* Inline Rejection Reason Dropdown if toggled */}
          {rejectConfirmOpen && (
            <div className="p-2 rounded-lg bg-[#1C1F24] border border-[#F43F5E]/40 space-y-2 animate-in fade-in duration-150">
              <label className="text-[10px] uppercase font-mono text-[#F43F5E] block">
                Confirm Rejection Reason:
              </label>
              <select
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                className="w-full h-7 px-2 text-xs rounded bg-[#23262C] border border-[#2E3239] text-[#F3F4F6]"
              >
                <option value="Quality / Hook mismatch">Quality / Hook mismatch</option>
                <option value="Duplicate content">Duplicate content</option>
                <option value="Audio or video glitch">Audio or video glitch</option>
                <option value="Not relevant">Not relevant</option>
              </select>
              <div className="flex items-center justify-end gap-1.5 pt-1">
                <button
                  type="button"
                  onClick={() => setRejectConfirmOpen(false)}
                  className="px-2 py-0.5 rounded text-[11px] text-[#A3AAB5] hover:text-[#F3F4F6]"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => onRejectClip(rejectReason)}
                  className="px-2.5 py-0.5 rounded bg-[#F43F5E] text-white text-[11px] font-semibold"
                >
                  Confirm Reject
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </aside>
  );
};
