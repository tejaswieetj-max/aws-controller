# CALIBUR Repository Audit

## Existing capabilities
- Atomic budget reservation and ledger operations exist in `services/spendctl/ledger.py`.
- Admission decisions and shadow margin candidates are recorded in `services/spendctl/admission.py`.
- Reservation lifecycle timestamps and resource identifiers are recorded by the workflow handlers.
- Billing reconciliation supports identified records, revisions, and attribution checks.
- Structured logging and CloudWatch Embedded Metric Format support exist.

## Research limitations
- `research/run.py` generates synthetic costs and feedback delays.
- `research/evaluate.py` evaluates reservation-level observations; the current FOCUS export has not been linked to those reservations.
- The billing normalizer expects CUR 2.0 fields and cannot directly normalize the current FOCUS 1.2 export.
- Existing admission logs do not capture the complete budget state at decision time.
- One FOCUS delivery does not establish real billing revisions.

## Safety constraints
- Preserve the existing production admission policy and atomic ledger.
- Keep synthetic experiments explicitly classified as synthetic.
- Do not invent missing IDs, costs, arrival times, or revisions.
- Do not deploy new AWS resources without explicit approval.

## Decision
Retain the existing Spendctl architecture. Implement FOCUS ingestion and research event collection only after their interfaces, provenance requirements, and tests are defined. Keep empirical validation status AMBER until linked real-event evidence is available.