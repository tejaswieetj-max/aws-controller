# Replay not run: data gap

Generated: 2026-10-26T13:04:31.992616Z
Protocol: `research/protocol.md`
Manifest: `research/data_manifest.md`
Ledger export: none supplied

**Status:** `NOT_RUN_DATA_GAP`
**Feasibility gate:** `AMBER` (unchanged)

No comparative evaluation was run. No metric table was written. No request,
reservation, telemetry, billing row, timestamp, or revision was created.

## Missing preconditions

### admission_records

- Requirement: real request/decision/reservation records
- Observed: 0 reservation record(s) supplied
- Needed: operator export of Spendctl admission/reservation records

### resource_linkage

- Requirement: request -> resource/runtime linkage with an observed timestamp
- Observed: 0 reservation(s) carry a resource reference and creation time
- Needed: reservation records containing resource identity and real timestamps

### final_billed_labels

- Requirement: final billed cost joined to the reservation by a verified key
- Observed: 0 billing record(s) carry a final amount and reservation_id
- Needed: settled billing records joined by a documented key

### first_availability

- Requirement: first-observability timestamp for each billing amount
- Observed: 0 billing record(s) carry a first-observed timestamp
- Needed: collector or export log recording when the amount first became observable

### stable_billing_key

- Requirement: stable billing key for revision comparison
- Observed: 0 billing record(s) carry a stable key
- Needed: confirmed row-level key across separately observed deliveries

### budget_context

- Requirement: authoritative budget/ledger state at each decision time
- Observed: 0 reservation(s) carry budget state at decision
- Needed: exported ledger snapshot per decision

### telemetry

- Requirement: observed usage telemetry linked to the reservation
- Observed: 0 record(s) carry telemetry timestamps
- Needed: real CloudWatch/telemetry observations with source timestamps

### revision_history

- Requirement: observed billing revision/arrival history with censoring
- Observed: 0 record(s) carry revision lineage
- Needed: validated revision pairs across separately observed exports

## Source verification

| File | Role | SHA-256 verified | Rows | Schema |
|---|---|---|---|---|
| `data/raw/focus12/CALIBUR_FOCUS_12-00001.snappy.parquet` | focus_delivery | True | 12200 | True |
| `data/raw/focus12/delivery_2026-10-25/CALIBUR_FOCUS_12-00001.snappy.parquet` | focus_delivery | True | 12404 | True |
| `data/raw/focus12/delivery_2026-10-25T11/CALIBUR_FOCUS_12-00001.snappy.parquet` | focus_delivery | True | 12579 | True |
| `data/raw/focus12/CALIBUR_FOCUS_12-Manifest-FOCUS.json` | focus_manifest | True | unknown | n/a |
| `data/raw/focus12/delivery_2026-10-25T11/CALIBUR_FOCUS_12-Manifest-FOCUS.json` | focus_manifest | True | unknown | n/a |
| `data/raw/focus12/delivery_2026-10-25T11/CALIBUR_FOCUS_12-Manifest.json` | focus_manifest | True | unknown | n/a |

## What would unblock the run

1. One or more real Spendctl admission decisions exported from the
   authoritative ledger, each with decision time, team, budget period,
   available budget, reserved amount, and a resource identifier.
2. A settled billing record joined to that reservation by a documented,
   verified key, with the currency and amount preserved.
3. A first-observability timestamp for each billing amount. Billing-period
   dates and S3 `LastModified` are not arrival times.
4. A validated stable billing key and at least two separately observed
   versions before claiming a revision or a revision delay.
5. Real telemetry with source record IDs and timestamps if the early-warning
   channel is to be evaluated.

Once these exist, extend the runner with the matching, chronology, and
leakage checks; do not substitute generated values.
