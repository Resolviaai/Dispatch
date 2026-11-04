---
url: https://www.opus.pro/blog/add-opusclip-mcp-to-grok-bot
title: "How to Add the OpusClip MCP Server to Grok Bot - OpusClip Blog"
description: "Add an MCP server to Grok Bot in minutes: install OpusClip from Plugins or as a custom server, sign in, set a posting rule, and run your first video task."
category: blog
word_count: 1172
---

# How to Add the OpusClip MCP Server to Grok Bot

**To add an MCP server to Grok Bot, either install it from the Plugins marketplace or ask your bot in chat to add a custom MCP server by URL.** For OpusClip, tell the bot "Add a custom MCP server called OpusClip at https://mcp.opus.pro/mcp", confirm, authorize, and sign in. Your bot can then clip, caption, edit, and schedule video through OpusClip. It takes about five minutes.

This guide walks through both methods, the posting rule to set first, and fixes for common errors.

## How to add an MCP server to Grok Bot: two routes

Grok Bot MCP support is built into the bot itself. Grok Bot uses connectors and MCP servers where clean integrations exist and computer use for apps without them. There is no config file for MCP servers and no settings field for a server URL. Instead there are two routes:

- **Grok Bot plugins:** install a listed vendor from the Plugins marketplace.
- **Grok Bot custom MCP server:** ask the bot in chat to add any remote server by its HTTPS URL.

MCP, the Model Context Protocol, is the open standard that lets agents call tools in other products. Here is [what MCP means for video editing](https://www.opus.pro/blog/what-is-mcp-for-video-editing).

## What you need

- **Grok Bot:** xAI lists SuperGrok, SuperGrok Plus, SuperGrok Heavy, and Cursor Pro, Pro+, Ultra, and Teams plans, on desktop and iOS.
- **An OpusClip account:** any account can connect and see the tools. Running them requires Pro, Max, or Business.
- **Your social accounts connected in OpusClip:** if you want the bot to post.

## Method 1: Install OpusClip from Plugins

1. Open **Plugins** in the Grok Bot app.
2. Search for **OpusClip** and choose **Add**.
3. Go to **Installed**, open the OpusClip entry, and choose **Authenticate**.
4. Sign in to OpusClip and pick your workspace.

If OpusClip does not appear in your Plugins list, use Method 2. It connects to the same server.

## Method 2: Add OpusClip as a custom MCP server

1. Open a chat with your bot and type: **Add a custom MCP server called OpusClip at https://mcp.opus.pro/mcp**
2. The bot shows the name and URL and asks you to confirm. Check the URL, then choose **Add it**.
3. The bot posts a connect card in the chat. Choose **Authorize** and sign in to OpusClip in your browser.
4. Type **@** in the chat to attach OpusClip to a task.

Say "custom server" in your request so the bot adds the URL you gave it rather than searching the marketplace. Sign in with the same method you used to create your OpusClip account (email, Google, or Apple). There is no API key.

## Step 3: Set your posting rule first

The OpusClip MCP server at mcp.opus.pro commits posting, scheduling, unscheduling, and project sharing as soon as those tools are called. Grok Bot comes back to you when something needs approval, so make publishing one of those moments. Send this before anything else:

When using OpusClip, never post, schedule, unschedule, or share anything without asking me first. Clipping, editing, and drafting copy are fine without asking.

## Step 4: Test the connection

Using OpusClip, tell me which workspace you are connected to and how much of this month's usage is left.

A workspace name and usage figure mean it works.

## Step 5: Run your first task

Using OpusClip, clip this video and give me the 5 best vertical clips under 60 seconds with captions: \[link\]. Remove filler words. Show me the clips before doing anything else.

Long videos take a few minutes to process. The bot keeps working and reports back when the clips are ready.

## What your bot can do with OpusClip

- **Clip:** submit videos from YouTube, Vimeo, Google Drive, Dropbox, Zoom, Loom, and more.
- **Edit:** captions, emoji, keyword highlights, filler-word and pause removal, trims, splits, reordering, phrase cuts, caption styling, dubbing, and undo.
- **Inspect:** read transcripts, describe clips, and analyze what is on screen.
- **Organize and export:** collections and HD download links.
- **Write:** platform-specific titles, descriptions, and hashtags.
- **Publish:** post or schedule to YouTube, TikTok, Instagram Reels, Facebook Pages, LinkedIn, X (in beta), Pinterest, Threads, and Bluesky.
- **Thumbnails:** generate thumbnail options for a clip.

The full list is in our reference to [every tool in the OpusClip MCP server](https://www.opus.pro/blog/opusclip-mcp-tools-reference).

## Troubleshooting

- **The bot installs something else:** repeat the request and say "custom MCP server" with the full URL.
- **"No matching client" at sign-in:** use the same sign-in method as your OpusClip account.
- **"API access not enabled for this org":** the workspace needs Pro, Max, or Business.
- **403 errors:** the monthly cap of 15 hours or 900 credits per workspace is used up. It resets on the first of the month (UTC).
- **429 errors:** four projects are already processing. The bot can retry when one finishes.
- **Wrong workspace:** remove the server and add it again, picking the right workspace at sign-in.
- **Bare URL fails:** the address must include the path: https://mcp.opus.pro/mcp.

## Plugins or custom server: which to choose

- **Plugins:** the quickest route when the vendor is listed, with sign-in handled from the Installed list.
- **Custom server:** works for any remote MCP server, including ones not yet in the marketplace, and lets you check the exact URL before adding it.
- **Either way:** the bot ends up calling the same hosted OpusClip server, with the same tools and the same usage limits.

Whichever route you pick, check the URL against our help center before you authorize, and only add servers from vendors you trust. An MCP server can act on your behalf in that product.

## Next steps

With OpusClip connected, learn how to [use Grok Bot to clip and post videos to X](https://www.opus.pro/blog/grok-bot-clip-and-post-videos-to-x), or go further and [build an always-on video team with Grok Bot](https://www.opus.pro/blog/grok-bot-always-on-video-team). Still choosing tools? Read our guide to the [best video editing bot for Grok Bot](https://www.opus.pro/blog/best-video-editing-bot-for-grok-bot).

## Frequently asked questions

### How do I add an MCP server to Grok Bot?

Install it from Plugins if it is listed, or ask your bot in chat: "Add a custom MCP server called \[name\] at \[https URL\]." Confirm, then authorize on the connect card.

### How do I connect OpusClip to Grok Bot?

Ask the bot to add a custom MCP server called OpusClip at https://mcp.opus.pro/mcp, or install OpusClip from Plugins. Then authorize and sign in to OpusClip.

### Do I need an OpusClip API key?

No. The OpusClip MCP server uses a sign-in flow, so there is nothing to copy or store.

### Is the OpusClip MCP server free?

Connecting is free for any account. Running tools requires Pro, Max, or Business and uses credits.

### Can I use the same OpusClip MCP server in other agents?

Yes. The same server works in other MCP hosts. For always-on agents from other companies, see the [best video editing bot for OpenAI Dots](https://www.opus.pro/blog/best-video-editing-bot-for-openai-dots) and the [best video editing bot for Meta Muse](https://www.opus.pro/blog/best-video-editing-bot-for-meta-muse).