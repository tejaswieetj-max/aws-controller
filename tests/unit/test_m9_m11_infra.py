import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from aws_cdk import App, Duration, Stack, assertions
from aws_cdk import aws_apigatewayv2 as apigatewayv2
from aws_cdk import aws_events as events
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_sqs as sqs

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from infra.stacks.foundation import (
    ApiStack,
    BillingStack,
    DataStack,
    LifecycleStack,
    ObservabilityStack,
    OrchestrationStack,
    WebStack,
    _lambda_code,
)


def test_api_synth_includes_authenticated_calibur_and_tau_routes():
    from infra.stacks.foundation import IdentityStack

    app = App()
    identity = IdentityStack(app, "IdentityStack")
    data = DataStack(app, "DataStack")
    source = Stack(app, "Source")
    bus = events.EventBus(source, "Bus")
    api = ApiStack(
        app, "ApiStack", user_pool=identity.pool,
        user_pool_client_id=identity.client.user_pool_client_id,
        data_stack=data, state_machine_arn="arn:aws:states:ap-south-1:123456789012:stateMachine:test",
        lifecycle_reaper_arn="arn:aws:lambda:ap-south-1:123456789012:function:reaper", event_bus=bus,
    )
    template = assertions.Template.from_stack(api).to_json()
    resources = template["Resources"]
    lambda_names = {resource["Properties"].get("FunctionName") for resource in resources.values() if resource["Type"] == "AWS::Lambda::Function"}
    assert "spendctl-dev-m5-state-estimate" in lambda_names
    assert "spendctl-dev-m5-observation-ingestor" in lambda_names
    assert "spendctl-dev-m5-tau-warning-ledger" in lambda_names
    assert "spendctl-dev-m5-tau-final-bill-ledger" in lambda_names
    assert "spendctl-dev-m5-historical-estimate" in lambda_names
    handlers = {
        resource["Properties"].get("FunctionName"): resource["Properties"].get("Handler")
        for resource in resources.values()
        if resource["Type"] == "AWS::Lambda::Function"
    }
    assert (
        handlers["spendctl-dev-m5-calibur-sample-writer"]
        == "spendctl.sample_writer.reservation_settled_handler"
    )
    rule_names = {
        resource["Properties"].get("Name")
        for resource in resources.values()
        if resource["Type"] == "AWS::Events::Rule"
    }
    assert "spendctl-dev-calibur-sample-writer" in rule_names
    routes = [resource["Properties"].get("RouteKey") for resource in resources.values() if resource["Type"] == "AWS::ApiGatewayV2::Route"]
    assert "GET /v1/calibur/state-estimate" in routes
    assert "GET /v1/requests/{id}/evidence-chain" in routes
    assert "POST /v1/calibur/historical-estimate" in routes
    # The live export bucket is account-specific and must come from context,
    # never from a committed literal.
    assert "calibur-focus12" not in str(template)


def test_api_stack_wires_focus_bucket_only_from_context():
    from infra.stacks.foundation import IdentityStack

    app = App(context={"focus_export_bucket": "focus-export-example"})
    identity = IdentityStack(app, "IdentityStack")
    data = DataStack(app, "DataStack")
    source = Stack(app, "Source")
    bus = events.EventBus(source, "Bus")
    api = ApiStack(
        app, "ApiStack", user_pool=identity.pool,
        user_pool_client_id=identity.client.user_pool_client_id,
        data_stack=data, state_machine_arn="arn:aws:states:ap-south-1:123456789012:stateMachine:test",
        lifecycle_reaper_arn="arn:aws:lambda:ap-south-1:123456789012:function:reaper", event_bus=bus,
    )
    body = assertions.Template.from_stack(api).to_json()
    notifications = [
        resource
        for resource in body["Resources"].values()
        if resource["Type"] == "Custom::S3BucketNotifications"
    ]
    assert notifications
    assert notifications[0]["Properties"]["BucketName"] == "focus-export-example"
    assert "focus-export-example" in str(body)


def test_lambda_asset_staging_excludes_python_bytecode(tmp_path: Path):
    source = tmp_path / "source"
    cache = source / "spendctl" / "__pycache__"
    cache.mkdir(parents=True)
    (source / "spendctl" / "handler.py").write_text("def handler(event, context): return {}\n")
    (cache / "handler.cpython-315.pyc").write_bytes(b"bytecode")
    (source / "stray.pyc").write_bytes(b"bytecode")

    app = App(outdir=str(tmp_path / "assembly"))
    stack = Stack(app, "AssetFilterTest")
    lambda_.Function(
        stack,
        "Function",
        runtime=lambda_.Runtime.PYTHON_3_12,
        handler="spendctl.handler.handler",
        code=_lambda_code(str(source)),
    )
    app.synth()

    staged_files = [path for path in (tmp_path / "assembly").rglob("*") if path.is_file()]
    assert any(path.name == "handler.py" for path in staged_files)
    assert not any(path.suffix == ".pyc" for path in staged_files)
    assert not any("__pycache__" in path.parts for path in staged_files)


def test_provisioner_caller_and_trust_allow_only_expected_session_tags():
    app = App()
    data = DataStack(app, "DataStack")
    lifecycle = LifecycleStack(app, "LifecycleStack", data_stack=data)
    orchestration = OrchestrationStack(
        app, "OrchestrationStack", data_stack=data, lifecycle_stack=lifecycle
    )
    template = assertions.Template.from_stack(orchestration).to_json()
    resources = template["Resources"]
    caller_policies = [
        resource["Properties"]["PolicyDocument"]
        for resource in resources.values()
        if resource["Type"] == "AWS::IAM::Policy"
        and any(
            statement.get("Action") == "sts:TagSession"
            or "sts:TagSession" in statement.get("Action", [])
            for statement in resource["Properties"]["PolicyDocument"]["Statement"]
        )
    ]
    assert len(caller_policies) == 1
    tag_permission = next(
        statement
        for statement in caller_policies[0]["Statement"]
        if statement.get("Action") == "sts:TagSession"
    )
    assert tag_permission["Resource"] == {"Fn::GetAtt": ["ProvisionerRoleCD6E5884", "Arn"]}
    assert tag_permission["Condition"] == {
        "StringLike": {"aws:RequestTag/reservation-id": "rsv_*"},
        "Null": {"aws:RequestTag/team": "false"},
        "ForAllValues:StringEquals": {"aws:TagKeys": ["reservation-id", "team"]},
    }

    provisioner_role = resources["ProvisionerRoleCD6E5884"]["Properties"]
    trust_statements = provisioner_role["AssumeRolePolicyDocument"]["Statement"]
    tag_trust = next(statement for statement in trust_statements if statement["Action"] == "sts:TagSession")
    assert tag_trust["Condition"] == tag_permission["Condition"]
    assert provisioner_role["PermissionsBoundary"] == {"Ref": "ProvisionerPermissionsBoundary787D5D96"}
    boundary = resources["ProvisionerPermissionsBoundary787D5D96"]["Properties"]["PolicyDocument"]
    assert boundary["Statement"] == [{
        "Action": "ec2:*",
        "Condition": {"StringEquals": {"aws:RequestedRegion": "ap-south-1"}},
        "Effect": "Allow",
        "Resource": "*",
    }]


def _web_stack(tmp_path: Path):
    app = App()
    web = WebStack(
        app, "WebStack", user_pool_id="pool-id", user_pool_client_id="client-id",
        api_url="https://api.example/v1", frontend_dir=tmp_path,
    )
    template = assertions.Template.from_stack(web)
    template.resource_count_is("AWS::CloudFront::Distribution", 1)
    template.resource_count_is("AWS::S3::Bucket", 1)
    template.has_resource_properties("AWS::S3::Bucket", {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True, "BlockPublicPolicy": True,
            "IgnorePublicAcls": True, "RestrictPublicBuckets": True,
        },
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [{
            "ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
        }]},
    })
    template.has_output("ApiBaseUrl", {"Value": "https://api.example/v1"})
    bucket_policies = template.find_resources("AWS::S3::BucketPolicy")
    assert bucket_policies
    policy_text = str(bucket_policies)
    assert "cloudfront.amazonaws.com" in policy_text
    assert "AWS:SourceArn" in policy_text
    return template


def test_web_stack_synthesizes_without_frontend_assets():
    with TemporaryDirectory() as temporary_directory:
        template = _web_stack(Path(temporary_directory))
    template.has_output("FrontendAssetsSynthesized", {"Value": "false"})


def test_web_stack_synthesizes_frontend_assets_when_directory_is_populated():
    with TemporaryDirectory() as temporary_directory:
        frontend_dir = Path(temporary_directory)
        (frontend_dir / "index.html").write_text("<html></html>", encoding="utf-8")
        template = _web_stack(frontend_dir)
    template.has_output("FrontendAssetsSynthesized", {"Value": "true"})


def test_billing_bucket_allows_only_account_data_exports_delivery():
    app = App()
    data = DataStack(app, "DataStack")
    source = Stack(app, "SourceStack")
    event_bus = events.EventBus(source, "EventBus")
    billing = BillingStack(app, "BillingStack", data_stack=data, event_bus=event_bus)

    bucket_policies = assertions.Template.from_stack(billing).find_resources(
        "AWS::S3::BucketPolicy"
    )
    serialized = str(bucket_policies)
    assert "bcm-data-exports.amazonaws.com" in serialized
    assert "s3:PutObject" in serialized
    assert "aws:SourceArn" in serialized
    assert "aws:SourceAccount" in serialized
    assert "us-east-1" in serialized


def test_observability_synthesizes_dashboard_alarms_and_no_broad_execution_role():
    app = App()
    source = Stack(app, "Sources")
    api = apigatewayv2.HttpApi(source, "Api")
    route_function = lambda_.Function(
        source, "RouteFunction", runtime=lambda_.Runtime.PYTHON_3_12, handler="index.handler",
        code=lambda_.Code.from_inline("def handler(event, context): return {}"),
        timeout=Duration.seconds(10), memory_size=256,
    )
    dlq = sqs.Queue(source, "InvariantDlq")
    api_stack = type("ApiStackRef", (), {"api": api, "api_functions": {"route": route_function}})()
    orchestration = type("OrchestrationRef", (), {"state_machine_arn": "arn:aws:states:ap-south-1:123456789012:stateMachine:test"})()
    lifecycle = type("LifecycleRef", (), {"invariant_dlq": dlq})()
    billing = type("BillingRef", (), {"reconciler": route_function})()
    observability = ObservabilityStack(
        app, "ObservabilityStack", api_stack=api_stack, orchestration_stack=orchestration,
        lifecycle_stack=lifecycle, billing_stack=billing,
    )
    template = assertions.Template.from_stack(observability)
    template.resource_count_is("AWS::CloudWatch::Dashboard", 1)
    template.resource_count_is("AWS::CloudWatch::Alarm", 11)
    template.resource_count_is("AWS::SNS::Topic", 1)
    template.resource_count_is("AWS::IAM::Role", 0)
    template.resource_count_is("AWS::SNS::Subscription", 0)
    body = template.to_json()
    serialized = str(body)
    for metric in ("Spendctl/Workflow", "Spendctl/Lifecycle", "Spendctl/Billing",
                   "Spendctl/Pricing", "Spendctl/Budget", "InvariantViolation"):
        assert metric in serialized


def test_billing_stack_enables_focus_reconciliation_with_finalization_gate():
    app = App()
    data = DataStack(app, "DataStack")
    source = Stack(app, "SourceStack")
    event_bus = events.EventBus(source, "EventBus")
    billing = BillingStack(app, "BillingStack", data_stack=data, event_bus=event_bus)
    template = assertions.Template.from_stack(billing)
    body = template.to_json()
    parameters = body["Parameters"]
    assert parameters["EnableBillingReconciliation"]["Default"] == "true"
    assert parameters["BillingSchema"]["Default"] == "focus"
    assert parameters["BillingFinalizationDelayDays"]["Default"] == 60

    function = next(
        resource
        for resource in body["Resources"].values()
        if resource["Type"] == "AWS::Lambda::Function"
        and resource["Properties"].get("FunctionName") == "spendctl-dev-m8-billing-reconciler"
    )
    variables = function["Properties"]["Environment"]["Variables"]
    assert variables["BILLING_SCHEMA"] == {"Ref": "BillingSchema"}
    assert variables["BILLING_TABLE"] == {"Ref": "CurTableName"}
    assert variables["BILLING_RECONCILIATION_ENABLED"] == {
        "Ref": "EnableBillingReconciliation"
    }
    assert variables["BILLING_FINALIZATION_DELAY_DAYS"] == {
        "Ref": "BillingFinalizationDelayDays"
    }
