---
url: https://www.opus.pro/mcp/inspiration/turn-your-best-clip-into-a-three-way-hook-test
title: "Turn your best clip into a three-way hook test"
description: "Your agent copies your best clip twice, rebuilds the opening of each copy a different way, and queues all three over the same week so the numbers pick the hook."
category: mcp
word_count: 683
---

[Inspiration gallery](/mcp/inspiration)

# Turn your best clip into a three-way hook test

Your agent copies your best clip twice, rebuilds the opening of each copy a different way, and queues all three over the same week, so the retention numbers tell you which hook to cut for next time.

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

The hook is the only part of a short-form video most people ever see, and there is no way to know which one lands until it is live. This workflow makes hook testing cheap enough to run every week. Your agent takes a clip that already scored well, duplicates it twice, and rebuilds the first few seconds of each copy a different way: one opens on the payoff line, one opens with a text card posing the question, one stays exactly as it was. All three go out over the same week, and the winner tells you how to cut the next fifty clips.

## What you need

- An OpusClip account (MCP tool calls require a Pro plan)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**api.opus.pro/api/mcp**)
- At least one social account connected in OpusClip
- A finished project with scored clips to pull from

## How it works

1. **The agent picks the control.** It lists the project's clips and takes the highest scorer as variant A: the version you would have posted anyway.
2. **It makes two independent copies.** A duplicate is its own clip, so a variant that flops never touches the original.
3. **It rebuilds each opening.** Variant B gets its sections reordered so the payoff runs first. Variant C keeps the original order but gets a text overlay in the first two seconds asking the question the clip answers.
4. **Each variant gets its own caption.** Copy is generated per clip and per platform, so the caption matches its hook instead of fighting it.
5. **All three get queued days apart.** Same account, same time of day, spaced so they never compete for the same audience. The agent hands back an approval link you confirm in OpusClip.
6. **You read the result, not the vibes.** Retention on the first three seconds is the entire test.

## Try this prompt

Take the top-scoring clip in my latest project and build a three-way hook test. Keep the original as variant A. For variant B, duplicate it and reorder the sections so the payoff line comes first. For variant C, duplicate it and add a text overlay in the first two seconds that asks the question the clip answers. Write a caption for each, then queue all three to my TikTok account at 6pm ET on Monday, Wednesday, and Friday.

## Tips

- Change one thing per variant. Two changes at once and you learn nothing from the result.
- Space the variants at least 48 hours apart, so the second is not fighting the first for reach.
- Keep a running note of which hook shape wins. After a month it stops being a test and becomes your default cut.