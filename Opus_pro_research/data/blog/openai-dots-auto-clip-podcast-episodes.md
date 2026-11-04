---
url: https://www.opus.pro/blog/openai-dots-auto-clip-podcast-episodes
title: "Auto-Clip Every Podcast Episode with OpenAI Dots - OpusClip Blog"
description: "Set up an OpenAI Dot with OpusClip to automatically clip every new podcast episode, caption it, and queue the best moments for approval."
category: blog
word_count: 1314
---

# How to Set Up a Dot That Clips Every New Podcast Episode Automatically

**You can automatically clip podcast episodes with AI by giving an OpenAI Dot a standing instruction and connecting it to OpusClip.** The Dot watches for new episodes, sends each one to OpusClip, picks the strongest captioned vertical clips, writes the posts, and asks you to approve them before they are scheduled. You set it up once, and every episode gets repurposed without you opening an editor.

This guide gives you the full setup, a copy-paste instruction, and the rules that keep it safe.

## Why an always-on AI agent for podcasts makes sense

Podcast clips automation is the textbook always-on task. It happens on a schedule, it follows the same steps every time, and it takes hours by hand. A one-hour episode usually contains a handful of moments strong enough to stand alone as short clips, but finding them means listening to the whole thing, then cutting, captioning, reframing to vertical, and writing a post for each platform.

OpenAI designed Dots to keep working in the background, monitor for changes, and bring finished work back for approval. OpusClip does the video work: it analyzes the full episode, ranks the best moments, and handles captions, framing, edits, copy, and scheduling. Together they turn a weekly chore into a two-minute review.

## What you need

- **A Dot:** included in ChatGPT Pro and Business Premium at launch.
- **OpusClip connected to ChatGPT:** follow our guide on [how to connect OpusClip to OpenAI Dots](https://www.opus.pro/blog/connect-opusclip-to-openai-dots). Clipping and scheduling require an OpusClip Pro, Max, or Business plan.
- **Where your episodes land:** a YouTube channel, a Google Drive or Dropbox folder, Zoom, or another source OpusClip accepts. Video podcasts give the best clips, since viewers expect a face on screen.
- **Social accounts linked in OpusClip:** for example YouTube Shorts, TikTok, Instagram Reels, and LinkedIn.

## The OpenAI Dots automation at a glance

1. **Watch:** the Dot checks your channel or folder for a new episode.
2. **Clip:** it submits the episode to OpusClip.
3. **Select:** it reads the ranked clips and transcripts and keeps the ones that match your brief.
4. **Polish:** it asks OpusClip to remove filler words, apply your caption style, and trim weak openings.
5. **Write:** it generates a title, description, and hashtags per platform.
6. **Approve:** it sends you the shortlist with preview links.
7. **Schedule:** after you approve, it spreads the clips across the week.

## Step 1: Write your clip brief

The brief is what makes the output feel like yours. Decide these once:

- **How many clips per episode:** five is a good default for a weekly show.
- **Length:** 30 to 60 seconds suits most short-form feeds.
- **What counts as a great moment:** a strong opinion, a surprising number, a story with a punchline, a practical tip.
- **What to skip:** sponsor reads, housekeeping, inside jokes that need context.
- **Caption style:** use an OpusClip brand template so every clip matches.
- **Posting rhythm:** for example one clip a day at 9am in your audience's time zone.

## Step 2: Give your Dot the standing instruction

This is how you turn a podcast into short clips every week without touching an editor. Paste this into your Dot and adjust the details in brackets:

Every day at 8am, check \[my YouTube channel URL\] for a new full podcast episode. When a new episode appears, send it to OpusClip and wait until the clips are ready. Keep the 5 strongest clips between 30 and 60 seconds. Prefer strong opinions, surprising numbers, and practical tips. Skip sponsor reads and intros. Remove filler words and pauses, and use my \[brand template name\] caption style. For each clip, generate a YouTube Shorts title, a TikTok caption, and a LinkedIn post. Send me the shortlist with preview links. Do not post or schedule anything until I approve. After I approve, schedule one clip per day at 9am Eastern across YouTube, TikTok, Instagram, and LinkedIn.

Because the Dot has its own computer and keeps running, it follows this instruction every day without you starting a new chat.

## Step 3: Lock down publishing with a custom rule

Dots let you add custom rules that allow, block, or require approval for specific actions. Add one that requires approval for posting and scheduling. Leave clipping, editing, and copywriting allowed so the Dot can finish all the prep work unattended. Your only job each week is to approve or reject a shortlist. If you later want to auto post podcast clips with no review, remove the rule for that account once the picks are consistently good.

## Step 4: Review the first two episodes closely

Treat the first two runs as calibration. Watch every clip and tell the Dot what to change in plain language, for example "cut the first sentence of clip 2" or "move captions higher so they clear the TikTok buttons." OpusClip applies those edits directly. Dots learn your preferences from feedback, so the shortlist should need fewer corrections each week.

## Step 5: Add an approval shortcut

Once you trust the picks, make approval fast. Ask the Dot to send the shortlist in Slack or Microsoft Teams, where you can reply "approve all" or "approve 1, 3, 4." You can also let it auto-schedule to a secondary account and keep approval on your main channel.

## Variations that work well

- **Guest-focused clips:** ask for two clips featuring the guest and tag them in the post copy so they reshare.
- **Weekly highlight reel:** collect the week's approved clips into an OpusClip collection and export the HD files for a newsletter or website.
- **Audio-first shows:** clips still work with captions and a clean layout, though video episodes usually perform better on vertical feeds.
- **Multiple shows:** give each show its own brief in the same instruction, with its own posting accounts.
- **Clean language:** ask OpusClip to bleep or mask flagged words before clips go to more conservative platforms such as LinkedIn.

## Limits to plan around

- **Monthly usage:** OpusClip Pro and Max API access includes 15 hours or 900 credits per workspace per month, with a 10-credit minimum per project. A weekly one-hour show fits comfortably.
- **Concurrency:** up to four projects process at once. A back catalog should be queued a few episodes at a time.
- **Dot availability:** OpenAI says Pro users in the European Economic Area, Switzerland, and the UK cannot use Dots yet. Business Premium works in all supported ChatGPT regions.

## Frequently asked questions

### How do I automatically clip podcast episodes with AI?

Connect OpusClip to an always-on agent such as an OpenAI Dot and give it a standing instruction: check for new episodes, send each one to OpusClip, keep the best clips, and ask for approval before scheduling. OpusClip handles moment detection, captions, vertical framing, edits, and posting.

### Can a Dot publish podcast clips without me?

It can, but we recommend a custom rule that requires approval for posting and scheduling, at least for your main accounts. The Dot still does all the prep work on its own.

### How many clips will OpusClip find in one episode?

OpusClip returns a ranked set of clips for every video. Your Dot keeps however many your brief asks for, which is usually three to seven per episode.

### Does this work for a back catalog of old episodes?

Yes. Ask the Dot to work through past episodes a few at a time, since up to four OpusClip projects process at once, and to spread the resulting clips across your posting calendar.

### What else can a Dot do for creators?

Podcasts are only the start. See our list of [10 video jobs to give your first Dot](https://www.opus.pro/blog/openai-dots-for-creators-video-jobs), or compare tools in our guide to the [best video editing bot for OpenAI Dots](https://www.opus.pro/blog/best-video-editing-bot-for-openai-dots).