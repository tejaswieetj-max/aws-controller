# AWS access, cost review, and deployment readiness

This guide is for preparing `spendctl` for a carefully reviewed AWS deployment. It is a manual checklist; following it does not itself authorize deployment. Do not use the AWS root user for CLI/CDK work, and never share credentials, session tokens, SSO start URLs, or unredacted account output in chat.

## Recommended order

Yes, you can do the frontend work first, then AWS access, cost review, and deployment review:

1. Build and run the frontend locally. This is independent of AWS credentials.
2. Arrange a non-root AWS identity and verify which account it reaches.
3. Review account costs, credits, quotas, region, and network inputs.
4. Synthesize and inspect the exact CDK changes; only then consider a separately approved deployment.

The frontend can compile before deployment, but sign-in and API calls require real Cognito/API values. Those values are normally available only after the relevant stacks have been deployed. Keep a local build separate from a live end-to-end claim.

## 1. Prepare AWS access (on your computer)

### Preferred: IAM Identity Center

Ask the account administrator to provide an individual IAM Identity Center assignment for the intended AWS account and a named permission set. The permission set should be least-privilege and reviewed for CDK deployment needs; it should not be administrator access by default. Require MFA. Do not create root access keys, IAM users, or long-lived access keys for this project.

Install/check AWS CLI v2 and configure a profile using the organization's actual Identity Center details (the SSO start URL and Identity Center region are provided by the administrator):

```sh
aws --version
aws configure sso --profile spendctl-dev
aws sso login --profile spendctl-dev
```

Set the intended deployment region in the current terminal and verify identity:

```sh
export AWS_PROFILE=spendctl-dev
export AWS_DEFAULT_REGION=ap-south-1
aws sts get-caller-identity --query '{Account:Account,Arn:Arn,UserId:UserId}' --output json
```

Check that the account ID is the intended account and the ARN identifies the assigned non-root role. Stop if the command fails, returns a root ARN, or shows the wrong account. Do not paste credential files or token values anywhere. If Identity Center is unavailable, ask the administrator for an approved short-lived role/federated access path; do not create an IAM user or attach policies yourself.

### Deployment permissions

The administrator should review the synthesized templates and determine the minimum permissions needed for CDK deployment and bootstrap. CDK bootstrap creates/updates deployment support resources and may create or use deployment roles; it is an AWS change and needs separate review and approval. Do not accept broad administrator policies merely to get past an access-denied error. Record the role/permission-set name, account ID, and region locally for your change review, but do not publish sensitive identifiers.

## 2. Review account costs and safety before any deployment

Do these checks in the AWS Console for the same account and region. They change account settings only when you explicitly save/confirm them; ask the account owner before doing so.

1. **Billing and credits:** inspect current month-to-date spend, available promotional credits and expiry, and any existing budgets. Credits do not guarantee that every service or charge is covered.
2. **Budget notifications:** ask the account owner to configure personal AWS Budgets alerts at 50%, 80%, and 100%, with a recipient they control. Verify delivery. Budget alerts are notifications, not an automatic spending cap.
3. **Service quotas:** check quotas/availability for Lambda, API Gateway, Cognito, DynamoDB, Step Functions, EventBridge Scheduler, CloudWatch, S3, CloudFront, Glue, Athena, and EC2 in `ap-south-1`. Check account-level quotas and regional service availability; do not request quota increases without review.
4. **Networking inputs:** M6 requires an existing subnet and security group. Verify the subnet is private and its route table has no public internet route and no NAT Gateway route. Verify the security group has no ingress rules. This stack does not create or validate the VPC/subnet/security group for you.
5. **EC2 safeguards:** confirm the intended AMI and supported instance/volume/runtime policy. Launched instances must have no public IPv4, no key pair, no ingress, IMDSv2, and controller ownership tags. The design excludes NAT Gateway and public IPv4 for controller-launched instances.
6. **Billing setup:** CUR 2.0/Data Export and cost-allocation tag activation are not established by local tests. Treat creating an export, activating tags, and enabling reconciliation as separate account/billing changes. Keep `EnableBillingReconciliation=false` until the delivered schema, attribution, and completeness have been checked.
7. **Teardown:** understand that `make teardown AWS_EXPECTED_ACCOUNT_ID=<account>` is a read-only inventory/planner, not a resource deletion command. It checks EC2 and EBS resources by ownership tags and refuses ambiguous ownership. It does not clean up unrelated or untagged resources.

Write down expected one-time and ongoing resources/cost drivers from the templates: CloudFront distribution, S3 buckets, Cognito hosted UI, DynamoDB tables, Lambda functions/logs, API Gateway, Step Functions, EventBridge Scheduler, CloudWatch alarms/logs, SNS, and optional Glue/Athena/CUR storage/query charges. Synthesis is not a price quote. Confirm the expected budget and who receives alerts before moving on.

## 3. Reproduce and inspect CDK locally

From the repository root, use the project environment and local-only commands first:

```sh
cd /path/to/aws-controller
source .venv/bin/activate
make lint
make test
make synth CDK_OUTDIR=/private/tmp/spendctl-review
```

`make synth` uses `infra/app.py` and does not deploy. Inspect its output and warnings. Check that `WebStack` reports `FrontendAssetsSynthesized=true`; if it says `false`, do not treat that stack as a deployable website. Ensure the frontend has been configured with the intended public Cognito/API values before building production assets. `VITE_*` values are embedded in browser JavaScript: they must not contain secrets.

Review the complete synthesized templates and change set with the administrator/reviewer. Pay special attention to:

- Every stack and resource being created, changed, or replaced, and deletion/retention policies.
- Per-Lambda execution roles and exact DynamoDB/S3/Athena/EC2/Step Functions permissions; no unexplained wildcard access.
- Cognito callback/logout URLs, hosted UI domain prefix, JWT authorizer, CORS origins, and API throttling.
- The five DynamoDB tables, GSIs, TTL/PITR settings, and table names.
- The `PrivateSubnetId` and `ProvisioningSecurityGroupId` parameters and the live network checks above.
- EC2 launch conditions and tags, no public IP/key pair/ingress, encrypted EBS and delete-on-termination.
- Scheduler/event rules, alarms, alert topic subscriptions, log retention, and disabled-by-default billing reconciliation.
- CloudFront/S3 configuration and whether frontend build assets are actually present.
- Any CDK warnings, empty stacks, unsupported runtime warnings, or assumptions listed in `docs/aws-assumptions.md`.

If a CDK CLI is available and the identity has been verified, `cdk diff` can contact AWS to compare deployed state. Run it only after the account owner authorizes that read and confirm the selected profile/account first. It is not a deploy, but it is not an offline operation. Do not use `cdk deploy`, `make deploy`, or `cdk bootstrap` until the full review is accepted and the user/account owner explicitly approves that AWS change.

## 4. Deployment sequence after review and explicit approval

Deployment is intentionally a separate decision. Before beginning, the operator should have: verified non-root identity/account/region; reviewed budget/credits/quotas; validated the private subnet and no-ingress security group; built frontend assets with correct public settings; reviewed every synthesized stack and IAM permission; decided whether CUR remains disabled; and approved the costs and changes.

Then, with the account owner present, use the repository's CDK application (not SAM):

1. Confirm the terminal still has the approved `AWS_PROFILE` and `AWS_DEFAULT_REGION`; rerun `aws sts get-caller-identity` and stop if anything differs.
2. Review CDK bootstrap requirements and the generated bootstrap change. Bootstrap only with explicit approval.
3. Run the reviewed `cdk diff` against the intended environment and compare it with the locally synthesized templates.
4. Deploy only the specifically approved stacks/parameters. Supply the reviewed private subnet, no-ingress security group, unique Cognito domain prefix, and billing defaults. Do not opt into billing reconciliation.
5. Read CloudFormation outputs and stack events; confirm no unexpected resources or failures. Do not launch a test EC2 request until the owner separately approves a bounded test and its expected maximum cost.
6. Run authenticated smoke checks, then verify logs/metrics, reservation state, and ownership-tagged EC2/EBS inventory. Confirm test resources are cleaned up before ending.
7. Record exact commands, stack IDs, test evidence, resource inventory, and unresolved issues in `docs/verify-log.md`. Never record credentials or tokens.

If any precondition fails, stop at local synthesis and ask the account administrator for the specific missing permission or information. Do not work around access denials with root credentials, broader policies, or unreviewed infrastructure changes.

## Current project boundary

The documented project checks are primarily local tests and synthesis. AWS identity, IAM permissions, live pricing, Cognito/API behavior, DynamoDB contention, Step Functions/EC2 lifecycle, CUR delivery, and CloudWatch/SNS delivery need account-backed verification. No deployment or live integration should be inferred from a successful local build or CDK synthesis.
