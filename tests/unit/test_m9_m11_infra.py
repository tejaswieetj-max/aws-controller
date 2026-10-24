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
    BillingStack,
    DataStack,
    LifecycleStack,
    ObservabilityStack,
    OrchestrationStack,
    WebStack,
    _lambda_code,
)


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
