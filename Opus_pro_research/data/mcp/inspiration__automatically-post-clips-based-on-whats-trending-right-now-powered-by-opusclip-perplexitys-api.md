---
url: https://www.opus.pro/mcp/inspiration/automatically-post-clips-based-on-whats-trending-right-now-powered-by-opusclip-perplexitys-api
title: "Automatically post clips about what's trending right now"
description: "Your agent checks Perplexity for what's trending this week, matches it to a clip already in your OpusClip library, and schedules it across every platform: content that reacts to the news cycle instead of guessing at it."
category: mcp
word_count: 890
---

[Inspiration gallery](/mcp/inspiration)

# Automatically post clips about what's trending, with OpusClip and Perplexity's API

Trending topics move fast, and by the time you've manually checked what's hot, matched it to your old footage, and scheduled the post - the moment's gone.

So I built this agent that searches Perplexity for what's actually trending in your niche, finds a relevant clip in your OpusClip library, and schedules it across your platforms. Running every week, fully on autopilot. Try it out!


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

# I. Setting up the Perplexity API

You'll need to create an API key on Perplexity's API platform. You don't need a paid subscription for it, but you'll probably need to purchase like $5 worth of API credits. ($5 should theoretically last you about 5 months to a year for this workflow).

Make sure to check that "Auto Reload" is disabled.

# II. Storing Your Perplexity API Key as an ENV variable

You can store your API key as an "environment variable" on your computer by running the terminal commands below. Then, you can tell your AI assistant to read from that when it needs your API key.

```
echo 'export PERPLEXITY_API_KEY=“PASTE-YOUR-API-KEY-HERE"' >> ~/.zshrc
source ~/.zshrc
```
👆 This is the clean and safe way to do it. **But if you don't care as much, and you tend to just give API keys directly to your AI, then you can do that and skip the above.** (Just know that if your Claude chat is ever leaked, your API key will be visible to outsiders. ⚠️)

# III. Finding daily trends with Perplexity

Here's the prompt you can give your AI, for it to find relevant trends through Perplexity:

Can you use the Perplexity Search API (https://docs.perplexity.ai/api-reference/search-post) and my API key (stored as an ENV variable PERPLEXITY\_API\_KEY) to search for new trending topics from the past week, about the following 5 topics:

1.```
Content creation
2. AI for content creation
3. Creator economy
4. Social media marketing
5. Video editing
```


When looking for "trends", bias towards specifics - breaking news, entirely new developments, or a specific new trending subject from social media.

Then, once you have arrived at a list, narrow it down to only the top 5 most compelling trends across these 5 topics. The 5 trends you narrow down to should be the most unique and most time sensitive.

Return the 5 in an ordered list, with all of the data provided by Perplexity.

# IV. Searching your OpusClip account for relevant clips

Then give your AI the following prompt to find your #1 most "on-trend" clip:

Now, pull up my OpusClip account, and look through all of the clips (titles and transcripts are fine) from all of my past projects, ignoring any projects that are deleted or expired. And try to find the the single clip that is most relevant to one of the top 5 trends that you flagged above. Then, return that clip here.

# V. Scheduling the clips with OpusClip

Give your AI the following prompt to schedule that clip to your social accounts, and tell it to message you with confirmation on Slack:

Now, can you use OpusClip to schedule that clip to be posted tomorrow, onto my connected YouTube Shorts, Instagram, TikTok, and X accounts, at times that are most optimal for each platform. Please tweak the post caption for each post written by OpusClip's AI copywriter, to specifically call attention to the overarching trend itself — and make sure the caption actually renders in each platform's caption field (don't assume title and description behave the same way across platforms; populate both where applicable, and verify nothing posts blank).

After all of the posts are scheduled, please send a DM to me on Slack with a recap of what was scheduled.

# VI. Automate it so that it runs every week on its own

Lastly, you can ask your AI something like the following, so that it will turn this entire workflow into a repeat process that runs on its own going forward:

Can you turn this entire process we just went through into a Claude Routine, that will run every Monday morning at 9 AM Pacific time?

It should be executed autonomously. Have each step occur in sequence, with each output chained together - Not requiring user input or response from me