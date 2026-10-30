"""Bounded, owner-gated live lifecycle verifier for Spendctl/CALIBUR.

This script verifies the full admission lifecycle against a *live* deployment:

    Preflight (read-only) -> Seed budget (mutation) -> POST /v1/estimate
    -> POST /v1/requests -> poll ACTIVE -> verify EC2 launch
    -> POST /v1/requests/{id}/cancel -> poll cleanup
    -> verify teardown + budget settlement

SAFETY MODEL
-----------
It performs NO AWS API calls and NO HTTP requests unless given explicit,
deliberate flags:

  --confirm-account <12-digit>   Required for *any* AWS contact (even read-only).
  --preflight                    Run only read-only checks; mutate nothing.
  --apply                        Permit mutation phases (seed + create request +
                                 cancel). Implies the demo incurs AWS charges.

The demo envelope is hard-bounded to one ``t3.micro``, 8 GB gp3, 1 hour, and
refuses to proceed if the live estimate exceeds ``--max-cost-usd``. These match
the live-aws-demo-runbook bounded-demo constraints and ``validation.py``.

This is a tool for the account owner/operator to run after their explicit
approval. It is not a local Moto test and does not authorize deployment,
seeding, or EC2 launch by itself.  See AGENTS.md and
``review/live-aws-demo-runbook.md`` for the full gating prerequisites.

Usage (read-only preflight only)::

    PYTHONPATH=services python3 scripts/verify_live.py \\
        --confirm-account 123456789012 --preflight \\
        --region ap-south-1 --team alpha --api-url https://<id>.execute-api.<region>.amazonaws.com/v1

Full lifecycle (mutation + billable; owner-approved only)::

    PYTHONPATH=services python3 scripts/verify_live.py \\
        --confirm-account 123456789012 --apply \\
        --max-cost-usd 0.05 --team alpha --period 2026-10 \\
        --api-url https://<id>.execute-api.ap-south-1.amazonaws.com/v1 \\
        --id-token <cognito-id-token-from-browser>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from spendctl.ledger import DynamoLedger
from spendctl.money import usd_to_micro
from spendctl.validation import validate_request

# --- Bounded demo envelope (do not widen without owner cost re-approval) -----
DEMO_INSTANCE_TYPE = "t3.micro"
DEMO_EBS_GB = 8
DEMO_DURATION_HOURS = 1
DEMO_PURPOSE = "approved live demo"
REQUIRED_STACKS = ("IdentityStack", "DataStack", "LifecycleStack", "OrchestrationStack", "ApiStack")
TABLE_PREFIX = "spendctl-dev"
_ACCOUNT_RE = re.compile(r"^[0-9]{12}$")
_ID_RE = re.compile(r"(i-[0-9a-f]{8,17}|vol-[0-9a-f]{8,17}|rsv_[0-9a-f]{8,40})")
_ACCOUNT_MASK = re.compile(r"\b\d{12}\b")
_POLL_INTERVAL = 10
_POLL_MAX_ATTEMPTS = 40  # ~6-7 minutes, matching the workflow wait window


def _redact(text: str) -> str:
    """Mask account IDs and AWS resource IDs so output is safe to share."""
    if not isinstance(text, str):
        text = str(text)
    text = _ACCOUNT_MASK.sub("************", text)
    return _ID_RE.sub(lambda m: m.group(1)[:3] + "…" + m.group(1)[-2:], text)


def _log(phase: str, message: str) -> None:
    print(f"[{phase}] {message}", file=sys.stderr)


def _die(message: str, code: int = 2) -> None:
    _log("error", message)
    raise SystemExit(code)


def _verify_identity(region: str, expected_account: str) -> str:
    sts = boto3.client("sts", region_name=region)
    actual = sts.get_caller_identity()["Account"]
    if actual != expected_account:
        _die(
            f"AWS identity {_redact(actual)} does not match --confirm-account; "
            "stopping before any mutation"
        )
    return actual


def _stacks_deployed(region: str) -> list[str]:
    cfn = boto3.client("cloudformation", region_name=region)
    missing = []
    for name in REQUIRED_STACKS:
        try:
            cfn.describe_stacks(StackName=name)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code in {"ValidationError", "ResourceNotFoundException", "AmazonCloudFormationException"}:
                missing.append(name)
                continue
            raise
    return missing


def _budget_row_exists(ledger: DynamoLedger, team: str, period: str) -> dict | None:
    try:
        return ledger.get_budget(team, period) or None
    except (BotoCoreError, ClientError) as error:
        _die(f"budget read failed for {team}/{period}: {type(error).__name__}")
        return None  # unreachable; satisfies type checkers


def _check_budget_invariant(budget: dict, label: str) -> bool:
    total = (
        int(budget["available_micro"])
        + int(budget["reserved_micro"])
        + int(budget["spent_micro"])
    )
    ok = total == int(budget["budget_micro"])
    if not ok:
        _log(label, f"BUDGET INVARIANT BROKEN: available+reserved+spent={total} != cap={budget['budget_micro']}")
    return ok


def preflight(args: argparse.Namespace) -> int:
    """Read-only: verify identity, deployed stacks, budget row, and AMI."""
    _log("preflight", "Running read-only checks (no mutations).")
    account = _verify_identity(args.region, args.confirm_account)
    _log("preflight", f"identity confirmed for account {_redact(account)} in {args.region}")

    missing = _stacks_deployed(args.region)
    if missing:
        _die(f"stacks not deployed: {', '.join(missing)}; deploy before verifying")
    _log("preflight", f"all required stacks present: {', '.join(REQUIRED_STACKS)}")

    ledger = _ledger(args)
    budget = _budget_row_exists(ledger, args.team, args.period)
    if not budget:
        _log("preflight", f"NO budget row for {args.team}/{args.period} — run with --apply to seed one")
        return 1
    _log("preflight", f"budget row exists for {args.team}/{args.period}")
    _check_budget_invariant(budget, "preflight")

    try:
        ssm = boto3.client("ssm", region_name=args.region)
        ami = ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-minimal-amd64")[
            "Parameter"
        ]["Value"]
        _log("preflight", f"resolved minimal AL2023 AMI: {_redact(ami)}")
    except (BotoCoreError, ClientError) as error:
        _log("preflight", f"AMI parameter read failed ({type(error).__name__}); the provisioner resolves AMI at runtime")

    if not args.api_url:
        _log("preflight", "--api-url not set; create-request/poll/cancel phases are unavailable")
    if not args.id_token:
        _log("preflight", "--id-token not set; API phases require a Cognito ID token")
    _log("preflight", "preflight complete.")
    return 0


def _ledger(args: argparse.Namespace) -> DynamoLedger:
    db = boto3.client("dynamodb", region_name=args.region)
    return DynamoLedger(
        db,
        f"{TABLE_PREFIX}-budgets",
        f"{TABLE_PREFIX}-reservations",
        f"{TABLE_PREFIX}-alerts",
    )


def seed_budget(args: argparse.Namespace) -> None:
    """Mutation: create a budget row for the team/period if none exists."""
    ledger = _ledger(args)
    existing = _budget_row_exists(ledger, args.team, args.period)
    if existing:
        _log("seed", f"budget row already exists for {args.team}/{args.period}; not overwritten")
        return
    try:
        budget_micro = usd_to_micro(Decimal(args.budget_usd))
    except (InvalidOperation, TypeError, ValueError) as error:
        _die(f"invalid --budget-usd: {error}")
    if budget_micro <= 0:
        _die("--budget-usd must be greater than zero")
    result = ledger.create_budget(team=args.team, period=args.period, budget_micro=budget_micro)
    _log("seed", f"created budget for {args.team}/{args.period}: {'replayed' if result.get('replayed') else 'created'}")


def _api_call(args: argparse.Namespace, method: str, path: str, body: dict | None = None) -> dict:
    if not args.api_url:
        _die("--api-url is required for API phases")
    if not args.id_token:
        _die("--id-token is required for API phases")
    url = args.api_url.rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {args.id_token}",
        "Content-Type": "application/json",
    }
    if method == "POST" and path == "/requests":
        key = f"live-verify-{int(datetime.now(UTC).timestamp())}"
        headers["Idempotency-Key"] = key
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        _die(
            f"API {method} {path} returned HTTP {error.code}: {_redact(detail)[:500]}"
        )
    except urllib.error.URLError as error:
        _die(f"API {method} {path} request failed: {error.reason}")


def estimate_and_check_ceiling(args: argparse.Namespace) -> dict:
    """Call POST /v1/estimate and refuse if the cost exceeds the owner ceiling."""
    body = {
        "team": args.team,
        "resource": {"type": "ec2", "instance_type": DEMO_INSTANCE_TYPE, "ebs_gb": DEMO_EBS_GB},
        "duration_hours": DEMO_DURATION_HOURS,
        "purpose": DEMO_PURPOSE,
    }
    estimate = _api_call(args, "POST", "/estimate", body)
    total_micro = int(estimate.get("total_micro", 0))
    total_usd = total_micro / 1_000_000
    _log("estimate", f"live estimate: ${total_usd:.6f} (source={estimate.get('source', 'unknown')})")
    if estimate.get("source") == "fallback_unverified":
        _die("pricing source is fallback_unverified; stopping without submitting per the runbook")
    ceiling_micro = int(usd_to_micro(Decimal(str(args.max_cost_usd))))
    if total_micro > ceiling_micro:
        _die(
            f"estimate ${total_usd:.6f} exceeds approved ceiling ${args.max_cost_usd}; "
            "increase --max-cost-usd only with owner re-approval"
        )
    _log("estimate", f"estimate within ceiling (${args.max_cost_usd})")
    return estimate


def create_request(args: argparse.Namespace) -> dict:
    """Mutation: POST /v1/requests and return the reservation."""
    request = validate_request(
        {
            "team": args.team,
            "resource": {"type": "ec2", "instance_type": DEMO_INSTANCE_TYPE, "ebs_gb": DEMO_EBS_GB},
            "duration_hours": DEMO_DURATION_HOURS,
            "purpose": DEMO_PURPOSE,
        }
    )
    if request.instance_type != DEMO_INSTANCE_TYPE or request.ebs_gb != DEMO_EBS_GB:
        _die("internal error: bounded envelope was violated before submission")
    result = _api_call(
        args,
        "POST",
        "/requests",
        {
            "team": args.team,
            "resource": {"type": "ec2", "instance_type": request.instance_type, "ebs_gb": request.ebs_gb},
            "duration_hours": request.duration_hours,
            "purpose": request.purpose,
        },
    )
    reservation_id = result.get("reservation_id")
    if not reservation_id:
        _die(f"create request returned no reservation_id: {_redact(json.dumps(result))[:500]}")
    _log(
        "create",
        f"admitted reservation {_redact(reservation_id)}; "
        f"state={result.get('state')} provisioning_started={result.get('provisioning_started')}",
    )
    return result


def poll_until_active(args: argparse.Namespace, reservation_id: str) -> dict:
    """Read-only: poll GET /v1/requests/{id} until the reservation is ACTIVE."""
    for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
        result = _api_call(args, "GET", f"/requests/{reservation_id}")
        reservation = result.get("request", result)
        status = reservation.get("status")
        _log("poll", f"attempt {attempt}: status={status} (cleanup={reservation.get('workflow_cleanup_state')})")
        if status == "ACTIVE":
            return reservation
        if status in {"EXPIRED", "CANCELLED", "RELEASED", "SETTLED"}:
            _die(f"reservation reached terminal status {status} before ACTIVE; inspect logs")
        time.sleep(_POLL_INTERVAL)
    _die(f"reservation did not reach ACTIVE within {_POLL_MAX_ATTEMPTS * _POLL_INTERVAL}s")
    return {}  # unreachable


def verify_ec2_launch(args: argparse.Namespace, reservation: dict) -> dict:
    """Read-only: verify the launched instance has required tags, no public IP, IMDSv2."""
    instance_id = reservation.get("instance_id")
    volume_ids = reservation.get("volume_ids", [])
    if not instance_id:
        _die("reservation has no instance_id; EC2 launch may not have completed")
    ec2 = boto3.client("ec2", region_name=args.region)
    try:
        response = ec2.describe_instances(InstanceIds=[instance_id])
    except (BotoCoreError, ClientError) as error:
        _die(f"describe_instances failed for {_redact(instance_id)}: {type(error).__name__}")
    reservations = response.get("Reservations", [])
    instances = [inst for res in reservations for inst in res.get("Instances", [])]
    if not instances:
        _die(f"instance {_redact(instance_id)} not found")
    instance = instances[0]
    if instance.get("State", {}).get("Name") != "running":
        _die(f"instance {_redact(instance_id)} is {instance.get('State', {}).get('Name')}, not running")
    if instance.get("PublicIpAddress"):
        _die(f"instance {_redact(instance_id)} has a public IP; violates the no-public-IPv4 posture")
    tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
    required_values = {"project": "spendctl", "managed-by": "spend-controller"}
    missing_values = {key for key, expected in required_values.items() if tags.get(key) != expected}
    required_keys = {"reservation-id", "team"}
    missing_keys = {key for key in required_keys if key not in tags}
    problems = sorted(missing_values | missing_keys)
    if problems:
        _die(f"instance {_redact(instance_id)} ownership tags incomplete/wrong: {problems}")
    http_tokens = instance.get("MetadataOptions", {}).get("HttpTokens")
    if http_tokens != "required":
        _die(f"instance {_redact(instance_id)} IMDSv2 is {http_tokens}, not 'required'")
    _log(
        "ec2-verify",
        f"instance {_redact(instance_id)} running, no public IP, IMDSv2 required, tags present",
    )
    if volume_ids:
        _log("ec2-verify", f"recorded volumes: {[_redact(v) for v in volume_ids]}")
    return instance


def cancel_request(args: argparse.Namespace, reservation_id: str) -> dict:
    """Mutation: POST /v1/requests/{id}/cancel."""
    result = _api_call(args, "POST", f"/requests/{reservation_id}/cancel")
    _log("cancel", f"cancellation requested; state={result.get('cancellation_requested')} cleanup={result.get('cleanup_state')}")
    return result


def poll_until_cleanup(args: argparse.Namespace, reservation_id: str) -> dict:
    """Read-only: poll until the reservation is CANCELLED with COMPLETE cleanup."""
    for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
        result = _api_call(args, "GET", f"/requests/{reservation_id}")
        reservation = result.get("request", result)
        status = reservation.get("status")
        cleanup = reservation.get("workflow_cleanup_state")
        _log("poll-cleanup", f"attempt {attempt}: status={status} cleanup={cleanup}")
        if status == "CANCELLED" and cleanup == "COMPLETE":
            return reservation
        if status == "CANCELLED" and cleanup not in (None, "PENDING", "UNKNOWN", "COMPLETE"):
            _die(f"cleanup ended in unexpected state {cleanup}; leave funds held and investigate")
        time.sleep(_POLL_INTERVAL)
    _die(
        f"cleanup did not complete within {_POLL_MAX_ATTEMPTS * _POLL_INTERVAL}s; "
        "do NOT manually release the hold; investigate"
    )
    return {}  # unreachable


def verify_teardown(args: argparse.Namespace, reservation: dict) -> None:
    """Read-only: verify EC2 terminated, EBS deleted, and budget invariant holds."""
    instance_id = reservation.get("instance_id")
    volume_ids = reservation.get("volume_ids", [])
    ec2 = boto3.client("ec2", region_name=args.region)
    if instance_id:
        try:
            response = ec2.describe_instances(InstanceIds=[instance_id])
            instances = [inst for res in response.get("Reservations", []) for inst in res.get("Instances", [])]
            state = instances[0].get("State", {}).get("Name") if instances else "absent"
            if state not in {"terminated", "shutting-down", "absent"}:
                _die(f"instance {_redact(instance_id)} is {state}, not terminated")
            _log("teardown-verify", f"instance {_redact(instance_id)} is {state}")
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code != "InvalidInstanceID.NotFound":
                _die(f"describe_instances failed: {type(error).__name__}")
            _log("teardown-verify", f"instance {_redact(instance_id)} is gone (NotFound)")
    for volume_id in volume_ids:
        try:
            response = ec2.describe_volumes(VolumeIds=[volume_id])
            volumes = response.get("Volumes", [])
            if volumes:
                vstate = volumes[0].get("State")
                if vstate != "deleted":
                    _die(f"volume {_redact(volume_id)} is {vstate}, not deleted")
            _log("teardown-verify", f"volume {_redact(volume_id)} is deleted/absent")
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code != "InvalidVolume.NotFound":
                _die(f"describe_volumes failed: {type(error).__name__}")
            _log("teardown-verify", f"volume {_redact(volume_id)} is gone (NotFound)")


def verify_budget_settlement(args: argparse.Namespace, reservation_id: str) -> dict:
    """Read-only: verify the reservation released/settled and the budget invariant holds."""
    ledger = _ledger(args)
    reservation = ledger.get_reservation(reservation_id)
    status = reservation.get("status")
    stage = reservation.get("settlement_stage")
    _log(
        "settlement-verify",
        f"reservation {_redact(reservation_id)} status={status} stage={stage}",
    )
    if status not in {"RELEASED", "SETTLED"}:
        _die(f"reservation is {status}, not RELEASED/SETTLED; funds may still be held — investigate")
    budget = ledger.get_budget(args.team, args.period)
    if not budget:
        _die(f"budget row missing for {args.team}/{args.period} after teardown")
    _check_budget_invariant(budget, "settlement-verify")
    _log(
        "settlement-verify",
        f"budget invariant holds: available={budget['available_micro']} "
        f"reserved={budget['reserved_micro']} spent={budget['spent_micro']} "
        f"cap={budget['budget_micro']}",
    )
    return budget


def run_full_lifecycle(args: argparse.Namespace) -> int:
    _log("lifecycle", "starting bounded live lifecycle verification (mutation + billable)")
    account = _verify_identity(args.region, args.confirm_account)
    _log("lifecycle", f"confirmed account {_redact(account)} in {args.region}")

    missing = _stacks_deployed(args.region)
    if missing:
        _die(f"stacks not deployed: {', '.join(missing)}")
    seed_budget(args)

    estimate_and_check_ceiling(args)
    created = create_request(args)
    reservation_id = created["reservation_id"]

    # Verify the budget hold immediately after admission (read-only ledger).
    ledger = _ledger(args)
    budget_after = ledger.get_budget(args.team, args.period)
    _check_budget_invariant(budget_after, "post-admission")

    active = poll_until_active(args, reservation_id)
    verify_ec2_launch(args, active)
    cancel_request(args, reservation_id)
    cancelled = poll_until_cleanup(args, reservation_id)
    verify_teardown(args, cancelled)
    verify_budget_settlement(args, reservation_id)

    _log("lifecycle", "FULL LIFECYCLE VERIFIED: request -> reservation -> EC2 -> teardown -> settlement")
    _log("lifecycle", "all output above is redacted; safe to record in docs/verify-log.md")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bounded, owner-gated live lifecycle verifier (no AWS calls without flags)."
    )
    parser.add_argument("--confirm-account", help="Exact 12-digit AWS account ID; required for any AWS contact")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--team", default="alpha", help="Team to verify (must match the id-token claim)")
    parser.add_argument("--period", default=datetime.now(UTC).strftime("%Y-%m"), help="Budget period YYYY-MM")
    parser.add_argument("--preflight", action="store_true", help="Run read-only checks only; mutate nothing")
    parser.add_argument("--apply", action="store_true", help="Permit mutation + billable phases")
    parser.add_argument("--budget-usd", default="10.00", help="Budget amount in USD for seeding (if --apply)")
    parser.add_argument("--max-cost-usd", default="0.05", help="Owner-approved estimate ceiling in USD")
    parser.add_argument("--api-url", help="Live API Gateway base URL ending in /v1")
    parser.add_argument("--id-token", help="Cognito ID token (obtain via the browser runbook; never commit it)")
    args = parser.parse_args()

    if not (args.preflight or args.apply):
        _log(
            "usage",
            "No --preflight or --apply given; no AWS calls will be made. "
            "Use --preflight for read-only checks, or --apply for the full mutation + billable lifecycle.",
        )
        parser.print_help()
        return
    if not args.confirm_account or not _ACCOUNT_RE.fullmatch(args.confirm_account):
        _die("--confirm-account with the reviewed 12-digit account ID is required for any AWS contact")
    if args.apply and (not args.api_url or not args.id_token):
        _die("--apply requires --api-url and --id-token for the full lifecycle")

    try:
        if args.preflight and not args.apply:
            code = preflight(args)
            raise SystemExit(code)
        if args.apply:
            raise SystemExit(run_full_lifecycle(args))
    except SystemExit:
        raise
    except (BotoCoreError, ClientError) as error:
        _die(f"AWS error: {type(error).__name__}: {str(error)[:200]}")


if __name__ == "__main__":
    main()
