# CALIBUR Data Feasibility Assessment

## Decision: AMBER

**Reason:** Genuine AWS billing data is available, but the linked event history required to validate CALIBUR's central research claim has not been established.

## 1. Data acquired

- Source: AWS FOCUS 1.2 Data Export with AWS columns.
- Local file: `data/raw/focus12/CALIBUR_FOCUS_12-00001.snappy.parquet`
- Records: 12,200 rows and 60 columns.
- Observed delivery: October 25, 2026, approximately 1:14 AM IST.
- Positive usage charges before credits: $18.77972 USD.
- Credits: -$18.77972 USD.
- Billing categories: Usage, Credit, and Tax.
- Services include EC2, RDS, and VPC.
- The original Parquet file has been downloaded and inspected.

## 2. Evidence not yet established

- Request IDs and request timestamps linked to billed resources.
- Historical admission decisions and reserved amounts.
- Runtime observations linked to the same requests and billed resources.
- The first time each billing record became available to the controller.
- Multiple dated snapshots demonstrating actual billing revisions.
- Budget caps and ledger state at each admission decision.

## 3. Delivery history

One timestamped FOCUS delivery has been inspected. Multiple deliveries containing the same billing period have not yet been compared.

A single delivery does not establish whether billing records were delayed or revised.

## 4. Separate workload dataset

Alibaba Cluster Trace v2017 provides real workload observations, but these are not linked to the AWS billing records. It may support a separately labelled workload analysis, not empirical AWS dollar-cost validation.

## 5. Permitted work

- Describe the acquired AWS billing data.
- Preserve raw files, manifests, retrieval timestamps, and checksums.
- Design and implement a collector for real request, admission, runtime, and billing events.
- Compare future billing snapshots when available.
- Test software correctness without presenting generated data as real-world empirical evidence.

## 6. Prohibited claims at this stage

Do not claim that CALIBUR has been empirically validated for budget safety or delayed/revised billing feedback.

Do not invent costs, resource links, arrival times, or revisions. Do not treat unrelated workload records as matching AWS bills.

## 7. Conditions for upgrading to GREEN

Upgrade only after the required request, admission, runtime, billed-outcome, arrival/revision, and budget-context evidence is available with documented keys and timestamps.

**Current conclusion:** AMBER — genuine cost data acquired; linked real-event evidence remains incomplete.

## 8. FOCUS 1.2 versus CUR 2.0 reconciliation mismatch (verified 2026-10-25)

The deployed AWS billing export (`CALIBUR_FOCUS_12`, ARN recorded in audit) delivers data in **FOCUS 1.2 format** (FinOps Open Cost and Usage Specification). The Spendctl billing reconciler in `services/spendctl/billing.py` expects **CUR 2.0 field names** (`identity_line_item_id`, `bill_billing_period_start_date`, `line_item_resource_id`, `resource_tags_user_reservation_id`, etc.).

These are different schemas. The FOCUS 1.2 fields are named `BilledCost`, `ResourceId`, `Tags`, `ChargePeriodStart`, etc. The reconciler cannot consume the FOCUS Parquet files without a schema adapter.

**Consequence:** Even if DynamoDB tables, Lambdas, and a real admission existed today, the `BillingReconciler` path could not automatically reconcile costs from the deployed export. The `research/ingestion/focus_loader.py` and `focus_billing_adapter.py` correctly read the FOCUS deliveries for descriptive analysis, but the production billing path is not connected to them.

**Resolution path (requires separate decision):** Either:
1. Configure a separate CUR 2.0 export in the same account and point `BillingStack` at it (the CDK stack already supports this via `CurExportPrefix`), **or**
2. Write a FOCUS-to-CUR adapter that translates FOCUS 1.2 field names to the CUR 2.0 names that `billing.py` expects. This would require verified field mapping and must be tested against the actual FOCUS schema.

Until one of these is done, the end-to-end billing reconciliation path (reservation → billed cost via `BillingReconciler`) remains unconnected to the deployed export. The `research/ingestion/` modules can still load the FOCUS data for the descriptive/feasibility analysis recorded in this document.

**No changes were made to AWS billing configuration.** `EnableBillingReconciliation` remains `false`.