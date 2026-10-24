"""Read-only inventory and teardown planner for explicitly owned spendctl resources."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")
_OWNERSHIP_MARKERS = (
    ("tag:project", "spendctl"),
    ("tag:managed-by", "spend-controller"),
)


def inventory_and_plan(ec2: Any, *, environment: str = "dev") -> dict[str, Any]:
    """Inventory ownership-marked EC2/EBS resources; never mutate AWS resources.

    A candidate is eligible for the report's owned target set only when all
    ownership tags, including reservation and team, are present and consistent.
    Any candidate in the requested environment with missing/conflicting tags is
    ambiguous and makes the plan unsafe. Other environments are reported but
    excluded. Unmarked resources are never selected for teardown.
    """
    instance_candidates = _discover(ec2, "describe_instances")
    volume_candidates = _discover(ec2, "describe_volumes")

    instances = _unique(instance_candidates, "InstanceId")
    instance_classification = {
        instance_id: _classify(_tags(row.get("Tags")), environment)
        for instance_id, row in instances.items()
    }
    owned_instance_ids = {
        instance_id
        for instance_id, classification in instance_classification.items()
        if classification[0] == "owned"
    }

    referenced_volume_ids = sorted(
        {
            mapping.get("Ebs", {}).get("VolumeId")
            for instance_id, row in instances.items()
            if instance_id in owned_instance_ids
            for mapping in row.get("BlockDeviceMappings", [])
            if mapping.get("Ebs", {}).get("VolumeId")
        }
    )
    for offset in range(0, len(referenced_volume_ids), 500):
        response = ec2.describe_volumes(VolumeIds=referenced_volume_ids[offset : offset + 500])
        volume_candidates.extend(response.get("Volumes", []))
    volumes = _unique(volume_candidates, "VolumeId")

    instance_rows: list[dict[str, Any]] = []
    volume_rows: list[dict[str, Any]] = []
    ambiguous: list[dict[str, str]] = []
    owned_volumes: set[str] = set()

    for instance_id, row in sorted(instances.items()):
        classification, reason = instance_classification[instance_id]
        detail = {
            "instance_id": instance_id,
            "classification": classification,
            "reason": reason,
            "instance_type": row.get("InstanceType"),
            "state": row.get("State", {}).get("Name"),
            "subnet_id": row.get("SubnetId"),
            "public_ip": row.get("PublicIpAddress"),
            "volume_ids": sorted(
                mapping.get("Ebs", {}).get("VolumeId")
                for mapping in row.get("BlockDeviceMappings", [])
                if mapping.get("Ebs", {}).get("VolumeId")
            ),
        }
        instance_rows.append(detail)
        if classification == "ambiguous":
            ambiguous.append({"resource_type": "instance", "resource_id": instance_id, "reason": reason})

    for volume_id, row in sorted(volumes.items()):
        classification, reason = _classify(_tags(row.get("Tags")), environment)
        attached_to = sorted(
            attachment.get("InstanceId")
            for attachment in row.get("Attachments", [])
            if attachment.get("InstanceId")
        )
        if classification == "owned" and any(instance_id not in owned_instance_ids for instance_id in attached_to):
            classification = "ambiguous"
            reason = "owned volume is attached to an instance that is not fully owned/tagged"
        if classification == "owned":
            owned_volumes.add(volume_id)
        volume_rows.append(
            {
                "volume_id": volume_id,
                "classification": classification,
                "reason": reason,
                "state": row.get("State"),
                "size_gb": row.get("Size"),
                "volume_type": row.get("VolumeType"),
                "encrypted": row.get("Encrypted"),
                "attached_instance_ids": attached_to,
            }
        )
        if classification == "ambiguous":
            ambiguous.append({"resource_type": "volume", "resource_id": volume_id, "reason": reason})

    return {
        "mode": "DRY_RUN",
        "project": "spendctl",
        "environment": environment,
        "safe_to_teardown": not ambiguous,
        "instances": instance_rows,
        "volumes": volume_rows,
        "owned_instance_ids": sorted(owned_instance_ids),
        "owned_volume_ids": sorted(owned_volumes),
        "ambiguous_resources": ambiguous,
        "deletion_actions": [],
        "note": "Read-only inventory only; no EC2 or EBS mutation is implemented or performed.",
    }


def _discover(ec2: Any, operation: str) -> list[dict[str, Any]]:
    paginator = ec2.get_paginator(operation)
    result_key = "Instances" if operation == "describe_instances" else "Volumes"
    rows = []
    for marker, value in _OWNERSHIP_MARKERS:
        pages = paginator.paginate(Filters=[{"Name": marker, "Values": [value]}])
        for page in pages:
            if operation == "describe_instances":
                rows.extend(
                    instance
                    for reservation in page.get("Reservations", [])
                    for instance in reservation.get("Instances", [])
                )
            else:
                rows.extend(page.get(result_key, []))
    return rows


def _unique(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        resource_id = row.get(key)
        if resource_id:
            unique[resource_id] = row
    return unique


def _tags(rows: list[dict[str, Any]] | None) -> dict[str, str]:
    return {str(row.get("Key")): str(row.get("Value", "")) for row in rows or [] if row.get("Key")}


def _classify(tags: dict[str, str], environment: str) -> tuple[str, str]:
    if tags.get("env") and tags["env"] != environment:
        return "other_environment", f"environment tag is {tags['env']!r}, expected {environment!r}"
    required = {
        "project": "spendctl",
        "env": environment,
        "managed-by": "spend-controller",
    }
    missing = [key for key in (*required, "reservation-id", "team") if not tags.get(key)]
    conflicting = [key for key, value in required.items() if tags.get(key) and tags[key] != value]
    if missing or conflicting:
        reason_parts = []
        if missing:
            reason_parts.append("missing required ownership tags: " + ", ".join(sorted(missing)))
        if conflicting:
            reason_parts.append("conflicting ownership tags: " + ", ".join(sorted(conflicting)))
        return "ambiguous", "; ".join(reason_parts)
    return "owned", "all required ownership tags match"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only EC2/EBS inventory and tag-scoped teardown plan; never deletes resources."
    )
    parser.add_argument("--dry-run", action="store_true", default=True, help="Read-only mode (default and only supported mode).")
    parser.add_argument("--apply", action="store_true", help="Rejected: destructive teardown is not implemented.")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--environment", default="dev")
    parser.add_argument("--profile", help="Optional named AWS CLI profile.")
    parser.add_argument(
        "--expected-account",
        default=os.environ.get("AWS_EXPECTED_ACCOUNT_ID"),
        help="Required 12-digit account guard; can be set with AWS_EXPECTED_ACCOUNT_ID.",
    )
    args = parser.parse_args(argv)
    if args.apply:
        parser.error("--apply is unsupported; this tool only creates a read-only dry-run inventory")
    if not args.expected_account or not _ACCOUNT_ID.fullmatch(args.expected_account):
        parser.error("provide --expected-account or AWS_EXPECTED_ACCOUNT_ID as a 12-digit account ID")

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        identity = session.client("sts").get_caller_identity()
        if identity.get("Account") != args.expected_account:
            print("Refusing inventory: caller account does not match --expected-account.", file=sys.stderr)
            return 2
        result = inventory_and_plan(session.client("ec2"), environment=args.environment)
    except (BotoCoreError, ClientError) as error:
        code = error.response.get("Error", {}).get("Code") if isinstance(error, ClientError) else type(error).__name__
        print(f"Read-only AWS inventory failed ({code}); no mutation was attempted.", file=sys.stderr)
        return 1

    result["account"] = identity["Account"]
    result["region"] = args.region
    result["caller_arn"] = identity.get("Arn")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["safe_to_teardown"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
