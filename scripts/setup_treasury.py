"""Create the payee (treasury) account and print its id.

    .venv/bin/python scripts/setup_treasury.py

The payee must differ from the payer: a self-transfer nets to zero for the
payer and would satisfy nothing. This account needs no key management — the
gateway only ever READS its credits from the mirror.
"""

from hiero_sdk_python import AccountCreateTransaction, Hbar, PrivateKey

from proofgate.demo.pay import operator_client


def main() -> None:
    client, _ = operator_client()
    key = PrivateKey.generate_ed25519()
    receipt = (
        AccountCreateTransaction()
        .set_key_without_alias(key.public_key())
        .set_initial_balance(Hbar(1))
        .set_account_memo("proofgate treasury")
        .execute(client, validate_status=True)  # returns the receipt post-consensus
    )
    account = receipt.account_id
    print(f"treasury account: {account}")
    print(f"treasury key (not needed by proofgate): {key.to_string_raw()}")
    print(f"\nput this in .env:\nPROOFGATE_PAY_TO={account}")


if __name__ == "__main__":
    main()
