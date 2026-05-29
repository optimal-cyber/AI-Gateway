# DNS for the lab — `optimallabs.io` (Phase 4)

> **Filename note.** This file was originally `google-dns-cnames.md` because the
> plan was to keep the lab on `lab.gooptimal.io` with DNS on Google. Cloudflare
> Access requires the domain to be in a Cloudflare-managed zone (and Free-plan
> subdomain zones aren't allowed), so the lab moved to `optimallabs.io`,
> which is already a Cloudflare zone. The filename is kept for git history;
> see **ADR-010** in [`decisions.md`](decisions.md) for the full reasoning.

## What's actually deployed

| Name | Type | Target | Where DNS lives | Proxy |
|---|---|---|---|---|
| `chat.optimallabs.io` | CNAME | `bbb2c5f5-…cfargotunnel.com` (chat tunnel UUID) | **Cloudflare DNS** | **Proxied (orange)** |
| `gateway.optimallabs.io` | CNAME | `be116b19-…cfargotunnel.com` (gateway tunnel UUID) | **Cloudflare DNS** | **Proxied (orange)** |
| `optimallabs.io` *(landing — Phase 4.5)* | CNAME | `<pages-project>.pages.dev` | **Cloudflare DNS** | DNS-only or proxied — Pages handles it |

The two app hostnames **must** be proxied (orange cloud) — that's what activates
Cloudflare Access on the request path. Without it, traffic still flows through
the tunnel, but Okta/MFA enforcement is bypassed entirely.

## Where they were added

In the Cloudflare dashboard:
**Websites → `optimallabs.io` → DNS → Records → + Add record**

For each record:
1. Type: `CNAME`
2. Name: `chat` or `gateway` (Cloudflare appends `.optimallabs.io`)
3. Target: the `<uuid>.cfargotunnel.com` value (no trailing dot in the CF UI; CF
   handles it)
4. Proxy status: **Proxied** for the app hostnames
5. TTL: Auto

Alternatively, via the API (requires `Zone DNS: Edit` scope, which the
bootstrap token did not include — added in a follow-up token if desired):

```bash
curl -X POST \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"type":"CNAME","name":"chat","content":"<uuid>.cfargotunnel.com","proxied":true,"ttl":1}' \
  "https://api.cloudflare.com/client/v4/zones/${IRONECHELON_ZONE_ID}/dns_records"
```

## Verify

```bash
# Should resolve to Cloudflare anycast IPs (orange-cloud proxied):
dig +short chat.optimallabs.io    @8.8.8.8     # -> 104.21.x / 172.67.x
dig +short gateway.optimallabs.io @8.8.8.8     # -> 104.21.x / 172.67.x

# Sanity: gooptimal.io is untouched — production records intact:
dig +short MX gooptimal.io              @8.8.8.8     # 1 smtp.google.com.
dig +short TXT gooptimal.io             @8.8.8.8     # v=spf1 include:_spf.google.com ~all
dig +short ai-security.gooptimal.io     @8.8.8.8     # optimal-cyber.github.io.
```

Once you browse to `https://chat.optimallabs.io`, Cloudflare Access intercepts
the request, redirects to Okta for authentication (with MFA), and only on success
does the request reach `cloudflared` → `open-webui:8080`. The `gateway.optimallabs.io`
path enforces the stricter policy (`lab-admins` + MFA + US geo).

## `gooptimal.io` is intentionally untouched

The original ADR-008 plan had us add three CNAMEs in Google Cloud DNS under
`lab.gooptimal.io`. Those CNAMEs were briefly added during the attempt and have
been **removed**. The `gooptimal.io` zone today contains exactly what it did
before this lab existed — apex, MX, SPF, DKIM, DMARC, `outpost`, `ai-security`,
`compliance`, `api/app/auth/monitoring`, and the `*.gooptimal.io` wildcard are
all in their original state.

> **Do NOT** add `lab.gooptimal.io` records back. They no longer serve any
> purpose — the lab is on `optimallabs.io` — and stray records would only
> confuse the topology.
