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

const states = [];
function track(current) {
  states.push(current);
  return current;
}

// A fresh heartbeat is the only state allowed to repeat the publisher claim.
{
  const fresh = track(model.presentation(operational, false, now));
  assert.equal(fresh.mode, "fresh");
  assert.equal(fresh.reason, "fresh");
  assert.equal(fresh.headline, "operational");
  assert.equal(fresh.summary, "payload");
  assert.equal(fresh.banner, null);
  assert.equal(fresh.bannerTitle, null);
  assert.equal(fresh.bannerText, null);
  assert.equal(fresh.note, "componentsNoteFresh");
  assert.equal(fresh.confirmed, true);
  assert.equal(fresh.forceUnknown, false);
  assert.equal(fresh.showData, true);
  assert.equal(fresh.showVitals, true);
  assert.equal(fresh.showStaleBanner, false);
  assert.equal(fresh.showErrorBanner, false);
  assert.equal(fresh.snapshot, "generated");
  assert.equal(fresh.signal, "count");
  assert.equal(fresh.generated, Date.parse("2026-10-25T11:59:00Z"));
}

// A null or empty heartbeat cannot confirm the payload's operational claim.
for (const [name, generated] of [
  ["null", null],
  ["empty", ""],
]) {
  const current = track(state({ generated_at: generated, expires_at: generated }));
  assert.equal(current.mode, "stale", `${name}: a missing heartbeat is not loading`);
  assert.equal(current.reason, "never");
  assert.equal(current.confirmed, false);
  assert.equal(current.forceUnknown, true);
  assert.equal(current.headline, "unknown", `${name}: an operational payload cannot supply the headline`);
  assert.equal(current.summary, "unknownNeverSummary");
  assert.equal(current.banner, "stale");
  assert.equal(current.bannerTitle, "staleNeverTitle");
  assert.equal(current.bannerText, "staleNeverText");
  assert.equal(current.note, "componentsNoteNever");
  assert.equal(current.showStaleBanner, true);
  assert.equal(current.showErrorBanner, false);
  assert.equal(current.showData, true);
  assert.equal(current.showVitals, true);
  assert.equal(current.signal, "unknown");
  assert.equal(current.snapshot, "none");
  assert.equal(current.generated, null);
}

// A heartbeat beyond the tolerated clock skew is not fresh, even though its
// age is negative.
{
  const current = track(
    state({
      generated_at: "2026-10-25T13:00:00Z",
      expires_at: "2026-10-25T13:05:00Z",
    }),
  );
  assert.equal(current.mode, "stale");
  assert.equal(current.reason, "future");
  assert.equal(current.summary, "unknownFutureSummary");
  assert.equal(current.bannerTitle, "staleFutureTitle");
  assert.equal(current.bannerText, "staleFutureText");
  assert.equal(current.note, "componentsNoteFuture");
  assert.equal(current.snapshot, "unknown");
  assert.equal(current.showVitals, true);
}

// Exactly five minutes ahead is tolerated; one second more is not.
{
  const boundary = state({
    generated_at: "2026-10-25T12:05:00Z",
    expires_at: "2026-10-25T12:10:00Z",
  });
  assert.equal(boundary.reason, "fresh");
  const beyond = track(
    state({
      generated_at: "2026-10-25T12:05:01Z",
      expires_at: "2026-10-25T12:10:01Z",
    }),
  );
  assert.equal(beyond.reason, "future");
  assert.equal(beyond.mode, "stale");
}

// An expired snapshot still has a readable timestamp, but not a current state.
{
  const current = track(
    state({
      generated_at: "2026-10-25T11:40:00Z",
      expires_at: "2026-10-25T11:50:00Z",
    }),
  );
  assert.equal(current.mode, "stale");
  assert.equal(current.reason, "expired");
  assert.equal(current.summary, "unknownStaleSummary");
  assert.equal(current.bannerTitle, "staleTitle");
  assert.equal(current.bannerText, "staleAgedText");
  assert.equal(current.note, "componentsNoteStale");
  assert.equal(current.snapshot, "generated");
  assert.equal(current.generated, Date.parse("2026-10-25T11:40:00Z"));
  assert.equal(current.showVitals, true);
}

// A heartbeat older than the 15-minute budget is aged even if a malformed
// document publishes a later expiry. The page must not trust that budget.
{
  const current = track(
    state({
      generated_at: "2026-10-25T11:44:00Z",
      expires_at: "2026-10-25T13:00:00Z",
    }),
  );
  assert.equal(current.mode, "stale");
  assert.equal(current.reason, "aged");
  assert.equal(current.summary, "unknownStaleSummary");
  assert.equal(current.bannerText, "staleAgedText");
  assert.equal(current.snapshot, "generated");
}

// A fresh heartbeat keeps a per-component unknown as a real unknown segment;
// only an unconfirmed heartbeat forces every row to unknown.
{
  const mixed = track(
    model.presentation(
      {
        ...operational,
        overall: "degraded",
        components: [
          { status: "operational" },
          { status: "degraded" },
          { status: "unknown" },
        ],
      },
      false,
      now,
    ),
  );
  assert.equal(mixed.mode, "fresh");
  assert.equal(mixed.confirmed, true);
  assert.equal(mixed.forceUnknown, false);
  assert.equal(mixed.headline, "degraded");
  assert.equal(mixed.signal, "count");
  assert.equal(mixed.note, "componentsNoteFresh");
}

// A failed read owns the view even when a previous payload is retained:
// no rows, no vitals, no counts, and no summary from the old document.
{
  const failed = track(model.presentation(operational, true, now));
  assert.equal(failed.mode, "error");
  assert.equal(failed.reason, "error");
  assert.equal(failed.headline, "unknown");
  assert.equal(failed.summary, "errorSummary");
  assert.equal(failed.banner, "error");
  assert.equal(failed.bannerTitle, "errorTitle");
  assert.equal(failed.bannerText, "errorText");
  assert.equal(failed.note, null);
  assert.equal(failed.confirmed, false);
  assert.equal(failed.forceUnknown, true);
  assert.equal(failed.showData, false);
  assert.equal(failed.showVitals, false);
  assert.equal(failed.showStaleBanner, false);
  assert.equal(failed.showErrorBanner, true);
  assert.equal(failed.signal, "unknown");
  assert.equal(failed.snapshot, "unknown");
}

// The same failed read before any successful read has no data to leak.
{
  const failed = track(model.presentation(null, true, now));
  assert.equal(failed.mode, "error");
  assert.equal(failed.headline, "unknown");
  assert.equal(failed.summary, "errorSummary");
  assert.equal(failed.banner, "error");
  assert.equal(failed.showData, false);
  assert.equal(failed.showVitals, false);
  assert.equal(failed.showStaleBanner, false);
  assert.equal(failed.signal, "unknown");
}

// The first load stays quiet until the bounded read window ends.
{
  const loading = track(model.presentation(null, false, now));
  assert.equal(loading.mode, "loading");
  assert.equal(loading.reason, "loading");
  assert.equal(loading.headline, "unknown");
  assert.equal(loading.summary, "loadingSummary");
  assert.equal(loading.banner, null);
  assert.equal(loading.bannerTitle, null);
  assert.equal(loading.bannerText, null);
  assert.equal(loading.note, "componentsNoteLoading");
  assert.equal(loading.showData, false);
  assert.equal(loading.showVitals, false);
  assert.equal(loading.showStaleBanner, false);
  assert.equal(loading.showErrorBanner, false);
  assert.equal(loading.signal, "unknown");
  assert.equal(loading.snapshot, "unknown");
}

// One presentation state at a time, and exactly one banner at most.
for (const current of states) {
  assert.ok(
    ["loading", "fresh", "stale", "error"].includes(current.mode),
    `unexpected mode ${current.mode}`,
  );
  assert.equal(
    current.showStaleBanner && current.showErrorBanner,
    false,
    `${current.mode}/${current.reason}: both banners are visible`,
  );
  assert.equal(
    current.banner === "stale",
    current.showStaleBanner,
    `${current.mode}/${current.reason}: stale banner decision disagrees`,
  );
  assert.equal(
    current.banner === "error",
    current.showErrorBanner,
    `${current.mode}/${current.reason}: error banner decision disagrees`,
  );
  if (current.mode === "error") {
    assert.equal(current.showData, false);
    assert.equal(current.showVitals, false);
    assert.equal(current.banner, "error");
  }
  if (current.mode === "loading") {
    assert.equal(current.banner, null);
    assert.equal(current.showData, false);
  }
  if (current.mode === "fresh") {
    assert.equal(current.banner, null);
    assert.equal(current.confirmed, true);
  }
  if (current.mode === "stale") {
    assert.equal(current.banner, "stale");
    assert.equal(current.confirmed, false);
    assert.equal(current.forceUnknown, true);
    assert.equal(current.headline, "unknown");
    assert.notEqual(current.summary, "payload");
  }
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

assert.deepEqual(model.VITAL_KEYS, [
  "operational",
  "degraded",
  "outage",
  "maintenance",
  "unknown",
]);
assert.equal(model.vitalKey("partial_outage"), "outage");
assert.equal(model.vitalKey("major_outage"), "outage");
assert.equal(model.vitalKey("green"), "unknown");
assert.equal(model.dashedArc(0), "0 100");
assert.equal(model.dashedArc(100), "1.2 2");

{
  const geometry = model.vitals(
    5,
    { operational: 2, degraded: 1, outage: 0, maintenance: 0, unknown: 2 },
    "count",
  );
  assert.equal(geometry.total, 5);
  assert.equal(geometry.signals, 3);
  assert.equal(geometry.signalLength, 60);
  assert.equal(geometry.scaleGap, "0.8 19.2");
  assert.equal(geometry.segments.operational.dasharray, "40 100");
  assert.equal(geometry.segments.operational.dashoffset, "0");
  assert.equal(geometry.segments.degraded.dasharray, "20 100");
  assert.equal(geometry.segments.degraded.dashoffset, "-40");
  assert.ok(geometry.segments.unknown.dasharray.startsWith("1.2 2"));
}

{
  const geometry = model.vitals(
    0,
    { operational: 0, degraded: 0, outage: 0, maintenance: 0, unknown: 0 },
    "unknown",
  );
  assert.equal(geometry.signals, null);
  assert.equal(geometry.signalLength, 0);
  assert.equal(geometry.scaleGap, "0 100");
  assert.equal(geometry.segments.unknown.dasharray, "0 100");
}

console.log("status model ok");
