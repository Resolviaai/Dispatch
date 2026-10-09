---
name: mobile-design-system
description: Design, audit, and refactor mobile app and video studio interfaces according to the Dispatch Design System (4-layer elevation, 44px hitboxes, 3-5 tab bottom nav, concentric geometry, and tactile micro-physics).
---

# Mobile Design System Skill for Dispatch

This skill provides step-by-step instructions and auditing checklists for designing, evaluating, and refactoring user interfaces in the Dispatch personal content engine (both the Web Review Studio and the Native Android App).

---

## 1. When to Use This Skill
Use this skill whenever:
* Creating or refactoring mobile screens, video player layouts, or subtitle editing tools.
* Auditing UI components for anti-AI vibecoding violations.
* Reviewing bottom navigation bars, gesture touch targets, or responsive viewport adaptations.
* Aligning Tailwind CSS classes or Jetpack Compose theme tokens with `DESIGN_SYSTEM.md`.

---

## 2. The 6-Step Implementation Checklist

### Step 1: Layout Containment & Single-Direction Verification
* [ ] Does the mobile view (`isMobileFrame` or native phone) use a strictly **1-column vertical stack** or horizontal snap carousel?
* [ ] Are all elements contained within bounded cards (no full-bleed overflow without container bounds)?
* [ ] On desktop/wide mode, does the interface transition smoothly to a structured grid (e.g., video canvas on left, tools/inspector on right)?

### Step 2: Elevation Layer Mapping (The 4-Layer Architecture)
Verify that every component maps cleanly to the 4-layer stack:
* **Layer 0 (Canvas):** Root background `#161616` (`bg-studio`).
* **Layer 1 (Cards & Sheets):** Card background `#1C1C1C` (`bg-surface-100`) with border `#2E2E2E` (`border-border`).
* **Layer 2 (Recessed Wells & Inputs):** Recessed containers `#232323` (`bg-surface-200`) and slider tracks `#282828` (`bg-surface-300`).
* **Layer 3 (Hero Brand Accent):** Electric Creator Blue `#2563EB` (`bg-primary`, hover `#3B82F6`, ring `rgba(37,99,235,0.15)`).
* **Semantic Tokens:** Success `#22C55E`, Warning `#F59E0B`, Danger `#EF4444`.

### Step 3: Biomechanical Touch Target & Bottom Nav Audit
* [ ] Does every interactive button, icon, and chip have a minimum bounding box of **$44\text{px} \times 44\text{px}$** (`min-h-[44px] min-w-[44px]`)?
* [ ] Does the bottom navigation bar have between **3 and 5 tabs** (maximum 5)?
* [ ] Does the bottom deck maintain safe-area clearance above the $34\text{px}$ home indicator?
* [ ] Does the active tab show dual visual cues (bold colored text + filled icon + glowing indicator dot)?

### Step 4: Typographic Hierarchy & Numeric Alignment
* [ ] Are there **maximum 4 font sizes** ($18\text{px}, 14\text{px}, 12\text{px}, 10\text{px}$) and **2 font weights** on core operational views?
* [ ] Are all numbers, durations, timestamps, and metrics set in `font-mono tabular-nums`?
* [ ] Are numbers in tables and lists **right-aligned** by place value?
* [ ] Are non-breaking spaces used before units (`42&nbsp;s`, `18.4&nbsp;MB`) and typographic ellipsis `…` instead of `...`?

### Step 5: Geometric Math & Concentric Radii
* [ ] Are spacing, paddings, and margins divisible by 4 or 8 (the 8-pt grid)?
* [ ] Does nested container curvature satisfy $R_{\text{inner}} = R_{\text{outer}} - \text{Padding}$?

### Step 6: Anti-AI Vibecoding & Tactile Micro-Interactions
* [ ] Are native OS emojis completely absent? Are all icons standard SVG vectors (`lucide-react`) with $1.5\text{--}2.0\text{px}$ stroke widths?
* [ ] Is there **exactly ONE primary hero CTA** button on the screen?
* [ ] Do buttons implement tactile compression: `active:scale-[0.98] transition-all duration-150`?
