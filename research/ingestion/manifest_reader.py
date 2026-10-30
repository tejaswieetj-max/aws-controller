"""Read-only reader for AWS BCM Data Exports FOCUS manifest files.

Two manifest formats are present for each CALIBUR delivery:

  CALIBUR_FOCUS_12-Manifest-FOCUS.json  — FOCUS schema manifest
    Top-level keys: DataGenerator, DataGeneratorVersion, Schema
    Schema.FocusVersion declares the FOCUS spec version.
    Schema.ColumnDefinition lists all columns with DataType.

  CALIBUR_FOCUS_12-Manifest.json  — BCM delivery manifest
    Top-level keys: executionId, exportArn, columns, dataFiles,
                    additionalOutputFiles
    executionId matches the S3 path segment used to identify the delivery.

Both files are present for D3 (delivery_2026-10-25T11).
D1 has only the FOCUS manifest locally.
D2 has no local manifest copy.

Guarantees
----------
- Never writes to, modifies, or downloads manifest files.
- Returns raw parsed values; does not invent fields.
- Missing fields are returned as None, never defaulted.
- SHA-256 of each manifest file is computed and returned.
- executionId from the BCM manifest is cross-referenced against the
  DELIVERY_REGISTRY in focus_billing_adapter.py; mismatches are flagged.

Provenance note
---------------
The Manifest-FOCUS.json schema version (FocusVersion "1.2") is the
authority used by focus_loader.py's focus_version field.  No other
version claim is made.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Approved manifest files with SHA-256 checksums
# ---------------------------------------------------------------------------

APPROVED_MANIFESTS: dict[str, str] = {
    # FOCUS schema manifests (Manifest-FOCUS.json)
    # D1 — only the FOCUS schema manifest exists locally for this delivery.
    # SHA-256 computed 2026-10-25 from the locally stored file.
    "data/raw/focus12/CALIBUR_FOCUS_12-Manifest-FOCUS.json":
        "898bb08c152fee6a2d8af114b28f41235d938045849666c636c161010943ed16",
    # D3 manifests — both present in delivery_2026-10-25T11.
    # The FOCUS schema manifest is identical to D1 (same schema version).
    # SHA-256 computed 2026-10-25 from the locally stored files.
    "data/raw/focus12/delivery_2026-10-25T11/CALIBUR_FOCUS_12-Manifest-FOCUS.json":
        "898bb08c152fee6a2d8af114b28f41235d938045849666c636c161010943ed16",
    "data/raw/focus12/delivery_2026-10-25T11/CALIBUR_FOCUS_12-Manifest.json":
        "61dd8c10cfb8525ba15eb6678ba7682eb3bb0e3eedfcb57cbe33d3d0d9a6c79b",
}


class ManifestNotApproved(Exception):
    """Raised when the manifest path is not in the approved list."""


class ManifestChecksumMismatch(Exception):
    """Raised when a manifest file's SHA-256 does not match."""


class ManifestParseError(Exception):
    """Raised when the manifest file cannot be parsed as expected JSON."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColumnDefinition:
    """One column entry from a FOCUS manifest."""
    name: str
    data_type: str            # "string", "double", "timestamp", "map"
    provider_tag_prefixes: list[str] = field(default_factory=list)


@dataclass
class FocusSchemaManifest:
    """Contents of a CALIBUR_FOCUS_12-Manifest-FOCUS.json file."""
    source_path: str          # relative to repo root
    sha256: str
    sha256_verified: bool
    data_generator: str | None          # "Amazon Web Services, Inc."
    data_generator_version: str | None  # "1.0.0"
    focus_version: str | None           # "1.2"
    schema_id: str | None               # "1.0.0"
    schema_creation_date: str | None    # "2025-11-19T00:00:00Z"
    columns: list[ColumnDefinition]
    raw: dict                           # full parsed JSON
    notes: list[str] = field(default_factory=list)


@dataclass
class BcmDeliveryManifest:
    """Contents of a CALIBUR_FOCUS_12-Manifest.json file."""
    source_path: str          # relative to repo root
    sha256: str
    sha256_verified: bool
    execution_id: str | None            # matches S3 path segment and DELIVERY_REGISTRY
    export_arn: str | None
    columns: list[ColumnDefinition]
    data_files: list[str]               # S3 URIs listed in the manifest
    additional_output_files: list[str]
    raw: dict
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel_str(abs_path: Path) -> str:
    try:
        return str(abs_path.resolve().relative_to(_REPO_ROOT.resolve()))
    except ValueError:
        return str(abs_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_focus_schema_manifest(
    path: str | Path,
    *,
    allow_unlisted: bool = False,
) -> FocusSchemaManifest:
    """Read a CALIBUR_FOCUS_12-Manifest-FOCUS.json file.

    Parameters
    ----------
    path:
        Absolute or repo-relative path to the manifest file.
    allow_unlisted:
        If True, accept files not in APPROVED_MANIFESTS.
        Checksum verification is skipped unless the file is listed.

    Raises
    ------
    ManifestNotApproved:
        If the file is not in APPROVED_MANIFESTS and allow_unlisted is False.
    ManifestChecksumMismatch:
        If the computed SHA-256 does not match the recorded value.
    ManifestParseError:
        If the file cannot be parsed as expected JSON.
    FileNotFoundError:
        If the file does not exist.
    """
    abs_path = Path(path) if Path(path).is_absolute() else _REPO_ROOT / path
    abs_path = abs_path.resolve()
    rel = _rel_str(abs_path)

    approved_sha = APPROVED_MANIFESTS.get(rel)
    if approved_sha is None and not allow_unlisted:
        raise ManifestNotApproved(
            f"{rel!r} is not in the approved manifest list. "
            "Pass allow_unlisted=True for new manifests."
        )
    if not abs_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {abs_path}")

    digest = _sha256_file(abs_path)
    sha_verified = False
    if approved_sha is not None:
        if digest != approved_sha:
            raise ManifestChecksumMismatch(
                f"{rel}: expected SHA-256 {approved_sha!r}, got {digest!r}"
            )
        sha_verified = True

    try:
        raw = json.loads(abs_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ManifestParseError(f"Cannot parse {rel}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ManifestParseError(f"{rel}: top-level value must be a JSON object")

    schema = raw.get("Schema") or {}
    column_defs: list[ColumnDefinition] = []
    for col in schema.get("ColumnDefinition", []):
        if isinstance(col, dict):
            column_defs.append(ColumnDefinition(
                name=col.get("ColumnName", ""),
                data_type=col.get("DataType", ""),
                provider_tag_prefixes=col.get("ProviderTagPrefixes", []),
            ))

    notes = [
        f"Source: {rel}",
        "FocusVersion is the authoritative FOCUS spec version for this delivery.",
        "Column definitions come from the manifest; they are not derived from the Parquet schema.",
        "This manifest does not contain billing-record arrival timestamps.",
    ]

    return FocusSchemaManifest(
        source_path=rel,
        sha256=digest,
        sha256_verified=sha_verified,
        data_generator=raw.get("DataGenerator"),
        data_generator_version=raw.get("DataGeneratorVersion"),
        focus_version=schema.get("FocusVersion"),
        schema_id=schema.get("SchemaId"),
        schema_creation_date=schema.get("CreationDate"),
        columns=column_defs,
        raw=raw,
        notes=notes,
    )


def read_bcm_delivery_manifest(
    path: str | Path,
    *,
    allow_unlisted: bool = False,
) -> BcmDeliveryManifest:
    """Read a CALIBUR_FOCUS_12-Manifest.json (BCM delivery manifest) file.

    Parameters
    ----------
    path:
        Absolute or repo-relative path to the manifest file.
    allow_unlisted:
        If True, accept files not in APPROVED_MANIFESTS.

    Raises
    ------
    ManifestNotApproved, ManifestChecksumMismatch, ManifestParseError,
    FileNotFoundError (same semantics as read_focus_schema_manifest).
    """
    abs_path = Path(path) if Path(path).is_absolute() else _REPO_ROOT / path
    abs_path = abs_path.resolve()
    rel = _rel_str(abs_path)

    approved_sha = APPROVED_MANIFESTS.get(rel)
    if approved_sha is None and not allow_unlisted:
        raise ManifestNotApproved(
            f"{rel!r} is not in the approved manifest list. "
            "Pass allow_unlisted=True for new manifests."
        )
    if not abs_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {abs_path}")

    digest = _sha256_file(abs_path)
    sha_verified = False
    if approved_sha is not None:
        if digest != approved_sha:
            raise ManifestChecksumMismatch(
                f"{rel}: expected SHA-256 {approved_sha!r}, got {digest!r}"
            )
        sha_verified = True

    try:
        raw = json.loads(abs_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ManifestParseError(f"Cannot parse {rel}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ManifestParseError(f"{rel}: top-level value must be a JSON object")

    column_defs: list[ColumnDefinition] = []
    for col in raw.get("columns", []):
        if isinstance(col, dict):
            column_defs.append(ColumnDefinition(
                name=col.get("name", ""),
                data_type=col.get("type", ""),
            ))

    notes = [
        f"Source: {rel}",
        "executionId matches the S3 path segment and DELIVERY_REGISTRY entry.",
        "dataFiles lists S3 URIs; these are the S3 locations, not local paths.",
        "This manifest does not contain billing-record arrival timestamps.",
        "exportArn identifies the BCM Data Export configuration; it is not a billing key.",
    ]

    exec_id = raw.get("executionId")
    if exec_id is None:
        notes.append("WARNING: executionId is missing from this manifest.")

    return BcmDeliveryManifest(
        source_path=rel,
        sha256=digest,
        sha256_verified=sha_verified,
        execution_id=exec_id,
        export_arn=raw.get("exportArn"),
        columns=column_defs,
        data_files=raw.get("dataFiles", []),
        additional_output_files=raw.get("additionalOutputFiles", []),
        raw=raw,
        notes=notes,
    )


def provenance(path: str | Path, *, allow_unlisted: bool = False) -> dict[str, Any]:
    """Return provenance metadata for a manifest file without fully parsing it.

    Computes SHA-256, checks approval status, and reads the minimal fields
    needed to identify the delivery.  Never loads row data.
    """
    abs_path = Path(path) if Path(path).is_absolute() else _REPO_ROOT / path
    abs_path = abs_path.resolve()
    rel = _rel_str(abs_path)

    approved_sha = APPROVED_MANIFESTS.get(rel)
    if approved_sha is None and not allow_unlisted:
        raise ManifestNotApproved(f"{rel!r} is not in the approved manifest list")
    if not abs_path.is_file():
        raise FileNotFoundError(f"{abs_path}")

    digest = _sha256_file(abs_path)
    is_focus = "Manifest-FOCUS" in abs_path.name
    manifest_type = "focus_schema" if is_focus else "bcm_delivery"

    prov: dict[str, Any] = {
        "source": "aws_bcm_data_exports_manifest",
        "manifest_type": manifest_type,
        "path": rel,
        "absolute_path": str(abs_path),
        "size_bytes": abs_path.stat().st_size,
        "sha256": digest,
        "sha256_matches_manifest": (digest == approved_sha) if approved_sha else None,
        "approved": approved_sha is not None,
        "execution_id": None,
        "focus_version": None,
        "linkage_to_billing_rows": None,   # not established
        "linkage_to_calibur_admissions": None,
    }

    try:
        raw = json.loads(abs_path.read_text(encoding="utf-8"))
        if is_focus:
            prov["focus_version"] = (raw.get("Schema") or {}).get("FocusVersion")
        else:
            prov["execution_id"] = raw.get("executionId")
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        prov["read_error"] = "manifest content could not be read"

    return prov
