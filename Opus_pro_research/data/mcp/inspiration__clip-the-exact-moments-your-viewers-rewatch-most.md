---
url: https://www.opus.pro/mcp/inspiration/clip-the-exact-moments-your-viewers-rewatch-most
title: "Clip the exact moments your viewers rewatch most"
description: "Your agent reads YouTube's most-replayed heatmap and clips exactly the spikes it shows. Your audience's rewatch behavior picks the clips, not just an AI score."
category: mcp
word_count: 549
---

[Inspiration gallery](/mcp/inspiration)

# Clip the exact moments your viewers rewatch most

Your agent reads the most-replayed heatmap on your YouTube video and clips exactly the moments viewers keep rewatching. The audience picks the clips, and the AI score just breaks ties.

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

YouTube already tells you which moments of your video people can't stop rewatching: the most-replayed heatmap on every watch page. Most creators glance at it and move on. This workflow treats it as an instruction: the agent reads the heatmap's spikes, maps them to timestamps, and clips exactly those windows. Instead of predicting what might hook viewers, you're clipping what already did.

## What you need

- An OpusClip account (MCP tool calls require a Pro plan)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**api.opus.pro/api/mcp**)
- A YouTube video with enough views for the most-replayed heatmap to appear (typically tens of thousands)

## How it works

1. **Give the agent a video URL.** Any of your videos where the heatmap is showing.
2. **It reads the replay spikes.** The heatmap's peaks become a ranked list of timestamps, ordered by rewatch intensity.
3. **Each spike becomes a clip.** The agent submits the video and cuts clips centered on those exact windows, with captions and vertical reframing.
4. **The AI score breaks ties, not the news.** When spikes overlap or a window can be cut multiple ways, the virality score picks the stronger edit, but the audience signal decides what gets clipped at all.
5. **You get clips ranked by proven attention.** Each one labeled with its spike rank, ready to post or schedule.

## Try this prompt

Look at the most-replayed heatmap on this video of mine: \[YouTube URL\]. Find the top 5 replay spikes, clip a 30–60 second window around each one with captions and vertical reframe, and rank the results by rewatch intensity. Use the virality score only to choose between overlapping cuts.

## Tips

- Merge spikes that sit within \~20 seconds of each other; they're usually one moment, not two.
- Compare the heatmap's picks against the AI's own top picks; where they agree, you've found your strongest clip.
- Run it on your back catalog's most-viewed videos first; older hits have the richest heatmaps.