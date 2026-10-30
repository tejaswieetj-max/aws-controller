# M8 billing reconciliation (FOCUS 1.2 and CUR 2.0)

## Implemented local path

`BillingStack` keeps the export bucket private and encrypted, stores Athena
results in its separate private encrypted bucket, creates the Glue database and
scan-limited Athena workgroup, and adds an S3 manifest notification to a
Python 3.12 reconciliation Lambda. The Data Export itself and its Glue table
remain manually configured. `BillingSchema` selects `focus` (default) or `cur`.
`EnableBillingReconciliation` defaults to `true`; set it to `false` to stop all
billing ledger changes. The Lambda also waits until a billing period ended at
least `BillingFinalizationDelayDays` (default 60) earlier. These gates reduce
risk; they do not prove that an export is complete or final.
The synthesized export bucket policy grants only `s3:PutObject` to the AWS Data
Exports service principal, constrained by the source account and that account's
Data Exports ARN in `us-east-1`. When creating the export, review the policy the
console proposes to apply and read back the final bucket policy; preserve the
project's TLS-only deny. The export cannot be created until the BillingStack
bucket exists and its output name is known.
The reconciler uses 512 MB and a five-minute timeout to poll Athena; M0–M5 API
Lambdas keep the 256 MB/10-second defaults. Set `CurExportPrefix` to the exact
dedicated export object prefix so both the trigger and S3 read policy stay
scoped. The stack currently creates and owns its export bucket. Reusing a
pre-existing export bucket (for example the live observations bucket) is not
implemented; it needs a follow-up change that imports the bucket, owns its
notification configuration, and grants the reconciler read access without
committing an account-specific name.

The handler validates a Data Export manifest, queries the configured Glue table
for the manifest's billing period and, for FOCUS, only the manifest's declared
data files, normalizes rows, attributes them by an exact `reservation-id` tag
or an exact resource-ID mapping, then asks the existing DynamoDB ledger to
apply a versioned correction. The query uses a fixed field list and a validated
period literal. Athena output is directed through the enforced
`spendctl-dev-billing` workgroup, whose query scan cap is 1 GiB.

## Record and money semantics

The parser currently expects these CUR 2.0 fields: `identity_line_item_id`,
`bill_billing_period_start_date`, `line_item_usage_start_date`,
`line_item_usage_end_date`, `line_item_usage_account_id`,
`product_product_code`, `line_item_usage_type`, `line_item_operation`,
`line_item_resource_id`, `line_item_line_item_type`,
`line_item_currency_code`, `line_item_unblended_cost`, optional
`line_item_net_unblended_cost`, and optional resource tag columns
`resource_tags_user_reservation_id` / `resource_tags_user_team`. These are
schema assumptions covered by fixtures, not live schema verification. Missing
required columns fail closed and emit `BillingSchemaInvalid`.

For FOCUS 1.2 the required fields are `BillingPeriodStart`,
`ChargePeriodStart`, `ChargePeriodEnd`, `ChargeCategory`, `BilledCost`,
`BillingCurrency`, and `x_ServiceCode`; `ResourceId`, `Tags`, `x_Operation`,
`SkuMeter`, and the account columns are read when present. `BilledCost` is the
ledger target. `EffectiveCost` is not substituted because amortized commitment
allocation is a different cost semantic. FOCUS `Tags` keys `reservation-id` and
`team` are the only accepted attribution tags. EC2 `instance/...` and
`volume/...` ARNs are reduced to the bare resource ID recorded by the ledger;
other ARNs are passed through unchanged and normally remain out of scope.

FOCUS 1.2 has no verified row-level unique key (`InvoiceId` is null and `Tags`
is empty in inspected deliveries). A delivery is therefore treated as one
complete snapshot: all rows are aggregated per reservation and the snapshot
total replaces the prior target. Rows are not deduplicated by content because
identical descriptive rows can be legitimate separate charges. The revision is
the manifest `executionId`; revision order is the observed delivery timestamp
in the S3 key folder (`billing_period=YYYY-MM/<timestamp>-<executionId>/`).
A stale delivery is skipped with `BillingStaleRevision` instead of rolling a
newer amount back. The query is restricted to the manifest's `dataFiles` via
Athena `"$path"`, so files from another delivery for the same period cannot be
double counted.

All input amounts are parsed with `Decimal`. USD is multiplied by 1,000,000 and
rounded to integer micro-USD with `ROUND_HALF_EVEN`; positive and negative
amounts use the same conversion. For CUR, the net unblended cost is selected
when present, otherwise unblended cost is selected. Only positive `Usage` /
`ChargeCategory=Usage` rows for `AmazonEC2` and `AmazonEBS` enter reservation
totals. Credits, refunds, discounts, taxes, fees, non-usage records, non-USD
records, and other services are not applied to a reservation. Negative
adjustments are excluded from per-reservation cost; they do not increase a
team's available budget. This avoids charging/refunding an individual team on
ambiguous shared adjustments.

The reconciliation is a target amount for attributable usage in one billing
period. It applies `new target - previous final target` in the same DynamoDB
transaction that writes the settlement version and `BILLING#<reconciliation>`
audit row. The audit records period, source revision, input digest, prior/new
actual, delta, settlement ID/version and line-item identities. Replaying the
same snapshot is a no-op. A changed revision gets a new version and applies
only the difference. An overrun may make available funds negative while the
ledger invariant remains true and the existing overrun alert is recorded.

The resource mappings use `RESOURCE#<resource-id>` records in the existing
Reservations table, written atomically with M6 workflow resource outcomes.
This preserves resource identity beyond later lifecycle cleanup. A resource
tag is checked against that mapping when both exist; conflicts and team-tag
mismatches remain unattributed and emit `BillingAttributionAmbiguous`. No
approximate timestamp, team-only, or instance-type attribution is allowed.

No CUR rows are not evidence of zero cost. The current handler only reconciles
reservations with positive attributable usage rows; it never refunds a
reservation for absent rows. This intentionally leaves true-zero usage and
records whose only charges are credits/discounts unresolved rather than
inferring a refund.

For additional safety, the current implementation leaves attribution unresolved
when lifecycle timestamps are missing or a reservation runtime spans billing
periods. The EC2/EBS charges then remain out of CUR correction and the
provisional/final M6/M7 accounting stays unchanged. Supporting cross-period
reservations requires aggregating complete snapshots for all covered periods
before one target can be applied; it is an explicit implementation limitation.

## Revision, deduplication, and recovery

For CUR, the event identity is the manifest's assembly ID/version and billing
period. Manifest `assemblyId` (or `assemblyVersion`) is the revision identity;
manifest `createTimestamp` is the source order. For FOCUS, `executionId` is the
revision identity and the S3 delivery-folder timestamp is the source order;
Data Exports does not put `createTimestamp` or `billingPeriod` in the real
manifest. A stale timestamp is rejected by a conditional ledger update, so S3
notifications arriving out of order cannot roll a newer amount back; the FOCUS
handler treats that rejection as a skip. Each CUR line item uses
`identity_line_item_id` plus manifest object identity. Duplicate identical CUR
line items are ignored; the same identity with different content fails the
whole batch. FOCUS rows use a snapshot-scoped ordinal because no verified
row-level key exists, so identical rows are aggregated. Reconciliation IDs
include reservation, period, source revision and digest. Ledger settlement,
budget delta, settlement version, and audit row are one DynamoDB transaction.
If the transaction outcome is ambiguous, retry with the same manifest/revision;
do not manufacture a new ID. Conflicting ledger state raises an error and emits
a `BillingLedgerConflict` event. Query/schema/ingestion failures are visible and
leave balances unchanged.

Athena's manifest event is assumed to represent a delivered export assembly;
the 60-day finalization delay is an operational guard, not a guarantee. For
FOCUS the manifest's `dataFiles` must be non-empty and inside the same delivery
folder; `additionalOutputFiles` must be empty. Missing data files or a manifest
that declares files but yields zero rows fail closed. The manifest shape, table
view of revisions, tag columns, and assembly semantics must be verified against
an actual account export before operational reliance. The query filters the
billing-period column (or partition) and selects only needed fields. Partition
pruning is not assumed because partition columns vary by export configuration;
the 1 GiB workgroup limit will stop oversized scans.

## Required IAM

The reconciler role can read CUR objects from the export bucket, write/query
results only in the result bucket, start queries in the one Billing workgroup,
read the `spendctl_billing` Glue catalog, read/update the Budgets and
Reservations tables and write overrun alerts there, publish billing events to
the lifecycle event bus, and write its own CloudWatch log group. Athena query
execution read APIs require wildcard resource scope in the current IAM service
authorization model; query start is scoped to the named workgroup. Live IAM
evaluation remains unverified.

## Human-run setup and verification — do not run as part of local tests

1. **VERIFY:** In AWS Billing in `us-east-1`, create a FOCUS 1.2 (or CUR 2.0)
   Data Export to the deployed `CurExportsBucketName` and `CurExportPrefix`,
   with Parquet output. Record its export name, prefix, delivery
   principal/policy, manifest sample and Glue table name. The bucket
   notification and role policy are created by CDK; the billing console export
   is not. Leave `BillingSchema` at `focus` for a FOCUS export.
2. **VERIFY:** Activate `reservation-id` and `team` cost allocation tags and
   record when each first appears in exported rows. Tag activation can lag. In
   the inspected FOCUS deliveries both tag maps were empty, so attribution
   currently depends on exact resource mapping.
3. **VERIFY:** Inspect the actual manifest and Glue table schema in Athena.
   Compare every field in `_REQUIRED_FOCUS_COLUMNS` or `_REQUIRED_CUR_COLUMNS`
   and the optional field list in `billing_handlers.py` with the real schema.
   Confirm the table represents the current export revision, and identify
   partition columns. Do not rely on the default-enabled gate until this
   passes.
   In the Athena console, select the synthesized `spendctl-dev-billing`
   workgroup and run `DESCRIBE spendctl_billing.<configured_table>;`, then a
   bounded representative `SELECT ... LIMIT 20` including the candidate line
   item ID, cost, resource ID and tag columns. Confirm the scanned bytes stay
   under the workgroup cap. Redact account/resource identifiers in saved
   evidence; do not use the table scan as proof of completeness.
4. **VERIFY:** Confirm the manifest is emitted after all data files are
   readable, the role can query only the intended table/workgroup, and a
   representative query stays below the 1 GiB scan cap.
5. **VERIFY:** With separate approval, compare a known tagged EC2/EBS bill to
   the normalized per-reservation amount, validate credits/refunds treatment,
   verify revised exports apply deltas once, and inspect the ledger invariant
   and `BudgetOverrun`/billing events.
6. **VERIFY:** Review expected costs before enabling: S3 storage and requests,
   Athena scans (capped per query but billed), Glue catalog use, Lambda
   invocations, and CloudWatch/EventBridge activity. No Data Export fee or
   account-specific billing estimate is claimed here.

## Local recovery

For `BillingSchemaInvalid`, keep reconciliation disabled, inspect the redacted
Glue schema and update the explicit field mapping/tests. For query/ingestion
failures, retry the same manifest and revision after the cause is fixed. For
ambiguous attribution, repair or verify the resource history/tag, then replay
the same export. For a ledger conflict, inspect the settlement version and
`BILLING#…` audit item before retrying. Never delete audit rows or manually
change budget balances. Missing or delayed data is not grounds for release or
refund.
