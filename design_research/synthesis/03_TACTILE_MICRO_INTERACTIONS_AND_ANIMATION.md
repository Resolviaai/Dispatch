# Dispatch Mobile Design System: Tactile Micro-Interactions & Animation Choreography

> **Document ID:** `DISPATCH-SYS-03`  
> **Source Material:** Videos 01 (`Everything you need to know about Mobile App UI's`), 05 (`7 UI/UX mistakes that SCREAM you're a beginner`), 06 (`Top UI/UX Design Tips - Bottom Navigation Bar`), 08 (`Every UI/UX Concept Explained in Under 10 Minutes`), 09 (`11 Micro Animations That Will Instantly Level Up Your UI`), 10 (`4 levels of UI/UX design and BIG mistakes to avoid`).  
> **Focus:** Tactile psychology, complete interaction states, spring physics, 11 pro micro-animations, cross-component feedback, and cinematic screen choreographies.

---

## 1. The Psychology of Tactile Response & State Completeness

In physical hardware, every button press produces a physical tactile snap and audible click. In software, touch screens lack mechanical feedback unless intentionally engineered into the software layer.

```
MECHANICAL SWITCH ANALOGY:
[User Press] ---> [Physical Compression] ---> [Audible Click] ---> [Instant Feedback]
                       (Zero Lag)               (Confirmation)          (High Trust)

TOUCHSCREEN INTERACTION:
[User Tap]   ---> [Immediate Visual Scale] ---> [Haptic Impulse] ---> [Optimistic Mutation]
                   (active:scale-[0.98])          (Taptic Engine)     (Before Server Acks)
```

### 1.1 The "Light Switch with No Click" Hazard
When an interactive button changes state with zero immediate visual feedback, the user enters cognitive limbo: *Did I tap it? Did the app freeze? Should I tap again?*
* Repeated tapping causes double-submissions, accidental double-bookings, and frustration.
* **The Rule of Tactile Immediacy:** Provide **instant visual tactile compression** within $< 16\text{ms}$ (one frame) of touch contact, long before asynchronous network requests resolve.

### 1.2 Mandatory 5-State Set for Interactive Elements
Every interactive component in the Dispatch design system must declare all five functional states:
1. **Default State:** Base resting appearance with clear signifiers of affordance.
2. **Hover / Focus State:** Subtle brightness elevation (`brightness-110`) or focus ring (`ring-2 ring-indigo-500/40`).
3. **Active / Pressed State:** Physical down-press simulation (`active:scale-[0.98]` or `active:scale-95`, `brightness-95`).
4. **Disabled State:** Opacity reduced to $40\%\text{--}50\%$ (`opacity-40 cursor-not-allowed pointer-events-none`).
5. **Loading / Processing State:** Preserves exact original button dimensions (`min-h-[44px] min-w-[120px]`), replaces label with centered indeterminate spinner or pulsing skeleton, preventing layout shift.

### 1.3 The Input State Hierarchy
Inputs must never rely on placeholder text alone to communicate state:
* **Resting:** Recessed container (`bg-black/40 border border-white/10`).
* **Focused:** High-contrast focus boundary (`border-indigo-500 ring-2 ring-indigo-500/20`).
* **Error:** Distinct semantic border (`border-rose-500/80 ring-2 ring-rose-500/10`) paired with an unambiguous error description message immediately below the field in `text-rose-400 text-xs`.
* **Success / Validated:** Verified checkmark glyph in trailing position.

---

## 2. The 11 Micro-Animations Catalog & Implementation Formulas

Sourced from production-grade patterns (Videos 08 & 09), these 11 micro-interactions deliver high-end polish while serving concrete functional purposes.

```
MICRO-INTERACTION PHYSICS MATRIX:
+------------------------+-----------+-------------+-------------+-------------------------------+
| Interaction            | Duration  | Stiffness   | Damping     | Key Tailwind / CSS Trigger     |
+------------------------+-----------+-------------+-------------+-------------------------------+
| Tactile Button Press   | 100ms     | N/A         | N/A         | active:scale-[0.98]           |
| Avatar Popover Tag     | 500ms     | k = 636     | c = 24      | cubic-bezier(0.34, 1.56, 0.64)|
| Toast Slide-In         | 300ms     | k = 400     | c = 28      | translate-y-0 opacity-100     |
| Bottom Sheet Pan       | 350ms     | k = 300     | c = 30      | ease-out duration-300         |
| Parallax Edge Back     | 250ms     | N/A         | N/A         | translate-x-[35%]             |
| Delayed Tooltip Dwell  | 1000ms dl | N/A         | N/A         | transition-delay: 1000ms      |
+------------------------+-----------+-------------+-------------+-------------------------------+
```

### 2.1 Tactile Button Press (The Standard Down-Press)
* **Mechanics:** Instant visual depression upon touch contact.
* **Code Implementation:**
  ```html
  <button class="transition-transform duration-100 ease-out active:scale-[0.98] select-none">
    Confirm Dispatch
  </button>
  ```

### 2.2 Masked Button Hover (Vertical Slide-Up Text)
* **Mechanics:** Used on primary desktop/tablet triggers. A fixed-height container masks two identical text elements stacked vertically; on hover, text $A$ slides up out of view while text $B$ slides into position.
* **Result:** Communicates kinetic vitality without clumsy color flips.

### 2.3 Toast Notifications with State Progression (Linear Pattern)
* **Mechanics:**
  1. Slides upward from bottom boundary with gentle spring overshoot.
  2. Displays an indeterminate spinner while mutation is active.
  3. Mutates smoothly into an emerald checkmark glyph upon completion.
  4. Emits subtle celebratory particle pulse before auto-dismissing after $3,500\text{ms}$.

### 2.4 Tactile Name Tag / Status Pill Pop-Out
* **Mechanics:** Tap or dwell on a fleet driver avatar reveals an elevated status badge.
* **Spring Specification:** Duration $500\text{ms}$, stiffness $k = 636$, damping $c = 24$.
* **Geometry:** `rounded-full`, dark background (`bg-slate-900 border border-white/15`), white text, slight $+2^\circ$ tilt for tactile charm.

### 2.5 Shimmer Stroke (Gradient Border Glow)
* **Mechanics:** Used for active dispatches or high-priority live alerts. A subtle angular gradient rotates around the card perimeter masked to a $1\text{px}$ hairline stroke.
* **Accessibility Rule:** Always respect `@media (prefers-reduced-motion: reduce)`. Provide a static border fallback when motion reduction is requested.

### 2.6 Delayed Tooltips (The Obsidian Dwell Pattern)
* **Problem:** Instant tooltips trigger accidentally during quick finger/cursor sweeps across dense operational dashboards, causing visual seizure.
* **Formula:** Implement a **$1,000\text{ms}$ delay timer** on hover/long-press. Tooltips appear only when user intent is deliberate.

### 2.7 Contextual Metadata Pop-Out (Preview Cards)
* **Mechanics:** Hovering or tapping a dispatch tracking code reveals a compact floating card displaying route origin, destination preview, and driver status thumbnail.

### 2.8 Masked Progress Fill (Continuous Liquid Fill)
* **Mechanics:** Operational progress bars (e.g., fuel level, route completion) must never jump in harsh discrete blocks. Utilize CSS linear masks or SVG dash-offset transitions with ease-out timing to create a smooth, continuous flow.

### 2.9 Card Swipe Stack (Dub.co / iMessage Pattern)
* **Mechanics:** Stacks of incoming delivery alerts.
  1. Swiping the top card rotates it by $\pm 12^\circ$ proportional to drag distance and reduces opacity to $0\%$.
  2. The background secondary card simultaneously scales from $0.94 \to 1.0$ and translates down into the primary foreground position.

### 2.10 Expanding Search Bar Morph
* **Mechanics:** In resting state, search exists as a compact $44\text{px}$ circular icon button. Tapping morphs the circle into a full-width pill search input with smooth bezier easing, seamlessly revealing the keyboard.

### 2.11 Contextual Quota Comparison Slide-In
* **Mechanics:** Tapping a fleet capacity metric smoothly slides out the secondary detail (e.g., `4 / 12 Active Vans` $\to$ `+8 Available in Depot`) within the same component boundary.

---

## 3. Cross-Component Micro-Feedback

Isolated component feedback is beginner tier; cohesive systemic feedback across disconnected UI regions is senior tier.

```
+-------------------------------------------------------------------------+
| [Vehicle Card #104]                                                     |
| Driver: Alex Mercer                                                     |
| [Bookmark / Star Action Button: TAPPED]                                 |
+-------------------------------------------------------------------------+
                                    |
            =================================================
            |  Simultaneous Dual Cross-Component Feedback   |
            =================================================
                    |                               |
                    v                               v
    +------------------------------+  +-------------------------------+
    | Local Button Feedback:       |  | Global Navbar Feedback:       |
    | • Bookmark icon fills solid  |  | • Bottom Nav "Saved" tab      |
    | • Scales up 1.15x -> 1.0x    |  |   spawns a 6px pulsing        |
    | • Micro-haptic click emitted |  |   indicator dot (Rose/Amber)  |
    +------------------------------+  +-------------------------------+
```

### The Systemic Feedback Law:
> When a user mutates data inside an operational card, the UI must confirm both **locally** (on the immediate button) and **globally** (on the relevant navigation tab or metric badge). This eliminates doubts about whether background data persisted.

---

## 4. Cinematic View Choreographies: Treating UI Like a Movie

As articulated in Video 10, the definitive differentiator in modern UI engineering is treating screen transitions as **continuous cinematic sequences** rather than disjointed slide shows.

### 4.1 The Lateral View Transition with 35% Depth Parallax
```
View Transition Timeline (duration: 300ms, ease: [0.32, 0.72, 0, 1])

Parent Screen A:  [ 0% offset ] ---------------> [ Translates Left to -35% ]
                                                  (Dimmable overlay: 0% -> 30%)
Sub-Screen B:     [ +100% (Offscreen Right) ] -> [ Translates Left to 0% ]
```
* **Formula:** When opening a sub-screen, the active screen moves left by **$-35\%$** while the new screen enters from $+100\%$. The depth differential mimics physical layers sliding over one another.

### 4.2 Bottom Sheet Zoom & Dim Choreography
* When a bottom sheet opens:
  1. Sheet translates upward from `translate-y-full` to `translate-y-0` ($350\text{ms}$).
  2. Main viewport background scales down from $1.0 \to 0.95$ with `rounded-2xl` corners applied.
  3. Canvas dim overlay fades from `opacity-0` to `opacity-60`.

### 4.3 Hero-to-Sticky-Header Morph
* On operational detail screens with a large hero status card:
  1. As the user scrolls down, the hero card slides upward.
  2. At the threshold where the hero exits the viewport, the entity title and status badge seamlessly cross-fade into a compact, sticky top navigation bar ($44\text{px}$ height).
  3. The user never loses operational context, regardless of scroll depth.
