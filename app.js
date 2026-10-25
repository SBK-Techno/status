"use strict";

(() => {
  document.documentElement.classList.remove("no-js");

  const LANG_KEY = "sbk.status.lang";
  const STATUS_URL = "status.json";
  const REFRESH_MS = 60_000;
  const FETCH_TIMEOUT_MS = 15_000;
  const MAX_AGE_MS = 15 * 60_000;

  const STATUSES = [
    "operational",
    "degraded",
    "partial_outage",
    "major_outage",
    "maintenance",
    "unknown",
  ];
  const INCIDENT_STATUSES = ["investigating", "identified", "monitoring", "resolved"];
  const STATUS_SET = new Set(STATUSES);
  const INCIDENT_STATUS_SET = new Set(INCIDENT_STATUSES);

  const MESSAGES = {
    fr: {
      docTitle: "État du service · SBK Techno",
      metaDescription: "État public des services de SBK Techno, publié depuis la plateforme.",
      skipLink: "Aller au contenu",
      langGroup: "Langue",
      brandHome: "SBK Techno, état du service",
      pageKicker: "Information publique",
      pageTitle: "État du service",
      pageLede:
        "L'état des services offerts aux clients et à l'équipe de SBK Techno, publié depuis la plateforme.",
      pageMetaStatic: "Page statique indépendante",
      aboutLabel: "À propos",
      overallEyebrow: "État général",
      overallLead: "L'état du service est",
      staticSummary: "Aucun signal de vie reçu. L'état du service n'est pas confirmé.",
      unconfirmedSummary: "L'état du service n'est pas confirmé.",
      componentsTitle: "Composantes",
      incidentsTitle: "Incidents",
      componentsTracked: "suivies",
      incidentsNone: "Aucun",
      vitalsComponents: "composantes",
      vitalOperational: "Opérationnelles",
      vitalDegraded: "Dégradées",
      vitalOutage: "Interrompues",
      vitalMaintenance: "Maintenance",
      vitalUnknown: "Inconnues",
      vitalSignals: "Signaux reçus",
      heroChecked: "Vérifié",
      sourceLabel: "Source",
      footerProduct: "Exploité par SBK Techno",
      footerSnapshot: "Instantané",
      footerSource: "Fichier publié",
      never: "Jamais",
      unavailable: "Indisponible",
      staleTitle: "Signal périmé",
      staleNever:
        "Aucun signal de vie n'a encore été reçu. L'état général est présenté comme inconnu et les composantes restent non confirmées.",
      staleAged:
        "Le dernier signal de vie n'est plus valide. L'état général est présenté comme inconnu et les états des composantes peuvent être périmés.",
      errorTitle: "Impossible de lire l'état du service",
      errorText:
        "Le fichier status.json n'a pas pu être chargé. Vérifiez votre connexion, puis réessayez. Les dernières données reçues restent affichées lorsqu'elles existent.",
      retry: "Réessayer",
      noComponents: "Aucune composante déclarée.",
      noIncidents: "Aucun incident déclaré. Le journal est vide pour cet instantané.",
      untitledIncident: "Incident sans titre",
      componentSnapshot: "Instantané",
      componentChecked: "Vérifié",
      componentObserved: "État au",
      incidentStarted: "Début",
      incidentUpdated: "Mis à jour",
    },
    en: {
      docTitle: "Service status · SBK Techno",
      metaDescription: "Public service status for SBK Techno, published from the platform.",
      skipLink: "Skip to content",
      langGroup: "Language",
      brandHome: "SBK Techno, service status",
      pageKicker: "Public information",
      pageTitle: "Service status",
      pageLede:
        "The status of the services provided to SBK Techno clients and staff, published from the platform.",
      pageMetaStatic: "Independent static page",
      aboutLabel: "About",
      overallEyebrow: "Overall status",
      overallLead: "Service status is",
      staticSummary: "No heartbeat received. Service status is unconfirmed.",
      unconfirmedSummary: "Service status is unconfirmed.",
      componentsTitle: "Components",
      incidentsTitle: "Incidents",
      componentsTracked: "tracked",
      incidentsNone: "None",
      vitalsComponents: "components",
      vitalOperational: "Operational",
      vitalDegraded: "Degraded",
      vitalOutage: "Interrupted",
      vitalMaintenance: "Maintenance",
      vitalUnknown: "Unknown",
      vitalSignals: "Signals received",
      heroChecked: "Checked",
      sourceLabel: "Source",
      footerProduct: "Operated by SBK Techno",
      footerSnapshot: "Snapshot",
      footerSource: "Published file",
      never: "Never",
      unavailable: "Unavailable",
      staleTitle: "Stale signal",
      staleNever:
        "No heartbeat has been received yet. The overall state is shown as unknown and the components remain unconfirmed.",
      staleAged:
        "The last heartbeat is no longer valid. The overall state is shown as unknown, and component states may be stale.",
      errorTitle: "Unable to read the service status",
      errorText:
        "The status.json file could not be loaded. Check your connection and try again. The last received data remains visible when available.",
      retry: "Try again",
      noComponents: "No components declared.",
      noIncidents: "No declared incidents. The journal is empty for this snapshot.",
      untitledIncident: "Untitled incident",
      componentSnapshot: "Snapshot",
      componentChecked: "Checked",
      componentObserved: "State at",
      incidentStarted: "Started",
      incidentUpdated: "Updated",
    },
  };

  const STATUS_LABELS = {
    fr: {
      operational: "Opérationnel",
      degraded: "Dégradé",
      partial_outage: "Panne partielle",
      major_outage: "Panne majeure",
      maintenance: "Maintenance",
      unknown: "Inconnu",
    },
    en: {
      operational: "Operational",
      degraded: "Degraded",
      partial_outage: "Partial outage",
      major_outage: "Major outage",
      maintenance: "Maintenance",
      unknown: "Unknown",
    },
  };

  const OVERALL_WORDS = {
    fr: {
      operational: "opérationnel",
      degraded: "dégradé",
      partial_outage: "partiellement en panne",
      major_outage: "en panne",
      maintenance: "en maintenance",
      unknown: "inconnu",
    },
    en: {
      operational: "operational",
      degraded: "degraded",
      partial_outage: "partially down",
      major_outage: "down",
      maintenance: "under maintenance",
      unknown: "unknown",
    },
  };

  const INCIDENT_LABELS = {
    fr: {
      investigating: "Enquête en cours",
      identified: "Cause identifiée",
      monitoring: "Sous surveillance",
      resolved: "Résolu",
    },
    en: {
      investigating: "Investigating",
      identified: "Identified",
      monitoring: "Monitoring",
      resolved: "Resolved",
    },
  };

  const VITAL_KEYS = ["operational", "degraded", "outage", "maintenance", "unknown"];
  const VITAL_GAP = 0.8;

  const state = {
    lang: readLang(),
    data: null,
    hasError: false,
    lastChecked: null,
    fetching: false,
  };

  const els = {
    html: document.documentElement,
    metaDescription: document.querySelector('meta[name="description"]'),
    langButtons: Array.from(document.querySelectorAll("[data-lang]")),
    hero: document.getElementById("hero"),
    heroStatus: document.getElementById("hero-status-text"),
    summary: document.getElementById("overall-summary"),
    heroChecked: document.getElementById("hero-checked"),
    snapshotTime: document.getElementById("snapshot-time"),
    staleBanner: document.getElementById("stale-banner"),
    staleText: document.getElementById("stale-text"),
    errorBanner: document.getElementById("fetch-error"),
    retry: document.getElementById("retry-button"),
    componentsList: document.getElementById("components-list"),
    componentsEmpty: document.getElementById("components-empty"),
    componentsCount: document.getElementById("components-count"),
    incidentsList: document.getElementById("incidents-list"),
    incidentsEmpty: document.getElementById("incidents-empty"),
    incidentsCount: document.getElementById("incidents-count"),
    vitals: document.getElementById("vitals"),
    vitalsTotal: document.getElementById("vitals-total"),
    vitalsNumber: document.getElementById("vitals-number"),
    vitalsSignal: document.getElementById("vitals-signal"),
    vitalsScale: document.getElementById("vitals-scale"),
    vitalsSignals: document.getElementById("vitals-signals"),
    vitalsSignalTotal: document.getElementById("vitals-signal-total"),
    vitalSegments: new Map(),
    vitalCounts: new Map(),
  };

  for (const node of document.querySelectorAll(".vitals__segment")) {
    els.vitalSegments.set(node.getAttribute("data-vital"), node);
  }
  for (const node of document.querySelectorAll(".vitals__count")) {
    els.vitalCounts.set(node.getAttribute("data-count"), node);
  }

  let refreshTimer = null;

  function readLang() {
    try {
      return window.localStorage.getItem(LANG_KEY) === "en" ? "en" : "fr";
    } catch {
      return "fr";
    }
  }

  function t(key) {
    const table = MESSAGES[state.lang] || MESSAGES.fr;
    if (Object.prototype.hasOwnProperty.call(table, key)) return table[key];
    if (Object.prototype.hasOwnProperty.call(MESSAGES.fr, key)) return MESSAGES.fr[key];
    return key;
  }

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function nonEmpty(value) {
    return typeof value === "string" && value.trim() !== "";
  }

  function pick(object, base) {
    const primary = object[base + (state.lang === "en" ? "_en" : "_fr")];
    const fallback = object[base + (state.lang === "en" ? "_fr" : "_en")];
    if (nonEmpty(primary)) return primary;
    if (nonEmpty(fallback)) return fallback;
    return "";
  }

  function parseTimestamp(value) {
    if (typeof value !== "string" || value.trim() === "") return null;
    const ms = Date.parse(value);
    return Number.isFinite(ms) ? ms : null;
  }

  function isoStamp(ms) {
    const wholeSeconds = Math.floor(ms / 1000) * 1000;
    return new Date(wholeSeconds).toISOString().slice(0, -5) + "Z";
  }

  function pad2(value) {
    return String(value).padStart(2, "0");
  }

  function formatStamp(ms) {
    const date = new Date(ms);
    return (
      `${date.getUTCFullYear()}-${pad2(date.getUTCMonth() + 1)}-${pad2(date.getUTCDate())}` +
      ` · ${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())} UTC`
    );
  }

  function normalizeStatus(value) {
    return STATUS_SET.has(value) ? value : "unknown";
  }

  function vitalKey(status) {
    if (status === "partial_outage" || status === "major_outage") return "outage";
    return status;
  }

  function createTime(ms) {
    const time = document.createElement("time");
    const iso = isoStamp(ms);
    time.dateTime = iso;
    time.title = iso;
    time.textContent = formatStamp(ms);
    return time;
  }

  function applyStaticTranslations() {
    for (const node of document.querySelectorAll("[data-i18n]")) {
      setText(node, t(node.getAttribute("data-i18n")));
    }
    for (const node of document.querySelectorAll("[data-i18n-aria-label]")) {
      node.setAttribute("aria-label", t(node.getAttribute("data-i18n-aria-label")));
    }
    if (els.metaDescription) {
      els.metaDescription.setAttribute("content", t("metaDescription"));
    }
    document.title = t("docTitle");
    els.html.lang = state.lang === "fr" ? "fr-CA" : "en-CA";
    for (const button of els.langButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.lang === state.lang));
    }
  }

  function getFreshness(payload) {
    const now = Date.now();
    const generated = parseTimestamp(payload.generated_at);
    const expires = parseTimestamp(payload.expires_at);

    if (generated === null) {
      return { stale: true, reason: "never", generated: null };
    }
    if (expires !== null && expires <= now) {
      return { stale: true, reason: "expired", generated };
    }
    if (now - generated > MAX_AGE_MS) {
      return { stale: true, reason: "aged", generated };
    }
    return { stale: false, reason: "fresh", generated };
  }

  function assertPayload(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new TypeError("Invalid status payload");
    }
    if (payload.version !== 1) {
      throw new TypeError("Unsupported status version");
    }
    if (!Array.isArray(payload.components)) {
      throw new TypeError("Missing components");
    }
    if (payload.incidents != null && !Array.isArray(payload.incidents)) {
      throw new TypeError("Invalid incidents");
    }
    for (const field of ["generated_at", "expires_at"]) {
      const value = payload[field];
      if (value !== null && value !== undefined && parseTimestamp(value) === null) {
        throw new TypeError("Invalid " + field);
      }
    }
  }

  function makePill(status, label) {
    const pill = document.createElement("span");
    pill.className = "pill";
    pill.setAttribute("data-status", status);
    pill.textContent = label;
    return pill;
  }

  function renderErrorBanner() {
    els.errorBanner.hidden = !state.hasError;
  }

  function renderStaleBanner(freshness) {
    const show = Boolean(freshness && freshness.stale);
    if (!show) {
      els.staleBanner.hidden = true;
      return;
    }
    setText(
      els.staleText,
      freshness.reason === "never" ? t("staleNever") : t("staleAged"),
    );
    els.staleBanner.hidden = false;
  }

  function renderStaticState() {
    els.hero.setAttribute("data-status", "unknown");
    setText(els.heroStatus, OVERALL_WORDS[state.lang].unknown);
    setText(els.summary, t("staticSummary"));
  }

  function renderHero(payload, freshness) {
    const status = freshness.stale ? "unknown" : normalizeStatus(payload.overall);
    els.hero.setAttribute("data-status", status);
    setText(els.heroStatus, OVERALL_WORDS[state.lang][status]);
    setText(els.summary, pick(payload, "summary") || t("unconfirmedSummary"));
  }

  function renderSnapshot(payload) {
    if (!els.snapshotTime) return;
    const generated = parseTimestamp(payload.generated_at);
    if (generated === null) return;
    els.snapshotTime.dateTime = isoStamp(generated);
    els.snapshotTime.textContent = formatStamp(generated);
  }

  function updateVitals(total, counts) {
    els.vitals.setAttribute("data-total", String(total));
    setText(els.vitalsTotal, pad2(total));
    setText(els.vitalsNumber, pad2(total));

    if (els.vitalsScale) {
      const gap = total > 0 ? `${VITAL_GAP} ${100 / total - VITAL_GAP}` : "0 100";
      els.vitalsScale.setAttribute("stroke-dasharray", gap);
    }

    let offset = 0;
    for (const key of VITAL_KEYS) {
      const count = counts[key] || 0;
      const length = total > 0 ? (count / total) * 100 : 0;
      const segment = els.vitalSegments.get(key);
      if (segment) {
        segment.setAttribute("stroke-dasharray", `${length} 100`);
        segment.setAttribute("stroke-dashoffset", String(-offset));
      }
      const label = els.vitalCounts.get(key);
      if (label) label.textContent = String(count);
      offset += length;
    }

    const signals = Math.max(0, total - (counts.unknown || 0));
    const signalLength = total > 0 ? (signals / total) * 100 : 0;
    if (els.vitalsSignal) {
      els.vitalsSignal.setAttribute("stroke-dasharray", `${signalLength} 100`);
      els.vitalsSignal.setAttribute("stroke-dashoffset", "0");
    }
    setText(els.vitalsSignals, String(signals));
    setText(els.vitalsSignalTotal, String(total));
  }

  function renderComponents(payload) {
    els.componentsList.replaceChildren();
    const generated = parseTimestamp(payload.generated_at);
    const checked = state.lastChecked;
    const counts = {
      operational: 0,
      degraded: 0,
      outage: 0,
      maintenance: 0,
      unknown: 0,
    };
    let rendered = 0;

    for (const component of payload.components) {
      if (!component || typeof component !== "object") continue;
      const status = normalizeStatus(component.status);
      counts[vitalKey(status)] += 1;
      rendered += 1;

      const item = document.createElement("li");
      item.className = "component";
      item.setAttribute("data-status", status);

      const index = document.createElement("span");
      index.className = "component-index";
      index.setAttribute("aria-hidden", "true");
      index.textContent = pad2(rendered);

      const name = document.createElement("h3");
      name.className = "component-name";
      name.textContent =
        pick(component, "label") ||
        (nonEmpty(component.id) ? component.id : t("unavailable"));

      const detailText = pick(component, "detail");
      const detail = document.createElement("p");
      detail.className = "component-detail";
      detail.textContent = detailText || t("unavailable");

      const side = document.createElement("div");
      side.className = "component-side";
      side.append(makePill(status, STATUS_LABELS[state.lang][status]));

      const meta = document.createElement("p");
      meta.className = "component-meta";
      const metaLabel = document.createElement("span");
      metaLabel.className = "micro";
      metaLabel.textContent = t(generated !== null ? "componentObserved" : "componentChecked");
      meta.append(metaLabel);
      const stamp = generated !== null ? generated : checked;
      if (stamp !== null) {
        meta.append(createTime(stamp));
      } else {
        const missing = document.createElement("span");
        missing.textContent = t("never");
        meta.append(missing);
      }
      side.append(meta);

      item.append(index, name, detail, side);
      els.componentsList.append(item);
    }

    setText(els.componentsCount, pad2(rendered));
    updateVitals(rendered, counts);
    els.componentsEmpty.hidden = rendered > 0;
    if (rendered === 0) setText(els.componentsEmpty, t("noComponents"));
  }

  function addIncidentTime(parent, labelKey, value) {
    const wrap = document.createElement("span");
    const label = document.createElement("span");
    label.className = "micro";
    label.textContent = t(labelKey);
    wrap.append(label);

    const ms = parseTimestamp(value);
    if (ms === null) {
      const missing = document.createElement("span");
      missing.textContent = t("unavailable");
      wrap.append(missing);
    } else {
      wrap.append(createTime(ms));
    }

    parent.append(wrap);
  }

  function renderIncidents(payload) {
    els.incidentsList.replaceChildren();
    const incidents = Array.isArray(payload.incidents)
      ? payload.incidents.filter((incident) => incident && typeof incident === "object")
      : [];

    if (incidents.length === 0) {
      els.incidentsEmpty.hidden = false;
      setText(els.incidentsEmpty, t("noIncidents"));
      setText(els.incidentsCount, t("incidentsNone"));
      return;
    }

    els.incidentsEmpty.hidden = true;
    setText(els.incidentsCount, pad2(incidents.length));
    const sorted = [
      ...incidents.filter((incident) => incident.status !== "resolved"),
      ...incidents.filter((incident) => incident.status === "resolved"),
    ];

    for (const incident of sorted) {
      const status = INCIDENT_STATUS_SET.has(incident.status) ? incident.status : "unknown";
      const item = document.createElement("li");
      item.className = "incident";
      item.setAttribute("data-status", status);

      const timeCol = document.createElement("div");
      timeCol.className = "incident-time";
      const startLabel = document.createElement("span");
      startLabel.className = "micro";
      startLabel.textContent = t("incidentStarted");
      timeCol.append(startLabel);
      const startMs = parseTimestamp(incident.started_at);
      if (startMs !== null) {
        timeCol.append(createTime(startMs));
      } else {
        const missing = document.createElement("span");
        missing.textContent = t("unavailable");
        timeCol.append(missing);
      }

      const rail = document.createElement("div");
      rail.className = "incident-rail";
      rail.setAttribute("aria-hidden", "true");
      const node = document.createElement("span");
      node.className = "incident-node";
      rail.append(node);

      const body = document.createElement("div");
      body.className = "incident-body";

      const head = document.createElement("div");
      head.className = "incident-head";

      const title = document.createElement("h3");
      title.className = "incident-title";
      title.textContent = pick(incident, "title") || t("untitledIncident");

      const label = INCIDENT_LABELS[state.lang][status] || STATUS_LABELS[state.lang].unknown;
      head.append(title, makePill(status, label));

      const meta = document.createElement("div");
      meta.className = "incident-meta";
      addIncidentTime(meta, "incidentUpdated", incident.updated_at);

      body.append(head);
      const summaryText = pick(incident, "summary");
      if (summaryText) {
        const summary = document.createElement("p");
        summary.className = "incident-summary";
        summary.textContent = summaryText;
        body.append(summary);
      }
      body.append(meta);

      item.append(timeCol, rail, body);
      els.incidentsList.append(item);
    }
  }

  function renderChecked() {
    if (state.lastChecked === null) return;
    if (!els.heroChecked) return;
    els.heroChecked.dateTime = isoStamp(state.lastChecked);
    els.heroChecked.textContent = formatStamp(state.lastChecked);
  }

  function renderAll() {
    applyStaticTranslations();
    const freshness = state.data ? getFreshness(state.data) : null;

    renderErrorBanner();
    renderStaleBanner(freshness);

    if (state.data) {
      renderHero(state.data, freshness);
      renderSnapshot(state.data);
      renderComponents(state.data);
      renderIncidents(state.data);
    } else {
      renderStaticState();
    }

    renderChecked();
  }

  async function refresh() {
    if (state.fetching) return;
    state.fetching = true;
    if (els.retry) els.retry.disabled = true;

    let timeoutId = null;
    try {
      const controller = new AbortController();
      timeoutId = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

      const url = new URL(STATUS_URL, window.location.href);
      url.searchParams.set("t", Date.now().toString(36));
      const response = await fetch(url, {
        cache: "no-store",
        credentials: "omit",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const payload = await response.json();
      assertPayload(payload);
      state.data = payload;
      state.hasError = false;
    } catch {
      state.hasError = true;
    } finally {
      if (timeoutId !== null) window.clearTimeout(timeoutId);
      state.fetching = false;
      state.lastChecked = Date.now();
      if (els.retry) els.retry.disabled = false;
      renderAll();
    }
  }

  function stopPolling() {
    if (refreshTimer !== null) {
      window.clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  function startPolling() {
    stopPolling();
    refreshTimer = window.setInterval(() => {
      if (!document.hidden) refresh();
    }, REFRESH_MS);
  }

  function handleVisibilityChange() {
    if (document.hidden) {
      stopPolling();
      return;
    }
    startPolling();
    refresh();
  }

  function setLang(lang) {
    if (lang !== "fr" && lang !== "en") return;
    if (lang !== state.lang) {
      state.lang = lang;
      try {
        window.localStorage.setItem(LANG_KEY, lang);
      } catch {
        // Storage can be unavailable in private browsing; the page still works.
      }
    }
    renderAll();
  }

  function bind() {
    for (const button of els.langButtons) {
      button.addEventListener("click", () => setLang(button.dataset.lang));
    }
    if (els.retry) els.retry.addEventListener("click", () => refresh());
    document.addEventListener("visibilitychange", handleVisibilityChange);
  }

  function init() {
    if (!els.hero) return;
    bind();
    applyStaticTranslations();
    renderStaticState();
    renderChecked();
    if (!document.hidden) startPolling();
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
