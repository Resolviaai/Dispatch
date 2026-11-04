import { useState, useEffect, type FormEvent } from 'react';
import { RELEASES } from './data/site';

export const PREVIEW_INVITE_CODE = 'DSP-ALPHA';

export default function App() {
  const [inviteCode, setInviteCode] = useState('');
  const [inviteError, setInviteError] = useState('');
  const [inviteAccepted, setInviteAccepted] = useState(false);
  const [email, setEmail] = useState('');
  const [emailSuccess, setEmailSuccess] = useState(false);
  const [activeFaq, setActiveFaq] = useState<number | null>(null);

  useEffect(() => {
    let timeoutId: number;
    const onScroll = () => {
      document.documentElement.classList.add('is-scrolling');
      window.clearTimeout(timeoutId);
      timeoutId = window.setTimeout(() => {
        document.documentElement.classList.remove('is-scrolling');
      }, 900);
    };

    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.clearTimeout(timeoutId);
    };
  }, []);

  function handleInvite(e: FormEvent) {
    e.preventDefault();
    if (inviteCode.trim().toUpperCase() === PREVIEW_INVITE_CODE) {
      setInviteAccepted(true);
      setInviteError('');
    } else {
      setInviteError('Invalid invite code. Please check your invitation email.');
    }
  }

  function handleWaitlist(e: FormEvent) {
    e.preventDefault();
    if (email.trim() && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setEmailSuccess(true);
    }
  }

  return (
    <div className="landing-layout">
      {/* ── TOP HEADER ── */}
      <header className="site-header">
        <div className="shell nav-shell">
          <a className="brand-logo" href="#hero" aria-label="Dispatch">
            <svg width="26" height="26" viewBox="0 0 28 28" fill="none">
              <path d="M8 19.5C6 17.5 6 14 8 12L13.5 6.5C15.5 4.5 19 4.5 21 6.5C23 8.5 23 12 21 14L15.5 19.5C13.5 21.5 10 21.5 8 19.5Z" stroke="#2563EB" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M12 16L16 12" stroke="#2563EB" strokeWidth="3.6" strokeLinecap="round"/>
            </svg>
            <span className="brand-name">dispatch</span>
          </a>

          <nav className="desktop-nav">
            <a href="#how-it-works">How It Works</a>
            <a href="#channels">Platforms</a>
            <a href="#pipeline">PC App</a>
            <a href="#faq">Questions</a>
          </nav>

          <div className="header-cta-wrap">
            <a className="nav-btn-primary" href="#download">
              <span>Get the Alpha</span>
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M3 7H11M11 7L7.5 3.5M11 7L7.5 10.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </a>
          </div>
        </div>
      </header>

      <main id="main">
        {/* ── 1. HERO SECTION ── */}
        <section className="hero-section" id="hero">
          <div className="hero-backdrop" aria-hidden="true">
            <img
              className="hero-backdrop-img"
              src={`${import.meta.env.BASE_URL}assets/Whimsical Butterfly Meadow Panorama (1).png`}
              alt=""
            />
          </div>
          <div className="hero-gradient-overlay" aria-hidden="true" />

          <div className="shell hero-content-shell">


            <h1 className="hero-headline">
              <span className="headline-dark">Speak for an hour.</span>
              <span className="headline-accent">
                Get viral clips.
                <svg className="hero-underline-stroke" viewBox="0 0 280 18" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                  <path d="M4 13C65 5 205 4 276 11C205 16 95 17 4 13Z" fill="#35452a"/>
                </svg>
              </span>
            </h1>

            <p className="hero-subline">
              Just press Record and Stop on your phone. Dispatch finds the best parts, adds captions, and posts them for you.
            </p>

            <div className="hero-buttons">
              <a className="btn-hero-primary" href="#download">
                <span>Get the Alpha</span>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M3.5 8H12.5M12.5 8L8.5 4M12.5 8L8.5 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </a>
            </div>

            </div>
        </section>

        {/* ── 2. THE TWO-BUTTON EXPERIENCE ── */}
        <section className="section-padded bg-paper-raised" id="how-it-works">
          <div className="shell">
            <div className="section-header">
              <span className="section-eyebrow">HOW IT WORKS</span>
              <h2 className="section-title">No Video Editing Needed</h2>
              <p className="section-desc">You talk. Dispatch does the hard work. No editing, no stress.</p>
            </div>

            <div className="two-button-flow-card">
              <div className="flow-badge">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                <span>ZERO SCRUBBING &bull; 100% AUTOMATED</span>
              </div>

              <div className="flow-steps">
                <div className="flow-box">
                  <div className="flow-icon-wrap">
                    <div className="btn-circle btn-record-start">
                      <span className="inner-dot" />
                    </div>
                  </div>
                  <div className="flow-step-tag">STEP 01</div>
                  <h4>Hit Record</h4>
                  <p>Talk as long as you want. Say whatever is on your mind.</p>
                </div>

                <div className="flow-arrow">
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#8a9a7b" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M5 12h14M12 5l7 7-7 7"/>
                  </svg>
                </div>

                <div className="flow-box">
                  <div className="flow-icon-wrap">
                    <div className="btn-circle btn-record-stop">
                      <span className="inner-square" />
                    </div>
                  </div>
                  <div className="flow-step-tag">STEP 02</div>
                  <h4>Hit Stop</h4>
                  <p>You are all done. Go relax and enjoy your day.</p>
                </div>

                <div className="flow-arrow">
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#8a9a7b" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M5 12h14M12 5l7 7-7 7"/>
                  </svg>
                </div>

                <div className="flow-box flow-magic">
                  <div className="flow-icon-wrap">
                    <div className="btn-circle btn-magic-done">
                      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#35452a" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M22 2L11 13"/>
                        <path d="M22 2L15 22L11 13L2 9L22 2Z"/>
                      </svg>
                    </div>
                  </div>
                  <div className="flow-step-tag">STEP 03 &bull; AUTO</div>
                  <h4>Done and Posted</h4>
                  <p>Your PC finds the best parts and shares them online.</p>
                </div>
              </div>

              {/* Sub-banner inside the flow card showcasing peace of mind */}
              <div className="flow-peace-banner">
                <img src={`${import.meta.env.BASE_URL}gen/hammock-peace.jpg`} alt="Creator resting with peace of mind in meadow hammock" className="peace-banner-avatar" />
                <div className="peace-banner-text">
                  <strong>Reclaim 4 hours per session.</strong> Dispatch works quietly in the background so you can enjoy unrushed days while your audience grows.
                </div>
              </div>
            </div>

            <div className="philosophy-grid">
              <div className="philosophy-card">
                <div className="card-visual-frame">
                  <img src={`${import.meta.env.BASE_URL}gen/peaceful-creator.jpg`} alt="Creator resting with peace of mind while Dispatch runs in background" className="visual-img" />
                </div>
                <div className="card-content">
                  <div className="card-num">01 &bull; TOTAL PEACE OF MIND</div>
                  <h3>Just Talk, Never Edit</h3>
                  <p>Never spend hours cutting videos. Dispatch finds the best short clips for you.</p>
                </div>
              </div>

              <div className="philosophy-card">
                <div className="card-visual-frame">
                  <img src={`${import.meta.env.BASE_URL}gen/desktop-studio.jpg`} alt="PC automatically processing and formatting clips" className="visual-img" />
                </div>
                <div className="card-content">
                  <div className="card-num">02 &bull; ZERO SUBSCRIPTION CLOUD FEES</div>
                  <h3>Runs on Your Own PC</h3>
                  <p>Uses your own computer to edit. No monthly bills or fees.</p>
                </div>
              </div>

              <div className="philosophy-card">
                <div className="card-visual-frame">
                  <img src={`${import.meta.env.BASE_URL}gen/phone-recording.jpg`} alt="Captioned vertical clip ready for social media" className="visual-img" />
                </div>
                <div className="card-content">
                  <div className="card-num">03 &bull; VIRAL WORD-BY-WORD CAPTIONS</div>
                  <h3>Word-by-Word Subtitles</h3>
                  <p>Words light up as you speak so people stop and watch.</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ── 3. AUTOMATIC MULTI-CHANNEL DISTRIBUTION ── */}
        <section className="section-padded bg-paper" id="channels">
          <div className="shell">
            <div className="section-header">
              <span className="section-eyebrow">AUTO POST</span>
              <h2 className="section-title">Post to 5 Platforms at Once</h2>
              <p className="section-desc">Make one video. Share it everywhere with one click.</p>
            </div>

            <div className="channels-grid">
              <div className="channel-card">
                <div className="channel-icon channel-yt">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="#FF0000">
                    <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
                  </svg>
                </div>
                <h3>YouTube Shorts</h3>
                <p>Uploads shorts with titles and tags ready to go.</p>
              </div>

              <div className="channel-card">
                <div className="channel-icon channel-ig">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="#E4405F">
                    <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/>
                  </svg>
                </div>
                <h3>Instagram Reels</h3>
                <p>Shares clips directly to your Instagram profile.</p>
              </div>

              <div className="channel-card">
                <div className="channel-icon channel-li">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="#0A66C2">
                    <path d="M19 0h-14c-2.761 0-5 2.239-5 5v14c0 2.761 2.239 5 5 5h14c2.762 0 5-2.239 5-5v-14c0-2.761-2.238-5-5-5zm-11 19h-3v-11h3v11zm-1.5-12.268c-.966 0-1.75-.79-1.75-1.764s.784-1.764 1.75-1.764 1.75.79 1.75 1.764-.783 1.764-1.75 1.764zm13.5 12.268h-3v-5.604c0-3.368-4-3.113-4 0v5.604h-3v-11h3v1.765c1.396-2.586 7-2.777 7 2.476v6.759z"/>
                  </svg>
                </div>
                <h3>LinkedIn Video</h3>
                <p>Share your ideas with other professionals with zero editing.</p>
              </div>

              <div className="channel-card">
                <div className="channel-icon channel-fb">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="#1877F2">
                    <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
                  </svg>
                </div>
                <h3>Facebook Reels</h3>
                <p>Post vertical reels to reach a bigger audience.</p>
              </div>

              <div className="channel-card">
                <div className="channel-icon channel-x">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="#000000">
                    <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
                  </svg>
                </div>
                <h3>X (Twitter)</h3>
                <p>Post video clips that are quick and easy to share.</p>
              </div>
            </div>
          </div>
        </section>

        {/* ── 4. THE 5-STAGE LOCAL PC PIPELINE ── */}
        <section className="section-padded bg-olive" id="pipeline">
          <div className="shell">
            <div className="section-header text-on-dark">
              <span className="section-eyebrow text-pistachio">BEHIND THE SCENES</span>
              <h2 className="section-title">How Your PC Does It</h2>
              <p className="section-desc text-muted-dark">Everything runs safely on your own computer.</p>
            </div>

            <div className="pipeline-ledger">
              <div className="ledger-row">
                <div className="ledger-icon-badge">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#b9cd8a" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>
                </div>
                <div className="ledger-step">01</div>
                <div className="ledger-meta">
                  <h4>Verify</h4>
                  <span>Check Video</span>
                </div>
                <div className="ledger-desc">Checks the video file to make sure audio and video are clean.</div>
                <div className="ledger-out"><code>Job Record</code></div>
              </div>

              <div className="ledger-row">
                <div className="ledger-icon-badge">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#b9cd8a" strokeWidth="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
                </div>
                <div className="ledger-step">02</div>
                <div className="ledger-meta">
                  <h4>Transcribe</h4>
                  <span>Listen & Write</span>
                </div>
                <div className="ledger-desc">Listens to your voice and writes down every word with exact timing.</div>
                <div className="ledger-out"><code>Word Timings</code></div>
              </div>

              <div className="ledger-row">
                <div className="ledger-icon-badge">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#b9cd8a" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M14.31 8l5.74 9.94M9.69 8h11.48M7.38 12l5.74-9.94M9.69 16L3.95 6.06M14.31 16H2.83M16.62 12l-5.74 9.94"/></svg>
                </div>
                <div className="ledger-step">03</div>
                <div className="ledger-meta">
                  <h4>Analyze</h4>
                  <span>Find Best Moments</span>
                </div>
                <div className="ledger-desc">AI finds the most interesting 30-second moments to share.</div>
                <div className="ledger-out"><code>Clip Range</code></div>
              </div>

              <div className="ledger-row">
                <div className="ledger-icon-badge">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#b9cd8a" strokeWidth="2"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/></svg>
                </div>
                <div className="ledger-step">04</div>
                <div className="ledger-meta">
                  <h4>Render</h4>
                  <span>Make Vertical Video</span>
                </div>
                <div className="ledger-desc">Cuts the video, centers your face, and adds glowing subtitles.</div>
                <div className="ledger-out"><code>1080&times;1920 MP4</code></div>
              </div>

              <div className="ledger-row">
                <div className="ledger-icon-badge">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#b9cd8a" strokeWidth="2"><path d="M22 2L11 13"/><path d="M22 2L15 22L11 13L2 9L22 2Z"/></svg>
                </div>
                <div className="ledger-step">05</div>
                <div className="ledger-meta">
                  <h4>Broadcast</h4>
                  <span>Post Everywhere</span>
                </div>
                <div className="ledger-desc">Sends the finished video to YouTube, Instagram, LinkedIn, Facebook, and X.</div>
                <div className="ledger-out"><code>Live Posts</code></div>
              </div>
            </div>
          </div>
        </section>

        {/* ── 5. STUDIO REVIEW HIGHLIGHTS BENTO ── */}
        <section className="section-padded bg-paper" id="studio">
          <div className="shell">
            <div className="section-header">
              <span className="section-eyebrow">YOU ARE IN CONTROL</span>
              <h2 className="section-title">Fast and Easy, With Your OK</h2>
              <p className="section-desc">The computer works automatically, but you always decide what gets posted.</p>
            </div>

            <div className="bento-container">
              <div className="bento-box bento-wide">
                <div className="bento-visual-half">
                  <img src={`${import.meta.env.BASE_URL}gen/serene-station.jpg`} alt="Local PC Review Studio with immediate preview" className="bento-thumb" />
                </div>
                <div className="bento-text-half">
                  <div className="bento-tag">Review First</div>
                  <h3>Nothing Posts Without You</h3>
                  <p>The AI makes the clips for you. You can check them first, edit words, or delete them in one click.</p>
                  <div className="bento-badges">
                    <span>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6L9 17l-5-5"/></svg>
                      Easy Video Slider
                    </span>
                    <span>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6L9 17l-5-5"/></svg>
                      Edit Words Easily
                    </span>
                    <span>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6L9 17l-5-5"/></svg>
                      Instant Video Preview
                    </span>
                  </div>
                </div>
              </div>

              <div className="bento-box">
                <div className="bento-card-media">
                  <img src={`${import.meta.env.BASE_URL}gen/clipping-scissors.jpg`} alt="Effortless precision clipping" className="bento-thumb" />
                </div>
                <div className="bento-tag">Caption Styles</div>
                <h3>Choose Your Style</h3>
                <p>Pick simple subtitles or fun glowing colors with one tap.</p>
              </div>

              <div className="bento-box">
                <div className="bento-card-media">
                  <img src={`${import.meta.env.BASE_URL}gen/automatic-gears.jpg`} alt="Quiet silent local GPU rendering" className="bento-thumb" />
                </div>
                <div className="bento-tag">Super Fast</div>
                <h3>Uses Your Own PC</h3>
                <p>Creates videos in seconds. No waiting in line, no monthly subscriptions.</p>
              </div>

              <div className="bento-box">
                <div className="bento-card-media">
                  <img src={`${import.meta.env.BASE_URL}gen/zen-sloth-mascot.jpg`} alt="Zen calm recording with peace of mind" className="bento-thumb" />
                </div>
                <div className="bento-tag">Never Stops</div>
                <h3>Records With Screen Off</h3>
                <p>Keep recording even if you lock your phone or get a text.</p>
              </div>

              <div className="bento-box">
                <div className="bento-card-media">
                  <img src={`${import.meta.env.BASE_URL}gen/growth-trophy.jpg`} alt="Social media fame and creator growth" className="bento-thumb" />
                </div>
                <div className="bento-tag">Grow Online</div>
                <h3>Post Everywhere</h3>
                <p>Share to YouTube, Instagram, LinkedIn, Facebook, and X all at once.</p>
              </div>
            </div>
          </div>
        </section>

        {/* ── 6. FAQ ── */}
        <section className="section-padded bg-paper-deep" id="faq">
          <div className="shell faq-shell">
            <div className="section-header">
              <span className="section-eyebrow">FREQUENTLY ASKED QUESTIONS</span>
              <h2 className="section-title">Simple Questions & Answers</h2>
            </div>

            <div className="faq-accordion">
              {[
                {
                  q: "How does Dispatch help me grow on social media?",
                  a: "Posting short videos is the best way to get noticed online. But editing takes hours. Dispatch does all the cutting and captions for you so you can grow fast without burning out."
                },
                {
                  q: "Which platforms does Dispatch publish to?",
                  a: "Dispatch posts to YouTube Shorts, Instagram Reels, LinkedIn, Facebook Reels, and X. You can post to all 5 at the same time."
                },
                {
                  q: "What do I actually have to do as a creator?",
                  a: "You only press two buttons: Record when you start, and Stop when you finish. Dispatch does everything else."
                },
                {
                  q: "Will Dispatch post without my permission?",
                  a: "Never. Dispatch has a strict 'Nothing Posts Without You' policy. Every clip sits in your PC review studio. You can scrub through it, edit word text or timings, and only what you approve gets published."
                },
                {
                  q: "Do I have to pay monthly cloud subscription fees?",
                  a: "No. It runs on your own computer. You never have to pay expensive monthly fees."
                }
              ].map((item, idx) => (
                <div
                  key={idx}
                  className={`faq-row ${activeFaq === idx ? 'faq-row-active' : ''}`}
                  onClick={() => setActiveFaq(activeFaq === idx ? null : idx)}
                >
                  <div className="faq-q">
                    <span>{item.q}</span>
                    <span className="faq-toggle">{activeFaq === idx ? '−' : '+'}</span>
                  </div>
                  {activeFaq === idx && (
                    <div className="faq-a">
                      <p>{item.a}</p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── 7. DOWNLOAD / ACCESS GATE ── */}
        <section className="section-padded bg-paper" id="download">
          <div className="shell download-shell">
            <div className="access-card">
              <div className="access-header">
                <span className="section-eyebrow">ALPHA INVITATION</span>
                <h2 className="access-title">Try Dispatch Today</h2>
                <p className="access-sub">Enter your invite code below to download the app.</p>
              </div>

              {inviteAccepted ? (
                <div className="access-unlocked">
                  <div className="unlocked-pill">Access Granted &bull; Invite Verified</div>
                  <h3>Dispatch Build 1.7.29-alpha</h3>
                  <p>Download the free Android app and start recording.</p>
                  <div className="unlocked-buttons">
                    <a className="btn-hero-primary" href={RELEASES} target="_blank" rel="noreferrer">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                        <polyline points="7 10 12 15 17 10"/>
                        <line x1="12" y1="15" x2="12" y2="3"/>
                      </svg>
                      <span>Download Android APK (21 MB)</span>
                    </a>

                  </div>
                </div>
              ) : (
                <div className="access-forms">
                  <div className="form-box">
                    <h4>Have an invite code?</h4>
                    <p>Enter the private code from your invitation email.</p>
                    <form onSubmit={handleInvite} className="input-group">
                      <input
                        type="text"
                        className="text-input"
                        placeholder="Enter your invite code"
                        value={inviteCode}
                        onChange={(e) => {
                          setInviteCode(e.target.value);
                          setInviteError('');
                        }}
                      />
                      <button type="submit" className="btn-submit">
                        Unlock
                      </button>
                    </form>
                    {inviteError && <span className="error-text">{inviteError}</span>}
                  </div>

                  <div className="form-divider">
                    <span>or</span>
                  </div>

                  <div className="form-box">
                    <h4>Request an invite</h4>
                    <p>Enter your email to receive an access code in the next release batch.</p>
                    {emailSuccess ? (
                      <div className="success-box">
                        <span>✓ You're on the list. We'll send your invite code soon.</span>
                      </div>
                    ) : (
                      <form onSubmit={handleWaitlist} className="input-group">
                        <input
                          type="email"
                          className="text-input"
                          placeholder="creator@studio.com"
                          value={email}
                          onChange={(e) => setEmail(e.target.value)}
                        />
                        <button type="submit" className="btn-submit btn-submit-muted">
                          Request Access
                        </button>
                      </form>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>
      </main>

      {/* ── FOOTER ── */}
      <footer className="site-footer">
        <div className="shell footer-wrap">
          <div className="footer-left">
            <div className="footer-brand">
              <svg width="24" height="24" viewBox="0 0 28 28" fill="none">
                <path d="M8 19.5C6 17.5 6 14 8 12L13.5 6.5C15.5 4.5 19 4.5 21 6.5C23 8.5 23 12 21 14L15.5 19.5C13.5 21.5 10 21.5 8 19.5Z" stroke="#2563EB" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round"/>
                <path d="M12 16L16 12" stroke="#2563EB" strokeWidth="3.6" strokeLinecap="round"/>
              </svg>
              <span>dispatch</span>
            </div>
            <p className="footer-p">
              Record once, post vertical clips to YouTube, Instagram, LinkedIn, Facebook, and X.
            </p>
          </div>

          <div className="footer-right">
            <div className="footer-col">
              <h5>Navigation</h5>
              <a href="#how-it-works">How It Works</a>
              <a href="#channels">Auto-Publish</a>
              <a href="#pipeline">PC Engine</a>
              <a href="#studio">Review Gate</a>
              <a href="#download">Get Alpha</a>
            </div>

            <div className="footer-col">
              <h5>Legal</h5>
              <a href="./privacy.html" target="_blank" rel="noreferrer">Privacy Policy</a>
              <a href="./terms.html" target="_blank" rel="noreferrer">Terms of Service</a>
              <a href="./data-deletion.html" target="_blank" rel="noreferrer">Data Deletion</a>
            </div>
          </div>
        </div>

        <div className="shell footer-legal">
          <p>&copy; 2026 Resolviaai &bull; Dispatch is invite-only alpha software.</p>
          
        </div>
      </footer>
    </div>
  );
}
