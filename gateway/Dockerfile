# AI Gateway façade (Phase 1 — docs/own-gateway.md)
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY static/ ./static/

ENV GATEWAY_PORT=4001
EXPOSE 4001

RUN mkdir -p /var/log/gateway /var/lib/gateway

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -m src.app --health || exit 1

CMD ["python", "-m", "src.app"]
