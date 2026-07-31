"""Runtime configuration. Everything comes from the environment / .env.

The gateway process needs NO private key — only the demo payer does, and it
reads HEDERA_OPERATOR_* itself. Keeping the key out of this Settings object
means a gateway misconfiguration can never leak it.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    hedera_network: str = "testnet"
    proofgate_mirror_endpoints: str = "https://testnet.mirrornode.hedera.com"

    proofgate_pay_to: str = ""
    proofgate_asset: str = "HBAR"
    proofgate_price_tinybar: int = 10_000_000  # 0.1 HBAR

    proofgate_freshness_window_s: int = 180
    proofgate_clock_skew_s: int = 5
    proofgate_crosscheck_mode: str = "off"  # off | receipt | record

    proofgate_public_url: str = "http://localhost:8787"
    proofgate_port: int = 8787

    @property
    def mirror_base(self) -> str:
        return self.proofgate_mirror_endpoints.split(",")[0].strip().rstrip("/")

    @property
    def x402_network(self) -> str:
        return f"hedera-{self.hedera_network}"


def settings() -> Settings:
    return Settings()
