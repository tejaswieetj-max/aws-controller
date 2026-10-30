#!/usr/bin/env python3
"""Read-only S3 delivery evidence collector for CALIBUR."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import boto3


def utc_now():
    return datetime.now(UTC).isoformat()


def sha256_s3_object(s3, bucket, key):
    """Stream an object to hash it without saving it locally."""
    digest = hashlib.sha256()
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    try:
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        body.close()
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "ap-south-1"))
    parser.add_argument(
        "--output",
        default="data/raw/collector_observations/s3_observations.jsonl",
    )
    parser.add_argument(
        "--hash-objects",
        action="store_true",
        help="Stream each object to compute SHA-256; increases data transfer.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    s3 = boto3.client("s3", region_name=args.region)
    paginator = s3.get_paginator("list_objects_v2")

    count = 0
    with output.open("a", encoding="utf-8") as stream:
        for page in paginator.paginate(Bucket=args.bucket, Prefix=args.prefix):
            for obj in page.get("Contents", []):
                observed_at = utc_now()
                key = obj["Key"]
                record = {
                    "source": "aws_s3",
                    "bucket": args.bucket,
                    "object_key": key,
                    "region": args.region,
                    "observed_at_utc": observed_at,
                    "s3_last_modified_utc": obj["LastModified"].astimezone(
                        UTC
                    ).isoformat(),
                    "size_bytes": obj["Size"],
                    "etag": obj.get("ETag"),
                    "s3_checksum_sha256": obj.get("ChecksumSHA256"),
                    "sha256": None,
                    "sha256_status": "not_computed",
                    "observation_scope": "object_metadata",
                }
                if args.hash_objects:
                    record["sha256"] = sha256_s3_object(s3, args.bucket, key)
                    record["sha256_status"] = "computed_by_streaming_object"
                    record["observation_scope"] = "object_metadata_and_content_hash"

                stream.write(json.dumps(record, sort_keys=True) + "\n")
                stream.flush()
                count += 1

    print(f"Appended {count} object observations to {output}")
    print("This records S3 object observations, not billing-record revision proof.")


if __name__ == "__main__":
    main()
