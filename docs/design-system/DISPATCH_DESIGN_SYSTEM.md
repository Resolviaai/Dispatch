# Dispatch Design System Specification (v2.0)

> **Scope:** Desktop Video Review Workstation (`/studio`) & Android Native Jetpack Compose / WebView Bridge.  
> **Aesthetic Archetype:** Linear Information Hierarchy + CapCut Editing Ergonomics + Descript Content Review Workflow.  
> **Core Mandate:** Pure functional dark studio tool. Zero ornamental fluff, zero AI-neon gradients, zero native OS emojis, 100% data-driven density and biomechanical precision.

---

## 1. Design Principles & Creator Experience

1. **The Video is the Visual Hero:** The stage must prioritize raw and rendered footage clarity. Overlays, chrome, and toolbars must remain calm, neutral, and readable across ultra-bright or pitch-black footage.
2. **Timeline is Data, Not Decoration:** Caption blocks and word timings must represent authentic audio timestamps derived from Whisper/speech models. Words are click-to-seek and editable in place.
3. **Information Density Without Clutter:** A pro tool earns trust through organization and spatial hierarchy. Spacing uses a 4px modular scale, numbers use `tabular-nums`, and labels remain visually subordinate to values.
4. **Publishing is a Consequential State Change:** Approving a clip for local review is strictly distinct from public multi-platform broadcast. Every destination reflects real authentication state, live character counters, and explicit privacy guarantees (Private Draft vs. Auto Public).
5. **Biomechanical & Keyboard Ergonomics:** Every interactive element satisfies $\ge 44\text{px}$ touch target on mobile or $\ge 32\text{px}$ click target on desktop with tactile micro-physics (`active:scale-[0.98]`). Complete review and playback workflows are navigable via standard editing shortcuts (`Space`, `J`, `K`, `L`, `A`, `R`, `E`).

---

## 2. Color Architecture & Surface Elevation (4-Layer Model)

Dispatch enforces a strict 4-layer tonal architecture rooted in deep obsidian graphite and charcoal:

```css
:root {
  /* Layer 0: Root Studio Canvas (60% visual surface) */
  --studio-canvas: #0D0F12;
  
  /* Layer 1: Raised Panels & Workstation Shell (30% visual surface) */
  --studio-surface: #16181C;
  --studio-surface-elevated: #1C1F24;
  
  /* Layer 2: Recessed Wells, Inputs & Timeline Track */
  --studio-recessed: #23262C;
  --studio-recessed-deep: #1A1C21;

  /* Low-Contrast Structural Borders (1px precision dividers) */
  --studio-border: #2E3239;
  --studio-border-subtle: #242830;
  --studio-border-strong: #3E434D;

  /* Typography Scales */
  --studio-text-primary: #F3F4F6;
  --studio-text-secondary: #A3AAB5;
  --studio-text-muted: #747D8A;
  --studio-text-inverse: #0B0D10;

  /* Functional Brand Accent (Single Hero CTA & Focus - 10%) */
  --studio-primary: #2563EB;
  --studio-primary-hover: #1D4ED8;
  --studio-primary-soft: rgba(37, 99, 235, 0.12);
  --studio-primary-focus: rgba(37, 99, 235, 0.35);

  /* Semantic Status Colors (Reserved strictly for truthful system telemetry) */
  --studio-success: #10B981;
  --studio-success-soft: rgba(16, 185, 129, 0.12);
  --studio-warning: #EAB308;
  --studio-warning-soft: rgba(234, 179, 8, 0.12);
  --studio-danger: #F43F5E;
  --studio-danger-soft: rgba(244, 63, 94, 0.12);

  /* Caption Active-Word Highlight */
  --studio-caption-highlight: #FFE600;
}
```

### Elevation Rules
* **Layer 0 (`--studio-canvas`, `#0D0F12`):** Outer browser window, desktop background, stage backdrop around contained video.
* **Layer 1 (`--studio-surface`, `#16181C`):** Persistent global header, left queue pane, right inspector pane.
* **Layer 2 (`--studio-recessed`, `#23262C`):** Form inputs, search fields, timeline canvas, scrubber tracks, media preview wells.
* **Layer 3 (Hero Focus & Primary CTA):** Electric Creator Blue (`#2563EB`) reserved exclusively for primary actions ("Approve & Publish"), active selections, and focus rings.

---

## 3. Typographic Hierarchy & Numeric Alignment

* **Primary Font:** System UI Sans (`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`).
* **Technical & Timecode Font:** Monospace with place-value tabular numbers (`font-mono tabular-nums`).

| Element | Size | Weight | Line Height | Tracking | Transform |
|---|---:|---|---:|---|---|
| Workstation Title | 14px | 600 (Semibold) | 20px | -0.01em | Normal |
| Section Headings | 12px | 600 (Semibold) | 16px | +0.02em | Uppercase |
| Clip Titles in Queue | 13px | 600 (Semibold) | 18px | -0.01em | Normal |
| Body & Inputs | 13px | 400 (Regular) | 18px | Normal | Normal |
| Badges & Status Chips | 11px | 500 (Medium) | 14px | Normal | Normal |
| Timecode & Durations | 12px | 500 (Medium) | 16px | Mono | Tabular |
| Metadata & Timestamps | 11px | 400 (Regular) | 14px | Normal | Normal |

---

## 4. Workstation Architecture: Three-Pane Grid

The desktop review workstation fills $100\text{dvh}$ without page-level scrollbars:

```
+---------------------------------------------------------------------------------------------------+
|  DISPATCH · STUDIO HEADER (54px) [YouTube: Connected | Gemini: Active | Disk: 42 GB | Focus Mode] |
+-----------------------+---------------------------------------------------+-----------------------+
|  LEFT QUEUE (300px)   |  CENTER STAGE (1fr, min 480px)                    |  RIGHT INSPECTOR      |
|  - Search & Filters   |  - Aspect-Ratio Framing Selector                  |  (380px)              |
|  - Session Grouping   |  - Video Preview Canvas                           |  - Title & Hook       |
|  - Compact Clip Rows  |  - Transport Controls (Play/Pause, Scrub, Vol)    |  - Caption Presets    |
|  - Batch Selection    +---------------------------------------------------+  - Destinations       |
|  - Score Threshold    |  CAPTION DOCK (170px)                             |  - Character Counters |
|                       |  - Time Ruler · Word Blocks · Inline Edit         |  - Action Dock        |
+-----------------------+---------------------------------------------------+-----------------------+
```

### Dimensions & Responsiveness
* **Header Height:** Fixed `54px` (`border-b border-studio-border`).
* **Left Queue Pane:** Default `300px` (min `260px`, max `360px`, collapsible to `0px`).
* **Center Stage Pane:** Flexible `minmax(0, 1fr)` (target $\ge 480\text{px}$).
* **Right Inspector Pane:** Default `380px` (min `340px`, max `460px`, collapsible to `0px`).
* **Caption Dock:** Fixed `170px` at bottom of Center Stage.
* **Focus Mode:** Collapses both Left and Right panes with one click or shortcut `F`, dedicating maximum canvas to video preview and timeline inspection.

---

## 5. Publishing Destination Constraints & Rules

Character counters compute live character usage against authoritative platform specifications:

| Platform | Field | Safe Limit | Hard Max | Validation Rules |
|---|---|---:|---:|---|
| **YouTube Shorts** | Title | 80 chars | 100 chars | Includes `#Shorts` tag. Missing title blocks publish. |
| **Instagram Reels** | Caption | 1,800 chars | 2,200 chars | Includes hashtags & mentions. |
| **LinkedIn** | Commentary | 2,500 chars | 3,000 chars | Professional summary and hashtags. |
| **X (Twitter)** | Post Text | 250 chars | 280 chars | Standard post limit. |

---

## 6. Keyboard Ergonomics & Shortcut Matrix

Shortcuts are globally active on desktop workstation, but strictly disabled when the creator is typing inside any `<input>`, `<textarea>`, or `contenteditable` container:

| Key | Context | Action |
|---|---|---|
| `Space` | Global Stage | Toggle Play / Pause video playback |
| `J` | Global Stage | Jump backward 5 seconds |
| `K` | Global Stage | Pause video |
| `L` | Global Stage | Jump forward 5 seconds |
| `ArrowUp` / `ArrowLeft` | Queue | Select previous clip |
| `ArrowDown` / `ArrowRight` | Queue | Select next clip |
| `A` | Selected Clip | Quick Approve selected clip |
| `R` | Selected Clip | Quick Reject selected clip |
| `E` | Selected Clip | Focus caption editor / inspector |
| `F` | Global Workstation | Toggle Focus Mode (maximize stage) |
| `?` | Global Workstation | Open Keyboard Shortcuts Cheat Sheet |
| `Escape` | Modals / Focus | Close active modal or exit Focus Mode |

---

## 7. Prohibited Anti-Patterns (Enforced)

1. **Zero Native OS Emojis:** Never use emojis (🚀, 💡, 🔥, ⚡, 📦) as UI icons. Use clean Lucide SVG icons with uniform $1.5\text{px}\text{--}2.0\text{px}$ stroke weights.
2. **No Neon Gradients or Rainbow Meters:** Never apply multi-color neon gradients (purple-to-green or cyan-to-pink) to cards or score indicators.
3. **No Unanchored Numbers:** All numbers, durations, speeds, and file sizes must enforce `font-mono tabular-nums` and right-align in tables and metric lists.
4. **No Fake Telemetry:** Remote account states, storage sizes, and video durations must reflect live backend reality or explicitly display "Not Configured / Requires Setup".
5. **No Mobile Squeeze on Desktop:** Desktop review must never render a stretched single-column card list with enormous empty gutters.
