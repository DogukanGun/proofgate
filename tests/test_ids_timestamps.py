"""The two modules that decide whether every payment works or every payment 402s."""

from __future__ import annotations

import pytest

from proofgate.hedera.ids import AccountParseError, AccountRef, TxParseError, TxRef
from proofgate.hedera.timestamps import ConsensusTime, TimestampParseError

# ---------------------------------------------------------------- timestamps


def test_parses_real_mirror_timestamp():
    t = ConsensusTime.parse("1785482761.487075104")
    assert (t.seconds, t.nanos) == (1785482761, 487075104)
    assert t.to_mirror() == "1785482761.487075104"


def test_nanos_are_zero_padded_to_nine():
    """The bug that 404s ~10% of payments nondeterministically.

    The SDK emits `...102.26000910` for nanos=26000910; the mirror needs
    `026000910`. Round-tripping must restore the padding.
    """
    t = ConsensusTime.parse("1785483102.026000910")
    assert t.nanos == 26000910
    assert t.to_mirror() == "1785483102.026000910"
    assert t.to_mirror().split(".")[1] == "026000910"


def test_short_nanos_are_padded_left_not_right():
    """`seconds.nanos` is two integer fields, not a decimal fraction.

    The mirror's own `"1785483102.026000910"` has a leading zero, which only
    makes sense for a fixed-width integer field. So `.26` is 26 nanos, not
    260ms — and the SDK's unpadded `.26000910` is 26,000,910 nanos. Reading
    it as a decimal fraction shifts the timestamp ~234ms and 404s the lookup.
    """
    assert ConsensusTime.parse("100.26").nanos == 26
    assert ConsensusTime.parse("100.26000910").nanos == 26_000_910
    assert ConsensusTime.parse("100.000000026").nanos == 26
    assert ConsensusTime.parse("100.487075104").nanos == 487_075_104


def test_ordering_and_equality_are_exact_at_nanosecond_scale():
    """Two records 100ns apart must compare unequal.

    As float64 these collapse to the same value — the reason this type exists.
    """
    a = ConsensusTime.parse("1785482761.487075104")
    b = ConsensusTime.parse("1785482761.487075204")
    assert a != b and a < b
    assert float(f"{a.seconds}.{a.nanos:09d}") == float(f"{b.seconds}.{b.nanos:09d}")


def test_age_and_arithmetic():
    a = ConsensusTime(1000, 0)
    assert a.age_seconds(ConsensusTime(1090, 0)) == pytest.approx(90.0)
    assert a.plus_seconds(1.5) == ConsensusTime(1001, 500_000_000)
    assert ConsensusTime.from_nanos(a.total_nanos) == a


@pytest.mark.parametrize("bad", ["", "abc", "-1.0", "1.0000000000", "1.2.3"])
def test_rejects_malformed_timestamps(bad):
    with pytest.raises(TimestampParseError):
        ConsensusTime.parse(bad)


# ------------------------------------------------------------------ accounts


def test_canonical_and_checksum_forms():
    assert AccountRef.parse_offline("0.0.1234") == AccountRef(0, 0, 1234)
    assert AccountRef.parse_offline("0.0.1234-vfmkw") == AccountRef(0, 0, 1234)


def test_long_zero_round_trip():
    ref = AccountRef(0, 0, 1234)
    assert ref.long_zero() == "0x" + "00" * 12 + (1234).to_bytes(8, "big").hex()
    assert AccountRef.parse_offline(ref.long_zero()) == ref


def test_long_zero_matches_real_mirror_pair():
    """Verified live: /api/v1/accounts/0x..0322 -> {"account": "0.0.802"}."""
    assert AccountRef.parse_offline(
        "0x0000000000000000000000000000000000000322"
    ) == AccountRef(0, 0, 802)


def test_the_form_mismatch_that_would_402_every_payment():
    """`0.0.802` and its long-zero are the SAME account.

    A naive `str(a).lower() != str(b).lower()` returns True here and denies
    every honest payment with payto_mismatch.
    """
    a, b = "0.0.802", "0x0000000000000000000000000000000000000322"
    assert a.lower() != b.lower()  # the naive comparison
    assert AccountRef.parse_offline(a) == AccountRef.parse_offline(b)  # the right one


def test_real_evm_alias_is_unresolved_not_wrong():
    """A keccak alias cannot be resolved offline. None means 'ask the mirror
    and fail closed', never 'no match'."""
    assert AccountRef.parse_offline("0x71C7656EC7ab88b098defB751B7401B5f6d8976F") is None


def test_zero_address_is_not_an_account():
    assert AccountRef.parse_offline("0x" + "00" * 20) is None


@pytest.mark.parametrize("bad", [None, "", "0.0", "0.0.x", "0xdeadbeef", "nope"])
def test_unresolvable_accounts(bad):
    assert AccountRef.parse_offline(bad) is None
    with pytest.raises(AccountParseError):
        AccountRef.require(bad)


def test_from_sdk_reads_fields_not_str():
    class FakeAccountId:
        shard, realm, num = 0, 0, 802

        def __str__(self):  # what the SDK does when an alias is set
            return "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"

    assert AccountRef.from_sdk(FakeAccountId()) == AccountRef(0, 0, 802)


# -------------------------------------------------------------- transactions


def test_mirror_dashed_form_round_trips():
    """The form verified to return HTTP 200."""
    tx = TxRef.parse("0.0.90-1785482751-300000342")
    assert tx.payer == AccountRef(0, 0, 90)
    assert tx.valid_start == ConsensusTime(1785482751, 300000342)
    assert tx.to_mirror() == "0.0.90-1785482751-300000342"


def test_sdk_at_form_converts_to_mirror_form():
    """The @ form returns HTTP 400 against the mirror; it must be converted."""
    tx = TxRef.parse("0.0.90@1785482751.300000342")
    assert tx.to_mirror() == "0.0.90-1785482751-300000342"
    assert tx.to_sdk() == "0.0.90@1785482751.300000342"


def test_sdk_unpadded_nanos_produce_a_padded_mirror_id():
    """THE bug. SDK gives `.26000910`; the mirror needs `-026000910`."""
    tx = TxRef.parse("0.0.945@1785483102.26000910")
    assert tx.to_mirror() == "0.0.945-1785483102-026000910"
    assert tx.to_mirror().rsplit("-", 1)[1] == "026000910"


def test_scheduled_and_nonce_flags():
    tx = TxRef.parse("0.0.90@1785482751.300000342?scheduled")
    assert tx.scheduled is True
    assert TxRef.parse("0.0.90@1785482751.300000342/3").nonce == 3


def test_both_forms_parse_to_the_same_ref():
    assert TxRef.parse("0.0.90-1785482751-300000342").to_mirror() == TxRef.parse(
        "0.0.90@1785482751.300000342"
    ).to_mirror()


@pytest.mark.parametrize(
    "bad", ["", "0.0.90", "0.0.90@", "@1.2", "0.0.90-abc-def", "garbage"]
)
def test_rejects_malformed_tx_ids(bad):
    with pytest.raises(TxParseError):
        TxRef.parse(bad)
