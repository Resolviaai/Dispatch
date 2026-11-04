---
url: https://www.opus.pro/mcp/inspiration/cut-an-outdated-price-out-of-every-clip-youve-made
title: "Cut an outdated price out of every clip you've made"
description: "A price changed and your old clips still quote it. Your agent finds every clip that says it, corrects the caption or cuts the line, and re-exports the fix."
category: mcp
word_count: 687
---

[Inspiration gallery](/mcp/inspiration)

# Cut an outdated price out of every clip you've made

A price changed and your back catalogue still quotes the old one. Your agent finds every clip that says it, corrects the caption or cuts the line out of the video, and re-exports the clean file.

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

Every clip you post is a small permanent commitment: a price, a plan name, a discount, a "we just launched" that stopped being true months ago. When one of those changes, the usual move is to write the clip off and shoot something new. This workflow repairs it instead. Your agent searches the project transcript for the stale wording, then either corrects the caption text in place or cuts the spoken line out of the video and the captions together, and re-exports the corrected file.

## What you need

- An OpusClip account (MCP tool calls require a Pro plan)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**api.opus.pro/api/mcp**)
- The old wording and the new wording, exactly as it is said out loud
- The projects to search, or a date range to search across

## How it works

1. **The agent finds the mentions.** It pulls each project's transcript and reports every clip whose captions carry the outdated wording, with timestamps.
2. **You choose repair or removal.** A wrong word gets replaced in the captions without touching the video or its timing. A whole claim that no longer holds gets cut out of the video and the captions together.
3. **Word counts have to match on a replacement.** Each spoken word keeps its own timing, so "nineteen dollars" can become "thirty dollars", but rewriting a full sentence cannot fit. When it will not fit, the agent cuts the line instead.
4. **The clip re-renders once.** Every edit for a clip goes in a single call, so you wait for one render instead of five.
5. **Anything still queued gets pulled and requeued.** The stale version comes out of the schedule and the corrected one goes back in.
6. **The corrected file gets exported.** HD for a straight re-upload, or a Premiere project file when the clip needs a human pass first.

## Try this prompt

We raised the Starter plan from nineteen dollars to thirty. Search my last three projects for clips that mention the old price. For each one, replace "nineteen dollars" with "thirty dollars" in the captions where the timing allows, and where it does not, cut the whole sentence out of the video and the captions. Unschedule any affected post, re-export the corrected clips in HD, and give me a list of exactly what you changed.

## Tips

- Run this the day the price changes, not the day a customer screenshots the old one.
- Replacements are for wording, not for reshaping a sentence. If the new claim is longer, cutting the line is cleaner than forcing a fit.
- Keep the change list the agent gives you. It is your record of which already-published posts still need the corrected file swapped in.