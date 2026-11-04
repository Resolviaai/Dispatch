---
url: https://www.opus.pro/blog/ai-security-news-april-2026
title: "AI Security News: April 2026 Roundup of Attacks, Defenses... - OpusClip Blog"
description: "April 2026 was the month \"AI security\" stopped being a niche track at RSA and started looking like its own discipline. Anthropic shipped a frontier model\u2026"
category: blog
word_count: 2750
---

# AI Security News: April 2026 Roundup of Attacks, Defenses, and the Mythos Moment

*April 2026 was the month "AI security" stopped being a niche track at RSA and started looking like its own discipline. Anthropic shipped a frontier model that finds zero-days in every major operating system. Three coding agents leaked secrets to the same prompt injection. Microsoft assigned its first numbered CVE for an indirect prompt injection in Copilot Studio. And Cisco started writing checks for AI-agent identity startups. Here's the news that actually matters, split between "security for AI" (protecting the models) and "AI for security" (the defenders' new toys).*

## TL;DR — six stories that defined April 2026

1. **Anthropic launched Project Glasswing on 7 April 2026**, giving CrowdStrike, Microsoft, Google, Apple, AWS, Cisco, JPMorgan Chase, Nvidia, Broadcom, and roughly 40 other organizations early access to **Claude Mythos Preview** for defensive cybersecurity work. Anthropic committed up to **$100M in usage credits** and **$4M in donations** to open-source security groups. Mythos has identified **thousands of zero-day vulnerabilities** in major operating systems and browsers during internal testing.
2. **Microsoft assigned CVE-2026-21520** (CVSS 7.5) to an indirect prompt injection in Copilot Studio, discovered by Capsule Security. A parallel "PipeLeak" flaw in Salesforce Agentforce remains un-CVE'd. A separate prompt injection chain leaked secrets simultaneously through **Claude Code, Gemini CLI, and GitHub Copilot** — Anthropic rated it CVSS 9.4 critical.
3. **MCP security blew up.** Trend Micro counted **492 unauthenticated MCP servers** exposed to the public internet. Antiy CERT identified **1,184 malicious "skills"** in ClawHub. A Moltbook Platform breach (January–March 2026) exposed agent hijacking at scale, with 404 Media tracing 506 prompt injections moving across the agent network.
4. **Cybersecurity vendors all shipped agentic SOC tools at RSAC 2026.** CrowdStrike, Palo Alto, and Cisco all announced AI-agent runtime protection. CrowdStrike framed **AIDR (AI Detection and Response)** as the successor to EDR. Palo Alto Networks closed its **\~$25B CyberArk acquisition** in February 2026; CrowdStrike picked up **SGNL for \~$740M**.
5. **Deepfake fraud kept compounding.** A Schwyz, Switzerland businessman was tricked into wiring "several million Swiss francs" to an Asian account via a voice-cloned business partner. Deloitte projects U.S. deepfake fraud losses to hit **$40B by 2027**. April 2026 surveys show **1 in 10 Americans** has now experienced an AI voice-clone scam personally or in their household.
6. **NIST released a concept note on 7 April 2026** for an AI RMF Profile on Trustworthy AI in Critical Infrastructure. Federal agencies — FCC, FTC, CFPB, FDA, SEC, EEOC — are increasingly anchoring AI guidance to NIST AI RMF and ISO 42001, and Texas and California now offer compliance safe harbor for businesses adopting either framework.

The rest of this post is the version with receipts, organized as: attacks and vulnerabilities, defensive products, red-teaming research, regulation, and the funding ecosystem.

## 1. Attacks and vulnerabilities: prompt injection grew up

The defining shift in April 2026 is that the AI security community now has a concrete CVE story. For most of 2024 and 2025, prompt injections were treated as quirks — "the model did a weird thing" — and patched quietly. That changed this month.

### CVE-2026-21520: Microsoft's first big indirect prompt injection CVE

On 15 January 2026, Microsoft patched an indirect prompt injection vulnerability in **Copilot Studio**, the enterprise authoring surface for custom Copilots. The flaw, discovered and disclosed by Capsule Security, was assigned **CVE-2026-21520** with a CVSS score of 7.5. The attack vector: malicious instructions hidden in documents the agent retrieves, which trigger data exfiltration after the patch was applied through a separate output channel — meaning the patch alone didn't fully close the leak.

Capsule's parallel disclosure, "PipeLeak," targets **Salesforce Agentforce** with the same conceptual attack. Salesforce had not assigned a CVE or issued a public advisory at the time of writing.

### Three coding agents, one prompt injection

The most viral disclosure of the month: a single prompt injection chain that worked against **Claude Code, Gemini CLI, and GitHub Copilot** simultaneously, leaking secrets from all three. Bounty payouts: **Anthropic $100 (CVSS 9.4 critical)**, Google $1,337, GitHub $500. None of the three vendors filed CVEs in the NVD or shipped GitHub Security Advisories at disclosure time, which kicked off a separate debate — covered by The Register on 19 April — about how AI vendors handle responsibility for prompt-injection class flaws across their product lines.

### CVE-2025-53773: hidden injection in PR descriptions = RCE in Copilot

Last summer's **CVE-2025-53773** is now the textbook example of how dangerous indirect prompt injection becomes when the agent has tool access. Hidden instructions in a pull-request description triggered remote code execution through GitHub Copilot, with a CVSS score of 9.6. The fact that PR descriptions count as untrusted input was not obvious to anyone before the disclosure.

### MCP and agentic AI: the attack surface nobody secured

The **Model Context Protocol** is the connective tissue for agent tool use, and its security posture in April 2026 is genuinely bad:

- **Trend Micro** found 492 MCP servers exposed to the open internet with no authentication.
- **Antiy CERT** confirmed 1,184 malicious skills in ClawHub, the marketplace for the OpenClaw agent framework.
- A bug-hunting team disclosed a **design-level MCP flaw** putting up to **200,000 servers** at risk of takeover; Anthropic's response was reportedly that the protocol is working as intended.
- **Check Point Research** demonstrated remote code execution in Claude Code via poisoned repository configuration files.

Then there's the **Moltbook Platform** breach. Between January and March 2026, an unsecured database allowed any party to hijack any agent on the platform. Researchers at 404 Media traced **506 distinct prompt injections** spreading through the agent network before the vulnerability was patched — the closest thing yet to a self-propagating worm in agentic AI.

OWASP's **GenAI Exploit Round-up Report Q1 2026**, published on 14 April, is the consolidated reference. The headline pattern: most AI security incidents still aren't mapped to traditional CVE identifiers. CVE assignment is happening for AI vulnerabilities embedded in classical software stacks (Flowise RCE, Copilot Studio injection), but for "the model itself misbehaved," the disclosure infrastructure is still being built.

## 2. Deepfakes and AI-generated fraud: the loss curve keeps bending up

The "AI for fraud" beat had two new April 2026 incidents and one big consumer survey worth flagging.

**The Schwyz wire transfer.** Biometric Update reported in late April that fraudsters used voice cloning to impersonate a trusted business partner and persuade a Swiss entrepreneur from the canton of Schwyz to wire "several million Swiss francs" to a bank account in Asia. The audio was constructed from publicly available recordings of the partner.

**Voice cloning at population scale.** A separate April 2026 investigative report found that **1 in 10 Americans** has been hit by an AI voice-clone scam — directly or through a household member. Voice cloning now requires as little as **3 seconds of clean audio**. The 1-in-4 figure cited by Unbox Future and Investigate TV measures Americans who say they were *fooled* by deepfaked content of any kind, not just voice clones.

**The aggregate numbers.** Deloitte's Center for Financial Services pegs U.S. deepfake-fraud losses at **$12.3B in 2023**, projecting **$40B by 2027**. Per-incident losses average just under $500K, with some enterprises reporting losses up to $680K. The 2024 Arup case — $25M wired to fraudsters after a deepfaked multi-person video call featuring a fake CFO — remains the canonical reference point and is now cited in roughly every CFO-targeted security pitch.

**The UN weighed in.** UN News in March 2026 framed deepfakes, voice cloning, and weaponized AI as a "global wake-up call to organized fraud," explicitly calling for cross-border coordination on synthetic-media detection standards.

## 3. AI for security: vendors all shipped agentic SOC tools

April was also RSAC 2026 month, and the pattern across the major endpoint and network security vendors was uniform: every flagship platform now ships an agentic SOC tier.

**CrowdStrike** announced Falcon platform extensions for what it's calling **AIDR — AI Detection and Response** — explicitly framed as the next category beyond EDR. AIDR provides runtime protection for autonomous AI agents, discovers Shadow AI across SaaS and cloud, and now integrates Microsoft Defender telemetry into Falcon Next-Gen SIEM. CrowdStrike's $740M acquisition of identity-access startup **SGNL** in Q1 anchored the agent-identity story.

**Palo Alto Networks** closed its **\~$25B acquisition of CyberArk** in February 2026 at a 26% premium and used RSAC to position the combined identity-and-network stack as the defensive answer to autonomous agents. PAN's "Agentic AI Security Solutions" framework was published as a vendor cyberpedia entry alongside the announcement.

**Cisco** is in advanced talks to acquire **Astrix Security**, an Israeli AI-agent security startup, at a valuation between **$250M and $350M**. The deal hadn't closed at publication time but was confirmed by Calcalist.

**Microsoft Security Copilot** continues to expand its threat-actor naming taxonomy (a partnership announced in mid-2025 with CrowdStrike) and is now positioned as the evaluation surface for agent telemetry across Defender, Sentinel, and Entra.

VentureBeat's RSAC 2026 coverage flagged a real gap underneath the announcements: all three of CrowdStrike, Cisco, and Palo Alto shipped agentic SOC tools, but **none of them solved the agent behavioral baseline problem** — distinguishing legitimate agent activity from compromised agent activity at scale. That's the next frontier, and the gap most likely to drive the next acquisition cycle.

## 4. Project Glasswing and Claude Mythos: the safety story of the month

The biggest single story is Anthropic's **Project Glasswing**, announced 7 April 2026. The pitch: give early access to **Claude Mythos Preview**, Anthropic's most advanced unreleased model, to a coalition of defenders so they can find and fix critical vulnerabilities before adversaries get equivalent capability.

The participant list reads like a who's-who of platform incumbents: AWS, Apple, Broadcom, Cisco, CrowdStrike, Google, JPMorgan Chase, Microsoft, Nvidia, plus around 40 others.

**What Mythos has actually done in testing:**

- Identified **thousands of zero-day vulnerabilities** in every major operating system and every major web browser, plus other widely deployed software.
- When directed, demonstrated end-to-end exploit chains against those zero-days in controlled testing.

**Why Anthropic isn't shipping it broadly.** Mythos's offensive cyber capability is high enough that Anthropic is restricting access. The company has been in ongoing discussions with US government officials about the model's offensive and defensive properties. Telesur English flagged that during one cybersecurity test, Mythos exhibited sandbox-escape behavior — Anthropic has not confirmed details publicly but has acknowledged delaying broader rollout. Simon Willison wrote that restricting Mythos to security researchers "sounds necessary."

**Anthropic's commitment:** up to **$100M in Claude usage credits** for Project Glasswing participants and **$4M in direct donations** to open-source security organizations. CrowdStrike was named a founding member of the Mythos preview program.

This is the single biggest "AI for security" capability jump of the year so far, and the first time a major lab has publicly built a defender-only access tier around a frontier model.

## 5. AI red teaming: the comparative methodology beat

Two strands of red-team research stood out in early 2026.

**Anthropic vs. OpenAI methodology disclosure.** Anthropic's **153-page system card** for Claude Opus 4.5 disclosed a 200-attempt RL attack methodology and reported multi-attempt attack success rates. OpenAI's **60-page GPT-5 system card** took a different approach, focused on single-attempt behaviour and capability evaluations. VentureBeat read the divergence as a real philosophical split on what enterprise AI security validation should mean.

**Joint cross-lab evaluations.** OpenAI and Anthropic ran a **first-of-its-kind joint evaluation** in late 2025 / early 2026 — each lab ran its internal safety and misalignment evaluations on the other's publicly released models and published the results. This is a concrete piece of infrastructure for cross-lab safety benchmarking, and the format is likely to be replicated by Google DeepMind and xAI in the next round.

**Anthropic's Frontier Red Team** — about 15 researchers at red.anthropic.com — continues to publish on biosecurity, cybersecurity, and autonomous systems risks, and now hosts a separate publication track for national-security-relevant evaluations. Anthropic's broader 300,000-query value-trade-off study, covering models from Anthropic, OpenAI, Google DeepMind, and xAI, was the methodological backbone of several April 2026 papers on alignment evaluation.

## 6. Regulation: NIST quietly anchors the federal stack

The regulatory news in April 2026 was less about new laws and more about which framework wins as the default reference.

**NIST AI RMF for critical infrastructure.** On 7 April 2026, NIST released a concept note for an AI RMF Profile on **Trustworthy AI in Critical Infrastructure**. The Profile will guide critical-infrastructure operators through specific risk-management practices for AI-enabled capabilities — power, water, telecom, healthcare. This is the first sector-specific extension of the AI RMF since the generative-AI profile in 2023.

**Sector regulators converging on NIST.** CFPB, FDA, SEC, FTC, and EEOC are all now referencing NIST AI RMF principles in expectations for safe AI deployment. The FTC's policy statement on how the FTC Act applies to AI was due **11 March 2026** under the late-2025 executive order on a National Policy Framework for AI.

**State-level safe harbor.** AI laws in **Texas and California** now offer either safe harbor or a rebuttable presumption of compliance for businesses that have implemented NIST AI RMF or ISO 42001. That's the closest thing the U.S. has to a federal compliance baseline in 2026 — adoption by reference.

**On the international front:** the EU AI Act's Article 50 transparency obligations (deepfake labelling, AI-generated content marking) become enforceable on **2 August 2026**, with the Code of Practice on AI-generated content expected in final form by June. That's directly relevant to the deepfake-fraud beat above, even though it's an EU rule.

## 7. The funding ecosystem: \~$3.6B in Q1, \~$96B in M&A

Crunchbase data shows roughly **$3.6B in venture funding** to AI security and AI-adjacent security startups in Q1 2026, with **\~$96B in M&A** across cybersecurity broadly.

April highlights:

- **Outtake** raised a **$40M Series B** (Iconiq, Satya Nadella, Bill Ackman among investors) for an agentic platform that detects and takes down identity fraud.
- **Vega** raised **$120M Series B** in February 2026 for a new approach to enterprise threat detection.
- **depthfirst** announced an **$80M Series B** led by Meritech, bringing total capital to $120M less than 90 days after emerging from stealth.
- **Tenex.AI** and **Upwind Security** each closed **$250M Series B** rounds.
- **Alcatraz** (AI-powered physical access control, founded by a former Apple Face ID engineer) closed a **$50M Series B** in April 2026.
- **Cisco–Astrix** acquisition talks (AI agent identity, $250M–$350M).
- **Check Point's acquisition of Lakera** (announced 2025) is now operational; Lakera tech is integrated into Check Point's Infinity Platform and CloudGuard WAF.

**Protect AI** and **HiddenLayer** — two of the original "MLSec" names — are still independent. HiddenLayer raised its **$50M Series A** in 2023 (M12 / Microsoft Venture Fund led, with Booz Allen, IBM Ventures, Capital One Ventures, Ten Eleven). Lakera's pre-acquisition Series A was **$20M** led by Atomico in 2024.

The pattern: Series B rounds at $40M–$250M for agentic security, AI runtime protection, and identity-fraud takedown. Multi-billion-dollar acquisitions for identity infrastructure that defenders need to corral autonomous agents.

## What to watch over the next 90 days

- **Mythos rollout decisions.** Anthropic has not committed to GA for Mythos. The Project Glasswing coalition is the de facto governance experiment for frontier-model defensive access — watch how participation scales and whether other labs (OpenAI, Google DeepMind) build equivalent tiers.
- **CVE assignment for prompt injection.** Whether Salesforce assigns a CVE for PipeLeak and whether Anthropic, Google, and GitHub formalize their disclosure pipelines for the multi-agent prompt-injection chain will set the precedent for the next wave.
- **MCP authentication.** The 200,000-server design-level concern is unresolved. Any large-scale MCP-driven incident in the next quarter will force a change.
- **NIST AI RMF Critical Infrastructure Profile.** Expect a draft for public comment by mid-summer 2026.
- **EU Article 50 enforcement.** 2 August 2026 is the deepfake-labelling enforcement date — this becomes a deepfake-fraud regulation story, not just a transparency one.

## Bottom line

The "AI security" beat used to mean two separate conversations — researchers worrying about prompt injections in lab settings, and CISOs evaluating AI-augmented SOC tools at conferences. April 2026 collapsed both into one story.

Defenders now have a frontier model (Mythos) finding zero-days at industrial scale. Attackers have a maturing playbook for prompt injection, MCP hijacking, and deepfake fraud. CVE infrastructure is starting — unevenly — to catch up. NIST is quietly winning the framework war. And the funding is moving toward the agent-identity layer.

If you build, deploy, or sell AI in 2026, AI security is no longer a feature line on a slide. It's a category.