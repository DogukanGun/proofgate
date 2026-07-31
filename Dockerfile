FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# Required at runtime (set them in the platform dashboard, never in the image):
#   HEDERA_OPERATOR_ID, HEDERA_OPERATOR_KEY  - testnet payer (portal.hedera.com)
#   PROOFGATE_PAY_TO                         - payee account (scripts/setup_treasury.py)
ENV PORT=8788
EXPOSE 8788

CMD ["python", "-m", "proofgate.demo.app"]
