---
url: https://www.opus.pro/mcp/inspiration/bulk-submit-videos-for-clipping-straight-from-your-computer
title: "Bulk submit videos for clipping straight from your computer"
description: "Point your agent at a folder on your computer and it submits every video inside for clipping in OpusClip. No browser uploads, one command for the whole batch."
category: mcp
word_count: 559
---

[Inspiration gallery](/mcp/inspiration)

# Bulk submit videos for clipping straight from your computer

Point your agent at a folder on your computer and it submits every video inside for clipping in OpusClip. No browser uploads, no drag-and-drop, one command for the whole batch.

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

Most creators have a hard drive full of long-form videos that never made it into OpusClip: raw recordings, old episodes, exported livestreams. This workflow turns that folder into a clipping queue. You point your agent at a directory on your computer, and it submits every video inside for clipping in OpusClip: no browser uploads, no drag-and-drop, one command for the entire batch.

## What you need

- An OpusClip account (MCP tool calls require a Pro plan)
- Claude, Cursor, or any MCP-enabled agent with the OpusClip MCP connected (**api.opus.pro/api/mcp**)
- A local folder with the long-form videos you want clipped

## How it works

1. **Ask once.** Tell your agent which folder to process. That's the whole job description.
2. **The agent scans the folder.** It lists every video file it finds (you can filter by format, size, or date) and confirms the batch before anything is submitted.
3. **Each file is uploaded securely.** The agent creates an upload link for every video and pushes the files straight from your filesystem to OpusClip.
4. **Every video becomes its own project.** Each file is submitted for clipping with a project name taken from the filename, so the dashboard stays organized.
5. **You get a report, not homework.** While OpusClip works through the queue in the background, the agent hands back a list of the submitted projects so you can review clips as they finish.

## Try this prompt

Look in \~/Videos/podcast-episodes and submit every video file for clipping in OpusClip. Name each project after its filename, confirm the list with me before uploading, and give me links to all the projects when you're done.

## Tips

- Ask the agent to skip files under a certain length. Short videos usually aren't worth a clipping run.
- Have it apply a naming convention (like *2026-07 Episode 41*) so batches stay sortable in the dashboard.
- Once clips are ready, the same agent can pull the top-scoring clips from each project and schedule them. The batch submit is step one of a full pipeline.