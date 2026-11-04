---
url: https://www.opus.pro/mcp
title: "Video Editing & Clipping MCP Server for Claude, Cursor & ChatGPT | OpusClip"
description: "Connect OpusClip's video MCP server to Claude, Cursor, or Codex. 29 tools to clip, caption, edit, and schedule video straight from your agent. OAuth setup, no code needed"
category: mcp
word_count: 1239
---

MODEL CONTEXT PROTOCOL

# Video MCP for any agent

The OpusClip MCP server lets any AI agent — Claude, Cursor, ChatGPT, and more — clip, caption, reframe, edit, and repurpose video.

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

## Clip and edit from any agent

- ### The video MCP server Turns long videos into short, ready‑to‑post clips right inside your AI assistant.
- ### For developers and agent users Your agent calls OpusClip like any other tool. No custom integration, no extra app.
- ### 2-minute setup Connect once and sign in — that's it. Nothing to download or configure.
- ### Free to start Try it for free, upgrade when you need more. You only pay for what you make.
- ### Works in Claude, Cursor, ChatGPT, VS Code The same setup runs in every AI tool you already use — no extra steps.

## Six tools your agent can call

- ### Clip Find highlights and cut short clips from a long video
- ### Find viral moments Score a video and return the highest-engagement moments
- ### Caption Burn in animated, styled subtitles
- ### Reframe Convert to 9:16 / 1:1 / 16:9 with subject tracking
- ### Repurpose Re-edit a clip for TikTok / Reels / Shorts / LinkedIn
- ### Schedule Queue finished clips to connected social accounts

## Try it with one prompt

Ask in plain language. Works on podcasts, webinars, sales calls, and livestreams.

- ### Clip Turn this YouTube video into 10 viral clips.
- ### Reframe and caption Reframe my webinar to 9:16 and caption it.
- ### Find viral moments Find the best moment in this podcast and translate it to Spanish.

## What people are building

Try these copy and paste templates for the most popular workflows with our MCP.

[View all templates](/mcp/inspiration)

## Best video MCP servers

| MCP server | Clipping intelligence | OAuth (no keys) | Free tier | Clients | 
|---|---|---|---|---|
| OpusClip | Yes (virality scoring) | Yes | Yes | Claude, Cursor, Cline, ChatGPT | 
| HeyGen | No (generation / avatars) | Yes | Yes | Claude, Cursor, ChatGPT | 
| VEED | No (generation) | Partial | Trial | MCP clients | 
| Submagic | Yes | API key | Trial | Claude, Cursor | 
| FFmpeg (DIY) | No — manual | N/A | Self-host | N/A | 

## Trusted at scale

The most powerful video editing MCP that works on any video. Built for speed, accuracy, and creative flexibility.

- 1B+ clips generated
- \~90s for 10 clips
- 10x faster than real-time

## Connect your AI agent to video clipping and editing

The MCP server for turning long video into viral-ready shorts

## Got questions?

### What is the OpusClip MCP server?

It’s a Model Context Protocol server that exposes OpusClip’s video tools — clip, caption, reframe, edit, repurpose, schedule\_post to AI agents. Any MCP-compatible client (Claude, Cursor, ChatGPT) can call it to turn long video into short, captioned, vertical clips in one conversation.

### What is a video MCP (and why use one)?

A video MCP server lets an AI agent create or edit video by calling a standard protocol instead of a custom integration. You install it once, and the agent gains video tools it can invoke on command, no separate app, no leaving the chat or editor.

### How do I install the OpusClip MCP server?

Run one command — for Claude Code: ‘claude mcp add --transport http opusclip https://mcp.opus.pro/mcp’ or add the URL in your client’s MCP settings. Sign in with OAuth and you’re done in about two minutes. The same server works across Claude, Cursor, and ChatGPT.

### Which clients and agents are supported?

Claude (Desktop, web, Code), Cursor, and ChatGPT via the Apps SDK, plus any MCP-compatible client. The same tools are available everywhere; only the install step differs per client.

### Do I need an API key?

No, the MCP connection uses OAuth, so you sign in once and your account and credits carry through. API keys (opk\_) are only needed if you call the OpusClip REST API directly outside of an MCP client.

### What tools does the OpusClip MCP expose?

29, all prefixed `opusclip_`. The ones agents call most: submit\_project (clip a video URL, with captions, reframe, filler-word removal, and brand template as parameters), list\_clips (ranked clips with virality scores), get\_transcript (per-word timing), get\_editing\_script and apply\_editing\_script (frame-level edits: trims, caption fixes, retiming), create\_censor\_job (profanity censor), export\_clip (HD, 4K, or an Adobe Premiere XML project file), create\_social\_copy\_job (titles, descriptions, hashtags), and schedule\_publish (queue to connected social accounts). The rest cover projects, collections, brand templates, social accounts, and usage. Agents chain them in one request.

### Is the OpusClip MCP server free?

Yes, there’s a free trial so you can clip videos through the MCP without paying, then a Pro plan for higher volume with metered, per-render-minute pricing and no per-seat fees. You connect with your existing OpusClip account.

### What’s the difference between a video clipping/video editing MCP and generation MCP?

OpusClip is a video clipping and editing MCP that turns existing long video into short, ready-to-post clips with viral-moment detection. A generation MCP (e.g. Agent Opus) creates new video from prompts.

### How is OpusClip’s MCP different from HeyGen’s or VEED’s?

HeyGen and VEED MCPs focus on generating video (avatars, text-to-video). OpusClip’s MCP repurposes video you already have into short clips with virality scoring, captions, and reframing, the ‘turn this long video into shorts’ job, which those generation MCPs don’t do. It also provides video editing capabilities.

### What are the rate limits and pricing?

The free trial covers prototyping, and Pro gives you more credits for production volume. The MCP uses the same credit balance as your OpusClip account. Different jobs use different amounts of credits, and your agent can check your remaining balance before it starts one. Rate limits scale with your plan, and the server returns standard 429s with retry headers so agents can back off gracefully.

### Is my video data private?

Yes. Work is tied to your authenticated OpusClip account, renders are sandboxed, and nothing is published until you approve it. Review OpusClip’s data-handling and retention terms for specifics.

### How do I use the OpusClip MCP in my own agent?

Point your MCP-compatible framework (OpenAI Agents SDK, LangChain, CrewAI, or any client) at https://mcp.opus.pro/mcp and authenticate. The server advertises its tools automatically, so your agent can discover and call clip, caption, reframe, and more.