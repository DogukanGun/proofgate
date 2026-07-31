# ProofGate

**An x402 payment gateway that checks whether the payment actually happened.**

Built for the [Hedera x402 bounty](https://hedera.com/x402-bounty).

> ⚠️ **Status: in active development** (started 2026-07-31). See [Current state](#current-state) for exactly what runs today and what does not. Nothing in this README describes behaviour that isn't in the repo.

---

## The problem

Every x402 gateway in production today releases a paid resource because an HTTP service told it to:

```python
settle_response = requests.post(facilitator + "/settle", json=payment)
if settle_response.json()["success"]:
    return the_paid_resource          # <-- that's the entire trust model
```

The `SettleResponse` contains **no proof**. It is an assertion. A facilitator that is malicious, compromised, or merely buggy can:

| Lie | What really happened |
|---|---|
| fabricate | the transaction never existed |
| censor | the transaction failed on-chain; success was claimed |
| short-pay | 5% of the invoice was transferred |
| wrong recipient | funds went somewhere else entirely |
| replay | a real transfer, for a different request |
| double-spend | one real payment, two resources served |

A buggy facilitator and a malicious one produce the same wrong resource grant, and the gateway cannot tell the difference — because it never looks.

## Why this is fixable on Hedera specifically

Verification means asking the chain "did this transfer really happen, for this amount, to this account, bound to this request?" That check has always been *possible* and rarely *economical*: on metered EVM RPC, confirming a sub-cent payment costs more than the payment.

Hedera's mirror nodes expose a **free, keyless, public REST API** over full transaction history. No API key, no credits, no node to run — a plain `GET`. Combined with ~3s deterministic finality and no reorg window, a gateway can independently confirm every settlement before it serves anything, at zero marginal cost.

**ProofGate holds no private key.** It reads the mirror node (keyless) and consensus-node receipts (free). It is a read-only verifier, which is a deliberate contrast to facilitators that custody funds.

## How it verifies

Five predicates, in order, first failure wins. Each one catches an attack that no weaker predicate catches:

| | Predicate | Catches |
|---|---|---|
| **P1** | a record exists on-chain and `result == SUCCESS` | fabricate, censor |
| **P2** | net credit to `payTo` ≥ `maxAmountRequired` | short-pay, wrong recipient |
| **P3** | the transaction memo equals `sha256(canonical(requirements))` | replay for a different request |
| **P4** | consensus timestamp is inside the authorization window | stale/expired receipts |
| **P5** | `(tx_id, memo)` unseen in the spend ledger | double-spend |

The memo binding is the load-bearing trick: the payer writes `base64url(sha256(canonical_json(payment_requirements)))` — unpadded, **exactly 43 characters**, comfortably inside Hedera's 100-byte memo field — into the transfer itself. That is what ties a specific on-chain transaction to a specific HTTP request, and it is why a real transfer for request A cannot buy request B.

### Three outcomes, not two

```
GRANT   serve the resource
FRAUD   refuse, and emit a signed attestation naming the lie
HOLD    refuse, ask the caller to retry — we could not confirm
```

**FRAUD requires positive, offline-replayable evidence**: a chain record (or proof of definitive absence) that contradicts the claim, re-derivable by a third party from the attestation alone. Anything that depends on *our* liveness — a timeout, a lagging mirror, an unresolvable account, a clock we don't trust — is **HOLD**.

Accusing an honest facilitator of fraud is a reputational act with a legal tail. Withholding a resource is a refund. They deserve different bars, and conflating them is how a verification product becomes a liability.

### Independence: mirror × consensus node

Querying one mirror three times is not three observations — a buggy importer agrees with itself perfectly. So the second opinion comes from a **different source class**: the consensus nodes, via a free `TransactionReceiptQuery`. Different codebase, different protocol (gRPC vs REST), different operators.

The consensus node list is **pinned in config, never fetched from the mirror** — otherwise the "independent" path is bootstrapped by the thing it is auditing.

The cross-check may corroborate a FRAUD or demote a verdict to HOLD. It can never upgrade anything to GRANT.

> **On the 2-of-N mirror quorum:** the research this is based on assumes several independent mirror operators. We checked. On testnet, exactly **one** free keyless mirror endpoint exists — Hashio is a JSON-RPC relay (405 on REST), Arkhia and Validation Cloud require API keys. A free operator-diverse mirror quorum is **not currently buildable**, so we did not claim one. Mirror × consensus-node is the honest substitute, and arguably a stronger axis.

## Provenance, and what is actually ours

The design — the predicate ladder, the memo binding, the GRANT/FRAUD/HOLD split — comes from a paper we published: **[Don't Trust the Facilitator (10.5281/zenodo.21704301)](https://zenodo.org/record/21704301)**.

That paper's artifact is a **discrete-event simulation**. Its "mirror nodes" are a Python dictionary; every headline number in it is a numpy draw. It contains no code that reads any chain.

**This repository is that design built against the real network.** None of the paper's quantitative claims are reproduced here, and none should be quoted as if this repo measured them. What we measure, we measure — and where the paper turned out to be wrong about Hedera, we say so:

| Paper | Reality (verified live, 2026-07-31) |
|---|---|
| tx id `0.0.90@1785482751.300000342` | that form returns **HTTP 400**; the mirror needs `0.0.90-1785482751-300000342` |
| mirror responses carry a "head" consensus timestamp | **no such field or header exists**; it comes from `/api/v1/blocks?limit=1&order=desc` |
| 2-of-3 independent mirror quorum | only one free keyless mirror operator exists |
| a byte-identical retry is a double-spend | it is an honest retry, and charging twice for it is a bug |
| no freshness predicate at all | P4 is required; stale-receipt replay is otherwise undetectable |

## Current state

| Component | Status |
|---|---|
| `hedera/timestamps.py` — exact integer consensus timestamps | ✅ done, tested |
| `hedera/ids.py` — account & transaction id normalization | ✅ done, tested |
| `hedera/mirror.py` — mirror node client | 🚧 next |
| `verify/` — the P1–P5 ladder | 🚧 |
| `demo/payer.py` — real testnet settlement | 🚧 |
| gateway API, 402/409 semantics | 🚧 |
| adversarial suite vs a lying facilitator | 🚧 |

**34 tests passing.** Run them:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

### Two encoding bugs worth stealing

Both were caught by tests before they cost an afternoon, and both would present as "the verifier is broken" rather than "the encoding is wrong":

**Nanosecond padding.** `seconds.nanos` is two integer fields, not a decimal fraction — the mirror's own `"1785483102.026000910"` proves it, since a decimal fraction never needs a leading zero. The Hedera SDK emits `f"{seconds}.{nanos}"` *unpadded*, so `.26000910` means 26,000,910 nanos. Read it as a decimal and you shift the timestamp ~234ms and 404 the lookup — for roughly 10% of transactions, nondeterministically.

**Account form.** `0.0.802` and `0x0000000000000000000000000000000000000322` are the same account. A gateway comparing them as strings denies every honest payment with `payto_mismatch`.

## License

MIT
