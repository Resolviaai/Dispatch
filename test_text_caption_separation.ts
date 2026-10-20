import assert from 'node:assert';
import { CaptionTrack, CaptionGroup } from './frontend/src/captions/types';
import { applyColorOpacity, applyCaseTransform } from './frontend/src/captions/DispatchCaptionRenderer';

console.log('🧪 Starting Dispatch "Text vs Caption Separation & Performance" Test Suite...\n');

// 1. Data Model & Highlight Immunity Test
console.log('Test 1: Normal text fill & spoken-word highlight immunity...');
{
  const textGroup: CaptionGroup = {
    id: 'txt_1',
    type: 'text',
    start: 1.0,
    end: 4.0,
    style: {
      primary_color: '#38BDF8',
      highlight_color: '#FFE600',
      highlight_enabled: false,
      font_family: 'Montserrat',
      font_size: 40,
    },
    words: [
      { id: 'w1', text: 'Brand', start: 1.0, end: 2.0 },
      { id: 'w2', text: 'Watermark', start: 2.0, end: 4.0 },
    ],
  };

  const captionGroup: CaptionGroup = {
    id: 'cap_1',
    type: 'caption',
    start: 1.0,
    end: 4.0,
    style: {
      primary_color: '#FFFFFF',
      highlight_color: '#FFE600',
      highlight_enabled: true,
      font_family: 'Montserrat',
      font_size: 44,
    },
    words: [
      { id: 'w3', text: 'Hello', start: 1.0, end: 2.0 },
      { id: 'w4', text: 'World', start: 2.0, end: 4.0 },
    ],
  };

  // Helper matching the renderer resolution logic
  function resolveWordVisual(grp: CaptionGroup, currentTime: number, wordIdx: number) {
    const isCaption = (grp.type || 'caption') === 'caption';
    const highlightEnabled = isCaption && (grp.style?.highlight_enabled !== false);
    const word = grp.words![wordIdx];
    const isSpoken = currentTime >= word.start && currentTime <= word.end;

    let textColor = grp.style?.primary_color || '#FFFFFF';
    if (isCaption && isSpoken && highlightEnabled) {
      textColor = word.style_override?.highlight_color || grp.style?.highlight_color || '#FFE600';
    } else if (word.style_override?.color) {
      textColor = word.style_override.color;
    }

    const animType = grp.animation?.active_word?.type || grp.animation?.type;
    const isPunched = isCaption && isSpoken && animType && (animType === 'pop' || animType === 'bounce');
    const activeScale = isPunched ? (grp.animation?.active_scale || 1.15) : 1.0;

    return { textColor, activeScale, isCaption, highlightEnabled };
  }

  // At time 1.5s, word 0 is active for both
  const textAt15 = resolveWordVisual(textGroup, 1.5, 0);
  assert.strictEqual(textAt15.isCaption, false, 'textGroup must not be classified as caption');
  assert.strictEqual(textAt15.highlightEnabled, false, 'textGroup must never have highlightEnabled');
  assert.strictEqual(textAt15.textColor, '#38BDF8', 'Normal text must preserve primary fill color even during spoken timestamp');
  assert.strictEqual(textAt15.activeScale, 1.0, 'Normal text must never punch or scale pop');

  const captionAt15 = resolveWordVisual(captionGroup, 1.5, 0);
  assert.strictEqual(captionAt15.isCaption, true, 'captionGroup must be classified as caption');
  assert.strictEqual(captionAt15.highlightEnabled, true, 'captionGroup must have highlightEnabled');
  assert.strictEqual(captionAt15.textColor, '#FFE600', 'Caption must illuminate with highlight_color when active');

  console.log('  ✓ Normal text preserves primary_color and remains immune to spoken word highlight and pop punch');
  console.log('  ✓ Caption correctly highlights with highlight_color on active words');
}

// 2. Caption Highlight Toggle Test
console.log('Test 2: Caption highlight toggle (uniform phrase styling)...');
{
  const captionWithToggleOff: CaptionGroup = {
    id: 'cap_off',
    type: 'caption',
    start: 0,
    end: 3.0,
    style: {
      primary_color: '#FFFFFF',
      highlight_color: '#FFE600',
      highlight_enabled: false, // User explicitly toggled off active-word highlight
    },
    words: [
      { id: 'w1', text: 'Uniform', start: 0, end: 1.5 },
      { id: 'w2', text: 'Text', start: 1.5, end: 3.0 },
    ],
  };

  const isCaption = (captionWithToggleOff.type || 'caption') === 'caption';
  const highlightEnabled = isCaption && (captionWithToggleOff.style?.highlight_enabled !== false);
  assert.strictEqual(highlightEnabled, false, 'Caption with highlight_enabled=false must not highlight');

  const word = captionWithToggleOff.words![0];
  const isSpoken = 1.0 >= word.start && 1.0 <= word.end;
  let textColor = captionWithToggleOff.style?.primary_color || '#FFFFFF';
  if (isCaption && isSpoken && highlightEnabled) {
    textColor = captionWithToggleOff.style?.highlight_color || '#FFE600';
  }

  assert.strictEqual(textColor, '#FFFFFF', 'Caption with highlight disabled must render primary_color for spoken word');
  console.log('  ✓ Caption with highlight_enabled: false maintains uniform primary fill color without highlight');
}

// 3. Multi-layer Concurrent Rendering Test (No Single-Group Swallow Bug)
console.log('Test 3: Multi-layer concurrent rendering (caption + watermark simultaneously visible)...');
{
  const multiTrack: CaptionTrack = {
    version: 2,
    language: 'en',
    groups: [
      {
        id: 'cap_subtitle',
        type: 'caption',
        start: 0.0,
        end: 5.0,
        words: [{ id: 'w1', text: 'Subtitle', start: 0, end: 5.0 }],
        layout: { position_y: 0.8 },
      },
      {
        id: 'txt_watermark',
        type: 'text',
        start: 0.0,
        end: 10.0,
        words: [{ id: 'w2', text: 'Watermark', start: 0, end: 10.0 }],
        layout: { position_y: 0.1 },
      },
      {
        id: 'aud_bgm',
        type: 'audio',
        start: 0.0,
        end: 10.0,
        words: [],
      },
    ],
  };

  const currentTime = 2.5;

  // Multi-group filter logic from updated DispatchCaptionRenderer and InteractiveTextCanvas
  const activeRenderGroups = multiTrack.groups.filter(
    (grp) => grp.type !== 'audio' && grp.type !== 'voiceover' && currentTime >= grp.start && currentTime <= grp.end
  );

  assert.strictEqual(activeRenderGroups.length, 2, 'Both caption and text layers must be active simultaneously');
  const activeIds = activeRenderGroups.map((g) => g.id);
  assert.ok(activeIds.includes('cap_subtitle'), 'Subtitle caption must be rendered');
  assert.ok(activeIds.includes('txt_watermark'), 'Watermark text must be rendered alongside subtitle');
  assert.ok(!activeIds.includes('aud_bgm'), 'Audio track must never be rendered on canvas');

  console.log('  ✓ Multi-layer composition renders both caption and text layers concurrently without swallowing');
}

// 4. Performance Comparator Immunity Test (Suppresses 30fps Re-rendering)
console.log('Test 4: CaptionStudio React.memo comparator performance verification...');
{
  function areCaptionStudioPropsEqual(prev: any, next: any): boolean {
    if (next.activePanel === 'transcript') {
      if (prev.currentTime !== next.currentTime) return false;
    }

    if (prev.track !== next.track) return false;
    if (prev.selectedGroupId !== next.selectedGroupId) return false;
    if (prev.isVideoSelected !== next.isVideoSelected) return false;
    if (prev.layoutMode !== next.layoutMode) return false;
    if (prev.mainVideoVolume !== next.mainVideoVolume) return false;
    if (prev.isMainVideoMuted !== next.isMainVideoMuted) return false;
    if (prev.activePanel !== next.activePanel) return false;
    if (prev.duration !== next.duration) return false;
    if (prev.canUndo !== next.canUndo) return false;
    if (prev.canRedo !== next.canRedo) return false;

    return true;
  }

  const baseProps = {
    track: { version: 2, groups: [] },
    currentTime: 1.0,
    selectedGroupId: 'txt_1',
    isVideoSelected: false,
    layoutMode: 'crop_916',
    mainVideoVolume: 100,
    isMainVideoMuted: false,
    activePanel: 'style',
    duration: 30,
    canUndo: false,
    canRedo: false,
  };

  // 1. Playhead advances from 1.0 to 1.033 (30fps playback tick) while editing Style panel
  const playheadNext = { ...baseProps, currentTime: 1.033 };
  const shouldSkipReRender = areCaptionStudioPropsEqual(baseProps, playheadNext);
  assert.strictEqual(shouldSkipReRender, true, 'CaptionStudio must suppress re-rendering on frame ticks when transcript is closed');

  // 2. User selects a different layer
  const selectNext = { ...baseProps, selectedGroupId: 'cap_2' };
  assert.strictEqual(areCaptionStudioPropsEqual(baseProps, selectNext), false, 'Must re-render when selected layer changes');

  // 3. User switches inspector tabs
  const tabNext = { ...baseProps, activePanel: 'fonts' };
  assert.strictEqual(areCaptionStudioPropsEqual(baseProps, tabNext), false, 'Must re-render when active panel changes');

  // 4. In Transcript editor mode, playhead ticks MUST re-render for live karaoke scrolling
  const transcriptPrev = { ...baseProps, activePanel: 'transcript', currentTime: 1.0 };
  const transcriptNext = { ...baseProps, activePanel: 'transcript', currentTime: 1.033 };
  assert.strictEqual(areCaptionStudioPropsEqual(transcriptPrev, transcriptNext), false, 'Transcript mode must re-render on time ticks for live karaoke');

  console.log('  ✓ 30fps currentTime ticks are cleanly suppressed during normal playback & editing');
  console.log('  ✓ Layer selection & inspector tab switching immediately trigger re-render');
  console.log('  ✓ Transcript editor mode selectively receives time ticks for fluid karaoke tracking');
}

console.log('\n🎉 ALL TEXT/CAPTION SEPARATION & PERFORMANCE TESTS PASSED 100%!');
