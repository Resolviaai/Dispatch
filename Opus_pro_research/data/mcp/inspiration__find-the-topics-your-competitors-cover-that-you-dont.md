---
url: https://www.opus.pro/mcp/inspiration/find-the-topics-your-competitors-cover-that-you-dont
title: "Find the topics your competitors cover that you don't"
description: "Your agent transcribes competitor videos, compares their topics against your own library, and delivers a content-gap list straight into your content calendar."
category: mcp
word_count: 600
---

[Inspiration gallery](/mcp/inspiration)

# Find the topics your competitors cover that you don't

Your agent studies what competitor channels are talking about, compares it against everything in your own library, and hands you a list of proven topics you haven't covered yet, delivered straight into your content calendar.

## Run it with your agent

1. Open Customize in your client and select “Connectors”
2. Find OpusClip and click "Add"
3. Sign in with your OpusClip account in the OAuth window

1. Run the command below in your terminal `claude mcp add --transport http opusclip https://mcp.opus.pro/mcp`
2. Start Claude Code and run `/mcp`
3. Approve the OAuth sign-in with your OpusClip account

`claude mcp add --transport http opusclip https://mcp.opus.pro/mcp`
1. Open the ChatGPT desktop app and select "Plugins" in the sidebar
2. Search for OpusClip, then click the + icon
3. Click "Connect" and sign in with your OpusClip account

1. Open ChatGPT and select "Plugins" in the sidebar
2. Search for OpusClip, then click the + icon
3. Click "Connect" and sign in with your OpusClip account

1. In Cursor, open Customize in the left panel
2. Search for OpusClip and click Add
3. Sign in with your OpusClip account

1. Open Extensions ⇧⌘X → search @mcp opusclip
2. Pick the opus-pro entry
3. Install → OAuth sign-in

## What this workflow does

Competitor research is the homework everyone skips: watching hours of other channels' videos to figure out what's working for them that you haven't tried. This workflow does the watching for you. The agent pulls recent videos from the competitor channels you name, clips and transcribes them, extracts the topics, and cross-references them against your own transcripts to surface the gaps: the subjects that are earning views in your niche while missing from your library.

**This is a research workflow.** The competitor footage is used for topic analysis only: nothing gets published or reposted. The output is a topic list for videos you'll make yourself.

## What you need

- An OpusClip account (MCP tool calls require a Pro plan)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**api.opus.pro/api/mcp**)
- A list of 3–5 competitor YouTube channels
- Optional: a Notion or Airtable MCP so gap ideas land directly in your content calendar

## How it works

1. **Name the competitors.** Give the agent the channels you benchmark against.
2. **It processes their recent uploads.** Each video is submitted for clipping and transcription, turning hours of footage into searchable text.
3. **Topics get extracted and ranked.** The agent identifies what each competitor keeps returning to, weighted by the views those videos earn.
4. **Your library is the filter.** It compares that topic map against transcripts from your own projects and keeps only what's missing.
5. **Gaps land in your calendar.** Each gap arrives with evidence (which competitor covered it, how it performed, and a suggested angle) as entries in your content calendar tool.

## Try this prompt

Pull the last 10 videos from \[competitor channel 1\] and \[competitor channel 2\], transcribe them, and list the topics they cover ranked by view count. Then compare against the transcripts in my OpusClip projects and give me the 10 best topics I haven't covered, each with a suggested angle. Add them to my Notion content calendar as ideas.

## Tips

- Run it monthly: gaps close and new ones open as competitors publish.
- Ask the agent to weight gaps by view velocity, not raw views, so you catch rising topics early.
- Keep competitor footage strictly internal: the deliverable is the topic list, never the clips themselves.