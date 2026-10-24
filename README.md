# SBK Techno status page

Public, static service status page for `status.sbktechno.com`, hosted on GitHub Pages.

## What this site is

- A static GitHub Pages site served at `https://status.sbktechno.com` from the repository root.
- Plain HTML, CSS, and JavaScript with no build step and no external runtime dependency: no framework, third-party font, remote image, analytics, or cookie.
- The platform host will publish `status.json` at the site root. The publisher is pending; until it exists, the committed seed reports a stale signal. This page reads only that public file. It never calls the private platform, the portal, or the AI gateway.
- If the platform host is down, GitHub Pages still serves the page, its assets, and the last published `status.json`. The page shows `unknown` and a stale signal notice when the heartbeat is missing or expired.
- French is the default language. English is available with the `FR | EN` switch. The choice is stored in `localStorage` under `sbk.status.lang`.
- This is not the documentation site. Documentation is private and served by Caddy.

## Brand and shared assets

The site is an SBK-operated surface, so it uses the same web brand system as the operator portal and the private documentation. It consumes four shared files from `SBK-Techno/brand` and never reimplements their colours, type, or logos:

| File | Source in `sbk-brand` | Role |
| --- | --- | --- |
| `brand.css` | `assets/web/brand.css` | Generated tokens, type, shell, footer, table, badge, and status primitives |
| `logo-color.svg` | `assets/svg/logo-color.svg` | Header wordmark |
| `icon-color.svg` | `assets/svg/icon-color.svg` | Footer mark and SVG favicon |
| `favicon.ico` | `assets/web/favicon.ico` | Browser favicon |

`make brand` copies those files from a sibling `../sbk-brand` checkout and rewrites `brand.lock` with their SHA-256 digests. Use `make brand BRAND=/path/to/sbk-brand` to read another checkout. `make brand-check` verifies the recorded digests, and CI runs the same check before validating the status data. Never edit the copied files here; change the brand repository and run `make brand`. Neither target is needed to serve the site: GitHub Pages serves the repository root as-is, and the Makefile only refreshes shared assets and runs local checks.

The platform command `cmd/brandlint` is the design gate for every SBK-operated surface. It rejects raw colours, literal font stacks, external stylesheet imports, and missing shell markers (`data-surface="provider"`, `sbk-surface`, `sbk-header`, `sbk-footer`, `brand.css`, `logo-color.svg`, and `Exploité par SBK Techno`). It also fails when this repository's `brand.css` drifts from the brand checkout or when a `brand.lock` digest does not match the copied bytes. The status workflow does not duplicate that scan: platform CI owns the design gate, and this repository owns asset integrity and status data.

`styles.css` contains only status-page layout. Every colour, font family, spacing value, and radius resolves through a `--sbk-*` token declared in `brand.css`.

## Hosting and DNS

- GitHub Pages origin: `sbk-techno.github.io`.
- The custom domain CNAME target is `sbk-techno.github.io`. Create a DNS CNAME record for `status` that points to `sbk-techno.github.io`.
- The `CNAME` file in this repository contains `status.sbktechno.com` followed by one newline. GitHub Pages uses it to serve the custom domain.
- `.nojekyll` disables Jekyll processing. No build step is required. Configure GitHub Pages to deploy from the `main` branch root.
- `404.html` is served by GitHub Pages for unknown paths. Its links use absolute paths because a 404 response can appear at any URL.
- Enable HTTPS for the custom domain in the repository's Pages settings.

## Heartbeat (pending)

The platform will write `status.json` atomically at the site root. The publisher is not implemented yet, so the committed seed is served and the page shows a stale signal. The page fetches the file every 60 seconds with `cache: "no-store"` and a cache-busting query. It refreshes immediately when the tab becomes visible after being hidden, and it does not poll while hidden.

Freshness rules:

- `generated_at` is null: stale.
- `expires_at` is in the past: stale.
- `generated_at` is older than 15 minutes: stale.
- Stale data keeps the component rows and their last reported state, marks them `Signal périmé`, forces the overall hero state to `unknown`, and shows the stale banner.
- A failed fetch keeps the last rendered data and shows an error banner with a retry button. If nothing has loaded yet, the page shows an explicit error state.

The publisher must write `generated_at` and `expires_at` in UTC. Keep `expires_at` no more than 15 minutes after `generated_at`. The page treats the signal as stale after that window even if `expires_at` is later.

## Schema

```json
{
  "version": 1,
  "generated_at": "2026-10-24T12:00:00Z",
  "expires_at": "2026-10-24T12:15:00Z",
  "overall": "operational",
  "summary_fr": "Tous les services fonctionnent normalement.",
  "summary_en": "All services are operating normally.",
  "components": [
    {
      "id": "portal",
      "label_fr": "Portail",
      "label_en": "Portal",
      "status": "operational",
      "detail_fr": "Le portail répond.",
      "detail_en": "The portal is responding."
    }
  ],
  "incidents": [
    {
      "id": "inc_20261024_1100",
      "status": "monitoring",
      "title_fr": "Latence élevée sur le portail",
      "title_en": "Elevated latency on the portal",
      "started_at": "2026-10-24T11:00:00Z",
      "updated_at": "2026-10-24T11:30:00Z",
      "summary_fr": "Le service est stable. Nous continuons de surveiller.",
      "summary_en": "The service is stable. We continue to monitor."
    }
  ]
}
```

Required fields:

- Top level: `version`, `generated_at`, `expires_at`, `overall`, `summary_fr`, `summary_en`, `components`, `incidents`.
- Component: `id` (unique), `label_fr`, `label_en`, `status`, `detail_fr`, `detail_en`.
- Incident: `id` (unique), `status`, `title_fr`, `title_en`, `started_at`, `updated_at`, `summary_fr`, `summary_en`.
- `version` must be `1`.
- `generated_at` and `expires_at` are null or UTC ISO 8601 timestamps. Incident timestamps are UTC ISO 8601 timestamps.
- `components` must be a non-empty list. Components are rendered in JSON order.
- Active incidents are rendered before resolved incidents. Within each group, JSON order is preserved.

### Overall and component status values

| Value | French label | English label |
| --- | --- | --- |
| `operational` | Opérationnel | Operational |
| `degraded` | Dégradé | Degraded |
| `partial_outage` | Panne partielle | Partial outage |
| `major_outage` | Panne majeure | Major outage |
| `maintenance` | Maintenance | Maintenance |
| `unknown` | Inconnu | Unknown |

### Incident status values

| Value | French label | English label |
| --- | --- | --- |
| `investigating` | Enquête en cours | Investigating |
| `identified` | Cause identifiée | Identified |
| `monitoring` | Sous surveillance | Monitoring |
| `resolved` | Résolu | Resolved |

## Local preview

```sh
python3 -m http.server 8000
```

Open `http://127.0.0.1:8000/`.

Do not open `index.html` through `file://`. Browsers block the `status.json` fetch for local files, so the page shows its error state.

Run the local checks with:

```sh
make brand-check
make validate
```

## Validation

`.github/workflows/validate.yml` runs on every push to `main`, on pull requests, and on manual dispatch. It pins `actions/checkout` to a full commit SHA and uses the runner's Python 3.

The workflow checks:

- The four shared assets match the SHA-256 digests in `brand.lock`.
- `status.json` parses, uses schema version 1, and has valid overall, component, and incident statuses.
- `components` is a non-empty list with unique ids and both language labels.
- `generated_at` and `expires_at` are null or valid UTC ISO 8601 timestamps.
- Every incident has the required fields.
- `CNAME` is exactly `status.sbktechno.com` with a final newline.
- `index.html`, `styles.css`, and `app.js` exist and are non-empty.

`make validate` runs the JSON parse and the brand lock check locally. The platform `cmd/brandlint` scan runs in platform CI and also scans this sibling checkout when present.

No third-party action is used beyond `actions/checkout`.

## Security and privacy

- `index.html` carries a strict CSP meta tag. JavaScript and CSS live in separate files, with no inline scripts or inline styles.
- There are no secrets, cookies, analytics, or external assets. The `status.json` fetch uses `credentials: "omit"`.
- Everything published in this repository and in `status.json` is public. Never put private platform data, client data, tokens, or internal URLs in the status file.
- GitHub Pages does not allow arbitrary response headers. The CSP is delivered with a meta tag. Browsers ignore `frame-ancestors` in a meta CSP, so clickjacking protection must be enforced at the edge or hosting layer if that threat is in scope for this public page.
