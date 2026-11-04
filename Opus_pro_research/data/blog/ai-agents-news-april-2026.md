---
url: https://www.opus.pro/blog/ai-agents-news-april-2026
title: "AI Agents News: April 2026 Roundup of Launches, Funding,... - OpusClip Blog"
description: "April 2026 was the month \"AI agents\" stopped being a slide in a keynote and became a line item on enterprise procurement decks. Every major lab shipped\u2026"
category: blog
word_count: 2654
---

# AI Agents News: April 2026 Roundup of Launches, Funding, and What's Actually Shipping

*April 2026 was the month "AI agents" stopped being a slide in a keynote and became a line item on enterprise procurement decks. Every major lab shipped something. Cognition is reportedly raising at a $25B valuation. Claude Opus 4.7 is leading SWE-bench. And the Linux Foundation now governs the two protocols — MCP and A2A — that the entire stack runs on. Here's what actually happened, and what's working in production.*

## TL;DR — the six stories that mattered this month

1. **Big-lab harness wars went mainstream.** OpenAI shipped a model-native harness as an Agents SDK update (15 April), Anthropic launched **Managed Agents** in public beta at **$0.08 per session-hour** (8 April), Google rebranded Vertex AI to the **Gemini Enterprise Agent Platform** at Cloud Next 2026, and Microsoft shipped its **Agent Governance Toolkit** as an open-source seven-package release. Same product category, four very different pricing models.
2. **Cognition is raising hundreds of millions at a reported $25B valuation** — up from $10.2B last September. Devin's ARR reportedly grew from $1M (Sept 2024) to $73M (June 2025). Replit closed at a **$9B valuation** in January, roughly tripling from its prior round. Coding agents are by far the most-funded vertical, with **>$3B raised** across Cognition, Poolside, Replit, Magic, Augment, Codeium, Factory, and StackBlitz combined.
3. **Claude Opus 4.7 leads SWE-bench Verified at 87.6%.** Claude Sonnet 4.5 leads the Princeton HAL GAIA leaderboard at 74.6%, with Anthropic models holding the top six spots. But UC Berkeley researchers showed **eight major agent benchmarks** — including SWE-bench, GAIA, OSWorld, WebArena, Terminal-Bench — can be gamed to near-perfect scores without solving a single task. Treat leaderboards accordingly.
4. **MCP crossed 97 million monthly SDK downloads.** Every major lab now natively supports it. OpenAI **deprecated its Assistants API** in favor of MCP earlier this year. A2A hit v1.0 with gRPC, signed Agent Cards, and multi-tenancy. Both protocols are now governed by the Linux Foundation's **Agentic AI Foundation (AAIF)**, founded December 2025 by OpenAI, Anthropic, Google, Microsoft, AWS, and Block.
5. **Salesforce Agentforce reports 8,000+ customers** with new Flex Credits pricing at **$0.10 per action**. ServiceNow took the #1 spot in the 2025 Gartner Critical Capabilities for Building and Managing AI Agents. Microsoft launched Copilot Studio's **Agent 365 control plane** for centralized management.
6. **The hype-vs-reality gap is real and getting worse.** Composio's 2025 AI Agent Report: 97% of executives say they've deployed agents, but **only 12% of initiatives reach production at scale**. McKinsey says **only 10% of organizations** have scaled agents within any single function. **Gartner predicts 40%+ of agentic AI projects will be scrapped by 2027.**

If you build, buy, or sell AI agents, the rest of this is the version with receipts.

## 1. The launch firehose: OpenAI, Anthropic, Google, Microsoft, Meta

April 2026 set a record for major-lab agent launches in a single month. The shape of the market is now clear: every frontier lab agrees the **harness is the product**. They disagree, vehemently, on how to charge for it.

### OpenAI

On **15 April**, OpenAI shipped a major update to its open-source Agents SDK — a model-native harness with configurable memory, sandbox-aware orchestration, and Codex-like filesystem tools. Developers can plug in their own sandbox or use built-in support for Blaxel, Cloudflare, Daytona, E2B, Modal, Runloop, and Vercel. Critically, the SDK works with any Chat Completions-compatible endpoint, making 100+ third-party and open-source models first-class citizens. There is no first-party runtime fee beyond standard API and tool charges.

A week later, on **22 April**, OpenAI launched **ChatGPT Workspace Agents** — shared agents a team builds once and uses together inside ChatGPT or Slack, refined through conversational corrections and persistent memory. This is OpenAI's direct shot at Microsoft Copilot and Salesforce Agentforce in the team-collaboration tier.

OpenAI's **Operator** (powered by the Computer-Using Agent / CUA model) continues to operate as the "agent that uses a computer" surface area, separate from the Agents SDK developer stack.

### Anthropic

On **8 April**, Anthropic launched **Managed Agents** in public beta at **$0.08 per session-hour** under the `managed-agents-2026-04-01` API header. Memory for Managed Agents is now in public beta as well. Anthropic also previewed:

- The **`ant` CLI**, a command-line client for the Claude API with native Claude Code integration and YAML versioning of API resources.
- **Ultraplan**, an early-preview workflow that lets you draft a plan in the cloud from your CLI, edit it in a web editor, and run it remotely or pull it back local.
- The **Claude Agent SDK** (the renamed Claude Code SDK) continues to expose the same agent loop, tools, and context management that power Claude Code, in Python and TypeScript.

Anthropic is reportedly testing an internal frontier model dubbed **"Claude Mythos,"** described as a step-change in capabilities. It may land in late April or Q2 2026.

At **Cloud Next 2026**, Google unified its agent stack. Vertex AI was renamed the **Gemini Enterprise Agent Platform**, absorbing Agentspace into a single Gemini Enterprise product. Google is leaning hard into A2A as its differentiator — the protocol it co-developed and donated to the Linux Foundation — and pairing it with a full-stack pitch that runs from TPUs through Workspace Studio to the agent layer.

### Microsoft

Microsoft did two notable things this month:

1. Shipped the **Agent Governance Toolkit** — a seven-package open-source system for governing autonomous agents, free on GitHub and PyPI. It pairs with Copilot Studio's **Agent 365 control plane** for enterprise-wide agent inventory, policy, and audit.
2. Added a **Critique** feature that lets multiple models — OpenAI's GPT, Anthropic's Claude, and Microsoft's own — collaborate inside a single Copilot workflow, with one model generating responses and another reviewing them for accuracy.

Copilot Studio also added **computer-use** capabilities and tighter integration with the Employee Self-Service Agent.

### Meta

Mark Zuckerberg announced **Llama 5** on **8 April** at a pre-LlamaCon event. The headline claims: System-2 reasoning, up to **5 million-token** context windows, and "specifically optimized" for agentic workloads. **LlamaCon on 29 April** is expected to ship the agent-platform pieces — runtime, tool integrations, and a Meta-hosted agent surface that competes with OpenAI's Operator and Anthropic's Managed Agents. The earlier-rumored "Avocado" LLM and "Mango" multimodal model now appear to have been folded into the Llama 5 release.

## 2. Funding: coding agents are eating the venture stack

The capital markets answered the agent thesis with checks. The headline numbers from this month and Q1 2026:

- **Cognition** (creator of Devin) is in talks to raise **hundreds of millions at a reported $25 billion valuation** — up from $10.2 billion in its September 2025 round (which itself was led by Founders Fund with Lux Capital, 8VC, Elad Gil, Definition Capital, and Swish Ventures). Devin's ARR reportedly went from **$1M in September 2024 to $73M in June 2025**.
- **Replit** closed a round at a **$9 billion valuation** in January 2026, up from $3B last year. The product driving it is **Replit Agent 3**, marketed as 10x more autonomous than Agent 2 and capable of testing/fixing code and building custom workflow agents.
- **Aggregate coding-agent funding crossed $3B** across Cognition, Poolside, Replit, Magic, Augment, Codeium, Factory, and StackBlitz — making software development the single highest-funded agentic AI vertical.

The thesis is clear: investors believe the first AI category to clear the production-vs-pilot bar is coding, and they are racing to back the names that already have ARR to show for it.

What is *not* yet showing up at this valuation tier: general-purpose computer-use agents (Operator-style), browser-automation pure-plays, and most "horizontal" agent platforms targeting knowledge work outside of code. Those are getting funded — just not at the multiples the coding-agent cohort commands.

## 3. Enterprise: Agentforce, ServiceNow, and the Microsoft middle

The enterprise picture in April 2026 is a three-way race plus a long tail.

**Salesforce Agentforce** reports **8,000+ customers** and rolled out a **Flex Credits** pricing model at **$0.10 per action**. The pitch: CRM-native agents purpose-built for service automation, sales acceleration, and customer-facing workflows that already live in Salesforce data.

**ServiceNow** took **#1 in the 2025 Gartner Critical Capabilities report** for Building and Managing AI Agents. Its differentiation is operational depth — AI Agent Orchestrator and AI Control Tower, plus thousands of pre-built agents for ITSM, HR, and customer service. ServiceNow's enterprise customers cite the deepest ITSM/operations integration as the reason they're standardizing here over Salesforce or Microsoft.

**Microsoft Copilot Studio** is the platform play. **Agent 365** centralizes governance across the agent estate; the Employee Self-Service Agent has emerged as the highest-volume use case; GPT-5 and Claude integration via the Critique feature lets customers route within a single workflow.

The market is still growing fast. Multiple analyst forecasts put the agent platform market at roughly **$7.84B in 2025 → $52.62B by 2030 (\~46% CAGR)**, and enterprise adoption is reportedly growing at \~41% annually. But — see Section 5 — adoption is not the same as production.

## 4. Capability progress: benchmarks, and why you should distrust them

Anthropic owned the leaderboards this month:

- **SWE-bench Verified:** Claude Opus 4.7 leads at **87.6% resolve rate**.
- **GAIA (Princeton HAL):** Claude Sonnet 4.5 leads at **74.6%**, with Anthropic models holding the top six positions.
- **OSWorld** continues to be the hardest of the three majors — GUI agents must control a desktop OS via mouse and keyboard given only a screenshot and a natural-language instruction. Top scores remain well below the SWE-bench numbers, and the gap between code-agent capability and computer-use agent capability is still the widest in the field.

Then the asterisk: **UC Berkeley's Center for Responsible Decentralized Intelligence published research showing every one of eight prominent agent benchmarks** — SWE-bench, WebArena, OSWorld, GAIA, Terminal-Bench, FieldWorkArena, CAR-bench, and one more — can be exploited to achieve near-perfect scores without solving any tasks. The exploits ranged from leaking ground-truth answers via tool calls to timing attacks on evaluators.

Practical reading: **published benchmark numbers are at most a coarse signal**, and the gap between leaderboard performance and customer-deployment performance has not narrowed this year. If you're evaluating an agent vendor, ask for held-out evals on your data, not screenshots of public leaderboards.

## 5. What's actually working in production?

This is the section you'd expect a vendor blog to skip. Don't.

The data on agentic deployment, as of April 2026:

- Composio's 2025 AI Agent Report: **97% of executives** report deploying agents in the last year — but **only 12% of initiatives** reach production at scale.
- McKinsey: **only 10% of organizations** report scaling agents within any single function. **23% of enterprises** are scaling overall; **39% are stuck in experimentation**.
- Gartner's call: **>40% of agentic AI projects will be scrapped by 2027**, primarily for operationalization failures, not model capability.
- **Only 14.4% of organizations** push agents to production with full security or IT approval. The rest are shipping ungoverned.
- **84% of companies** have not redesigned jobs or workflows around agent capabilities.

So what *is* actually shipping?

**Coding agents are the clearest win.** Devin's ARR run rate, GitHub Copilot's penetration, Cursor and Claude Code adoption among engineers, and Replit Agent's revenue trajectory all point to a category that has crossed the production line. Coding has uniquely good ergonomics for agents — bounded environment (a repo, a CI suite), deterministic feedback (tests pass or don't), and tolerant users (engineers).

**Customer-service agents inside Salesforce, ServiceNow, and Zendesk** are the next-clearest production category — narrow domains, structured data, human-in-the-loop fallback. The wins here are real but unglamorous: deflection rate improvements in the 15–35% range, not the 10x productivity claims of the keynote era.

**Internal IT and HR self-service** (Microsoft's Employee Self-Service Agent, ServiceNow's HR agents) is the third category showing real adoption — again, narrow, structured, deflection-economics workflows.

**Where production deployment is still rare:** computer-use agents on knowledge workers' actual desktops, multi-step research agents replacing analyst work, "fully autonomous" sales or marketing operations. These are where the demos are best and the scrap rate is highest.

The overall pattern: agents work where the environment is bounded, the feedback is deterministic, and the user can correct mistakes cheaply. Everywhere else, the gap between demo and production is still the biggest unsolved problem in the category.

## 6. Protocols and infrastructure: MCP wins, A2A becomes a standard

The protocol story is the most under-covered piece of the agent stack, and it's the one with the most decided outcome.

**MCP (Model Context Protocol):**

- Originated at Anthropic; now adopted by **every major lab — Anthropic, OpenAI, Google, Microsoft, AWS**.
- **97 million monthly SDK downloads** (Python + TypeScript) as of February 2026.
- Native support in Claude, ChatGPT, Gemini, Cursor, VS Code, JetBrains IDEs.
- **OpenAI deprecated its Assistants API earlier in 2026 in favor of MCP**, ending the period of proprietary tool-integration approaches.

**A2A (Agent-to-Agent):**

- Originated at Google; **v1.0 shipped in early 2026** with gRPC, signed Agent Cards, and multi-tenancy.
- Designed for the orthogonal problem to MCP: MCP is *vertical* (agent talks to tools); A2A is *horizontal* (agent talks to agents).
- Picking up real adoption inside enterprise multi-agent systems where Salesforce, ServiceNow, and Microsoft agents need to coordinate.

**Governance:**

- The **Linux Foundation Agentic AI Foundation (AAIF)**, launched December 2025, now governs both protocols. Founding members: **OpenAI, Anthropic, Google, Microsoft, AWS, and Block**.
- This is an unusually decisive standardization outcome for a market this young. Compare to JavaScript framework wars or container orchestration in 2017 — the agent protocol layer settled in a year.

If you are building a custom agent platform in 2026 without MCP support, you are off the standard track. If you are building one without A2A on the roadmap, you are betting against horizontal interop.

## 7. Things to watch over the next 60 days

A short list of what to keep an eye on between now and the end of June:

- **LlamaCon (29 April).** Meta's agent-platform shape will become clear. If Llama 5 ships a credible managed-agent surface and an open-source harness story, the four-lab race becomes a five-lab race.
- **Claude "Mythos" reveal.** If Anthropic ships the rumored frontier model, expect new SWE-bench and GAIA leaderboard movement and a likely repricing of Managed Agents.
- **Cognition's funding close.** A $25B valuation lands or it doesn't. Either outcome resets the coding-agent comp set.
- **Q1 2026 earnings calls.** Salesforce, Microsoft, ServiceNow, and Adobe will all be pressed on agent revenue specifically. Watch for the first time any of them break out agent-attributed ARR.
- **Benchmark response.** Berkeley's exploit paper is putting pressure on SWE-bench, GAIA, and OSWorld maintainers to ship hardened versions. Expect at least one to release a contamination-resistant 2026 update.
- **Computer-use agent adoption.** OSWorld scores need to clear \~70% before this category has a credible production story. Watch for any vendor that crosses that bar with a non-gamed score.

## Bottom line

April 2026 was the month the AI agent narrative split in two. On one side: a record-setting cycle of launches from every frontier lab, a $25B valuation rumor for the leading coding-agent startup, leaderboard scores in the high 80s, and a protocol stack that just got handed to the Linux Foundation. On the other side: a 12% production rate, a 10% scaling rate, and Gartner forecasting that almost half of these projects will be scrapped before they ever ship.

The right read isn't "agents are overhyped" or "agents are the future." Both are partly true. The accurate read is that **agents work in narrow, bounded, feedback-rich environments today** — coding, customer service, IT self-service — and that the gap between those environments and "agent does my job" is still the central unsolved problem of the field.

If you're a builder: ship into bounded domains, instrument production heavily, and pick MCP and A2A. If you're a buyer: ignore leaderboards, demand held-out evals, and budget for the governance work nobody's putting on the keynote slide.

The hype is loud. The production cohort is still small. That's the real April 2026 news.