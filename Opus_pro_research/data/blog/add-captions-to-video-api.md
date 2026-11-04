---
url: https://www.opus.pro/blog/add-captions-to-video-api
title: "Add Captions to Video with the OpusClip API (2026 Guide) - OpusClip Blog"
description: "Add styled, word-by-word captions to any video in one API call. Full Python + Node + cURL walkthrough with the OpusClip video captions API."
category: blog
word_count: 1459
---

# How to Add Captions to a Video with the OpusClip API

Captions are the single highest-leverage edit you can make to any video. Over 80% of social video is watched without sound. Accessibility law in most jurisdictions now mandates captions for public-facing video. And the engagement lift from captions on platforms like TikTok, Reels, and YouTube Shorts has been replicated in study after study — anywhere from 12% to 40% improvement in watch time and completion.

Doing it by hand doesn't scale. A creator producing five clips a week can manage it. A team producing fifty? An LMS company captioning ten thousand course videos? Not a chance. That's why a captions API is the right primitive for any team doing video at volume.

This guide is a developer-focused look at what a video captions API does, what to expect when integrating one, and how the OpusClip API will fit in when it goes generally available. **The OpusClip API is available on the Pro (Beta), Max and Business plans**, with your API key in the OpusClip dashboard. See [opus.pro/api](https://www.opus.pro/api) for docs.

## Key takeaways

• A video captions API handles three things in one workflow: speech-to-text, timing alignment, and on-screen rendering (burn-in) or sidecar file (SRT/VTT) output.

• Quality is driven by the transcription model's language coverage, accent robustness, and accuracy on noisy audio — not by the renderer.

• Caption styling (font, color, animation, position, highlight emphasis) is what separates platform-native captions from generic auto-captions. This is where most off-the-shelf APIs underperform.

• For social video, burned-in captions outperform sidecar caption files because most platforms don't surface CC by default.

• The OpusClip captions endpoint is designed for social-native output: word-by-word reveal, configurable highlight emphasis, multi-language support, and burn-in MP4 in one call.

## Why captions are non-negotiable for video at scale

The data is consistent across platforms:

• **84% of Facebook video is watched on mute** (Verizon Media). Without captions, that's 84% of viewers losing the message.

• **YouTube content with captions sees \~12% more watch time on average** (Discovery Digital Networks).

• **TikTok creators who burn in captions report 25-40% lifts in completion rate** in self-reported case studies — the platform itself promotes captions as a top engagement lever.

• **80% of consumers are more likely to finish a video when captions are present** (Verizon).

Beyond engagement, captions are an accessibility requirement under the ADA (US), the European Accessibility Act (EU), and Section 508 (US federal). For any business serving an enterprise or public-sector buyer, missing captions can disqualify you from procurement.

## What a video captions API actually does

Under the hood, a captions API stitches together three steps that used to require three different tools:

1\. **Transcription.** Audio extracted from the video is run through a speech-to-text model. The best models support 30+ languages, handle accents, and degrade gracefully on overlapping speakers and background music.

2\. **Timing alignment.** Each word is mapped to a start and end timestamp. For burned-in captions, this drives the word-by-word reveal animation. For sidecar files, this generates the SRT or VTT timing.

3\. **Rendering.** Captions are styled (font, size, color, highlight emphasis, position, animation) and either burned into the output video as pixels or saved as a separate caption file the player loads at runtime.

A good API exposes configuration knobs for each step — language selection, caption styling, output format — and returns either a finished video file or both the video and a sidecar SRT/VTT.

## What to consider when integrating a captions API

A few decisions you'll need to make before you ship:

**Burn-in versus sidecar.** Burn-in is durable: captions ship with the video and can't be turned off, which is what you want for social platforms where most viewers won't enable CC. Sidecar files (SRT, VTT) keep the caption editable and accessibility-tool-friendly, which is what you want for course platforms, web players you control, and YouTube uploads where the platform handles CC display.

**Language and accent coverage.** Major English-only APIs still struggle with strong accents and code-switching. If your audience speaks Spanish, Hindi, Mandarin, Portuguese, Arabic, or any non-English language at meaningful volume, confirm the model's published accuracy benchmarks in those languages before committing.

**Styling control.** Default captions look generic and dated. Look for an API that lets you control font family, size, weight, text color, background or stroke color, highlight color for emphasis words, position on screen, and animation style (word-by-word reveal vs. line-by-line vs. static).

**Processing time and async patterns.** Captioning a 30-minute video takes 30 seconds to several minutes depending on the model and infrastructure. Plan your integration as async from day one — submit a job, poll or listen for a webhook, retrieve the result.

**Cost structure.** Most APIs price per minute of video processed. Compare apples-to-apples by running the same source through a few options and noting both cost and quality.

## Common use cases by team type

• **Social media teams.** Burn captions into every clip exported for TikTok, Reels, and YouTube Shorts. Pair with auto-clipping to get a fully automated long-form-to-social pipeline.

• **Podcasters and creators.** Generate audiograms and clips with on-screen captions to make audio content shareable on visual platforms.

• **Course creators and LMS platforms.** Generate SRT files for every lesson video to satisfy accessibility requirements and improve searchability inside the course player.

• **Marketing teams.** Add captions to product demos, customer story videos, and webinar recordings before they ship to landing pages and ad accounts.

• **Internal communications.** Caption all-hands recordings, training videos, and async updates so they're accessible and skimmable for distributed teams.

## Common pitfalls

• **Trusting auto-transcription without review for high-stakes content.** Legal, medical, and PR-sensitive video should always have a human review pass. Expect 92-97% word accuracy from modern APIs on clear audio — good enough for social, not good enough for litigation.

• **Captions that overlap platform UI.** TikTok, Reels, and Shorts each have UI chrome (like and comment buttons, captions text, hashtags) at the bottom of the frame. Captions positioned at the very bottom get covered. Move them up.

• **Caption text too small on phone screens.** What looks fine on your desktop preview reads as tiny on a phone. Default to 40-60pt at 1080p, larger if your audience skews older.

• **Forgetting right-to-left languages.** Arabic and Hebrew render right-to-left. If your API doesn't handle text direction automatically, you'll ship broken captions on those languages.

• **No fallback for music-heavy or low-audio segments.** Some clips just don't have speech. Your pipeline should either skip captioning those segments or surface them for manual handling.

## How captions work in the OpusClip API

Captions are generated as part of OpusClip API clip projects, available on the Pro (Beta), Max and Business plans. The endpoint is designed for social-native output — word-by-word reveal, configurable highlight emphasis (call out the most important words in a different color), 30+ language support, and burn-in MP4 or sidecar files from one call.

Designed for developers who want one call, not a chain of tools:

• One request submits the video and configuration.

• The job runs async; you poll or listen for a webhook.

• The response delivers the finished video plus the structured transcript and caption file.

The parameter reference and quickstart live in the [OpusClip API docs](https://help.opus.pro/api-reference/overview).

## FAQ

### How accurate are modern auto-transcription APIs?

Production APIs publish 92-97% word-level accuracy on clear English audio. Accuracy drops with background music, overlapping speakers, strong accents, and low-bandwidth sources. For high-stakes content (legal, medical, regulatory), human review is still required.

### Should I burn captions in or use SRT/VTT sidecar files?

For social video, burn captions in — most viewers don't enable CC. For long-form video on YouTube, your own player, or course platforms, ship SRT/VTT files so users can toggle them and search inside them.

### Which languages should a video captions API support?

Look for 30+ languages including Spanish, Portuguese, French, German, Hindi, Mandarin, Japanese, Korean, and Arabic at minimum. Confirm the model's published accuracy in your target languages before committing.

### How long does it take to caption a video programmatically?

Roughly 0.05–0.2× the source video duration on modern APIs. A 10-minute video processes in 30 seconds to 2 minutes. Build your integration assuming async — don't block a user request on a transcription job.

### Does the OpusClip API expose styling controls?

Yes. The captions endpoint is designed for full styling control — font, size, weight, position, animation, and per-word highlight emphasis. Detailed parameter reference will publish to the developer docs at GA.

## Next steps

For automating the full workflow from long-form video to captioned shorts, see [Auto-Generate Shorts from a Podcast](/blog/podcast-to-shorts-api), [Convert Zoom Recordings to Social Clips](/blog/zoom-recording-to-clips-api), and [Build a YouTube-to-TikTok Automation](/blog/youtube-to-tiktok-automation). For non-English audiences, see [Multi-Language Captions Tutorial](/blog/multi-language-captions-api).