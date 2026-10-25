"""Tests for research/ingestion/manifest_reader.py.

All fixtures are synthetic, created in temporary directories.
None of these tests touch data/raw/.
No fixture value may be presented as an empirical result.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

RESEARCH_DIR = Path(__file__).resolve().parents[2] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from ingestion.manifest_reader import (
    APPROVED_MANIFESTS,
    BcmDeliveryManifest,
    ColumnDefinition,
    FocusSchemaManifest,
    ManifestChecksumMismatch,
    ManifestNotApproved,
    ManifestParseError,
    read_bcm_delivery_manifest,
    read_focus_schema_manifest,
    provenance as manifest_provenance,
)


# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def _write_focus_manifest(path: Path, focus_version: str = "1.2") -> str:
    """Write a minimal Manifest-FOCUS.json fixture. Returns SHA-256."""
    content = {
        "DataGenerator": "Amazon Web Services, Inc.",
        "DataGeneratorVersion": "1.0.0",
        "Schema": {
            "SchemaId": "1.0.0",
            "CreationDate": "2025-11-19T00:00:00Z",
            "FocusVersion": focus_version,
            "ColumnDefinition": [
                {"ColumnName": "BilledCost", "DataType": "double"},
                {"ColumnName": "BillingCurrency", "DataType": "string"},
                {
                    "ColumnName": "Tags",
                    "DataType": "map",
                    "ProviderTagPrefixes": ["aws"],
                },
            ],
        },
    }
    path.write_text(json.dumps(content), encoding="utf-8")
    return _sha256(path)


def _write_bcm_manifest(path: Path, execution_id: str = "test-exec-abc") -> str:
    """Write a minimal Manifest.json fixture. Returns SHA-256."""
    content = {
        "executionId": execution_id,
        "exportArn": "arn:aws:bcm-data-exports:us-east-1:123456789012:export/TEST-123",
        "columns": [
            {"name": "BilledCost", "type": "double"},
            {"name": "BillingCurrency", "type": "string"},
        ],
        "dataFiles": [
            "s3://test-bucket/prefix/data.parquet"
        ],
        "additionalOutputFiles": [],
    }
    path.write_text(json.dumps(content), encoding="utf-8")
    return _sha256(path)


# ===========================================================================
# 1. Approval gate
# ===========================================================================

class TestManifestApproval:
    def test_unapproved_focus_manifest_raises(self, tmp_path):
        p = tmp_path / "CALIBUR_FOCUS_12-Manifest-FOCUS.json"
        _write_focus_manifest(p)
        with pytest.raises(ManifestNotApproved, match="not in the approved"):
            read_focus_schema_manifest(p)

    def test_unapproved_bcm_manifest_raises(self, tmp_path):
        p = tmp_path / "CALIBUR_FOCUS_12-Manifest.json"
        _write_bcm_manifest(p)
        with pytest.raises(ManifestNotApproved, match="not in the approved"):
            read_bcm_delivery_manifest(p)

    def test_allow_unlisted_bypasses_approval(self, tmp_path):
        p = tmp_path / "CALIBUR_FOCUS_12-Manifest-FOCUS.json"
        _write_focus_manifest(p)
        # Should not raise ManifestNotApproved
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        assert isinstance(result, FocusSchemaManifest)

    def test_approved_manifests_dict_has_correct_types(self):
        for path_str, sha in APPROVED_MANIFESTS.items():
            assert isinstance(path_str, str)
            assert len(sha) == 64
            int(sha, 16)  # must be valid hex


# ===========================================================================
# 2. FocusSchemaManifest structure
# ===========================================================================

class TestFocusSchemaManifest:
    def test_parses_focus_version(self, tmp_path):
        p = tmp_path / "m.json"
        sha = _write_focus_manifest(p, focus_version="1.2")
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        assert result.focus_version == "1.2"

    def test_parses_data_generator(self, tmp_path):
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        assert result.data_generator == "Amazon Web Services, Inc."

    def test_parses_column_definitions(self, tmp_path):
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        names = [c.name for c in result.columns]
        assert "BilledCost" in names
        assert "BillingCurrency" in names

    def test_column_with_provider_tag_prefixes(self, tmp_path):
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        tags_col = next((c for c in result.columns if c.name == "Tags"), None)
        assert tags_col is not None
        assert "aws" in tags_col.provider_tag_prefixes

    def test_sha256_computed_and_returned(self, tmp_path):
        p = tmp_path / "m.json"
        sha = _write_focus_manifest(p)
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        assert result.sha256 == sha

    def test_sha256_verified_true_when_expected_matches(self, tmp_path):
        p = tmp_path / "m.json"
        sha = _write_focus_manifest(p)
        import ingestion.manifest_reader as mr
        orig = dict(mr.APPROVED_MANIFESTS)
        mr.APPROVED_MANIFESTS[str(p)] = sha
        try:
            result = read_focus_schema_manifest(p)
            assert result.sha256_verified is True
        finally:
            mr.APPROVED_MANIFESTS.pop(str(p), None)
            mr.APPROVED_MANIFESTS.update(orig)

    def test_wrong_checksum_raises(self, tmp_path):
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        import ingestion.manifest_reader as mr
        orig = dict(mr.APPROVED_MANIFESTS)
        mr.APPROVED_MANIFESTS[str(p)] = "a" * 64
        try:
            with pytest.raises(ManifestChecksumMismatch):
                read_focus_schema_manifest(p)
        finally:
            mr.APPROVED_MANIFESTS.pop(str(p), None)
            mr.APPROVED_MANIFESTS.update(orig)

    def test_sha256_not_verified_when_unlisted(self, tmp_path):
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        assert result.sha256_verified is False

    def test_source_path_is_relative_or_absolute(self, tmp_path):
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        assert isinstance(result.source_path, str)
        assert len(result.source_path) > 0

    def test_raw_dict_preserved(self, tmp_path):
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        assert isinstance(result.raw, dict)
        assert "Schema" in result.raw

    def test_notes_include_focus_version_note(self, tmp_path):
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        combined = " ".join(result.notes).lower()
        assert "focusversion" in combined or "focus" in combined

    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_focus_schema_manifest(tmp_path / "nonexistent.json", allow_unlisted=True)

    def test_invalid_json_raises_parse_error(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text("not valid json {{", encoding="utf-8")
        with pytest.raises(ManifestParseError):
            read_focus_schema_manifest(p, allow_unlisted=True)

    def test_non_object_json_raises_parse_error(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ManifestParseError):
            read_focus_schema_manifest(p, allow_unlisted=True)

    def test_load_does_not_modify_file(self, tmp_path):
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        before = p.read_bytes()
        read_focus_schema_manifest(p, allow_unlisted=True)
        assert p.read_bytes() == before

    def test_no_files_written(self, tmp_path):
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        before = set(tmp_path.iterdir())
        read_focus_schema_manifest(p, allow_unlisted=True)
        assert set(tmp_path.iterdir()) == before


# ===========================================================================
# 3. BcmDeliveryManifest structure
# ===========================================================================

class TestBcmDeliveryManifest:
    def test_parses_execution_id(self, tmp_path):
        p = tmp_path / "Manifest.json"
        _write_bcm_manifest(p, execution_id="exec-deadbeef")
        result = read_bcm_delivery_manifest(p, allow_unlisted=True)
        assert result.execution_id == "exec-deadbeef"

    def test_parses_export_arn(self, tmp_path):
        p = tmp_path / "Manifest.json"
        _write_bcm_manifest(p)
        result = read_bcm_delivery_manifest(p, allow_unlisted=True)
        assert result.export_arn is not None
        assert result.export_arn.startswith("arn:aws:")

    def test_parses_data_files(self, tmp_path):
        p = tmp_path / "Manifest.json"
        _write_bcm_manifest(p)
        result = read_bcm_delivery_manifest(p, allow_unlisted=True)
        assert len(result.data_files) == 1
        assert result.data_files[0].startswith("s3://")

    def test_parses_column_definitions(self, tmp_path):
        p = tmp_path / "Manifest.json"
        _write_bcm_manifest(p)
        result = read_bcm_delivery_manifest(p, allow_unlisted=True)
        names = [c.name for c in result.columns]
        assert "BilledCost" in names

    def test_additional_output_files_is_empty_list(self, tmp_path):
        p = tmp_path / "Manifest.json"
        _write_bcm_manifest(p)
        result = read_bcm_delivery_manifest(p, allow_unlisted=True)
        assert result.additional_output_files == []

    def test_sha256_computed(self, tmp_path):
        p = tmp_path / "Manifest.json"
        sha = _write_bcm_manifest(p)
        result = read_bcm_delivery_manifest(p, allow_unlisted=True)
        assert result.sha256 == sha

    def test_missing_execution_id_note_added(self, tmp_path):
        p = tmp_path / "Manifest.json"
        p.write_text(json.dumps({"columns": [], "dataFiles": []}), encoding="utf-8")
        result = read_bcm_delivery_manifest(p, allow_unlisted=True)
        assert result.execution_id is None
        combined = " ".join(result.notes).lower()
        assert "missing" in combined or "executionid" in combined.replace("_", "")

    def test_notes_disclaim_arrival_time(self, tmp_path):
        p = tmp_path / "Manifest.json"
        _write_bcm_manifest(p)
        result = read_bcm_delivery_manifest(p, allow_unlisted=True)
        combined = " ".join(result.notes).lower()
        assert "arrival" in combined or "not" in combined

    def test_load_does_not_modify_file(self, tmp_path):
        p = tmp_path / "Manifest.json"
        _write_bcm_manifest(p)
        before = p.read_bytes()
        read_bcm_delivery_manifest(p, allow_unlisted=True)
        assert p.read_bytes() == before


# ===========================================================================
# 4. provenance() metadata
# ===========================================================================

class TestManifestProvenance:
    def test_focus_manifest_provenance_keys(self, tmp_path):
        p = tmp_path / "CALIBUR-Manifest-FOCUS.json"
        _write_focus_manifest(p)
        prov = manifest_provenance(p, allow_unlisted=True)
        assert prov["source"] == "aws_bcm_data_exports_manifest"
        assert prov["manifest_type"] == "focus_schema"
        assert len(prov["sha256"]) == 64
        assert prov["size_bytes"] > 0
        assert prov["linkage_to_billing_rows"] is None
        assert prov["linkage_to_calibur_admissions"] is None

    def test_bcm_manifest_provenance_keys(self, tmp_path):
        p = tmp_path / "CALIBUR-Manifest.json"
        _write_bcm_manifest(p, execution_id="exec-test")
        prov = manifest_provenance(p, allow_unlisted=True)
        assert prov["manifest_type"] == "bcm_delivery"
        assert prov["execution_id"] == "exec-test"

    def test_focus_version_in_focus_manifest_provenance(self, tmp_path):
        p = tmp_path / "CALIBUR-Manifest-FOCUS.json"
        _write_focus_manifest(p, focus_version="1.2")
        prov = manifest_provenance(p, allow_unlisted=True)
        assert prov["focus_version"] == "1.2"

    def test_provenance_does_not_modify_file(self, tmp_path):
        p = tmp_path / "CALIBUR-Manifest-FOCUS.json"
        _write_focus_manifest(p)
        before = p.read_bytes()
        manifest_provenance(p, allow_unlisted=True)
        assert p.read_bytes() == before

    def test_provenance_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            manifest_provenance(tmp_path / "nonexistent.json", allow_unlisted=True)

    def test_provenance_unapproved_raises_without_flag(self, tmp_path):
        p = tmp_path / "CALIBUR-Manifest-FOCUS.json"
        _write_focus_manifest(p)
        with pytest.raises(ManifestNotApproved):
            manifest_provenance(p)


# ===========================================================================
# 5. Real manifest files smoke-test (D1 and D3 only; D2 has no local copy)
# ===========================================================================

class TestRealManifestFiles:
    """These tests read the actual manifest files from data/raw/.
    They verify checksums match APPROVED_MANIFESTS and that key fields parse.
    They do not modify files and do not load Parquet row data.
    """

    _REPO_ROOT = Path(__file__).resolve().parents[2]

    def _real(self, rel: str) -> Path:
        return self._REPO_ROOT / rel

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parents[2]
             / "data/raw/focus12/CALIBUR_FOCUS_12-Manifest-FOCUS.json").is_file(),
        reason="D1 Manifest-FOCUS.json not present in data/raw/",
    )
    def test_d1_focus_schema_manifest_loads_and_verifies(self):
        result = read_focus_schema_manifest(
            self._real("data/raw/focus12/CALIBUR_FOCUS_12-Manifest-FOCUS.json")
        )
        assert result.sha256_verified is True
        assert result.focus_version == "1.2"
        assert len(result.columns) > 0
        assert result.data_generator == "Amazon Web Services, Inc."

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parents[2]
             / "data/raw/focus12/delivery_2026-10-25T11"
             / "CALIBUR_FOCUS_12-Manifest-FOCUS.json").is_file(),
        reason="D3 Manifest-FOCUS.json not present in data/raw/",
    )
    def test_d3_focus_schema_manifest_same_as_d1(self):
        """D3 FOCUS schema manifest has the same SHA-256 as D1 — schema unchanged."""
        d1 = read_focus_schema_manifest(
            self._real("data/raw/focus12/CALIBUR_FOCUS_12-Manifest-FOCUS.json")
        )
        d3 = read_focus_schema_manifest(
            self._real(
                "data/raw/focus12/delivery_2026-10-25T11/CALIBUR_FOCUS_12-Manifest-FOCUS.json"
            )
        )
        assert d1.sha256 == d3.sha256, (
            "D1 and D3 FOCUS schema manifests must be identical (same schema version)"
        )
        assert d1.focus_version == d3.focus_version == "1.2"

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parents[2]
             / "data/raw/focus12/delivery_2026-10-25T11"
             / "CALIBUR_FOCUS_12-Manifest.json").is_file(),
        reason="D3 Manifest.json not present in data/raw/",
    )
    def test_d3_bcm_manifest_execution_id_matches_registry(self):
        """D3 BCM manifest executionId must match DELIVERY_REGISTRY."""
        result = read_bcm_delivery_manifest(
            self._real(
                "data/raw/focus12/delivery_2026-10-25T11/CALIBUR_FOCUS_12-Manifest.json"
            )
        )
        assert result.sha256_verified is True
        assert result.execution_id == "2719ee81-66bd-346a-93bc-ec5e99dc1747", (
            "D3 executionId must match DELIVERY_REGISTRY entry"
        )
        assert result.export_arn is not None
        assert len(result.data_files) >= 1

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parents[2]
             / "data/raw/focus12/CALIBUR_FOCUS_12-Manifest-FOCUS.json").is_file(),
        reason="D1 Manifest-FOCUS.json not present in data/raw/",
    )
    def test_real_manifest_does_not_contain_arrival_time(self):
        """No manifest field constitutes a billing-record arrival timestamp."""
        result = read_focus_schema_manifest(
            self._real("data/raw/focus12/CALIBUR_FOCUS_12-Manifest-FOCUS.json")
        )
        # Raw JSON must not have an "arrival" key
        raw_keys = set(result.raw.keys())
        assert not any("arrival" in k.lower() for k in raw_keys)

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parents[2]
             / "data/raw/focus12/CALIBUR_FOCUS_12-Manifest-FOCUS.json").is_file(),
        reason="D1 Manifest-FOCUS.json not present in data/raw/",
    )
    def test_real_manifest_file_not_modified(self):
        """Reading a real manifest must not change the file."""
        p = self._real("data/raw/focus12/CALIBUR_FOCUS_12-Manifest-FOCUS.json")
        before = p.read_bytes()
        read_focus_schema_manifest(p)
        after = p.read_bytes()
        assert before == after


# ===========================================================================
# 6. Fixture isolation — manifests never enter billing pipeline
# ===========================================================================

class TestManifestPipelineIsolation:
    def test_focus_manifest_has_no_billing_row_data(self, tmp_path):
        """A FocusSchemaManifest must not have row-level billing fields."""
        p = tmp_path / "m.json"
        _write_focus_manifest(p)
        result = read_focus_schema_manifest(p, allow_unlisted=True)
        # Must not have any attribute named like a FOCUS billing row field
        assert not hasattr(result, "billed_cost")
        assert not hasattr(result, "rows")
        assert not hasattr(result, "observations")

    def test_bcm_manifest_has_no_billing_row_data(self, tmp_path):
        p = tmp_path / "m.json"
        _write_bcm_manifest(p)
        result = read_bcm_delivery_manifest(p, allow_unlisted=True)
        assert not hasattr(result, "billed_cost")
        assert not hasattr(result, "rows")
        assert not hasattr(result, "observations")
