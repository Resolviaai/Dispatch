---
url: https://www.opus.pro/api
title: "OpusClip API: The #1 AI video clipping API"
description: "Build an autonomous video pipeline of viral clips with OpusClip API. Automatically generate captions, highlights, and social ready exports at scale. Designed for creators, developers, and businesses."
category: core_features
word_count: 606
---

[How Audacy Drove 1B+ Views](/how-audacy-drove-1b-views-using-opusclip)

# The video API every AI agent can call

Turn long video into ready-to-post clips, captions, reframes, and translations — from your code, or inside Claude, Cursor, and ChatGPT. One API, every agent.

## Built for AI agents

`# Submit a long-form video to create a new clipping project curl --request POST \  --url https://api.opus.pro/api/clip-projects \  --header 'Authorization: Bearer <API_KEY>' \  --header 'Content-Type: application/json' \  --data '{    "videoUrl": "https://www.youtube.com/watch?v=dvO5NsYovM4"  }'``// Submit a long-form video to create a new clipping project const options: RequestInit = {  method: 'POST',  headers: {    Authorization: 'Bearer <token>',    'Content-Type': 'application/json',  },  body: JSON.stringify({    videoUrl: 'https://www.youtube.com/watch?v=LXvv6CbGg8A',    conclusionActions: [{ type: 'EMAIL', notifyFailure: true, email: 'opusclip@opus.pro' }],    curationPref: {      range: { startSec: 28, endSec: 636 },      clipDurations: [[0, 90]],      topicKeywords: ['OpusClip'],      genre: 'Auto',      skipCurate: false,    },    importPref: { sourceLang: 'auto' },    brandTemplateId: 'preset-fancy-Karaoke',  }),}; fetch('https://api.opus.pro/api/clip-projects', options)  .then((res: Response) => res.json())  .then((res) => console.log(res))  .catch((err) => console.error(err));``# Submit a long-form video to create a new clipping project import requests url = "https://api.opus.pro/api/clip-projects" payload = {    "videoUrl": "https://www.youtube.com/watch?v=LXvv6CbGg8A",    "conclusionActions": [        {            "type": "EMAIL",            "notifyFailure": True,            "email": "opusclip@opus.pro"        }    ],    "curationPref": {        "range": {            "startSec": 28,            "endSec": 636        },        "clipDurations": [[0, 90]],        "topicKeywords": ["OpusClip"],        "genre": "Auto",        "skipCurate": False    },    "importPref": { "sourceLang": "auto" },    "brandTemplateId": "preset-fancy-Karaoke"}headers = {    "Authorization": "Bearer <token>",    "Content-Type": "application/json"} response = requests.post(url, json=payload, headers=headers) print(response.text)`
Works with

## What you can build with OpusClip API

## Auto caption & reframe

Auto-generate branded captions in multiple languages and reframe for any aspect ratio to maintain consistency across every platform.

### No.1

clipping tool

The No.1 Spanish-language TV network in the U.S. uses OpusClip API to automate captions and reframe at a fraction of the cost.

## Automate your editing workflow

Automatically ingest, clip, and export your videos through a fully integrated workflow.

### $100k+

saved

A developer cut over $100K in maintenance costs all by integrating the OpusClip API.

## Turn your footage into feeds

Clip and publish videos straight to your app’s feed, keeping it stocked with an always-on pipeline of fresh content.

### 1000+

videos daily

The No. 1 cable news network in the U.S. uses OpusClip API to power a TikTok-style feed in their mobile app, producing over 1,000+ videos daily.

## Scale distribution across channels and languages

Create videos that you can post faster across multiple channels to gain more views and revenue streams.

### 20+

languages

The No.1 science YouTuber with 70M+ subscribers uses OpusClip API to distribute content across multiple channels in over 20 languages.

## Clip live moments in real time

Automatically detect, clip and post key moments from your livestream instantly.

### 1.4M+

viewers

A top 20 streamer used OpusClip API to clip live highlights during an event that drew in 1.4M+ unique viewers.

## Who is OpusClip API for?

- ### Developers Build autonomous video pipelines
- ### Media companies Scale video creation, grab mindshare
- ### Professional creators Build your media network
- ### Agencies Boost output without extra hires

[Talk to sales](/contact)

## Why choose OpusClip API?

- ### The #1 video editing API for scaling videos With our API, you can integrate OpusClip with within your existing workflow.
- ### Trusted world wide Trusted by the world's top businesses and developers.
- ### SOC 2 Type II Enterprise-grade security with SOC 2 Type II compliance.

## Built for agents and the developers

- ### OpusClip MCP for AI Agents Video clipping and editing MCP
- ### 6+ official SDKs Node, Python, Go, PHP, Ruby, Java
- ### Public OpenAPI + llms.txt built to be read by agents
- ### Self-serve free tier start building without a sales call