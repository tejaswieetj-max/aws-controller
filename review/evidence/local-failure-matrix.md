# Local failure-matrix results

Recorded from the evidence-pack run on 2026-10-22. Command:

```sh
.venv/bin/python -m pytest -q tests/unit/test_ledger.py tests/unit/test_pricing_admission.py tests/unit/test_api.py tests/unit/test_core.py tests/unit/test_provisioner.py tests/unit/test_lifecycle.py tests/unit/test_billing.py tests/unit/test_invariants.py
```

Result: **126 passed in 9.82s**. These are Moto/fake/local tests; they are not AWS verification.

| Scenario | Test evidence | Local result | What this does not prove |
|---|---|---|---|
| Duplicate/replayed request | `test_create_request_idempotency_and_conflict`; `test_budget_create_and_reserve_replay_are_idempotent` | Passed | Cognito, API Gateway, or live DynamoDB replay. |
| Conflicting payload | `test_create_request_idempotency_and_conflict`; `test_admission_rejects_cross_team_and_conflicting_key_reuse` | Passed | Live JWT claim behavior. |
| Concurrent reservation | `test_concurrent_reservations_never_overdraw_budget`; `test_concurrent_admissions_preserve_budget_invariant` | Passed | Real DynamoDB contention/transaction cancellation rates. |
| Ambiguous workflow start | `test_ambiguous_workflow_start_stays_unknown_and_keeps_reservation`; `test_timeout_with_no_visible_execution_remains_unknown_and_held` | Passed | Step Functions response ambiguity or eventual visibility in AWS. |
| EC2 launch constraints/failure | `test_launch_uses_client_token_private_network_and_required_tags`; `test_failure_injection_is_explicit_and_occurs_before_launch` | Passed | An actual EC2 request, IAM evaluation, or subnet behavior. |
| Unknown/failed cleanup | `test_cleanup_failure_keeps_hold_reserved_and_retries`; `test_partial_or_unknown_cleanup_never_refunds` | Passed | Event delivery or actual EC2 termination. |
| Confirmed termination/settlement | `test_expiry_reaper_terminates_owned_instance_then_provisionally_settles_once`; `test_confirmed_cleanup_settlement_restores_unused_budget_and_is_idempotent` | Passed | Real resource cleanup or invoice accuracy. |
| Duplicate lifecycle event | `test_cancel_reaper_marks_cancelled_and_retries_cleanup_without_double_settlement`; `test_expiry_reaper_terminates_owned_instance_then_provisionally_settles_once` | Passed | AWS event ordering/delivery guarantees. |
| CUR duplicate/revision | `test_reconcile_attribution_revision_and_duplicate_are_atomic`; `test_concurrent_same_snapshot_is_applied_once` | Passed | Real CUR schema, export completeness, tag activation, or billing attribution. |
| Invariant mismatch | `test_checker_recomputes_reservations_and_emits_deduplicated_alert` | Passed | CloudWatch/EventBridge delivery in AWS. |

The separate deterministic replay artifact is [`idempotency-demo.json`](idempotency-demo.json). It measures one reservation and one budget hold; it deliberately reports no workflow identity/count because this local `AdmissionService` scenario does not start Step Functions.
