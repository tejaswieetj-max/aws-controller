# CALIBUR End-to-End Connection Audit
**Date:** 2026-10-25  
**Branch:** `research/calibur-real-data`  
**AWS Account:** ••••215623 (root session — single IAM user, no MFA, AdministratorAccess)

---

## 1. Existing implementation state

### Code present and locally tested

| Component | File(s) | Local tests | Notes |
|---|---|---|---|
| Request validation | `services/spendctl/validation.py` | `test_api.py` | |
| Pricing (PriceList + cache + fallback) | `services/spendctl/pricing.py` | `test_pricing_admission.py` | Live prices not verified |
| Admission service + CALIBUR policy hook | `services/spendctl/admission.py` | `test_pricing_admission.py` | |
| Atomic DynamoDB ledger | `services/spendctl/ledger.py` | `test_ledger.py` | Moto only |
| CALIBUR engine (SettlementHandler, state stores) | `services/spendctl/calibur.py` | `tests/calibur_engine/` | 32 tests |
| Margin policies + calibration | `services/spendctl/calibration.py` | `test_pricing_admission.py`, `test_calibur_engine.py` | |
| Orchestration workflow | `services/spendctl/workflow_start.py`, `workflow_handlers.py` | `test_core.py` | Step Functions ASL only |
| Provisioner | `services/spendctl/provisioner.py` | `test_provisioner.py` | DryRun not run |
| Lifecycle reaper/sweeper | `services/spendctl/lifecycle.py`, `lifecycle_handlers.py` | `test_lifecycle.py` | |
| Billing reconciler (CUR 2.0) | `services/spendctl/billing.py` | `test_billing.py` | Disabled by default |
| API Lambda handler | `services/spendctl/api.py` | `test_api.py` | |
| Ingestion adapters (Alibaba, FOCUS) | `research/ingestion/` | `tests/research/` | 271 tests |
| Empirical runner | `research/empirical_run.py` | `tests/research/test_empirical_run.py` | |
| Ledger export reader | `research/ingestion/ledger_export_reader.py` | `tests/research/` | |
| Manifest reader | `research/ingestion/manifest_reader.py` | `tests/research/test_manifest_reader.py` | |
| S3 evidence collector | `research/collectors/s3_evidence_collector.py` | none (CLI script) | |

### CDK synthesized (not deployed)

8 stacks synthesized locally: IdentityStack, DataStack, ApiStack, OrchestrationStack, LifecycleStack, BillingStack, WebStack, ObservabilityStack. **CDK bootstrap exists** (CDKToolkit stack, bootstrap v32). No application stacks are deployed.

### Actually deployed in AWS

| Resource | Status |
|---|---|
| CDKToolkit bootstrap stack | Deployed (CREATE_COMPLETE) |
| FOCUS 1.2 Data Export (`CALIBUR_FOCUS_12`) | Deployed, HEALTHY, last refreshed 2026-10-25T11:25Z |
| S3 bucket (account-specific name withheld) | Deployed (3 deliveries, manifests) |
| AWS Budget `spendctl-monthly-budget` at $35 | Deployed |
| **DynamoDB tables** | **None** |
| **Lambda functions** | **None** |
| **API Gateway** | **None** |
| **Cognito User Pool** | **None** |
| **Step Functions** | **None** |
| **EventBridge custom bus** | **None** |
| **EventBridge Scheduler schedules** | **None** |
| **CloudTrail trail** | **None** |
| **EC2 instances** | **None** |
| **Spendctl IAM roles** | **None** |

---

## 2. Connection chain status

```
Request → Admission → [DynamoDB ledger] → [Workflow] → [EC2] → [Lifecycle] → [Billing reconciler] → [CALIBUR settlement] → empirical_run.py
```

| Link | Code | Deployed | Blocker |
|---|---|---|---|
| Request → Admission (validation, pricing, CALIBUR policy) | ✅ Implemented | ❌ | No API Gateway, Lambda, Cognito |
| Admission → DynamoDB reservation | ✅ Implemented | ❌ | No DynamoDB tables |
| DynamoDB → Workflow start | ✅ Implemented | ❌ | No Step Functions, no Lambda |
| Workflow → EC2 provisioner | ✅ Implemented | ❌ | No ProvisionerRole, no subnet/SG |
| EC2 → lifecycle/cleanup | ✅ Implemented | ❌ | No EventBridge Scheduler, no Lambda |
| Lifecycle → ledger settlement | ✅ Implemented | ❌ | No DynamoDB tables |
| Billing (CUR) → ledger reconciliation | ✅ Implemented | ❌ | CUR exists as FOCUS format, not CUR 2.0; reconciler expects CUR 2.0 field names |
| FOCUS billing → empirical_run.py | ✅ Implemented | ✅ (data only) | AMBER: no admission records to join |
| Ledger export → empirical_run.py | ✅ Implemented | ❌ | No DynamoDB tables to export from |
| CALIBUR settlement → SettlementHandler | ✅ Implemented | ❌ | Needs ReservationSettled events from billing reconciler |

---

## 3. Missing links

### Critical blockers (require deployment approval)

1. **DynamoDB tables** — 3 tables needed: Budgets, Reservations, Alerts. CDK DataStack synthesizes them. Until deployed, no admission can be persisted.
2. **Lambda functions + API Gateway** — needed to accept the first real admission. CDK ApiStack + OrchestrationStack.
3. **IAM roles** — ProvisionerRole + Lambda execution roles needed. `sts:TagSession` policy already fixed in synthesis.
4. **Private subnet + security group** — CDK parameter inputs; must be verified as no-route-to-internet subnet.

### Safe local code gaps (can be implemented now)

5. **No `scripts/export_ledger.py`** — there is no operator script to export DynamoDB reservation + billing_audit records to a JSON file that `ledger_export_reader.translate()` and `empirical_run.py` can consume. The `seed.py` creates budgets but nothing exports the research data format.

6. **`billing.py` expects CUR 2.0** — field names (`identity_line_item_id`, `bill_billing_period_start_date`, etc.) are CUR 2.0. The deployed export is FOCUS 1.2. The `BillingReconciler` cannot consume FOCUS Parquet directly. There is no FOCUS→research-pipeline adapter that feeds `BillingReconciler`. This gap means the FOCUS billing data cannot yet feed into the per-reservation settlement path.

7. **No `ReservationSettled` event producer connected to CALIBUR state store** — `calibur.py` has `SettlementHandler` and `DynamoCaliburStateStore`, but there is no Lambda handler that reads `ReservationSettled` EventBridge events and calls `process_settlement_event()` with the actual DynamoDB state store. The Lambda is referenced in CDK ASL but not wired.

8. **`empirical_run.py` has no `--dry-run` or status-check mode** — the runner stops correctly at AMBER but does not separately report what's missing vs what stage got furthest. A `--status` flag would help diagnose deployment readiness.

---

## 4. Safety risks

- **Root credentials** — the current session is root (no MFA). Any deployment with root is a security risk. Should use a dedicated IAM role with least-privilege.
- **Default VPC subnets have public IPv4 auto-assign** — EC2 instances launched in default subnets would get a public IP, violating the CALIBUR constraint. A private subnet with no internet route must be confirmed before deployment.
- **AWS Budget notifications ≠ spending cap** — the $35 budget alert does not stop spending. EC2 costs are the primary risk. A 1-hour `t3.micro` with 8 GB gp3 costs ~$0.012 + ~$0.0009 ≈ $0.013. 100 such requests would cost ~$1.30. Risk is low if the admission flow is tested with minimal requests.
- **No CloudTrail trail** — bypass detection (M7) requires a CloudTrail trail. None exists. The bypass detector Lambda rule would not fire.
- **`billing.py` reconciler supports FOCUS 1.2 and is enabled by default** — `normalize_focus_record` maps FOCUS 1.2 columns (`BilledCost`, `ResourceId`, `ChargePeriodStart`, `Tags`, etc.) to the internal reconciliation model; `BillingSchema` defaults to `focus` and `EnableBillingReconciliation` defaults to `true` (see `infra/stacks/foundation.py`, asserted in `tests/unit/test_m9_m11_infra.py`). Late-arriving revisions replace prior billed amounts via `billing_source_revision_order` ordering and `reconciliation_id` idempotency, not duplicate charges (`tests/unit/test_billing.py`). The reconciler is **not** AWS-verified end-to-end: the Data Export, its Glue table, and `reservation-id`/`team` cost-allocation tags are still not created or activated in any account, and the FOCUS 1.2 vs CUR 2.0 schema match remains a deploy-time verification step.

---

## 5. Smallest viable next steps (local — safe now)

1. Implement `scripts/export_ledger.py` — operator script to export DynamoDB reservations + billing_audit rows to the JSON format `ledger_export_reader.translate()` expects.
2. Add `--status` flag to `empirical_run.py` to report the pipeline stage and what's missing.
3. Document the FOCUS→CUR 2.0 mismatch explicitly in `research/data_feasibility.md`.

## 6. Next steps requiring deployment approval (STOP)

Before any of these, present the exact proposal and get explicit approval:

A. **Deploy DataStack** — creates 3 DynamoDB tables + price cache table. Cost: ~$0/month (on-demand pricing for tiny dev usage). Requires: non-root IAM identity, reviewed private subnet, reviewed CDK templates.
B. **Deploy ApiStack + OrchestrationStack** — Lambda + API Gateway + Step Functions. Cost: ~$0 for infrequent dev calls (Lambda free tier). Requires: A first, Cognito domain prefix parameter.
C. **First admission test** — 1 request, t3.micro, 1 hour, 8 GB, alpha team. Cost: ~$0.013 + small overhead. Requires: B first, private subnet confirmed.

**Current empirical result: DATA_INFEASIBILITY** — no CALIBUR admissions exist. The pipeline correctly stops at AMBER.
