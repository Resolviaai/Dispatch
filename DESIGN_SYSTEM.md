# Dispatch Unified Mobile & Studio Design System Specification

> **Version:** 2.0.0  
> **Status:** Authoritative System Constitution  
> **Curriculum Synthesis:** Forensic extraction of 10 Masterclass YouTube Video Transcripts (`design_research/`) + LeadMiner Design Tokens + CapCut Mobile Interaction Model.  
> **Aesthetic Archetype:** Hybrid **Jensen Huang** (Precise SaaS/AI Operational Grid) $\times$ **Steve Jobs** (Clean Minimalist System).  
> **Target Scope:** Dual-Surface Source of Truth — Web Review Studio (Wide Desktop) & Native Mobile App (POCO C65 / Android 13/14 HyperOS).

---

## 1. System Philosophy & Executive Summary

Dispatch is a high-grade personal content engine for solo creators. A creator shooting high-definition video on a smartphone and editing vertical clips on a laptop does not have time for ambiguous controls, unanchored floating elements, or AI neon slop.

The visual language of Dispatch is **tactile, calculated, stable, and unmistakably premium**. It adheres strictly to human-computer interaction (HCI) research, biomechanical thumb ergonomics, and the mathematical laws of spatial geometry.

### The Seven Unbreakable Laws of Dispatch UI
1. **The Single-Direction Law (Video 1):** Every mobile section moves along exactly ONE physical axis. Either a vertical card stack OR a horizontal snap carousel. Multi-axis 2D dashboard grids on mobile are strictly prohibited.
2. **The 44px Biomechanical Touch Target Rule (Videos 1 & 6):** Every clickable target maintains a minimum $44\text{px} \times 44\text{px}$ bounding box (matching the 10–12mm human thumb pad), with an $8\text{px}$ minimum hitbox separation.
3. **The 4-Layer Elevation Architecture:** Never pick arbitrary hex colors. Every pixel belongs to an explicit elevation layer (Canvas `#161616` $\to$ Cards `#1C1C1C` $\to$ Wells `#232323` $\to$ Hero Accent `#2563EB`).
4. **The Senior 4-Size Typography Limit (Video 10):** Operational screens use a maximum of 4 font sizes and 2 font weights. Numbers and metrics are strictly set in `font-mono tabular-nums`.
5. **Zero Decorative Emojis (Anti-AI Vibecoding Standard):** OS emojis (🚀, 💡, 🔥, ⚡, 📦) are strictly banned. Only crisp, uniform $1.5\text{px}\text{--}2.0\text{px}$ vector SVG icons (`lucide-react`) are permitted.
6. **Concentric Geometry ($R_{\text{inner}} = R_{\text{outer}} - P$):** Nested elements must follow exact geometric curvature math so border contours remain parallel and natural.
7. **Tactile Immediacy (Video 9):** Every interactive control provides instant physical tactile feedback (`active:scale-[0.98] transition-all duration-150`) in $<16\text{ms}$.

---

## 2. The 4-Layer Color Architecture & Depth System

Dispatch eliminates arbitrary neon palettes and enforces the **60/30/10 Rule (Videos 8 & 10)**:
- **60% Surface Base:** Deep charcoal canvas (`#161616`) anchor.
- **30% Structural Contrast:** Elevated cards (`#1C1C1C`), recessed wells (`#232323`), and borders (`#2E2E2E`).
- **10% Brand Accent:** Electric Creator Blue (`#2563EB`), reserved strictly for primary actions, active highlights, and focused states.

```
┌────────────────────────────────────────────────────────────┐
│ Layer 3: Functional Accents & Semantics (10%)              │
│   • Dispatch Hero CTA (#2563EB / hover: #3B82F6)           │
│   • Semantic States: Success #22C55E, Warning #F59E0B      │
├────────────────────────────────────────────────────────────┤
│ Layer 2: Nested Containers, Wells, Inputs, Deep Wells (30%)│
│   • bg-surface-200 (#232323) | bg-surface-300 (#282828)   │
│   • border-border (#2E2E2E) or border-border-strong        │
├────────────────────────────────────────────────────────────┤
│ Layer 1: Elevated Surfaces & Cards (30%)                   │
│   • bg-surface-100 (#1C1C1C)                               │
│   • border-border (#2E2E2E)                                │
├────────────────────────────────────────────────────────────┤
│ Layer 0: Root Canvas / Background (60%)                    │
│   • bg-studio / bg-background (#161616)                    │
└────────────────────────────────────────────────────────────┘
```

### Complete Color Token Reference Table

| Token Name | Hex Code | Tailwind Token | Android Compose Token | Purpose & Application |
| :--- | :--- | :--- | :--- | :--- |
| **Canvas** | `#161616` | `bg-studio`, `bg-background` | `CanvasBackground` | Root page background, full window backdrop |
| **Surface 100** | `#1C1C1C` | `bg-surface-100` | `CardSurface` | Elevated cards, header bars, bottom sheets |
| **Surface 200** | `#232323` | `bg-surface-200` | `InputBackground` | Nested data wells, input containers, inactive tab wells |
| **Surface 300** | `#282828` | `bg-surface-300` | `InputBackgroundDeep`| Deep slider tracks, recessed toolbars, preview frames |
| **Border Default**| `#2E2E2E` | `border-border` | `CardBorder` | Card borders, dividers, subtle separators |
| **Border Strong** | `#383838` | `border-border-strong`| `CardBorderStrong` | Hover borders, active selection borders |
| **Hero Brand** | `#2563EB` | `bg-primary`, `text-primary` | `PrimaryBlue` | Primary CTAs, active timeline playhead, focus rings |
| **Hero Hover** | `#3B82F6` | `hover:bg-primary-hover` | `PrimaryBlueHover` | Hover state for primary buttons |
| **Hero Soft** | `rgba(37,99,235,0.15)` | `bg-primary/15` | `PrimaryBlueSoft` | Active badge backgrounds, selected template rings |
| **Text Main** | `#EDEDED` | `text-text-main` | `TextPrimary` | High-contrast headings, prominent labels, titles |
| **Text Secondary**| `#A0A0A0` | `text-text-secondary` | `TextSecondary` | Subtitles, field labels, metadata descriptions |
| **Text Muted** | `#707070` | `text-text-muted` | `TextMuted` | Secondary timestamps, unit markers, helper text |
| **Semantic Success**| `#22C55E`| `text-success`, `bg-success/15`| `SemanticSuccess` | Verified sync, uploaded state, healthy battery |
| **Semantic Warning**| `#F59E0B`| `text-warning`, `bg-warning/15`| `SemanticWarning` | Soft rate limits, pending upload, YouTube auth req. |
| **Semantic Danger** | `#EF4444`| `text-danger`, `bg-danger/15` | `SemanticRecording`| Recording pulse, delete clip, permanent failures |

---

## 3. Spatial Geometry & The 8-Point Grid Mathematics (Videos 5 & 10)

Dispatch strictly forbids random pixel dimensions (e.g., `11px`, `19px`, `25px`). Every spatial distance, padding, margin, and container dimension must be cleanly divisible by **4** or **8**.

### Spatial Scale Progression
```css
--space-1:  4px;   /* Micro gap: icon-to-label, badge internal padding */
--space-2:  8px;   /* Tight grouping: label-to-input, chip gap, horizontal spacing */
--space-3: 12px;   /* Compact container inset, dense list spacing */
--space-4: 16px;   /* Standard card padding (p-4), modal body inset */
--space-5: 20px;   /* Comfortable screen gutter (px-5) on compact mobile */
--space-6: 24px;   /* Standard screen gutter (px-6) on tablet/desktop */
--space-8: 32px;   /* Section separation between distinct feature blocks */
--space-12: 48px;  /* View transition margins, empty state breathing room */
```

### Concentric Corner Radii Formula
When an inner element sits inside an outer card with padding $P$:
$$R_{\text{inner}} = R_{\text{outer}} - P$$

* If outer card is `rounded-2xl` ($16\text{px}$) with `p-4` ($16\text{px}$):
  $$R_{\text{inner}} = 16 - 16 = 0\text{px} \quad \implies \text{Flush or use } R_{\text{inner}} = 6\text{--}8\text{px} \text{ (rounded-md/lg)}$$
* If outer card is `rounded-xl` ($12\text{px}$) with `p-2` ($8\text{px}$):
  $$R_{\text{inner}} = 12 - 8 = 4\text{px} \text{ (rounded-sm)}$$
* If outer card is `rounded-2xl` ($16\text{px}$) with `p-2` ($8\text{px}$):
  $$R_{\text{inner}} = 16 - 8 = 8\text{px} \text{ (rounded-lg)}$$

---

## 4. Mobile Ergonomics & Bottom Navigation Architecture (Videos 1 & 6)

Mobile navigation is the backbone of app usability. The bottom navigation bar is prime thumb territory.

### Navigation Rules
1. **The 3 to 5 Tab Law:** Maximum 5 tabs. 3 to 4 is ideal. More than 5 leads to choice paralysis and accidental taps.
2. **Tab Anatomy:**
   - Active Tab: Dual visual cue (bold label in brand color + filled vector icon + glowing dot indicator).
   - Inactive Tab: Outline icon with muted text (`#707070` $\to$ hover `#EDEDED`).
   - Hitbox: minimum `min-h-[44px] min-w-[64px]` per tab item.
3. **Safe Area & Home Indicator Clearance:**
   - Modern notchless phones (POCO C65, iPhone 14/15) have an OS home indicator bar ($34\text{px}$).
   - The navigation bar must sit strictly above the safe area, never overlapping the home indicator:
     `pb-[calc(env(safe-area-inset-bottom,34px)+8px)]` or dedicated bottom spacer.
4. **No Desktop Clutter at the Bottom:** Never put back buttons, logo watermarks, or search bars inside the bottom navigation bar. Use the top bar or dedicated search screens.

```
┌────────────────────────────────────────────────────────────┐
│ TOP APP BAR (Contextual: Title, Sync Badge, Refresh)       │
├────────────────────────────────────────────────────────────┤
│                                                            │
│                  MAIN CONTENT VIEWPORT                     │
│               (Single Direction Vertical Stack)            │
│                                                            │
├────────────────────────────────────────────────────────────┤
│ BOTTOM NAVIGATION DECK (Layer 1: #1C1C1C, border #2E2E2E)  │
│   [ Record ]      [ Sessions ]      [ Clips ]   [ Settings]│
├────────────────────────────────────────────────────────────┤
│ HOME INDICATOR SAFE AREA (34px clear void: #1C1C1C)        │
└────────────────────────────────────────────────────────────┘
```

---

## 5. Typographic Discipline & Numeric Place-Value Alignment (Videos 1, 8, 10)

### The Mobile Type Scaling Paradox (Video 1)
Despite phone screens being physically smaller than desktop monitors, human eyes hold phones closer ($25\text{--}35\text{cm}$) and thumbs navigate handheld. Therefore, **mobile base body text must never be smaller than 14–16px**, while desktop dashboards can afford 12–14px.

### The 4-Size / 2-Weight Scale for Operational Screens (Video 10)
| Role | Size | Weight | Tracking / Leading | Example |
| :--- | :--- | :--- | :--- | :--- |
| **Page Title** | $18\text{px}$ (`text-lg`) | Semibold (`font-semibold`) | `tracking-tight leading-tight` | "Ready to review" |
| **Section Header**| $14\text{px}$ (`text-sm`) | Semibold (`font-semibold`) | `tracking-normal leading-snug` | "Layout Framing" |
| **Body / Labels** | $12\text{px}$ (`text-xs`) | Regular (`font-normal`) | `leading-relaxed text-text-secondary`| "Clips ready from this session" |
| **Micro Metadata**| $10\text{px}$ (`text-[10px]`)| Medium (`font-medium`) | `font-mono tabular-nums text-text-muted` | "[00:12.40 - 00:14.80]" |

### Place-Value & Monospace Rules
- All numeric measurements, file sizes, timestamps, and confidence percentages must enforce `font-mono tabular-nums`.
- In tables and lists, numbers must always be **right-aligned** so digits line up by place value (tens over tens, hundreds over hundreds).
- Always use non-breaking spaces before units: `42&nbsp;s`, `18.4&nbsp;MB`, `1080&nbsp;p`.
- Never use three periods `...`; always use the typographic ellipsis glyph `…` (`&hellip;`).

---

## 6. Tactile Micro-Interactions & Animation Physics (Video 9)

An interface feels cheap when buttons don't react immediately. Dispatch implements tactile micro-physics:

1. **Optimistic Press Compression:**
   Every button and interactive card must implement tactile spring compression on touch:
   ```css
   .btn-tactile {
     transition: transform 150ms cubic-bezier(0.16, 1, 0.3, 1), background-color 150ms ease;
   }
   .btn-tactile:active {
     transform: scale(0.98);
   }
   ```
2. **Spring Physics for Popovers & Active Words:**
   Active karaoke subtitle popover badges use calibrated spring stiffness ($k = 636$, damping $c = 24$) with duration $100\text{--}120\text{ms}$.
3. **Optimistic Toast Notifications (Video 9):**
   Toasts enter with vertical slide-up ($150\text{ms}$ ease-out), transition state from spinner $\to$ checkmark, and auto-dismiss without blocking the workspace.
4. **The Law of the Single Hero CTA:**
   Every screen has **exactly ONE primary glowing hero button** (`bg-primary text-white shadow-hero-glow`). Secondary actions (e.g. "Reject", "Cancel") must remain neutral recessed (`bg-surface-200 border-border text-text-main`). Never have competing colored buttons side-by-side.

---

## 7. The Six Cognitive UX Drivers (Video 3)

1. **Smart Defaults (70–90% Retention):** Pre-select the most common configuration (e.g. 9:16 Fit+Black, Yellow Pop subtitles, YouTube Shorts export). Eliminates decision fatigue.
2. **Goal Gradient Effect:** Start pipelines visually at $\ge 20\%$ rather than $0\%$ to motivate completion.
3. **Reciprocity:** Deliver value upfront (render video preview and live subtitle tracks) before demanding user actions.
4. **IKEA / Endowment Effect:** Allow creators to customize, reposition, and tweak word timings; once a user touches the canvas, they value the clip significantly higher.
5. **Loss Aversion:** Frame warnings around potential loss (e.g., "Unuploaded raw video will remain safely stored on device until sync is verified").
6. **Anchor Contrast:** Compare upload speeds and processing metrics against baseline benchmarks.

---

## 8. Anti-AI Vibecoding Standards (Prohibited Patterns)

| Prohibited Pattern (AI Slop) | Mandated Dispatch Standard |
| :--- | :--- |
| Native OS emojis (🚀, 💡, 🔥, ⚡, 📦) as icons | Crisp vector SVG icons (`lucide-react` / Phosphor) with uniform 1.5–2px stroke |
| Arbitrary neon purple/cyan gradients on buttons | Strict 4-layer tonal architecture with Electric Creator Blue `#2563EB` |
| Harsh black drop shadows (`rgba(0,0,0,0.8)`) | Subtle elevated border (`border-border` `#2E2E2E`) + soft blurred glow |
| 6+ different font sizes and weights on one card | Maximum 4 font sizes and 2 font weights on core operational screens |
| Centered currency and metric numbers | Right-aligned numbers set in `font-mono tabular-nums` |
| Floating cards with no background anchor | Explicit Layer 0 Canvas (`#161616`) anchor containing all cards |
| Multi-axis chaotic grid layouts on mobile | Strictly single-direction layout per section (vertical stack OR horizontal carousel) |
| Multiple glowing primary buttons on one screen | Exactly ONE hero CTA button per viewport |
