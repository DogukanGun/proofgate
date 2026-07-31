"""The P1–P5 ladder against a scripted mirror. No network, no keys."""

import pytest

from proofgate.hedera.ids import AccountRef
from proofgate.hedera.mirror import MirrorError, MirrorTransaction
from proofgate.hedera.timestamps import ConsensusTime
from proofgate.verify import PaymentRequirements, SpendLedger, verify

PAY_TO = "0.0.5005"
PRICE = 10_000_000
NOW = 1_785_500_000  # fixed chain-time for every test


def reqs() -> PaymentRequirements:
    return PaymentRequirements(
        maxAmountRequired=str(PRICE),
        resource="http://localhost:8787/premium/market/HBAR",
        description="test",
        payTo=PAY_TO,
    )


class FakeMirror:
    base_url = "https://fake.mirror"

    def __init__(self, records=None, head=NOW, error=False):
        self.records = records or []
        self.head = ConsensusTime(head, 0)
        self.error = error

    def get_transaction(self, tx):
        if self.error:
            raise MirrorError("scripted outage")
        return self.records

    def head_time(self):
        if self.error:
            raise MirrorError("scripted outage")
        return self.head


def record(memo=None, amount=PRICE, result="SUCCESS", ts=NOW - 10):
    return MirrorTransaction(
        transaction_id="0.0.111-1785499990-000000000",
        result=result,
        consensus_timestamp=ConsensusTime(ts, 0),
        memo=reqs().memo() if memo is None else memo,
        transfers={AccountRef.require(PAY_TO): amount},
    )


def ledger(tmp_path):
    return SpendLedger(str(tmp_path / "spends.db"))


# transaction id whose valid_start is `ago` seconds before NOW
def tx_id(ago: int) -> str:
    return f"0.0.111-{NOW - ago}-000000000"


def test_grant_when_everything_holds(tmp_path):
    v = verify(tx_id(10), reqs(), FakeMirror([record()]), ledger(tmp_path))
    assert v.outcome == "GRANT"
    assert v.evidence["credited_tinybar"] == PRICE


def test_p1_fabricate_after_window_closes(tmp_path):
    v = verify(tx_id(3600), reqs(), FakeMirror([]), ledger(tmp_path))
    assert (v.outcome, v.predicate) == ("FRAUD", "P1")
    assert "never" in v.reason


def test_p1_hold_while_window_open(tmp_path):
    v = verify(tx_id(5), reqs(), FakeMirror([]), ledger(tmp_path))
    assert (v.outcome, v.predicate) == ("HOLD", "P1")


def test_p1_censored_failure(tmp_path):
    rec = record(result="INSUFFICIENT_PAYER_BALANCE")
    v = verify(tx_id(10), reqs(), FakeMirror([rec]), ledger(tmp_path))
    assert (v.outcome, v.predicate) == ("FRAUD", "P1")
    assert "INSUFFICIENT_PAYER_BALANCE" in v.reason


def test_p2_short_pay(tmp_path):
    v = verify(tx_id(10), reqs(), FakeMirror([record(amount=PRICE // 10)]),
               ledger(tmp_path))
    assert (v.outcome, v.predicate) == ("FRAUD", "P2")
    assert v.evidence["credited_tinybar"] == PRICE // 10


def test_p2_wrong_recipient(tmp_path):
    rec = MirrorTransaction(
        transaction_id="x", result="SUCCESS",
        consensus_timestamp=ConsensusTime(NOW - 10, 0),
        memo=reqs().memo(),
        transfers={AccountRef.require("0.0.666"): PRICE},
    )
    v = verify(tx_id(10), reqs(), FakeMirror([rec]), ledger(tmp_path))
    assert (v.outcome, v.predicate) == ("FRAUD", "P2")
    assert "wrong recipient" in v.reason


def test_p3_memo_mismatch_is_replay(tmp_path):
    v = verify(tx_id(10), reqs(), FakeMirror([record(memo="pizza")]),
               ledger(tmp_path))
    assert (v.outcome, v.predicate) == ("FRAUD", "P3")


def test_p4_stale_receipt_is_hold_not_fraud(tmp_path):
    v = verify(tx_id(500), reqs(), FakeMirror([record(ts=NOW - 500)]),
               ledger(tmp_path))
    assert (v.outcome, v.predicate) == ("HOLD", "P4")


def test_p5_double_spend(tmp_path):
    led = ledger(tmp_path)
    mirror = FakeMirror([record()])
    assert verify(tx_id(10), reqs(), mirror, led).outcome == "GRANT"
    v = verify(tx_id(10), reqs(), mirror, led)
    assert (v.outcome, v.predicate) == ("FRAUD", "P5")


def test_mirror_outage_is_hold_never_fraud(tmp_path):
    v = verify(tx_id(10), reqs(), FakeMirror(error=True), ledger(tmp_path))
    assert (v.outcome, v.predicate) == ("HOLD", "transport")


def test_memo_is_43_chars_and_stable():
    r = reqs()
    assert len(r.memo()) == 43
    assert r.memo() == r.memo()
    # the hint copy hashes identically: `extra` is excluded from canonical
    assert r.with_memo_hint().memo() == r.memo()
    # any bound field changes the binding
    assert r.model_copy(update={"resource": "http://other"}).memo() != r.memo()


def test_amount_must_be_net_credit_not_gross(tmp_path):
    """A transfer list crediting and debiting payTo nets out — P2 must fail."""
    ref = AccountRef.require(PAY_TO)
    rec = MirrorTransaction(
        transaction_id="x", result="SUCCESS",
        consensus_timestamp=ConsensusTime(NOW - 10, 0),
        memo=reqs().memo(), transfers={ref: PRICE - 1},
    )
    v = verify(tx_id(10), reqs(), FakeMirror([rec]), ledger(tmp_path))
    assert (v.outcome, v.predicate) == ("FRAUD", "P2")
