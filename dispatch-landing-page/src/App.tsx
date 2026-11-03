import { AlphaGate } from './components/AlphaGate';
import { ExternalHint } from './components/ExternalHint';
import { HeroArt } from './components/HeroArt';
import { HeroVideo } from './components/HeroVideo';
import { RELEASE, RELEASES, REPOSITORY } from './data/site';

export default function App() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <header className="masthead">
        <div className="shell masthead-inner">
          <a className="brand" href="#main" aria-label="Dispatch, back to top">
            <img
              src="/assets/dispatch-lockup-white.svg"
              alt=""
              width="108"
              height="30"
            />
          </a>
          <p className="masthead-note">Capture, review, dispatch.</p>
          <a className="masthead-link" href="#download">
            Get the alpha
          </a>
        </div>
      </header>

      <main id="main" tabIndex={-1}>
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero-media" aria-hidden="true">
            <HeroArt />
            <HeroVideo />
          </div>
          <div className="hero-shade" aria-hidden="true" />
          <div className="hero-grain" aria-hidden="true" />

          <div className="shell hero-inner">
            <div className="hero-body">
              <div className="hero-copy">
                <h1 id="hero-title">
                  <span>Keep the hour.</span>
                  <span>Post the minute.</span>
                </h1>
                <p className="hero-lede">
                  Long sessions in. Short captioned clips out. You approve every one.
                </p>
                <div className="hero-actions">
                  <a className="button button--primary" href="#download">
                    Get the alpha
                  </a>
                  <a className="button button--ghost" href="#workflow">
                    See the workflow
                  </a>
                </div>
              </div>

              <dl className="hero-spec">
                <div>
                  <dt>Capture</dt>
                  <dd>Android, 1080p/30 target</dd>
                </div>
                <div>
                  <dt>Process</dt>
                  <dd>Windows PC</dd>
                </div>
                <div>
                  <dt>Publish</dt>
                  <dd>Review first</dd>
                </div>
              </dl>
            </div>
          </div>
        </section>

        <section className="routes" id="workflow" aria-labelledby="workflow-title">
          <div className="shell">
            <div className="section-head">
              <h2 id="workflow-title">
                Two ways in.
                <br />
                One desk.
              </h2>
              <p>Record on Android, or bring a YouTube link. Both routes land on your Windows PC.</p>
            </div>

            <div
              className="route-board"
              role="group"
              aria-label="Two routes into the same Windows PC"
            >
              <article className="route">
                <div className="route-head">
                  <h3>Record on Android</h3>
                  <p>CameraX at a 1080p/30 target, in ten-minute segments.</p>
                </div>
                <ol className="route-steps">
                  <li>
                    <span className="step-index" aria-hidden="true">
                      01
                    </span>
                    <span className="step-name">Record</span>
                    <span className="step-place">Phone</span>
                  </li>
                  <li>
                    <span className="step-index" aria-hidden="true">
                      02
                    </span>
                    <span className="step-name">Upload unlisted</span>
                    <span className="step-place">YouTube</span>
                  </li>
                  <li>
                    <span className="step-index" aria-hidden="true">
                      03
                    </span>
                    <span className="step-name">Ingest</span>
                    <span className="step-place">Windows PC</span>
                  </li>
                </ol>
              </article>

              <article className="route">
                <div className="route-head">
                  <h3>Bring a YouTube link</h3>
                  <p>Public or unlisted. Private videos stop at the visibility gate.</p>
                </div>
                <ol className="route-steps">
                  <li>
                    <span className="step-index" aria-hidden="true">
                      01
                    </span>
                    <span className="step-name">Paste link</span>
                    <span className="step-place">Studio</span>
                  </li>
                  <li>
                    <span className="step-index" aria-hidden="true">
                      02
                    </span>
                    <span className="step-name">Check visibility</span>
                    <span className="step-place">Studio</span>
                  </li>
                  <li>
                    <span className="step-index" aria-hidden="true">
                      03
                    </span>
                    <span className="step-name">Ingest</span>
                    <span className="step-place">Windows PC</span>
                  </li>
                </ol>
              </article>
            </div>
          </div>
        </section>

        <section className="review" id="review" aria-labelledby="review-title">
          <div className="shell review-grid">
            <div className="review-copy">
              <h2 id="review-title">Nothing leaves without you.</h2>
              <p>Dispatch can suggest a range, time captions, and render. It cannot approve.</p>
            </div>

            <ul className="review-list">
              <li className="review-item">
                <h3>Captions land on the word.</h3>
                <p>Word timings drive the captions. The active word lights up.</p>
              </li>
              <li className="review-item">
                <h3>Approval is a real gate.</h3>
                <p>Every clip waits for your approval. Publishing needs a connected account.</p>
              </li>
              <li className="review-item">
                <h3>The PC does the cut.</h3>
                <p>FFmpeg renders on your Windows machine, not in a cloud queue.</p>
              </li>
            </ul>
          </div>
        </section>

        <section className="pipeline" id="pipeline" aria-labelledby="pipeline-title">
          <div className="shell">
            <div className="section-head pipeline-head">
              <h2 id="pipeline-title">Five stages on your PC.</h2>
              <p>Both routes run the same pipeline on the PC.</p>
            </div>

            <table className="stage-table">
              <caption className="sr-only">The five stages of the Dispatch pipeline</caption>
              <thead>
                <tr>
                  <th scope="col">Stage</th>
                  <th scope="col">What happens</th>
                  <th scope="col">Output</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Verify</th>
                  <td data-label="What happens">Check the source. Persist the job.</td>
                  <td data-label="Output">Job record</td>
                </tr>
                <tr>
                  <th scope="row">Transcribe</th>
                  <td data-label="What happens">YouTube captions first. Gemini when needed.</td>
                  <td data-label="Output">Word timings</td>
                </tr>
                <tr>
                  <th scope="row">Analyze</th>
                  <td data-label="What happens">Gemini proposes a range you can edit.</td>
                  <td data-label="Output">Clip range</td>
                </tr>
                <tr>
                  <th scope="row">Render</th>
                  <td data-label="What happens">FFmpeg cuts and burns the captions.</td>
                  <td data-label="Output">Captioned MP4</td>
                </tr>
                <tr>
                  <th scope="row">Finalize</th>
                  <td data-label="What happens">Store the clip for review.</td>
                  <td data-label="Output">Review item</td>
                </tr>
              </tbody>
            </table>

            <p className="pipeline-note">
              Clips run 5 to 180&nbsp;s. Source: seven days on the PC.
            </p>
          </div>
        </section>

        <AlphaGate />
      </main>

      <footer className="colophon">
        <div className="shell colophon-grid">
          <div className="colophon-brand">
            <img
              src="/assets/dispatch-lockup-white.svg"
              alt="Dispatch"
              width="98"
              height="27"
            />
            <p>Alpha software, built in the open.</p>
          </div>

          <nav className="colophon-links" aria-label="Dispatch links">
            <a href={REPOSITORY} target="_blank" rel="noreferrer">
              Repository
              <ExternalHint />
            </a>
            <a href={RELEASES} target="_blank" rel="noreferrer">
              Releases
              <ExternalHint />
            </a>
            <a href={RELEASE} target="_blank" rel="noreferrer">
              This build
              <ExternalHint />
            </a>
          </nav>

          <div className="colophon-credits">
            <p>Hero field rendered for Dispatch. No stock imagery.</p>
            <p>
              Type: Fraunces and Manrope,{' '}
              <a href="https://openfontlicense.org" target="_blank" rel="noreferrer">
                SIL Open Font License
                <ExternalHint />
              </a>
              .
            </p>
          </div>
        </div>
      </footer>
    </>
  );
}
