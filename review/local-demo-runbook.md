# SPENDCTL local demo runbook

## What this demo is

The local walkthrough exercises the real request validator, estimator, fixture pricing provider, reserve policy, and upward integer micro-USD rounding. Its price values are illustrative fixtures marked `fixture_unverified`. It does **not** authenticate a person, read or mutate a budget ledger, call a local API, start Step Functions, launch EC2, or call AWS.

The React frontend can run as a visual preview. With no Cognito configuration it displays the intentional “Sign-in setup needed” screen; the current project has no local API server or mock-data mode. Once configured with real Cognito and API values it makes authenticated requests to that AWS backend. That integration has not been verified, and the project records say application stacks are not deployed.

## Prerequisites

- From the project root, `.venv` with project dependencies installed. If needed, set it up with Python 3.12:

  ```sh
  python3.12 -m venv .venv
  .venv/bin/python -m pip install -e '.[dev]'
  ```

- Node.js and npm. If `frontend/node_modules` is absent, install the lockfile-pinned frontend dependencies:

  ```sh
  cd frontend
  npm ci
  cd ..
  ```

- No AWS credentials, account, credentials file, `.env.local`, deployed resources, or network setup is needed for the offline walkthrough.

## Start and present

From the repository root, in Terminal 1:

```sh
make demo
```

Expected output includes:

```text
SPENDCTL local request walkthrough
Validated request: team=alpha, type=t3.micro, gp3=8GB, hours=4
Pricing source: fixture_unverified (illustrative fixture; not live AWS pricing)
Estimated compute: $0.048000
Estimated gp3 storage: $0.003507
Estimated total: $0.051507
Policy reserve at 1.10x: $0.056658
No budget was reserved; no AWS API, workflow, or EC2 instance was used.
```

For an optional local frontend preview, in Terminal 2:

```sh
cd frontend
npm run dev -- --host 127.0.0.1
```

Open the URL Vite prints (normally `http://127.0.0.1:5173`). With the repository's current unconfigured frontend, show the setup-needed preview and explain that it makes no AWS connection. Do not present it as an authenticated product session or live dashboard.

To show the offline validation smoke check in Terminal 1:

```sh
make verify
```

Expected: `Local smoke checks passed; no AWS APIs were called.` This check validates the sample shape and fixed-policy default; it does not start the app or test the full API workflow.

## What the current implementation demonstrates

- Request validation for the supported EC2 shape and bounds.
- A deterministic illustrative compute and gp3 estimate through the project estimator.
- The fixed 1.10 reserve multiplier and round-up to integer micro-USD.
- Frontend buildability and an honest unconfigured sign-in preview.

## What it does not demonstrate

- Real AWS prices, authentication, authorization, or a connected frontend/API session.
- Durable budget creation/reservation, idempotency against a database, workflow recovery, lifecycle cleanup, or billing reconciliation in this CLI demo.
- Step Functions execution, EC2 provisioning/termination, CloudWatch delivery, or any deployed resource behavior.
- A complete end-to-end user flow from browser request through backend ledger and provisioning.

## Cleanup

- Stop Vite with **Ctrl+C** in Terminal 2.
- The CLI demo is process-local and leaves no reservation or cloud resource. `make verify` is offline and non-mutating.
- `npm run build` writes generated static files to `frontend/dist`; the demo run does not require the build. No AWS cleanup command is needed or appropriate.

## Presenter notes and remaining work

For a local engineering walkthrough, the CLI demo plus the frontend preview is presentable now if the fixture and simulation boundaries above are stated. For a convincing interactive product demo, an essential gap remains: there is no local backend/mock mode, and the frontend currently needs real Cognito and API deployment outputs to exercise its data views. Implementing a reviewed local demo API mode or completing an approved AWS deployment would be separate work. AWS deployment is presently blocked by the dated readiness findings in `spendctl-readiness-report.md`; no AWS functionality is claimed as verified.

In this restricted audit environment, the frontend tests and production build pass, but Vite could not bind to `127.0.0.1:5173` (`listen EPERM`). Try the startup command on the presentation machine; do not treat build success as proof that the browser server or Cognito/API integration was exercised.
