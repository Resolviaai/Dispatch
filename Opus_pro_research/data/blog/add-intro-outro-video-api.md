---
url: https://www.opus.pro/blog/add-intro-outro-video-api
title: "Add Branded Intros and Outros via API (OpusClip) - OpusClip Blog"
description: "Add branded intros and outros to every video automatically with the OpusClip API. Full walkthrough for prepending bumpers and CTA cards at scale."
category: blog
word_count: 1091
---

# Add Branded Intros and Outros to Video via the OpusClip API

Branded intros and outros are the easiest, highest-leverage consistency move a video team can make. A 2-second logo bumper at the start and a 5-second CTA card at the end signal production value, drive subscribes, and surface CTAs without a creator needing to remember them. A render API automates the entire workflow: pass a source video plus your intro/outro assets, get back a fully composited output.

This guide is a developer-focused look at how render APIs handle intro/outro composition and how the OpusClip API supports branded production workflows.

**The OpusClip API is now publicly available** — included on the free trial and the Pro plan, with no waitlist or sales call required. [Get started at opus.pro/api](https://www.opus.pro/api), and find the full endpoint and parameter reference in the [API docs](https://help.opus.pro/api-reference/overview).

## Key takeaways

• Intro/outro composition is one of the simplest but most-repetitive video tasks — automating it saves real production time.

• Render APIs accept any combination of static images (auto-converted to video) or pre-rendered MP4s.

• Aspect-ratio mismatches between source and intro/outro need handling (cover, contain, stretch, or crop-center modes).

• Audio handling has three modes: sequential, ducked, source-only.

• The OpusClip API supports intro/outro composition as a standalone render step and as a built-in option in clip generation.

## Why automate intros and outros

The economics are obvious once you do the math:

• A creator producing 4 videos per week adds intros/outros manually. Even at 2 minutes per video, that's 8 minutes weekly. 7+ hours per year.

• A team producing 40 videos per week loses 80+ minutes weekly to this single task. 70+ hours per year.

• Branded consistency is also a quality issue. Manual editing produces slightly different intros each time. Automated rendering is identical every time.

For any team producing video at scale, this is one of the obvious things to automate first.

## What a render API does for intros and outros

Three operations:

1\. **Composition.** Stitch the intro segment + source + outro segment into one timeline. Handle aspect-ratio mismatches with the configured fit mode.

2\. **Transitions.** Apply cross-dissolves, fades, slides, or hard cuts between segments. Configurable transition duration per junction.

3\. **Audio handling.** Decide how the intro audio interacts with source audio (sequential, ducked underneath, source-only) and similarly for the outro.

A good API also handles edge cases — resolution mismatches, audio sample rate differences, codec variations — without dropping quality.

## What to consider when integrating

**Asset format support.** Modern APIs accept MP4, MOV, image files (PNG/JPG auto-converted to video segments). Confirm yours supports what you'll bring.

**Aspect ratio handling.** Source is 16:9, intro is 9:16. Without explicit handling, you get black bars or stretched content. Fit modes (cover, contain, stretch, crop-center) determine behavior.

**Audio mode.** Sequential is most common (intro audio plays, then source audio plays, then outro audio plays). Ducked plays source audio underneath intro music. Source-only ignores intro/outro audio entirely. Pick the right mode for your workflow.

**Transition style.** Cross-dissolve at 0.4-0.8s is the workhorse. Hard cuts feel jarring on branded content. Long fades over 1s feel slow.

**Resolution and quality.** A 4K intro on a 1080p source forces the output to lower resolution. For best quality, render intros at multiple resolutions and pick the matching one.

**Dynamic text overlay.** Episode numbers, dates, or topic-tagged variations need text overlay on top of a base intro. Many APIs support this; check before relying.

## Common use cases by team type

• **Creators.** Every video gets the same intro bumper and a "Subscribe" outro automatically.

• **Brand marketing.** Every customer story video gets the same branded intro with the customer name overlaid.

• **Podcasters with video.** Episode-numbered intros that automatically reference the current episode number.

• **Course creators.** Every lesson gets a course-branded intro plus a "Next lesson" outro card.

• **Internal video.** All-hands recordings get a company logo intro and a "Send feedback to..." outro.

## Common pitfalls

• **Long intros (>4 seconds).** Retention drops sharply during intros over 3 seconds. Keep bumpers tight.

• **Outro retention is short.** Even branded subscribe-card outros lose 30-50% of viewers in the first 2 seconds. Make the CTA visible immediately.

• **Audio crossfades over 1 second.** Feel slow. 0.4-0.8 seconds is the sweet spot.

• **Resolution mismatches.** A 4K intro applied to a 1080p source scales the whole output down. Pre-render intros at the resolution(s) you actually need.

• **Aspect-ratio mismatches.** Without explicit fit-mode configuration, you'll get unexpected letterboxing or cropping. Confirm your fit mode for vertical vs. horizontal targets.

## How the OpusClip render API works

The OpusClip API is generally available. The render workflow for intros/outros is built around:

• Intro and outro asset URLs (MP4 or image) per job

• Configurable transitions (cut, fade, cross-dissolve, slide, wipe, zoom) per junction

• Aspect-ratio fit modes (cover, contain, stretch, crop-center)

• Audio modes (sequential, ducked, source-only)

• Dynamic text overlay support for episode-numbered or topic-tagged variations

• Multi-asset chaining (intro + mid-roll bumper + outro) for ad-supported workflows

Full endpoint and parameter reference lives in the [API documentation](https://help.opus.pro/api-reference/overview); generate an API key from your OpusClip dashboard to start building.

## FAQ

### Can I add a mid-roll bumper between chapters?

Yes — the render endpoint will support multiple insert points, not just intro and outro. Useful for sponsor breaks or chapter markers.

### Does the API generate intros and outros for me?

The render endpoint composites existing assets. For generating new branded intros, use a design tool (Figma, Canva) or generative video tools. The render step composites whatever you bring.

### Can I customize the intro per video (e.g., dynamic episode number)?

Yes — dynamic text overlay on a base intro is a standard pattern. Pass overlay text with timing and styling, and the API renders it on top of the intro segment.

### How is this different from ffmpeg concatenation?

ffmpeg works for simple cases. The OpusClip render API adds aspect-ratio handling, audio crossfade, hardware-accelerated rendering, and integration with the rest of the OpusClip pipeline. For pure concatenation at scale, ffmpeg is cheaper; for production quality with edge cases handled, the API is faster end-to-end.

### Can I have different intros for different content types?

Yes — the render API accepts any URL. Maintain a map of (content\_type → intro\_url) in your application and pass the right one per job.

## Next steps

For full publishing automation that includes intros/outros, see [Build a YouTube-to-TikTok Automation](/blog/youtube-to-tiktok-automation). For repurposing pipelines, see [Auto-Generate Shorts from a Podcast](/blog/podcast-to-shorts-api) and [Build a Webinar-to-Shorts Pipeline](/blog/webinar-to-shorts-pipeline).