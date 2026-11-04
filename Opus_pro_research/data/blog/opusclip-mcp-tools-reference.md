---
url: https://www.opus.pro/blog/opusclip-mcp-tools-reference
title: "OpusClip MCP Server: All 27 Tools, Explained - OpusClip Blog"
description: "A complete reference for all 27 tools in the OpusClip MCP server: clipping, editing, export, collections, and publishing, with which ones cost credits."
category: blog
word_count: 1021
---

# Every Tool in the OpusClip MCP Server: A Complete Reference

**The OpusClip MCP server exposes 27 tools.** This page lists all of them, grouped by what they do, with the ones that cost credits marked. It exists because "what can this server actually do" is a question people ask agents, and a vague answer is useless to both.

Every tool name below is prefixed `opusclip_` in your client. Connect the server first if you have not: [the setup guide](https://www.opus.pro/blog/claude-video-editing-mcp) covers every client.

## Submitting and inspecting projects

- **submit\_project.** The main entry point. Give it a video URL and it clips, captions, and reframes. Accepts YouTube, Vimeo, Google Drive, Dropbox, Zoom, Riverside, Loom, Frame.io, StreamYard, Twitch, Rumble, Facebook, LinkedIn, X, or any public S3 mp4 up to 10GB. Costs credits.
- **list\_projects.** Every project in the workspace.
- **get\_transcript.** The source video's transcript with per-word millisecond timing. This is what lets an agent find a phrase and compute an exact trim boundary.
- **create\_upload\_link.** An upload target for footage that is not reachable by URL.
- **share\_project.** A shareable link to a project.

## Working with clips

- **list\_clips.** A project's clips with rank, virality score, title, description, hashtags, duration, and preview URLs. Judge sub-scores for hook, coherence, connection, and trend appear once a clip is scored.
- **describe\_clip.** One clip in detail: transcript, keywords, layout, score, preview URL, and whether a re-render is still in flight.
- **duplicate\_clip.** An independent copy, so a variant never risks the original. Not idempotent: calling twice makes two copies.

## Editing

This is the group most people do not know exists, and the reason an agent can finish a job rather than just start one.

- **get\_editing\_script.** Returns the clip's full EditingScript, the same JSON the web editor's Save button writes.
- **apply\_editing\_script.** Posts a mutated script back and re-renders. Together these two make arbitrary edits possible: trims to an exact millisecond, caption rewrites, retiming, dropping elements. Re-renders do not consume extra credits today.
- **create\_censor\_job.** Profanity censor on the caption track, asterisks by default, beep optional. Triggers a re-render.

## Exporting

- **export\_clip.** One clip, as HD (default), 4K (paid plan plus a 4K source), or `xml`, an Adobe Premiere project file for finishing in Premiere Pro or DaVinci Resolve. Clips are preview-only until exported.
- **export\_collection.** HD download URLs for every clip in a collection at once.

## Collections

- **create\_collection.** A new collection.
- **add\_clip\_to\_collection.** Put a clip in one.
- **list\_collections.** All collections.
- **list\_clips\_in\_collection.** What is in a given collection.

## Copy and publishing

- **create\_social\_copy\_job.** Generates titles, descriptions, and hashtags for a clip and a target account. Accepts a tone prompt.
- **get\_social\_copy\_job.** Polls that job until the copy is ready.
- **list\_social\_accounts.** Connected accounts.
- **create\_post\_task.** Builds a post.
- **schedule\_publish.** Queues a clip to a connected account at a chosen time.
- **list\_scheduled\_posts.** The queue.
- **unschedule\_publish.** Pulls something back out of it.

Outside the clipping layer this group is rare: generation, rendering, and infrastructure servers almost all stop at the export. Within it, publishing has become common, so it is worth checking what a server does after publishing as well as whether it can. We covered how the whole pipeline chains together in [the scheduling walkthrough](https://www.opus.pro/blog/claude-social-media-manager-mcp).

## Account and configuration

- **whoami.** Which account and plan the agent is authenticated as. The right first call when something is failing.
- **get\_usage.** Credit and quota consumption.
- **list\_brand\_templates.** Brand templates in the workspace, for passing to a submission.

## Which tools cost anything

Reads are free. The tools that consume credits are the ones that render video, principally `submit_project`. Re-renders triggered by `apply_editing_script` and `create_censor_job` do not consume extra credits today, though that may change. Connecting the server and browsing the catalog is free on any plan; calling tools that render requires a paid plan.

Because reads are free, the sane way to start is a read-only prompt. Ask the agent to call `whoami` and list your projects. If that works, the connection is good and nothing has been spent.

## Chains worth knowing

- **Clip and publish.** submit\_project, list\_clips, create\_social\_copy\_job, schedule\_publish.
- **Fix and ship.** get\_editing\_script, apply\_editing\_script, wait for the render, export\_clip.
- **Find an exact moment.** get\_transcript for word-level timing, then submit with a range, or trim an existing clip to those boundaries.
- **Safe experiment.** duplicate\_clip, then edit the copy.

## Limits worth knowing up front

- **No multi-segment stitching.** Joining non-adjacent moments into one clip is not supported. Each clip comes from one continuous stretch of source.
- **Renders are asynchronous.** Anything that re-renders returns a pending flag first. Reading the artifact before that clears gives you the previous version.
- **The agent cannot see the picture.** Every decision comes from transcripts, timestamps, and metadata.

For what these tools add up to conceptually, see [what MCP for video editing is](https://www.opus.pro/blog/what-is-mcp-for-video-editing). For how the editing tools work in practice, see [the editing guide](https://www.opus.pro/blog/claude-video-editing-tutorial).

## Frequently asked questions

### How many tools does the OpusClip MCP server have?

27, all prefixed opusclip\_. They cover submitting and inspecting projects, working with clips, editing, exporting, collections, social copy and publishing, and account configuration. Reads are free; the tools that render video consume credits.

### Which OpusClip MCP tools can edit video?

get\_editing\_script and apply\_editing\_script round-trip the clip's full EditingScript, the same JSON the web editor saves, which makes trims, caption rewrites, and retiming possible. create\_censor\_job handles profanity. duplicate\_clip creates a safe copy to edit. Filler-word removal and reframing are parameters on submit\_project.

### Do OpusClip MCP tools cost credits?

Read tools are free. Rendering video costs credits, principally submit\_project. Re-renders from apply\_editing\_script and create\_censor\_job do not consume extra credits today, though that pricing may change. Connecting the server and browsing the tool catalog is free on any plan.

### Can the OpusClip MCP server post to social media?

Yes. schedule\_publish queues a clip to a connected account, list\_scheduled\_posts shows the queue, and unschedule\_publish removes something from it. create\_social\_copy\_job generates the titles, descriptions, and hashtags first. Competing clipping servers now publish too, so the sharper difference is what happens after: editing a clip that already exists.