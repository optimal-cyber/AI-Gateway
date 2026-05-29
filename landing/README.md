# Landing page — `ironechelon.com`

Static single-page site for Cloudflare Pages. **Intentionally public** — it's the
"what is this thing" page someone hits from a LinkedIn link before they ask for
access, so it is **not** behind Cloudflare Access (that would defeat its purpose).

- Hand-written HTML + inline CSS + inline SVG. **No frameworks, no external
  requests, no fonts/CDNs** (system fonts only). Single file: `index.html`.
- Page weight is well under the 50 kb target (~11 kb, and it's the only asset).

## Deploy (Cloudflare Pages)

Connected to the GitHub repo (`optimal-cyber/AI-Lab`), Pages auto-deploys on push.

**Dashboard path:**
1. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**.
2. Pick `optimal-cyber/AI-Lab`.
3. Build settings:
   - **Framework preset:** None
   - **Build command:** *(empty)*
   - **Build output directory:** `landing`
4. Deploy → you get `https://<project>.pages.dev` (e.g. `ai-lab-landing.pages.dev`).
5. **Custom domains → Set up a custom domain →** `ironechelon.com`. Cloudflare
   shows the exact CNAME target; put it in **Google Cloud DNS** per
   [`docs/google-dns-cnames.md`](../docs/google-dns-cnames.md). DNS stays in Google
   (ADR-008).

**Wrangler equivalent (optional):**
```bash
npm i -g wrangler
wrangler pages project create ai-lab-landing --production-branch main
wrangler pages deploy landing --project-name ai-lab-landing
```

## Free tier

Cloudflare Pages free tier — **no cost**. Not counted in the lab's ~$75–85/mo.

## Before you ship

- [ ] Set the real LinkedIn URL in `index.html` (search `TODO: set the LinkedIn URL`).
- [ ] Confirm `outpost.gooptimal.io` is the right newsletter link.
- [ ] Eyeball it on mobile (the layout collapses to one column under 620 px).
