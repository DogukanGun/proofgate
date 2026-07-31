"""The P1–P5 verification ladder. First failure wins.

    P1  a record exists on-chain and result == SUCCESS     fabricate, censor
    P2  net credit to payTo >= maxAmountRequired            short-pay, wrong recipient
    P3  memo == b64url(sha256(canonical(requirements)))     replay for another request
    P4  consensus timestamp inside the freshness window     stale receipts
    P5  (tx_id, memo) unseen in the spend ledger            double-spend

Three outcomes, not two. FRAUD requires positive, offline-replayable evidence
— every FRAUD verdict carries the mirror URL and the exact observed values a
third party needs to re-derive it. Anything that depends on OUR liveness (a
timeout, a lagging mirror) is HOLD. P4 staleness is also HOLD, not FRAUD: an
old-but-real payment proves a slow client, not a lying facilitator.

Chain time, not wall time: absence and freshness are judged against the
mirror's own head timestamp, so a skewed local clock cannot manufacture a
verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..hedera.ids import AccountParseError, AccountRef, TxParseError, TxRef
from ..hedera.mirror import MirrorClient, MirrorError, MirrorTransaction
from .ledger import SpendLedger
from .requirements import PaymentRequirements

# A Hedera transaction's valid window is at most 180s after valid_start. If
# the mirror's head has moved this far past the window's end and there is
# still no record, the transaction can never appear: absence is definitive.
MAX_VALID_DURATION_S = 180
ABSENCE_MARGIN_S = 30


@dataclass(frozen=True)
class Verdict:
    outcome: Literal["GRANT", "FRAUD", "HOLD"]
    predicate: str  # "P1".."P5", or "input"/"transport"
    reason: str
    evidence: dict = field(default_factory=dict)

    @property
    def granted(self) -> bool:
        return self.outcome == "GRANT"


def verify(
    tx: str | TxRef,
    requirements: PaymentRequirements,
    mirror: MirrorClient,
    ledger: SpendLedger,
    freshness_window_s: int = 180,
    clock_skew_s: int = 5,
) -> Verdict:
    # ---- inputs. Malformed input is the caller's problem, not fraud.
    try:
        tx_ref = TxRef.parse(tx)
        pay_to = AccountRef.require(requirements.payTo)
    except (TxParseError, AccountParseError) as exc:
        return Verdict("HOLD", "input", str(exc))

    expected_memo = requirements.memo()
    mirror_url = f"{mirror.base_url}/api/v1/transactions/{tx_ref.to_mirror()}"
    base_evidence = {"transaction_id": tx_ref.to_mirror(), "mirror_url": mirror_url}

    # ---- read the chain. Transport failure is HOLD, never FRAUD.
    try:
        records = mirror.get_transaction(tx_ref)
        head = mirror.head_time()
    except MirrorError as exc:
        return Verdict("HOLD", "transport", f"mirror unavailable: {exc}")

    # ---- P1: the record exists and succeeded
    if not records:
        window_end = tx_ref.valid_start.plus_seconds(
            MAX_VALID_DURATION_S + ABSENCE_MARGIN_S
        )
        if head.total_nanos > window_end.total_nanos:
            return Verdict(
                "FRAUD",
                "P1",
                "fabricated: no record exists and the valid window has closed"
                " — this transaction can never reach consensus",
                {**base_evidence, "mirror_head": str(head),
                 "valid_window_closed_at": str(window_end)},
            )
        return Verdict(
            "HOLD", "P1",
            "no record yet, valid window still open — mirror may lag; retry",
            {**base_evidence, "mirror_head": str(head)},
        )

    record = _pick(records)
    base_evidence["consensus_timestamp"] = str(record.consensus_timestamp)

    if not record.success:
        return Verdict(
            "FRAUD", "P1",
            f"censored failure: transaction reached consensus with result"
            f" {record.result!r}, yet settlement success was claimed",
            {**base_evidence, "result": record.result},
        )

    # ---- P2: the money actually arrived, in full, at the right account
    credited = record.net_credit(pay_to)
    if credited < requirements.amount_tinybar:
        reason = (
            "wrong recipient: payTo received nothing"
            if credited == 0
            else f"short-pay: {credited} of {requirements.amount_tinybar} tinybar"
        )
        return Verdict(
            "FRAUD", "P2", reason,
            {**base_evidence, "pay_to": pay_to.canonical(),
             "credited_tinybar": credited,
             "required_tinybar": requirements.amount_tinybar},
        )

    # ---- P3: the transfer is bound to THIS request
    if record.memo != expected_memo:
        return Verdict(
            "FRAUD", "P3",
            "memo mismatch: a real transfer, but bound to a different request"
            " (or to none) — replay",
            {**base_evidence, "expected_memo": expected_memo,
             "observed_memo": record.memo},
        )

    # ---- P4: freshness, in chain time
    age = record.consensus_timestamp.age_seconds(now=head)
    if age > freshness_window_s or age < -clock_skew_s:
        return Verdict(
            "HOLD", "P4",
            f"stale receipt: settled {age:.0f}s ago, window is"
            f" {freshness_window_s}s — refusing without accusation",
            {**base_evidence, "age_seconds": round(age, 3),
             "freshness_window_s": freshness_window_s},
        )

    # ---- P5: one payment, one grant. The INSERT is the predicate.
    if not ledger.claim(tx_ref.to_mirror(), expected_memo):
        return Verdict(
            "FRAUD", "P5",
            "double-spend: this exact (transaction, request) pair already"
            " bought a resource serve",
            {**base_evidence, "memo": expected_memo},
        )

    return Verdict(
        "GRANT", "P5", "all predicates hold",
        {**base_evidence, "credited_tinybar": credited, "memo": expected_memo,
         "age_seconds": round(age, 3)},
    )


def _pick(records: list[MirrorTransaction]) -> MirrorTransaction:
    """Parent + children + duplicates can share an id; judge the best row.

    Prefer a SUCCESS row — if any node executed it successfully, that is the
    settlement. Failing that, judge the first (parent) row's failure code.
    """
    for r in records:
        if r.success:
            return r
    return records[0]
