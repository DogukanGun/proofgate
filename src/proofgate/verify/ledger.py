"""The spend ledger backing P5 (double-spend) — sqlite, one table.

Sqlite rather than a dict so the property actually holds across gateway
restarts: "one on-chain payment buys one resource serve" is meaningless if
bouncing the process resets it. The UNIQUE constraint is the predicate; we
never check-then-insert, we insert and let the constraint answer atomically.
"""

from __future__ import annotations

import sqlite3
import threading


class SpendLedger:
    def __init__(self, path: str = "proofgate_spends.db"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS spends ("
            " tx_id TEXT NOT NULL, memo TEXT NOT NULL,"
            " granted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
            " PRIMARY KEY (tx_id, memo))"
        )
        self._conn.commit()

    def claim(self, tx_id: str, memo: str) -> bool:
        """Atomically claim (tx_id, memo). True = first use, False = replay."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO spends (tx_id, memo) VALUES (?, ?)", (tx_id, memo)
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def release(self, tx_id: str, memo: str) -> None:
        """Undo a claim when a LATER predicate fails after P5 claimed.

        Without this, a payment that fails freshness once is burned forever
        even though it never bought anything.
        """
        with self._lock:
            self._conn.execute(
                "DELETE FROM spends WHERE tx_id = ? AND memo = ?", (tx_id, memo)
            )
            self._conn.commit()
