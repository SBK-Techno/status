# SBK Techno status page

Public, static service status page for `status.ia.sbktechno.com`, hosted on GitHub Pages.

## What this site is

- A static GitHub Pages site intended for `https://status.ia.sbktechno.com`, served from the repository root.
- Plain HTML, CSS, and JavaScript with no build step and no external runtime dependency: no framework, third-party font, remote image, analytics, cookie, or browser storage.
- The platform host will publish `status.json` at the site root. The publisher is pending; until it exists, the committed seed reports a stale signal. This page reads only that public file. It never calls the private platform, the portal, or the AI gateway.
- If the platform host is down, GitHub Pages still serves the page, its assets, and the last published `status.json`. The page shows `unknown` and a stale signal notice when the heartbeat is missing or expired.
- French is the default language. English is available with the `FR | EN` switch. The choice is kept in memory and can be shared as `?lang=en`; the page uses no browser storage.
- This is not the documentation site. Documentation is private and served by Caddy.

## Brand and shared assets

The site is an SBK-operated surface, so it uses the same web brand system as the operator portal and the private documentation. The brand repository's `assets.json` is the single owner of the logical web assets, and `assets/web/manifest.json` maps each logical name to a content-hashed file, digest, media type, and byte size. This repository never keeps its own asset list.

The graphite header renders the brand layer's approved `logo-reverse.svg` unchanged, at its intrinsic 284x118 aspect ratio. Its width comes from the brand's `--sbk-logo-header` token (112 px, height auto), so the status page does not define its own header size. The brand guideline's 240 px minimum applies to client logos; the SBK issuer lockup uses the 112 px token. The footer uses the published `icon-color.svg` on a `--sbk-text` plate: the brand blue measures 2.92:1 directly on the `#080A0F` canvas, 5.99:1 on the plate, and the plate separates from the canvas at 17.47:1. `cmd/genstatus` resolves the reverse lockup and the icon from the brand manifest, rejects the colour lockup on the status surface, and fails closed when the reverse lockup is missing.

### Header lockup size and contrast

The header lockup is the brand's logotype, so WCAG 1.4.11's logotype exemption applies to it. The validator records one explicit exemption: the approved `#2255BC` fill measures 2.92:1 on the rendered `#080A0F` header surface, below the 3:1 non-text threshold. No uniform surface can satisfy both elements: the blue needs a light surface (where the white wordmark falls below 2.5:1) and the white wordmark needs a dark one (where the blue stays at or below 2.92:1).

The exemption is not a blanket pass. `scripts/validate_status.py`:

- reads the surface from `.status-header` in `styles.css` and the fills from the file the page references;
- requires exactly the approved fills `#FFFFFF` and `#2255BC`, so a recolour such as `#111111` fails;
- requires the white wordmark to reach 4.5:1 and the recorded blue ratio to match the rendered surface, so a surface change fails until it is re-approved;
- verifies every published asset's SHA-256 and byte size and checks that the header size comes from `--sbk-logo-header`.

Lifting the exemption requires the brand layer to publish a dark-surface variant whose icon and `TECHNOLOGIES` label reach 3:1 while the wordmark remains legible; this repository must not recolour the published asset.

`templates/index.html` and `templates/404.html` are the source. They reference assets with `{{asset:<logical-name>}}` placeholders. `make brand` runs `cmd/genstatus` from the platform checkout, which:

- resolves every placeholder from the brand manifest and fails on an unknown name, an unresolved placeholder, or a literal hashed filename;
- writes the hashed assets and the resolved `index.html` and `404.html`;
- writes `brand.manifest.json` for this repository;
- retains the previous hashed assets so a page that is still cached can load them, and removes the retired `brand.lock`.

Never edit the root `index.html`, `404.html`, the hashed assets, or `brand.manifest.json` by hand. Change the template or the brand repository and run `make brand`. `make brand-check` verifies the committed output without writing. Use `make brand BRAND=/path/to/sbk-brand` to read another brand checkout.

The platform command `cmd/brandlint` is the design gate for every SBK-operated surface. It reads `brand.manifest.json` and the brand manifest, and it rejects raw colours, literal font stacks, external stylesheet imports, missing shell markers (`data-surface="provider"`, `sbk-surface`, `sbk-header`, `sbk-footer`, and `Exploité par SBK Techno`), unresolved placeholders, digest or size drift, and a published asset that a page does not reference. Client apps are exempt.

`styles.css` contains only status-page layout. Every colour, font family, spacing value, and radius resolves through a `--sbk-*` token declared in the brand stylesheet.

## Presentation

The public board is a graphite surface (`data-theme="dark"`). It opens on a full-viewport panel with a text status word in the status colour, a monospaced check time, and a compact inline-SVG vitals instrument on the right. The instrument is decorative and `aria-hidden`; its arcs and counts mirror the component board, which is the accessible source of the same facts. The component rows and the incident timeline are separated by hairlines rather than cards. The theme colour is the dark canvas token, not a second literal.

The static HTML does not claim a status, a count, or a time it has not read. With JavaScript disabled the page says `L'état publié n'est pas affiché`, shows no component rows, no numeric count, no instrument, no hard-coded timestamps, keeps the `<noscript>` note, and links `status.json`; the language switch and the check time stay hidden. The same phrase is the single fallback used by the hero, the component board, and the incident journal.

The error and stale banners sit above the hero, so a failed read or an unconfirmed heartbeat is visible on the first screen at every width.

## Hosting and DNS

- GitHub Pages origin: `sbk-techno.github.io`.
- The custom domain CNAME target is `sbk-techno.github.io`. Create a DNS CNAME record for `status.ia` that points to `sbk-techno.github.io`.
- The `CNAME` file in this repository contains `status.ia.sbktechno.com` followed by one newline. GitHub Pages uses it to serve the custom domain.
- `.nojekyll` disables Jekyll processing. GitHub Pages serves the committed output as-is; the generator runs before push, not on Pages. Pages is configured to deploy from the `main` branch root.
- The custom domain is live at `https://status.ia.sbktechno.com/`; the Pages certificate is valid and HTTPS enforcement is enabled.
- `404.html` is served by GitHub Pages for unknown paths. Its links use absolute paths because a 404 response can appear at any URL.
- Enable HTTPS for the custom domain in the repository's Pages settings.

## Heartbeat

The platform `cmd/statuspublisher` probes the public surfaces, writes `status.json` atomically, signs the exact bytes with HMAC-SHA256, and dispatches `.github/workflows/publish.yml`. The workflow verifies the signature with the `STATUS_HEARTBEAT_HMAC` repository secret, validates the document with `scripts/validate_status.py`, writes `status.json`, and pushes it; Pages then deploys the branch. The publisher is implemented, but the host timer and credentials must be installed before the page leaves its stale seed.

The host reads the key from `/run/secrets/status-heartbeat-hmac` and a fine-grained GitHub token with Actions write access on this repository from `/run/secrets/status-dispatch-token`. The HMAC value must equal the repository secret. Never commit either value.

The page fetches the file every 60 seconds with `cache: "no-store"` and a cache-busting query. It refreshes immediately when the tab becomes visible after being hidden, and it does not poll while hidden.

Freshness rules:

- `generated_at` is null, empty, or unparseable: no heartbeat. `overall` and every component must be `unknown`; the validator rejects a document that claims a healthy state without a heartbeat.
- `generated_at` is more than 5 minutes in the future: invalid. Both the validator and the page reject the claim instead of treating the negative age as fresh.
- `expires_at` is in the past, or `generated_at` is older than 15 minutes: stale.
- A missing, empty, or future heartbeat forces every rendered component row and every vitals count to `unknown`, shows no verification time, and opens with the stale banner above the hero. The document's own summary is not repeated as a healthy claim.
- An expired or aged heartbeat keeps the last reported rows and their `État au <generated_at>` time, forces the overall hero state to `unknown`, and shows the stale banner. The banner is the single stale marker; rows stay clean.
- A failed fetch renders every component as unknown by rendering none, hides the vitals instrument, sets the component and incident counts to `L'état publié n'est pas affiché`, sets the check time and snapshot to `Inconnu`, and puts the error banner above the hero. A failed attempt never moves the check time; when last data exists, the page keeps it and shows the last successful read time.

The publisher writes `generated_at` and `expires_at` in UTC. `expires_at` is at most 15 minutes after `generated_at`. The page treats the signal as stale after that window even if `expires_at` is later, so the timer must run more often than 15 minutes. Incidents stay operator-owned: the publisher copies them from its configured incident file and never invents or resolves one.

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
- `generated_at` and `expires_at` are null or UTC ISO 8601 timestamps, and both must be null or both set. When they are null, `overall` and every component status must be `unknown`; a document cannot claim a confirmed state without a heartbeat. A heartbeat more than 5 minutes in the future is rejected. Incident timestamps are UTC ISO 8601 timestamps.
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

- The published assets match the SHA-256 digests and byte sizes in `brand.manifest.json`, verified by both the workflow and `validate_manifest_assets`.
- `index.html` and `404.html` reference every published asset and contain no unresolved placeholder.
- `status.json` passes `scripts/validate_status.py`: schema version 1, valid statuses, unique ids, both languages, UTC `Z` timestamps, paired `generated_at`/`expires_at`, a lifetime of at most 15 minutes, no heartbeat more than 5 minutes in the future, and no healthy claim when `generated_at` is null.
- The no-JS snapshot is checked: it has no `<time>`, no hard-coded timestamp, no component rows, no numeric count, a hidden vitals instrument, and the single `L'état publié n'est pas affiché` fallback.
- The header mark is measured from the file the page references and the surface `.status-header` paints; recoloured fills, a changed surface, and a header-size override fail.
- Every incident has the required fields and `updated_at` is not before `started_at`.
- The same validator runs in `publish.yml` before a signed heartbeat is committed.
- `CNAME` is exactly `status.ia.sbktechno.com` with a final newline.
- `index.html`, `styles.css`, `app.js`, and `status-model.js` exist and are non-empty.

`make validate` runs the JSON parse and delegates to the platform generator check locally. `python3 -m unittest discover -s scripts -p '*_test.py'` also runs `scripts/status_model_test.mjs` through Node so the null-heartbeat and failed-fetch render states are gated with the browser model.

No third-party action is used beyond `actions/checkout`.

## Security and privacy

- `index.html` carries a strict CSP meta tag. JavaScript and CSS live in separate files, with no inline scripts or inline styles.
- There are no secrets, cookies, browser storage, analytics, service workers, beacons, or external assets. The `status.json` fetch uses `credentials: "omit"`.
- Everything published in this repository and in `status.json` is public. Never put private platform data, client data, tokens, or internal URLs in the status file.
- GitHub Pages does not allow arbitrary response headers. The CSP is delivered with a meta tag. Browsers ignore `frame-ancestors` in a meta CSP, so clickjacking protection must be enforced at the edge or hosting layer if that threat is in scope for this public page.

### Deployment follow-up: response headers

Serving the status page behind an edge that emits `Content-Security-Policy`, HSTS, and the other security headers remains a deployment follow-up. This task does not attempt to work around the GitHub Pages header limitation.
