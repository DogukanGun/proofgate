"""Hedera consensus timestamps as exact integers.

NEVER float. Epoch nanos are ~1.79e18; float64's 53-bit mantissa resolves only
~200ns at that magnitude, so `float(a) == float(b)` can be true for two records
100ns apart. Exact equality between the mirror's decimal string and the
consensus node's (seconds, nanos) pair is the join key of the entire
cross-check — it has to be integer arithmetic.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

NANOS_PER_SECOND = 1_000_000_000

# "1785482761.487075104" — seconds.nanos, nanos always 1..9 digits on the wire
_TS_RE = re.compile(r"^(?P<sec>\d{1,19})(?:\.(?P<nanos>\d{1,9}))?$")


class TimestampParseError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class ConsensusTime:
    """An exact Hedera consensus timestamp. Ordering is total and correct."""

    seconds: int
    nanos: int

    def __post_init__(self) -> None:
        if not (0 <= self.nanos < NANOS_PER_SECOND):
            raise TimestampParseError(f"nanos out of range: {self.nanos}")
        if self.seconds < 0:
            raise TimestampParseError(f"negative seconds: {self.seconds}")

    # ---- parsing ----
    @classmethod
    def parse(cls, s: str | "ConsensusTime") -> "ConsensusTime":
        """Parse `"1785482761.487075104"`.

        This is NOT a decimal fraction. It is two integer fields joined by a
        dot, and the nanos field is rendered as 9 digits. The proof is that
        the mirror emits `"1785483102.026000910"` — a leading zero, which a
        decimal fraction would never need but a fixed-width integer field
        does.

        So a short fraction is zero-padded on the LEFT, not the right. This
        matters because the SDK's `TransactionId.to_string()` emits
        `f"{seconds}.{nanos}"` with `nanos` an unpadded int: `.26000910`
        means 26,000,910 nanos, not 260,009,100. Getting this backwards
        shifts the timestamp by ~234ms and 404s the lookup.
        """
        if isinstance(s, ConsensusTime):
            return s
        m = _TS_RE.match(str(s).strip())
        if not m:
            raise TimestampParseError(f"not a consensus timestamp: {s!r}")
        frac = m.group("nanos") or ""
        return cls(int(m.group("sec")), int(frac.zfill(9)) if frac else 0)

    @classmethod
    def from_nanos(cls, total: int) -> "ConsensusTime":
        return cls(total // NANOS_PER_SECOND, total % NANOS_PER_SECOND)

    @classmethod
    def now(cls) -> "ConsensusTime":
        return cls.from_nanos(time.time_ns())

    # ---- rendering ----
    def to_mirror(self) -> str:
        """`1785482761.487075104` — nanos ALWAYS zero-padded to 9.

        The mirror rejects short nanos in a transaction id path segment, and
        an unpadded value is the single most likely cause of a spurious 404.
        """
        return f"{self.seconds}.{self.nanos:09d}"

    @property
    def total_nanos(self) -> int:
        return self.seconds * NANOS_PER_SECOND + self.nanos

    def age_seconds(self, now: "ConsensusTime | None" = None) -> float:
        """Signed age in seconds. Positive = in the past.

        Float is fine HERE and only here: this feeds human-scale window
        comparisons (a 180s freshness window), never an equality test.
        """
        ref = now or ConsensusTime.now()
        return (ref.total_nanos - self.total_nanos) / NANOS_PER_SECOND

    def plus_seconds(self, secs: float) -> "ConsensusTime":
        return ConsensusTime.from_nanos(self.total_nanos + int(secs * NANOS_PER_SECOND))

    def __str__(self) -> str:
        return self.to_mirror()
