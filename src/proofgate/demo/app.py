"""Single-process launcher for hosted demos (Render, Railway, Fly, Docker).

    PORT=8788 python -m proofgate.demo.app

Runs the gateway privately on 127.0.0.1:PROOFGATE_PORT and exposes the demo
console on 0.0.0.0:$PORT, because hosting platforms give you one public
port. Locally, prefer running the two processes separately; the key
separation is the point of the architecture.
"""

from __future__ import annotations

import os
import threading
import time

import uvicorn


def main() -> None:
    from ..gateway import app as gateway_app, cfg
    from .console import app as console_app

    gw = threading.Thread(
        target=uvicorn.run,
        kwargs={"app": gateway_app, "host": "127.0.0.1", "port": cfg.proofgate_port},
        daemon=True,
    )
    gw.start()
    time.sleep(1)  # let the gateway bind before the console health-checks it
    uvicorn.run(console_app, host="0.0.0.0", port=int(os.environ.get("PORT", 8788)))


if __name__ == "__main__":
    main()
