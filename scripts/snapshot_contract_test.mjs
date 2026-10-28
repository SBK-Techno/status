/*
 * Cross-language contract for the static snapshot.
 *
 * Prints the browser model's presentation state for the shared fixtures.
 * scripts/validate_status_test.py compares it with the Python snapshot
 * renderer, so the publish-time HTML and the live refresh cannot disagree
 * about freshness, the headline, the summary, the row forcing, or the banner.
 */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const model = require("../status-model.js");
const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(
  readFileSync(path.join(here, "snapshot_fixtures.json"), "utf8"),
);

const results = fixtures.fixtures.map((fixture) => {
  const current = model.presentation(
    fixture.document,
    false,
    Date.parse(fixture.now),
  );
  const components = Array.isArray(fixture.document.components)
    ? fixture.document.components.filter(
        (component) => component && typeof component === "object",
      )
    : [];
  const counts = {
    operational: 0,
    degraded: 0,
    outage: 0,
    maintenance: 0,
    unknown: 0,
  };
  for (const component of components) {
    const status = current.forceUnknown
      ? "unknown"
      : model.normalizeStatus(component.status);
    counts[model.vitalKey(status)] += 1;
  }
  return {
    name: fixture.name,
    mode: current.mode,
    reason: current.reason,
    headline: current.headline,
    summary: current.summary,
    note: current.note,
    banner: current.banner,
    snapshot: current.snapshot,
    signal: current.signal,
    confirmed: current.confirmed,
    forceUnknown: current.forceUnknown,
    generated: current.generated,
    components: components.length,
    vitals: model.vitals(components.length, counts, current.signal),
  };
});

process.stdout.write(JSON.stringify(results));
