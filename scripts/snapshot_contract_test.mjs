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
    components: Array.isArray(fixture.document.components)
      ? fixture.document.components.length
      : 0,
  };
});

process.stdout.write(JSON.stringify(results));
