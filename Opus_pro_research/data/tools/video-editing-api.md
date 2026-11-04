---
url: https://www.opus.pro/tools/video-editing-api
title: "Video Editing API for Clips and Captions | OpusClip"
description: "A video editing API that edits: trim, split, reorder, cut phrases, fix captions, remove filler words, dub, then export HD. Base URL api.opus.pro, 30 req/min."
category: tools
word_count: 1662
---

# Video Editing API

One call applies named operations to a clip: caption, emoji and keyword-highlight toggles, filler-word and pause removal, section trim, split, drop and reorder, phrase cutting, caption colour, position and casing, dubbing, and undo.

Removal operations shorten the clip and keep video, captions and audio in sync, so you are not re-aligning subtitles afterwards.

## What separates an editing API from a render farm

### Edits stay in sync

Cutting a phrase or a pause shortens the clip and moves captions and audio with it. The output is a finished clip, not a timeline you still have to repair.

### It knows what was said

Transcripts come back as paragraphs with start and end times in seconds, an optional speaker, and per-word timings, which is what makes phrase-level cuts possible.

### Undo is an operation

Undo is part of the same named-operation set, so an automated edit pass can roll a step back instead of rebuilding the clip from scratch.

## Fix captions without opening an editor

Toggle captions, emoji and keyword highlights, change caption colour, position and casing, or correct a single typo in one clip.

The preview re-renders after the call, so the next read shows the corrected cut.

[See the operations](https://clip.opus.pro/dashboard)

[See the operations](#)

## Fix captions without opening an editor

Toggle captions, emoji and keyword highlights, change caption colour, position and casing, or correct a single typo in one clip.

The preview re-renders after the call, so the next read shows the corrected cut.

[See the operations](https://clip.opus.pro/dashboard)

[See the operations](#)

## Ship clean audio by default

Filler-word and pause removal run as operations on an existing clip, or as enableRemoveFillerWords at submit time.

Either way the um, uh and dead air come out before anyone watches.

[Read the reference](https://clip.opus.pro/dashboard)

[Read the reference](#)

## Ship clean audio by default

Filler-word and pause removal run as operations on an existing clip, or as enableRemoveFillerWords at submit time.

Either way the um, uh and dead air come out before anyone watches.

[Read the reference](https://clip.opus.pro/dashboard)

[Read the reference](#)

## Get real files, not previews

Listing and describing clips returns preview URLs. HD downloads come from the export call, one clip at a time or a whole collection at once.

Build that into your pipeline and nothing ships at preview quality by accident.

[Start exporting](https://clip.opus.pro/dashboard)

[Start exporting](#)

## Get real files, not previews

Listing and describing clips returns preview URLs. HD downloads come from the export call, one clip at a time or a whole collection at once.

Build that into your pipeline and nothing ships at preview quality by accident.

[Start exporting](https://clip.opus.pro/dashboard)

[Start exporting](#)

## From key to edited clip

### Create a key

API access is on Pro (Beta), Max and Business plans. Generate the key in the OpusClip dashboard under API Access, and keep it in the environment that launches your code.

### Create a project

POST a source URL to /api/clip-projects, or request an upload link for a local file. Inputs run up to 10 hours and 30GB.

### Apply operations

Edit the clip with named operations in a single call: trims, splits, reorders, caption changes, filler-word removal, dubbing.

### Export

Pull the HD download for the clip or the whole collection, and send a signed webhook to your own service when the render completes.

## Heading

# Heading 1

## Heading 2

### Heading 3

#### Heading 4

##### Heading 5

###### Heading 6

Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur.

Block quote

Ordered list

1. Item 1
2. Item 2
3. Item 3

Unordered list

- Item A
- Item B
- Item C

**Bold text**

*Emphasis*

<sup>Superscript</sup>

<sub>Subscript</sub>

## What the API covers

### Trim, split, drop, reorder

Section-level operations on an existing clip, applied in one request.

### Caption control

Toggle captions and emoji, highlight keywords, and set colour, position and casing.

### Filler words and pauses

Remove them as an edit operation or set the flag when the project is created.

### Clip dubbing

Dub a clip as a named operation. Note that dubbing runs on the MCP server surface, not the Claude connector.

### Censor jobs

POST /api/censor-jobs bleeps or masks flagged content, with a status endpoint to poll.

### Transcripts

GET /api/transcripts returns paragraphs, speakers and per-word timings for the source video.

### Brand templates

Fetch available templates and apply one per project so edits inherit your styling.

### Predictable limits

30 requests a minute per key on core project and clip endpoints, with 429 when you exceed it.

## What people are saying

"I love that it's AI can find the best moments from a podcast or livestream instead of having to ask my client for specific timestamps, and will even add subtitles, effects and media to the videos as well."

### Anonymous

"Simple and easy. Just upload the video and everything else is taken care of. Generally it picks great snapshots to use a YouTube shorts."

### Erik-Jon M.

"OpusClip is AMAZING! I love how quickly it deconstructs longer videos into short clips with ratings and descriptions. It has made me more thoughtful in my speeches."

### Steve W.

## Frequently Asked Questions

### Does this API edit an existing video or only make new clips?

Both. Creating a project cuts a long video into clips, and the edit call then applies named operations to a clip you already have, including trims, splits, reorders, caption fixes and filler-word removal.

### How many requests can I make per minute?

30 per minute per API key on the core project and clip endpoints. Going over returns 429, and the social posting endpoints have their own per-endpoint limits, mostly 1 to 10 requests per second.

### How do I get a downloadable file?

Call the export endpoint. Listing and describing clips returns preview URLs only; the HD download URL comes from exporting a single clip or an entire collection.

### Which plans include API access?

Pro (Beta), Max and Business. Pro Beta and Max include 15 hours or 900 credits of API usage per workspace per calendar month, and Business limits follow the contract.

## Frequently Asked Questions

### What languages does your video transcription tool support?

Our AI free video transcription technology supports over 20 languages, including English, Spanish, French, German, Japanese, and more. The system automatically detects the spoken language or allows you to specify it before processing. This makes transcribing multilingual video content simple and accurate.

### Can I edit my video transcription after it's generated?

Yes, our platform includes a built-in editor for your free video transcription. You can correct any inaccuracies, format text, and add speaker labels directly in the interface. The editor synchronizes with timestamps, making it easy to perfect your transcript before exporting it for use.

### What makes AI video transcription faster than manual methods?

AI video transcription processes speech in minutes rather than hours of manual typing. Our advanced algorithms recognize speech patterns across different accents and languages, providing time-stamped text with speaker identification. This technology lets you transcribe a 60-minute video in just a few minutes.

### How much does it cost to transcribe a video?

Video transcription costs vary based on video length and features needed. Our platform offers a free trial transcription so you can experience the quality before committing. After that, we provide affordable pricing plans for regular video transcription needs with multi-language support and speaker detection.

### How can I transcribe a video?

Upload your video file or paste a link to our AI free video transcription tool. The system automatically converts speech to text with over 95% accuracy. Once complete, you can download your transcript in various formats like TXT, SRT, or VTT for captions, blog posts, or other content.

### How accurate are AI YouTube video transcription tools?

AI YouTube video transcript extractors typically achieve 95%+ accuracy for clear audio in English, with strong performance in over 50 other languages. For best results, videos with clear audio and minimal background noise produce the most accurate transcripts, though you can edit any errors afterward.

### What formats can I download YouTube video transcripts in?

YouTube video transcript generators typically allow you to download transcripts in multiple formats including TXT (plain text), SRT (SubRip Subtitle), and VTT (Web Video Text Tracks) files. These different formats support various use cases from content creation to adding captions to videos.

### Can I transcribe someone else's YouTube video?

Yes, you can transcribe anyone's public YouTube video using a YouTube video transcript generator. Simply copy the video URL, paste it into the transcription tool, and the AI will extract the spoken content into text format. This is useful for research, content repurposing, or accessibility purposes.

### How to get a YouTube video transcript from any video?

To get a YouTube video transcript, copy the video URL and paste it into a transcript generator tool. Click "Generate Transcript" and the AI will process the audio, delivering a complete text version in under a minute. You can then edit, format, and download the transcript in your preferred format.

### Does YouTube have auto transcription?

Yes, YouTube offers auto transcription for many videos, but these transcripts often contain errors. For more accurate results, use a dedicated YouTube video transcript generator that employs advanced AI to deliver 95%+ accuracy, proper formatting, and additional features like timestamp integration and multiple export formats.

### Can I get a transcript of a YouTube video?

Yes, you can get a transcript of any YouTube video by using an AI-powered YouTube video transcript extractor. Simply paste the video URL into the tool, and it will automatically convert the spoken content into text format with high accuracy, allowing you to download the transcript in seconds.