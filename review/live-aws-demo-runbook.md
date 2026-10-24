# SPENDCTL live AWS browser demo — gated runbook

## Current status

**Not runnable against AWS yet.** The existing frontend, API, ledger, workflow, provisioner, and lifecycle code implement the intended path, but the project records say no Spendctl application stacks are deployed. No Cognito login, live API call, DynamoDB reservation, EC2 launch, status poll, or cleanup has been verified against AWS.

This is a plan for the owner/operator after the listed preconditions are satisfied. It does not authorize deployment, bootstrap, budget-row creation, or EC2 launch. None of those actions was performed while preparing this runbook.

## What the code path implements

1. The React app signs in through Cognito authorization-code + PKCE and sends the Cognito ID token to API Gateway's JWT-authorized routes.
2. `POST /v1/estimate` validates the request and returns a compute/gp3 estimate with provider provenance.
3. `POST /v1/requests` checks the verified team, calculates the reserve, and atomically writes a budget hold plus idempotency/reservation records to DynamoDB before starting Step Functions. It reuses the same idempotency key after ambiguous outcomes.
4. The Standard workflow calls the provisioner, which assumes the constrained provisioner role and starts the EC2 instance with required ownership tags, no public IPv4 or key pair, IMDSv2, and encrypted delete-on-termination gp3 storage.
5. The app can list requests and fetch `GET /v1/requests/{id}` for state tracking. Cancellation dispatches lifecycle cleanup; unknown launch/cleanup outcomes remain held instead of being refunded.
6. These behaviors have local unit/Moto/fake and synthesis coverage only. They have not been proven in the AWS account. After cancellation, M7 conservatively makes a provisional settlement from the held amount; without verified M8 CUR reconciliation, do not promise that the application budget hold is refunded to available funds.

Relevant code: `frontend/src/auth.ts`, `frontend/src/App.tsx`, `frontend/src/api.mjs`, `services/spendctl/api.py`, `services/spendctl/admission.py`, `services/spendctl/ledger.py`, `infra/stacks/foundation.py`, and `services/spendctl/lifecycle.py`.

## Current blockers from project records

The findings below were recorded by read-only AWS checks on **2026-10-22**. They are dated evidence, not fresh checks. The later identity refresh recorded in `docs/verify-log.md` failed because the login session had expired.

- **Identity and bootstrap role:** the inspected IAM user had `AdministratorAccess`, no MFA, and shared-file credentials; the existing CDK bootstrap execution role also had `AdministratorAccess` and no permissions boundary. No IAM Identity Center instance was found. Do not deploy with that access. An owner must arrange an approved individual, MFA-protected, least-privilege path and review the bootstrap role.
- **Cost approval:** the recorded $10 monthly AWS budget showed $11.559 forecast and $0 actual; available credits were not verified. Resolve the forecast and obtain an explicit maximum cost approval for the demo before deploying or launching anything. AWS Budgets notifications are not a hard spending cap.
- **Network inputs:** the inspected default VPC routes to an internet gateway, its inspected subnets auto-assign public IPv4, and inspected security groups include `0.0.0.0/0` ingress. None is acceptable for the provisioner. The account owner must identify a private subnet with no internet or NAT route and a security group with no ingress, or separately approve and review creation of appropriate network resources.
- **No deployment:** project records state no Spendctl application stacks are deployed. The configured AWS login later expired; no account state was refreshed after that failure.
- **Budget ledger:** a newly deployed DataStack has no team budget rows automatically seeded. A budget row with enough available balance must exist before a request can be admitted. Do not create/change AWS Budgets as part of this runbook; obtain explicit approval before writing an application ledger budget.
- **Live integration unknowns:** IAM conditions, Cognito claims, API CORS, DynamoDB transactions, Step Functions, EC2 launch, Scheduler/EventBridge cleanup, and termination outcomes all require real AWS verification.
- **Provisioner session tags:** fixed in local source and fresh synthesis. The provisioner Lambda role now has a separate `sts:TagSession` allow on only the synthesized `ProvisionerRole` ARN, with `reservation-id` matching `rsv_*`, non-null `team`, and only `reservation-id`/`team` tag keys. The target trust statement has the matching conditions. This satisfies the code-defined caller/trust policy requirements; it has not been tested against live IAM. No AWS IAM policy was changed.

The CloudFront custom-domain and ACM certificate finding is **not a blocker for a localhost-served frontend**. The API already includes localhost in its CORS allowlist, and the Cognito client defaults to `http://localhost:5173/callback` and `http://localhost:5173/` callbacks. For this path, set `ApiStack`'s `CloudFrontOrigin` parameter to `http://localhost:5173` and do not deploy `WebStack`. A custom domain and `us-east-1` certificate are still required if you choose to serve the demo through the project's CloudFront distribution while requiring TLS 1.2+.

## Owner preflight — stop if any item is unresolved

1. Confirm the intended AWS account and `ap-south-1` region with the account owner. Arrange an approved non-root, MFA-protected, short-lived identity and least-privilege deployment access. Do not share credentials or tokens.
2. Have the owner/reviewer inspect the existing bootstrap execution role and approve the exact deployment role/policy. Do not accept AdministratorAccess as a shortcut.
3. Review the current forecast, available credits, expected services and maximum demo spend with the owner. Resolve the documented forecast overrun; do not assume the $10 budget blocks charges.
4. Get the exact owner-verified `PrivateSubnetId` and `ProvisioningSecurityGroupId`; verify no internet/NAT route, no public IPv4 assignment, and no ingress on the selected security group. Never use the recorded default-VPC resources.
5. Review the current synthesized templates and requested CDK parameters locally. Confirm a unique Cognito hosted UI domain prefix.
6. Obtain separate approval for the application-level team budget row and for one bounded EC2 demo request, its maximum runtime, and its cost ceiling. Keep CUR reconciliation disabled.
7. Ensure a demo Cognito user exists with the correct `custom:team` claim (`alpha` or `beta`) and `developers` group. The app client has self-sign-up disabled. An administrator account is needed only for admin-only views/actions.

If any item is not satisfied, stop at local synthesis. The assistant has not inspected current AWS state in this run.

## Deployment scope and permission review

For a localhost browser demo, the selected stack set is `IdentityStack`, `DataStack`, `LifecycleStack`, `OrchestrationStack`, and `ApiStack`, in `ap-south-1`. The checked-in `cdk.out` templates contain 5, 5, 36, 26, and 67 CloudFormation resources respectively (139 total). This scope supplies Cognito, five DynamoDB tables, the API and nine routes/Lambdas, the Standard Step Functions workflow and provisioner, and lifecycle/EventBridge/Scheduler recovery. It intentionally excludes `WebStack` (CloudFront/S3), `BillingStack` (CUR/Athena/Glue), `ObservabilityStack` (dashboard/alarms/SNS), and the opt-in `NaiveAdmissionExperimentStack`. Counts are local synthesized-template evidence, not deployed state.

The authorization review must distinguish these three identities; they are not interchangeable:

| Identity | Minimum scope to review |
|---|---|
| **Human/CDK deployment identity** | Assume only the approved CDK bootstrap lookup, file-publishing, and deployment roles in the intended account/region; read/execute CloudFormation change sets for the five named stacks; and pass only the reviewed CloudFormation execution role. Permit access to the CDK bootstrap asset S3 bucket only as needed for Lambda zip assets. Do not grant direct administrator access or unrestricted `iam:*`, `cloudformation:*`, or `s3:*`. Exact CDK role names and policy actions depend on the installed CDK CLI/bootstrap version and must be generated/reviewed for this account; the project does not define a deployer policy. |
| **CloudFormation execution role** | Create/update/delete only resources represented by the reviewed five templates: Cognito UserPool/client/domain/groups; DynamoDB tables/indexes; API Gateway v2 APIs/authorizer/routes/integrations/stage; Lambda functions/permissions/log groups; IAM roles/policies/managed policy; Step Functions state machine; EventBridge bus/rules; Scheduler group/schedules; SQS DLQ; and necessary SSM public AMI parameter resolution. Scope IAM role/policy changes to the named project roles and `iam:PassRole` to those roles with `iam:PassedToService` conditions for the intended services. CloudFormation stack operations and rollback need the corresponding resource read/create/update/delete actions. This is an action-family inventory, not a ready-made policy: CloudFormation often needs resource-specific and some `Resource: "*"` permissions, and the exact deploy policy must be derived from the templates and approved by the account security owner. |
| **Application service roles synthesized by the stacks** | Keep these separate and limited to runtime tasks below. The provisioner path uses a dedicated EC2 role and permissions boundary; API/Lambda and lifecycle roles are not the human deployment identity and must not be reused as one. The templates are the authority for their current policies; live IAM evaluation remains unverified. |

Runtime-role scope visible in `infra/stacks/foundation.py`:

- **API Lambda roles:** logs to their own one-month log groups; handler-specific DynamoDB reads/writes/queries on the needed tables/indexes; `pricing:GetProducts` for estimate and create-request functions; `states:StartExecution`/`ListExecutions` on the named workflow and `DescribeExecution` on its execution ARN prefix for create-request recovery; `lambda:InvokeFunction` on the named lifecycle reaper for cancellation.
- **Step Functions role:** invoke only its five named callback/provisioner Lambdas. It has no EC2 permissions.
- **M6 provisioner Lambda role:** write its own logs, `sts:AssumeRole` only to `spendctl-dev-provisioner-role`, and `sts:TagSession` only to that same role ARN with `reservation-id` matching `rsv_*`, non-null `team`, and allowed tag keys exactly `reservation-id` and `team`.
- **`ProvisionerRole`:** trust is limited to the provisioner/reaper roles and requires reservation/team session tags. Its permissions boundary permits EC2 actions only in `ap-south-1`; its role policy allows conditional `ec2:RunInstances` with the selected subnet/SG, `t3.micro` or `t3.small`, gp3 1–30 GB, no public IP, and required project/env/managed-by/reservation/team tags; `CreateTags` for launch-created instances/volumes; describe calls; and tag/session-scoped `TerminateInstances`/`DeleteVolume`. `RunInstances` and describe APIs use wildcard resource scope in the synthesized policy, so the conditions and boundary need explicit security review and live positive/negative verification.
- **Lifecycle roles:** reaper can read/update/transactionally settle the reservation/budget tables, query the expiry index, write alerts, start/list/describe the named workflow, publish to the project event bus, and assume the provisioner role with reservation/team tags. Other lifecycle roles are separately scoped to their duties; selected permissions include EC2 `DescribeInstances`/`DescribeVolumes` with wildcard resource scope, EventBridge delivery, Scheduler invokes, and SQS DLQ delivery.

The October 22 account record says the existing bootstrap CloudFormation execution role had `AdministratorAccess` and no permissions boundary. That role is **not** an acceptable approved execution role as recorded. It must be reviewed/replaced or constrained by the owner before deployment. No current IAM state was checked in this local-only review.

## Code-defined runtime, cost envelope, and cleanup

- `services/spendctl/validation.py` accepts only `t3.micro` or `t3.small`, gp3 `1–30` GB, and planned duration `1–24` hours. Admission reserves the estimate times the fixed `1.10` policy (`services/spendctl/admission.py`). For a bounded demo choose `t3.micro`, 8 GB, and 1 hour; this is a request setting, not an AWS hard spending cap.
- `services/spendctl/provisioner.py` requests one instance, no public IPv4 or key pair, IMDSv2 required, one encrypted gp3 root volume, and `DeleteOnTermination=true` for interface and root volume. The root volume size follows the request.
- The workflow waits and polls every 10 seconds up to 36 attempts (about six minutes) for the instance to run (`infra/stacks/foundation.py`, `OrchestrationStack`). Most API/orchestration Lambdas time out at 10 seconds; lifecycle reaper/orphan-audit functions use 30 seconds. Admission duration may be up to 24 hours. Automatic expiry is scheduled at `expires_at`, with a five-minute sweeper as recovery; neither is a strict real-time maximum if AWS delivery/cleanup is delayed or unknown. Unknown cleanup must remain held and be investigated.
- After confirmed cancellation/expiry cleanup, M7 moves the **full reserved amount** to provisional spend (`services/spendctl/lifecycle.py`) until a verified billing correction exists. It is not a promise of a refund. CUR reconciliation remains disabled and is not required for the first demo.
- Stack-level continuing cost drivers include Cognito, API requests, five pay-per-request DynamoDB tables (Budgets and Reservations enable PITR), Lambda/log ingestion/storage, Step Functions Standard transitions, EventBridge, Scheduler, and the lifecycle SQS DLQ. Log groups retain data for one month; the invariant-check DLQ retains messages for 14 days. Deployment itself may incur charges. The selected stack set excludes CloudFront, CUR/Athena, and observability alarms/dashboard/SNS, but this does not make it free.
- `docs/aws-assumptions.md` records a Price List result dated October 22 of `$0.0112` per `t3.micro` hour, `$0.0224` per `t3.small` hour, and `$0.0912` per gp3 GB-month in `ap-south-1`. At those dated rates, 1 hour of `t3.micro` plus 8 GB gp3 is approximately `$0.0122` before any other service, tax, transfer, minimum-billing, or account-specific charges. These are not a current quote. Offline fixture rates differ (`FixturePricingProvider` in `services/spendctl/pricing.py`) and must never be presented as live prices. Require an owner-approved cost ceiling based on current account pricing and anticipated stack charges.
- The account record from October 22 reports a `$10` monthly AWS budget with `$11.559` forecast, `$0` actual, no forecast notification, and unverified credits. These values have not been refreshed. The account owner must decide whether any spend is acceptable under the current situation; do not assume the budget blocks spend.

## Demo ledger budget and Cognito setup

These are application data/configuration requirements, not AWS Budgets controls:

- `DataStack` creates `spendctl-dev-budgets`; it creates no budget rows. An `OPEN` row is needed for the user's team and current period. Admission checks `available_micro` and atomically transfers reserve to `reserved_micro`; the invariant is `available_micro + reserved_micro + spent_micro = budget_micro`.
- Existing `scripts/seed.py` supports a dry run and, with `--apply --confirm-account`, writes the same approved per-team amount for `alpha` and `beta` in the `spendctl-dev-budgets` table for the selected `--period`. It refuses a changed/existing conflicting budget rather than resetting it. The script does not fetch or move money: `budget_usd` is a virtual admission limit, not an AWS service charge cap and not an AWS Budgets setting. Check existing rows first; never increase an amount merely to get a demo request through. Only after separate approval, the exact write form is `python3 scripts/seed.py --period <YYYY-MM> --budget-usd <approved-amount> --region ap-south-1 --table-prefix spendctl-dev --apply --confirm-account <approved-12-digit-account>`; this is an AWS/DynamoDB mutation and is not to be run as part of local review.
- `IdentityStack` parameters/config: required `HostedUiDomainPrefix`; localhost callback `http://localhost:5173/callback`; logout `http://localhost:5173/`; public OAuth authorization-code client with no client secret; scopes `openid`, `email`, `profile`; self-sign-up disabled; groups `developers` and `admins`; custom attribute `team` readable by the client but not writable by it. The approved demo user must be created by the owner/admin with a valid email, `custom:team=alpha` (or `beta` matching an existing ledger row), and membership in `developers`. No admin group is needed for ordinary estimate/reserve/status/cancel flow.
- `ApiStack` requires `CloudFrontOrigin`; for a localhost demo its value must be exactly `http://localhost:5173`. The API is an HTTP API v2 with Cognito JWT authorizer; all nine routes require authorization. Frontend configuration uses the actual `ApiUrl`, `HostedUiDomain`, and `UserPoolClientId` outputs and the two localhost redirect URIs. Do not use example/placeholder values.

These source requirements do not establish that the current account has this user, prefix, open budget row, valid endpoints, or the required network/security configuration.

For networking, `OrchestrationStack` only takes `PrivateSubnetId` and `ProvisioningSecurityGroupId` parameters with descriptions requiring a private/no-NAT subnet and no-ingress security group. `infra/app.py` does not create those network resources, and synthesis does not inspect or enforce their route tables or ingress rules. The account owner must verify those AWS properties before deployment; the provisioner separately requests no public IPv4.

The code launches no workload payload, user data, or service that needs to connect to the instance. The app and workflow use AWS control-plane APIs from API/Lambda/Step Functions, so the instance itself has no demonstrated outbound-network requirement for this bounded flow. The no-internet/no-NAT route is the project's stated security posture (`docs/aws-setup.md`), rather than a dependency of the workflow. Keep the instance without a public IP and use a no-ingress security group; do not broaden egress for this demo without a separately identified need.

## Gated deployment outline — do not run until explicitly approved

These commands change AWS resources. Run them only when the account owner has approved the reviewed deployment and separately approved the bounded EC2 request. Do not run them as part of this audit or by copying them into an unreviewed shell.

The code-defined caller-policy gap and Python bytecode asset issue were corrected locally on 2026-10-22. Focused tests, the requested Ruff command, and fresh CDK synthesis passed; the synthesized provisioner caller and target trust statements match the required session-tag conditions, and staged Lambda assets contain no `.pyc` or `__pycache__`. This is local template evidence only. It does not establish live IAM evaluation or prove tagged `AssumeRole` works in the AWS account.

First configure the approved profile through the organization's approved method. Never paste credential material into a shell command or chat. Then verify identity and region:

```sh
export AWS_PROFILE=spendctl-owner
export AWS_DEFAULT_REGION=ap-south-1
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output json
```

Stop unless this returns the specifically approved account and non-root role. Re-synthesize and review locally before any deployment:

The CDK v2 CLI executable was not present in the local environment during the October 22 audit (`command -v cdk` returned no path). Install or activate the organization-approved CDK v2 CLI compatible with the project before `cdk diff` or `cdk deploy`; `make synth` uses the Python CDK library and does not supply the CLI. The installed Node v26.11.0 also emitted the CDK/JSII unsupported-runtime warning during local synthesis.

```sh
make lint
make test
make synth CDK_OUTDIR=/private/tmp/spendctl-live-demo
```

After explicit owner approval for bootstrap, if bootstrap is actually required, bootstrap is a separate AWS mutation and requires its own reviewed command/role. Do not assume the recorded bootstrap role is safe. After separate deployment approval, first review the proposed change against the intended environment for the selected stacks. `cdk diff` contacts AWS and is not an offline command; the network and Hosted UI parameter values are supplied at deploy time and must be reviewed with the change plan:

```sh
export HOSTED_UI_PREFIX='owner-approved-unique-prefix'
export PRIVATE_SUBNET_ID='owner-verified-private-subnet-id'
export NO_INGRESS_SECURITY_GROUP_ID='owner-verified-security-group-id'
cd infra
cdk diff IdentityStack DataStack LifecycleStack OrchestrationStack ApiStack
cd ..
```

Stop and have the owner review any unexpected resource replacement, deletion, or permission expansion. Then, after separate approval of the reviewed change plan, deploy only the service stacks needed for a localhost UI (not WebStack or BillingStack):

```sh
cd infra
cdk deploy IdentityStack DataStack LifecycleStack OrchestrationStack ApiStack \
  --parameters IdentityStack:HostedUiDomainPrefix="$HOSTED_UI_PREFIX" \
  --parameters OrchestrationStack:PrivateSubnetId="$PRIVATE_SUBNET_ID" \
  --parameters OrchestrationStack:ProvisioningSecurityGroupId="$NO_INGRESS_SECURITY_GROUP_ID" \
  --parameters ApiStack:CloudFrontOrigin=http://localhost:5173
cd ..
```

Review the change plan and confirm the CLI targets the approved account before accepting it. This runbook does not assert that these commands will succeed; CDK CLI version, deployment permissions, bootstrap-role behavior, parameters, quotas, and service integration still need owner validation. Do not opt in to billing reconciliation.

Read stack outputs using the approved profile (do not paste account IDs or output bundles into chat):

```sh
aws cloudformation describe-stacks --stack-name IdentityStack --query 'Stacks[0].Outputs' --output table
aws cloudformation describe-stacks --stack-name ApiStack --query 'Stacks[0].Outputs' --output table
```

The deployment must also have an application-level ledger row for the signed-in team's current period before admission. Check for an existing row first; if none exists, have the owner approve the period and amount, then use the guarded seed command described above. The seed creates both `alpha` and `beta` rows, so approve that scope explicitly. Do not run the seed or change AWS Budgets as part of this local-only review.

## Connect the local browser after approved deployment

Use the actual `ApiUrl` from ApiStack (it already ends in `/v1`), `HostedUiDomain` and `UserPoolClientId` from IdentityStack. These are public frontend configuration values, not secrets. Do not invent placeholder values or use private credentials here.

Create `frontend/.env.local` locally with:

```dotenv
VITE_API_BASE_URL=<ApiUrl output>
VITE_COGNITO_HOSTED_UI_DOMAIN=<HostedUiDomain output>
VITE_COGNITO_CLIENT_ID=<UserPoolClientId output>
VITE_COGNITO_REDIRECT_URI=http://localhost:5173/callback
VITE_COGNITO_LOGOUT_URI=http://localhost:5173/
```

Do not commit `.env.local`. Restart Vite after changing it, from the repository root:

```sh
cd frontend
npm ci
npm test
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://localhost:5173`, sign in with the approved demo Cognito user, and verify the team in the UI. If login or API access fails, stop; do not weaken Cognito/API permissions or CORS as a workaround.

## Bounded live demo steps — only after owner approval

1. On **Cost estimator**, select `t3.micro`, `8` GB gp3, and `1` hour. Enter a clear purpose such as `approved live demo` and calculate. Confirm the pricing provenance and estimate are plausible to the owner; the AWS Price List source is not a complete account bill. If the source is `fallback_unverified`, stop without submitting: the UI labels it and warns, but the submit button remains enabled.
2. Verify the right team budget is open and has enough `available_micro` for the displayed reserve. If not, stop. Do not increase a budget during the demo.
3. Select **Review and submit request** and approve the displayed reserve only if it is within the pre-agreed ceiling. This creates a real DynamoDB reservation and starts a real Step Functions/EC2 workflow. This action incurs AWS charges.
4. In **Requests**, click **Refresh** and open request details until status is `ACTIVE`. Status is manually refreshed; the browser does not poll automatically. If status is pending/unknown or does not advance, do not resubmit with a new key; use the same pending retry flow and inspect owner-authorized logs/state.
5. Immediately request cancellation. Keep the browser open and refresh until cleanup reports complete and the reservation is `CANCELLED`. Unknown or failed cleanup is a stop condition: leave funds held and have the operator investigate; never manually release the hold or terminate an unverified resource.
6. Before ending, verify the owned instance is terminated and its recorded EBS volume is gone using approved read-only inventory/log checks. `make teardown AWS_EXPECTED_ACCOUNT_ID=<approved-account-id>` is an inventory/planner only; it does not delete resources. Do not call it for an account you have not confirmed.
7. Record redacted result, durations, status transitions, resource cleanup evidence, and any errors in `docs/verify-log.md`. Never record tokens, secrets, or unredacted account/resource identifiers.

## Simulated versus live

| Area | Current local evidence | What a live demo must verify |
|---|---|---|
| Frontend/auth | TypeScript/Vite build and Node client tests pass; no Cognito login was tested. | Cognito hosted UI login, callback/PKCE, team claim, and API token acceptance. |
| Estimate | Fixture and fake provider tests; prices have explicit provenance. | Price List access and estimate provenance/rates in the deployed account. |
| Reservation | Moto tests cover transactional budget invariant, idempotency, and ambiguous outcomes. | Real DynamoDB transaction, team isolation, idempotency, and available/reserved/spent invariant. |
| Provisioning | Synthesized Step Functions/Lambda/IAM and fake tests cover guarded behavior. | Real workflow execution, role assumption and conditions, actual EC2 launch in the verified subnet/SG, IMDSv2, ownership tags, and EBS settings. |
| Status/cleanup | Local handler/lifecycle tests cover state polling and conservative unknown outcomes. | Live status transitions, cancellation/sweeper behavior, EC2 termination and EBS removal; verify no orphaned resources. |
| Billing | Local reconciliation tests only; gate defaults to false. | CUR export/schema/attribution and final charge reconciliation. It is not required for the first bounded demo and must remain disabled. |

## Exact next action

Ask the AWS account owner/reviewer to resolve the recorded access and spend blockers and provide: (1) an approved non-admin MFA identity/deployment-role plan, (2) explicit maximum cost approval for a one-hour `t3.micro` plus 8 GB gp3 demo and confirmation of the recorded $10-budget/$11.559-forecast situation, and (3) verified private-subnet and no-ingress security-group IDs. Do not send credentials. Once the owner approves deployment and supplies these prerequisites, the next safe technical command is `make synth CDK_OUTDIR=/private/tmp/spendctl-predeploy-review`; review its templates and parameters before any separately approved deployment. The current environment has not established AWS identity or live prerequisites, so no live browser demo is ready to run.
