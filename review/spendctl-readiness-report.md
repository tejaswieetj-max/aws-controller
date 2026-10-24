# SPENDCTL readiness audit — 2026-10-22

## Status

**Not ready for deployment or live billing reconciliation.** Local evidence supports the application code, built frontend, and eight-stack CDK synthesis. The AWS project records also contain deployment-blocking IAM, cost, networking, and billing findings from read-only checks on October 22. A subsequent caller-identity check could not refresh the expired CLI session, so those dated AWS findings have not been revalidated since then.

This audit distinguishes **recorded evidence** (the October 22 verification log and assumptions record) from checks rerun for this audit. No AWS operation, deployment, bootstrap, account change, credential operation, or destructive command was performed. No local code defect justified a business-logic change.

## Prioritized blockers and remaining work

| Priority | Finding and evidence | Required next action |
|---|---|---|
| **Critical** | **Administrator-level access is unsuitable for deployment.** `docs/aws-assumptions.md` records that the inspected IAM user had `AdministratorAccess`, no MFA, and credentials sourced from the shared credentials file; the existing CDK bootstrap execution role also had `AdministratorAccess` and no permissions boundary. These are dated October 22 findings, not a fresh live check. The later `aws sts get-caller-identity` attempt failed because the saved login session had expired (`docs/verify-log.md`). | Account owner must arrange a named, short-lived, MFA-protected identity and review least-privilege deployment permissions and bootstrap-role scope. Do not use the recorded administrator identity or broaden access to work around denials. |
| **High** | **Recorded monthly forecast exceeds the stated budget.** `docs/aws-assumptions.md` reports a $10 monthly budget, $0 actual, and $11.559 forecast; actual notifications existed at 50/80/100%, with no forecast notification found. This is not an enforced spending cap. Credit balance was not verified. | Owner reviews the forecast, expected stack costs, credits, and budget/forecast notifications before approving any AWS change. |
| **High** | **No safe provisioning network inputs are established.** The October 22 record says the default VPC has an active internet-gateway route, inspected default subnets auto-assign public IPv4, and inspected security groups include `0.0.0.0/0` ingress; none was selected. CDK requires `PrivateSubnetId` and `ProvisioningSecurityGroupId` but does not create or validate them (`infra/stacks/foundation.py`, `OrchestrationStack`). | Owner must identify a subnet with neither internet nor NAT route and a security group with no ingress, then independently verify those exact resources before deployment. Existing default-VPC resources do not satisfy the stated private-networking requirement. |
| **High** | **CloudFront custom-domain/TLS prerequisites are absent.** The records say there were no Route 53 hosted zones or ACM certificates in `us-east-1`. The synthesized WebStack uses the default `cloudfront.net` hostname; AWS applies TLSv1 to that hostname, so the source's TLS 1.2 minimum property does not deliver the required effective minimum (`docs/aws-assumptions.md`, `docs/frontend.md`, WebStack template). | Decide whether a custom domain and `us-east-1` certificate are required. If required, arrange an owner-approved DNS name and certificate and update/review the distribution alias and viewer certificate before deployment. |
| **High** | **Billing integration is incomplete and unverified.** October 22 records report no CUR/Data Exports and inactive `reservation-id`/`team` cost-allocation tags. BillingStack creates buckets, Glue database, Athena workgroup and notification plumbing, but does not create the export or its Glue table. `CurTableName` is required; `EnableBillingReconciliation` defaults to `false`. Expected fields, manifest/revision semantics, delivery, tag attribution, and actual invoice matching remain assumptions (`docs/m8-manual-steps.md`). | Keep reconciliation disabled. Owner must configure/approve export and tags, inspect real manifest and Glue schema, verify attribution and revision ordering, run bounded workgroup queries, and compare a known charge before considering opt-in. |
| **High** | **Deployment parameters and account checks remain outstanding.** Cognito `HostedUiDomainPrefix`, API `CloudFrontOrigin`, private subnet/security group, billing table name, quotas/availability, credit balance, and notification delivery require owner review. The October 22 identity refresh failed; no app stacks were deployed per `docs/aws-assumptions.md` and `docs/verify-log.md`. | Resolve parameters and validate account, region, limits, costs, identity, and outputs as separate owner-approved steps. Do not infer account state from local synthesis. |
| **Medium** | **Effective least privilege is not established.** Local templates scope many roles, but some actions necessarily or currently use wildcard resources (for example provisioner `RunInstances` under conditions, EC2 describe calls, pricing lookup, and Athena result/query reads). Synthesized policies do not prove effective authorization. The recorded administrator bootstrap execution role is a separate high-priority concern above. | Security reviewer evaluates all synthesized IAM and runtime condition-key behavior under the approved role; live positive/negative checks remain separate. |
| **Medium** | **Live integration and operational behavior remain unverified.** Cognito/JWT/API, DynamoDB transaction contention and GSIs/TTL, Step Functions, Scheduler/EventBridge/CloudTrail, EC2 launch/cleanup, IAM conditions, CloudWatch alarm delivery, SNS subscriptions, and real pricing were not established by local tests. | Schedule bounded account-backed verification only after blockers and costs are approved. Keep EC2 launch and cleanup tests explicitly bounded and separately approved. |
| **Low** | **Synthesis warnings remain.** The October 22 log records Node.js 26 unsupported by installed JSII/CDK and deprecated `Stack.addDependency`. | Address in routine maintenance after checking supported toolchain versions and dependency semantics; do not upgrade blindly. |

## Milestones

Milestones M0–M12 are defined in `docs/module-acceptance.md`; completion below means local evidence only where stated.

- **M0 — Foundation/DevOps:** Local Python/lint/test/synth workflow is evidenced. Deployment readiness is blocked by AWS access and configuration prerequisites.
- **M1 — Identity/access:** Cognito and permissions-boundary templates and local handler tests exist. Live sign-in, identity, MFA/least-privilege account access, and assumed-role/EC2 DryRun checks remain outstanding.
- **M2 — Data/ledger:** Five tables and local Moto state/invariant/idempotency tests are evidenced. Real DynamoDB behavior remains unverified.
- **M3 — Pricing:** Fixture/provider/cache/fallback tests exist. Live price comparisons and current cost validation remain outstanding.
- **M4 — Admission:** Local auth-adapter, concurrency, budget, and invariant tests exist. Live JWT/DynamoDB authorization remains unverified.
- **M5 — API:** Nine JWT-protected routes and local handler/template evidence exist. Live Cognito/API Gateway/IAM verification remains outstanding.
- **M6 — Orchestration:** Workflow, guarded launch parameters, ownership constraints, cleanup and settlement have local tests and synthesized resources. No live execution, IAM-condition, AMI, subnet, or security-group verification exists.
- **M7 — Lifecycle:** Local lifecycle/reaper tests and synthesized scheduler/event resources exist. Delivery, CloudTrail, role assumption, termination, and EBS deletion remain unverified live.
- **M8 — Billing:** Local parser/reconciliation logic and disabled-by-default infrastructure exist. Export, tags, Glue schema, manifest semantics, Athena, attribution, and bill comparison are incomplete; reconciliation must stay disabled.
- **M9 — Calibration:** Local evaluator and synthetic artifacts exist; there are no verified live billing observations.
- **M10 — Web:** Frontend tests/build passed in the October 22 log. Synthesized WebStack reports `FrontendAssetsSynthesized=true` and includes the asset deployment. Browser login, accessibility, callbacks/CORS, and live hosting remain unverified.
- **M11 — Observability:** Dashboard and alarms synthesize; live metrics/alarm behavior and SNS recipient delivery remain unverified. The SNS topic has no synthesized subscription.
- **M12 — Test/demo/teardown:** Offline demo, local safety tests, and read-only teardown planner are evidenced. No deployment, live demo, or AWS-backed post-run inventory was done.

## Recorded checks and checks rerun for this audit

**Recorded, not rerun:** `docs/verify-log.md` records for 2026-10-22 that `make verify` passed (offline smoke), `make test` passed with 147 tests, Ruff passed, frontend tests passed (5), TypeScript/Vite production build passed, and CDK synthesis passed with eight stacks and `FrontendAssetsSynthesized=true`. The October 22 entry's Ruff command was `ruff check services infra tests scripts`; an earlier exact command in the log is `ruff check services infra tests scripts research`. This audit preserves the recorded result and does not describe `make verify` as unrun or rerun it.

The log separately records an expired-session failure for a later AWS identity refresh. Earlier read-only AWS observations remain dated evidence, not a fresh audit-time AWS query.

**Rerun during this audit:** Read-only inspection of repository documentation, source, and existing `cdk.out` artifacts. Python standard-library JSON parsing succeeded for each of these eight existing template files: `ApiStack`, `BillingStack`, `DataStack`, `IdentityStack`, `LifecycleStack`, `ObservabilityStack`, `OrchestrationStack`, and `WebStack`. Their resource counts were respectively 67, 15, 5, 5, 36, 13, 26, and 9. Billing template confirmed `EnableBillingReconciliation` defaults to `false` and `CurTableName` is required. Web template confirmed `FrontendAssetsSynthesized=true`. Orchestration template includes required private subnet and security-group parameters. No `make verify`, lint, tests, synthesis, AWS command, or diff command was rerun.

## Security and application-flow evidence

- Source and tests implement request validation, estimate/reserve before workflow start, idempotency handling, durable workflow-start recovery, guarded provisioning, tag-scoped cleanup, and conservative handling of unknown outcomes. Unknown launch/cleanup outcomes remain held rather than refunded. Ledger code enforces `available_micro + reserved_micro + spent_micro = budget_micro`; October 22 local test count is recorded, not rerun here.
- Synthesized provisioning policy conditions restrict region, instance type, subnet/security group, volume type/size, ownership tags, and public-IP association. Source checks for IMDSv2 and encrypted delete-on-termination EBS. Actual IAM evaluation and live networking remain unverified; account-recorded default VPC and ingress findings are blockers.
- API uses a Cognito JWT authorizer and rate-limited default stage; frontend uses PKCE without a client secret and treats backend authorization as authoritative. Live auth, CORS, and browser behavior are not proven by source or build.
- Web hosting uses private S3 with CloudFront OAC. The default CloudFront hostname's TLS policy limitation remains as documented above.
- Billing's parser and transactional revision logic have local fixtures/Moto evidence only. Do not treat local tests as proof that the real CUR schema or invoice attribution matches.

## Next actions, in order

1. Account owner replaces the recorded administrator-level access path with approved MFA-protected least-privilege access and reviews the bootstrap execution role.
2. Owner resolves the $11.559 forecast against the $10 budget, reviews expected costs and credits, and confirms notification recipients.
3. Owner supplies and verifies private subnet/no-NAT/no-internet-route and no-ingress security group; never select the recorded default-VPC resources.
4. Decide on the CloudFront hostname policy. If TLS 1.2+ is required, arrange custom DNS and a `us-east-1` ACM certificate before deployment.
5. Resolve deployment parameters, service limits, and frontend runtime configuration; review synthesized IAM and resources.
6. Keep CUR reconciliation disabled until the real export/table/tag/schema/revision/attribution and bill checks pass.
7. Only after these are addressed, plan separately approved, bounded live integration and deployment checks; record exact results in `docs/verify-log.md`.

No AWS-changing command is included here. Any future `cdk bootstrap`, `cdk deploy`, EC2 launch, billing export/tag setup, budget change, or destructive operation requires its own explicit review and approval.
