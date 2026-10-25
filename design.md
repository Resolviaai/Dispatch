---
name: Dispatch Design System v3
source: dispatch-design-system-v3.md (full spec — read it before any large UI work)
themes: [dark (canonical), light (token swap)]
font: Inter (UI) + system mono for numbers
accent: "#2563EB" (both themes; hover "#1D4ED8")
tokens:
  canvas:      { dark: "#161616", light: "#FFFFFF", use: "App background, lowest layer" }
  surface:     { dark: "#1C1C1C", light: "#FFFFFF", use: "Cards, sheets, elevated content" }
  surface-2:   { dark: "#232323", light: "#F0F0F0", use: "Input wells, inactive chips, recessed rows" }
  surface-3:   { dark: "#282828", light: "#E5E5E5", use: "Pressed states, nested wells, deep recess" }
  border:      { dark: "#2E2E2E", light: "#E5E5E5", use: "Card borders, hairlines, dividers" }
  border-strong: { dark: "#3E3E3E", light: "#D4D4D4", use: "Focused inputs, emphasized dividers" }
  text:        { dark: "#F5F5F5", light: "#0F0F0F", use: "Headings, primary text, values" }
  text-2:      { dark: "#A3A3A3", light: "#606060", use: "Body text, labels" }
  text-3:      { dark: "#737373", light: "#909090", use: "Metadata, timestamps, placeholders" }
  success:     { dark: "#22C55E", light: "#16A34A", use: "Completed, published, healthy" }
  warning:     { dark: "#F59E0B", light: "#D97706", use: "Queued-with-issue, needs attention" }
  danger:      { dark: "#EF4444", light: "#DC2626", use: "Destructive, failed, recording only — never the brand" }
  info:        { dark: "#38BDF8", light: "#0284C7", use: "Processing, syncing" }
  overlay:     { dark: "rgba(0,0,0,0.6)", light: "rgba(0,0,0,0.4)", use: "Sheet/modal scrim" }
  shadow:      { dark: none, light: "0 1px 2px rgba(0,0,0,.06), 0 4px 12px rgba(0,0,0,.06)", use: "Light-mode elevation only" }
type:
  page-title: "18px / 600 / 120%"
  section-title: "16px / 600 / 120%"
  body: "14px / 400 / 140%  # never below 14 on mobile"
  metadata: "12px / 400 / 130%"
  mono: "12px / 500 / tabular-nums — durations, sizes, timestamps (right-aligned)"
  button: "14px / 600"
spacing: { grid: 4, page-margin: 16, card-padding: 16, section-gap: 32, radii: "card 16, inner 12/8, buttons 12, inputs 10, sheet-top 20, chips full" }
targets: { min: "44px tall", tabs: "44x64", chips: "32px tall / 13px text" }
---

# Dispatch Design System (summary)

When creating or modifying ANY UI in this repository, read and follow this file.
The full spec with rationale is `dispatch-design-system-v3.md`; this file is the
operational summary. Where the older `DESIGN_SYSTEM.md` (v2) disagrees, v3 wins.

## Laws

1. **Tokens only.** Never hardcode a hex in a component. All colors come from the
   CSS variables in `frontend/src/index.css` via the Tailwind names in
   `frontend/tailwind.config.js` (`canvas`, `surface`, `surface-2/3`, `border`,
   `text-main/secondary/muted`, `primary`, `success/warning/danger/info`).
   Legacy v2 class names (`bg-studio`, `bg-surface-100/200/300`, `studio-*`) are
   aliased to the same variables — new code must use the v3 names.
2. **Two themes, one system.** Dark is canonical; `[data-theme="light"]` swaps
   values. Switching lives in Settings → Appearance (device / dark / light) via
   `src/theme.ts` and recolours instantly. Never port a light value into dark:
   dark surfaces get LIGHTER as they elevate, light surfaces get darker/bordered.
3. **Typography:** 14px mobile body minimum, 12px metadata/mono only, max 4 sizes
   and 2 weights per screen. Numbers are `tabular-nums`, right-aligned in rows,
   mono for measurements. Ellipsis glyph `…`, non-breaking space before units.
4. **One hero per viewport:** exactly one accent (#2563EB) primary action;
   secondary actions are neutral recessed surfaces. Red is reserved for
   destructive actions and the recording indicator — never decoration, never brand.
5. **Status tints:** use the `.chip-success/.chip-warning/.chip-danger/.chip-info`
   utilities (and `text-status-*`) for tinted status surfaces — they are AA-safe in
   both themes. Do NOT put Tailwind alpha modifiers on semantic var tokens.
   (Alpha modifiers only work on literal-hex colors like `primary`.)
6. **44px minimum touch targets**, 8px separation, bottom nav above the
   home-indicator safe area. Chips 32px. Press feedback `active:scale-[0.98]`
   within 150ms on every interactive element, plus a visible `:focus-visible` ring.
7. **Single direction per mobile section** — vertical stack or horizontal snap row,
   never 2D grids. Cards are deliberate, not default; lists/dividers carry rows.
8. **Concentric radii** `R_inner = R_outer − padding`. Cards 16px, wells 12/8px.
9. **Icons:** Lucide only, 1.5–2px stroke, one style. Zero OS emojis in UI chrome.
10. **Editor exception:** the caption studio and studio workstation
    (`frontend/src/captions/*`, `frontend/src/studio/*`, `ColorPickerModal`) are
    deliberately NOT redesigned; they inherit v3 only through the shared tokens.
    Do not restyle them without the owner's explicit instruction.

## Known debt (do not "helpfully" fix without asking)

- The caption studio in LIGHT theme has not been hand-audited (dark is the daily
  driver); its hardcoded on-canvas colors (caption highlight `#FFE600`, glow
  `#00F0FF`) are caption-content colors, not app chrome.
- `frontend/src/screens/ProcessingScreen.tsx` and `AdminScreen.tsx` are unrouted
  legacy code kept for reference.
- Alpha modifiers cannot apply to CSS-var tokens (Tailwind limitation); tinted
  surfaces must use the `.chip-*` utilities.
