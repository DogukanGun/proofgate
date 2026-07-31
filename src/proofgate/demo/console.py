"""The demo console: a browser UI that plays the payer AND the adversary.

    .venv/bin/python -m proofgate.demo.console   # http://localhost:8788

Runs as a separate process on a separate port, deliberately: this process
holds the payer key, the gateway does not, and the demo video should show
two different services. Every button here drives real HTTP against the
gateway and (for the attacks that need one) a real HBAR transfer on testnet.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse

from ..config import settings
from ..verify.requirements import PaymentRequirements
from .pay import hashscan, pay
from .payer import present_with_retry, x_payment_header

cfg = settings()
GATEWAY = f"http://localhost:{cfg.proofgate_port}"
app = FastAPI(title="ProofGate demo console")

_here = Path(__file__).parent


def _offer(http: httpx.Client, url: str) -> PaymentRequirements:
    r = http.get(url)
    return PaymentRequirements(**r.json()["accepts"][0])


def _present(http: httpx.Client, url: str, tx_id: str, network: str,
             retry: bool) -> httpx.Response:
    if retry:
        return present_with_retry(http, url, tx_id, network)
    return http.get(url, headers={"X-PAYMENT": x_payment_header(tx_id, network)})


def _verdict_of(r: httpx.Response) -> dict:
    body = r.json()
    v = body.get("verdict") or body.get("attestation") or {}
    return {
        "status": r.status_code,
        "outcome": v.get("outcome", "GRANT" if r.status_code == 200 else "?"),
        "predicate": v.get("predicate", ""),
        "reason": v.get("reason", body.get("error", "")),
        "evidence": v.get("evidence", {}),
        "signature": v.get("signature"),
        "resource": body.get("resource"),
    }


@app.get("/")
def index():
    return FileResponse(_here / "console.html")


@app.get("/api/info")
def info():
    with httpx.Client(timeout=10) as http:
        gw = http.get(f"{GATEWAY}/").json()
        atts = http.get(f"{GATEWAY}/attestations").json()
    return {"gateway": gw, "attestation_count": atts["count"],
            "hashscan_payee": f"https://hashscan.io/testnet/account/{gw['payTo']}"}


@app.get("/api/attestations")
def attestations():
    with httpx.Client(timeout=10) as http:
        return http.get(f"{GATEWAY}/attestations").json()


@app.post("/api/run/{scenario}")
def run(scenario: str, symbol: str = "HBAR"):
    """Execute one scenario end-to-end and narrate every step."""
    url = f"{GATEWAY}/premium/market/{symbol}"
    steps: list[dict] = []

    def step(title: str, detail: str = "", link: str = "", verdict: dict | None = None):
        steps.append({"title": title, "detail": detail, "link": link,
                      "verdict": verdict})

    with httpx.Client(timeout=120) as http:
        reqs = _offer(http, url)
        memo, price, net = reqs.memo(), reqs.amount_tinybar, reqs.network
        step("402 Payment Required",
             f"{price} tinybar to {reqs.payTo}, memo binding {memo}")

        if scenario == "honest":
            tx = pay(reqs.payTo, price, memo)
            step("Paid on Hedera testnet", f"tx {tx}, full price, bound memo",
                 hashscan(tx))
            r = _present(http, url, tx, net, retry=True)
            step("Presented X-PAYMENT", "gateway verified against the mirror",
                 verdict=_verdict_of(r))

        elif scenario == "fabricate":
            tx = f"0.0.999999-{int(time.time()) - 3600}-000000001"
            step("Claimed a settlement that never happened",
                 f"tx {tx} (valid window closed an hour ago)")
            r = _present(http, url, tx, net, retry=False)
            step("Gateway checked the chain", verdict=_verdict_of(r))

        elif scenario == "pending":
            tx = f"0.0.999999-{int(time.time()) - 2}-000000001"
            step("Claimed a settlement the mirror has not seen",
                 f"tx {tx} (valid window still open)")
            r = _present(http, url, tx, net, retry=False)
            step("Gateway refused WITHOUT accusing", verdict=_verdict_of(r))

        elif scenario == "wrong-memo":
            tx = pay(reqs.payTo, price, "hello, this memo buys nothing")
            step("Paid full price with an unbound memo", f"tx {tx}", hashscan(tx))
            r = _present(http, url, tx, net, retry=True)
            step("Gateway compared the memo binding", verdict=_verdict_of(r))

        elif scenario == "short-pay":
            tx = pay(reqs.payTo, max(price // 10, 1), memo)
            step("Paid 10% of the invoice, correct memo", f"tx {tx}", hashscan(tx))
            r = _present(http, url, tx, net, retry=True)
            step("Gateway summed the transfer list", verdict=_verdict_of(r))

        elif scenario == "double-spend":
            tx = pay(reqs.payTo, price, memo)
            step("Paid honestly once", f"tx {tx}", hashscan(tx))
            r1 = _present(http, url, tx, net, retry=True)
            step("First presentation", verdict=_verdict_of(r1))
            r2 = _present(http, url, tx, net, retry=False)
            step("Same payment presented AGAIN", verdict=_verdict_of(r2))

        else:
            return {"error": f"unknown scenario {scenario!r}"}

    return {"scenario": scenario, "steps": steps}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8788)


if __name__ == "__main__":
    main()
