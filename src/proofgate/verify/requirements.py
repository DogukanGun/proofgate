"""x402 PaymentRequirements and the memo binding.

The binding: the payer writes

    base64url( sha256( canonical_json(requirements) ) )   # unpadded, 43 chars

into the Hedera transfer's memo. `canonical_json` is JSON with sorted keys,
no whitespace, over every requirements field EXCEPT `extra` — `extra` is
where we hand the client the expected memo itself, so including it would be
circular. This is what ties one on-chain transfer to one HTTP resource offer:
a real payment for request A cannot be replayed to buy request B, because B's
requirements hash differently.
"""

from __future__ import annotations

import base64
import hashlib
import json

from pydantic import BaseModel, Field

MEMO_LEN = 43  # sha256 -> 32 bytes -> ceil(32*8/6) = 43 b64url chars, unpadded


class PaymentRequirements(BaseModel):
    """The x402 v1 `accepts` entry, scheme `exact` on Hedera rails."""

    scheme: str = "exact"
    network: str = "hedera-testnet"
    maxAmountRequired: str  # tinybar, as a string per x402 convention
    resource: str
    description: str = ""
    mimeType: str = "application/json"
    payTo: str  # 0.0.x
    maxTimeoutSeconds: int = 180
    asset: str = "HBAR"
    extra: dict = Field(default_factory=dict)

    def canonical(self) -> bytes:
        body = self.model_dump(exclude={"extra"})
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    def memo(self) -> str:
        digest = hashlib.sha256(self.canonical()).digest()
        out = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert len(out) == MEMO_LEN
        return out

    def with_memo_hint(self) -> "PaymentRequirements":
        """The copy we actually serve in the 402 body: memo in `extra`."""
        return self.model_copy(update={"extra": {**self.extra, "memo": self.memo()}})

    @property
    def amount_tinybar(self) -> int:
        return int(self.maxAmountRequired)
