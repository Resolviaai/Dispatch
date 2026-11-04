---
url: https://www.opus.pro/mcp/inspiration/turn-a-live-shopping-replay-into-one-clip-per-product
title: "Turn a live shopping replay into one clip per product"
description: "Your agent cuts a live selling stream into one clip per product, puts the product name on screen, and files each clip into that product's collection."
category: mcp
word_count: 768
---

[Inspiration gallery](/mcp/inspiration)

# Turn a live shopping replay into one clip per product

Your agent cuts a live shopping replay into one short clip per product, puts the product name on screen, and files every clip into that product's collection, ready for ads and product pages.

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

A two hour live selling show is twenty product demos in a row, and almost none of them get watched again once the stream ends. This workflow turns the replay into inventory. Your agent asks OpusClip for one clip per product, works out which product each clip covers, labels it on screen, and files it into a collection for that product. After a few shows, the serum collection holds every good demo of the serum you have ever done.

## What you need

- An OpusClip account on a Pro, Max, or Business plan (calling MCP tools needs one)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**mcp.opus.pro/mcp**)
- A replay link from YouTube or Facebook, or the video file on your computer
- Your product list, written the way you want the names to appear on screen

## How it works

1. **The agent sizes the job first.** Processing costs roughly one credit per minute of source video, so it checks your remaining credits before it submits a two hour show.
2. **It skips the warm-up.** If the show opens with ten minutes of "we will start in a few", the agent sets a start time so clips are only drawn from the show itself.
3. **It steers curation toward products.** A custom prompt asks for one clip per product, starting when the host introduces it and ending after the offer, in portrait, at 15 to 60 seconds.
4. **It identifies each product from the clip's own transcript.** Every clip carries its own transcript and keywords, and the agent matches them against your product list. It flags products that got two clips and products that got none instead of guessing.
5. **It puts the product name on screen.** A title card at the top of the frame for the first few seconds. It renders as a white rounded box with bold black text.
6. **It files and exports.** Each clip goes into its product's collection, and the agent exports a collection as HD files whenever your ad account or a product page needs it.

## Try this prompt

Check my OpusClip credits, then submit this replay: \[link\]. Skip the first 8 minutes. Ask for one clip per product, starting when I introduce it and ending after the offer, portrait, 15 to 60 seconds. Match each clip to this product list, written exactly as it should appear on screen: Serum, Night Cream, SPF 50. Add the product name as a title card at the top for the first 3 seconds, put each clip into a collection named after its product (create any that do not exist yet), and tell me which products got no clip.

## Tips

- Cut show-only prices before a clip goes on a product page. "Today only, $19" ages fast, and the agent can delete that phrase from the video and the captions in the same edit that adds the title card.
- Say the product name on air. The agent matches clips by what was said, so a show full of "this one" gives it nothing to go on.
- TikTok links are not on the supported source list. If that is where you stream, save the replay file and have the agent upload it from your computer.