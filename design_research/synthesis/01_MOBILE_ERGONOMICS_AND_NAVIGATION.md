# Dispatch Mobile Design System: Mobile Ergonomics & Navigation Architecture

> **Document ID:** `DISPATCH-SYS-01`  
> **Source Material:** Videos 01 (`Everything you need to know about Mobile App UI's`), 06 (`Top UI/UX Design Tips - Bottom Navigation Bar`), 07 (`Top 5 Advanced UX/UI Design Tips`), 08 (`Every UI/UX Concept Explained in Under 10 Minutes`).  
> **Focus:** Physical ergonomics, biomechanical thumb zones, bottom navigation engineering, sheet vs. page modal flows, gesture mechanics, and single-direction layout rules.

---

## 1. Physical Biomechanics & Ergonomics

Mobile interaction is constrained by hand anatomy and screen physics. Unlike desktop environments with high-precision mouse pointers, mobile interfaces are operated by variable finger pads under dynamic real-world conditions (walking, one-handed grip, divided attention).

```
+------------------------------------------+  ^
|              UNREACHABLE ZONE            |  |
|       (Requires second hand or grip shift)|  | ~25%
+------------------------------------------+  v
|                 OK ZONE                  |  ^
|       (Reach with thumb stretch)         |  | ~35%
+------------------------------------------+  v
|              NATURAL THUMB ZONE          |  ^
|       (Effortless 1-handed arc sweep)    |  | ~40%
|       [Bottom Navigation & Prime CTAs]   |  |
+------------------------------------------+  v
```

### 1.1 The Thumb Arc & Zone Physics
* **Natural Thumb Zone (Bottom 40%):** Effortless sweep zone for one-handed operation. High-frequency actions, critical primary CTAs, search triggers, and the primary navigation bar must reside here.
* **OK Zone (Middle 35%):** Accessible with thumb extension. Ideal for secondary data exploration, scrollable feed content, and reading.
* **Unreachable Zone (Top 25%):** Requires user to either shift grip or bring in a second hand. Destructive actions, rare global settings, and non-blocking display titles live here. Never place high-frequency interactive triggers in top corners without bottom-accessible alternatives.

### 1.2 Mathematical Tap Target Formulas
* **Minimum Touch Target:** **$44\text{px} \times 44\text{px}$** (iOS Human Interface Guidelines) / **$48\text{px} \times 48\text{px}$** (Android Material Design).
  * **Anatomy Justification:** The average human thumb contact patch measures between $10\text{mm}$ and $12\text{mm}$, translating to roughly $44\text{px}\text{--}48\text{px}$ on standard retina displays.
  * **The Visual vs. Hitbox Rule:** A visual icon may be $24\text{px} \times 24\text{px}$, but its interactive bounding container (`hit-target`) must remain $\ge 44\text{px} \times 44\text{px}$ via transparent padding (`p-2.5` or `min-w-[44px] min-h-[44px]`).
  * **Hitbox Spacing:** Interactive hitboxes must maintain at least $8\text{px}$ of separation to prevent fat-finger mistaps.

---

## 2. Bottom Navigation Bar Engineering

The bottom navigation bar is the foundational backbone of mobile usability and represents the top-level structural taxonomy of the application.

```
+-------------------------------------------------------------------------+
| [Top Content Area: Canvas Layer 0]                                      |
+-------------------------------------------------------------------------+
| [1px Border Separation: border-t border-white/10]                       |
|  +-------------------------------------------------------------------+  |
|  |  [Tab 1]      [Tab 2]      [Center CTA]      [Tab 3]      [Tab 4] |  |
|  |   24px         24px           48px            24px         24px   |  |
|  |   11px         11px          (Floating)       11px         11px   |  |
|  +-------------------------------------------------------------------+  |
| [Home Indicator Safe Area: pb-[env(safe-area-inset-bottom, 34px)]]      |
+-------------------------------------------------------------------------+
```

### 2.1 Tab Count & Density Constraints
* **Golden Rule:** **3 to 5 tabs maximum** (3 or 4 is optimal; 5 is the hard physiological limit).
* **Choice Paralysis:** Exceeding 5 tabs shrinks individual tap targets below $44\text{px}$, triggers cognitive choice paralysis, and causes mistaps.
* **Jakob's Law Application:** Users spend 90% of their time on other applications. Conforming to standard bottom navigation conventions maintains intuitive muscle memory.

### 2.2 Architectural Taxonomy: What Belongs vs. What Never Belongs
| Location | Included Elements (High Frequency) | Strictly Prohibited Elements (Low Frequency / Displaced) |
| :--- | :--- | :--- |
| **Bottom Navigation** | • Core Feed / Dashboard (`Home`)<br>• Search / Discovery (`Search`)<br>• High-Priority Action (`New / Dispatch`)<br>• Urgent Comms (`Activity / Inbox`)<br>• User Workspace (`Profile / Fleet`) | • Legal pages, Privacy Policies, Terms<br>• Settings, Help, FAQs<br>• Log out buttons<br>• Brand logos / Decorative wordmarks<br>• Browser-style Back / Forward arrows |
| **Top Bar / Settings** | • Ambient context title<br>• Global profile / status chip<br>• Contextual overflow more (`...`) | • Primary action buttons intended for frequent execution<br>• Search input without a bottom thumb trigger |

### 2.3 Exact Physical Dimensions & Metrics
* **Total Bar Height:** $64\text{px}$ base content height $+$ Home Indicator offset ($\sim 34\text{px}$) $= \mathbf{98\text{px}}$ total footprint.
* **Safe Zone Insets:** Always incorporate `pb-[env(safe-area-inset-bottom, 34px)]`. Overlapping the $34\text{px}$ home indicator causes accidental app dismissals when users attempt to tap navigation icons.
* **Icon Dimensions:** $24\text{px} \times 24\text{px}$ with a uniform stroke width of $1.5\text{px}\text{--}2.0\text{px}$.
* **Label Typography:** $10\text{px}\text{--}12\text{px}$ sans-serif font size, single-line only.
  * Below $10\text{px}$ fails accessibility and readability.
  * Above $12\text{px}$ creates visual noise and competes with primary content.
  * Multi-line labels are strictly forbidden; truncate or simplify copy (e.g., use "History", not "Trip History").

### 2.4 Active vs. Inactive State Differentiation
Changing text color alone is insufficient for rapid identification. The bottom navigation bar must implement a **dual-visual cue system**:
1. **Icon Morphology:** Transition from outline icon ($1.5\text{px}$ stroke) on inactive state to filled/solid icon on active state.
2. **Color & Weight Shift:** Active label becomes bolder (`font-semibold`) and shifts to the primary brand accent (or high-contrast bright neutral); inactive elements use muted opacity ($60\%\text{--}70\%$) with a minimum $3:1$ WCAG contrast ratio against the navbar surface.
3. **Sliding Indicator / Capsule:** Optional subtle animated pill indicator or sliding underline transitioning horizontally between active tabs.

### 2.5 Surface Separation from Canvas
A major junior design flaw is letting bottom navigation bleed directly into background content without visual hierarchy. Enforce one of three separation techniques:
1. **Structural Stroke:** $1\text{px}$ border along the top edge (`border-t border-white/10` in dark mode, `border-slate-200` in light mode).
2. **Elevated Surface Contrast:** Navbar background set to Layer 1 Surface (`bg-slate-900/90` with backdrop blur `backdrop-blur-md`) against Layer 0 Canvas (`bg-[#0B0F17]`).
3. **Soft Elevation Shadow:** Directional top shadow (`shadow-[0_-4px_20px_rgba(0,0,0,0.25)]`).

---

## 3. The Central Action Button (Floating CTA)

When an app has a primary generative action (e.g., creating a dispatch order, composing a message, creating an item), break out the center button:
* **Dimensions:** $48\text{px} \times 48\text{px}$ to $54\text{px} \times 54\text{px}$ circular or rounded-squircle button.
* **Elevation:** Offset upward by $-12\text{px}\text{--}-16\text{px}$ above the navbar baseline.
* **Interaction:** Tap directly reveals a contextual bottom sheet or modal input, maintaining thumb accessibility.

---

## 4. Modal Hierarchy: Bottom Sheets vs. Full-Page Views

Space scarcity on mobile dictates the core architectural philosophy: **"One Screen Does One Thing."** Cluttering a single screen with editor, templates, tasks, and scratchpads results in cognitive overload.

```
                    +--------------------------------+
                    |        User Context Trigger    |
                    +--------------------------------+
                                    |
                    Is action a temporary modifier,
                    template picker, or filter?
                                   / \
                                  /   \
                             YES /     \ NO
                                v       v
              +--------------------+   +---------------------+
              |    Bottom Sheet    |   | Full-Page Navigation |
              |  (In-context flow) |   |  (Dedicated focus)  |
              +--------------------+   +---------------------+
              | • Background scales|   | • Unmounts editor   |
              |   down 35% depth   |   | • Replaces navbar   |
              | • Swipe down exits |   | • Swipe-right back  |
              +--------------------+   +---------------------+
```

### 4.1 Bottom Sheet Mechanics
* **When to Use:** Selecting filters, choosing status options, secondary confirmation modals, template selection, and contextual inspection.
* **Visual Framing:** Slides up from the bottom boundary. Keeps the underlying context visible.
* **Depth Animation (Zoom & Dim):**
  * When the bottom sheet ascends, the background screen scales down to $\sim 94\%\text{--}96\%$ and receives a backdrop dim overlay (`bg-black/50` or `backdrop-blur-sm`).
  * Dismissal via swipe-down gesture restores the canvas scale and opacity seamlessly.
* **Affordance Handle:** Prominent top handle bar ($36\text{px} \times 4\text{px}$, `rounded-full`, muted gray) signaling drag capability.

### 4.2 Full-Page Transitions
* **When to Use:** Dedicated complex workflows (e.g., comprehensive multi-step dispatch creation, detailed route inspection, global settings).
* **Dynamic Action Transformation:** When navigating to a full-page sub-view:
  * Hide the persistent bottom navigation bar.
  * Reveal view-specific bottom contextual actions (e.g., "Confirm Order • \$24.00" sticky bar).

---

## 5. Touch Gestures & Physical Feedback

Gestures elevate mobile interfaces from static web pages into tactile physical instruments.

### 5.1 Swipe Right to Go Back (Edge Parallax)
* **Mechanics:** Native swipe from the left screen boundary.
* **Parallax Formula:** While the active view translates right by $100\%$, the parent view underneath translates from $-35\%$ to $0\%$ with an opacity crossfade. This depth cue eliminates disorientation.

### 5.2 Long Press (The Mobile Right-Click)
* **Mechanics:** Press-and-hold trigger ($400\text{ms}\text{--}500\text{ms}$).
* **Visual Response:**
  * Background canvas is blurred (`backdrop-blur-md`).
  * Targeted component scales up slightly ($1.02\times\text{--}1.04\times$) with an elevated drop shadow.
  * Contextual action sheet or popover chip menu emerges directly adjacent to the thumb position.

### 5.3 Swipe Up to Search
* In information-dense views, an upward flick or downward pull in feed view summons an expanding search modal or highlights the global query input.

---

## 6. Layout Physics: The Single-Direction Rule

On desktop, expansive canvas width permits layouts expanding in **two dimensions simultaneously** (e.g., $3$ columns by $4$ rows of cards). On mobile, dual-axis expansion causes chaotic scrolling conflicts and cognitive overload.

### The Single-Direction Law:
> **For any individual screen section, choose exactly ONE movement axis:**
> 1. **Vertical Stack:** Cards and items stack top-to-bottom with single-direction page scroll.
> 2. **Horizontal Card Snap:** Cards scroll horizontally with card-snapping physics (`snap-x snap-mandatory`), remaining bounded within their vertical slot.
> 
> **Never combine multi-column grid expansion with variable horizontal and vertical scrolling within the same section.**

---

## 7. Dynamic Content Ergonomics: Sticky Bottom Action Bars

Users make purchasing or dispatch confirmation decisions only *after* reviewing content details down the page. 
* **The Anti-Pattern:** Placing the "Confirm / Buy" button exclusively at the bottom of a long scrollable form, forcing users to scroll all the way down, or stranding it at the top where it's unreachable.
* **The Pro Solution:** **Sticky Bottom Action Container**
  * Sticks to the bottom viewport boundary, elevated above the safe area.
  * Contains the mutable selector (e.g., quantity stepper, route toggle) directly paired with the primary CTA.
  * **Price / State Encapsulation:** Always embed calculated total value directly inside the CTA button label (e.g., `Dispatch Fleet • 3 Vehicles ($180)`). This eliminates cognitive uncertainty before tapping.
