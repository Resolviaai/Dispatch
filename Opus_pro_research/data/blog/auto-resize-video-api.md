---
url: https://www.opus.pro/blog/auto-resize-video-api
title: "Auto-Resize Video to 9:16, 16:9, 1:1 (OpusClip API) - OpusClip Blog"
description: "Reframe horizontal video to vertical, square, or any aspect ratio with smart subject tracking. Full code walkthrough of the OpusClip reframing API."
category: blog
word_count: 1297
---

# Auto-Resize Videos to 9:16, 16:9, and 1:1 with the OpusClip API

Every platform demands a different aspect ratio. TikTok and Reels want 9:16 vertical. YouTube takes 16:9 horizontal but Shorts wants 9:16. LinkedIn rewards 1:1 square. X (Twitter) feed video prefers 16:9 but supports vertical. If you film once and post everywhere, you need a way to reframe — and letterboxing isn't it. Black bars cut your usable screen area by 30-50% and destroy engagement on vertical-first platforms.

Smart reframing APIs solve this. They detect the subject (face, motion, salient region), track it across the timeline, and crop to any target aspect ratio while keeping the subject in frame. This guide is a developer-focused look at how smart reframing works and how the OpusClip API fits in.

**The OpusClip API is now publicly available** — included on the free trial and the Pro plan, with no waitlist or sales call required. [Get started at opus.pro/api](https://www.opus.pro/api), and find the full endpoint and parameter reference in the [API docs](https://help.opus.pro/api-reference/overview).

## Key takeaways

• Smart reframing uses face detection plus motion-based saliency to track the subject across the timeline, then crops to fit any target aspect ratio.

• Multi-speaker content benefits from speaker-switching reframing, where the camera cuts between active speakers based on diarization.

• Letterboxing (black bars) loses 30-50% of screen area on vertical platforms; smart reframing keeps the full canvas usable.

• One source video should typically produce 9:16, 1:1, and 16:9 outputs from a single API call.

• The OpusClip API supports multi-aspect-ratio output, configurable tracking modes (faces, motion, speaker switching), and Zoom/Loom UI awareness.

## Why letterboxing isn't enough

The simplest way to "convert" horizontal video to vertical is to letterbox it — keep the original 16:9 frame and pad with black above and below. It "works" technically but kills engagement.

Why:
- **Usable screen area drops 30-50%.** On a 9:16 phone screen, a letterboxed 16:9 video uses only the middle \~56% of the height.
- **Algorithms penalize black-bar content.** TikTok and Reels both deprioritize letterboxed video in the feed because it signals "lazy repurpose."
- **The subject ends up tiny.** A talking head that filled an entire 16:9 frame is now a small square in the middle of a tall screen.

Smart reframing crops the horizontal frame down to 9:16 by following the subject — face, motion, or active speaker. The output uses the full vertical canvas with the subject correctly framed.

## What a smart reframing API does

Three stages:

1\. **Subject detection.** Run face detection or motion saliency on each frame (or each frame group). Identify the dominant subject's bounding box.

2\. **Temporal smoothing.** Raw frame-by-frame tracking jitters. A smoothing pass uses splines or low-pass filtering on the bounding-box center to produce camera-like movement (slow pans, holds, smooth tracks).

3\. **Crop and re-render.** Render the output at the target aspect ratio, with the crop window centered on the smoothed subject path.

Different content types need different signals:
- **Talking head:** face detection works best.
- **Screen recording:** cursor and saliency tracking (not faces).
- **Multi-speaker:** speaker switching based on diarization, cutting between active speakers.
- **B-roll or action:** motion saliency, following the most active region.

A good API exposes all four tracking modes as configuration options.

## What to consider when integrating

**Tracking mode for your content type.** Default face tracking fails on screen recordings, animation, and b-roll. Make sure the API supports saliency, speaker switching, and explicit region tracking.

**Multi-aspect-ratio output from one job.** Don't pay 3x for 3 aspect ratios. Look for APIs that render multiple outputs in a single call.

**Smoothing intensity.** Too little smoothing produces jittery camera-shake output. Too much smoothing means the camera lags behind the subject. The best APIs tune this automatically; the OK ones let you tune it manually.

**Overlay preservation.** Loom bubble-cams, Zoom UI chrome, podcast cover art, picture-in-picture inserts — these all need handling. Some get preserved (bubble-cam), others get cropped out (Zoom chrome).

**Resolution and quality.** Smart reframing means cropping. Cropping a 1080p source to 9:16 produces a 608x1080 output at native resolution. For 1080x1920 vertical output you need source upscaling, which most APIs handle but quality varies.

**Subject-loss handling.** What happens when the subject leaves the frame entirely (looks away, ducks down, switches positions off-camera)? Good APIs hold position; bad ones snap to the center.

## Common use cases by team type

• **Social media teams.** One horizontal master → 9:16 TikTok/Reels/Shorts + 1:1 LinkedIn + 4:5 Instagram feed, all from one API call.

• **Podcasters.** Convert horizontal video podcast recordings to vertical for clip distribution without re-shooting.

• **Course creators.** Take 16:9 lesson recordings and produce vertical previews for social marketing without re-editing.

• **Sales enablement.** Reframe customer testimonials and case study videos for use in vertical social ads and LinkedIn campaigns.

• **News and editorial.** Reframe interview footage for TikTok-native news distribution.

## Common pitfalls

• **Defaulting to face tracking on non-face content.** Screen recordings need saliency tracking. Animation needs explicit region tracking. Music videos with cuts need cut-aware tracking. Match the mode to the content.

• **Trusting auto-detection on multi-subject scenes.** Two people on opposite sides of the frame confuses face trackers. Either tell the API which subject to follow, or use speaker-switching mode and rely on diarization.

• **Cropping out important on-screen text.** Lower-thirds, slide content, watermarks — these get cropped out aggressively when reframing to vertical. Either reposition them in the source, or use APIs that detect and preserve text regions.

• **Forgetting captions need re-positioning.** If you burn captions into the horizontal source, then reframe to vertical, the caption position shifts. Reframe first, then add captions.

• **One-size-fits-all smoothing.** A talking head can have aggressive smoothing (subject barely moves). A sports clip needs minimal smoothing (camera follows fast motion). Tune per content type.

## How the OpusClip reframing will work

The OpusClip API is generally available. The reframing workflow supports:

• Multi-aspect-ratio output (9:16, 1:1, 4:5, 16:9, custom) from a single submission

• Tracking modes: face detection, saliency, speaker switching, explicit region

• Source-aware handling for Zoom, Loom, screen recordings, and podcast video

• Optional smart zoom (gentle zoom-in on the active region for added emphasis)

• Per-output configuration (different captions, different durations, different overlays)

Full endpoint and parameter reference lives in the [API documentation](https://help.opus.pro/api-reference/overview); generate an API key from your OpusClip dashboard to start building.

## FAQ

### Why not just letterbox to fit a new aspect ratio?

Black bars cut usable screen area by 30-50% on vertical platforms, which destroys engagement. Algorithms also deprioritize letterboxed content. Smart reframing keeps the subject in frame and uses the full target canvas.

### How does the API decide what to keep in frame?

Subject detection runs every frame (faces or motion or salient regions), then a smoothing pass prevents jitter. You can override the default by passing a tracking mode appropriate to your content.

### What if there are multiple speakers and the camera should switch between them?

Pair speaker diarization with speaker-switching reframing. The API detects which speaker is active and cuts between them, producing podcast-style multi-speaker vertical video.

### Can I output multiple aspect ratios from one API call?

Yes — that's the standard pattern. Submit once, render to 9:16 + 1:1 + 4:5 in parallel, get all outputs back from one job.

### How does reframing handle Loom or Zoom recordings?

The OpusClip API detects screen-recording sources and switches to saliency tracking automatically. It also preserves the Loom bubble-cam as a corner overlay and crops out Zoom UI chrome. Those details are in the [API docs](https://help.opus.pro/api-reference/overview).

## Next steps

For combining reframing with full clip generation, see [Auto-Generate Shorts from a Podcast](/blog/podcast-to-shorts-api), [Build a Webinar-to-Shorts Pipeline](/blog/webinar-to-shorts-pipeline), and [Generate YouTube Shorts from Long Videos](/blog/youtube-shorts-from-long-video-api). For screen-recording-specific workflows, see [Turn Screen Recordings into Social Shorts](/blog/screen-recording-to-shorts-api).