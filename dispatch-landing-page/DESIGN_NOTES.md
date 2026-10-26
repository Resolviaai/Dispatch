# Dispatch landing page — design notes

## Reference study

- **Clucky**: a friendly single-character mascot, generous pale canvas, direct headline, soft rounded details, and a playful but quiet voice. The lesson is to make one character memorable and let it carry small moments of warmth; Dispatch uses an original sloth rather than copying the rooster, poses, assets, or composition.
- **Black**: oversized type, strong contrast, and product imagery staged as part of a scroll narrative. Dispatch borrows the sense of scale and pacing, not its brand, UI, copy, or visual assets.
- **Outerspace**: editorial display typography, spacious framing, large moving objects, and clear shifts between sections. Dispatch translates this into small CSS motion and a pinned workflow preview, without importing its imagery or page structure.

## Page purpose and content guardrails

The page introduces Dispatch as an early creator workflow for turning longer talking sessions into short clips worth reviewing. It describes the current alpha as Android capture plus processing on a Windows PC companion, followed by creator review. It avoids performance promises, customer claims, usage metrics, broad platform-integration promises, public launch language, and claims that the service is fully cloud-hosted.

Primary action opens the public GitHub releases page; secondary links move to real page sections or the public repository. There is no pretend signup, waitlist, demo request, or contact form.

## Visual system

- Canvas: warm paper `#F4F1E9`; lighter panels `#FBF9F3`; ink `#252A26`.
- Supporting tones: quiet evergreen `#23483F`, soft teal `#557B72`, cocoa `#765140`, parchment `#E8DBC0`, and restrained orange `#D8764C`.
- Typography: system sans-serif for practical interface copy; Georgia for expressive editorial headlines. The page does not fetch remote fonts.
- Shape language: broad circles, rounded paper panels, timeline rules, and simple, tactile cards. One clear primary action per section.
- Mascot: relaxed, observant, patient, lightly witty. It never speaks for the user, claims an edit is correct, or implies work completed without review. Its tiny lines are “I’m keeping an eye on the timeline,” “I found a moment worth a look,” and “No rush. You’re the editor here.”
- The mascot has one approved master asset. Reuse it with size, crop, and gentle CSS movement rather than generating a drifting collection of poses.

## Interaction and motion

- Workflow preview uses three keyboard-accessible tabs; clicking or focusing a step updates the illustrative interface.
- Scroll position selects the most visible workflow step through one `IntersectionObserver`; no scroll-jacking or continuous scroll handler.
- The sloth floats gently at idle. `prefers-reduced-motion` turns that animation off and collapses transition durations.
- All content stays in normal document flow on phones. The preview becomes non-sticky and the page remains a single vertical stack.

## Responsive and accessibility rules

- One-dimensional page flow at phone widths, no horizontal scrolling, and 44px minimum interactive heights for navigation and controls.
- Semantic landmarks, skip link, descriptive headings, accessible tabs, visible keyboard focus, and meaningful alternative text.
- Illustrative UI is explicitly labeled as illustrative; decorative shapes are hidden from assistive technology.

## Source inspiration

The three public references were inspected on 2026-10-26: [tryclucky.com](https://tryclucky.com/), [az-black.webflow.io](https://az-black.webflow.io/), and [outerspace.com](https://www.outerspace.com/). This is a visual study only; no external site assets, code, copy, or brand identifiers are reused.
