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

  function snapshotFor(reason, generated) {
    if (reason === "never") return "none";
    if (reason === "future" || generated === null) return "unknown";
    return "generated";
  }

  function bannerCopy(reason) {
    if (reason === "never") {
      return { title: "staleNeverTitle", text: "staleNeverText" };
    }
    if (reason === "future") {
      return { title: "staleFutureTitle", text: "staleFutureText" };
    }
    return { title: "staleTitle", text: "staleAgedText" };
  }

  function summaryFor(reason) {
    if (reason === "never") return "unknownNeverSummary";
    if (reason === "future") return "unknownFutureSummary";
    return "unknownStaleSummary";
  }

  function noteFor(reason) {
    if (reason === "never") return "componentsNoteNever";
    if (reason === "future") return "componentsNoteFuture";
    return "componentsNoteStale";
  }

  /*
   * One presentation state owns every visible decision. The renderer must not
   * combine the payload, the error flag, and freshness by itself: that is how
   * the headline, the rows, the vitals count, and the banner came to disagree.
   *
   * `mode` is exactly one of loading, fresh, stale, or error. `banner` is
   * exactly one of null, stale, or error, so an error and a stale notice can
   * never be shown together. Every unconfirmed state (never, future, expired,
   * aged, or a failed read) forces the headline and every component row to
   * unknown, reports the signal count as unknown, and never reuses the payload
   * summary as a current claim. A failed read also hides the rows and the
   * vitals instrument entirely: the retained payload is not a reading, so it
   * must not supply counts.
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
        summary: hasError ? "errorSummary" : "loadingSummary",
        banner: hasError ? "error" : null,
        bannerTitle: hasError ? "errorTitle" : null,
        bannerText: hasError ? "errorText" : null,
        note: hasError ? null : "componentsNoteLoading",
        confirmed: false,
        forceUnknown: true,
        showData: false,
        showVitals: false,
        showStaleBanner: false,
        showErrorBanner: hasError,
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
        summary: "errorSummary",
        banner: "error",
        bannerTitle: "errorTitle",
        bannerText: "errorText",
        note: null,
        confirmed: false,
        forceUnknown: true,
        showData: false,
        showVitals: false,
        showStaleBanner: false,
        showErrorBanner: true,
        snapshot: "unknown",
        signal: "unknown",
        generated: null,
      };
    }

    const state = freshness(payload, current);
    if (state.stale) {
      const banner = bannerCopy(state.reason);
      return {
        mode: "stale",
        reason: state.reason,
        headline: "unknown",
        summary: summaryFor(state.reason),
        banner: "stale",
        bannerTitle: banner.title,
        bannerText: banner.text,
        note: noteFor(state.reason),
        confirmed: false,
        forceUnknown: true,
        showData: true,
        showVitals: true,
        showStaleBanner: true,
        showErrorBanner: false,
        snapshot: snapshotFor(state.reason, state.generated),
        signal: "unknown",
        generated: state.generated,
      };
    }

    return {
      mode: "fresh",
      reason: "fresh",
      headline: normalizeStatus(payload.overall),
      summary: "payload",
      banner: null,
      bannerTitle: null,
      bannerText: null,
      note: "componentsNoteFresh",
      confirmed: true,
      forceUnknown: false,
      showData: true,
      showVitals: true,
      showStaleBanner: false,
      showErrorBanner: false,
      snapshot: "generated",
      signal: "count",
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
