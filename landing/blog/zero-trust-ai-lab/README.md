# Blog post — `optimallabs.io/blog/zero-trust-ai-lab/`

Public-facing HTML rendering of the writeup at
[`docs/blog-zero-trust-ai-lab.md`](../../../docs/blog-zero-trust-ai-lab.md). The
markdown source stays canonical for the repo; this folder is for the rendered
page that LinkedIn / Slack / Twitter previews link to with a proper Open Graph
image.

Why a separate HTML page instead of letting people land on the GitHub markdown:
GitHub sets its own `og:image` (the generic GitHub-logo card), and you can't
override it from the markdown. The standalone page gives LinkedIn a
`1200×627` hero image that's actually about this post.

## File map

| Path | Purpose |
|---|---|
| `index.html` | The post itself — single self-contained HTML with inline CSS, Mermaid via CDN |
| `og-hero.png` | **REQUIRED before publishing.** The 1200×627 social-preview image referenced by `og:image`. **Currently a placeholder — see "Hero image" below.** |
| `images/s5-open-webui-signed-in.png` | Hero image inside the post |
| `images/s6-litellm-logs.png` | LiteLLM admin Logs screenshot |
| `images/s8-blocked-secret.png` | Open WebUI blocked secret screenshot |
| `images/s9-blocked-injection.png` | Open WebUI blocked prompt-injection screenshot |

## Hero image (`og-hero.png`)

LinkedIn's preferred dimensions for shared posts: **1200×627** (1.91:1 aspect).
Save the social preview as `og-hero.png` directly in this folder.

Sources to consider:

- **`chat.optimallabs.io`** (incognito, signed in as `ryan@gooptimal.io`, with
  the `claude-opus-4-7` model picker visible at the top). Crop / pad to 1200×627
  with a dark background if the raw screenshot isn't that ratio. Macos: open in
  Preview → Tools → Adjust Size → make canvas 1200×627 with dark fill.
- **Composite hero** — `chat.optimallabs.io` window on the left, architecture
  diagram on the right.

After replacing the file, force LinkedIn to re-crawl at
<https://www.linkedin.com/post-inspector/> — paste
`https://optimallabs.io/blog/zero-trust-ai-lab/` and hit Inspect.

## Deploy

This page builds with the same Cloudflare Pages project as the root landing
page. The Pages build output directory is `landing/`, so
`landing/blog/zero-trust-ai-lab/index.html` is served at
`https://optimallabs.io/blog/zero-trust-ai-lab/`.

A `git push` to `main` triggers the Pages build automatically. No build command
needed (this is static HTML).

## Mermaid

The two Mermaid blocks (architecture + sequence) are rendered client-side via
`mermaid@11` from jsDelivr. **One external request** for ~100 KB of JS, which
the marketing landing page (at `/`) deliberately avoids — but a content page
this size already has more weight in screenshots, so the trade-off is OK.

If you want a fully self-contained page later, pre-render the two `.mermaid`
blocks to inline SVG with mermaid-cli and drop the script tag:

```bash
npm i -g @mermaid-js/mermaid-cli
mmdc -i diagram.mmd -o diagram.svg -t dark -b transparent
```

## OG / Twitter card debug

| Tool | Use |
|---|---|
| [LinkedIn Post Inspector](https://www.linkedin.com/post-inspector/) | Force LI to re-fetch the OG image after you swap `og-hero.png` |
| [Meta Sharing Debugger](https://developers.facebook.com/tools/debug/) | Facebook / Instagram preview |
| [Twitter Card Validator](https://cards-dev.twitter.com/validator) | Twitter / X preview |
| [opengraph.xyz](https://www.opengraph.xyz/) | One-shot preview across all platforms |
