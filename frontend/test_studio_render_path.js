/** Selected-clip, preview, caption-track and timeline render regression. */

import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CaptionDock } from './src/studio/CaptionDock';
import { PreviewMediaNotice, PreviewStage } from './src/studio/PreviewStage';
import { ClipLibraryNotice } from './src/studio/ClipLibraryNotice';
import { parseClipListResponse } from './src/studio/clipLibrary';
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

const previewProps = {
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
  onRetryClip: () => {},
};
const previewMarkup = renderToStaticMarkup(React.createElement(PreviewStage, previewProps));
assert.match(previewMarkup, /Selected fixture clip/, 'selected clip mounts the preview header');
assert.match(previewMarkup, /<video/, 'selected clip mounts its video preview');
assert.match(previewMarkup, /Editor/, 'normalized captions mount in the preview canvas');

const unavailablePreviewMarkup = renderToStaticMarkup(React.createElement(PreviewStage, {
  ...previewProps,
  clip: { ...firstClip, video_url: null },
  track: null,
}));
assert.match(unavailablePreviewMarkup, /Preview source unavailable/, 'missing media keeps the recovery notice');
assert.match(unavailablePreviewMarkup, /Retry preview/, 'missing media keeps its retry action');

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

const loadingTimelineMarkup = renderToStaticMarkup(React.createElement(CaptionDock, {
  track: null,
  currentTime: 0,
  duration: 4,
  onSeek: () => {},
  onUpdateTrack: () => {},
  selectedGroupId: null,
  onSelectGroup: () => {},
  captionLoadState: 'loading',
}));
assert.match(loadingTimelineMarkup, /Loading captions/, 'caption loading stays visible');

const captionErrorTimelineMarkup = renderToStaticMarkup(React.createElement(CaptionDock, {
  track: null,
  currentTime: 0,
  duration: 4,
  onSeek: () => {},
  onUpdateTrack: () => {},
  selectedGroupId: null,
  onSelectGroup: () => {},
  captionLoadState: 'error',
  captionLoadError: 'HTTP 503: captions unavailable',
  onRetryCaptions: () => {},
}));
assert.match(captionErrorTimelineMarkup, /HTTP 503: captions unavailable/, 'caption errors stay visible');
assert.match(captionErrorTimelineMarkup, /Retry/, 'caption errors keep a retry action');

const clipErrorMarkup = renderToStaticMarkup(React.createElement(ClipLibraryNotice, {
  state: 'error',
  error: 'HTTP 503: Failed to load clips',
  onRetry: () => {},
}));
assert.match(clipErrorMarkup, /Couldn’t load clips/, 'clip-list failures are visible');
assert.match(clipErrorMarkup, /HTTP 503/, 'clip-list failure detail is surfaced');
assert.match(clipErrorMarkup, />Retry</, 'clip-list failure exposes a retry action');

const emptyClipMarkup = renderToStaticMarkup(React.createElement(ClipLibraryNotice, {
  state: 'loaded',
  onRetry: () => {},
}));
assert.match(emptyClipMarkup, /No clips available/, 'a successful empty library is distinct from a failed request');

const previewErrorMarkup = renderToStaticMarkup(React.createElement(PreviewMediaNotice, {
  title: 'Video preview failed to load',
  detail: 'The media may be temporarily unreachable.',
  onRetry: () => {},
}));
assert.match(previewErrorMarkup, /role="alert"/, 'video-load failures are announced');
assert.match(previewErrorMarkup, /Retry preview/, 'video-load failures expose a retry action');

assert.deepEqual(parseClipListResponse([]), [], 'a valid empty array is an empty library');
assert.equal(parseClipListResponse({ ready: [{ id: 'clip-loaded' }], recent_approved: [] })[0].id, 'clip-loaded');
assert.throws(() => parseClipListResponse({}), /invalid response/, 'unknown API payloads are not silently shown as empty libraries');
assert.throws(() => parseClipListResponse([{ title: 'missing id' }]), /invalid clip entry/);

console.log('  passed: selection, preview/timeline mounting, clip loading states, and media recovery UI');
