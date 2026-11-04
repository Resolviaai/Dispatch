---
url: https://www.opus.pro/blog/claude-skills-video-editing
title: "Claude Skills for Video Editing: Setup Guide - OpusClip Blog"
description: "Install the OpusClip skill in Claude Code, Codex, or OpenClaw and clip, edit, and post video from your terminal. Setup commands, limits, and Skills vs MCP."
category: blog
word_count: 1731
---

# Claude Skills for Video Editing: How to Install and Use the OpusClip Skill

Most of the attention on "AI that edits video" has gone to MCP, and it earned it. Point a hosted server at Claude, sign in once, and the model can cut a two-hour podcast into a week of shorts. But there is a second way to give an agent video capability, and for anyone who lives in a terminal it is frequently the better one. It is called a Skill.

We publish one: [opus-skills](https://github.com/opus-pro/opus-skills), a skill marketplace that adds clipping and social posting to a coding agent. This covers what a Skill actually is, how to install it in every host that supports it, what it does that our MCP server cannot, and where the real limits sit. It is in beta, which is worth repeating, because both the tools and the pricing are still moving.

## What a Skill is, and how it differs from MCP

An MCP server is a service your agent talks to over a protocol. It runs somewhere else, it authenticates, and it hands the model a list of callable functions. A Skill is closer to a briefing document with tools stapled to it: a folder containing a SKILL.md file that tells the agent when and how to do a job, plus scripts it can execute locally.

The practical difference is where the work happens. MCP tool calls execute on our infrastructure. A Skill executes on your machine, in your shell, with your files within reach. The same API sits underneath both. The ergonomics are not remotely the same.

SKILL.md is the clever part. It stays out of the context window until it is relevant, then loads when your phrasing matches: "clip this video", "make shorts", "post to youtube", "schedule post". You never invoke it by name. You describe a job in the language you would have used anyway, and the agent recognises that it now has a way to do it.

## Installing it

Pick the line for your host. In Claude Code, add the marketplace and then the plugin:

`/plugin marketplace add opus-pro/opus-skills``/plugin install opusclip@opus-skills`
In the Codex CLI or app:

`codex plugin marketplace add github:opus-pro/opus-skills`
In OpenClaw:

`openclaw plugins install github:opus-pro/opus-skills/skills/opusclip`
In any host that supports the npx installer:

`npx skills add opus-pro/opus-skills`
For claude.ai and Cowork there is no CLI step: download the repo, zip the `skills` folder, and upload it under Settings, then Customize, then Skills.

Then authenticate. Skills use an API key rather than the OAuth flow the MCP server uses, so generate one from the [OpusClip dashboard](https://clip.opus.pro/dashboard) and export it:

`export OPUSCLIP_API_KEY=sk_...`
Two things to check before you start. The key requires an Enterprise, Pro, or Max plan. And the CLI is a bash wrapper, so it expects `curl` and `jq` on your PATH, plus `ffmpeg` if you want the local trim, storyboard, and preview commands. On a developer machine you almost certainly have all three already.

## The first run

You can talk to it in plain language, which is the point of the SKILL.md, but the underlying CLI is worth seeing once because it tells you the shape of what the agent is doing:

`skills/opusclip/scripts/opusclip project create --url "https://youtube.com/watch?v=..."`
The command surface follows a resource-and-verb structure that maps cleanly onto how editing actually works. Projects handle create, list, share, transcript, and preview. Clips cover list, get, export, edit, duplicate, trim, and storyboard. Collections group clips and export in bulk. The social namespace lists connected accounts, generates copy, then publishes or schedules. There is also template listing, usage checks, and thumbnail creation.

In practice you say "clip this episode into six shorts and show me the best three" and the agent composes those calls itself. Seeing the primitives just makes it much easier to correct when it picks the wrong one.

## What a Skill does that MCP does not

- **Local files without a round trip.** The CLI takes a `--file` flag, so a video on your drive goes straight in. No browser upload, no staging it somewhere public first.
- **ffmpeg on your own machine.** Trim, storyboard, and preview run locally. Checking a cut point does not cost a render or a credit, which changes how freely you iterate.
- **It composes with the rest of your shell.** It is bash. Loop it over a directory, pipe it into `jq`, wire it into a Makefile or a cron job. Anything you can script, you can script around this.
- **Far more headroom for bulk.** This is the one that surprises people: 50 concurrent projects against the API, versus 4 through the MCP server. For backfilling an archive, that is not a marginal difference.
- **It lives in the repo.** A Skill is files. Commit it, review it, pin it to a version, ship it to a teammate who gets the identical setup.

## What MCP still does better

- **No key management.** OAuth on first use, nothing to export, nothing to leak into a dotfile or a screen share.
- **It works where there is no terminal.** claude.ai, Claude Desktop, Cursor, Cline, and Continue all speak MCP. The Skills plugin installers do not reach all of them, and Cursor in particular should use [the MCP server](https://www.opus.pro/blog/claude-video-editing-mcp).
- **Approval per call.** Write operations prompt before they fire, which is the safer default when an agent is working unattended.
- **No local dependencies.** Nothing to install, no PATH to get wrong, and it behaves the same on every machine.

## Which one to use

- **Editing in chat, on a laptop, occasionally.** MCP. Faster to set up and there is nothing to maintain.
- **Working in a coding agent all day anyway.** Skills. The video work joins the same shell as everything else you are doing.
- **Processing a backlog.** Skills, for the concurrency ceiling and because bash loops are the right tool for repetition.
- **Local footage rather than links.** Skills, for the `--file` path.
- **Building a product on top.** Neither. Go to [the API](https://www.opus.pro/api) directly, and read [webhooks versus polling](https://www.opus.pro/blog/webhooks-vs-polling-video-api) before you wire the async half.
- **Not sure.** Install both. They authenticate separately and do not conflict, and the answer becomes obvious within a week.

## The guardrails are deliberate

The SKILL.md constrains the agent on purpose, and the constraints are worth knowing because they will occasionally interrupt you. It shows a copyright notice before creating any project, confirming the footage is yours. It stops for confirmation before running three or more clip edits, or five or more scheduled posts, in a single session. It will not loop those operations without checking in on each iteration. And it lists clips by title and description rather than ID, so you are approving something legible instead of a hash.

None of that is decoration. An agent with a posting tool and no brakes is a genuinely bad afternoon, and rate limits are a poor substitute for a confirmation step.

## The limits, plainly

- **Beta.** Tools and pricing are subject to change, and API pricing can differ from web pricing.
- **30 requests per minute.** Enough for interactive work, worth pacing in a loop.
- **10 hours maximum per video.** Jobs draw on your OpusClip credit balance. Credit use depends on the source video and the features you turn on, not on how many clips come out.
- **Projects expire after 30 days.** Export anything you intend to keep.
- **Plan gated.** The key needs Enterprise, Pro, or Max.
- **No multi-segment stitching.** One clip comes from one continuous range. Supercuts still get assembled elsewhere.

## Where this fits

Skills and MCP are two doors into the same building, and the choice is mostly about where you already work. If you want the chat-first version, start with [the MCP setup guide](https://www.opus.pro/blog/claude-video-editing-mcp). If distribution is the part eating your week, [the clip to copy to schedule pipeline](https://www.opus.pro/blog/claude-social-media-manager-mcp) covers it end to end. For a map of what else is out there, we surveyed [the video MCP landscape](https://www.opus.pro/blog/best-mcp-servers-for-video), competitors included.

The reason to care about the Skill specifically: it puts video into the environment where a lot of technical work already happens. Clipping a demo recording stops being a context switch into a separate app and becomes another thing the agent handles in the window you already had open. That is a small change that compounds, because the edits you actually ship are the ones that were never worth interrupting your day for.

## Frequently asked questions

### What are Claude Skills?

A Skill is a folder containing a SKILL.md instruction file plus scripts an agent can run locally. The instructions stay out of the context window until your request matches the skill's triggers, then load automatically. Unlike an MCP server, which runs remotely and exposes callable functions over a protocol, a Skill executes on your own machine with access to your local files. Claude Code, Codex, OpenClaw, and claude.ai all support them.

### Can Claude Code edit videos with a skill?

Yes. Installing the OpusClip skill with /plugin marketplace add opus-pro/opus-skills and /plugin install opusclip@opus-skills gives Claude Code a bash CLI that clips long videos, edits and trims clips, generates captions and social copy, and posts or schedules to connected accounts. It triggers on natural phrasing such as "clip this video" or "make shorts", so you do not call it by name.

### Should I use the OpusClip skill or the MCP server?

Use MCP for chat-first editing, for clients without a terminal such as claude.ai or Cursor, and when you would rather not manage an API key, since it authenticates over OAuth. Use the skill when you already work inside a coding agent, need local files via the --file flag, want ffmpeg trim and preview without spending a render, or are processing a backlog: the API allows 50 concurrent projects against 4 through MCP. They do not conflict, so installing both is reasonable.

### What do I need to use the OpusClip skill?

An API key from the OpusClip dashboard, which requires an Enterprise, Pro, or Max plan, exported as OPUSCLIP\_API\_KEY. The CLI is a bash wrapper, so it also needs curl and jq on your PATH, plus ffmpeg for the optional storyboard, trim, and preview commands. Limits are 30 requests per minute, a 10 hour maximum video length, and projects expiring after 30 days. Jobs draw on your OpusClip credit balance.