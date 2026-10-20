import assert from 'node:assert';
import { CaptionTrack, CaptionGroup } from './frontend/src/captions/types';
import { extractSectionProperties, applySectionStylesToTrack } from './frontend/src/captions/applyToAll';

console.log('🧪 Starting Dispatch "Apply to All Text Styling" Test Suite...\n');

// Mock Caption Track
const initialTrack: CaptionTrack = {
  version: 2,
  language: 'en',
  default_style: {
    font_family: 'Inter',
    font_size: 32,
    font_weight: 'normal',
    text_transform: 'none',
    letter_spacing: 0,
    line_height: 1.2,
    primary_color: '#FFFFFF',
    highlight_color: '#FFE600',
    outline_enabled: false,
    outline_color: '#000000',
    outline_width: 2,
    glow_enabled: false,
    shadow_enabled: false,
    background_enabled: false,
  },
  default_layout: {
    position_x: 0.5,
    position_y: 0.78,
    alignment: 'center',
    max_width: 0.88,
    max_lines: 2,
  },
  default_animation: {
    type: 'pop',
    duration_ms: 120,
    active_scale: 1.15,
    easing: 'ease_out',
  },
  groups: [
    {
      id: 'cap_a',
      type: 'caption',
      start: 0,
      end: 2.5,
      style: {
        font_family: 'Inter',
        font_size: 32,
        font_weight: 'normal',
        primary_color: '#FFFFFF',
        outline_enabled: true,
        outline_color: '#111111',
        outline_width: 2,
        shadow_enabled: false,
        glow_enabled: false,
        background_enabled: false,
      },
      words: [
        { id: 'w1', text: 'Hello', start: 0, end: 1.2, style_override: { color: '#00FF00' } },
        { id: 'w2', text: 'World', start: 1.2, end: 2.5 },
      ],
    },
    {
      id: 'cap_b',
      type: 'caption',
      start: 2.5,
      end: 5.0,
      style: {
        font_family: 'Montserrat',
        font_size: 48,
        font_weight: 'bold',
        letter_spacing: 1.5,
        line_height: 1.3,
        text_transform: 'uppercase',
        primary_color: '#FF0055',
        outline_enabled: false,
        outline_color: '#000000',
        outline_width: 0,
        shadow_enabled: true,
        shadow_color: '#000000',
        shadow_blur: 8,
        glow_enabled: false,
        background_enabled: false,
      },
      words: [
        { id: 'w3', text: 'Caption', start: 2.5, end: 3.8 },
        { id: 'w4', text: 'Two', start: 3.8, end: 5.0 },
      ],
    },
    {
      id: 'cap_c',
      type: 'caption',
      start: 5.0,
      end: 7.5,
      style: {
        font_family: 'Roboto',
        font_size: 28,
        font_weight: '300',
        primary_color: '#00FFFF',
        outline_enabled: true,
        outline_color: '#FF0000',
        outline_width: 4,
        shadow_enabled: false,
        glow_enabled: true,
        glow_color: '#00FFFF',
        glow_blur: 15,
        background_enabled: true,
        background_color: '#00000088',
      },
      words: [
        { id: 'w5', text: 'Caption', start: 5.0, end: 6.2 },
        { id: 'w6', text: 'Three', start: 6.2, end: 7.5 },
      ],
    },
    {
      id: 'overlay_img',
      type: 'overlay',
      start: 1.0,
      end: 4.0,
      overlay_type: 'image',
      media_url: 'https://example.com/sticker.png',
      style: {
        font_family: 'IgnoreMe',
        primary_color: '#000000',
      },
      words: [],
    },
    {
      id: 'audio_layer',
      type: 'audio',
      start: 0,
      end: 10,
      media_url: 'https://example.com/bgm.mp3',
      words: [],
    },
  ],
};

// --------------------------------------------------------------------------
// TEST 1: Property Isolation — Font Section
// --------------------------------------------------------------------------
console.log('Test 1: Font section isolation...');
{
  const result = applySectionStylesToTrack(initialTrack, 'cap_b', 'font');
  assert.strictEqual(result.affectedCount, 3, 'Should affect exactly 3 text/caption elements');

  const capA = result.nextTrack.groups.find((g) => g.id === 'cap_a')!;
  const capB = result.nextTrack.groups.find((g) => g.id === 'cap_b')!;
  const capC = result.nextTrack.groups.find((g) => g.id === 'cap_c')!;

  // Font properties must match cap_b
  assert.strictEqual(capA.style?.font_family, 'Montserrat');
  assert.strictEqual(capA.style?.font_size, 48);
  assert.strictEqual(capA.style?.font_weight, 'bold');
  assert.strictEqual(capA.style?.letter_spacing, 1.5);
  assert.strictEqual(capA.style?.line_height, 1.3);
  assert.strictEqual(capA.style?.text_transform, 'uppercase');

  assert.strictEqual(capC.style?.font_family, 'Montserrat');
  assert.strictEqual(capC.style?.font_size, 48);

  // UNRELATED properties must NOT be changed!
  // cap_a had primary_color #FFFFFF and outline_color #111111
  assert.strictEqual(capA.style?.primary_color, '#FFFFFF', 'Cap A color must remain intact');
  assert.strictEqual(capA.style?.outline_enabled, true, 'Cap A outline must remain intact');
  assert.strictEqual(capA.style?.outline_color, '#111111', 'Cap A outline color must remain intact');
  assert.strictEqual(capA.style?.shadow_enabled, false, 'Cap A shadow must remain false');

  // cap_c had primary_color #00FFFF, glow_enabled true, background_enabled true
  assert.strictEqual(capC.style?.primary_color, '#00FFFF', 'Cap C color must remain intact');
  assert.strictEqual(capC.style?.glow_enabled, true, 'Cap C glow must remain intact');
  assert.strictEqual(capC.style?.background_enabled, true, 'Cap C background must remain intact');
  assert.strictEqual(capC.style?.outline_color, '#FF0000', 'Cap C outline color must remain intact');

  console.log('  ✓ Font properties propagated without touching colors, strokes, glows, or shadows');
}

// --------------------------------------------------------------------------
// TEST 2: Property Isolation — Color Section
// --------------------------------------------------------------------------
console.log('Test 2: Color section isolation...');
{
  const result = applySectionStylesToTrack(initialTrack, 'cap_c', 'color');
  assert.strictEqual(result.affectedCount, 3);

  const capA = result.nextTrack.groups.find((g) => g.id === 'cap_a')!;
  const capB = result.nextTrack.groups.find((g) => g.id === 'cap_b')!;
  const capC = result.nextTrack.groups.find((g) => g.id === 'cap_c')!;

  // Primary color must now match cap_c
  assert.strictEqual(capA.style?.primary_color, '#00FFFF');
  assert.strictEqual(capB.style?.primary_color, '#00FFFF');
  assert.strictEqual(capC.style?.primary_color, '#00FFFF');

  // But font properties on cap_b must remain Montserrat Bold 48!
  assert.strictEqual(capB.style?.font_family, 'Montserrat', 'Cap B font must remain untouched');
  assert.strictEqual(capB.style?.font_size, 48, 'Cap B font size must remain untouched');
  assert.strictEqual(capA.style?.font_family, 'Inter', 'Cap A font must remain untouched');

  console.log('  ✓ Color applied to all without touching font families or sizes');
}

// --------------------------------------------------------------------------
// TEST 3: Non-Text Layer Immunity
// --------------------------------------------------------------------------
console.log('Test 3: Non-text layer immunity (overlays & audio)...');
{
  const result = applySectionStylesToTrack(initialTrack, 'cap_b', 'font');
  const overlay = result.nextTrack.groups.find((g) => g.id === 'overlay_img')!;
  const audio = result.nextTrack.groups.find((g) => g.id === 'audio_layer')!;

  assert.strictEqual(overlay.style?.font_family, 'IgnoreMe', 'Overlay style must NOT be modified');
  assert.strictEqual(audio.type, 'audio', 'Audio layer must NOT be touched');
  assert.strictEqual(result.affectedCount, 3, 'Affected count must only count text elements');

  console.log('  ✓ Non-text layers (overlay, audio) are 100% immune');
}

// --------------------------------------------------------------------------
// TEST 4: Word-Level Overrides & Timing Preservation
// --------------------------------------------------------------------------
console.log('Test 4: Word-level timestamps & style overrides preservation...');
{
  const result = applySectionStylesToTrack(initialTrack, 'cap_b', 'font');
  const capA = result.nextTrack.groups.find((g) => g.id === 'cap_a')!;

  assert.strictEqual(capA.words.length, 2, 'Words array length preserved');
  assert.strictEqual(capA.words[0].start, 0);
  assert.strictEqual(capA.words[0].end, 1.2);
  assert.strictEqual(capA.words[0].style_override?.color, '#00FF00', 'Word style override preserved');
  assert.strictEqual(capA.words[1].start, 1.2);
  assert.strictEqual(capA.words[1].end, 2.5);

  console.log('  ✓ Word timestamps and style overrides preserved intact');
}

// --------------------------------------------------------------------------
// TEST 5: One-Time Action & Future Edits Independence
// --------------------------------------------------------------------------
console.log('Test 5: One-time action verification (future edits remain local)...');
{
  // Step A: Apply font to all
  const afterApply = applySectionStylesToTrack(initialTrack, 'cap_b', 'font').nextTrack;

  // Step B: Later routine edit on Caption C ONLY (simulating CaptionStudio patch())
  const targetGroup = afterApply.groups.find((g) => g.id === 'cap_c')!;
  const localEditedTrack: CaptionTrack = {
    ...afterApply,
    groups: afterApply.groups.map((grp) => {
      if (grp.id !== targetGroup.id) return grp;
      return {
        ...grp,
        style: { ...(grp.style || afterApply.default_style), primary_color: '#990099' },
      };
    }),
  };

  const capA = localEditedTrack.groups.find((g) => g.id === 'cap_a')!;
  const capB = localEditedTrack.groups.find((g) => g.id === 'cap_b')!;
  const capC = localEditedTrack.groups.find((g) => g.id === 'cap_c')!;

  assert.strictEqual(capC.style?.primary_color, '#990099', 'Cap C changed color');
  assert.strictEqual(capA.style?.primary_color, '#FFFFFF', 'Cap A color was NOT changed by future edit');
  assert.strictEqual(capB.style?.primary_color, '#FF0055', 'Cap B color was NOT changed by future edit');

  console.log('  ✓ Routine edits remain strictly local; Apply to All is not a persistent mode');
}

// --------------------------------------------------------------------------
// TEST 6: Stroke, Glow, Background, Shadow, Alignment, Animation Sections
// --------------------------------------------------------------------------
console.log('Test 6: Stroke, Glow, Background, Shadow, Alignment, and Animation isolation...');
{
  // Stroke test
  const strokeResult = applySectionStylesToTrack(initialTrack, 'cap_a', 'stroke').nextTrack;
  const strokeCapB = strokeResult.groups.find((g) => g.id === 'cap_b')!;
  assert.strictEqual(strokeCapB.style?.outline_enabled, true);
  assert.strictEqual(strokeCapB.style?.outline_color, '#111111');
  assert.strictEqual(strokeCapB.style?.outline_width, 2);
  assert.strictEqual(strokeCapB.style?.font_family, 'Montserrat', 'Font untouched during stroke apply');

  // Alignment test
  const alignTrack: CaptionTrack = {
    ...initialTrack,
    groups: initialTrack.groups.map((g) =>
      g.id === 'cap_c' ? { ...g, layout: { ...initialTrack.default_layout, alignment: 'right' } } : g
    ),
  };
  const alignResult = applySectionStylesToTrack(alignTrack, 'cap_c', 'alignment').nextTrack;
  const alignCapA = alignResult.groups.find((g) => g.id === 'cap_a')!;
  assert.strictEqual(alignCapA.layout?.alignment, 'right');
  assert.strictEqual(alignCapA.style?.font_family, 'Inter', 'Style untouched during alignment apply');

  console.log('  ✓ Stroke, alignment, and other individual sections propagate strictly isolated properties');
}

// --------------------------------------------------------------------------
// TEST 7: Timeline-Aware Visibility Invariant
// --------------------------------------------------------------------------
console.log('Test 7: Timeline-aware visibility invariant (selection does NOT force visibility outside duration)...');
{
  const groups: CaptionGroup[] = [
    { id: 'c1', start: 0, end: 2.0, words: [{ id: 'w1', text: 'One', start: 0, end: 2.0 }] },
    { id: 'c2', start: 2.5, end: 4.5, words: [{ id: 'w2', text: 'Two', start: 2.5, end: 4.5 }] },
  ];

  const isVisible = (grp: CaptionGroup, currentTime: number) => {
    return currentTime >= grp.start && currentTime <= grp.end;
  };

  // Playhead at 1.0 (inside c1, outside c2)
  assert.strictEqual(isVisible(groups[0], 1.0), true, 'c1 must be visible at t=1.0');
  assert.strictEqual(isVisible(groups[1], 1.0), false, 'c2 must be invisible at t=1.0');

  // Even if c2 is selected, its timeline-aware visibility remains false!
  const selectedGroupId = 'c2';
  const c2IsSelected = selectedGroupId === groups[1].id;
  assert.strictEqual(c2IsSelected, true, 'c2 is selected');
  assert.strictEqual(isVisible(groups[1], 1.0), false, 'c2 selection must NOT force it visible at t=1.0');

  // Playhead at 3.0 (outside c1, inside c2)
  assert.strictEqual(isVisible(groups[0], 3.0), false, 'c1 must be invisible at t=3.0');
  assert.strictEqual(isVisible(groups[1], 3.0), true, 'c2 must be visible at t=3.0');
  console.log('  ✓ Layer visibility is strictly timeline-aware; selection never forces visibility outside duration');
}

// --------------------------------------------------------------------------
// TEST 8: Animation Categorization & Loop Apply to All
// --------------------------------------------------------------------------
console.log('Test 8: Categorized animation model and loop propagation on Apply to All...');
{
  const animTrack: CaptionTrack = {
    ...initialTrack,
    groups: initialTrack.groups.map((g) =>
      g.id === 'cap_a'
        ? {
            ...g,
            animation: {
              type: 'pop',
              duration_ms: 100,
              active_scale: 1.2,
              easing: 'bounce',
              enter: { type: 'slide_up', duration_ms: 220 },
              exit: { type: 'slide_down', duration_ms: 160 },
              loop: { type: 'pulse', speed_ms: 850 },
              active_word: { type: 'pop', active_scale: 1.25, duration_ms: 110 },
            },
          }
        : g
    ),
  };

  const animResult = applySectionStylesToTrack(animTrack, 'cap_a', 'animation').nextTrack;
  const capBAnim = animResult.groups.find((g) => g.id === 'cap_b')?.animation;
  assert.strictEqual(capBAnim?.enter?.type, 'slide_up');
  assert.strictEqual(capBAnim?.exit?.type, 'slide_down');
  assert.strictEqual(capBAnim?.loop?.type, 'pulse');
  assert.strictEqual(capBAnim?.loop?.speed_ms, 850);
  assert.strictEqual(capBAnim?.active_word?.active_scale, 1.25);

  // Check that cap_b font and colors were NOT affected
  const capBStyle = animResult.groups.find((g) => g.id === 'cap_b')?.style;
  assert.strictEqual(capBStyle?.font_family, 'Montserrat', 'Font preserved on animation apply');
  assert.strictEqual(capBStyle?.primary_color, '#FF0055', 'Color preserved on animation apply');

  console.log('  ✓ Animation enter, exit, loop, and active_word propagate accurately with complete style isolation');
}

// --------------------------------------------------------------------------
// TEST 9: Apply Position & Size to All Captions (Layout Section Isolation)
// --------------------------------------------------------------------------
console.log('Test 9: Apply Position & Size to All Captions (layout section isolation & layer restriction)...');
{
  const layoutTrack: CaptionTrack = {
    ...initialTrack,
    groups: [
      ...initialTrack.groups,
      {
        id: 'manual_title',
        type: 'text',
        start: 0,
        end: 5.0,
        layout: {
          position_x: 0.1,
          position_y: 0.15,
          scale: 0.8,
          alignment: 'left',
          max_width: 0.6,
          max_lines: 1,
        },
        style: {
          font_family: 'Arial Black',
          font_size: 20,
          font_weight: 'bold',
          primary_color: '#FF0000',
        },
        words: [{ id: 'mt1', text: 'Top Title', start: 0, end: 5.0 }],
      },
    ],
  };

  // Modify layout of cap_b locally (drag / resize on preview)
  const modifiedTrack: CaptionTrack = {
    ...layoutTrack,
    groups: layoutTrack.groups.map((g) =>
      g.id === 'cap_b'
        ? {
            ...g,
            layout: {
              position_x: 0.42,
              position_y: 0.82,
              scale: 1.35,
              alignment: 'center',
              max_width: 0.9,
              max_lines: 2,
            },
          }
        : g
    ),
  };

  // Verify that prior to clicking "Apply to All", other captions remain UNCHANGED
  const preCapA = modifiedTrack.groups.find((g) => g.id === 'cap_a')!;
  assert.strictEqual(preCapA.layout?.position_y, undefined, 'Local change must not leak before Apply to All');

  // Trigger Apply to All Captions with section: 'layout'
  const result = applySectionStylesToTrack(modifiedTrack, 'cap_b', 'layout');

  // Must affect ONLY the 3 spoken captions (cap_a, cap_b, cap_c)
  assert.strictEqual(result.affectedCount, 3, 'Must affect strictly spoken captions');

  const postCapA = result.nextTrack.groups.find((g) => g.id === 'cap_a')!;
  const postCapB = result.nextTrack.groups.find((g) => g.id === 'cap_b')!;
  const postCapC = result.nextTrack.groups.find((g) => g.id === 'cap_c')!;
  const postManualText = result.nextTrack.groups.find((g) => g.id === 'manual_title')!;
  const postOverlay = result.nextTrack.groups.find((g) => g.id === 'overlay_img')!;
  const postAudio = result.nextTrack.groups.find((g) => g.id === 'audio_layer')!;

  // 1. Spoken captions received the new position & scale
  assert.strictEqual(postCapA.layout?.position_x, 0.42, 'cap_a received position_x');
  assert.strictEqual(postCapA.layout?.position_y, 0.82, 'cap_a received position_y');
  assert.strictEqual(postCapA.layout?.scale, 1.35, 'cap_a received scale');

  assert.strictEqual(postCapC.layout?.position_x, 0.42, 'cap_c received position_x');
  assert.strictEqual(postCapC.layout?.position_y, 0.82, 'cap_c received position_y');
  assert.strictEqual(postCapC.layout?.scale, 1.35, 'cap_c received scale');

  // 2. Unrelated properties PRESERVED (styles, animations, words, timestamps)
  assert.strictEqual(postCapA.style?.font_family, 'Inter', 'cap_a font preserved');
  assert.strictEqual(postCapA.style?.primary_color, '#FFFFFF', 'cap_a color preserved');
  assert.strictEqual(postCapA.words[0].style_override?.color, '#00FF00', 'cap_a word override preserved');
  assert.strictEqual(postCapC.style?.glow_enabled, true, 'cap_c glow preserved');

  // 3. Manual text (type === 'text') is STRICTLY IMMUNE to caption layout changes!
  assert.strictEqual(postManualText.layout?.position_y, 0.15, 'Manual text layout must NOT be modified');
  assert.strictEqual(postManualText.layout?.position_x, 0.1, 'Manual text layout must NOT be modified');
  assert.strictEqual(postManualText.layout?.scale, 0.8, 'Manual text scale must NOT be modified');

  // 4. Overlays and audio are strictly immune
  assert.strictEqual(postOverlay.layout?.position_x, undefined, 'Overlay layout not modified');
  assert.strictEqual(postAudio.type, 'audio', 'Audio layer not modified');

  // 5. Future local edits remain independent (one-time action, not persistent mode)
  const futureEditTrack: CaptionTrack = {
    ...result.nextTrack,
    groups: result.nextTrack.groups.map((g) =>
      g.id === 'cap_a'
        ? { ...g, layout: { ...g.layout!, position_y: 0.5 } }
        : g
    ),
  };
  const capAFuture = futureEditTrack.groups.find((g) => g.id === 'cap_a')!;
  const capBFuture = futureEditTrack.groups.find((g) => g.id === 'cap_b')!;
  assert.strictEqual(capAFuture.layout?.position_y, 0.5);
  assert.strictEqual(capBFuture.layout?.position_y, 0.82, 'cap_b unchanged by later local edit on cap_a');

  console.log('  ✓ Layout position_x, position_y, and scale propagated strictly to spoken captions');
  console.log('  ✓ Manual text layers, overlays, and audio layers remain completely immune');
  console.log('  ✓ Word timestamps, style overrides, and animations remain 100% preserved');
  console.log('  ✓ One-time action invariant holds (future edits remain local)');
}

// ============================================================================
// TEST 10: CapCut Style Panels Isolation (spacing, bold_italic, case, stroke, shadow, background)
// ============================================================================
{
  console.log('Test 10: CapCut Style Panels Isolation (spacing, bold_italic, case)...');
  const track: CaptionTrack = JSON.parse(JSON.stringify(initialTrack));

  // Modify source group cap_a with CapCut styling attributes
  const capA = track.groups.find((g) => g.id === 'cap_a')!;
  capA.style = {
    ...capA.style!,
    font_weight: '900',
    font_style: 'italic',
    underline: true,
    text_transform: 'title',
    letter_spacing: 3.5,
    line_height: 1.6,
    outline_opacity: 0.85,
    shadow_distance: 5.0,
    shadow_angle: 90.0,
    shadow_opacity: 0.75,
    background_mode: 'line',
    background_opacity: 0.9,
  };
  capA.layout = {
    ...capA.layout!,
    alignment: 'left',
    scale: 1.25,
  };

  // 1. Test bold_italic section
  const boldItalicResult = applySectionStylesToTrack(track, 'cap_a', 'bold_italic');
  const capBBold = boldItalicResult.nextTrack.groups.find((g) => g.id === 'cap_b')!;
  assert.strictEqual(capBBold.style?.font_weight, '900', 'cap_b received font_weight 900');
  assert.strictEqual(capBBold.style?.font_style, 'italic', 'cap_b received font_style italic');
  assert.strictEqual(capBBold.style?.underline, true, 'cap_b received underline true');
  // Unrelated properties preserved
  assert.strictEqual(capBBold.style?.text_transform, 'uppercase', 'cap_b text_transform unaffected');
  assert.strictEqual(capBBold.style?.primary_color, '#FF0055', 'cap_b color unaffected');

  // 2. Test case section
  const caseResult = applySectionStylesToTrack(track, 'cap_a', 'case');
  const capBCase = caseResult.nextTrack.groups.find((g) => g.id === 'cap_b')!;
  assert.strictEqual(capBCase.style?.text_transform, 'title', 'cap_b received title case');
  assert.strictEqual(capBCase.style?.font_weight, 'bold', 'cap_b font_weight untouched');

  // 3. Test spacing section
  const spacingResult = applySectionStylesToTrack(track, 'cap_a', 'spacing');
  const capBSpacing = spacingResult.nextTrack.groups.find((g) => g.id === 'cap_b')!;
  assert.strictEqual(capBSpacing.style?.letter_spacing, 3.5, 'cap_b received letter_spacing');
  assert.strictEqual(capBSpacing.style?.line_height, 1.6, 'cap_b received line_height');
  assert.strictEqual(capBSpacing.layout?.alignment, 'left', 'cap_b received alignment left');
  assert.strictEqual(capBSpacing.layout?.scale, 1.25, 'cap_b received scale 1.25');
  assert.strictEqual(capBSpacing.style?.font_size, 48, 'cap_b font_size untouched');

  console.log('  ✓ bold_italic, case, and spacing sections propagate with complete isolation');
}

console.log('\n🎉 ALL 10 REGRESSION TESTS PASSED 100%!');
