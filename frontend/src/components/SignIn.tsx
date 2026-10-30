import { Icon } from './Icon';
import { InlineNotice } from './ui';

export function SignIn({
  error,
  onSignIn,
}: {
  error: Error | null;
  onSignIn: () => void;
}) {
  return (
    <div className="auth-page">
      <div className="auth-visual" aria-hidden="true">
        <div className="auth-orbit orbit-one" />
        <div className="auth-orbit orbit-two" />
        <div className="auth-visual-copy">
          <span className="eyebrow light">Cloud financial control</span>
          <h1>Reserve before you run.</h1>
          <p>
            Spendctl turns a resource request into an auditable budget decision. CALIBUR sets the
            hold, the ledger enforces it, and every step is visible.
          </p>
          <ul className="auth-points">
            <li><Icon name="shield" size={16} />Server-authoritative admission</li>
            <li><Icon name="activity" size={16} />Atomic ledger invariants</li>
            <li><Icon name="calibur" size={16} />Explainable reserve policy</li>
          </ul>
        </div>
      </div>

      <div className="auth-panel">
        <div className="auth-brand">
          <span className="brand-mark" aria-hidden="true">S</span>
          <span>spendctl<small>financial control</small></span>
        </div>

        {error && (
          <InlineNotice tone="danger" title="Sign-in failed">
            {error.message}
          </InlineNotice>
        )}

        <span className="eyebrow">Secure workspace</span>
        <h2>Spend with confidence.</h2>
        <p className="auth-sub">
          Your team and role come from a verified Cognito session. The API still authorizes every
          financial action on the server.
        </p>
        <button className="button primary full" onClick={onSignIn}>
          Continue with secure sign-in <Icon name="arrow" size={17} />
        </button>
        <p className="auth-fineprint">Sign-in uses the configured Cognito hosted UI.</p>
      </div>
    </div>
  );
}

export function SetupNeeded() {
  return (
    <main className="fullscreen">
      <section className="auth-card">
        <span className="brand-mark large">S</span>
        <span className="eyebrow">Configuration required</span>
        <h1>Connect the frontend</h1>
        <p>
          Copy <code>.env.example</code> to <code>.env.local</code> and set the Cognito hosted UI
          domain, public app client ID, and API base URL.
        </p>
        <p className="muted">No AWS connection is made by this screen.</p>
      </section>
    </main>
  );
}
