# AWS assumptions and verification status

Read-only AWS checks were performed on 2026-10-22 using a verified non-root
identity. No AWS resources or account settings were changed. Verified facts:

- The active IAM user has `AdministratorAccess` and no MFA device. Its credentials
  are sourced from the shared credentials file. It is not suitable for deployment.
- The CDK bootstrap stack exists, but its CloudFormation execution role has
  `AdministratorAccess` and no permissions boundary. No SpendCtl application
  stacks are deployed.
- The account is standalone (AWS Organizations is not in use). No IAM Identity
  Center instance was found in `ap-south-1` or `us-east-1`.
- The default VPC's main route table has an active internet-gateway route. All
  inspected default-VPC subnets auto-assign public IPv4 addresses. Existing
  security groups include ingress from `0.0.0.0/0`; none was selected. No EC2
  instances or EBS volumes currently exist in `ap-south-1`.
- No CUR report definitions or Data Exports exist. The `reservation-id` and
  `team` cost-allocation tags are not active.
- The `$10` monthly budget reports `$0` actual and `$11.559` forecast. Actual
  notifications exist at 50%, 80%, and 100%; no forecast notification was
  found. Cost Explorer results do not establish the remaining credit balance.
  A follow-up credit lookup could not be completed because the saved AWS login
  session had expired and STS requested reauthentication; the credit balance
  therefore remains unverified.
- The sampled standard EC2 quota is 8 vCPUs and Lambda account concurrency is
  10; current SpendCtl usage is zero. Other quota limits and utilization are
  only partially assessed.
- No Route 53 hosted zones or ACM certificates were found in `us-east-1`. The
  synthesized CloudFront distribution uses the default hostname; AWS sets its
  viewer policy to TLSv1 in that mode, so the requested TLS 1.2 minimum is not
  effective.
- The AWS Price List API returned Mumbai on-demand Linux prices of `$0.0112`
  per `t3.micro` instance-hour, `$0.0224` per `t3.small` instance-hour, and
  `$0.0912` per GB-month for gp3 storage.

The following remain assumptions, not verified facts:

- Price List API supports the selected product queries and fields; actual ap-south-1 instance and gp3 rates and query dimensions.
- CUR 2.0/Data Exports availability, hourly/resource ID configuration, exported tag columns, line-item schema, and Athena/Glue partition behavior.
- IAM authorization evaluation for `RunInstances`, `CreateTags`, AMIs, volumes, networking resources, pass-role and reservation-bound STS tags.
- M6 `ProvisionerRole` IAM condition-key enforcement for instance type, region, configured subnet/security group, gp3 volume size/type, request tags and reservation/team principal tags; no live positive/negative IAM evaluation has been run.
- The configured subnet has no NAT route, the configured security group has no ingress, the regional SSM Amazon Linux 2023 parameter resolves to an approved AMI, and EC2 applies the requested instance/volume tag specifications and cleanup semantics.
- Remaining regional quotas and usage for required services, the available
  promotional-credit balance, and delivery of budget notification emails.
- IAM authorization evaluation for project operations, Cognito callback and
  hosted UI behavior, CloudFront OAC behavior, alarm delivery and SNS
  subscription confirmation.
- M7 EventBridge Scheduler one-time schedule delivery/retries and deletion after completion; EventBridge event bus delivery; CloudTrail `RunInstances` event availability; role trust and session tag propagation for the lifecycle reaper's scoped provisioner-role assumption; live Lambda timeout behavior during paginated cleanup; and EC2 `Describe`/`Terminate`/EBS delete outcomes under the synthesized IAM policies.
- Any live pricing, billing, latency, or cleanup behavior.

Each item must be verified with actual account queries and evidence recorded in `docs/verify-log.md`. AWS deployment, EC2 launches, billing configuration, account budget changes, and destructive cleanup require explicit approval.
