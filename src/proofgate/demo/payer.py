"""The honest x402 client: hit the paywall, pay on-chain, present the proof.

    .venv/bin/python -m proofgate.demo.payer [--symbol HBAR] [--gateway URL]

This is the complete happy path of the protocol against the real testnet:
402 -> real HBAR transfer with the memo binding -> 200 + verified resource.
"""

from __future__ import annotations

import argparse
import base64
import json
import time

import httpx

from ..verify.requirements import PaymentRequirements
from .pay import hashscan, pay


def x_payment_header(tx_id: str, network: str) -> str:
    return base64.b64encode(json.dumps({
        "x402Version": 1,
        "scheme": "exact",
        "network": network,
        "payload": {"transactionId": tx_id},
    }).encode()).decode()


def fetch_offer(client: httpx.Client, url: str) -> PaymentRequirements:
    r = client.get(url)
    if r.status_code != 402:
        raise SystemExit(f"expected 402 from {url}, got {r.status_code}")
    offer = r.json()["accepts"][0]
    reqs = PaymentRequirements(**offer)
    # Never trust the server's memo hint blindly — recompute the binding.
    if offer.get("extra", {}).get("memo") != reqs.memo():
        raise SystemExit("server memo hint does not match canonical hash")
    return reqs


def present_with_retry(
    http: httpx.Client, url: str, tx_id: str, network: str, attempts: int = 10
) -> httpx.Response:
    """HOLD means exactly one thing: retry. The mirror lags consensus by a
    few seconds, so an honest client polls until the record lands. FRAUD
    (409) and GRANT (200) both stop the loop — retrying either is pointless."""
    headers = {"X-PAYMENT": x_payment_header(tx_id, network)}
    for _ in range(attempts):
        r = http.get(url, headers=headers)
        if r.status_code != 402 or r.json().get("verdict", {}).get("outcome") != "HOLD":
            return r
        print(f"  … HOLD ({r.json()['verdict']['reason']}), retrying in 2s")
        time.sleep(2)
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="HBAR")
    ap.add_argument("--gateway", default="http://localhost:8787")
    args = ap.parse_args()
    url = f"{args.gateway}/premium/market/{args.symbol}"

    with httpx.Client(timeout=30) as http:
        print(f"→ GET {url} (no payment)")
        reqs = fetch_offer(http, url)
        price = reqs.amount_tinybar
        print(f"← 402: {price} tinybar to {reqs.payTo}, memo binding {reqs.memo()}")

        print("→ paying on Hedera testnet …")
        tx_id = pay(reqs.payTo, price, reqs.memo())
        print(f"← settled: {tx_id}")
        print(f"  {hashscan(tx_id)}")

        print("→ re-requesting with X-PAYMENT …")
        r = present_with_retry(http, url, tx_id, reqs.network)
        print(f"← {r.status_code}")
        print(json.dumps(r.json(), indent=2))
        if r.status_code == 200:
            receipt = json.loads(base64.b64decode(r.headers["X-PAYMENT-RESPONSE"]))
            print("X-PAYMENT-RESPONSE:", json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
