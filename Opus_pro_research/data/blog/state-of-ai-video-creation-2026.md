---
url: https://www.opus.pro/blog/state-of-ai-video-creation-2026
title: "AI Video Statistics 2026: 36K Videos Analyzed | Agent Opus - OpusClip Blog"
description: "We analyzed a sample of 36,388 AI videos made by 11,416 creators. Here's what the data reveals about length, avatars, captions, language use, and format."
category: blog
word_count: 1823
---

# What 36,388 AI Videos Reveal About How People Actually Use AI Video Generators

*Most "state of AI video" numbers come from surveys, vendor benchmarks, or synthetic tests. What's rare is actual production data — real videos that real users built and rendered. That's what this is.*

We analyzed a **sample of 36,388 Agent Opus projects** created by 11,416 distinct users between January 14 and February 23, 2026 — a six-week production window. All metrics are aggregate and anonymized. This is a sample, not our full dataset — a recent, clean slice of production behavior.

## TL;DR: The 2026 Production Snapshot

- **Median video length:** 43 seconds. p90: 103 seconds. AI video is vertical-social shaped, not YouTube shaped.
- **Avatar adoption:** 24.1%. **Voice cloning:** 14.2%. **Burned-in captions:** 9.7%.
- **97.5% of projects feed in images.** 46.9% pull from YouTube. Tweets are essentially irrelevant (\<0.1%).
- The median project uses **14 images, 4 YouTube clips, and 5 stock assets**.
- **119 languages** captured across the sample. English is 57% — the long tail is bigger than people assume.
- "canon," "pastel," and "fuji" are the three most-used visual styles — cinematic, warm, documentary. Not what the "AI slop" discourse would predict.

The headline finding: most people who generate AI video are not trying to make a shorter YouTube video. They're trying to make a better TikTok.

## Finding 1: AI Video Is Vertical-Social Shaped

The single most telling number in the dataset: the median video is **43 seconds long**. The 90th percentile is 103 seconds. Only 4.7% of projects exceed 120 seconds.

| Length bucket | Share | 
|---|---|
| 0–15s | 2.6% | 
| 15–30s | 14.1% | 
| **30–60s** | **54.2%** | 
| 60–90s | 14.8% | 
| 90–120s | 9.6% | 
| 120s+ | 4.7% | 

More than half of all projects land in the 30–60 second bucket. That's the native TikTok / Reels / Shorts sweet spot — not the 5–10 minute YouTube length people often associate with "AI video."

The mental model of AI video as "automated YouTube" is wrong at the production layer. People are using these tools to make **social-shaped content** — the same length, roughly the same cadence, as the short-form videos we've all been trained to consume.

## Finding 2: What People Actually Feed In

| Asset type | Projects using | Share | Avg per project (when used) | 
|---|---|---|---|
| **Images** | 35,487 | **97.5%** | 14.1 | 
| Stock assets | 28,158 | 77.4% | 5.1 | 
| YouTube clips | 17,048 | 46.9% | 4.1 | 
| Articles (URL ingest) | 3,462 | 9.5% | 1.5 | 
| Tweets | 23 | \<0.1% | 1.1 | 

Two surprises.

**The image is the atom of AI video.** Almost every project starts with images. The median creator uploads \~14 of them. Any AI-video tool without a first-class image ingestion flow is operating against the grain.

**Tweets are dead as a video source.** In 36K projects, only 23 pulled from tweets. Platform policy plus workflow friction has killed this as a primary behavior. Screenshot-and-upload still wins over URL-ingest for X content.

## Finding 3: Avatars Beat Captions (And That's Weird)

Adoption of the three signature AI-video features in our sample:

- **Avatar usage:** 24.1%
- **Voice cloning:** 14.2%
- **Burned-in captions:** 9.7%

Captions are the least-adopted of the three. This is genuinely surprising, because TikTok and Reels — the platforms these videos are made for — treat captions as table stakes. The creator-economy consensus is that silent autoplay demands captioned video.

A few interpretations:

1. **Platform-native captions cover it.** TikTok and Reels auto-generate captions. Creators may be relying on that layer.
2. **AI voices are crisp.** Unlike lo-fi creator audio, AI voiceover is clean — reducing the perceived need for captions.
3. **The workflow is clunky enough to skip.** If enabling captions adds friction, users route around.

We suspect (3). Caption adoption is a feature-discovery and feature-friction problem, not a user-preference problem. For any AI-video tool looking for a durable wedge, making burned-in captions frictionless — auto-on, style-templated — would hit a real creator pain point.

## Finding 4: Feature Adoption Varies Massively by Niche

| Niche | Avatar % | Voice-clone % | Caption % | Avg length | 
|---|---|---|---|---|
| Lifestyle & Aesthetic | **38.7%** | 6.7% | 6.7% | 39.5s | 
| Tech & Innovation | 34.8% | **30.7%** | 10.4% | 58.7s | 
| Finance & Commerce | 25.9% | 16.8% | 9.8% | 57.7s | 
| Trends & Commentary | 19.8% | 9.0% | 8.3% | 49.1s | 
| Narrative & Documentary | 15.8% | 15.6% | **13.9%** | 60.8s | 

Several patterns worth calling out:

- **Lifestyle & Aesthetic is the avatar category.** Nearly 4 in 10 projects use an avatar — the "personal-brand-without-being-on-camera" use case.
- **Tech & Innovation leans hard on voice cloning.** Developer and tech-explainer creators want their own voice at scale.
- **Narrative & Documentary is the only category where captions cross 10%.** Long-form narrative benefits from readability.
- **Narrative is the longest bucket** (60.8s avg). Lifestyle is the shortest (39.5s). Longer videos correlate with more editorial intent.

## Finding 5: The "AI Slop" Aesthetic Is a Minority

| Visual style | Projects | Share | Avatar use | 
|---|---|---|---|
| canon | 6,309 | **17.3%** | 24.4% | 
| pastel | 6,139 | 16.9% | 19.7% | 
| fuji | 5,951 | 16.4% | 27.1% | 
| eggshell | 4,741 | 13.0% | 32.6% | 
| parchment | 4,273 | 11.7% | 18.6% | 
| graffiti | 3,704 | 10.2% | 19.4% | 
| center | 2,651 | 7.3% | 15.5% | 
| ugc | 1,595 | 4.4% | **43.3%** | 

If you read the AI-slop discourse, you'd expect a sea of over-saturated synthetic imagery. What the data actually shows is **cinematic, warm, documentary** as the dominant modes. `canon`, `pastel`, `fuji`, and `eggshell` together are nearly two-thirds of the sample.

`ugc` (mimicking amateur phone-shot creator video) is only 4.4% — smaller than most people assume. When it's used, it's the style with the highest avatar adoption (43.3%). That fits: if you're imitating talking-head creator content, you want a face.

The visual output people are generating trends *toward* realism and warmth, not away from it.

## Finding 6: Language Diversity Is the Biggest Story Nobody's Telling

| Language | Projects | Share | 
|---|---|---|
| en-US | 11,370 | 45.0% | 
| en-GB | 3,070 | 12.2% | 
| nl-NL | 2,200 | **8.7%** | 
| fr-FR | 979 | 3.9% | 
| de-DE | 966 | 3.8% | 
| ru-RU | 815 | 3.2% | 
| it-IT | 507 | 2.0% | 
| uk-UA | 477 | 1.9% | 
| zh-CN | 466 | 1.8% | 

Of the top 15 languages, nine are non-English. Two things jump out.

**Dutch punches way above its weight.** The Netherlands has \~17 million native speakers — well under 1% of the global population. It's 8.7% of projects in our sample. Dutch creators are AI-video early adopters at a rate wildly out of proportion to their population.

**Ukrainian is present at 1.9%.** A country at war, with creators still making content. This is a cultural note that deserves more attention than "AI is automating English slop."

## Finding 7: The Use-Case Matrix

Crossing niche with use case reveals what people are actually making. The dominant use case across every niche is **educational explainer**:

- Narrative & Documentary → Educational Explainers: 5,545 projects (1,797 users)
- Finance & Commerce → Educational Explainers: 2,300 projects (1,139 users)
- Tech & Innovation → Educational Explainers: 1,890 projects (815 users)
- Lifestyle & Aesthetic → Educational Explainers: 1,428 projects (623 users)

Not entertainment, not performance marketing, not viral stunts — just "explain something." AI video is most useful when an idea needs to be *transmitted* quickly, not when it needs to be *felt*.

## What This Implies for the Industry

1. **The format is short-form social, not long-form YouTube.** Building AI-video tooling for "automated podcasts" or "long-form summaries" is swimming against gravity.
2. **Image input is non-negotiable.** 97.5% of projects use images. Any AI-video tool without great image-ingest is operating against the grain.
3. **Captions are under-adopted.** Frictionless captions — auto-on, high-contrast, style-templated — would hit a real gap.
4. **Aesthetic is warmer than the discourse implies.** AI slop is a real visual mode but it's not where production volume is concentrated.
5. **Language is a moat.** 119 languages captured. English-only tools cede huge share.
6. **Educational explainer is the killer use case.** Build for explainers first, everything else second.

## What This Data Can't Tell You

- **Post-publish performance.** We have production data, not distribution data. A project that rendered well might never get shared.
- **Pro vs hobbyist segmentation.** We kept the analysis fully anonymized — no plan-tier cuts.
- **Generalization to other AI-video tools.** This is one platform, one window. Treat our numbers as "what Agent Opus production looks like in early 2026" — not "what AI video creation looks like everywhere."

## Methodology

Based on a **sample of 36,388 Agent Opus projects** from 11,416 distinct users created between January 14 and February 23, 2026 — a six-week production window. All queries were run against production BigQuery tables; all metrics are aggregate-level, anonymized, and exclude PII. Scene and shot counts come from `dwd_storyboard_shot`. Project-level metrics come from `dim_derived_project` (production environment only). This is a subset of Agent Opus's project data — not the full dataset. Different windows, different product states, and different user cohorts may produce materially different numbers.

## Start Creating

Agent Opus turns scripts, images, and source material into cinematic AI video — storyboard, assets, avatars, voice, and render in one end-to-end flow. [Try Agent Opus →](https://agentopus.ai)

## Frequently Asked Questions

### What's the average length of an AI-generated video in 2026?

In our sample of 36,388 Agent Opus projects, the median video length is 43 seconds and the 90th percentile is 103 seconds. Only 4.7% of projects exceed 120 seconds. AI video production is concentrated in the short-form social format (TikTok, Reels, Shorts), not long-form YouTube.

### How many people use AI avatars in their videos?

In our sample, 24.1% of projects use an avatar. Adoption varies dramatically by niche: Lifestyle & Aesthetic creators use avatars 38.7% of the time, while Narrative & Documentary creators use them just 15.8% of the time.

### What inputs do people use to create AI videos?

97.5% of projects feed in images (median 14 per project). 77.4% use stock assets. 46.9% pull from YouTube clips. Tweets and article URLs are minority inputs. If you're building an AI-video tool, a strong image-ingest flow is non-negotiable.

### How many languages are represented in AI video production?

Our sample captured 119 languages. English is 57% of projects (en-US and en-GB combined). Dutch (nl-NL) is 8.7% — dramatically outsized relative to the Netherlands' population. Ukrainian creators represent 1.9% of projects despite the ongoing war.