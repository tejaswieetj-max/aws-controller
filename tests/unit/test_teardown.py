from __future__ import annotations

import json

import pytest

from scripts import teardown


def _tags(**overrides):
    values = {
        "project": "spendctl",
        "env": "dev",
        "managed-by": "spend-controller",
        "reservation-id": "rsv-1",
        "team": "alpha",
    }
    values.update(overrides)
    return [{"Key": key, "Value": value} for key, value in values.items()]


class FakePaginator:
    def __init__(self, client, operation):
        self.client = client
        self.operation = operation

    def paginate(self, *, Filters):
        self.client.filters.append((self.operation, Filters))
        if self.operation == "describe_instances":
            rows = self.client.instances
            if Filters[0]["Name"] == "tag:project":
                rows = [row for row in rows if _tag_map(row.get("Tags")).get("project") == "spendctl"]
            else:
                rows = [row for row in rows if _tag_map(row.get("Tags")).get("managed-by") == "spend-controller"]
            yield {"Reservations": [{"Instances": rows}]} if rows else {"Reservations": []}
        else:
            rows = self.client.volumes
            if Filters[0]["Name"] == "tag:project":
                rows = [row for row in rows if _tag_map(row.get("Tags")).get("project") == "spendctl"]
            else:
                rows = [row for row in rows if _tag_map(row.get("Tags")).get("managed-by") == "spend-controller"]
            yield {"Volumes": rows}


class FakeEC2:
    def __init__(self, instances=None, volumes=None):
        self.instances = instances or []
        self.volumes = volumes or []
        self.filters = []
        self.volume_id_requests = []

    def get_paginator(self, operation):
        return FakePaginator(self, operation)

    def describe_volumes(self, *, VolumeIds):
        self.volume_id_requests.append(VolumeIds)
        return {"Volumes": [row for row in self.volumes if row["VolumeId"] in VolumeIds]}


def _tag_map(rows):
    return {row["Key"]: row["Value"] for row in rows or []}


def test_dry_run_lists_only_complete_owned_resources_and_never_mutates():
    ec2 = FakeEC2(
        instances=[{
            "InstanceId": "i-owned", "Tags": _tags(), "InstanceType": "t3.micro",
            "State": {"Name": "stopped"}, "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-owned"}}],
        }],
        volumes=[{
            "VolumeId": "vol-owned", "Tags": _tags(), "State": "available", "Size": 8,
            "VolumeType": "gp3", "Encrypted": True, "Attachments": [],
        }],
    )

    result = teardown.inventory_and_plan(ec2)

    assert result["mode"] == "DRY_RUN"
    assert result["safe_to_teardown"] is True
    assert result["owned_instance_ids"] == ["i-owned"]
    assert result["owned_volume_ids"] == ["vol-owned"]
    assert result["deletion_actions"] == []
    assert ec2.volume_id_requests == [["vol-owned"]]
    assert len(ec2.filters) == 4


def test_incomplete_ownership_tags_make_plan_unsafe_and_never_target_resource():
    ec2 = FakeEC2(instances=[{
        "InstanceId": "i-ambiguous", "Tags": _tags(**{"managed-by": ""}),
        "State": {"Name": "running"},
    }])

    result = teardown.inventory_and_plan(ec2)

    assert result["safe_to_teardown"] is False
    assert result["owned_instance_ids"] == []
    assert result["ambiguous_resources"] == [{
        "resource_type": "instance", "resource_id": "i-ambiguous",
        "reason": "missing required ownership tags: managed-by",
    }]


def test_other_environment_is_reported_but_excluded_from_targets():
    ec2 = FakeEC2(instances=[{
        "InstanceId": "i-prod", "Tags": _tags(env="prod"), "State": {"Name": "running"},
    }])

    result = teardown.inventory_and_plan(ec2, environment="dev")

    assert result["safe_to_teardown"] is True
    assert result["owned_instance_ids"] == []
    assert result["instances"][0]["classification"] == "other_environment"


def test_untagged_volume_referenced_by_owned_instance_is_ambiguous():
    ec2 = FakeEC2(
        instances=[{
            "InstanceId": "i-owned", "Tags": _tags(), "State": {"Name": "running"},
            "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-unmarked"}}],
        }],
        volumes=[{
            "VolumeId": "vol-unmarked", "Tags": [], "State": "in-use", "Size": 8,
            "Attachments": [{"InstanceId": "i-owned"}],
        }],
    )

    result = teardown.inventory_and_plan(ec2)

    assert result["safe_to_teardown"] is False
    assert result["owned_instance_ids"] == ["i-owned"]
    assert result["owned_volume_ids"] == []
    assert result["volumes"][0]["classification"] == "ambiguous"


def test_expected_account_mismatch_refuses_before_creating_ec2_client(monkeypatch, capsys):
    class FakeSTS:
        def get_caller_identity(self):
            return {"Account": "111122223333", "Arn": "arn:aws:iam::111122223333:user/dev"}

    class FakeSession:
        def __init__(self, **_kwargs):
            self.services = []

        def client(self, service):
            self.services.append(service)
            if service == "sts":
                return FakeSTS()
            raise AssertionError("EC2 client must not be created after account mismatch")

    session = FakeSession()
    monkeypatch.setattr(teardown.boto3, "Session", lambda **kwargs: session)

    result = teardown.main(["--expected-account", "999900001111"])

    assert result == 2
    assert session.services == ["sts"]
    assert "does not match" in capsys.readouterr().err


def test_destructive_apply_option_is_rejected_before_aws_client_creation(monkeypatch):
    monkeypatch.setattr(
        teardown.boto3,
        "Session",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("AWS session must not be created")),
    )
    with pytest.raises(SystemExit) as error:
        teardown.main(["--expected-account", "111122223333", "--apply"])
    assert error.value.code == 2


def test_report_is_json_serializable():
    result = teardown.inventory_and_plan(FakeEC2())
    assert json.loads(json.dumps(result))["safe_to_teardown"] is True
