from datetime import UTC, datetime, timedelta

import pytest

from calibur_engine.tau_eudaci import (
    EarlyUpdateEvent,
    FinalBillEvent,
    TauEudaciError,
    TauEudaciLedger,
)

T0 = datetime(2026, 10, 26, tzinfo=UTC)


def test_warning_is_idempotent_and_final_bill_closes_without_claiming_calibration_update():
    ledger = TauEudaciLedger()
    warning = EarlyUpdateEvent("early-1", "job-1", T0, 100, 101)
    state, added = ledger.record_early(warning)
    assert added and state.pending_final and state.warning_event_ids == ("early-1",)
    assert ledger.record_early(warning)[1] is False
    state, added = ledger.record_final(FinalBillEvent("bill-1", "job-1", T0 + timedelta(days=1), 90))
    assert added and not state.pending_final and state.final_overrun is False


def test_final_bill_revision_replaces_not_duplicates_and_stale_fails():
    ledger = TauEudaciLedger()
    ledger.record_early(EarlyUpdateEvent("early", "job", T0, 100, 120))
    ledger.record_final(FinalBillEvent("bill", "job", T0, 110, 0))
    state, added = ledger.record_final(FinalBillEvent("bill", "job", T0 + timedelta(minutes=1), 120, 1))
    assert added and state.final_revision == 1 and state.final_overrun is True
    with pytest.raises(TauEudaciError, match="stale"):
        ledger.record_final(FinalBillEvent("bill", "job", T0, 110, 0))
