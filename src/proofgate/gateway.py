"""The ProofGate gateway: an x402 resource server that verifies for itself.

HTTP semantics:

    402  no payment presented, or HOLD (unconfirmed — retry is meaningful)
    409  FRAUD (the payment claim contradicts the chain — retry is pointless)
    200  GRANT, resource in the body, receipt in X-PAYMENT-RESPONSE

The gateway holds NO Hedera private key. The only key in this process is an
ephemeral ed25519 attestation key generated at startup, used solely to sign
FRAUD attestations so a third party can tell which gateway emitted them. It
controls no funds.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from hiero_sdk_python import PrivateKey

from .config import settings
from .hedera.mirror import MirrorClient
from .verify import PaymentRequirements, SpendLedger, Verdict, verify

cfg = settings()
app = FastAPI(title="ProofGate", version="0.1.0")

mirror = MirrorClient(cfg.mirror_base)
ledger = SpendLedger()
attestation_key = PrivateKey.generate_ed25519()
attestations: list[dict] = []


def requirements_for(path: str, description: str) -> PaymentRequirements:
    """Deterministic offer for a resource — the client and the gateway must
    derive the identical object, since its hash IS the memo binding."""
    return PaymentRequirements(
        network=cfg.x402_network,
        maxAmountRequired=str(cfg.proofgate_price_tinybar),
        resource=f"{cfg.proofgate_public_url}{path}",
        description=description,
        payTo=cfg.proofgate_pay_to,
        maxTimeoutSeconds=cfg.proofgate_freshness_window_s,
        asset=cfg.proofgate_asset,
    )


def payment_required(reqs: PaymentRequirements, error: str, verdict: Verdict | None = None):
    body: dict = {
        "x402Version": 1,
        "error": error,
        "accepts": [reqs.with_memo_hint().model_dump()],
    }
    if verdict:
        body["verdict"] = verdict.__dict__
    return JSONResponse(body, status_code=402)


def fraud_response(verdict: Verdict) -> JSONResponse:
    """409 + a signed, offline-replayable attestation naming the lie."""
    att = {
        "type": "proofgate.fraud.v1",
        "outcome": verdict.outcome,
        "predicate": verdict.predicate,
        "reason": verdict.reason,
        "evidence": verdict.evidence,
        "network": cfg.x402_network,
    }
    canonical = json.dumps(att, sort_keys=True, separators=(",", ":")).encode()
    att["signature"] = {
        "alg": "ed25519",
        "public_key": attestation_key.public_key().to_string_raw(),
        "sig": attestation_key.sign(canonical).hex(),
        "signed_sha256": hashlib.sha256(canonical).hexdigest(),
    }
    attestations.append(att)
    return JSONResponse({"x402Version": 1, "error": "fraud", "attestation": att},
                        status_code=409)


def decode_x_payment(header: str) -> str:
    """Extract the transaction id from the X-PAYMENT header (b64 json)."""
    payload = json.loads(base64.b64decode(header))
    return str(payload["payload"]["transactionId"])


def gate(request: Request, reqs: PaymentRequirements, resource: dict):
    header = request.headers.get("X-PAYMENT")
    if not header:
        return payment_required(reqs, "payment required")
    try:
        tx_id = decode_x_payment(header)
    except (ValueError, KeyError, json.JSONDecodeError):
        return payment_required(reqs, "malformed X-PAYMENT header")

    verdict = verify(
        tx_id, reqs, mirror, ledger,
        freshness_window_s=cfg.proofgate_freshness_window_s,
        clock_skew_s=cfg.proofgate_clock_skew_s,
    )
    if verdict.outcome == "FRAUD":
        return fraud_response(verdict)
    if verdict.outcome == "HOLD":
        return payment_required(reqs, f"hold: {verdict.reason}", verdict)

    receipt = base64.b64encode(json.dumps({
        "success": True,
        "network": cfg.x402_network,
        "transaction": verdict.evidence["transaction_id"],
        "verified_against": verdict.evidence["mirror_url"],
    }).encode()).decode()
    return JSONResponse(
        {"resource": resource, "verdict": verdict.__dict__},
        headers={"X-PAYMENT-RESPONSE": receipt},
    )


@app.get("/")
def index():
    return {
        "service": "ProofGate",
        "what": "x402 gateway that verifies the facilitator's claim against"
                " Hedera before serving anything",
        "paid_resources": ["/premium/market/{symbol}"],
        "attestations": "/attestations",
        "network": cfg.x402_network,
        "payTo": cfg.proofgate_pay_to,
        "price_tinybar": cfg.proofgate_price_tinybar,
    }


@app.get("/premium/market/{symbol}")
def market(symbol: str, request: Request):
    """Pay-per-query market data — reference architecture #1 of the bounty."""
    symbol = symbol.upper()
    reqs = requirements_for(
        f"/premium/market/{symbol}", f"live market quote for {symbol}"
    )
    # A deterministic pseudo-feed: good enough to demo pay-per-query without
    # a third-party data dependency in the middle of a payment demo.
    seed = int.from_bytes(hashlib.sha256(f"{symbol}:{int(time.time())//30}".encode()).digest()[:4], "big")
    quote = {
        "symbol": symbol,
        "price": round(100 + (seed % 10_000) / 100, 2),
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "proofgate-demo-feed",
    }
    return gate(request, reqs, quote)


@app.get("/attestations")
def list_attestations():
    """Every FRAUD attestation this gateway has emitted, signed."""
    return {"count": len(attestations), "attestations": attestations}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=cfg.proofgate_port)


if __name__ == "__main__":
    main()
