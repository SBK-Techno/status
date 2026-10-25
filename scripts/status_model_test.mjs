/*
 * Contract test for the pure status render state.
 *
 * Run with: node scripts/status_model_test.mjs
 * The Python suite invokes it so the browser model, every stale heartbeat,
 * and the failed-fetch view are gated in CI.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const model = require("../status-model.js");

const now = Date.parse("2026-10-25T12:00:00Z");
const operational = {
  generated_at: "2026-10-25T11:59:00Z",
  expires_at: "2026-10-25T12:04:00Z",
  overall: "operational",
  summary_fr: "Tous les services surveillés répondent.",
  summary_en: "All monitored services respond.",
  components: [{ status: "operational" }],
};

function state(overrides = {}, hasError = false) {
  return model.presentation({ ...operational, ...overrides }, hasError, now);
}

function assertUnconfirmed(current, reason) {
  assert.equal(current.reason, reason);
  assert.equal(current.confirmed, false);
  assert.equal(current.forceUnknown, true);
  assert.equal(current.headline, "unknown");
  assert.notEqual(current.summary, "payload");
  assert.equal(current.signal, "unknown");
  assert.equal(current.showStaleBanner, true);
}

// A fresh heartbeat is the only state allowed to repeat the publisher claim.
{
  const fresh = model.presentation(operational, false, now);
  assert.equal(fresh.mode, "fresh");
  assert.equal(fresh.reason, "fresh");
  assert.equal(fresh.headline, "operational");
  assert.equal(fresh.summary, "payload");
  assert.equal(fresh.confirmed, true);
  assert.equal(fresh.forceUnknown, false);
  assert.equal(fresh.showStaleBanner, false);
  assert.equal(fresh.snapshot, "generated");
  assert.equal(fresh.signal, "count");
  assert.equal(fresh.generated, Date.parse("2026-10-25T11:59:00Z"));
}

// A null or empty heartbeat cannot confirm the payload's operational claim.
for (const [name, generated] of [
  ["null", null],
  ["empty", ""],
]) {
  const current = state({ generated_at: generated, expires_at: generated });
  assertUnconfirmed(current, "never");
  assert.equal(current.snapshot, "none");
  assert.equal(current.generated, null);
  assert.equal(
    current.headline,
    "unknown",
    `${name}: an operational payload cannot supply the headline`,
  );
}

// A heartbeat beyond the tolerated clock skew is not fresh, even though its
// age is negative.
{
  const current = state({
    generated_at: "2026-10-25T13:00:00Z",
    expires_at: "2026-10-25T13:05:00Z",
  });
  assertUnconfirmed(current, "future");
  assert.equal(current.snapshot, "unknown");
  assert.equal(current.showVitals, true);
  assert.equal(current.showComponents, true);
}

// Exactly five minutes ahead is tolerated; one second more is not.
{
  const boundary = state({
    generated_at: "2026-10-25T12:05:00Z",
    expires_at: "2026-10-25T12:10:00Z",
  });
  assert.equal(boundary.reason, "fresh");
  const beyond = state({
    generated_at: "2026-10-25T12:05:01Z",
    expires_at: "2026-10-25T12:10:01Z",
  });
  assertUnconfirmed(beyond, "future");
}

// An expired snapshot still has a readable timestamp, but not a current state.
{
  const current = state({
    generated_at: "2026-10-25T11:40:00Z",
    expires_at: "2026-10-25T11:50:00Z",
  });
  assertUnconfirmed(current, "expired");
  assert.equal(current.snapshot, "generated");
  assert.equal(current.generated, Date.parse("2026-10-25T11:40:00Z"));
  assert.equal(current.showVitals, true);
  assert.equal(current.showComponents, true);
}

// A heartbeat older than the 15-minute budget is aged even if a malformed
// document publishes a later expiry. The page must not trust that budget.
{
  const current = state({
    generated_at: "2026-10-25T11:44:00Z",
    expires_at: "2026-10-25T13:00:00Z",
  });
  assertUnconfirmed(current, "aged");
  assert.equal(current.snapshot, "generated");
}

// A failed read owns the view even when a previous payload is retained:
// no rows, no vitals, no counts, and no summary from the old document.
{
  const failed = model.presentation(operational, true, now);
  assert.equal(failed.mode, "error");
  assert.equal(failed.reason, "error");
  assert.equal(failed.headline, "unknown");
  assert.equal(failed.summary, "notDisplayed");
  assert.equal(failed.confirmed, false);
  assert.equal(failed.forceUnknown, true);
  assert.equal(failed.showData, false);
  assert.equal(failed.showComponents, false);
  assert.equal(failed.showVitals, false);
  assert.equal(failed.showStaleBanner, false);
  assert.equal(failed.signal, "unknown");
  assert.equal(failed.snapshot, "unknown");
}

// The same failed read before any successful read has no data to leak.
{
  const failed = model.presentation(null, true, now);
  assert.equal(failed.mode, "error");
  assert.equal(failed.headline, "unknown");
  assert.equal(failed.summary, "notDisplayed");
  assert.equal(failed.showData, false);
  assert.equal(failed.showVitals, false);
  assert.equal(failed.signal, "unknown");
}

{
  const loading = model.presentation(null, false, now);
  assert.equal(loading.mode, "loading");
  assert.equal(loading.reason, "loading");
  assert.equal(loading.headline, "unknown");
  assert.equal(loading.summary, "loadingSummary");
  assert.equal(loading.showData, false);
  assert.equal(loading.showVitals, false);
  assert.equal(loading.showStaleBanner, false);
}

// Low-level freshness remains the single timestamp rule.
{
  const freshness = model.freshness({ ...operational, generated_at: null }, now);
  assert.equal(freshness.reason, "never");
  assert.equal(freshness.generated, null);
}

{
  const freshness = model.freshness(operational, now);
  assert.equal(freshness.reason, "fresh");
  assert.equal(freshness.stale, false);
}

{
  const freshness = model.freshness(
    {
      ...operational,
      generated_at: "2026-10-25T11:30:00Z",
      expires_at: "2026-10-25T11:35:00Z",
    },
    now,
  );
  assert.equal(freshness.reason, "expired");
  assert.equal(freshness.stale, true);
}

assert.deepEqual(model.STATUSES, [
  "operational",
  "degraded",
  "partial_outage",
  "major_outage",
  "maintenance",
  "unknown",
]);
assert.equal(model.normalizeStatus("operational"), "operational");
assert.equal(model.normalizeStatus("green"), "unknown");

console.log("status model ok");
