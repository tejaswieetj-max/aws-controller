# CALIBUR and Spendctl live validation

**Checked:** 2026-10-26 using `AWS_PROFILE=calibur-validation` in `ap-south-1`.
Account and user identifiers are omitted.

All AWS calls in this check were read-only. No budget, request, reservation,
workload, or infrastructure was created or changed.

## Application API

The deployed HTTP API currently has 15 routes, all configured with its Cognito
JWT authorizer. The estimate, request creation/history, budget, and CALIBUR
routes map to the expected M5 Lambda handlers. The `$default` stage has
automatic deployment and API access logging enabled.

In the retained API access logs:

- `POST /v1/estimate` returned HTTP 200.
- `GET /v1/reservations` returned HTTP 200 repeatedly.
- `GET /v1/budgets/{team}` returned HTTP 404.
- One `POST /v1/requests` returned HTTP 409.
- No HTTP 500 was found in the retained API access-log window.
- No recent `ERROR` records were found in the Reservations Lambda log group.

Thus the Requests page's list call is `GET /v1/reservations`; the observed
deployed call succeeds. The user-reported 500 was not reproducible from retained
logs, so its root cause cannot be attributed from the evidence available. The
missing budget explains the budget 404; the 409 response body was not read, so
the reason for that denial is not asserted here.

The deployed state-estimate Lambda has an empty `CALIBUR_KALMAN_CONFIG`. The
handler requires owner-reviewed transition, process-noise, source mapping, and
observation-noise settings and has no defensible defaults. No access-log call
to the state-estimate route was found. Locally, a missing or invalid estimator
configuration now returns an explicit 503 configuration error instead of an
internal 500; the AWS function has not been modified by this work.

Read-only DynamoDB counts returned zero budget records and zero reservation
records. This matches the budget 404 and means no successful admission or
reservation workflow is evidenced by the current ledger.

The API access log shows requests passing the JWT authorizer, but no user token
or response bodies were inspected. No user was created, no password was reset,
and no authentication bypass was attempted.

## Billing

`bcm-data-exports:ListExports` is denied for the current AWS identity, so the
current Data Export state and delivered billing records could not be verified.
The BillingStack configuration previously inspected had reconciliation
disabled. No live billing integration is claimed as verified.

## Local changes and validation

The frontend now accepts the backend's `error.details` envelope, preserves
CALIBUR denial reason codes in its message, and displays request, budget,
dashboard, estimate, metrics, and evidence-load errors as errors rather than
empty or zero results. The current local API maps missing estimator
configuration to `CALIBUR_ESTIMATOR_UNCONFIGURED` (503).

The request → reservation workflow remains unverified live: no budget record
was found by the observed budget lookup, request creation returned 409, and no
successful reservation or provisioned workload was established. Local
automated tests cover the API handler, admission, atomic ledger, lifecycle, and
billing logic. They do not establish live AWS end-to-end behavior.

Research remains **AMBER / `NOT_RUN_DATA_GAP`**. Real request, decision,
reservation, resource, telemetry, billing availability, revision, and release
records are not linked into an eligible evaluation sample.

## Remaining blockers

1. Provide the request URL, approximate timestamp, and API Gateway request ID
   for the reported 500 if it occurred outside the retained log window or in a
   different environment.
2. Configure `CALIBUR_KALMAN_CONFIG` with reviewed model/noise assumptions
   before using the live state-estimate route.
3. Have an authorized administrator create the required team/month budget if
   the application is meant to admit new requests. No budget write was made.
4. Grant read-only AWS Data Exports access or provide verified export
   provenance before claiming live billing access.
5. Provide an existing authorized Cognito session for protected response-body
   inspection and a separately approved, safe workload test before claiming
   live admission-to-resource-to-billing verification.
