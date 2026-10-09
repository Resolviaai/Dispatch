# Dispatch — Brand Guidelines & Identity Spec

> Autonomous Personal Content Engine · Brand Identity Guidelines

---

## 1. The Logo & Visual Metaphor

- **Brand Name**: Dispatch
- **Core Concept**: **Velocity D** — A muscular, architectural capital **D** with an embedded forward play / dispatch chevron in negative space.
- **Double-Read Signifier**:
  1. *Immediate Identification*: Reads unmistakably as the letter **D** (Dispatch).
  2. *Kinetic Conduit*: The interior counter forms a 30° isometric play / forward transmission arrow, symbolizing continuous camera footage being instantly captured, transcribed, and dispatched into viral content.
- **Geometry & Craft**: 100/100 production-readiness score under automated SVG auditing (`svg_audit.py`). Constructed with pure integer grid points, exact 30.0° diagonal angles, continuous Bézier curvature, and robust 44px+ stroke weights that never collapse at small display sizes.

---

## 2. Logo Configurations

| Variant | File (`branding/dist/`) | Primary Use Case |
|---|---|---|
| **Primary Symbol** | `dispatch-symbol-black.svg` | Standalone avatars, app launcher icons, toolbars |
| **White Symbol** | `dispatch-symbol-white.svg` | Dark canvas (`#070A0F`), video watermarks, dark terminals |
| **Brand Mono Symbol** | `dispatch-symbol-mono-2563eb.svg` | Marketing highlights, accent CTAs, single-color prints |
| **Small-Size Cut** | `dispatch-symbol-small.svg` | Micro displays (<24px): browser favicons, status bar icons |
| **Horizontal Lockup** | `dispatch-lockup-horizontal-black.svg` | Light theme headers, documentation, wide badges |
| **Horizontal Lockup (Dark)**| `dispatch-lockup-horizontal-white.svg` | Dark dashboard header, GitHub README banner |
| **App Icon Master** | `dispatch-symbol-app-icon.svg` | iOS & Android squircle tiles, PWA homescreen |

---

## 3. Clear Space & Proportions

- **Clear Space Rule**: Maintain an isolation zone equal to **`X`** on all four sides of the mark, where `X` is equal to the width of the play triangle base (48px on a 256px grid, or ~20% of the symbol dimension).
- **Proportions**:
  - Horizontal lockup symbol-to-wordmark height ratio: 1 : 1 (symbol height 168px matches cap-height optical prominence).
  - Wordmark tracking: Clean architectural letter-spacing (20px unit).

---

## 4. Minimum Sizes

| Configuration | Digital Minimum | Print Minimum | Notes |
|---|---|---|---|
| **Symbol** | `16 × 16 px` | `5 × 5 mm` | Use `favicon.ico` or `dispatch-symbol-small.svg` below 24px |
| **Horizontal Lockup** | `120 × 32 px` | `30 × 8 mm` | Ensure letter counters in 'P' and 'A' remain open |
| **Android App Icon** | `48 × 48 px` | N/A | Android mdpi baseline up to 192px xxxhdpi |

---

## 5. Color Architecture (4-Layer System)

Dispatch adheres to a strict 4-layer color architecture designed for high-performance developer and media tools:

| Layer | Role | HEX | RGB | Purpose |
|---|---|---|---|---|
| **Layer 0** | Deep Canvas | `#070A0F` | `(7, 10, 15)` | Primary dark workspace background |
| **Layer 1** | Surface / Card | `#131A26` | `(19, 26, 38)` | Elevated panels, cards, and modal containers |
| **Layer 2** | Recessed Container | `#0A0F1D` | `(10, 15, 29)` | Tables, terminals, code blocks, inputs |
| **Layer 3** | Primary Brand Accent | `#2563EB` | `(37, 99, 235)` | Primary brand color, focused states, CTAs |
| **Layer 3** | Sky Accent | `#38BDF8` | `(56, 189, 248)` | Subtitle highlights, secondary telemetry |
| **Semantic**| Recording Red | `#EF4444` | `(239, 68, 68)` | Active camera recording indicator |
| **Semantic**| Success Emerald | `#10B981` | `(16, 185, 129)` | Verified upload, pipeline completion |

---

## 6. Typography

- **Wordmark**: Custom geometric sans-serif construct with precision rectangular counters and unified 18px stroke stems.
- **UI & Dashboard Typeface**:
  - System stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`
  - Telemetry & Pipeline Badges: Monospace (`JetBrains Mono`, `Fira Code`, `ui-monospace`)

---

## 7. App Icon Implementations

### Android Launcher (`android/app/src/main/res/`)
- Vector Adaptive Icon: `drawable/ic_launcher_foreground.xml` + `@color/ic_launcher_background` (`#070A0F`).
- Raster Mipmaps:
  - `mipmap-mdpi`: 48×48 px (`ic_launcher.png`, `ic_launcher_round.png`)
  - `mipmap-hdpi`: 72×72 px
  - `mipmap-xhdpi`: 96×96 px
  - `mipmap-xxhdpi`: 144×144 px
  - `mipmap-xxxhdpi`: 192×192 px

### Web Dashboard & PWA (`dispatch/web/static/` & `frontend/public/`)
- `favicon.ico`: Multi-size container (16×16, 32×32, 48×48).
- `favicon.svg`: Scalable vector favicon.
- `apple-touch-icon.png`: 180×180 px iOS homescreen icon.
- `site.webmanifest`: PWA standalone launcher manifest with `theme_color: #070A0F`.

---

## 8. Usage Rules & Don'ts

- **Do NOT** stretch, distort, or skew the symbol or lockup.
- **Do NOT** rotate the mark or change the 30° angle of the play chevron.
- **Do NOT** add artificial neon drop shadows, outer glows, or bevels.
- **Do NOT** recolor the play arrow separately from the letter D—the mark is a unified silhouette with negative-space subtraction.
- **Do NOT** crowd the logo inside tight containers without adhering to the clear-space rule.
