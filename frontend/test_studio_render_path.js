/** Selected-clip, preview, caption-track and timeline render regression. */

import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CaptionDock } from './src/studio/CaptionDock';
import { PreviewStage } from './src/studio/PreviewStage';
import { normalizeCaptionTrack, resolveStudioClip, trackForClip } from './src/captions/normalizeTrack';

console.log('Studio clip render regressions...');

const firstClip = { id: 'clip-a', title: 'Selected fixture clip', duration: 4, video_url: '/clips/fixture.mp4', start_time: 0, end_time: 4 };
const secondClip = { id: 'clip-b', title: 'Other clip', duration: 2, start_time: 0, end_time: 2 };
assert.equal(resolveStudioClip([firstClip, secondClip], 'clip-b')?.id, 'clip-b');
assert.equal(resolveStudioClip([firstClip, secondClip], 'missing')?.id, 'clip-a');

const normalized = normalizeCaptionTrack({
  groups: [
    { id: 'caption-a', start: 0, end: 2, words: [{ id: 'word-a', text: 'Editor', start: 0, end: 1 }] },
    null,
  ],
}, 'clip-a', firstClip.duration);
assert.ok(normalized.default_style, 'caption preview receives required default style');
assert.ok(normalized.default_layout, 'caption preview receives required default layout');
assert.ok(normalized.default_animation, 'caption preview receives required default animation');
assert.equal(normalized.groups.length, 2, 'malformed group entries are normalized safely');

const currentTrack = trackForClip('clip-a', 'clip-a', normalized);
assert.equal(currentTrack, normalized);
assert.equal(trackForClip('clip-b', 'clip-a', normalized), null, 'a newly selected clip cannot render the previous clip captions');

const previewMarkup = renderToStaticMarkup(React.createElement(PreviewStage, {
  clip: firstClip,
  track: currentTrack,
  currentTime: 0,
  duration: 4,
  isPlaying: false,
  onTimeUpdate: () => {},
  onDurationChange: () => {},
  onTogglePlay: () => {},
  onSeek: () => {},
  onUpdateTrackGroup: () => {},
  selectedGroupId: 'caption-a',
  onSelectGroup: () => {},
  onLayoutChange: async () => {},
  onTriggerRender: async () => {},
  isRendering: false,
  pcHost: 'localhost:8000',
}));
assert.match(previewMarkup, /Selected fixture clip/, 'selected clip mounts the preview header');
assert.match(previewMarkup, /<video/, 'selected clip mounts its video preview');
assert.match(previewMarkup, /Editor/, 'normalized captions mount in the preview canvas');

const timelineMarkup = renderToStaticMarkup(React.createElement(CaptionDock, {
  track: currentTrack,
  currentTime: 0,
  duration: 4,
  onSeek: () => {},
  onUpdateTrack: () => {},
  selectedGroupId: 'caption-a',
  onSelectGroup: () => {},
  captionLoadState: 'success',
}));
assert.match(timelineMarkup, /Caption Timeline/, 'selected clip mounts the timeline');
assert.match(timelineMarkup, /Editor/, 'normalized caption word is available in the timeline');

console.log('  passed: clip resolution, stale-track isolation, normalized preview, and timeline mounting');
