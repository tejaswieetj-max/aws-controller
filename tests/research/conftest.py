"""Shared fixtures for research tests.

The primary purpose of this conftest is the ``no_checksum_enforcement`` fixture,
which nullifies the APPROVED_FILES SHA-256 values for the duration of any test
that uses ``tmp_path``-based Alibaba CSV fixtures.

Background: ``alibaba_loader.APPROVED_FILES`` was updated to contain real per-file
SHA-256 checksums once the real extracted files were measured (2026-10-25).
Existing tests in ``test_loaders.py`` write small synthetic CSV fixtures into
``tmp_path`` and call ``load_file(filename, raw_dir=tmp_path)``.  Those fixtures
have different checksums than the real files, so ``load_file`` raises
``ChecksumMismatch`` unless the expected checksum is either overridden via
``expected_sha256=`` or set to ``None`` in ``APPROVED_FILES``.

The fixture below temporarily sets all ``APPROVED_FILES`` entries to ``None``
(meaning "approved filename, checksum not enforced") for the test's duration,
then restores the real values.  Tests that want to verify checksum behaviour
pass an explicit ``expected_sha256=`` argument to ``load_file`` (see
``TestAlibabaChecksum``); those tests are unaffected because they already supply
the hash explicitly.

Tests that deliberately test with real checksums (``TestAlibabaChecksumsFilled``)
do not receive ``tmp_path`` and are therefore unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RESEARCH_DIR = Path(__file__).resolve().parents[2] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))


@pytest.fixture(autouse=True)
def no_checksum_enforcement(request):
    """Disable APPROVED_FILES checksum enforcement for tmp_path fixture tests.

    Only applies when the test function receives the ``tmp_path`` fixture
    (i.e., when it creates synthetic fixture files).  Restores original values
    after the test.  Never touches the real data files.
    """
    # Only patch if the test actually uses tmp_path.
    if "tmp_path" not in request.fixturenames:
        yield
        return

    from ingestion import alibaba_loader

    # Save and null out checksums so tmp_path fixtures don't hit ChecksumMismatch.
    saved = dict(alibaba_loader.APPROVED_FILES)
    for key in alibaba_loader.APPROVED_FILES:
        alibaba_loader.APPROVED_FILES[key] = None
    try:
        yield
    finally:
        # Restore real checksums.
        for key, value in saved.items():
            alibaba_loader.APPROVED_FILES[key] = value
