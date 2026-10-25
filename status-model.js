/*
 * Pure freshness and view-state rules for the public status page.
 *
 * This file has no DOM dependency: app.js renders it and the Node contract
 * test exercises the same functions. Keeping the decision here means the
 * browser and the test cannot disagree about a null, empty, future, aged,
 * expired, or failed heartbeat.
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
  const STATUSES = [
    "operational",
    "degraded",
    "partial_outage",
    "major_outage",
    "maintenance",
    "unknown",
  ];
  const STATUS_SET = new Set(STATUSES);

  function normalizeStatus(value) {
    return STATUS_SET.has(value) ? value : "unknown";
  }

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
   * One presentation state owns every visible decision. The renderer must not
   * combine the payload, the error flag, and freshness by itself: that is how
   * the headline, the rows, the vitals count, and the banner came to disagree.
   *
   * Every unconfirmed state (never, future, expired, aged, or a failed read)
   * forces the headline and every component row to unknown, reports the signal
   * count as unknown, and never reuses the payload summary as a current claim.
   * A failed read also hides the rows and the vitals instrument entirely: the
   * retained payload is not a reading, so it must not supply counts.
   *
   * `summary` is either a message key or "payload"; only a fresh state may
   * render the publisher's own summary.
   */
  function presentation(payload, hasError, now) {
    const current = Number.isFinite(now) ? now : Date.now();
    const hasData = Boolean(payload);

    if (!hasData) {
      return {
        mode: hasError ? "error" : "loading",
        reason: hasError ? "error" : "loading",
        headline: "unknown",
        summary: hasError ? "notDisplayed" : "loadingSummary",
        confirmed: false,
        forceUnknown: true,
        showData: false,
        showComponents: false,
        showVitals: false,
        showStaleBanner: false,
        snapshot: "unknown",
        signal: "unknown",
        generated: null,
      };
    }

    if (hasError) {
      return {
        mode: "error",
        reason: "error",
        headline: "unknown",
        summary: "notDisplayed",
        confirmed: false,
        forceUnknown: true,
        showData: false,
        showComponents: false,
        showVitals: false,
        showStaleBanner: false,
        snapshot: "unknown",
        signal: "unknown",
        generated: null,
      };
    }

    const state = freshness(payload, current);
    const confirmed = !state.stale;

    return {
      mode: confirmed ? "fresh" : state.reason,
      reason: state.reason,
      headline: confirmed ? normalizeStatus(payload.overall) : "unknown",
      summary: confirmed ? "payload" : "unconfirmedSummary",
      confirmed,
      forceUnknown: !confirmed,
      showData: true,
      showComponents: true,
      showVitals: true,
      showStaleBanner: state.stale,
      snapshot:
        state.generated === null
          ? "none"
          : state.reason === "future"
            ? "unknown"
            : "generated",
      signal: confirmed ? "count" : "unknown",
      generated: state.generated,
    };
  }

  return {
    MAX_AGE_MS,
    FUTURE_SKEW_MS,
    STATUSES,
    parseTimestamp,
    normalizeStatus,
    freshness,
    presentation,
  };
});
