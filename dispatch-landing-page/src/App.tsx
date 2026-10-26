import { useEffect, useState, type CSSProperties } from 'react';

const releaseUrl = 'https://github.com/Resolviaai/Dispatch/releases/latest';
const featureCards = [
  { title: 'Capture', image: '/assets/feature-cards/capture.png' },
  { title: 'Find', image: '/assets/feature-cards/find.png' },
  { title: 'Trim', image: '/assets/feature-cards/trim.png' },
  { title: 'Captions', image: '/assets/feature-cards/captions.png' },
  { title: 'Review', image: '/assets/feature-cards/review.png' },
  { title: 'Export', image: '/assets/feature-cards/export.png' },
];
const dialMilestones = [
  ...Array.from({ length: 11 }, (_, index) => index),
  ...Array.from({ length: 9 }, (_, index) => (index + 2) * 10),
  ...Array.from({ length: 9 }, (_, index) => (index + 2) * 100),
  ...Array.from({ length: 9 }, (_, index) => (index + 2) * 1000),
  ...Array.from({ length: 9 }, (_, index) => (index + 2) * 10000),
  ...Array.from({ length: 9 }, (_, index) => (index + 2) * 100000),
];
const sideDialMetrics = ['views', 'followers'] as const;

function formatCount(value: number) {
  return Math.round(value).toLocaleString('en-US');
}

function interpolateTrackValue(stops: number[], distance: number) {
  const position = Math.min(stops.length - 1, Math.max(0, distance));
  const lower = Math.floor(position);
  const upper = Math.min(stops.length - 1, lower + 1);
  return stops[lower] + (stops[upper] - stops[lower]) * (position - lower);
}

function ArrowIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="arrow-icon">
      <path d="M4 10h11M10 4l6 6-6 6" />
    </svg>
  );
}

function BrandMark() {
  return (
    <a href="#top" className="brand" aria-label="Dispatch home">
      <span className="brand-mark" aria-hidden="true"><span /><span /><span /></span>
      <span>dispatch</span>
    </a>
  );
}

function PhoneMockup() {
  return (
    <figure className="hero-phone-figure">
      <div className="hero-phone" role="img" aria-label="Illustrative Dispatch mobile clips screen">
        <div className="phone-screen">
          <div className="phone-island" aria-hidden="true" />
          <div className="phone-statusbar" aria-hidden="true">
            <span>9:41</span>
            <span className="phone-status-icons"><i /><i /><i /></span>
          </div>
          <div className="phone-appbar">
            <span className="phone-app-mark" aria-hidden="true"><i /><i /><i /></span>
            <div><span>DISPATCH</span><strong>Clips</strong></div>
            <span className="phone-refresh" aria-hidden="true"><svg viewBox="0 0 20 20"><path d="M16.5 8A6.8 6.8 0 0 0 4.3 5.5L3 7m0 0V3.8M3 7h3.2M3.5 12a6.8 6.8 0 0 0 12.2 2.5L17 13m0 0v3.2M17 13h-3.2" /></svg></span>
          </div>
          <div className="phone-online"><i /> Studio online <span>5 ready</span></div>
          <div className="phone-tabs"><strong>Ready <b>5</b></strong><span>Uploaded</span><span>All</span></div>
          <div className="phone-session"><span>SESSION 01</span><span>2 clips</span></div>
          <div className="phone-clip-card">
            <div className="phone-thumbnail" aria-hidden="true">
              <div className="phone-person"><i /><b /></div>
              <span>00:42</span>
            </div>
            <div className="phone-clip-copy">
              <span>READY TO REVIEW</span>
              <strong>A moment worth keeping</strong>
              <small>Preview · captions · your call</small>
            </div>
          </div>
          <div className="phone-bottom-nav" aria-hidden="true">
            <span>Record</span><span>Sessions</span><strong>Clips</strong>
          </div>
        </div>
      </div>
      <figcaption>Dispatch mobile app · illustrative preview</figcaption>
    </figure>
  );
}

function PageSideOrnaments() {
  const [scrollProgress, setScrollProgress] = useState(0);
  const [isMobileDial, setIsMobileDial] = useState(() => window.innerWidth <= 680);

  useEffect(() => {
    let frame = 0;
    const updateProgress = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        const scrollableHeight = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
        const progress = scrollableHeight === 0 ? 0 : Math.min(1, Math.max(0, window.scrollY / scrollableHeight));
        setScrollProgress(progress);
        setIsMobileDial(window.innerWidth <= 680);
        frame = 0;
      });
    };

    updateProgress();
    window.addEventListener('scroll', updateProgress, { passive: true });
    window.addEventListener('resize', updateProgress);
    return () => {
      window.removeEventListener('scroll', updateProgress);
      window.removeEventListener('resize', updateProgress);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  const trackPosition = scrollProgress * (dialMilestones.length - 1);
  const milestoneIndex = Math.min(dialMilestones.length - 1, Math.floor(trackPosition));
  const scrollPhase = milestoneIndex === dialMilestones.length - 1 ? 0 : trackPosition - milestoneIndex;
  const track = isMobileDial
    ? { step: 2.8, x: [0, .55, 1.25, 1.7, 2.1] }
    : { step: 4, x: [0, .7, 1.7, 2.2, 2.7] };

  return (
    <div className="page-side-ornaments">
      {sideDialMetrics.map((metric) => {
        const value = dialMilestones[milestoneIndex];
        const neighbors = [
          { slot: 'far-above', offset: 3 },
          { slot: 'above', offset: 2 },
          { slot: 'near-above', offset: 1 },
          { slot: 'near-below', offset: -1 },
          { slot: 'below', offset: -2 },
          { slot: 'far-below', offset: -3 },
        ];
        return (
          <div className={`page-side-dial page-side-dial--${metric}`} key={metric}>
            <div className="page-side-ring" aria-hidden="true" />
            <div className="page-side-neighbors" aria-hidden="true">
              {neighbors.map(({ slot, offset }) => {
                const neighborIndex = milestoneIndex + offset;
                const trackPosition = offset - scrollPhase;
                const distance = Math.abs(trackPosition);
                const arcX = interpolateTrackValue(track.x, distance) * (metric === 'views' ? -1 : 1);
                const arcY = -trackPosition * track.step;
                const tilt = Math.sign(-trackPosition) * interpolateTrackValue([0, 12, 24, 34, 42], distance) * (metric === 'views' ? 1 : -1);
                const opacity = interpolateTrackValue([0, .86, .68, .45, .22], distance) * Math.min(1, distance / .55);
                return (
                  <span
                    className={`page-side-neighbor page-side-neighbor--${slot}`}
                    key={slot}
                    style={{ '--arc-x': `${arcX}rem`, '--arc-y': `${arcY}rem`, '--arc-tilt': `${tilt}deg`, opacity } as CSSProperties}
                  >
                    {neighborIndex < 0 || neighborIndex >= dialMilestones.length ? '' : formatCount(dialMilestones[neighborIndex])}
                  </span>
                );
              })}
            </div>
            <div className="page-side-current" role="img" aria-label={`${formatCount(value)} ${metric}`}>
              <strong aria-hidden="true">{formatCount(value)}</strong><small aria-hidden="true">{metric}</small>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FeatureCards() {
  return (
    <section className="feature-section" aria-labelledby="feature-title">
      <div className="feature-content">
        <h2 id="feature-title">A long session.<br />A few good moments.<br />Your call.</h2>
        <ul className="feature-grid">
          {featureCards.map((card) => (
            <li className="feature-card" key={card.title}>
              <h3>{card.title}</h3>
              <img src={card.image} alt="" loading="lazy" />
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function Hero() {
  return (
    <section className="hero" id="top" aria-labelledby="hero-title">
      <header className="site-header page-shell">
        <BrandMark />
        <span className="header-note">ANDROID + WINDOWS ALPHA</span>
        <a className="header-cta" href={releaseUrl} target="_blank" rel="noreferrer">
          Explore the alpha <ArrowIcon />
        </a>
      </header>

      <div className="hero-center page-shell">
        <div className="hero-mascot">
          <img src="/assets/dispatch-sloth-master.png" alt="Dispatch's relaxed sloth mascot holding a small clapperboard" width="1280" height="1280" />
          <span className="mascot-caption">“No rush. I’m looking.”</span>
        </div>
        <div className="hero-copy">
          <p className="eyebrow"><span className="eyebrow-dot" /> A CALMER WAY TO FIND THE GOOD PARTS</p>
          <div className="hero-title-stage">
            <h1 id="hero-title">Meet Dispatch</h1>
            <PhoneMockup />
          </div>
          <p className="hero-lede">Dispatch helps you find short clips in longer talking sessions, so you can spend less time scrubbing and more time creating.</p>
          <div className="hero-actions">
            <a className="button button--dark" href={releaseUrl} target="_blank" rel="noreferrer">Explore the Android alpha <ArrowIcon /></a>
            <a className="button button--light" href="#how-it-works">See how it works <ArrowIcon /></a>
          </div>
        </div>
        <p className="hero-footnote">Android capture <span>·</span> Windows PC processing <span>·</span> You decide what to keep</p>
      </div>
    </section>
  );
}

export default function App() {
  return (
    <div className="landing-page">
      <PageSideOrnaments />
      <a className="skip-link" href="#main-content">Skip to content</a>
      <Hero />
      <main id="main-content">
        <FeatureCards />
      </main>
    </div>
  );
}
