# CALIBUR Event Collector Specification

## 1. Purpose

Collect genuine, timestamped evidence needed to evaluate the CALIBUR budget-admission controller. Do not fabricate missing values or infer billing arrival times from billing-period timestamps.

## 2. Events to record

### A. Request received
- `request_id`: stable unique request identifier
- `request_timestamp`: UTC time the request was received
- `requested_resource_shape`: requested resource/service configuration
- `workload_type`: requested workload type

### B. Admission decision
- `request_id`: join key to the request
- `decision_timestamp`: UTC decision time
- `decision`: approved or denied
- `reserved_amount`: amount reserved
- `currency`: reservation currency
- `policy_version`: policy used
- `budget_cap`: budget limit at decision time
- `ledger_balance`: available budget at decision time

### C. Runtime observations
- `request_id` and actual AWS `resource_id`, where a real linkage exists
- `observation_timestamp`: UTC observation time
- `usage_quantity` and `usage_unit`, when available
- `resource_start_timestamp` and `resource_end_timestamp`, when observed

### D. Billing snapshots
- Actual provider cost and currency
- Real AWS resource identifier, where present
- Billing period and charge-period timestamps
- Export/report identifier and delivery timestamp
- `first_observed_timestamp`: when the collector first observed the record
- Snapshot hash or version identifier
- Previous and current costs when a real record changes

## 3. Timestamp rules

Record UTC timestamps and distinguish:
- Provider charge-period time
- S3 report delivery time
- Collector observation time

Do not treat these timestamps as interchangeable. A revision is counted only when the same documented billing key has different values in separate observed snapshots.

## 4. Data integrity

- Preserve raw exports unchanged.
- Record source, retrieval time, object key, size, and SHA-256 checksum.
- Never invent resource IDs, request IDs, costs, delays, or revisions.
- Keep unmatched billing records explicitly unmatched.
- Record observation end time and mark unresolved revision status as right-censored where applicable.

## 5. Initial limitations

The current FOCUS export contains genuine cost records but has not been linked to CALIBUR request IDs, admission decisions, or runtime observations. One observed delivery does not establish a billing revision history.

## 6. Implementation gate

Implement the collector only after reviewing the existing Spendctl logging, data models, and tests. Do not modify the production reservation policy or deploy new AWS resources without explicit approval.