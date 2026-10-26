"use strict";

(() => {
  const model = window.StatusModel;
  if (!model) return;

  document.documentElement.classList.remove("no-js");

  const STATUS_URL = "status.json";
  const REFRESH_MS = 60_000;
  const FETCH_TIMEOUT_MS = 4_000;
  const ERROR_GRACE_MS = 9_000;
  const RETRY_DELAY_MS = 500;
  const MAX_ATTEMPTS = 2;

  const INCIDENT_STATUSES = ["investigating", "identified", "monitoring", "resolved"];
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
      pageMetaStatic: "Page statique indépendante",
      overallEyebrow: "État général",
      overallLead: "L'état du service est",
      loadingWord: "en attente",
      loadingSummary: "Lecture de l'état publié dans status.json.",
      notDisplayed: "L'état publié n'est pas affiché.",
      errorSummary: "La lecture a échoué. Le service n'est pas confirmé en panne.",
      unknownNeverSummary:
        "Aucun signal reçu : le service n'est pas confirmé en panne, et la page se met à jour dès qu'un signal arrive.",
      unknownStaleSummary:
        "Le dernier signal est trop ancien : le service n'est pas confirmé en panne, et la page se met à jour dès qu'un nouveau signal arrive.",
      unknownFutureSummary:
        "L'horodatage du signal est incohérent : le service n'est pas confirmé en panne, et la page se met à jour dès qu'un signal valide arrive.",
      confirmedSummary: "L'état du service est confirmé par le dernier signal.",
      componentsTitle: "Composantes",
      componentsTracked: "suivies",
      componentsTrackedOne: "suivie",
      componentsLoading: "Lecture de l'état publié...",
      componentsCountLoading: "Lecture",
      componentsCountError: "Indisponible",
      componentsNoteFresh: "États issus du dernier signal.",
      componentsNoteNever: "Les états ci-dessous restent inconnus.",
      componentsNoteStale: "Les états ci-dessous ne sont pas confirmés.",
      componentsNoteFuture: "Les états ci-dessous ne sont pas confirmés.",
      componentsNoteLoading: "Lecture de l'état publié...",
      incidentsTitle: "Incidents",
      incidentsNone: "Aucun",
      vitalsComponents: "composantes",
      vitalOperational: "Opérationnelles",
      vitalDegraded: "Dégradées",
      vitalOutage: "Interrompues",
      vitalMaintenance: "Maintenance",
      vitalUnknown: "Inconnues",
      vitalSignals: "Signaux reçus",
      heroSignal: "Dernier signal :",
      heroChecked: "Dernière lecture :",
      sourceLabel: "Source :",
      footerProduct: "Exploité par SBK Techno",
      footerSnapshot: "Instantané",
      footerSource: "Fichier publié",
      noSnapshot: "Aucun",
      unavailable: "Indisponible",
      unknown: "Inconnu",
      staleTitle: "Signal périmé",
      staleNeverTitle: "Aucun signal reçu",
      staleFutureTitle: "Horodatage incohérent",
      staleNeverText:
        "L'état général et les composantes restent inconnus.",
      staleAgedText:
        "L'état général et les composantes restent inconnus.",
      staleFutureText:
        "Le signal publié est daté dans le futur. Il n'est pas utilisé comme état courant.",
      errorTitle: "Impossible de lire l'état du service",
      errorText:
        "Le fichier status.json n'a pas répondu. Vérifiez votre connexion, puis réessayez.",
      retry: "Réessayer",
      noComponents: "Aucune composante déclarée.",
      noIncidents: "Aucun incident déclaré. Le journal est vide pour cet instantané.",
      untitledIncident: "Incident sans titre",
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
      pageMetaStatic: "Independent static page",
      overallEyebrow: "Overall status",
      overallLead: "Service status is",
      loadingWord: "waiting",
      loadingSummary: "Reading the published status from status.json.",
      notDisplayed: "The published status is not displayed.",
      errorSummary: "The read failed. The service is not confirmed down.",
      unknownNeverSummary:
        "No signal received: the service is not confirmed down, and the page updates when a signal arrives.",
      unknownStaleSummary:
        "The last signal is too old: the service is not confirmed down, and the page updates when a new signal arrives.",
      unknownFutureSummary:
        "The signal timestamp is inconsistent: the service is not confirmed down, and the page updates when a valid signal arrives.",
      confirmedSummary: "The service state is confirmed by the last signal.",
      componentsTitle: "Components",
      componentsTracked: "tracked",
      componentsTrackedOne: "tracked",
      componentsLoading: "Reading the published status...",
      componentsCountLoading: "Reading",
      componentsCountError: "Unavailable",
      componentsNoteFresh: "States from the last signal.",
      componentsNoteNever: "The states below remain unknown.",
      componentsNoteStale: "The states below are not confirmed.",
      componentsNoteFuture: "The states below are not confirmed.",
      componentsNoteLoading: "Reading the published status...",
      incidentsTitle: "Incidents",
      incidentsNone: "None",
      vitalsComponents: "components",
      vitalOperational: "Operational",
      vitalDegraded: "Degraded",
      vitalOutage: "Interrupted",
      vitalMaintenance: "Maintenance",
      vitalUnknown: "Unknown",
      vitalSignals: "Signals received",
      heroSignal: "Last signal:",
      heroChecked: "Last read:",
      sourceLabel: "Source:",
      footerProduct: "Operated by SBK Techno",
      footerSnapshot: "Snapshot",
      footerSource: "Published file",
      noSnapshot: "None",
      unavailable: "Unavailable",
      unknown: "Unknown",
      staleTitle: "Stale signal",
      staleNeverTitle: "No signal received",
      staleFutureTitle: "Inconsistent timestamp",
      staleNeverText:
        "The overall state and the components remain unknown.",
      staleAgedText:
        "The overall state and the components remain unknown.",
      staleFutureText:
        "The published signal is dated in the future. It is not used as the current state.",
      errorTitle: "Unable to read the service status",
      errorText:
        "The status.json file did not respond. Check your connection and try again.",
      retry: "Try again",
      noComponents: "No components declared.",
      noIncidents: "No declared incidents. The journal is empty for this snapshot.",
      untitledIncident: "Untitled incident",
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
  const UNKNOWN_DASH = 1.2;
  const UNKNOWN_GAP = 2;

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
    heroSignal: document.getElementById("hero-signal"),
    heroChecked: document.getElementById("hero-checked"),
    snapshotTime: document.getElementById("snapshot-time"),
    staleBanner: document.getElementById("stale-banner"),
    staleTitle: document.getElementById("stale-title"),
    staleText: document.getElementById("stale-text"),
    errorBanner: document.getElementById("fetch-error"),
    retry: document.getElementById("retry-button"),
    componentsList: document.getElementById("components-list"),
    componentsNote: document.getElementById("components-note"),
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
    vitalsSignalValue: document.querySelector(".vitals__signal-line .vitals__signal-value"),
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
    const requested = new URLSearchParams(window.location.search).get("lang");
    return requested === "en" ? "en" : "fr";
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

  function vitalKey(status) {
    if (status === "partial_outage" || status === "major_outage") return "outage";
    return status;
  }

  function setStamp(node, ms) {
    if (!node) return;
    const iso = isoStamp(ms);
    if (node instanceof HTMLTimeElement) node.dateTime = iso;
    node.title = iso;
    node.textContent = formatStamp(ms);
  }

  function createTime(ms) {
    const time = document.createElement("time");
    setStamp(time, ms);
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
      if (value === null || value === undefined || value === "") continue;
      if (typeof value !== "string" || model.parseTimestamp(value) === null) {
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

  function renderErrorBanner(visible) {
    els.errorBanner.hidden = !visible;
  }

  function renderStaleBanner(current) {
    if (current.banner !== "stale") {
      els.staleBanner.hidden = true;
      return;
    }
    setText(els.staleTitle, t(current.bannerTitle));
    setText(els.staleText, t(current.bannerText));
    els.staleBanner.setAttribute("data-reason", current.reason);
    els.staleBanner.hidden = false;
  }

  function renderSignalTime(current) {
    for (const node of [els.heroSignal, els.snapshotTime]) {
      if (!node) continue;
      if (current.snapshot === "none") {
        setText(node, t("noSnapshot"));
        node.removeAttribute("title");
      } else if (current.snapshot === "generated" && current.generated !== null) {
        setStamp(node, current.generated);
      } else {
        setText(node, t("unknown"));
        node.removeAttribute("title");
      }
    }
  }

  function renderUnavailable(current) {
    const loading = current.mode === "loading";
    els.hero.setAttribute("data-status", "unknown");
    els.hero.setAttribute("data-mode", current.mode);
    setText(els.heroStatus, loading ? t("loadingWord") : OVERALL_WORDS[state.lang].unknown);
    setText(els.summary, t(current.summary));
    renderSignalTime(current);
    setText(
      els.componentsCount,
      loading ? t("componentsCountLoading") : t("componentsCountError"),
    );
    setText(
      els.incidentsCount,
      loading ? t("componentsCountLoading") : t("componentsCountError"),
    );
    els.componentsList.replaceChildren();
    els.incidentsList.replaceChildren();
    if (els.componentsNote) els.componentsNote.hidden = true;
    els.componentsEmpty.hidden = false;
    setText(els.componentsEmpty, loading ? t("componentsLoading") : t("notDisplayed"));
    els.incidentsEmpty.hidden = false;
    setText(els.incidentsEmpty, loading ? t("componentsLoading") : t("notDisplayed"));
    els.vitals.hidden = true;
  }

  function renderHero(payload, current) {
    els.hero.setAttribute("data-status", current.headline);
    els.hero.setAttribute("data-mode", current.mode);
    setText(els.heroStatus, OVERALL_WORDS[state.lang][current.headline]);
    setText(
      els.summary,
      current.summary === "payload"
        ? pick(payload, "summary") || t("confirmedSummary")
        : t(current.summary),
    );
  }

  function setSignalValue(signals, total) {
    if (!els.vitalsSignalValue) return;
    if (signals === null) {
      els.vitalsSignalValue.replaceChildren(t("unknown"));
      return;
    }
    const value = document.createElement("span");
    value.id = "vitals-signals";
    value.textContent = String(signals);
    const totalNode = document.createElement("span");
    totalNode.id = "vitals-signal-total";
    totalNode.textContent = String(total);
    els.vitalsSignalValue.replaceChildren(value, "/", totalNode);
  }

  function dashedArc(length) {
    if (length <= 0) return "0 100";
    if (length >= 99.999) return `${UNKNOWN_DASH} ${UNKNOWN_GAP}`;
    const parts = [];
    let remaining = length;
    while (remaining > 0.001) {
      const dash = Math.min(UNKNOWN_DASH, remaining);
      parts.push(dash.toFixed(3).replace(/\.?0+$/, ""));
      remaining -= dash;
      if (remaining <= 0.001) break;
      const gap = Math.min(UNKNOWN_GAP, remaining);
      parts.push(gap.toFixed(3).replace(/\.?0+$/, ""));
      remaining -= gap;
    }
    const tail = 100 - length;
    if (tail > 0.001) parts.push(tail.toFixed(3).replace(/\.?0+$/, ""));
    return parts.join(" ");
  }

  function updateVitals(total, counts, signalMode) {
    els.vitals.setAttribute("data-total", String(total));
    setText(els.vitalsTotal, String(total));
    setText(els.vitalsNumber, String(total));

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
        segment.setAttribute(
          "stroke-dasharray",
          key === "unknown" ? dashedArc(length) : `${length} 100`,
        );
        segment.setAttribute("stroke-dashoffset", String(-offset));
      }
      const label = els.vitalCounts.get(key);
      if (label) label.textContent = String(count);
      offset += length;
    }

    const signals =
      signalMode === "count" ? Math.max(0, total - (counts.unknown || 0)) : null;
    const signalLength =
      signals !== null && total > 0 ? (signals / total) * 100 : 0;
    if (els.vitalsSignal) {
      els.vitalsSignal.setAttribute("stroke-dasharray", `${signalLength} 100`);
      els.vitalsSignal.setAttribute("stroke-dashoffset", "0");
    }
    setSignalValue(signals, total);
  }

  function renderComponents(payload, current) {
    els.componentsList.replaceChildren();
    const forced = current.forceUnknown;
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
      const status = forced ? "unknown" : model.normalizeStatus(component.status);
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

      const side = document.createElement("div");
      side.className = "component-side";
      side.append(makePill(status, STATUS_LABELS[state.lang][status]));

      item.append(index, name, side);
      els.componentsList.append(item);
    }

    setText(
      els.componentsCount,
      `${rendered} ${t(rendered === 1 ? "componentsTrackedOne" : "componentsTracked")}`,
    );
    updateVitals(rendered, counts, current.signal);
    els.componentsEmpty.hidden = rendered > 0;
    if (rendered === 0) setText(els.componentsEmpty, t("noComponents"));

    if (rendered > 0 && current.note && els.componentsNote) {
      setText(els.componentsNote, t(current.note));
      els.componentsNote.hidden = false;
    } else if (els.componentsNote) {
      els.componentsNote.hidden = true;
    }
  }

  function addIncidentTime(parent, labelKey, value) {
    const wrap = document.createElement("span");
    const label = document.createElement("span");
    label.className = "micro";
    label.textContent = t(labelKey);
    wrap.append(label);

    const ms = model.parseTimestamp(value);
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
      const startMs = model.parseTimestamp(incident.started_at);
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
    if (state.lastChecked === null) {
      setText(els.heroChecked, t("unknown"));
      return;
    }
    // The label is "Dernière lecture", so it reports when the last successful
    // read happened, even when that read carried no usable heartbeat. It is
    // not a claim that the heartbeat is current.
    setStamp(els.heroChecked, state.lastChecked);
  }

  function renderAll() {
    applyStaticTranslations();
    const current = model.presentation(state.data, state.hasError, Date.now());

    renderErrorBanner(current.banner === "error");
    renderStaleBanner(current);

    if (current.showData) {
      renderHero(state.data, current);
      renderSignalTime(current);
      renderComponents(state.data, current);
      renderIncidents(state.data);
      els.vitals.hidden = !current.showVitals;
    } else {
      renderUnavailable(current);
    }

    renderChecked();
  }

  function wait(ms) {
    return new Promise((resolve) => {
      window.setTimeout(resolve, ms);
    });
  }

  async function fetchOnce() {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try {
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
      return payload;
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  async function refresh({ manual = false } = {}) {
    if (state.fetching) return;
    state.fetching = true;
    if (els.retry) els.retry.disabled = true;

    if (manual) {
      // A retry starts a new read. The error state must not keep the previous
      // payload on screen while the new attempt is in flight.
      state.data = null;
      state.hasError = false;
      renderAll();
    }

    const started = Date.now();
    let attempt = 0;
    try {
      while (true) {
        attempt += 1;
        try {
          const payload = await fetchOnce();
          state.data = payload;
          state.hasError = false;
          // Only a successful read may move the check time. A failed attempt
          // leaves the last successful read visible, or unknown before the first.
          state.lastChecked = Date.now();
          return;
        } catch {
          const elapsed = Date.now() - started;
          if (attempt < MAX_ATTEMPTS && elapsed < ERROR_GRACE_MS) {
            await wait(RETRY_DELAY_MS);
            continue;
          }
          const remaining = ERROR_GRACE_MS - elapsed;
          if (remaining > 0) await wait(remaining);
          state.hasError = true;
          return;
        }
      }
    } finally {
      state.fetching = false;
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
      const url = new URL(window.location.href);
      if (lang === "en") url.searchParams.set("lang", "en");
      else url.searchParams.delete("lang");
      window.history.replaceState(null, "", url);
    }
    renderAll();
  }

  function bind() {
    for (const button of els.langButtons) {
      button.addEventListener("click", () => setLang(button.dataset.lang));
    }
    if (els.retry) {
      els.retry.addEventListener("click", () => {
        refresh({ manual: true });
      });
    }
    document.addEventListener("visibilitychange", handleVisibilityChange);
  }

  function init() {
    if (!els.hero) return;
    bind();
    renderAll();
    if (!document.hidden) startPolling();
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
