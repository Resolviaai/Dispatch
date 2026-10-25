# Dispatch Design System v3 — Mobile-first, Dark + Light, YouTube-inspired

**Status:** Spec only. No repo code was changed. Applying this to the app needs Rohit's go-ahead.
**Applies to:** Dispatch mobile app first (POCO C65 / Android). The web studio follows the same tokens.
**Supersedes:** `DESIGN_SYSTEM.md` v2 in the Dispatch repo where v3 says otherwise (see §2.10 for the explicit override list).

## How this document was built

- **29 unique videos, 973 lessons, extracted line by line.** Every transcript the user uploaded was read cover to cover and every distinct lesson, rule, number, and mistake was pulled out as its own bullet — not summarized. The full line-by-line extractions live in `workspace/dispatch-design-system/extract/`:
  - `set1-dashboard-ui-points.md` — 448 points, 17 dashboard/UI-craft videos
  - `set2a-design-systems-1.md` — 158 points, 5 design-system foundation videos
  - `set2b-design-systems-2.md` — 211 points, AI workflows + atomic design
  - `set2c-design-systems-3.md` — 156 points, design.md, WWDC25, redesign, FEEL, AI-tells
  - `youtube-mobile-observation.md` — the YouTube observation below
- **YouTube mobile, observed live (25 Oct 2026, signed out, observe-only).** Layout, components, and both themes were recorded from the real mobile web app — not from memory. Hex values from that observation are visual approximations (the browser does not expose computed styles); the structure and patterns are exact.
- **The existing Dispatch system (v2) was read, not ignored.** v3 keeps what v2 got right, fixes where v2 contradicts the transcript evidence, and adds what v2 never had: a light theme and a fix-list for the drift between the spec and the shipped UI.
- **Two uploads were duplicates** (the "3 dashboard UI flaws" video appears twice; the "100 Tutorials" redesign appears in both sets). Their points are counted once.

---

# PART 1 — The combined learnings (all 29 videos, organized by theme)

## 1.1 Hierarchy is everything, and it is relative

- Users never read a screen. They land with a question already in mind and hunt for one answer. Design for scanning, not reading.
- Rank content by importance BEFORE designing, then enforce the ranking with size, position, and color. Most important goes higher and farther left. Values prominent, labels muted ("591 sales", not the metric title).
- Emphasis is not something an element has — it is the difference between it and its neighbors. To make one item stand out, change its surroundings, not the item. Never design a checkbox blue when off: color is the signal that something changed.
- Hierarchy is built on contrast: big vs. small, colorful vs. not. A different arrangement can still be right if the same ideas apply.
- Every screen must answer three questions immediately: Where am I? What can I do? Where can I go from here? If the screen itself does not orient the user, a tab label at the bottom comes too late.
- Do not open with a menu, and do not use a branding-only title. Menus are vague; branding titles get skipped, and users then miss the recommended content.
- Real interfaces have variation because content is not equally important. Uniform hierarchy — every section the same size, every widget the same weight — is an AI tell. A primary action must look different from a secondary action; a critical metric may deserve more space than everything around it.
- Hierarchy must survive change: longer text, other languages, larger text sizes. Never build a fixed layout that breaks when text grows.

## 1.2 Let the data drive the form

- Build the UI around the data you need to display. Finite sets of values (departments, statuses) become chips. Numbers right-align so digits align by place value. Time-delineated data becomes a timeline, not a time-sorted table.
- Dashboard color must come from the data itself, not decoration. In activity logs, a red icon in a chip draws the eye because the action is urgent.
- An avatar beats a name: the eye associates who did what far faster than reading a name. Information that needs zero reading (avatars, universal icons, red octagon = stop) parses instantly.
- Design for imperfect content: assume long titles, bright images, messy uploads. Truncate long names; guarantee icon contrast on any image with a container + outline; never design for one perfect screenshot.
- Coin your own metrics when they fit: streaks, readiness scores, "rings closed" — a metric users say out loud is a retention moat.
- Users always ask "Why? What changed? What caused this spike?" Show what to do about the data, not just what happened. A weekly recap + one insight next to the chart beats a chart alone.

## 1.3 Color: the four-layer system (60-30-10 is NOT a rule)

- **The 60-30-10 ratio is explicitly rejected by its own video for product UI** (one real product is ~90% black, 8% white, 2% red). Do not encode it. Use the four-layer system instead:
  - **Layer 1, neutral foundation:** generally four background layers, at least one stroke (often two), about three text variants. Landing pages: 3–5 neutrals. Product UI: four background layers + strokes + text variants, ignoring charts/hover/interactivity.
  - **Layer 2, functional accent:** never a single color — a scale. Use 500/600 as the main color, 700 on hover, 400/500 for links. Products either stay neutral (Vercel) or own one accent (Linear's blue-purple, Supabase's green).
  - **Layer 3, semantic communication:** colors carry meaning, so you must break your own system for them. Destructive is always red, even if the brand is purple. A neutral-only chart is lame; a brand-ramp-only chart looks too similar across series — charts need a full spectrum.
  - **Layer 4, theming:** for every neutral, drop lightness by 0.03 and raise chroma by 0.02, then shift hue — the OKLCH trick (set lightness + chroma, step hue by ~25–30 per chart color) gives equal perceived brightness across hues. Bright greens look more neon than bright blues; OKLCH fixes that perceptually.
- **Dark mode rules change.** Dark colors look more similar, so they need more distance: light palettes have ~2% white between steps, dark needs 4–6%. Surfaces always get lighter as they elevate — raised cards need a lighter color or just a border. Dark accent: use 300/400 as primary, hover 400/500; dim text, brighten borders.
- **Light mode is flexible:** dark background + lighter cards (Vercel), light background + darker cards (Notion), or monochromatic layers (Supabase) all work. Washed-out cards: do not reach for a thin black border — use ~85% white to define the edge. Buttons: the more important, the darker — ghost to black-with-white-text; most multi-purpose buttons sit at 90–95% white. Text: darkest reserved for important headings at ~11% white; most text 15–20% white; subtext 30–40% white.
- **Dark-mode depth without shadows:** dark mode has no shadows like light mode, so create depth by making the card lighter than the background. A light border creates too much contrast — lower it. Dim dark-mode chips (saturation + brightness down) and flip the text treatment.
- **Light-mode shadows:** most are too strong — reduce opacity, increase blur. Cards need less shadow; things above other content (popovers) need stronger shadows. Match shadow color to the background behind it; never pure gray/black shadows on colored backgrounds. If the shadow is the first thing you notice, it is wrong.
- **Semantic meanings (use color for purpose, not decoration):** blue = trust, red = danger/urgency, green = success, yellow = warning. Start from one primary (brand) color, lighten it for backgrounds, darken it for text — that is already half a color ramp.
- **Colors must come from the product's personality and context**, not from AI's idea of modern. A fintech dashboard, a productivity app, and a dev tool must not share the same visual identity by default.

## 1.4 Typography: one family, few sizes, real numbers

- One font family is the safe default in almost every case; create hierarchy with size, weight, color, and line height. (Exception: a deliberate serif + sans pairing for brand personality, e.g. a printed-menu feel — never accidental mixing.)
- **Mobile base body text must never be smaller than 14–16px.** Phones are held at 25–35 cm, closer than desktop monitors, so mobile type runs LARGER than desktop dashboards (which can afford 12–14px). Dashboard type caps at ~24px because of information density.
- **Operational screens: max 4 font sizes, 2 font weights.** Landing pages: max 6 sizes, but with a large range.
- Headline hack: tighten letter spacing −2% to −3% and drop line height to 110–120% — large text instantly looks professional. Small uppercase labels need extra letter spacing so they breathe.
- Numbers: always `tabular-nums`, right-aligned, mono where they are measurements (timestamps, file sizes, percentages). Never three periods `...` — use the ellipsis glyph `…`. Non-breaking space before units (`42 s`, `18.4 MB`).
- Headlines attract attention, paragraphs support understanding: increase paragraph line height and slightly reduce body contrast so text scans easier and the page feels calmer.
- Icons: match icon size to the font's line height (e.g. 24px line height → 24px icons), then tighten the text. Most icons ship too large.
- Type styles must survive Dynamic Type and long text. Prefer system text styles where the platform offers them; define type exactly (weight 600 only, in the cal.com example) with nothing left to interpretation.

## 1.5 Layout, spacing, and edges

- **Mobile: one physical axis per section.** Either a vertical card stack OR a horizontal snap carousel. Multi-axis 2D dashboard grids on mobile are prohibited. (This is v2's Single-Direction Law, confirmed by every mobile video.)
- **The 4-point grid:** every distance is a multiple of 4 (splitting in half always works, which creates consistency). 8-point spacing for larger rhythms. Never random values like 11px, 19px, 25px.
- **Concentric radii:** `R_inner = R_outer − padding`. Card 16px radius + 16px padding → inner 0–8px; card 12px + 8px padding → inner 4px; card 16px + 8px padding → inner 8px.
- **Section spacing ~32px** between distinct blocks; group elements that belong together (announcement with text, text with subtext) — grouping is itself hierarchy. Content margins consistent across the screen (e.g. everything starts 24px from the left) — misalignment is felt as untrustworthy even when it is not consciously noticed.
- **Edges rule (compact UIs):** everything borders on at least two edges. Cards hand you four edges for free and resize easily — but nobody actually builds just a card. Anti-pattern: containers inside cards inside containers until you have borders on borders and three radii stacked. Alternative: dissolve cards into the interface with a single line — the column edge does the card edge's job, rows stack, sections group content, chips and icons do the reading, defaults go gray so non-defaults catch the eye.
- **Cards are a deliberate pattern, not a default.** Card-everything (cards inside cards, same huge radius) is an AI tell. Create hierarchy through spacing, typography, alignment, dividers, scale, and layout instead. Container test: remove some containers and see if you actually miss them.
- **Lists beat grids for scanning.** A list is flexible, familiar, takes less vertical space than images, and fits more items on screen. Grids take too much space for two items and handle long text badly. For large image groups use a collection with consistent spacing and minimal text on items.
- **Most dashboard functionality is not on the page** — it lives in modals, popovers, and new pages. Popover = simple context, non-blocking, click away free. Modal = complex context, blocking, user must create or cancel — and since the page is hidden during a modal change, a toast confirming the change is good practice. Permanent or very large context = a new page, and then a back button or breadcrumb is required.
- **The four dashboard components:** lists/tables, cards, user input, tabs. Master those and you can build virtually any dashboard page. A good list = separation by space, lines/dividers, or color. A good table = functionality: search, filter, sort — display alone is half the job.

## 1.6 Components: states, tabs, nav, filters, inputs

- **Every button needs 4 states (default, hover, active/pressed, disabled) plus loading.** Every input needs focus, error (red border + message), and warning states. When a user does anything, there must be a response.
- **Button taxonomy:** filled = primary, stroked = secondary, no stroke = tertiary; each with and without icon. Leading icon = before text, trailing = after. Too many buttons on one screen destroys hierarchy — with too many actions, use a drawer, chips, or icon buttons. Never show "edit / save / place order" as three buttons together. Never two default buttons side by side.
- **One hero CTA per viewport.** Every screen has exactly one primary glowing button; secondary actions stay neutral and recessed. Never competing colored buttons side by side.
- **Tabs are for navigation, not actions.** Move primary actions (e.g. Add) inside the section where they are used. Each extra tab is one more decision; 3–5 tabs max, 3–4 ideal. Tab labels + icons must communicate meaning without interaction. Merging duplicate screens beats adding tabs.
- **Bottom nav (mobile):** prime thumb territory. Active tab = dual cue (brand-colored bold label + filled icon + dot). Inactive = outline icon + muted text. Min hitbox 44px tall, 64px wide per tab. Never put back buttons, logos, or search bars in the bottom nav. Sit strictly above the home-indicator safe area (34px + 8px).
- **Thumb zone:** key elements (buttons, nav, CTAs) must sit where the thumb reaches without grip adjustment. Most-used features must be easiest to reach.
- **Filters must help users decide, not just hide.** Visible chips beat buried dropdowns (8 tappable chips vs. 17 options behind two dropdowns nobody finds). Filters in a sheet over the results keep context; everything in one scroll lets users balance trade-offs. The apply button must state the result count ("Show 41 homes") before tapping. Show counts per option before choosing (3 bedrooms → 459, 4 → 229) so users see what strictness costs. Steppers are for nudging known numbers (basket quantity), not for choosing from visible options — show all options, tap directly. "+" must mean "or more" unless exactly-N stays separately choosable. A histogram behind a price slider shows distribution, so users see whether stretching the budget opens up options. Range selection (tap 2, then 4) beats single values. Making the interface look simpler is not the same as making the decision easier.
- **Chips:** finite sets become chips; chips also categorize content and give users functionality to swap tags that would otherwise hide behind a menu. Selected chip inverts (YouTube: black bg/white text vs. grey bg/black text).
- **Inputs:** sidebar links are ghost buttons (no background until hover) — isolate one, center it, and you have a perfect standalone button. Button padding guideline: width ≈ double the height. Search bars: collapse to the magnifier icon, expand on tap — large permanent search bars get in the way.
- **Tooltips are not optional.** Assume users will not understand all icons or ambiguous labels. Delayed tooltips (1000ms hover) explain icons where space is too tight to label everything.
- **Icons over images must survive any image.** Icons sitting directly on an image are only visible because that image is dark enough. Fix: subtle container behind the icons + enough contrast + thin outline, so they read on dark, bright, busy, and clean shots alike. Same for save icons on bright listing images (circle behind).
- **Place information where it is needed:** rating next to the title (trust signal at the decision point), price early (what it is, how good, how much — in that order), quantity selector next to the CTA, total price inside the CTA button (removes uncertainty before clicking), unit inside the selector. Never put "1 kg" in a title when the selector can change it.
- **Badges scan fast or not at all:** "20% off" alone beats "20% off discount" + icon. Redundant labels ("price" before a currency number) get removed.
- **Sticky context wins:** scrollable card over the image, title transitions into sticky top nav, sticky bottom action area — users decide at any scroll depth, not just at the top. Predefined options from real data (500g / 1kg / 2kg) beat repeated selector taps.

## 1.7 Motion, micro-interactions, and feel

- **Tactile immediacy:** every interactive control reacts instantly — press compression `scale(0.98)` in 150ms (cubic-bezier(0.16, 1, 0.3, 1)), under 16ms perceived. Buttons get smaller when clicked; text slides up on hover (mask trick, not random hover colors).
- **Springs, not fades:** name-tag hover pop uses smart animate, 500ms, stiffness 636, damping 24 — the easing IS the difference. Active-word popovers: 100–120ms springs. Entrance animations: stars rotate-and-pop, balloons fly in then bob slowly, calendars move-and-rotate — robotic fade-ins are no fun. Easing makes motion natural; parallax in large margins adds life without layered backgrounds.
- **Animate page changes.** Default tab switches are instant and forgettable; sliding pages over, repeated 100 times across an app, add up to a premium feel. Break complex interactions into sub-animations (send button rotates into a checkmark, background expands from the mic, text fades with a spring) — never one-shot prompt AI for a complex interaction; describe each sub-animation in plain English.
- **Haptics, varied by context:** light haptics for high-frequency actions (many taps), heavier for tab changes. Done right, heavy haptic use is satisfying, not annoying — if people tap around for fun, that is a good sign. (Platform default apps skip this at billion-user scale for battery/accessibility; an indie app can choose differently.)
- **Micro-interactions are feedback stepped up:** a copy button with hover/click states still does not tell you it copied — a chip sliding up to confirm does. Toasts: slide-up entry (150ms ease-out), spinner → checkmark transition, auto-dismiss, never blocking the workspace. Linear-style smooth progress bars keep forms engaging.
- **Icon style is a feel decision:** thin minimal lines vs. filled heavy icons vs. complicated multi-line icons each send a different message. Switching a tab bar from thin to thick icons changes the whole feel. Never mix icon styles — except to show state: thin inactive, filled active (a depth cue Apple does not use; Apple only changes color).
- **Motion principles:** direct the eye and the focal point. On scroll, slide elements off-screen and blur text while the new section slides over the top. Repeat animation themes across sections (pull-away on scroll, gradient transitions) — animation is a visual identity, not one-offs. Buttons should almost always have a small animation; scroll-jacking almost never.
- **"Load more" beats infinite scroll:** it gives control and, crucially, lets users actually reach the footer.
- **Taste is built by exposure:** browse real app screenshots a few minutes a day (Mobbin), follow designers whose work you like, bookmark everything. Use reference search to answer specific questions ("show me tab bars") instead of general inspiration.

## 1.8 Psychology, empty states, loading, and trust

- **Smart defaults (70–90% retention):** pre-select the most common configuration. Eliminates decision fatigue.
- **Goal gradient:** start pipelines visually at ≥20%, not 0%, to motivate completion.
- **Reciprocity:** deliver value upfront (preview, live subtitles) before demanding actions.
- **Endowment:** once users customize or touch the canvas, they value the result significantly more.
- **Loss aversion:** frame warnings around loss ("unuploaded video stays safely on device until sync is verified").
- **Anchor contrast:** compare speeds/metrics against baselines.
- **Progressive disclosure is sequencing, not hiding:** start with a single tooltip pointing at the most important action, then a second tooltip or a corner checklist. Never a 6-bullet modal at login — instantly forgotten on dismiss. Onboarding is not about keeping it short; it is about never overwhelming.
- **The spectrum of explicitness:** always-visible global share = high; hover-to-copy a cell = low; swipe-revealed secondary actions = middle. Choose by space tightness and how central the action is. Hiding as a clutter band-aid is bad; hiding as sequenced disclosure of secondary actions is good.
- **Empty states are opportunities, never dead ends.** A bare "no projects" screen is a roadblock. A good one: headline offering the solution, illustration, actionable tips, and a CTA button. Custom illustrations/mascots for empty states (ghost searching, sleeping, drinking coffee) beat bare text — start from a commissioned base illustration, generate variations with AI.
- **Loading states must name the step.** A 3-second silent sync feels like forever; naming the exact step buys patience; real progress beats a generic spinner. A silent 3-second anything is a failure.
- **"Less is more" is about helping the user at that exact moment, not element count.** A bare "no data" screen and a silent sync are failures of less, not virtues. Sometimes users want lots of information (sliders, checkboxes, inputs for an informed decision); sometimes one button. Simplicity ≠ few elements; confusion ≠ many elements. Judge by whether the user is helped right now.
- **Trust signals:** uniform, consistent apps feel trustworthy; non-uniform apps feel untrustworthy. Real data, realistic edge cases, real imagery, meaningful labels, and product-specific content make a design feel intentional. Generic copy ("unlock your potential"), fake activity feeds, suspiciously round stats ("10K+ users, 99.9% uptime"), and testimonial "Sarah Chen, product lead at TechCorp" destroy trust.
- **Friendly copy beats corporate copy:** "we sweat the details" beats "we take pride in our attention to detail." Text is ~80% of most interfaces — make the text itself interesting (motion, micro-interactions), not just the chrome.

## 1.9 How to build and run a design system (the craft itself)

- **Declare before you design.** Components, guidelines, and rules come BEFORE a single screen. Never design screens from hunches. Beginner-sufficient = 5–6 things: palette, typography, atomic components (inputs, dropdowns, date picker, pop-ups, buttons). Big-company docs are not the starting point.
- **Build order:** palette (primitives: grays, brand, semantic) → type styles → shadows → buttons (primary/secondary/tertiary) → inputs (incl. error, dropdown, radio) → pop-ups. Mobile and web get DIFFERENT type styles and component sets — one system does not fit every gadget.
- **Atomic design (Brad Frost):** atoms (labels, inputs, buttons) → molecules (input + button = field) → organisms (groups) → templates → pages. Stop the atomic approach at roughly the organism level; beyond that is design-system work (variants documented, collaboration). A button without text/icon is just rectangle + text. Icons: uniform size via Center/Center constraints (never Scale); do not componentize at wireframe stage — you will keep breaking components. Naming conventions matter only when the product is large.
- **Tokens need when-to-use, or they are useless.** Raw variable lists (surface vs. surface-subtle vs. surface-sunken vs. surface-raised) cannot be applied by anyone, human or AI. Every token table needs: name, light value, dark value, one-line "when to use" description — the description is the most important column. Use only the mapped/semantic collection (variables actually applied to components), not every primitive collection.
- **One theme first, second theme by token duplication.** Start with ONE theme (light or dark). Dark mode = duplicate and change ONLY the tokens; keep the per-mode mapping explicit so switching has zero discrepancies. (Dispatch note: the user has now asked for both themes — so v3 defines dark first, then derives light by token swap, exactly this way.)
- **Split docs per component grouping** (form / navigation / data display), never one giant .md — AI hallucinates and miscounts across giant files, and may miss components entirely without explicit mentions. Use exact component and property names; flag gaps. Mention every component even if not fully documented.
- **Rules + anti-patterns + technical debt, written down.** E.g. ">4 options → checkbox, <4 → radio"; "never two default buttons together"; legacy items that cannot be fixed (a button failing AA contrast, a $2M fix) must be documented explicitly or AI will "helpfully" try to fix them. Reasons (no budget, politics) stated plainly.
- **design.md pattern (Google/Stitch spec):** YAML front matter with machine-readable tokens + plain-English body explaining usage. Keep it lightweight and high-level; point to per-component metadata files (Storybook) for full contracts — never restate every contract in the summary. Wire it into agent instructions: "When creating or modifying any UI, read and follow design.md" in both CLAUDE.md and AGENTS.md. Validate with a lint command that self-corrects (e.g. 2XL → XXL). Colors as hex. Typography defined exactly, nothing left to interpretation. Brand feeling in words ("calm, trustworthy, precise"), not just values.
- **Documentation structure per component (same for all):** overview → anatomy → usage guidelines → variants (states, status, disabled, full mix) → preview → properties → dos and don'ts → accessibility. Inconsistent docs confuse developers.
- **AI workflow that actually works:** never ask AI for the whole system at once (mistakes, shortcuts, token burn). Tokens → building blocks → components, one step at a time, double-checking against the reference every time (AI still gets colors/spacing wrong). Iterate in fast tools for options (Grok/ChatGPT: options over accuracy) → prototype + self-QA in a browser-driving tool (Antigravity: builds, screenshots, fixes its own output) → connect to the design system in the most guideline-accurate tool (Claude). No single tool one-prompts world-class UX + perfect system connection + perfect code. Platform ≠ model (Grok platform ≠ Grok model inside Cursor). Canvas AI tools burn tokens at insane rates (one Paper dashboard: 155,400 tokens before iteration). Skills beat re-prompting: aspirational skills (treatments you like) vs. design-system skills (rules that must be followed); toggle per project so brand rules never leak across clients.
- **AI cannot build a true design system.** Styles + variables + a button = 1/1000th of a system. "10 hours building it, 100 hours figuring out what it built — or 3 hours learning to build one." If you did not build it, you cannot answer why a variant exists when a developer asks. Learn fundamentals before automation (manual gear before automatic).
- **The counter-argument, kept honestly:** one video argues the whole design-system industry is collapsing — when the generator becomes the system, nobody maintains anything, and the "everyone needs a system" push was partly a Figma seat-selling trick. For Dispatch the resolution is: the system exists to make the app consistent and to make AI-assisted builds land on-brand — a lightweight, enforced spec, not a 1,800-screen bureaucracy. Dashboards and systems were never the goal; they were a bridge. Users want meaning, a next step, and clarity — not more data.

## 1.10 The AI-slop audit (how to spot — and avoid — generated-looking UI)

- **Color tells:** indigo-purple gradients, dark backgrounds, giant blurred glows, one gradient-filled text, dot patterns, background grids — AI shorthand for "modern/technical."
- **Card-everything:** cards for features, stats, navigation, settings, cards inside cards, same huge radius everywhere, Lucide icons in little tinted rounded squares.
- **Default compositions:** websites = pill badge + giant centered headline + gray subtitle + two buttons. Dashboards = 3–4 stat cards + giant chart + another card grid. Mobile apps = greeting + giant summary card + quick actions row + recent activity list.
- **Uniform hierarchy:** every section same headline/subtitle/three-column grid; every widget same size; every settings section same space. Real content is not equally important.
- **Decorative-but-useless:** glassmorphism, glowing borders, pulsing dots, excessive shadows, hover-scaling cards, random gradients, floating shapes, unnecessary animations, everything fading up on entry. Element test: if the only answer to "what is this doing?" is "it looks cool," start simplifying.
- **Generic content + fake data:** "unlock your potential," meaningless chart data, placeholder notifications, perfectly clean empty states that represent no real use.
- **The swap-logo test:** if another company's logo still works on the design, it is the average solution, not the specific one. Nothing specific to the actual product is the biggest tell.
- **None of these patterns are automatically bad** — rounded cards, consistent icon sets, even purple gradients are fine when they are decisions, not defaults. The job is deciding when the obvious solution is right and when to push it somewhere specific.
- **Fix by reference:** study 5–10 real examples of the same flow (e.g. onboarding) — what they prioritize, how many steps, how they handle permissions, where they differ — then decide for your product. Never copy one screen; understand the patterns, then choose.
- **Vibe-coding tells in SaaS:** emojis instead of professional icons (Notion excepted); AI-chosen bright colors that do not work together; the same 4 KPIs repeated three times; irrelevant sidebar content on pages where it does not belong; gradient profile circles with letters (swap for an account card); dead cards that do nothing; stray links that should live in a popover; sparse flyouts wasting space where a modal fits; pricing plans that make no sense ("standard less than hobby"), five plans where one must go, plan name bigger than the price (invert: price is what everyone cares about).
- **Dashboards that stick:** not the prettiest — the ones that knew what to leave out, showed what to do about the data, and had one distinctive idea (live-visitor dots, a birthday cake in a donut chart, a game when all bills are paid, screen-time as swipeable stories).

## 1.11 Contradictions across the videos — and Dispatch's resolutions

Every contradiction found by the extractors is kept here with both sides. The right column is the decision v3 takes.

| # | Tension | Side A | Side B | Dispatch resolution |
|---|---------|--------|--------|---------------------|
| 1 | Systems essential vs. obsolete | Declare rules before any screen; uniformity → trust | AI generates/maintains UI; maintaining a system is unnecessary | Keep a lightweight enforced spec (this document). It exists so AI builds land on-brand, not as bureaucracy. |
| 2 | One theme first vs. both themes | Beginners: one theme only | Duplicate tokens for the second mode | Dark first (v3 canonical), light derived by token swap — both shipped, dark defined first. |
| 3 | 5–6 things vs. 40–50 components | Beginners need only the basics | Real systems run 40–50+ components | v3 ships the beginner set done properly (tokens, type, 12 components), with room to grow. |
| 4 | One font vs. two | One family always suffices | Serif + sans pairing adds personality | One family (Inter) by default; serif never in UI. Personality comes from imagery, not a second font. |
| 5 | Less vs. more | "Less is more" became a soulless trend | Good dashboards "knew what to leave out" | Judge by help at the exact moment, not count. |
| 6 | Hiding: band-aid vs. technique | Hiding elements does not fix clutter | Progressive disclosure is core technique | Hiding as sequenced disclosure of secondary actions: yes. Hiding as a clutter band-aid: no. |
| 7 | Cards vs. lines | Cards are the default dashboard structure | Dissolve cards into lines/sections | Cards deliberate, not default. Rows + sections carry lists; cards carry media and elevated content. |
| 8 | 60-30-10 | Fixed ratio balances interfaces | Explicitly rejected for product UI | Rejected. Four-layer system instead. |
| 9 | Light card edges | ~85% white tint defines the edge | Background-color separation | v3 light: white cards + #E5E5E5 border + soft shadow. Both mechanisms, kept subtle. |
| 10 | Icons: system vs. distinctive | SF Symbols for familiarity | Defaults are generic; pick a distinctive pack | Lucide, one style, 1.5–2px stroke, everywhere — distinctive enough, consistent always. |
| 11 | Motion/haptics: restraint vs. freedom | Platform defaults skip animation/haptics at scale | Indies should animate + haptic liberally | Indie freedom, bounded: press/motion always, haptics light except tab changes, reduced-motion honored. |
| 12 | Color source | Personality/context of the product | System/semantic colors for dynamic surfaces | Personality for brand moments (hero accent, empty-state art); semantic tokens for everything dynamic. |
| 13 | design.md: lightweight vs. exact | Lightweight summary, point to metadata | Nothing left to interpretation (hex, weight 600 only) | Split: this doc is the lightweight summary; per-component metadata files (to be written at apply time) hold full contracts. |
| 14 | Type guideline needed? | Type styles are a required minimum | Dedicated type guideline usually unnecessary | v3 defines the scale exactly (mobile base 14px) because the repo drifted to 12px — the guideline earns its place. |
| 15 | Dark body text size | v2 spec: body 12px | Transcripts: mobile base never below 14–16px | **v3 corrects v2: mobile body = 14px.** 12px is demoted to metadata/micro only. |

---

# PART 2 — The Dispatch Design System v3 (the spec)

## 2.1 Principles (the laws)

1. **Single-direction layout.** One physical axis per section: vertical card stack OR horizontal snap carousel. No 2D dashboard grids on mobile.
2. **44px minimum touch targets, 8px minimum separation.** Two adjacent buttons never touch. No target smaller than 44px tall (tabs: 44px tall, 64px wide).
3. **Four-layer elevation.** Canvas → cards → recessed wells → accent. Elevation reads as lightness in dark mode, as border + shadow in light mode.
4. **Four sizes, two weights max per screen.** Mobile base 14px. Numbers always tabular, right-aligned.
5. **Zero OS emojis.** Lucide icons only, 1.5–2px stroke, one consistent style. Icons sit in tinted rounded squares only when they are status/action glyphs — never as decoration.
6. **Concentric radii.** `R_inner = R_outer − padding` on every nested element.
7. **Tactile, instant.** Press scale 0.98 in 150ms. No dead taps, no silent waits, no bare spinners.
8. **One hero CTA per viewport.** One glowing primary action; everything else neutral.
9. **Inspired by YouTube, never a copy.** Patterns borrowed (bottom nav, pill actions, card anatomy, bottom sheets, chip filters, instant theme switch). YouTube's red, 3-tab nav, and feed composition are NOT copied — Dispatch has its own brand, its own 4-tab nav, and its own flow.
10. **Design for imperfect content.** Long titles truncate, bright thumbnails keep icon contrast, offline states exist, every action gets a response.
11. **Every screen answers: Where am I? What can I do? Where can I go?** Title in the top bar, primary action visible, next step obvious.

## 2.2 Tokens — one name, two values

Semantic tokens only in code. Primitive hex values appear here (the source of truth) and nowhere in components. **Dark is canonical** (defined first, matches the standing playbook). Light is derived by token swap, not by a second design.

| Token | Dark | Light | When to use |
|---|---|---|---|
| `canvas` | `#161616` | `#FFFFFF` | App background, the lowest layer |
| `surface` (card) | `#1C1C1C` | `#FFFFFF` | Cards, sheets, elevated content |
| `surface-2` (recessed/well) | `#232323` | `#F0F0F0` | Input fields, wells, inactive chips, thumbnail placeholders |
| `surface-3` (deep recess) | `#282828` | `#E5E5E5` | Pressed states, nested wells, dividers on dark |
| `border` | `#2E2E2E` | `#E5E5E5` | Card borders, hairlines, dividers |
| `border-strong` | `#3E3E3E` | `#D4D4D4` | Focused inputs, emphasized dividers |
| `text` | `#F5F5F5` | `#0F0F0F` | Headings, primary text, values |
| `text-2` | `#A3A3A3` | `#606060` | Body text, labels, secondary info |
| `text-3` | `#737373` | `#909090` | Metadata, timestamps, captions, placeholders |
| `accent` (hero) | `#2563EB` | `#2563EB` | The ONE primary action per screen, selected states, links |
| `accent-hover` | `#1D4ED8` | `#1D4ED8` | Hero hover/press |
| `accent-soft` | `rgba(37,99,235,0.14)` | `rgba(37,99,235,0.10)` | Selected chip bg, active nav tint, icon wells |
| `success` | `#22C55E` | `#16A34A` | Completed, published, healthy (light value darkened for AA on white) |
| `warning` | `#F59E0B` | `#D97706` | Queued-with-issue, needs attention |
| `danger` | `#EF4444` | `#DC2626` | Destructive, failed, recording indicator |
| `info` | `#38BDF8` | `#0284C7` | Processing, syncing, informational |
| `overlay` (scrim) | `rgba(0,0,0,0.6)` | `rgba(0,0,0,0.4)` | Bottom sheet / modal dim |
| `shadow` | none (dark uses lightness, not shadows) | `0 1px 2px rgba(0,0,0,0.06), 0 4px 12px rgba(0,0,0,0.06)` | Light-mode elevation only |

**Rules for tokens:**
- Never hardcode a hex in a component. If a value is not in this table, it does not exist — add it here first.
- Accent appears once per viewport (hero CTA or selected state), never as decoration. Red is reserved for destructive + recording; it is never the brand.
- Dark mode: surfaces get LIGHTER as they elevate. Light mode: surfaces get DARKER or bordered as they elevate. Never copy a light palette straight into dark — double the step distance (4–6% vs. 2%).
- Text on accent: white in both themes. Check AA contrast for every text/background pair before shipping (the repo's legacy `text-3` on `canvas` in dark is the known borderline case — flagged in section 2.10).

## 2.3 Typography (mobile-first)

| Style | Size / weight / line-height | Use |
|---|---|---|
| Page title | 18px / 600 / 120% | Screen titles in top bar |
| Section title | 16px / 600 / 120% | Card headers, section headers |
| Body | **14px / 400 / 140%** | All body copy, list rows, settings (v2's 12px body is retired — see 1.11 #15) |
| Metadata | 12px / 400 / 130% | Timestamps, counts, captions, secondary lines |
| Mono | 12px / 500 / tabular | Durations, file sizes, percentages, timestamps |
| Button label | 14px / 600 | All button text (never all-caps shouting) |

- Family: Inter (UI) + system mono for numbers. One family, no serif in UI.
- Headline tracking: −2% to −3% on titles; small uppercase labels get +letter-spacing so they breathe.
- Titles truncate at 2 lines with ellipsis; metadata at 1 line. Never mid-word breaks on titles.
- Hierarchy by size/weight/color only — never by adding a second font or a decorative color.
- Must survive Dynamic Type / larger text: rows grow, nothing clips, nothing overlaps.

## 2.4 Spacing, radii, motion

- **Grid:** 4pt base. Gaps: 4 / 8 / 12 / 16 / 24 / 32. Page margin 16px mobile. Section gap 32px. Card padding 16px. Row gap 12px.
- **Radii:** card 16px, inner elements 12px or 8px by the concentric rule, chips/pills fully rounded, buttons 12px, inputs 10px, bottom sheet top corners 20px.
- **Targets:** 44px min height; tabs 44×64; icon buttons 44×44; chips 32px tall (text 13px).
- **Motion:** press scale 0.98 within 150ms; page transitions slide (not instant cut); springs stiffness 636 / damping 24 for popovers; tooltip delay 1000ms; toast slide-up 150ms ease-out; progress bars animate smoothly (Linear-style stroke draw).
- **Haptics:** light on frequent taps, heavier on tab changes. Honor reduced-motion (motion becomes instant, haptics off).
- **Focus:** visible focus rings on every interactive element (never bare outline-none). Keyboard/tab focus is a first-class state, not an afterthought.

## 2.5 App shell (YouTube pattern, Dispatch structure)

- **Top bar:** screen title (left) + at most two screen-specific actions (right). Never a branding-only title, never a menu as the first thing. Title answers "Where am I?"; actions answer "What can I do?".
- **Bottom nav (4 tabs, Dispatch's own):** Record | Sessions | Clips | Settings. Sticky, above the home-indicator safe area, thin top divider. Active = filled icon + accent bold label + dot; inactive = outline icon + muted label. (YouTube uses 3 tabs — Home/Shorts/You. Dispatch keeps 4 because its flow has 4 homes; copying 3 would force an unnatural merge.)
- **Record is a screen, not a tab-bar action.** Per WWDC: tabs are for navigation, not actions. The big record action lives as the hero CTA inside the Record screen — not as a center tab button.
- **Single column, vertical scroll.** No sidebars on mobile, no multi-column grids. Content margin 16px, full-bleed only for the video preview stage.
- **Theme switch:** Settings → Appearance → Use device theme / Dark / Light (YouTube's picker, same behavior). Switching recolours instantly, no reload, no restart.

## 2.6 Components

**Clip card (YouTube compact card, adapted).** Anatomy in order: thumbnail (16:9 for landscape clips, 9:16 for vertical — never mixed in one list without a clear section break) with duration badge bottom-right (black bg, white text, both themes) and status pill; title, max 2 lines, bold; metadata line (source • duration • status • age) in `text-3`; small source avatar left where a source exists; kebab menu far right opening a bottom sheet. Hierarchy: thumbnail > title > metadata > avatar/badges > menu. Never a card inside a card.

**Filter chips row (YouTube chip pattern).** Horizontally scrollable pill row under the top bar: All, Processing, Ready, Published, Failed (or per-screen equivalents). Selected chip inverts (dark theme: accent-soft bg + accent text; light theme: black bg + white text, mirroring YouTube's selected chip). Unselected: `surface-2` bg, `text-2` text. One row only; overflow scrolls, never wraps to two rows.

**Processing queue row.** Progress bar with goal gradient (start visually at 20%, never 0%), named step text ("Transcribing…", "Finding hooks…", "Rendering clip 3/8"), never a silent spinner. Failed steps show red status + retry action inline. Optimistic UI: the clip appears in the list the moment processing starts, with its live status — no awkward pause before anything shows.

**Publish sheet (bottom sheet).** Destination rows (YouTube, Instagram, TikTok, X) each with platform icon, account name, and a pill toggle. Caption field on top, preview thumbnail beside it. ONE hero CTA at the bottom: "Publish to 3 accounts" — total inside the button (destinations count, like total price inside a buy button). Sheet: rounded top corners 20px, grab handle, scrim dim, swipe-down to dismiss.

**Mini editor.** Preview stage on top (9:16 or 16:9, matching the clip), timeline below, caption-style chips below that, sticky bottom action area (Discard neutral left, Save/Export hero right). Title transitions into the sticky top nav on scroll (YouTube/uxpeak pattern) so context is never lost. One hero CTA per viewport — Save is the hero; everything else is ghost/neutral.

**URL input (Record screen).** Large paste-friendly field (paste never blocked), placeholder naming what it accepts ("Paste a YouTube link, or record now"). Auto-detects a URL on paste and advances. States: default, focus (accent border + ring), error (red border + message), warning (amber border + message for e.g. age-restricted or over-length videos). Helper text under the field, never inside as the only label.

**Record controls.** Big hero record button (accent), timer in mono tabular, pause/resume + stop as neutral secondary buttons beside it — never competing colored buttons. Recording indicator: red dot + pulsing ring (red's one legitimate non-destructive use). After stop: immediate preview + "Use this recording" hero CTA + "Retake" ghost button.

**Empty states (never bare).** Headline offering the solution + illustration/mascot + 1–2 actionable tips + CTA. Examples: No clips yet → "Your first clip is one link away" + "Paste a video" CTA. No sessions → "Record or paste to start" + Record CTA. Never a screen that only says "no data."

**Toasts.** Slide-up, bottom, above the nav bar. Spinner → checkmark transition on completion. Auto-dismiss, never blocking the workspace. Used for: clip ready, published, copied link, saved edit, failed upload (with Retry action inside the toast).

**Bottom sheets vs. modals.** Sheet (rounded top, grab handle, scrim): actions for an item (kebab menu), publish destinations, filters. Centered modal card: account/settings decisions, destructive confirmations, anything requiring reading before acting. Popover (small, non-blocking, click-away): simple context like sort order or display settings. Full page: permanent or large context (editor, session detail) — with back button/breadcrumb.

**Toggles.** Pill switches (on = accent pill, knob right; off = `surface-2`, knob left). Never a checkbox for a two-option yes/no — that is what a toggle is for.

**Segmented control.** Two-to-four options, one pill container, selected segment inverted. Used for clip aspect (9:16 / 16:9 / 1:1) and theme-adjacent choices. More than four options → chips or a sheet, never a segmented control.

## 2.7 Screens and the core flow

Dispatch's flow is record-or-paste → autonomous clipping → light edit → publish from inside the app. Every screen below answers Where am I / What can I do / Where can I go.

1. **Record (home).** Hero: paste-URL field + big Record button. Below: "Recent sessions" list (clip-card rows). Empty state: mascot + "Paste a link or hit record." One hero CTA: Record (or "Make clips" when a URL is pasted).
2. **Processing (inside Sessions).** Queue rows with named steps + goal-gradient progress. Tapping a row opens its detail: source video, clip count, per-clip status. Failed items offer Retry inline. Nothing silent, nothing bare.
3. **Clips (library).** Chip filter row on top (All / Ready / Published / Failed), then clip-card rows grouped by recency (Today, This week, Older — grouping by time makes any list findable in a second). Kebab → sheet: Edit, Publish, Duplicate, Delete (destructive last, red).
4. **Editor (light).** Preview stage, timeline, caption chips, sticky bottom actions. Kept deliberately small: trim, captions, aspect — not a full NLE. Save → toast → back to Clips with the updated card.
5. **Publish (sheet).** Destinations, toggles, caption, hero CTA with destination count. Success → toast + card status flips to Published (green pill).
6. **Settings.** Appearance picker (device / dark / light), accounts (connected socials with per-account rows), storage, about. Lists + toggles + one destructive zone at the bottom (red text, never a red button unless it is the destructive confirm).

**Grouping rules for lists:** by time (Recent), by progress (Processing → Ready → Published), by pattern (same source together). Never one long undifferentiated list.

## 2.8 Theming rules (both modes, one system)

- One token name → two values (table in 2.2). Components reference tokens, never hex. Switching themes recolours instantly (YouTube behavior), no reload.
- Dark is the canonical definition; light is derived by the same token names with inverted layer logic (dark: lighter = higher; light: darker/bordered = higher). Never port a light value straight into dark.
- Accent stays `#2563EB` in both themes (AA on white and on `#1C1C1C`). Dark hover/press uses the same ramp; light uses the same blue, never a lighter "light-mode blue" that fails contrast.
- Semantic colors shift per theme for contrast (success `#22C55E` dark → `#16A34A` light; danger `#EF4444` → `#DC2626`), but their meaning never changes.
- Shadows exist only in light mode. Dark mode depth = lightness steps + borders. Light mode depth = border + soft shadow.
- The theme picker lives in Settings → Appearance, with three options (device / dark / light), exactly like YouTube's picker. Default: device theme.

## 2.9 What was taken from YouTube vs. what was deliberately NOT copied

| Taken (pattern) | Why it fits Dispatch |
|---|---|
| Bottom nav, icon + label, filled = active, sticky, thin divider | Thumb-zone navigation for a 4-home app |
| Compact card: thumbnail left, title + metadata right, kebab far right | Clip rows scan the same way video rows do |
| Pill actions everywhere (chips, actions, Subscribe-style CTA) | Dispatch's filters, statuses, and publish actions are all pill-shaped decisions |
| Bottom sheet for kebab menus (rounded top, grab handle, scrim) | Item actions without leaving the list |
| Filter chip row, selected chip inverts | Clip status filtering |
| Duration badge black/white on thumbnails | Clip length at a glance, both themes |
| Hierarchy identical across themes, only colors invert | One token set, two values |
| Appearance picker, instant recolour, no reload | Theme switching without friction |
| 2-line title truncation, grey metadata, avatar + badges | Scannability rules for every row |

| NOT copied | Why |
|---|---|
| YouTube red `#FF0000` as brand/accent | Dispatch's hero is creator blue `#2563EB`; red stays reserved for destructive + recording |
| 3-tab nav (Home / Shorts / You) | Dispatch has 4 homes; forcing 3 would merge unrelated flows |
| Feed composition (endless scroll, sponsored cards, Up next) | Dispatch is a tool, not a feed — its home is an action (record/paste), not content |
| Search-first header | Dispatch's first action is creation, not search; search lives inside lists |
| Shorts full-bleed player page | Dispatch's preview is an editor stage, not a consumption feed |

## 2.10 What v3 changes about v2, and the drift backlog

**Explicit v2 overrides (v3 wins):**
1. Body text 12px → **14px** (transcript evidence: mobile base never below 14px).
2. 60/30/10 ratio → **four-layer color system** (the ratio's own video rejects it for product UI).
3. Single dark theme → **dark + light, same token names**.
4. Hero blue stays `#2563EB`; success/danger unified to one pair per theme (see 2.2) instead of v2's mixed values.

**Drift found in the repo (Oct 25 grep; fix at apply time, not now):**
- **Two token sets coexist:** `DESIGN_SYSTEM.md` v2 (canvas `#161616`, surface `#1C1C1C`, recessed `#232323`/`#282828`, borders `#2E2E2E`) vs. `frontend/tailwind.config.js` ("v2 semantic tokens": canvas `#0D0F12`, surface `#16181C`, elevated `#1C1F24`, recessed `#23262C`, borders `#2E3239`/`#242830`/`#3E434D`, text `#F3F4F6`/`#A3AAB5`/`#747D8A`, success `#10B981`, danger `#F43F5E`). v3 adopts the doc's `#161616` family (it matches the standing playbook) — the tailwind config must be rewritten to v3 tokens, not the other way round.
- **~700+ hardcoded arbitrary color classes** (`bg-[#…]` / `text-[#…]` / `border-[#…]`) across `frontend/src` instead of token classes — the single biggest drift. Migration order: tokens → building blocks → components (per the transcripts), never a whole-system rewrite in one prompt.
- **Off-palette surfaces:** `#232832` (×36), `#16191E` (×23), `#1C1F26` (×20), `#262B35` (×18) — map each to the nearest v3 token or delete.
- **Playbook violations:** `#00F0FF` cyan (×8), `#EC4899` pink (×5) — delete or map to accent/semantic. `#FFE600` (×17, caption highlight) — plausibly legitimate; keep only as the caption-highlight token, documented.
- **Inline `style={{}}` in 12 files** (Studio panes, CaptionStudio, ColorPickerModal, etc.) — move to tokens.
- **Success/danger split:** `#10B981` vs. `#22C55E`, `#F43F5E` vs. `#EF4444` — unify to v3's per-theme pair.
- **Unread at spec time:** `docs/design-system/DISPATCH_DESIGN_SYSTEM.md` (159 lines) and `design_research/synthesis/05_DISPATCH_UNIFIED_DESIGN_SYSTEM.md` (281 lines) were not folded in — check them at apply time for anything v3 missed, before writing code.

## 2.11 How to apply this (needs Rohit's go-ahead — nothing below has been done)

1. **Write the design.md.** This document becomes `DESIGN_SYSTEM.md` v3 in the repo (lightweight summary) + a `design.md` wired into agent instructions ("When creating or modifying any UI, read and follow design.md" in AGENTS.md/CLAUDE.md).
2. **Per-component metadata files.** One file per component grouping (form / navigation / data display) with anatomy, variants, states, dos/don'ts, accessibility — the deep contracts this summary points to. Never one giant file.
3. **Rewrite `tailwind.config.js` to v3 tokens** (dark + light values per token), then migrate components in order: tokens → building blocks (buttons, inputs, chips) → components (cards, sheets, editor). Verify each step against this spec; AI still gets colors and spacing wrong even with full context.
4. **Fix the drift list in 2.10** as part of the migration, not as a separate pass.
5. **QA like the transcripts teach:** build, screenshot in both themes, check AA contrast, 44px targets, truncation with long titles, bright thumbnails, empty states, and a failed publish — before calling it done.
6. **Document known debt explicitly** (anything that cannot be fixed yet, with the reason) so future AI passes do not "helpfully" unfix it.

---

*Sources: 29 videos (extraction files in `workspace/dispatch-design-system/extract/`), YouTube mobile observation 25 Oct 2026 (`extract/youtube-mobile-observation.md`), Dispatch repo `DESIGN_SYSTEM.md` v2 + `frontend/tailwind.config.js` + Oct 25 drift grep. No repo code was modified to produce this document.*
