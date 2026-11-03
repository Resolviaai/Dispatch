import { useState, type FormEvent } from 'react';
import { RELEASE, RELEASES, REPOSITORY, RELEASE_FACTS } from '../data/site';
import { ExternalHint } from './ExternalHint';

export const PREVIEW_INVITE_CODE = 'DSP-ALPHA';

export function AlphaGate() {
  const [inviteCode, setInviteCode] = useState('');
  const [inviteError, setInviteError] = useState('');
  const [inviteAccepted, setInviteAccepted] = useState(false);
  const [email, setEmail] = useState('');
  const [emailError, setEmailError] = useState('');
  const [waitlistNotice, setWaitlistNotice] = useState(false);

  function submitInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (inviteCode.trim().toUpperCase() === PREVIEW_INVITE_CODE) {
      setInviteAccepted(true);
      setInviteError('');
      return;
    }

    setInviteError('That code does not match. Check it and try again.');
  }

  function submitWaitlist(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const address = email.trim();

    if (!address || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(address)) {
      setEmailError('Enter an email address.');
      setWaitlistNotice(false);
      return;
    }

    setEmailError('');
    setWaitlistNotice(true);
  }

  return (
    <section className="access" id="download" aria-labelledby="access-title">
      <div className="shell">
        <div className="access-head">
          <h2 id="access-title">
            The alpha is open
            <br />
            by invitation.
          </h2>
        </div>

        <div className="access-ledger">
          <div className="access-row">
            <div className="access-copy">
              <h3>Have a code</h3>
              <p>Open the Android alpha and the Windows companion.</p>
            </div>

            {inviteAccepted ? (
              <div className="gate-accepted" role="status">
                <p className="gate-accepted-title">Code accepted.</p>
                <div className="gate-downloads">
                  <a
                    className="button button--primary"
                    href={RELEASES}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Download the APK
                    <ExternalHint />
                  </a>
                  <a className="text-link" href={REPOSITORY} target="_blank" rel="noreferrer">
                    Windows companion
                    <ExternalHint />
                  </a>
                  <a className="text-link" href={RELEASE} target="_blank" rel="noreferrer">
                    Release notes
                    <ExternalHint />
                  </a>
                </div>
                <p className="gate-note">
                  The Android build is 1.7.23. The Windows companion runs from the repository with
                  Python and FFmpeg.
                </p>
              </div>
            ) : (
              <form className="gate-form" onSubmit={submitInvite} noValidate>
                <label className="gate-label" htmlFor="invite-code">
                  Invite code
                </label>
                <div className="gate-field">
                  <input
                    className="gate-input"
                    id="invite-code"
                    name="code"
                    type="text"
                    value={inviteCode}
                    onChange={(event) => {
                      setInviteCode(event.target.value);
                      if (inviteError) setInviteError('');
                    }}
                    placeholder="DSP-XXXX"
                    autoComplete="off"
                    autoCapitalize="characters"
                    spellCheck={false}
                    aria-invalid={inviteError ? true : undefined}
                    aria-describedby={inviteError ? 'invite-note invite-error' : 'invite-note'}
                  />
                  <button className="button button--primary" type="submit">
                    Check code
                  </button>
                </div>
                <p className="gate-note" id="invite-note">
                  Preview gate. Codes are checked in this browser.
                </p>
                {inviteError ? (
                  <p className="gate-error" id="invite-error" role="alert">
                    {inviteError}
                  </p>
                ) : null}
              </form>
            )}
          </div>

          <div className="access-row">
            <div className="access-copy">
              <h3>Need a code</h3>
              <p>Ask for one with the next build.</p>
            </div>

            <form
              className="gate-form"
              onSubmit={submitWaitlist}
              noValidate
              data-endpoint=""
            >
              <label className="gate-label" htmlFor="waitlist-email">
                Email address
              </label>
              <div className="gate-field">
                <input
                  className="gate-input"
                  id="waitlist-email"
                  name="email"
                  type="email"
                  value={email}
                  onChange={(event) => {
                    setEmail(event.target.value);
                    if (emailError) setEmailError('');
                    if (waitlistNotice) setWaitlistNotice(false);
                  }}
                  placeholder="you@studio.com"
                  autoComplete="email"
                  aria-invalid={emailError ? true : undefined}
                  aria-describedby={emailError ? 'waitlist-note waitlist-error' : 'waitlist-note'}
                />
                <button className="button button--quiet" type="submit">
                  Join the waitlist
                </button>
              </div>
              <p className="gate-note" id="waitlist-note">
                Preview form. No address is sent or stored.
              </p>
              {emailError ? (
                <p className="gate-error" id="waitlist-error" role="alert">
                  {emailError}
                </p>
              ) : null}
              {waitlistNotice ? (
                <p className="gate-status" role="status">
                  Nothing was sent. This form is not connected yet.
                </p>
              ) : null}
            </form>
          </div>
        </div>

        <div className="release-strip">
          <h3 className="release-strip-title">Current build</h3>
          <dl className="release-facts">
            {RELEASE_FACTS.map((fact) => (
              <div key={fact.label}>
                <dt>{fact.label}</dt>
                <dd>{fact.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </section>
  );
}
