/*
 * Pure freshness and view-state rules for the public status page.
 *
 * This file has no DOM dependency: app.js renders it and the Node contract
 * test exercises the same functions. Keeping the decision here means the
 * browser and the test cannot disagree about a null, empty, future, or failed
 * heartbeat.
 */
(function (root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.StatusModel = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const MAX_AGE_MS = 15 * 60_000;
  const FUTURE_SKEW_MS = 5 * 60_000;

  function parseTimestamp(value) {
    if (typeof value !== "string" || value.trim() === "") return null;
    const ms = Date.parse(value);
    return Number.isFinite(ms) ? ms : null;
  }

  /*
   * A document without a usable heartbeat is never fresh. A document whose
   * heartbeat is beyond the tolerated clock skew is also not fresh, even
   * though `now - generated_at` is negative: a publisher bug must not keep a
   * status current into the future.
   */
  function freshness(payload, now) {
    const current = Number.isFinite(now) ? now : Date.now();
    const generated = parseTimestamp(payload && payload.generated_at);
    const expires = parseTimestamp(payload && payload.expires_at);

    if (generated === null) {
      return { stale: true, reason: "never", generated: null };
    }
    if (generated - current > FUTURE_SKEW_MS) {
      return { stale: true, reason: "future", generated };
    }
    if (expires !== null && expires <= current) {
      return { stale: true, reason: "expired", generated };
    }
    if (current - generated > MAX_AGE_MS) {
      return { stale: true, reason: "aged", generated };
    }
    return { stale: false, reason: "fresh", generated };
  }

  /*
   * No heartbeat and a future heartbeat are both unconfirmed states: the
   * payload's component statuses cannot be trusted, so every row and every
   * vitals count is forced to unknown. An expired or aged heartbeat keeps
   * its last reported rows under the stale banner.
   */
  function forcesUnknown(state) {
    return Boolean(state && (state.reason === "never" || state.reason === "future"));
  }

  function hasValidHeartbeat(state) {
    return Boolean(state && state.generated !== null && !forcesUnknown(state));
  }

  /*
   * The page has three visible states. Before a payload exists there is no
   * component, count, or check time to show; a failed read is the same
   * unavailable view plus the error banner.
   */
  function view(hasData, hasError) {
    if (hasData) {
      return {
        mode: "data",
        showVitals: true,
        showComponents: true,
        componentCount: "data",
        checked: "data",
      };
    }
    return {
      mode: hasError ? "error" : "loading",
      showVitals: false,
      showComponents: false,
      componentCount: "unavailable",
      checked: "unknown",
    };
  }

  return {
    MAX_AGE_MS,
    FUTURE_SKEW_MS,
    parseTimestamp,
    freshness,
    forcesUnknown,
    hasValidHeartbeat,
    view,
  };
});
