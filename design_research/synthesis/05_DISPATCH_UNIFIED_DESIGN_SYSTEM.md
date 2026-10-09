# Dispatch Mobile Design System: Unified Engineering Specification

> **Document ID:** `DISPATCH-SYS-05`  
> **Status:** Production-Ready System Constitution  
> **Synthesis:** Unification of Research Synthesis Documents `01` through `04`  
> **Aesthetic Classification:** Hybrid *"Jensen Huang"* (Precise SaaS/AI Operational Grid) $\times$ *"Steve Jobs"* (Clean Minimalist System).  
> **Target Platform:** Mobile First (iOS & Android Native / React Native / PWA).

---

## 1. System Vision & Core Tenets

Dispatch is a high-reliability, real-time logistics and fleet operations platform. In mission-critical dispatch environments, visual ambiguity leads to operational errors. The interface must feel like a precision flight instrument: calculated, tactile, calm, and immediately readable under high stress and movement.

### Core Architectural Laws:
1. **The Single-Direction Law:** Every screen section moves along exactly one physical axis (vertical page stack OR horizontal card snap, never both).
2. **The 44px Biomechanical Rule:** Every touch target maintains an interactive bounding box $\ge 44\text{px} \times 44\text{px}$.
3. **The 4-Layer Surface Architecture:** Strict depth separation without arbitrary color picking.
4. **The Strict 4-Size Typography Limit:** Core operational screens use exactly 4 font sizes and 2 font weights.
5. **Zero Decorative Emojis:** Native OS emojis are strictly prohibited; standard $1.5\text{px}\text{--}2.0\text{px}$ vector SVGs only.
6. **Tactile Immediacy:** Every interactive control provides optimistic tactile compression (`active:scale-[0.98]`) $< 16\text{ms}$.

---

## 2. Spacing & Geometric Grid Tokens

Dispatch uses a strict $4\text{pt}$ and $8\text{pt}$ geometric progression. Fractional sub-pixel dimensions and arbitrary numbers are completely rejected.

```
+-----------------------------------------------------------------------------------------+
| Screen Boundary Margin: px-6 (24px)                                                      |
|  +-----------------------------------------------------------------------------------+  |
|  | Card Inset: p-4 (16px)                                                            |  |
|  |  +-----------------------------------------------------------------------------+  |  |
|  |  | Header Row [mb-1: 4px]                                                      |  |  |
|  |  | Subtitle Metadata                                                           |  |  |
|  |  +-----------------------------------------------------------------------------+  |  |
|  |  [gap-3: 12px internal element separation]                                        |  |
|  |  +-----------------------------------------------------------------------------+  |  |
|  |  | Operational Data Table / Right-Aligned Numerics                             |  |  |
|  |  +-----------------------------------------------------------------------------+  |  |
|  +-----------------------------------------------------------------------------------+  |
|  [mb-6: 24px section break between cards]                                               |
+-----------------------------------------------------------------------------------------+
```

### 2.1 Spacing Scale
```css
/* Dispatch Spatial Tokens */
--space-1:  4px;   /* Micro gap: icon-text, badge padding */
--space-2:  8px;   /* Tight grouping: label to field, chip gaps */
--space-3: 12px;   /* Compact container padding, dense lists */
--space-4: 16px;   /* Standard card padding, modal body inset */
--space-5: 20px;   /* Comfortable card padding, header insets */
--space-6: 24px;   /* Global screen gutters (px-6) */
--space-8: 32px;   /* Section separation between distinct modules */
--space-12: 48px;  /* View transition gutters, empty state breathing room */
```

### 2.2 Container Insets & Safe Areas
* **Screen Horizontal Gutter:** `px-5` ($20\text{px}$) on compact screens, `px-6` ($24\text{px}$) on standard screens.
* **Bottom Navigation Safe Offset:** `pb-[calc(env(safe-area-inset-bottom,34px)+16px)]`.
* **Touch Target Bounding Constraint:** `min-w-[44px] min-h-[44px]`.

---

## 3. The 4-Layer Color Architecture & Depth System

Dispatch eliminates arbitrary coloring by enforcing the **60-30-10 rule** across four functional layers:

```
[Layer 3: Functional Accents & Status] -> Indigo Brand (#6366F1), Emerald, Rose, Amber
    ^
[Layer 2: Recessed Containers & Wells] -> bg-black/40 or bg-slate-950/80 border-white/5
    ^
[Layer 1: Elevated Surfaces & Cards]   -> bg-slate-900/60 border border-white/10
    ^
[Layer 0: Canvas Anchor Root]          -> bg-[#070A0F] (Deep Charcoal Void)
```

### 3.1 Token Color Map
| Layer | Token Name | Hex Value / Class | Description |
| :--- | :--- | :--- | :--- |
| **Layer 0** | `canvas-root` | `#070A0F` (`bg-[#070A0F]`) | Neutral deep anchor; consumes $60\%$ of visual area. |
| **Layer 1** | `surface-card` | `rgba(15, 23, 42, 0.65)` (`bg-slate-900/65`) | Primary operational cards; paired with `border-white/10`. |
| **Layer 1-Hi**| `surface-elevated`| `rgba(30, 41, 59, 0.85)` (`bg-slate-800/85`) | Modals, bottom sheets, floating navigation bars. |
| **Layer 2** | `container-recessed`| `rgba(0, 0, 0, 0.45)` (`bg-black/45`) | Sunken wells: code, telemetry logs, inputs. |
| **Layer 3** | `brand-primary` | `#6366F1` (`text-indigo-400`, `bg-indigo-600`) | Core interactive accents and active highlights. |
| **Semantic**| `status-emerald`| `#10B981` (`text-emerald-400`, `bg-emerald-500/10`) | Active vehicle, completed route, verified signal. |
| **Semantic**| `status-rose` | `#F43F5E` (`text-rose-400`, `bg-rose-500/10`) | Breakdown, traffic hazard, critical alert, destructive CTA. |
| **Semantic**| `status-amber`| `#F59E0B` (`text-amber-400`, `bg-amber-500/10`) | Warning, maintenance due, reroute suggested. |

---

## 4. Typography Scale & Readability Engineering

Adhering to the Senior Standard (Video 10), Dispatch operates with **maximum 4 font sizes and 2 font weights** in operational screens.

```
TYPOGRAPHIC SPECIFICATION:
text-title: 20px / leading-[1.15] / font-semibold / tracking-tight (-0.025em)
text-body:  15px / leading-[1.45] / font-normal   / tracking-normal
text-meta:  13px / leading-[1.38] / font-medium   / tracking-normal (slate-400)
text-micro: 11px / leading-[1.25] / font-semibold / tracking-wider (+0.04em, uppercase)
```

### 4.1 Typographic Tokens
```css
/* Dispatch Typography Tokens */
.text-title {
  font-size: 20px;
  line-height: 24px;
  font-weight: 600;
  letter-spacing: -0.025em; /* Pro Display Tuning Hack */
  color: #FFFFFF;
}

.text-body {
  font-size: 15px;
  line-height: 22px;
  font-weight: 400;
  color: #E2E8F0;
}

.text-meta {
  font-size: 13px;
  line-height: 18px;
  font-weight: 500;
  color: rgba(148, 163, 184, 0.7); /* slate-400 at 70% */
}

.text-micro {
  font-size: 11px;
  line-height: 14px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
```

### 4.2 Numeric & Telemetry Rules
* **Tabular Numbers:** Every coordinate, speed, timestamp, currency, and ETA must use `font-mono tabular-nums`.
* **Decimal Scale Down:** For currency or fractions (e.g., `$1,820.40`), render the integer part in `text-title font-mono` and the decimal part in `text-meta font-mono text-slate-400` to prevent visual collision.
* **Alignment:** Numerical rows in dispatch manifests must always be **right-aligned**.

---

## 5. Core Component Engineering Specifications

### 5.1 Floating Bottom Navigation Bar
```
+-------------------------------------------------------------------------------+
| [1px Top Border: border-t border-white/10]                                    |
| [Backdrop Surface: bg-slate-900/85 backdrop-blur-xl]                          |
|  +-------------------------------------------------------------------------+  |
|  |   [Feed]         [Fleet]       [+ DISPATCH]       [Routes]     [Profile] |  |
|  |    24px           24px          50px Floating      24px          24px   |  |
|  |    11px           11px          Center CTA         11px          11px   |  |
|  +-------------------------------------------------------------------------+  |
| [Home Indicator Clearance: pb-[env(safe-area-inset-bottom,34px)]]             |
+-------------------------------------------------------------------------------+
```

* **Layout:** Exactly 4 secondary tabs $+$ 1 center floating action button.
* **Center Floating CTA:**
  * Dimensions: $50\text{px} \times 50\text{px}$ squircle (`rounded-2xl`).
  * Elevation: Offset upward by $-14\text{px}$ with glowing accent shadow (`shadow-[0_4px_16px_rgba(99,102,241,0.4)]`).
  * Function: Instant bottom sheet trigger for "Create Dispatch".
* **Tab Items:**
  * Bounding Hitbox: `min-w-[44px] min-h-[44px] flex flex-col items-center justify-center`.
  * Active Transition: Outline SVG morphs to Solid SVG; label shifts from `text-slate-400` to `text-indigo-400 font-semibold`.
  * Inactive Contrast: Maintained at $\ge 3:1$ WCAG contrast ratio (`text-slate-400/80`).

### 5.2 Single-Layer Operational Cards
* **Rule:** Strict prohibition of double-nested cards.
* **Structure:**
  ```html
  <div class="bg-slate-900/65 backdrop-blur-md border border-white/10 rounded-2xl p-4 space-y-3">
    <!-- Header: Title + Trust Status Proximity -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2">
        <h3 class="text-title">Van #12 (Ford Transit)</h3>
        <span class="text-micro px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
          ONLINE
        </span>
      </div>
      <span class="font-mono tabular-nums text-meta text-right">98% BATT</span>
    </div>

    <!-- 1px Subtle Divider -->
    <div class="h-px bg-white/5 w-full"></div>

    <!-- Recessed Telemetry Well -->
    <div class="bg-black/40 rounded-xl p-3 border border-white/5 flex items-center justify-between">
      <span class="text-meta">Current Route</span>
      <span class="font-mono tabular-nums text-body text-slate-200">Sector 4 -> Airport Hub</span>
    </div>
  </div>
  ```

### 5.3 Sticky Bottom Action Bar with Embedded State
* **Placement:** `sticky bottom-0 left-0 right-0 p-4 bg-slate-900/95 backdrop-blur-xl border-t border-white/10 pb-[calc(env(safe-area-inset-bottom,34px)+12px)]`.
* **Pro UX Pattern:** Stepper directly paired with CTA.
* **CTA Button Copy:** Always embeds calculated value:
  ```html
  <button class="w-full h-12 bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98] rounded-xl font-semibold text-white flex items-center justify-between px-5 transition-transform">
    <span>Confirm Dispatch</span>
    <span class="font-mono tabular-nums text-indigo-200">2 Vans • $140.00</span>
  </button>
  ```

### 5.4 Contextual Bottom Sheet Modal
* **Trigger:** Center "+" button or card tap.
* **Transition Choreography:**
  * Sheet slides from `translate-y-full` to `translate-y-0` ($300\text{ms}$ ease-out).
  * Main screen scales down to $0.95$ with rounded corners and $50\%$ dim overlay.
  * Drag handle: $36\text{px} \times 4\text{px}$ centered pill (`bg-slate-700 rounded-full`).
  * Dismissal: Swipe down gesture or tapping the dim overlay.

### 5.5 Protective Icon Containers on Map/Media
* When an icon sits over maps or satellite imagery, wrap it in a protective pill container:
  ```html
  <div class="w-10 h-10 rounded-full bg-slate-900/80 backdrop-blur-md border border-white/15 flex items-center justify-center text-white shadow-lg">
    <svg class="w-5 h-5" ...></svg>
  </div>
  ```

---

## 6. Motion & Tactile Micro-Interaction Tokens

All motion in Dispatch is purposeful and tactile. Scrolljacking and gratuitous decorative animations are banned.

### 6.1 Motion Timing & Easing Scale
```css
/* Dispatch Motion Physics */
--ease-tactile: cubic-bezier(0.2, 0.8, 0.2, 1);
--ease-spring:  cubic-bezier(0.34, 1.56, 0.64, 1);

--duration-tap:    100ms;  /* Button compression */
--duration-state:  200ms;  /* Toggle, checkbox, hover */
--duration-sheet:  320ms;  /* Bottom sheet pan */
--duration-screen: 280ms;  /* Lateral view transition */
```

### 6.2 Key Interaction Implementations
1. **Button Down-Press:** `active:scale-[0.98] transition-transform duration-100 ease-out`.
2. **Cross-Component Confirmation:** Saving a vehicle route fills the card bookmark icon AND immediately pulses a $6\text{px}$ indicator dot on the Bottom Nav "Fleet" tab.
3. **Delayed Operational Tooltips:** $1,000\text{ms}$ hover dwell timer to prevent accidental flicker during fast thumb sweeps.
4. **Toast Progression:** Bottom-docked toast slides up with an indeterminate spinner, resolving into an emerald checkmark before auto-dismissing after $3,500\text{ms}$.

---

## 7. Psychological Principles for Operational Conversion

Dispatch applies the 6 proven psychological principles (Video 03) to streamline operator workflows:

1. **Smart Defaults (The 70-90% Law):** Every dispatch form loads pre-filled with the most common route configuration and vehicle assignment. Operators adjust only anomalies.
2. **Goal Gradient Effect (The 20% Head-Start):** Manifest creation initiates at $20\%$ complete (Step 1 "Route Initialized" pre-checked), providing immediate forward momentum.
3. **Reciprocity Upfront:** Full route telemetry, traffic delays, and cost calculations are visible *before* confirming manifests or requiring authorization locks.
4. **Endowment & IKEA Attachment:** Operators configure vehicle tags and route rules directly. The CTA is labeled *"Save Configured Fleet"* rather than a generic *"Submit"*.
5. **Loss Aversion Alerts:** Critical alerts are framed around preventing operational loss: *"Route bottleneck detected: 28 minutes at risk. Tap to reroute."* (Dismiss button: *"Ignore Risk"*).
6. **Price & Cost Anchoring:** Ancillary expediting costs are displayed relative to cargo value: `+$30 Priority Escort (just 1.8% of cargo)`.

---

## 8. Anti-AI Audit Checklist for Implementation

Before any screen or component is approved in Dispatch, verify compliance against this checklist:

- [ ] **No Native Emojis:** Verified zero OS emojis; only uniform SVGs used.
- [ ] **No Arbitrary Spacing:** All padding, margins, and gaps are multiples of $4\text{px}$ or $8\text{px}$.
- [ ] **No Double-Nested Cards:** Cards do not contain nested card boxes.
- [ ] **Hit Target Compliance:** All interactive touch elements are $\ge 44\text{px} \times 44\text{px}$.
- [ ] **Safe Area Clearance:** Bottom elements sit above the $34\text{px}$ Home Indicator.
- [ ] **4-Size Typography Limit:** Screen contains $\le 4$ font sizes and $\le 2$ weights.
- [ ] **Headlines Tuned:** Display headlines use `tracking-tight` and `leading-[1.15]`.
- [ ] **Monospace Telemetry:** All numeric metrics use `font-mono tabular-nums` and right-alignment.
- [ ] **Tactile Press Feedback:** Interactive buttons implement `active:scale-[0.98]`.
- [ ] **Single-Direction Flow:** Section moves strictly on one axis (vertical or horizontal).
- [ ] **4-Layer Stack Adherence:** Colors strictly follow Canvas $\to$ Surface $\to$ Well $\to$ Accent.
