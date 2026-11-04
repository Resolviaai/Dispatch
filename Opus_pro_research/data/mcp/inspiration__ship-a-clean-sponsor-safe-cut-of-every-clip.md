---
url: https://www.opus.pro/mcp/inspiration/ship-a-clean-sponsor-safe-cut-of-every-clip
title: "Ship a clean, sponsor-safe cut of every clip"
description: "Your agent runs the profanity censor across a project, cuts the words no filter knows to look for, and hands you one collection a brand partner will approve."
category: mcp
word_count: 632
---

[Inspiration gallery](/mcp/inspiration)

# Ship a clean, sponsor-safe cut of every clip

Your agent runs the profanity censor across a whole project, cuts the words no filter knows to look for, and hands you a separate collection of clips a brand partner will actually approve.

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

One unbleeped word is enough to get a clip rejected by a brand partner, throttled by a platform, or pulled from a paid campaign. This workflow gives you a second, clean version of everything without re-editing anything by hand. Your agent runs OpusClip's profanity censor across the project, cuts the extra words no profanity list could know about (a competitor's name, an unannounced feature, a guest's slip), and gathers the clean cuts into one collection ready to export and hand over.

## What you need

- An OpusClip account (MCP tool calls require a Pro plan)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**api.opus.pro/api/mcp**)
- Your own banned-word list, on top of the standard profanity filter
- A finished project whose clips have rendered

## How it works

1. **The censor runs clip by clip.** Matched words get starred out in the captions and beeped in the audio, so the clip still reads as finished instead of going silent.
2. **Your own list runs after it.** Standard filters catch swearing. They do not catch a competitor's name or a number you were not supposed to say, so those get cut from the video and the captions.
3. **The agent waits for the re-render.** Censoring queues a fresh render, so it polls until the clip is actually ready rather than handing you the old file.
4. **The clean cuts go into their own collection.** The originals stay exactly as they were, for the channels where they were never a problem.
5. **One export gets you the whole pack.** Exporting the collection returns every clean clip at once, ready to attach to the sponsorship thread.

## Try this prompt

Run the profanity censor with a beep on every clip in my latest project. Once each one has re-rendered, also cut any mention of our competitor's name and the phrase "still in beta" out of both the video and the captions. Put the cleaned clips in a new collection called Brand Safe, wait for the renders to finish, then export the collection in HD and give me the download links.

## Tips

- Beep, do not mute. A silent gap reads as a mistake and a beep reads as a choice.
- Keep the clean pack separate from the originals. Your own channel usually wants the uncut version.
- Add to the banned list after every recording session. The words that get a clip rejected are almost never the obvious ones.