/*
 * Contract test for the pure status render state.
 *
 * Run with: node scripts/status_model_test.mjs
 * The Python suite invokes it so the browser model, the null heartbeat, and
 * the failed-fetch view are gated in CI.
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
  components: [{ status: "operational" }],
};

{
  const freshness = model.freshness({ ...operational, generated_at: null }, now);
  assert.equal(freshness.reason, "never");
  assert.equal(model.forcesUnknown(freshness), true);
  assert.equal(model.hasValidHeartbeat(freshness), false);
}

{
  const freshness = model.freshness({ ...operational, generated_at: "" }, now);
  assert.equal(freshness.reason, "never");
  assert.equal(model.forcesUnknown(freshness), true);
}

{
  const freshness = model.freshness(
    {
      ...operational,
      generated_at: "2026-10-25T13:00:00Z",
      expires_at: "2026-10-25T13:05:00Z",
    },
    now,
  );
  assert.equal(freshness.reason, "future");
  assert.equal(model.forcesUnknown(freshness), true);
}

{
  const freshness = model.freshness(operational, now);
  assert.equal(freshness.reason, "fresh");
  assert.equal(model.forcesUnknown(freshness), false);
  assert.equal(model.hasValidHeartbeat(freshness), true);
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
  assert.equal(model.forcesUnknown(freshness), false);
}

assert.deepEqual(model.view(false, false), {
  mode: "loading",
  showVitals: false,
  showComponents: false,
  componentCount: "unavailable",
  checked: "unknown",
});

assert.deepEqual(model.view(false, true), {
  mode: "error",
  showVitals: false,
  showComponents: false,
  componentCount: "unavailable",
  checked: "unknown",
});

assert.deepEqual(model.view(true, true), {
  mode: "data",
  showVitals: true,
  showComponents: true,
  componentCount: "data",
  checked: "data",
});

console.log("status model ok");
