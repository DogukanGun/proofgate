"""The demo payer's Hedera write path. This module is the ONLY place in the
repo that touches a private key, and the gateway never imports it."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from hiero_sdk_python import (
    AccountId,
    Client,
    Network,
    PrivateKey,
    TransferTransaction,
)

from ..hedera.ids import TxRef


def operator_client() -> tuple[Client, AccountId]:
    load_dotenv()
    op_id = os.environ.get("HEDERA_OPERATOR_ID", "")
    op_key = os.environ.get("HEDERA_OPERATOR_KEY", "")
    if not op_id or "x" in op_id or not op_key:
        raise SystemExit(
            "Set HEDERA_OPERATOR_ID / HEDERA_OPERATOR_KEY in .env "
            "(testnet account from https://portal.hedera.com)"
        )
    network = os.environ.get("HEDERA_NETWORK", "testnet")
    client = Client(Network(network=network))
    account = AccountId.from_string(op_id)
    client.set_operator(account, _parse_key(op_key))
    return client, account


def _parse_key(s: str) -> PrivateKey:
    """The portal hands out DER hex (ed25519 OR ecdsa) and hex-with-0x forms;
    accept any of them rather than making the operator guess."""
    s = s.strip().removeprefix("0x")
    for parse in (PrivateKey.from_string_der, PrivateKey.from_string_ecdsa,
                  PrivateKey.from_string_ed25519, PrivateKey.from_string):
        try:
            return parse(s)
        except Exception:
            continue
    raise SystemExit("HEDERA_OPERATOR_KEY is not a parseable private key")


def pay(pay_to: str, amount_tinybar: int, memo: str) -> str:
    """One real HBAR transfer with the given memo. Returns the tx id in
    mirror form, after consensus (get_receipt blocks on finality, ~3s)."""
    client, payer = operator_client()
    tx = (
        TransferTransaction()
        .add_hbar_transfer(payer, -amount_tinybar)
        .add_hbar_transfer(AccountId.from_string(pay_to), amount_tinybar)
        .set_transaction_memo(memo)
    )
    receipt = tx.execute(client, validate_status=True)  # blocks on finality, ~3s
    return TxRef.parse(str(receipt.transaction_id)).to_mirror()


def hashscan(tx_mirror_id: str, network: str = "testnet") -> str:
    return f"https://hashscan.io/{network}/transaction/{tx_mirror_id}"
