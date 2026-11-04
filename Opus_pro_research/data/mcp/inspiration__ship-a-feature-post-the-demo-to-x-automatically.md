---
url: https://www.opus.pro/mcp/inspiration/ship-a-feature-post-the-demo-to-x-automatically
title: "Ship a feature, post the demo to X automatically"
description: "Record a screen demo of what you shipped, and your agent clips the it-works moments, captions them, writes the post copy, and publishes the reel to X."
category: mcp
word_count: 548
---

[Inspiration gallery](/mcp/inspiration)

# Ship a feature, post the demo to X automatically

Record a quick screen demo of the feature you just shipped, and your agent clips the it-works moments, captions them, writes the post copy, and publishes the reel to X: build-in-public content as a byproduct of building.

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

Every developer building in public faces the same tax: you shipped the thing, and now you owe the internet a post about it. This workflow reduces that tax to hitting record. Screen-record the feature working (no script, no editing), and the agent finds the payoff moments, cuts them into short captioned clips, drafts the post copy in your voice, and publishes to X.

## What you need

- An OpusClip account (MCP tool calls require a Pro plan)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**api.opus.pro/api/mcp**)
- A screen recording of the feature working (QuickTime, Loom export, or any local video file)
- Your X account connected in OpusClip

## How it works

1. **Record the demo raw.** Just capture the feature doing its job. The agent handles everything after.
2. **It submits the recording and finds the payoffs.** The moments where something visibly works (the click, the result, the before/after) get clipped.
3. **Clips get captioned and formatted.** Captions matter on X, where most video plays silently.
4. **Copy gets drafted from the transcript.** A short post explaining what shipped and why it matters, in plain builder voice rather than marketing speak.
5. **It posts (or queues) to X.** Approve the draft in chat and the clip goes out, same day as the ship.

## Try this prompt

Here's a screen recording of the feature I just shipped: \~/Desktop/feature-demo.mov. Clip the moments where it visibly works, caption them, and draft an X post announcing it: plain language, lead with what it does, under 200 characters. Show me the draft before posting.

## Tips

- Keep clips under 45 seconds: the hook is the result, not the setup.
- Ask for the demo clip plus a one-line hook as the post text; threads underperform single strong clips for ship announcements.
- Make it a ritual: record every ship, and the agent turns your changelog into a public track record.