"use strict";

(() => {
  const LANG_KEY = "sbk.status.lang";
  const STATUS_URL = "status.json";
  const REFRESH_MS = 60_000;
  const CLOCK_MS = 30_000;
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
      overallEyebrow: "État général",
      lastUpdate: "Dernière mise à jour",
      componentsTitle: "Composantes",
      incidentsTitle: "Incidents",
      loadingSummary: "Lecture du dernier signal publié.",
      errorSummary: "L'état du service est momentanément indisponible.",
      unconfirmedSummary: "L'état du service n'est pas confirmé.",
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
      loadingComponents: "Chargement des composantes…",
      loadingIncidents: "Chargement des incidents…",
      loadFailedComponents: "Les composantes n'ont pas pu être chargées.",
      loadFailedIncidents: "Les incidents n'ont pas pu être chargés.",
      noComponents: "Aucune composante déclarée.",
      noIncidents: "Aucun incident déclaré.",
      untitledIncident: "Incident sans titre",
      staleFlag: "Signal périmé",
      incidentStarted: "Début",
      incidentUpdated: "Mis à jour",
      checkedLabel: "Dernière vérification",
      checkedNever: "Pas encore vérifié",
      refreshNote: "Actualisation automatique toutes les 60 secondes.",
      footerSource: "Fichier publié",
      footerBlurb:
        "Page d'état publique de SBK Techno. Les données sont publiées par la plateforme sous forme de fichier statique.",
      footerPrivacy: "Aucun cookie, aucun outil de mesure, aucune ressource externe.",
      footerHost: "Hébergé sur GitHub Pages",
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
      overallEyebrow: "Overall status",
      lastUpdate: "Last updated",
      componentsTitle: "Components",
      incidentsTitle: "Incidents",
      loadingSummary: "Reading the latest published signal.",
      errorSummary: "Service status is temporarily unavailable.",
      unconfirmedSummary: "Service status is unconfirmed.",
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
      loadingComponents: "Loading components…",
      loadingIncidents: "Loading incidents…",
      loadFailedComponents: "Components could not be loaded.",
      loadFailedIncidents: "Incidents could not be loaded.",
      noComponents: "No components declared.",
      noIncidents: "No declared incidents.",
      untitledIncident: "Untitled incident",
      staleFlag: "Stale signal",
      incidentStarted: "Started",
      incidentUpdated: "Updated",
      checkedLabel: "Last checked",
      checkedNever: "Not checked yet",
      refreshNote: "Automatically refreshed every 60 seconds.",
      footerSource: "Published file",
      footerBlurb:
        "Public status page for SBK Techno. Data is published by the platform as a static file.",
      footerPrivacy: "No cookies, no analytics, no external assets.",
      footerHost: "Hosted on GitHub Pages",
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

  const OVERALL_LABELS = {
    fr: {
      operational: "Opérationnel",
      degraded: "Service dégradé",
      partial_outage: "Panne partielle",
      major_outage: "Panne majeure",
      maintenance: "Maintenance en cours",
      unknown: "État inconnu",
    },
    en: {
      operational: "Operational",
      degraded: "Degraded service",
      partial_outage: "Partial outage",
      major_outage: "Major outage",
      maintenance: "Maintenance in progress",
      unknown: "Status unknown",
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

  const FR_UNITS = {
    second: ["seconde", "secondes"],
    minute: ["minute", "minutes"],
    hour: ["heure", "heures"],
    day: ["jour", "jours"],
  };

  const EN_UNITS = {
    second: ["second", "seconds"],
    minute: ["minute", "minutes"],
    hour: ["hour", "hours"],
    day: ["day", "days"],
  };

  const RTF_CACHE = {};

  const state = {
    lang: readLang(),
    data: null,
    hasError: false,
    lastChecked: null,
    stale: false,
    fetching: false,
  };

  const els = {
    html: document.documentElement,
    metaDescription: document.querySelector('meta[name="description"]'),
    langButtons: Array.from(document.querySelectorAll("[data-lang]")),
    hero: document.getElementById("hero"),
    heroIcon: document.querySelector("#hero .status-icon use"),
    heroStatus: document.getElementById("hero-status-text"),
    summary: document.getElementById("overall-summary"),
    heroUpdated: document.getElementById("hero-updated-value"),
    staleBanner: document.getElementById("stale-banner"),
    staleText: document.getElementById("stale-text"),
    errorBanner: document.getElementById("fetch-error"),
    retry: document.getElementById("retry-button"),
    componentsList: document.getElementById("components-list"),
    componentsEmpty: document.getElementById("components-empty"),
    incidentsList: document.getElementById("incidents-list"),
    incidentsEmpty: document.getElementById("incidents-empty"),
    checked: document.getElementById("checked-value"),
  };

  let refreshTimer = null;
  let clockTimer = null;

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
    const iso = new Date(ms).toISOString();
    return iso.endsWith(".000Z") ? iso.slice(0, -5) + "Z" : iso;
  }

  function normalizeStatus(value) {
    return STATUS_SET.has(value) ? value : "unknown";
  }

  function relativeFormatter() {
    if (Object.prototype.hasOwnProperty.call(RTF_CACHE, state.lang)) {
      return RTF_CACHE[state.lang];
    }
    let formatter = null;
    try {
      formatter = new Intl.RelativeTimeFormat(state.lang === "fr" ? "fr-CA" : "en-CA", {
        numeric: "auto",
        style: "long",
      });
    } catch {
      formatter = null;
    }
    RTF_CACHE[state.lang] = formatter;
    return formatter;
  }

  function formatRelative(ms) {
    const diff = Math.max(0, Date.now() - ms);
    let value;
    let unit;

    if (diff < 45_000) {
      value = 0;
      unit = "second";
    } else if (diff < 59.5 * 60_000) {
      value = -Math.max(1, Math.round(diff / 60_000));
      unit = "minute";
    } else if (diff < 23.5 * 3_600_000) {
      value = -Math.max(1, Math.round(diff / 3_600_000));
      unit = "hour";
    } else {
      value = -Math.max(1, Math.round(diff / 86_400_000));
      unit = "day";
    }

    const formatter = relativeFormatter();
    if (formatter) {
      try {
        return formatter.format(value, unit);
      } catch {
        // Fall through to the manual form.
      }
    }

    if (value === 0) return state.lang === "fr" ? "à l'instant" : "just now";
    const count = Math.abs(value);
    const units = state.lang === "fr" ? FR_UNITS : EN_UNITS;
    const label = units[unit][count === 1 ? 0 : 1];
    return state.lang === "fr" ? `il y a ${count} ${label}` : `${count} ${label} ago`;
  }

  function createTime(ms) {
    const time = document.createElement("time");
    const iso = isoStamp(ms);
    time.dateTime = iso;
    time.title = iso;
    time.dataset.ts = String(ms);
    time.textContent = formatRelative(ms);
    return time;
  }

  function updateTimes() {
    for (const node of document.querySelectorAll("time[data-ts]")) {
      const ms = Number(node.dataset.ts);
      if (Number.isFinite(ms)) node.textContent = formatRelative(ms);
    }
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

  function renderUpdated(container, ms, fallbackKey) {
    container.replaceChildren();
    if (ms === null) {
      const fallback = document.createElement("span");
      fallback.textContent = t(fallbackKey);
      container.append(fallback);
      return;
    }
    container.append(createTime(ms));
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

  function renderHero(payload, freshness) {
    const status = freshness.stale ? "unknown" : normalizeStatus(payload.overall);
    els.hero.setAttribute("data-status", status);
    els.heroIcon.setAttribute("href", "#icon-" + status);
    setText(els.heroStatus, OVERALL_LABELS[state.lang][status]);
    setText(els.summary, pick(payload, "summary") || t("unconfirmedSummary"));

    const updated = freshness.stale
      ? freshness.generated
      : parseTimestamp(payload.generated_at);
    renderUpdated(els.heroUpdated, updated, "never");
  }

  function renderComponents(payload, stale) {
    els.componentsList.replaceChildren();
    let rendered = 0;

    for (const component of payload.components) {
      if (!component || typeof component !== "object") continue;
      const status = normalizeStatus(component.status);
      const item = document.createElement("li");
      item.className = "component";

      const main = document.createElement("div");
      main.className = "component-main";

      const name = document.createElement("h3");
      name.className = "component-name";
      name.textContent =
        pick(component, "label") ||
        (nonEmpty(component.id) ? component.id : t("unavailable"));

      const detailText = pick(component, "detail");
      if (detailText) {
        const detail = document.createElement("p");
        detail.className = "component-detail";
        detail.textContent = detailText;
        main.append(name, detail);
      } else {
        main.append(name);
      }

      const side = document.createElement("div");
      side.className = "component-side";
      side.append(makePill(status, STATUS_LABELS[state.lang][status]));

      if (stale) {
        const flag = document.createElement("span");
        flag.className = "stale-flag";
        flag.textContent = t("staleFlag");
        side.append(flag);
      }

      item.append(main, side);
      els.componentsList.append(item);
      rendered += 1;
    }

    els.componentsEmpty.hidden = rendered > 0;
    if (rendered === 0) setText(els.componentsEmpty, t("noComponents"));
  }

  function addIncidentTime(parent, labelKey, value) {
    const wrap = document.createElement("span");
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = t(labelKey);
    wrap.append(label, document.createTextNode(" "));

    const ms = parseTimestamp(value);
    if (ms === null) {
      const missing = document.createElement("span");
      missing.textContent = t("unavailable");
      wrap.append(missing);
    } else {
      wrap.append(createTime(ms));
    }

    parent.append(wrap, document.createTextNode(" "));
  }

  function renderIncidents(payload) {
    els.incidentsList.replaceChildren();
    const incidents = Array.isArray(payload.incidents)
      ? payload.incidents.filter((incident) => incident && typeof incident === "object")
      : [];

    if (incidents.length === 0) {
      els.incidentsEmpty.hidden = false;
      setText(els.incidentsEmpty, t("noIncidents"));
      return;
    }

    els.incidentsEmpty.hidden = true;
    const sorted = [
      ...incidents.filter((incident) => incident.status !== "resolved"),
      ...incidents.filter((incident) => incident.status === "resolved"),
    ];

    for (const incident of sorted) {
      const status = INCIDENT_STATUS_SET.has(incident.status) ? incident.status : "unknown";
      const item = document.createElement("li");
      item.className = "incident";
      item.setAttribute("data-status", status);

      const head = document.createElement("div");
      head.className = "incident-head";

      const title = document.createElement("h3");
      title.className = "incident-title";
      title.textContent = pick(incident, "title") || t("untitledIncident");

      const label = INCIDENT_LABELS[state.lang][status] || STATUS_LABELS[state.lang].unknown;
      head.append(title, makePill(status, label));

      const summaryText = pick(incident, "summary");
      const meta = document.createElement("div");
      meta.className = "incident-meta";
      addIncidentTime(meta, "incidentStarted", incident.started_at);
      addIncidentTime(meta, "incidentUpdated", incident.updated_at);

      item.append(head);
      if (summaryText) {
        const summary = document.createElement("p");
        summary.className = "incident-summary";
        summary.textContent = summaryText;
        item.append(summary);
      }
      item.append(meta);
      els.incidentsList.append(item);
    }
  }

  function renderPlaceholder() {
    els.hero.setAttribute("data-status", "unknown");
    els.heroIcon.setAttribute("href", "#icon-unknown");
    setText(els.heroStatus, OVERALL_LABELS[state.lang].unknown);
    setText(els.summary, state.hasError ? t("errorSummary") : t("loadingSummary"));
    renderUpdated(els.heroUpdated, null, state.hasError ? "unavailable" : "never");

    els.componentsList.replaceChildren();
    els.componentsEmpty.hidden = false;
    setText(
      els.componentsEmpty,
      state.hasError ? t("loadFailedComponents") : t("loadingComponents"),
    );

    els.incidentsList.replaceChildren();
    els.incidentsEmpty.hidden = false;
    setText(
      els.incidentsEmpty,
      state.hasError ? t("loadFailedIncidents") : t("loadingIncidents"),
    );
  }

  function renderChecked() {
    els.checked.replaceChildren();
    if (state.lastChecked === null) {
      els.checked.textContent = t("checkedNever");
      return;
    }
    els.checked.append(createTime(state.lastChecked));
  }

  function renderAll() {
    applyStaticTranslations();
    const freshness = state.data ? getFreshness(state.data) : null;
    state.stale = Boolean(freshness && freshness.stale);

    renderErrorBanner();
    renderStaleBanner(freshness);

    if (state.data) {
      renderHero(state.data, freshness);
      renderComponents(state.data, state.stale);
      renderIncidents(state.data);
    } else {
      renderPlaceholder();
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

  function tick() {
    if (document.hidden) return;
    updateTimes();
    if (!state.data) return;
    const freshness = getFreshness(state.data);
    if (freshness.stale !== state.stale) renderAll();
  }

  function stopPolling() {
    if (refreshTimer !== null) {
      window.clearInterval(refreshTimer);
      refreshTimer = null;
    }
    if (clockTimer !== null) {
      window.clearInterval(clockTimer);
      clockTimer = null;
    }
  }

  function startPolling() {
    stopPolling();
    refreshTimer = window.setInterval(() => {
      if (!document.hidden) refresh();
    }, REFRESH_MS);
    clockTimer = window.setInterval(tick, CLOCK_MS);
  }

  function handleVisibilityChange() {
    if (document.hidden) {
      stopPolling();
      return;
    }
    startPolling();
    refresh();
    tick();
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
