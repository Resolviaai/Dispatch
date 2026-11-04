---
url: https://www.opus.pro/mcp/inspiration/turn-any-longform-video-into-an-email-newsletter-using-the-opusclip-and-beehiiv-mcps
title: "Automatically turn your videos into email newsletters with OpusClip and beehiiv"
description: "Your agent pulls the transcript and clips from your newest OpusClip video, drafts a recap newsletter around them, and builds it as a ready-to-send draft in beehiiv: an audience-building email instead of a video that only lives on social."
category: mcp
word_count: 1201
---

[Inspiration gallery](/mcp/inspiration)

# Turn any longform video into an email newsletter, using the OpusClip and beehiiv MCPs

Newsletters are one of the most obvious ways to grow your mindshare. But by the time you've rewatched your last video, pulled the transcript apart, and figured out how to turn it into an email... usually you've already moved on to filming the next one.

So I built this agent that pulls the transcript from your newest OpusClip video, matches it with clips you've already posted, and drafts the whole thing as a ready-to-send email in beehiiv. Running every Monday morning, fully on autopilot. Try it out!


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

# I. Connecting OpusClip & beehiiv to your AI

Both OpusClip and beehiiv connect to your AI assistant directly as MCP connectors (Claude, ChatGPT, and others all support this). No API keys needed here, just add the OpusClip MCP and the beehiiv MCP as connectors in your AI assistant of choice, and you're ready to go.

For tutorials on how to install the OpusClip MCP, go here: [https://www.opus.pro/mcp#how-to-install](https://www.opus.pro/mcp#how-to-install)

And for beehiiv's MCP, go here: [https://www.beehiiv.com/support/article/39255979546263-getting-started-with-the-beehiiv-mcp](https://www.beehiiv.com/support/article/39255979546263-getting-started-with-the-beehiiv-mcp)

# II. Finding your newest video, and any clips you've already posted about it

Start by giving your AI the following prompt to pull your latest video and surface relevant supporting clips:

Using the OpusClip MCP:

1. List my OpusClip projects, sorted most-recent-first, identify the single most recent project, and pull its full transcript.

2. Look at my OpusClip Scheduler for posts sent to my YouTube (Shorts) account over the last 30 days with status "posted" (actually live, not just scheduled). Identify up to 3 clips that are thematically relevant to the most recent project's topic. Prioritize clips that came from the same source project if any exist, otherwise pick the closest topical matches, and report each one's title, description, clip\_id, score, duration, and real YouTube URL.

Give me back: the source video's title, project ID, source type (was it imported via a pasted YouTube link, or uploaded as a file?), and, if YouTube, its original YouTube URL; the full transcript text; and the details for each relevant clip found. Don't draft anything yet, just gather and report this information.

# III. Drafting the recap newsletter

Next, have your AI turn that raw material into an actual newsletter draft, using this prompt:

Using the transcript and relevant clips gathered in the previous step, draft a "video recap" newsletter body using this structure:

• A small eyebrow/kicker label at the very top (e.g., "New YouTube video")

• A title and subject line / preview text for the email

• If the source video's source type was a pasted YouTube link: include a landscape (16:9) hero video embed of the SOURCE video itself, placed right after the headline and before the intro copy, using its original YouTube URL. If the source video was uploaded as a file instead, skip this hero embed entirely and go straight from the headline into the intro copy (beehiiv can't embed a non-YouTube source that way).

• An intro (1-2 short paragraphs) framing this as a recap of the newest video, written like an "in case you missed it" note to subscribers

• 3 body sections, each with its own header. Read the transcript and pick headers/themes that actually fit THIS video's content (don't reuse headers from a prior example)

• Within each section: write the explanatory body copy first, then place one of the relevant clips right after it as a natural supporting example, with a one-line caption tying the clip back to what was just described. Order the clips so a hook-y one appears near the top, a supporting example lands mid-body, and the strongest/highest-scoring one closes out the last section

• A closing paragraph

• A CTA button back to the full video

• A sign-off

Give me the complete drafted copy, section by section, clearly noting whether a hero embed should be included (and its URL if so), with each clip's placement clearly marked (include its real YouTube URL). Don't create anything in beehiiv yet, just produce the draft text for review.

# IV. Building the draft inside beehiiv

Once you're happy with the draft, have your AI turn it into an actual beehiiv post:

Using the beehiiv MCP, take the drafted newsletter copy from the previous step and create it as a new draft post in my beehiiv account:


• Use the "\[TEMPLATE NAME\]" post template (look up its current ID via list\_post\_templates) as the starting point

• Set the post title, subject line, and preview text from the draft

• If the draft includes a hero video embed, place it near the top as a landscape YouTube embed using its URL

• Build the body content: for each of the 3 clips, use a two-column layout: the embedded clip video in the left column, and its title + description + metadata in the right column, matching the reference email format

• Include the CTA button linking to the full video

• Leave the post as a draft, do not try to schedule or send it

Give me the beehiiv editor URL when done so I can review it myself.

\* And be sure to swap **`[TEMPLATE NAME]`** for whichever beehiiv post template you want to use (e.g., a template built specifically for this recap format).

# V. Automate it so that it runs on its own

Lastly, you can ask your AI something like the following, so it checks for new video content and runs this whole workflow automatically going forward:

Can you turn this entire process we just went through into a Claude Routine? Have it check every Monday morning at 9 AM Pacific time for whether a new OpusClip project has been added since the last run. If there's a new one, run the full workflow: find the video and relevant clips, draft the recap, and build it as a draft in beehiiv. If there's nothing new, skip the run entirely.

It should be executed autonomously. Have each step occur in sequence, with each output chained together, not requiring user input or response from me. The final beehiiv draft should always be left unpublished for me to review and send myself.