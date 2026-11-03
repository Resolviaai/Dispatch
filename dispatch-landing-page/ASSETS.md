# Assets

| File | Purpose | Format | Source and limits |
| --- | --- | --- | --- |
| `public/assets/dispatch-sloth-master.png` | Approved original sloth mascot, reused in the hero, workflow note, and alpha note | PNG with transparency | Generated for this project and approved by the owner. Keep the source image; do not claim trademark or exclusivity rights beyond the project’s use. |
| `public/assets/hero-landscape.mp4` | Hero field above 760px | MP4 H.264, 1920×1080, 10.0 s, 240 frames at 24 fps, 1.16 MiB, silent | Rendered for Dispatch from `render/hero-field/main.js`. See "Hero field". |
| `public/assets/hero-portrait.mp4` | Hero field at 760px and below | MP4 H.264, 1080×1920, 10.0 s, 240 frames at 24 fps, 951 KiB, silent | Same scene, portrait light placement. |
| `public/assets/hero-landscape-poster.jpg` | Poster for the landscape loop | JPEG, 1920×1080, 48 KiB | Frame 0, so the poster and the first frame match. |
| `public/assets/hero-portrait-poster.jpg` | Poster for the portrait loop | JPEG, 540×960, 16 KiB | Frame 0. |
| `public/favicon.svg` | Small Dispatch wordmark mark for browser tabs | SVG | Authored locally from simple timeline-like bars and a point accent. |
| `render/hero-field/main.js` | Source of the hero field | Three.js + GLSL fragment shader | Authored for this project. Reproduce with `npm run dev`, then `render/hero-field/index.html?w=&h=` and step `window.__setTime(0…1)`. |
| `media-src/hero-source-klickpin.mp4` | Reference only. Not served, not imported, and not part of the build. | MP4, 720×1280, 10.0 s, 309 KiB, with audio | The third-party ambient loop that set the brief. It was obtained through Klickpin.com (a Pinterest downloader) and its rights were never cleared, so **nothing derived from it ships** — the shipped field is an independent render. Kept only so the provenance of the idea is traceable. |

## Hero field

An earlier version of this hero was a crop of the Klickpin clip. That clip is soft-focus at its own resolution — 720 px wide, 252 kbps, mean edge energy 0.485 — so cropping its centre band to 1600 px stretched it 2.2× and measured 0.207. No encode setting recovers that; the pixels simply are not there.

The shipped field is instead rendered at the size it is displayed:

- **Scene** — a deep evergreen wash with a light pool, two soft bodies drifting through it, and concentric hairline rings expanding from a single pale bead. The rings echo the site's concentric geometry; the bead is the same "minute leaving the hour" idea as `HeroArt.tsx`.
- **Colour** — held inside the Field Desk palette (`#0a0f0c` anchor through `#4a7551` into a `#c8e0a6` seam) rather than the clip's acidic spring green, which sat outside the token system.
- **Sharpness** — 0.591 mean edge energy at 1920×1080, 2.9× the cropped clip, with peak edges 6× stronger. The rings are true antialiased hairlines, not smeared.
- **Loop** — every oscillator is a function of the loop phase and the rings advance exactly one gap per pass, so frame 239 to frame 0 is indistinguishable from any other step (measured mean delta 0.4823 against a typical step of 0.4839). No ping-pong needed.
- **Banding** — the shader dithers at 0.0035 before encoding, which is what keeps the gradient from breaking up under h264.

Product UI panels are HTML/CSS illustrations in `src/App.tsx`, clearly labeled as illustrative. The hero field is the only motion asset on the page; `HeroArt.tsx` stays mounted underneath it as the `prefers-reduced-motion` and pre-paint fallback.
