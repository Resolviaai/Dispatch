---
url: https://www.opus.pro/mcp/inspiration/label-every-exercise-in-your-workout-clips
title: "Label every exercise in your workout clips"
description: "Your agent reads each clip's own transcript, works out which exercise it covers, and burns the name on as a title card so the set scrolls like a series."
category: mcp
word_count: 679
---

[Inspiration gallery](/mcp/inspiration)

# Label every exercise in your workout clips

Your agent reads each clip's own transcript, works out which exercise it covers, and puts the name on screen as a title card, so a scroller knows what they are watching before they have to guess.

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

A workout video cut into clips gives you fifteen good clips and no way to tell them apart in a feed. Every one is a person in a gym. This workflow labels them. Your agent reads each clip's own transcript, works out which movement it covers, and puts that name on the first few seconds as a title card, so a clip about Romanian deadlifts announces itself instead of blending into the other fourteen.

## What you need

- An OpusClip account (MCP tool calls require a Pro plan)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**api.opus.pro/api/mcp**)
- A finished project whose clips have rendered
- How you want the movements written, if it is not the words you said out loud

## How it works

1. **The agent reads each clip individually.** Every clip carries its own transcript and keywords, which is what tells the agent this one is about split squats and that one is about the warmup.
2. **It names the movement.** Whatever you called it mid-set becomes the label, normalised to the way you want it written rather than the way you said it while out of breath.
3. **The label goes on as a title card.** A text overlay at the very start, held about three seconds, sitting at the top of the frame so it never fights the captions along the bottom.
4. **Each clip re-renders once.** Every edit for a clip goes in one call, so it renders a single time no matter how many changes you asked for.
5. **The agent waits for the render.** It polls each clip until the new version is ready, rather than handing you back the old file.
6. **The labelled set lands in a collection.** One folder, one export, ready to schedule as a series instead of fifteen loose files.

## Try this prompt

Go through every clip in my latest project, work out which exercise each one covers from its transcript, and add a text overlay with that name at the top of the frame for the first three seconds. Title case, four words maximum. Wait for each clip to finish re-rendering, put them all in a collection called Form Series, and tell me which ones you could not identify.

## Tips

- The overlay renders as the same card the auto-hook uses: bold black text on a white rounded box. Write the label short rather than clever.
- Keep labels at the top of the frame. The bottom band belongs to the captions, and stacking the two costs you both.
- Ask for a list of the clips it could not identify instead of letting it guess. An unlabelled clip is recoverable, a mislabelled exercise is not.