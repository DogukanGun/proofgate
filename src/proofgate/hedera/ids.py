"""Hedera account and transaction identifiers.

A Hedera account has up to four textual forms in the wild:

    0.0.1234                                        canonical
    0.0.1234-vfmkw                                  canonical + checksum
    0x00000000000000000000000000000000000004d2      long-zero EVM address
    0x71C7656EC7ab88b098defB751B7401B5f6d8976F      real ECDSA-derived alias

Comparing any two of these as strings gives the wrong answer, and the wrong
answer here is "every payment 402s with payto_mismatch" — a failure that looks
exactly like a verifier bug. Everything crossing a trust boundary is parsed
into `AccountRef` and compared structurally.

Transaction ids have two forms and the difference is not cosmetic:

    0.0.90-1785482751-300000342     mirror REST path segment  -> HTTP 200
    0.0.90@1785482751.300000342     SDK / HAPI                -> HTTP 400

and the SDK emits nanos UNPADDED, while the mirror requires 9 digits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .timestamps import ConsensusTime, TimestampParseError

_ACCOUNT_RE = re.compile(r"^(\d{1,10})\.(\d{1,19})\.(\d{1,19})(?:-[a-z]{5})?$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

# mirror: 0.0.90-1785482751-300000342
_TX_DASH_RE = re.compile(
    r"^(?P<acct>\d+\.\d+\.\d+)-(?P<sec>\d+)-(?P<nanos>\d{1,9})$"
)
# SDK: 0.0.90@1785482751.300000342  (optionally ?scheduled / /nonce)
_TX_AT_RE = re.compile(
    r"^(?P<acct>\d+\.\d+\.\d+(?:-[a-z]{5})?)@(?P<sec>\d+)\.(?P<nanos>\d{1,9})"
    r"(?P<flags>\?scheduled)?(?:/(?P<nonce>\d+))?$"
)


class AccountParseError(ValueError):
    pass


class TxParseError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class AccountRef:
    shard: int
    realm: int
    num: int

    def canonical(self) -> str:
        return f"{self.shard}.{self.realm}.{self.num}"

    def long_zero(self) -> str:
        """The deterministic EVM address form: shard(4) || realm(8) || num(8)."""
        raw = (
            self.shard.to_bytes(4, "big")
            + self.realm.to_bytes(8, "big")
            + self.num.to_bytes(8, "big")
        )
        return "0x" + raw.hex()

    @classmethod
    def parse_offline(cls, s: str | None) -> "AccountRef | None":
        """Parse without touching the network.

        Returns None for a real (keccak-derived) EVM alias, which can only be
        resolved by asking the mirror. Callers must treat None as "unresolved"
        and fail CLOSED, never as "no match".
        """
        if s is None:
            return None
        s = str(s).strip()
        if not s:
            return None

        if m := _ACCOUNT_RE.match(s):
            # The -vfmkw checksum is network-specific; we ignore rather than
            # validate it. Validating would require the ledger id and would
            # reject valid mainnet ids on testnet.
            return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))

        h = s[2:] if s[:2].lower() == "0x" else s
        if len(h) == 40 and _HEX_RE.match(h):
            b = bytes.fromhex(h)
            # Long-zero iff the first 12 bytes are zero. A keccak alias
            # colliding with that has probability 2^-96.
            if b[:12] == b"\x00" * 12:
                num = int.from_bytes(b[12:], "big")
                if num != 0:  # 0x0 is not an account
                    return cls(0, 0, num)
            return None  # real alias -> mirror lookup required
        return None

    @classmethod
    def require(cls, s: str | None) -> "AccountRef":
        ref = cls.parse_offline(s)
        if ref is None:
            raise AccountParseError(f"unresolvable account reference: {s!r}")
        return ref

    @classmethod
    def from_sdk(cls, account_id: object) -> "AccountRef":
        """Read shard/realm/num off an SDK AccountId.

        NEVER `str(account_id)` — the SDK renders the alias form when an
        alias key or evm address is set, which would silently reintroduce the
        string-comparison bug this module exists to prevent.
        """
        return cls(
            int(getattr(account_id, "shard", 0)),
            int(getattr(account_id, "realm", 0)),
            int(getattr(account_id, "num")),
        )

    def __str__(self) -> str:
        return self.canonical()


@dataclass(frozen=True)
class TxRef:
    payer: AccountRef
    valid_start: ConsensusTime
    scheduled: bool = False
    nonce: int = 0

    @classmethod
    def parse(cls, s: str | "TxRef") -> "TxRef":
        """Accept the mirror form, the SDK form, or an SDK TransactionId."""
        if isinstance(s, TxRef):
            return s
        if not isinstance(s, str):  # SDK TransactionId object
            s = str(s)
        s = s.strip()
        if not s:
            raise TxParseError("empty transaction id")

        try:
            if m := _TX_DASH_RE.match(s):
                return cls(
                    AccountRef.require(m.group("acct")),
                    ConsensusTime.parse(f"{m.group('sec')}.{m.group('nanos')}"),
                )
            if m := _TX_AT_RE.match(s):
                return cls(
                    AccountRef.require(m.group("acct")),
                    ConsensusTime.parse(f"{m.group('sec')}.{m.group('nanos')}"),
                    scheduled=bool(m.group("flags")),
                    nonce=int(m.group("nonce") or 0),
                )
        except (AccountParseError, TimestampParseError) as exc:
            raise TxParseError(f"malformed transaction id {s!r}: {exc}") from exc
        raise TxParseError(f"unrecognised transaction id form: {s!r}")

    def to_mirror(self) -> str:
        """Path segment for /api/v1/transactions/{id}. Nanos padded to 9."""
        return (
            f"{self.payer.canonical()}-{self.valid_start.seconds}"
            f"-{self.valid_start.nanos:09d}"
        )

    def to_sdk(self) -> str:
        return f"{self.payer.canonical()}@{self.valid_start.to_mirror()}"

    def __str__(self) -> str:
        return self.to_mirror()
