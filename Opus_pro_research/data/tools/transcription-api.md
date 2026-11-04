---
url: https://www.opus.pro/tools/transcription-api
title: "Transcription API with Word Timings | OpusClip"
description: "GET /api/transcripts returns paragraphs with start and end times in seconds, an optional speaker and per-word timings, in 20+ languages. ASR internals stripped."
category: tools
word_count: 1633
---

# Transcription API

GET /api/transcripts?q=findByProjectId returns the source video transcript as paragraphs: text, start and end times in seconds, an optional speaker, and timings for every word. ASR internals such as seek, tokens and log probabilities are stripped out.

It reads from a clip project, so the flow is create the project, then read the transcript. We do not publish a transcription-only endpoint.

## What you get back, precisely

### Timings at the word

Each paragraph carries start and end times in seconds plus a per-word timing object, which is what caption alignment and phrase-level cutting need.

### Speaker when we have it

An optional speaker field comes through on the paragraph, so multi-voice recordings do not arrive as one undifferentiated wall of text.

### A clean payload

Model internals are removed before the response leaves us. You parse paragraphs and words, not decoder state.

## Transcribe long recordings

Inputs run up to 10 hours and 30GB, imported from YouTube, Vimeo, Zoom, Riverside, Loom, StreamYard, Apple Podcast, Drive, Dropbox and more, or any public S3 MP4 link.

Pass sourceLang if the audio is not English; we support 20+ languages and auto-detect by default.

[See supported languages](https://clip.opus.pro/dashboard)

[See supported languages](#)

## Transcribe long recordings

Inputs run up to 10 hours and 30GB, imported from YouTube, Vimeo, Zoom, Riverside, Loom, StreamYard, Apple Podcast, Drive, Dropbox and more, or any public S3 MP4 link.

Pass sourceLang if the audio is not English; we support 20+ languages and auto-detect by default.

[See supported languages](https://clip.opus.pro/dashboard)

[See supported languages](#)

## Get the file formats editors expect

In the app, Pro plan users can download the transcript of an original video as .txt and the subtitles as .srt, from the result page or My projects.

The .srt is the standard subtitle format YouTube and other platforms accept.

[Download a transcript](https://clip.opus.pro/dashboard)

[Download a transcript](#)

## Get the file formats editors expect

In the app, Pro plan users can download the transcript of an original video as .txt and the subtitles as .srt, from the result page or My projects.

The .srt is the standard subtitle format YouTube and other platforms accept.

[Download a transcript](https://clip.opus.pro/dashboard)

[Download a transcript](#)

## Read it from an agent too

opusclip\_get\_transcript fetches a project's source-video transcript from Claude, Cursor, Codex or any connected MCP host.

It is a read tool, so it prompts for nothing and spends no credits.

[Connect an agent](https://clip.opus.pro/dashboard)

[Connect an agent](#)

## Read it from an agent too

opusclip\_get\_transcript fetches a project's source-video transcript from Claude, Cursor, Codex or any connected MCP host.

It is a read tool, so it prompts for nothing and spends no credits.

[Connect an agent](https://clip.opus.pro/dashboard)

[Connect an agent](#)

## From recording to transcript

### Create a key

API access is on Pro (Beta), Max and Business plans. Generate it in the OpusClip dashboard under API Access and set it in your environment.

### Submit the recording

POST the source URL to /api/clip-projects. Pass skipCurate in curation preferences if you want the recording processed without being cut into clips.

### Wait for completion

Attach a webhook conclusion action so we tell you when the project is ready, instead of polling on a timer.

### Read the transcript

GET /api/transcripts?q=findByProjectId&projectId=... and parse the paragraphs, speakers and per-word timings.

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

## Transcription features worth knowing

### Paragraph structure

Text with start and end times in seconds, ready to index or search.

### Per-word timings

A timing object inside each paragraph, for caption alignment and precise cuts.

### Optional speaker field

Carried on the paragraph when speaker information is available.

### 20+ languages

Auto-detected by default, or set explicitly with sourceLang in import preferences.

### TXT and SRT downloads

Pro plan users can export the original video's transcript as .txt and subtitles as .srt from the app.

### Bring your own SRT

Upload an SRT file during submission and we use it to generate captions instead of the transcript we would create.

### Agent access

get\_transcript is one of the read-only tools on our MCP server and Claude connector.

### Podcast sources

Apple Podcast is one of the supported import platforms, alongside Zoom, Riverside and StreamYard.

## What people are saying

"Opus Clip saves me hours! Its AI finds the best parts of any long form video, then whips up catchy clips with smart captions and b-roll. Boom, instant social media content that actually engages!!"

### Martin R.

""

### Anonymous

"I love the ease of use for the platform, it lets me being the editor for streams use it and also my less tech savvy streamer partner use it as well without confusion"

### Ron M.

## Frequently Asked Questions

### Is there a transcription-only endpoint?

Not one we publish. Transcripts are read per project: create a clip project for the recording, then call GET /api/transcripts with that projectId. Use skipCurate if you want the audio processed without clipping.

### What does the response contain?

Paragraphs with text, start and end times in seconds, an optional speaker, and per-word timings. Recogniser internals such as seek, tokens and log probabilities are stripped before the response is returned.

### Can I get an SRT file?

Yes, from the app: Pro plan users can download subtitles of the original video as .srt and the transcript as .txt from the result page or My projects. The .srt format works with YouTube and other platforms.

### Do I always get the whole recording back?

Not on a range-limited project. For API callers, paragraphs are filtered to the billed curation range, which is the portion that was paid to clip; full-source submissions return the full transcript.

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