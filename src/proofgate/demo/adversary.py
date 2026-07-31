"""The lying facilitator, played live against a running gateway.

    .venv/bin/python -m proofgate.demo.adversary [--gateway URL]

Five scenarios. Two are free (no transaction exists); three spend real
testnet HBAR to stage the attack on-chain, so every verdict is checkable on
HashScan. Expected outcomes:

    1 fabricate     tx never existed, window closed      -> 409 FRAUD P1
    2 pending       tx unknown, window still open        -> 402 HOLD  P1
    3 wrong memo    real transfer, bound to nothing      -> 409 FRAUD P3
    4 short-pay     real transfer, 10% of the invoice    -> 409 FRAUD P2
    5 double-spend  real honest payment presented twice  -> 200 then 409 FRAUD P5
"""

from __future__ import annotations

import argparse
import json
import time

import httpx

from ..verify.requirements import PaymentRequirements
from .pay import hashscan, pay
from .payer import fetch_offer, present_with_retry, x_payment_header


def present(http: httpx.Client, url: str, tx_id: str, network: str,
            retry: bool = False) -> tuple[int, dict]:
    """retry=True for staged REAL transfers: they must wait out mirror lag,
    exactly like an honest client, before the verdict means anything."""
    if retry:
        r = present_with_retry(http, url, tx_id, network)
    else:
        r = http.get(url, headers={"X-PAYMENT": x_payment_header(tx_id, network)})
    return r.status_code, r.json()


def show(name: str, expected: str, status: int, body: dict, link: str | None = None):
    v = body.get("verdict") or body.get("attestation") or {}
    got = f"{status} {v.get('outcome', '?')} {v.get('predicate', '')}".strip()
    print(f"\n=== {name} ===")
    if link:
        print(f"  on-chain: {link}")
    print(f"  expected {expected}")
    print(f"  got      {got}: {v.get('reason', body.get('error', ''))}")
    if body.get("attestation", {}).get("signature"):
        print(f"  signed attestation sha256={v['signature']['signed_sha256'][:16]}…")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="http://localhost:8787")
    args = ap.parse_args()
    url = f"{args.gateway}/premium/market/FRAUDCOIN"

    with httpx.Client(timeout=30) as http:
        reqs: PaymentRequirements = fetch_offer(http, url)
        net, memo, price = reqs.network, reqs.memo(), reqs.amount_tinybar

        # 1. fabricate: a tx id whose valid window closed long ago
        fake_old = f"0.0.999999-{int(time.time()) - 3600}-000000001"
        s, b = present(http, url, fake_old, net)
        show("1 FABRICATE (tx never existed)", "409 FRAUD P1", s, b)

        # 2. pending: unknown tx, but its window is still open -> no accusation
        fake_new = f"0.0.999999-{int(time.time()) - 2}-000000001"
        s, b = present(http, url, fake_new, net)
        show("2 PENDING (unknown, window open)", "402 HOLD P1", s, b)

        # 3. a REAL transfer, full price, but bound to nothing
        tx = pay(reqs.payTo, price, "hello, this memo buys nothing")
        s, b = present(http, url, tx, net, retry=True)
        show("3 WRONG MEMO (real transfer, unbound)", "409 FRAUD P3", s, b, hashscan(tx))

        # 4. a REAL transfer with the CORRECT memo — for 10% of the invoice
        tx = pay(reqs.payTo, max(price // 10, 1), memo)
        s, b = present(http, url, tx, net, retry=True)
        show("4 SHORT-PAY (10% of invoice)", "409 FRAUD P2", s, b, hashscan(tx))

        # 5. one honest payment, presented twice
        tx = pay(reqs.payTo, price, memo)
        s1, _ = present(http, url, tx, net, retry=True)
        s2, b2 = present(http, url, tx, net)
        show("5 DOUBLE-SPEND (honest pay, twice)",
             f"200 then 409 FRAUD P5 (first was {s1})", s2, b2, hashscan(tx))

        n = http.get(f"{args.gateway}/attestations").json()["count"]
        print(f"\n{n} signed fraud attestations at {args.gateway}/attestations")


if __name__ == "__main__":
    main()
