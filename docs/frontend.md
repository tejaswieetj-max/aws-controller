# M10 React frontend

The Vite + React + TypeScript source is under `frontend/`. It uses Cognito's public app-client authorization-code flow with PKCE: a cryptographically random verifier, state and nonce are held in `sessionStorage`; token exchange uses `client_id` and never a client secret. API requests use the ID token expected by the API Gateway JWT authorizer. Team and group claims only control presentation and route parameters; the backend remains authoritative. An API 401 clears the browser session.

The application provides Overview, Cost estimator, Requests & reservations, Budgets, Alerts and read-only Calibration views. It uses all nine existing API routes and their current shapes. Request submission reuses a pending idempotency key for the same payload after network or 5xx uncertainty. Cancellation is shown only for ACTIVE requests and explains asynchronous cleanup. Currency formatting converts API integer micro-USD only for display. The UI labels fallback prices, empty calibration evidence and the unverified nature of API-reported calibration rows.

## Local development and build

```sh
cd frontend
cp .env.example .env.local
# Fill VITE_API_BASE_URL, VITE_COGNITO_HOSTED_UI_DOMAIN, VITE_COGNITO_CLIENT_ID,
# VITE_COGNITO_REDIRECT_URI, and VITE_COGNITO_LOGOUT_URI from reviewed outputs.
npm ci
npm run dev
npm test
npm run build
```

No secrets belong in `.env.local`; Vite embeds `VITE_*` values into browser assets. The public client must have the corresponding callback/logout origins configured in Cognito and the API's CORS origin allowlist. Current CDK Cognito callbacks default to localhost; a CloudFront URL must be configured with the documented context value before deployment.

Without the Cognito hosted UI domain and public client ID, the local app shows a `Sign-in setup needed` preview screen and disables sign-in. This means the frontend can be previewed without AWS credentials, but authenticated API features are unavailable until Cognito and API values are configured. `frontend/.env.local` is ignored by Git.

`WebStack` creates an encrypted, versioned, private S3 bucket, an OAC-restricted CloudFront distribution with HTTPS redirect, and deployment from `frontend/dist`. Build the frontend before CDK synthesis. S3 public access is blocked. CloudFront's viewer endpoint serves only static assets; all financial reads/writes still go through authenticated API Gateway. No CloudFront URL or Cognito callback is fabricated by this source. The stack exports the CloudFront URL, API URL, pool ID, client ID and Cognito issuer. Set frontend build values from reviewed stack outputs.

The CloudFront distribution currently uses the default `cloudfront.net` hostname.
AWS fixes that hostname's viewer security policy to TLSv1, regardless of the
`minimum_protocol_version` property in CDK. To enforce TLS 1.2 or newer, provide
a custom DNS name and an ACM certificate in `us-east-1`, then configure the
distribution's alias and viewer certificate before deploying it. No suitable
hosted zone or certificate was found in the inspected account.

## Verification boundary

Frontend tests use Node's built-in test runner for the API client's token, idempotency, ambiguity, 401 and currency behavior. Browser-level login, accessible interaction, responsive rendering, callbacks, CORS and CloudFront hosting require a built browser app and live account configuration; they are not verified by source tests or CDK synthesis.
