# Deploy & redeploy (gateway-host / chat-host)

The container stacks run on the EC2 app hosts, driven by systemd + a
secrets-bootstrap step. This is the host-side mechanism the compose files
reference. Full phase order is in the [README](../README.md#deploy-order); a
config-redeploy + live demo walkthrough is in [`demo-live.md`](demo-live.md).

## Mechanism

- The repo is checked out on each host at `/opt/ai-lab/repo`.
- `secrets-bootstrap.sh` (systemd `ai-lab-secrets@<role>`) pulls `lab/*` from
  Secrets Manager into a tmpfs `.env` (`/run/ai-lab/<role>.env`, 0600) and
  symlinks it as the compose `.env`. Secrets never touch disk or git.
- `ai-lab-stack@<role>` runs `docker compose up -d` for that host's stack
  (`docker/gateway-host` or `docker/chat-host`).

## Build vs. mounted — what a change requires

> **Front door = the façade.** The gateway-host runs the `gateway-facade`
> (port 4001) in front of LiteLLM (4000). It is the OpenAI-compatible `/v1`
> endpoint and the control plane (`/admin/ui`); the Cloudflare tunnel and Open
> WebUI point at `:4001`. Control plane is **on** (it owns virtual keys/budgets);
> see [`own-gateway.md`](own-gateway.md) and [`operate.md`](operate.md).

| Service | Source | A change needs |
|---|---|---|
| `gateway-facade` | **built** from `../../gateway` | rebuild: `docker compose up -d --build gateway-facade` |
| `litellm` | image (pinned by digest) + **mounted** `litellm-config.yaml` | recreate: `docker compose up -d --force-recreate litellm` |
| `compliance-mcp` | **built** from `../../mcp-server` | rebuild: `docker compose up -d --build compliance-mcp` |
| `nemo-guardrails` | **built** from `./nemo` | rebuild: `docker compose up -d --build nemo-guardrails` |
| Squid egress allowlist | Terraform `egress_allowlist_domains` → proxy user-data → `/etc/squid/allowlist.txt` | `terraform apply` (may replace the proxy), or hot-reload on the proxy: append to `/etc/squid/allowlist.txt` + `squid -k reconfigure` |

## Redeploy after a config/code change

```bash
# SSM into the host (no SSH — ADR-006):
sudo git -C /opt/ai-lab/repo pull
cd /opt/ai-lab/repo/docker/gateway-host
sudo docker compose up -d --build gateway-facade        # if façade code changed
sudo docker compose up -d --build compliance-mcp        # if MCP code changed
sudo docker compose up -d --force-recreate litellm      # if litellm-config changed
cd /opt/ai-lab/repo && ./scripts/run-smoke-tests.sh     # verify (targets :4001)
```

See [`demo-live.md`](demo-live.md) for the full redeploy-then-demo walkthrough.
