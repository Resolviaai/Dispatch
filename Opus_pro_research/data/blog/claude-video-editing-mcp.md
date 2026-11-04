---
url: https://www.opus.pro/blog/claude-video-editing-mcp
title: "How to Use Claude to Edit Your Videos (MCP Setup) - OpusClip Blog"
description: "Connect Claude to a real video editor with the OpusClip MCP server. One command to install, then clip, caption, reframe, and schedule from chat."
category: blog
word_count: 1847
---

# How to Use Claude to Edit Your Videos: The Complete MCP Setup

**Claude can now drive a real video editor.** Not a code renderer that builds videos out of React components, and not a text-to-video model that invents footage. It is an actual editing product, working on your actual files, from a chat window. You paste a YouTube link, say what you want, and clips come back captioned, reframed, and ready to post.

The thing that makes this possible is MCP (the Model Context Protocol) and a hosted MCP server we run at OpusClip. This guide walks through the whole setup: what to install, the first prompt to send, how to direct the edit, and what the agent genuinely cannot do yet. Everything here takes about five minutes to get working.

## What MCP actually changes

Before MCP, "use AI to edit video" meant one of two things. Either you used an AI feature inside an editor (still clicking, still in the app) or you wrote code against an API and babysat a job queue. Both work. Neither feels like delegation.

MCP is a standard that lets an AI assistant call external tools directly. When you connect an MCP server, Claude sees a list of functions it can invoke and decides which ones to use based on what you asked for. There is no glue code. You describe the outcome; the model figures out the call sequence.

For video, that is a bigger shift than it sounds. Editing is not one operation. It is a chain of them. Find the good moment. Trim it. Caption it. Reframe it to vertical. Brand it. Export it. Post it. Chaining those by hand is exactly the kind of work that fills an afternoon and produces nothing you would call creative. It is also exactly what an agent is good at, provided something on the other end can actually cut video.

## What you need before you start

- **An MCP-capable client.** Claude Code, Claude Desktop, claude.ai, Cursor, Cline, Continue, or anything else that speaks MCP. The examples below use Claude Code because it is the fastest to set up.
- **An OpusClip account.** Free accounts can connect the server and browse what it exposes. Actually running a job (anything that renders video) requires a paid plan (Pro Beta, Max, or Business).
- **A long video to work from.** A podcast episode, a webinar recording, a livestream VOD, a Zoom call. Anything with more good material in it than you have time to cut by hand.

## Step 1: Install the MCP server

In Claude Code, it is one command:

`claude mcp add --transport http opusclip https://mcp.opus.pro/mcp`
Restart the session, sign in through the OAuth prompt, and you are done. There is no API key to generate, paste, or rotate: the server is hosted on our side and authenticates through your existing OpusClip login.

For clients that expect a local command instead of a URL, use the npm launcher:

`npx -y @opusclip/mcp`
Or drop this straight into the client's config file:

`{"mcpServers":{"opusclip":{"command":"npx","args":["-y","@opusclip/mcp"]}}}`
On claude.ai, add it as a custom connector instead of a command. If a connection fails, the connector URL is the first thing to check: the [agent setup docs](https://help.opus.pro/api-reference/agent-setup) list the current endpoint for every client, and that page is the source of truth if anything here drifts.

## Step 2: Confirm the connection with a read-only prompt

Before you spend a credit, make the agent prove it can see the tools. Ask for something harmless:

Using the OpusClip tools, tell me which account and plan I'm connected as, how many credits I have left this month, and list my most recent projects.

Claude will call the account, usage, and project-listing tools and report back. If that works, every other workflow in this guide will work. If it does not, the problem is authentication or the endpoint, not your prompt.

The server exposes 27 workflow tools: 13 read tools (account, usage, projects, clips, collections, transcripts, brand templates, connected social accounts) and 14 write tools (submit a video, edit clips, censor, build collections, generate social copy, export, post, and schedule). Worth knowing the split, because the read tools cost nothing and are the right way to explore.

## Step 3: Clip a long video

Now the real thing. Give it a URL and an outcome:

Take this YouTube video and pull the 8 strongest standalone moments as vertical clips with captions burned in: \[URL\]. Score them for me and tell me why you picked each one.

What happens next: Claude submits the video, our viral-moment model scores the transcript, and clips come back cut, reframed to 9:16 with subject tracking, and captioned. Rendering is asynchronous: the agent polls until the job finishes rather than blocking your session.

You do not have to upload anything if your video already lives somewhere. The server ingests directly from YouTube, Vimeo, Riverside, Loom, Frame.io, StreamYard, Zoom, Google Drive, and Dropbox. If the file is only on your machine, ask Claude for an upload link and it will generate one.

The scoring matters more than it looks. Asking an agent to "find the good parts" of a two-hour podcast is a genuinely hard retrieval problem, and a general-purpose model reading a transcript is not good at it: it will pick the parts that *read* well rather than the parts that *perform* well. The clipping model is trained on what actually gets watched. Let it do that job, and spend your prompt budget on direction instead. We wrote up how the scoring works in [this breakdown of programmatic viral-moment detection](https://www.opus.pro/blog/viral-moment-detection-api).

## Step 4: Direct the edit instead of accepting it

The first pass is a draft, and the interesting part of an agent workflow is what you do with the second pass. A few directions that work well:

- **Cut to an exact line.** Ask for the transcript first. It comes back with per-word millisecond timing, so you can say "start the clip on the phrase 'nobody tells you this' and end after the laugh" and get a frame-accurate trim rather than an approximation.
- **Skip the AI curation entirely.** If you already know your timestamps (because you marked them while recording, or a producer sent them over), tell Claude to use them. The clipping step can be turned off and the platform used as a pure render engine. This is the single most underrated capability in the whole toolset.
- **Apply your brand.** Ask it to list your brand templates and apply one by name, so caption styling, fonts, and layout match everything else you publish.
- **Batch into a collection.** Group a week of clips, then export the whole collection in one call instead of clip by clip.

The pattern that makes this feel different from an app is that you are editing in sentences. "Make clip 3 four seconds shorter and start it on the question" is a faster instruction to give than it is to execute in a timeline, and it is the same instruction whether there is one clip or thirty.

## Step 5: Publish without leaving the chat

Most agent-video workflows stop at the export, which means they stop right before the part that actually takes the time. Ours does not: the same server that cut the clips can write platform-specific copy and schedule the posts to your connected TikTok, YouTube, Instagram, and LinkedIn accounts.

Write hooks and captions for the top 5 clips, tuned per platform, then schedule them to my TikTok and YouTube accounts one per weekday at 6pm starting Monday.

That is one prompt covering copywriting, sequencing, and distribution. We go deep on this half of the workflow in [turning Claude into your social media manager](https://www.opus.pro/blog/claude-social-media-manager-mcp).

## What the agent can't do yet

Worth being straight about the edges, because finding them mid-project is worse than knowing them now.

- **No multi-segment stitching.** Joining two non-adjacent moments into a single clip is not supported. One clip comes from one continuous range. If you need a supercut, assemble it elsewhere.
- **Rendering takes real time.** Video is not a text completion. A long source video takes minutes, not seconds, and the agent will be polling in the background while it happens.
- **There are usage caps.** 15 hours or 900 credits per workspace per month, a 10-credit floor per project, and 4 concurrent projects. Bulk jobs need to be paced, and an agent left unsupervised on a playlist will find that ceiling for you.
- **Tool calls need a paid plan.** Free accounts connect and browse; rendering requires Pro Beta, Max, or Business.
- **It edits, it doesn't generate.** This server works on footage that exists. If you want video generated from a prompt, that is a different surface: our [Agent Opus MCP server](https://agent.opus.pro/mcp) handles generation, and the two compose well in the same session.

## Where to go from here

If you live in a terminal, there is also an agent skill, [opus-skills](https://github.com/opus-pro/opus-skills), that installs as a plugin in Claude Code, Codex, and OpenClaw and bundles the CLI and ffmpeg helpers alongside the MCP tools. If you would rather build a service than a chat workflow, the same capabilities are available over [the public API](https://www.opus.pro/api), and [this guide to webhooks versus polling](https://www.opus.pro/blog/webhooks-vs-polling-video-api) covers how to wire the async part properly.

The larger point: the bottleneck in short-form video was never the cutting. It was the fifteen small steps between "I have a good recording" and "it is posted." Those steps are legible to an agent now, which means they are delegable. The judgment calls (what is worth saying, which moment lands, what your brand sounds like) are still yours. That is the right split.

## Frequently asked questions

### Can Claude actually edit video?

Yes, through MCP. Claude cannot process video files on its own, but connecting an MCP server that exposes a video editor lets it call real editing tools. With the OpusClip MCP server at https://mcp.opus.pro/mcp, Claude can clip a long video, burn in captions, reframe to 9:16, apply a brand template, export, and schedule the result to social accounts.

### How do I connect Claude to OpusClip?

In Claude Code, run: claude mcp add --transport http opusclip https://mcp.opus.pro/mcp, then restart and sign in through the OAuth prompt. For clients that expect a local command, use npx -y @opusclip/mcp. On claude.ai, add it as a custom connector. No API key is required.

### Is the OpusClip MCP server free to use?

Free accounts can connect the server and browse the available tools, but calling tools that render video requires a paid plan (Pro Beta, Max, or Business). Usage is capped at 15 hours or 900 credits per workspace per month, with a 10-credit minimum per project and 4 concurrent projects.

### What can't Claude do with video over MCP?

Three real limits: it cannot stitch non-adjacent moments into a single clip (one clip comes from one continuous range), rendering takes minutes rather than seconds, and it edits existing footage rather than generating new footage. Generation is a separate surface: the Agent Opus MCP server.