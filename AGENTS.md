# Dispatch UI/UX & Mobile Engineering Invariants

> **Scope:** Repository-wide frontend constitution (Web Review Studio & Android Native Jetpack Compose).  
> **Source Specification:** `c:\CODE\Dispatch\DESIGN_SYSTEM.md`  
> **Curriculum Baseline:** Forensic synthesis of 10 Mobile UI/UX Video Masterclasses (`design_research/`) + LeadMiner Design Tokens + CapCut Interaction Model.

Every agent working on frontend code, layout templates, components, and Android Compose views in this repository must strictly adhere to the following architectural rules:

---

## 1. The Single-Direction Law (Mobile Viewports)
* On mobile viewports and mobile frames (`isMobileFrame` or phone screens), every screen section moves along strictly **ONE** physical axis: either a vertical card stack OR a horizontal snap card carousel.
* Never lay out 2D multi-column desktop grids on mobile screens. Desktop 2-column or 12-column layouts must collapse to clean, unconstrained 1-column vertical stacks on mobile.

## 2. Biomechanical 44px Hitboxes & Safe Areas
* Every interactive element (button, icon trigger, tab, chip, slider thumb) must maintain a minimum touch bounding box of **$44\text{px} \times 44\text{px}$** (matching the human thumb contact patch).
* Maintain at least $8\text{px}$ separation between adjacent touch hitboxes to prevent mistaps.
* Bottom navigation bars and floating controls must respect the modern hardware safe area, clearing the $34\text{px}$ home indicator bar with dedicated bottom spacing.

## 3. The 4-Layer Elevation Architecture (60/30/10 Rule)
Never pick arbitrary hex colors or AI neon palettes. Every surface belongs to one of four calibrated layers:
* **Layer 0 (Canvas Root - 60%):** `#161616` (Deep charcoal studio anchor, `bg-studio`, `bg-background`).
* **Layer 1 (Cards & Elevated Surfaces - 30%):** `#1C1C1C` with border `#2E2E2E` (`bg-surface-100`, `border-border`).
* **Layer 2 (Recessed Wells & Inputs):** `#232323` (`bg-surface-200`) with deeper slider tracks and wells at `#282828` (`bg-surface-300`).
* **Layer 3 (Hero Brand CTA & Focus - 10%):** Electric Creator Blue `#2563EB` (`bg-primary`, hover `#3B82F6`, soft glow `rgba(37,99,235,0.15)`).
* **Semantic Colors (Functional Status Only):** Success `#22C55E`, Warning `#F59E0B`, Danger `#EF4444`. Never use semantic colors for decorative backgrounds.

## 4. The Law of the Single Hero CTA
* Each viewport must have **exactly ONE primary glowing hero action button** (`bg-primary text-white shadow-hero-glow`).
* Secondary and destructive actions ("Reject", "Cancel", "Back") must remain neutral recessed surfaces (`bg-surface-200 border-border text-text-main hover:bg-surface-300`). Never place competing brightly-colored buttons side-by-side.

## 5. Senior Typographic Limit & Numeric Place-Value Alignment
* Operational dashboard and studio screens must use **maximum 4 font sizes** ($18\text{px}$, $14\text{px}$, $12\text{px}$, $10\text{px}$) and **2 font weights** (Semibold for titles/headers, Regular/Medium for body/labels).
* **Numeric Place-Value Rule:** All numbers, durations, timestamps, speeds, file sizes, and percentages must strictly enforce `font-mono tabular-nums`. In tables and lists, numbers must be **right-aligned** so digits line up by place value.
* Always use non-breaking spaces before units (`42&nbsp;s`, `18.4&nbsp;MB`). Use the typographic ellipsis glyph `…` (`&hellip;`) rather than three periods `...`.

## 6. Concentric Geometry
* Nested elements inside rounded cards must follow exact concentric curvature mathematics:
  $$R_{\text{inner}} = R_{\text{outer}} - \text{Padding}$$
  Avoid mismatched pill vs squircle radiuses that create uneven visual gaps.

## 7. Anti-AI Vibecoding Standards (Prohibited Patterns)
* **Zero Native OS Emojis:** Never use emojis (🚀, 💡, 🔥, ⚡, 📦) as interface icons. Always use crisp SVG vector icons (`lucide-react`, Phosphor) with uniform $1.5\text{px}\text{--}2.0\text{px}$ stroke widths.
* **No Harsh Drop Shadows:** Never use opaque black drop shadows (`rgba(0,0,0,0.8)`). Use subtle border elevation (`border-border` `#2E2E2E`) paired with soft ambient diffusion.
* **No Multi-Color Neon Gradients:** Never apply blue-to-green or purple-to-cyan gradients to buttons or card backgrounds.

## 8. Tactile Feedback & Micro-Interactions
* Every clickable button and interactive card must provide immediate tactile feedback on touch/click:
  `active:scale-[0.98] transition-all duration-150`.
* All states must be fully specified: default, hover, active (pressed), disabled, and loading.
