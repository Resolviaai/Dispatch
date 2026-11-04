---
url: https://www.opus.pro/mcp/inspiration/make-every-clip-match-your-album-art
title: "Make every clip match your album art"
description: "Your agent puts the release palette on every clip in one pass: caption colour, accent, caps, emoji off, so a clip reads as yours before the track even starts."
category: mcp
word_count: 645
---

[Inspiration gallery](/mcp/inspiration)

# Make every clip match your album art

Your agent puts the release palette on every clip in one pass, caption colour, accent colour, caps and emoji included, so a clip reads as yours in the half second before anyone hears the track.

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

A release has a look: two or three colours off the cover, a type treatment, a feel. Clips cut from the video arrive in OpusClip's defaults instead, bright green keyword highlights and all, which is why so much music content on a feed looks interchangeable. This workflow puts the release palette across the whole batch in one pass, so a clip is recognisably yours before the first bar plays.

## What you need

- An OpusClip account (MCP tool calls require a Pro plan)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**api.opus.pro/api/mcp**)
- Two hex codes off the cover art: one for the caption text, one for the accent
- A finished project whose clips have rendered

## How it works

1. **You hand over the palette.** One hex code for the caption text, one for the keyword highlight. Pull both straight off the cover so the clips and the artwork agree.
2. **The agent styles every clip in the project.** Caption colour, accent colour, caption position, and all caps if that is the treatment. Several style changes count as one change, so each clip renders once.
3. **The stock green goes away.** OpusClip highlights keywords in bright green out of the box. Naming your own accent is the single biggest thing that stops a clip looking like everyone else's.
4. **Emoji come off if they are not you.** Auto emoji suit a podcast clip and rarely suit a record.
5. **The next release starts already styled.** Once the look is settled, the agent lists your brand templates and submits the next video against the right one, so nothing needs restyling after the fact.

## Try this prompt

Style every clip in my latest project to match the single's cover: caption text in #F2E8D5, keyword highlight in #C1440E, captions centred, all caps, and turn the auto emoji off. Make sure keyword highlighting is actually switched on, not just recoloured. Then list my brand templates so I can pick the one to submit the next video against.

## Tips

- Setting an accent colour does not switch highlighting on. The toggle is a separate change, and an agent that only sets the colour leaves you with recoloured nothing.
- Caption colour is a style setting, not a wording change. It is the one part of the look you cannot get to by rewriting the captions.
- Take the accent off the cover, not off the video. The cover is what sits next to the track on every platform people already use.