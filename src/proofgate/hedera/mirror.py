"""Mirror node REST client — the free, keyless read path.

Two endpoints only:

    GET /api/v1/transactions/{0.0.x-sec-nanos}   the settlement record
    GET /api/v1/blocks?limit=1&order=desc        chain-time "now"

The second one matters because the verifier must not trust its own clock for
freshness (P4) or for declaring definitive absence: both are judged against
the mirror's OWN notion of now, so a skewed local clock can't turn a pending
transaction into a "proven absent" one.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

import httpx

from .ids import AccountRef, TxRef
from .timestamps import ConsensusTime


class MirrorError(RuntimeError):
    """Transport-level failure: timeouts, 5xx, unparseable bodies.

    Callers must map this to HOLD, never to FRAUD — our inability to read
    the mirror is not evidence about the facilitator.
    """


@dataclass(frozen=True)
class MirrorTransaction:
    """One row from the mirror's transactions array, decoded."""

    transaction_id: str
    result: str
    consensus_timestamp: ConsensusTime
    memo: str  # decoded utf-8, "" if unset or undecodable
    transfers: dict[AccountRef, int] = field(default_factory=dict)  # net tinybar
    charged_tx_fee: int = 0

    @property
    def success(self) -> bool:
        return self.result == "SUCCESS"

    def net_credit(self, account: AccountRef) -> int:
        return self.transfers.get(account, 0)


def _decode(raw: dict) -> MirrorTransaction:
    memo = ""
    if raw.get("memo_base64"):
        try:
            memo = base64.b64decode(raw["memo_base64"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            memo = ""  # an undecodable memo can never equal a b64url hash
    transfers: dict[AccountRef, int] = {}
    for t in raw.get("transfers") or []:
        ref = AccountRef.parse_offline(t.get("account"))
        if ref is not None:
            transfers[ref] = transfers.get(ref, 0) + int(t.get("amount", 0))
    return MirrorTransaction(
        transaction_id=raw.get("transaction_id", ""),
        result=raw.get("result", ""),
        consensus_timestamp=ConsensusTime.parse(raw["consensus_timestamp"]),
        memo=memo,
        transfers=transfers,
        charged_tx_fee=int(raw.get("charged_tx_fee", 0)),
    )


class MirrorClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, **params) -> httpx.Response:
        try:
            return self._http.get(self.base_url + path, params=params or None)
        except httpx.HTTPError as exc:
            raise MirrorError(f"mirror unreachable: {exc}") from exc

    def head_time(self) -> ConsensusTime:
        """Chain-time now: the newest block's closing consensus timestamp."""
        r = self._get("/api/v1/blocks", limit=1, order="desc")
        if r.status_code != 200:
            raise MirrorError(f"blocks endpoint returned {r.status_code}")
        try:
            return ConsensusTime.parse(r.json()["blocks"][0]["timestamp"]["to"])
        except (KeyError, IndexError, ValueError) as exc:
            raise MirrorError(f"malformed blocks response: {exc}") from exc

    def get_transaction(self, tx: TxRef | str) -> list[MirrorTransaction]:
        """All records for a transaction id (parent + children + duplicates).

        [] means the mirror has no record — which is NOT yet proof of
        absence; that judgement needs head_time() and the valid window.
        """
        ref = TxRef.parse(tx)
        r = self._get(f"/api/v1/transactions/{ref.to_mirror()}")
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            raise MirrorError(f"transactions endpoint returned {r.status_code}")
        try:
            return [_decode(row) for row in r.json().get("transactions", [])]
        except (KeyError, ValueError) as exc:
            raise MirrorError(f"malformed transaction response: {exc}") from exc
