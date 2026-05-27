# NeMo Guardrails DaaS service (ADR-003)
FROM python:3.11-slim

WORKDIR /app

# Pin at deploy time to the version you validate. nemoguardrails pulls langchain
# + friends; fastapi/uvicorn host the DaaS endpoint; pydantic for the schema.
# (Versions verified against current upstream at build time — see docs/decisions.md.)
RUN pip install --no-cache-dir \
      "nemoguardrails>=0.11,<0.20" \
      "fastapi>=0.110" \
      "uvicorn[standard]>=0.29" \
      "pydantic>=2.6"

COPY detectors.py server.py ./
COPY config/ ./config/

RUN mkdir -p /var/log/nemo

EXPOSE 8000

# Lightweight healthcheck — does not require the HTTP server to be reachable.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python server.py --health || exit 1

CMD ["python", "server.py"]
