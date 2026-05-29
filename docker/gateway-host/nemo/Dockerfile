# NeMo Guardrails DaaS service (ADR-003)
# Using the full python:3.11 image (not -slim) because nemoguardrails pulls
# 'annoy' as a transitive dep, and annoy has no pre-built wheel for slim's
# Debian base — it compiles from source and needs g++/gcc that slim doesn't
# ship. Full image ships build tools; saves us an apt-install step (and
# avoids needing deb.debian.org in the squid allowlist). Net cost: ~700 MB
# of extra layers, which is fine for a lab.
FROM python:3.11

WORKDIR /app

# Pin at deploy time to the version you validate. nemoguardrails pulls langchain
# + friends; fastapi/uvicorn host the DaaS endpoint; pydantic for the schema.
# (Versions verified against current upstream at build time — see docs/decisions.md.)
RUN pip install --no-cache-dir \
      "nemoguardrails>=0.11,<0.20" \
      "fastapi>=0.110" \
      "uvicorn[standard]>=0.29" \
      "pydantic>=2.6" \
      "dataclasses-json>=0.6"
# dataclasses-json is a transitive dep of nemoguardrails' internals (used by
# its langchain integration). Some 0.11.x releases ship it as a hard dep and
# some don't, depending on the resolver path. Pinning it here makes the SDK
# import deterministic; without it the FastAPI service still starts and the
# deterministic detectors still enforce, but `nemo_enabled` stays false and
# the `activated_rails` audit field is empty.

COPY detectors.py server.py ./
COPY config/ ./config/

RUN mkdir -p /var/log/nemo

EXPOSE 8000

# Lightweight healthcheck — does not require the HTTP server to be reachable.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python server.py --health || exit 1

CMD ["python", "server.py"]
