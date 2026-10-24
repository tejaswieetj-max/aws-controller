# SPENDCTL

SPENDCTL is an AWS resource-admission controller: it estimates a requested EC2 workload, atomically reserves a team's application budget, provisions through a constrained role, tracks uncertain lifecycle outcomes conservatively, and can reconcile completed usage against CUR data.

The implementation has local tests and synthesized infrastructure. A real Cognito sign-in, API request, EC2 launch/cleanup, or CUR reconciliation has **not** been verified in AWS. The application budget ledger is not an AWS billing cap.

## Status

- **Implemented:** admission, idempotency, budget transactions/invariants, constrained EC2 provisioning, lifecycle cleanup, frontend, and optional CUR reconciliation. See [architecture](docs/architecture.md).
- **Locally verified:** targeted Moto/fake tests, frontend checks and CDK synthesis are documented in the [evidence index](docs/evidence-index.md) and [verification log](docs/verify-log.md). A repeatable local admission replay writes [redacted evidence](review/evidence/idempotency-demo.json).
- **AWS verified:** no end-to-end AWS execution is recorded. See the [live demo runbook](review/live-aws-demo-runbook.md) for current prerequisites and gates.

## Architecture and evidence

- [Architecture and budget invariant](docs/architecture.md)
- [Demo scenarios](docs/demo-scenarios.md)
- [Failure modes](docs/failure-modes.md)
- [Evidence index](docs/evidence-index.md)
- [Module acceptance checklist](docs/module-acceptance.md)
- API and event contracts: [API](docs/api.md), [events](docs/events.md)

## Local commands

Python 3.12 is the target runtime. Install development dependencies with `make install`. Useful local checks:

```sh
PATH="$PWD/.venv/bin:$PATH" ruff check services infra tests scripts research
.venv/bin/python -m pytest -q
make verify
make synth CDK_OUTDIR=/private/tmp/spendctl-local-synth
PYTHONPATH=services .venv/bin/python scripts/evidence_idempotency_demo.py
```

`make demo` is a simpler offline request/fixture-pricing walkthrough and does not reserve a budget or provision resources. The evidence demo uses Moto and a fixture provider; it is not an AWS integration test.

## Presentation outline

1. Explain the architecture and the `budget_micro = available_micro + reserved_micro + spent_micro` invariant.
2. Show estimate and provenance, then demonstrate same-key replay and conflicting-key rejection.
3. Show concurrent reservations, ambiguous workflow start, and cleanup failure keeping funds held.
4. Show a synthetic CUR revision correcting provisional settlement.
5. For a live presentation, follow the gated [live AWS runbook](review/live-aws-demo-runbook.md) and label any behavior not yet exercised in AWS.

## Teardown and limitations

`make teardown AWS_EXPECTED_ACCOUNT_ID=<12-digit-account>` is a read-only, tag-scoped EC2/EBS inventory and dry-run planner; it does not delete resources. For deployment, EC2 launch, billing setup, or destructive operations, follow [AWS setup](docs/aws-setup.md) and obtain the required explicit approval.

Live identity, networking, current costs, deployment permissions, Cognito claims, service delivery, and CUR schema/attribution remain account-specific prerequisites. Reconciliation is disabled by default. See [readiness report](review/spendctl-readiness-report.md) for dated findings and their verification status.
