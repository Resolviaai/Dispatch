---
url: https://www.opus.pro/mcp/inspiration/polish-every-loom-before-it-goes-to-a-customer
title: "Polish every Loom before it goes to a customer"
description: "Your agent cuts the ums, the dead air and the \"can you see my screen?\" out of a Loom, keeps it 16:9, and hands back a clean HD file to send."
category: mcp
word_count: 758
---

[Inspiration gallery](/mcp/inspiration)

# Polish every Loom before it goes to a customer

Your agent takes a Loom link, cuts the filler words, the long pauses and the "can you see my screen?" out of the video and the captions, and hands back a clean 16:9 file you can send to a customer.

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

A Loom recorded in one take is honest, and it is also full of "um", ten second silences while a page loads, and a minute of "can you see my screen?" at the top. That is fine for a teammate and rough for a customer or a prospect. This workflow cleans it up without a re-record. Your agent pulls the Loom in as one full-length video, keeps it landscape so nothing gets cropped, cuts the filler and the dead air, removes the setup lines you name, and gives you an HD file to send.

## What you need

- An OpusClip account on a Pro, Max, or Business plan (calling MCP tools needs one)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**mcp.opus.pro/mcp**)
- A Loom share link that anyone with the link can view
- The setup lines you always say, if you want those gone too

## How it works

1. **The agent checks your credits.** Processing costs roughly one credit per minute of source video, so it confirms there is room before it starts.
2. **It imports the Loom as one video, not clips.** No-clip mode keeps the whole recording as a single output, and landscape output keeps your screen at 16:9 instead of reframing it for a phone.
3. **It previews the edit first.** A dry run reports what each cut would do and saves nothing, so you know how much is coming out before anything renders.
4. **It cuts everything in one pass.** Filler words, pauses above the threshold you set, and every occurrence of the phrases you named. Phrase cuts come out of the video and the captions together, so the two never drift apart.
5. **It re-renders once.** All of the cuts go in a single call, which means a single render, and the edit can be undone for 72 hours if you want something back.
6. **You get a review link and an HD file.** The agent shares the editor link so you can watch the result straight away, waits for the render to finish, then exports the HD MP4.

## Try this prompt

Here is my Loom: \[link\]. Check my OpusClip credits, then import it as one full-length landscape video without cutting it into clips. Remove filler words, cut pauses longer than 1.5 seconds, and delete every "can you see my screen" and "let me share my screen". Show me a dry run first. When I approve, apply it, give me the editor link, and export the HD file once the render is done.

## Tips

- Set the pause threshold higher than you would for a talking-head video. On a screen recording, a silent stretch is often you clicking through something the customer needs to see. 1.5 seconds is a safer start than the 0.5 second default.
- Turn captions off for a product walkthrough if they would sit on top of your UI. It is one more change in the same call, so it costs no extra render.
- The output is a new file. Your original Loom link stays exactly as it was, so send the cleaned file or upload it as a new Loom.