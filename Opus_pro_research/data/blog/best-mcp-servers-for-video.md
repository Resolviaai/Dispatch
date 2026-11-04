---
url: https://www.opus.pro/blog/best-mcp-servers-for-video
title: "The 10 Best Video Editing MCP Servers in 2026 - OpusClip Blog"
description: "A map of the video MCP landscape: clipping, generation, avatars, rendering, and infrastructure, with endpoints, tool counts, and the real limits."
category: blog
word_count: 2415
---

# The 10 Best Video Editing MCP Servers in 2026

**"Video MCP server" now describes about six different jobs, and most roundups blur them together.** A server that generates footage from a prompt, a server that renders JSON into an MP4, a server that indexes a video library for search, and a server that clips a podcast into shorts are not substitutes for each other. They are different layers of the same stack, and picking the wrong layer is the most common reason an agent workflow stalls halfway through.

So this is a map, sorted by what each server's tools actually do. One disclosure up front: we build OpusClip and Agent Opus, so read those two entries with that in mind. We have tried to be specific enough (endpoints, tool counts, named limitations) that every claim here is checkable against the vendors' own docs. If the underlying concept is new, [start here instead](https://www.opus.pro/blog/what-is-mcp-for-video-editing).

## Clipping and repurposing: long video in, posts out

This is the most competitive layer in the stack and the one that changed most in the last year. All three servers below now clip, caption, reframe, and publish from an agent. The differences are in curation quality, how deep the editing goes, and what happens when the first result is not quite right.

### OpusClip

**Endpoint:** `https://mcp.opus.pro/mcp`, hosted, OAuth. **Tools:** 27.

Ours. It takes a long video (from a URL or an upload link), scores it for the moments most likely to perform, and cuts clips with burned-in captions and subject-tracked reframing. It ingests directly from YouTube, Vimeo, Riverside, Loom, Zoom, StreamYard, Frame.io, Drive, and Dropbox, so a recording goes straight from where it already lives into the workflow. Clips come back ranked, with judge sub-scores for hook, coherence, connection, and trend rather than a single opaque number.

The part that is genuinely uncommon is not the clipping: it is what happens after. `get_editing_script` and `apply_editing_script` round-trip the same edit format the OpusClip web editor saves, so an agent can trim to an exact millisecond, rewrite a caption, or drop an element, then re-render. `export_clip` will hand back an Adobe Premiere project file, so a clip an agent assembled can be finished by a human editor. No other server in this cohort exposes editor-parity edits or an NLE round-trip. We walk through the actual edits in [the editing guide](https://www.opus.pro/blog/claude-video-editing-tutorial), and [the full tool reference](https://www.opus.pro/blog/opusclip-mcp-tools-reference) lists all 27.

Transcripts return per-word millisecond timing, so an agent can find a phrase and compute an exact boundary, and curation can be switched off entirely to use the server as a pure render engine against timestamps you supply. Posting, scheduling, queue listing, and unscheduling are all named tools.

**Limits worth knowing:** no multi-segment stitching (one clip, one continuous range); no dubbing; tool calls require a paid plan; 15 hours or 900 credits per workspace per month with 4 concurrent projects.

### Reap

**Endpoint:** `https://mcp.reap.video/mcp`, hosted, OAuth. **Tools:** 29.

The most direct competitor to us on the agent surface, and on raw tool count the largest server in this layer. 29 tools across six categories: creating projects (clips, captions, transcription, reframe, dubbing), tracking and retrieving results, editing metadata, publishing, reference catalogs, and billing.

Two things it does that we do not. Dubbing into 80+ languages is a first-class tool rather than a caption translation, which matters if your distribution is genuinely multilingual. And billing is agent-accessible: an agent can check plan usage, top up credits, and manage the subscription without a human opening a dashboard, which is either convenient or alarming depending on your governance.

Its publishing surface is a full six tools, including listing and editing already-scheduled posts. If you read an older roundup saying Reap is API-only for posting, that is out of date.

**Where it stops:** its editing tools cover metadata (`update_clip`, `update_video`) rather than the timeline. There is no equivalent of an editing-script round-trip, so "trim four seconds off the front of clip 3" is not a thing an agent can do against a rendered clip.

### Submagic

**Endpoint:** `https://api.submagic.co/mcp`, hosted, Bearer API key. **Tools:** around 18.

Strong product with a compact, sensibly chosen tool set: project creation from a URL, Magic Clips generation, status retrieval, background music, caption template access, export, and publishing to TikTok and Instagram. It is also the only server in this layer that hands an agent published-post analytics, so "which of last week's clips actually performed" is answerable in the same session that made them.

The notable difference is auth. Submagic uses a Bearer API key rather than OAuth, which means a key has to be generated, pasted, and stored somewhere. That is a small thing for a developer and a real barrier for everyone else, and it is the one place this layer has not converged.

**Where it stops:** no timeline-level editing tools, and the public docs do not publish a definitive tool list, so treat the count as approximate.

## Generation: prompt in, footage out

### Agent Opus

**Endpoint:** `https://agent.opus.pro/mcp`, hosted, OAuth. **Tools:** 29.

Also ours, and the generation counterpart to the clipping server: text or a script in, a finished video out with AI voiceover, visual styles, and timing. It also builds music videos around an uploaded track, with visuals cut to the song and lip-synced performance, which is an unusual capability to find behind an agent interface.

The tool surface splits into project creation, a reusable asset library (logos, products, characters that can anchor any video), voice cloning and voice selection, visual style listing and customization, motion graphics, and its own scheduling tools. Because both of our servers can be connected at once, generate then clip then schedule runs in a single session without leaving chat.

**Limits worth knowing:** videos cap at about 10 minutes; generation is voice-driven, so a script or an audio track is required rather than optional; styles are set from images and text, not from a reference video; there is no dubbing; and generation is approval-gated rather than fully autonomous.

### RunwayML

**Endpoint:** `https://mcp.runwayml.com/mcp`, hosted, OAuth.

Strategically the most interesting server in the stack, because it is a menu rather than a dish. Through one connector, an agent can run text-to-video and image-to-video on Runway's own Gen-4.5 and Gen-4 Turbo *and* on Kling 3.0, Google Veo 3.1, Seedance 2.0, GPT Image 2, and Nano Banana Pro. Billing draws on Runway credits.

That aggregation is worth thinking about as a pattern, not just a feature. Runway has made itself the surface through which its own competitors get called, which means an agent asking for "the best available model" reaches Runway first regardless of who wins the model race. If your workflow is "generate a shot," this is the widest single surface.

### Replicate

**Endpoint:** `mcp.replicate.com`, hosted, OAuth, with a local build available.

Search, compare, and run across Replicate's model catalog, video models included. It is discoverable through the official MCP Registry, which matters more than it sounds: registry presence is how agents find servers without a human pasting a URL, and most servers in this roundup are not there.

The tradeoff against Runway is control versus curation. Replicate gives you a far larger catalog and expects you to know which model you want; Runway gives you a shortlist someone has already vetted. Pick Replicate when model choice is the point.

## Avatar and voice

### HeyGen

**Endpoint:** `https://mcp.heygen.com/mcp/v1/`, hosted, OAuth. **Tools:** 45.

The most complete agent surface in the entire video stack, and the benchmark everyone in this category is measured against. 45 tools across video agent, videos, avatars, voices, audio, lip sync, translation, assets, and brand.

It is also listed in the Cursor marketplace bundled with three skills, and that is the more important lesson. Being in the store where agent users browse beats having more tools than anyone else, and HeyGen happens to have both. Every other vendor here, us included, is reachable mainly by someone already knowing the URL.

**One trap:** HeyGen also ships an older 6-tool local server that many directories still list. If you are evaluating HeyGen, evaluate the 45-tool hosted server.

## Editing and rendering: programmatic composition

### Shotstack

**Endpoint:** `https://mcp.shotstack.io/`, hosted (OAuth) with a local stdio fallback. **Tools:** 10.

The real render toolset in this category: render a video, check render status, create studio links, and five template operations. This is the server to reach for when video is assembled from data rather than captured, such as a personalized video per customer record or a weekly report rendered from a spreadsheet.

Client support is unusually broad: claude.ai, Claude Desktop, Claude Code, ChatGPT, Cursor, VS Code, Codex CLI, Gemini CLI, Windsurf, Zed, JetBrains, Goose, Raycast. Flagged as beta, so expect the tool surface to move.

### Remotion

Worth including specifically to correct a common misreading. Remotion is the leading open-source programmatic-video framework, but its *official* MCP server (`@remotion/mcp`, local via npx) is a documentation-retrieval helper: it indexes Remotion's docs so your editor's AI understands the framework better. It does not expose tools that render video.

Community servers do add render tools, so "Remotion can't render via MCP" is false; only the official server is docs-only. Know which one a directory is listing, because the two are routinely conflated and they solve completely different problems.

## Infrastructure and understanding

### Mux

**Endpoint:** `https://mcp.mux.com`, hosted, automatic OAuth-style auth.

The infrastructure layer: asset creation, uploads, live streams, playback URLs, plus the Data API's metrics and real-time dimensions. Tools are generated dynamically, so do not cite a fixed count.

Its nicest design touch is a client query parameter that scopes the tool set per client. That is a genuinely good pattern worth copying, because agents make better choices with smaller tool lists, and a 40-tool server that dumps everything into context makes the model worse at picking.

### Twelve Labs

**Endpoint:** `https://mcp.twelvelabs.io` (hosted) alongside its original local server.

Video understanding: search, analysis and summarization, and indexing exposed as tools. This is the layer you want when the question is "find every moment in our 400-hour archive where someone mentions pricing" rather than "make me a video."

It pairs naturally with the clipping layer rather than competing with it. Twelve Labs finds the moment across a library; a clipping server turns that moment into something postable.

## How to actually choose

Sort by the job, not the brand:

- **You have long recordings and need posts.** Clipping layer: OpusClip, Reap, or Submagic. All three publish from the agent now, so decide on curation quality, whether you need dubbing (Reap) or timeline-level editing (OpusClip), and whether OAuth versus an API key matters to whoever is installing it.
- **You need footage that doesn't exist.** Generation layer: Runway for model breadth, Replicate for catalog and registry discoverability, Agent Opus for script-to-finished-video and music videos.
- **You need a presenter without filming one.** HeyGen, on the 45-tool hosted server.
- **You're composing video from data or templates.** Shotstack for a real render toolset; Remotion if you are writing React and want the docs helper.
- **You're building a product on top of video.** Mux for infrastructure, Twelve Labs for search and understanding.
- **You want vertical versions of existing videos.** Any of the clipping three will reframe, and [it can be done without clipping the video up](https://www.opus.pro/blog/horizontal-to-vertical-video-ai-agent) if you want the full length kept.

One more decision sits underneath all of these: whether you want an agent driving the tools at all, or a pipeline you write yourself against the same capabilities. We put the two side by side in [MCP versus API](https://www.opus.pro/blog/video-editing-mcp-vs-api).

## Three patterns worth noticing

**Hosted with OAuth has all but won.** Nearly every serious server here is remote and authenticates through a sign-in flow rather than a pasted API key, with Submagic the notable holdout. Local stdio servers still make sense for developer context, but for agent distribution they are a dead end: too much install friction between "I want this" and "it works."

**Publishing stopped being a differentiator this year.** A year ago most video servers stopped at the export and left distribution to a second tool. Today all three clipping servers post and schedule from the agent, and two of them expose queue management. If a roundup still tells you posting is rare, check its date.

**The new cut line is how deep the editing goes.** Every server here can produce a video. Far fewer can change one after it exists. Metadata edits are common; timeline edits against a rendered clip, and handing the result to a human editor in Premiere, are not. That is where this category is competing next, because "the agent made something almost right" is the failure mode every one of these products actually has.

If clipping, editing, and publishing is the job you have, [our setup guide](https://www.opus.pro/blog/claude-video-editing-mcp) gets the OpusClip server running in one command, [the prompt pack](https://www.opus.pro/blog/claude-code-video-prompts) covers the sequences worth saving, and [the scheduling walkthrough](https://www.opus.pro/blog/claude-social-media-manager-mcp) covers the publish half.

## Frequently asked questions

### What is the best MCP server for video editing?

It depends on the job. For turning long recordings into short-form posts, the clipping layer (OpusClip, Reap, Submagic) is the right one, and all three now clip, caption, reframe, and publish from an agent. OpusClip is the one that exposes timeline-level editing, with an editor-parity script round-trip and an Adobe Premiere export. Reap has the largest tool count at 29 and adds dubbing. For programmatic composition from templates or data, Shotstack ships a real 10-tool render server.

### Which video MCP servers can post to social media?

All three in the clipping layer. OpusClip exposes posting, scheduling, queue listing, and unscheduling. Reap ships six publishing tools including listing and editing already-scheduled posts. Submagic publishes to TikTok and Instagram and adds published-post analytics. Outside that layer it remains rare: generation, rendering, and infrastructure servers almost all stop at the export.

### Does Remotion have an MCP server that renders video?

Not officially. Remotion's official MCP server (@remotion/mcp, run locally via npx) is a documentation-retrieval helper that indexes Remotion's docs so an editor's AI understands the framework. It does not expose render tools. Community-built Remotion MCP servers do add rendering, so check which one a directory is listing.

### Are MCP servers for video hosted or do I install them locally?

The serious ones are hosted and remote, and nearly all use OAuth sign-in with nothing to install. Submagic is the main exception, using a Bearer API key. Local stdio servers still have a place in developer tooling, but install friction makes them a poor fit for agent distribution.