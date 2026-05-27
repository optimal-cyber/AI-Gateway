# Phase 2 upgrade hook — Cloudflare Gateway via WARP Connector

> Naming note: this is the *future egress upgrade* doc referenced by ADR-004/009
> (requirement #5), not the build-phase-2 (Docker stacks) record. It's the
> documented path to move workload egress filtering from the Squid proxy /
> AWS Network Firewall to Cloudflare Gateway (SASE), so ingress and egress are
> governed by one identity-aware control plane.

## Why consider it later

Today egress is a Squid allowlist proxy (default, ADR-009) with AWS Network
Firewall as an optional mode. Both enforce a domain allowlist at the AWS edge.
Cloudflare Gateway would instead apply **identity-aware**, logged egress policy
(the same place Access governs ingress), giving:

- one policy surface and one log stream for both ingress and egress;
- per-identity / per-app egress rules (not just per-subnet);
- DLP and category filtering on outbound traffic, reusing the Access posture.

Trade-off: it concentrates both directions on Cloudflare (a dependency the
current AWS-native egress avoids). Decide deliberately — see ADR-004.

## What it would take (stubbed)

1. **WARP Connector** on the egress path. Deploy a WARP Connector (a cloudflared
   variant that routes a subnet's traffic into Cloudflare) on a small instance in
   the egress subnet, or run WARP in connector mode on the proxy host. The app
   subnet default route then points at the connector instead of NAT.
2. **Gateway egress policies** (Zero Trust → Gateway → Firewall / Network):
   - DNS + HTTP allowlist mirroring `egress_allowlist_domains`
   - block categories; DLP for credential paste patterns
   - identity selectors where the traffic carries identity
3. **Terraform**: a `terraform/modules/cloudflare-gateway/` module (not yet
   created) for `cloudflare_zero_trust_gateway_policy` + the connector tunnel.
   Add a `egress_mode = "cloudflare_gateway"` option alongside `proxy` /
   `networkfirewall`, and have the network module route the app subnet to the
   connector ENI in that mode.
4. **Decommission** the Squid proxy (or NFW) once Gateway egress is verified.

## TODO checklist

- [ ] Create `terraform/modules/cloudflare-gateway/` (WARP Connector + policies)
- [ ] Add `egress_mode = "cloudflare_gateway"` and the network routing branch
- [ ] Port `egress_allowlist_domains` into Gateway HTTP/DNS policies
- [ ] Add Gateway egress logs to the audit-trail story (sso-role-mapping.md)
- [ ] Re-evaluate ADR-004 concentration-risk trade-off before cutting over
