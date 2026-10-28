#!/usr/bin/env python3
"""Render the publish-time static snapshot of the public status document.

The snapshot is the page a visitor receives before any JavaScript runs. It is
rendered from the exact bytes of ``status.json`` while the publisher holds
them, and it records the document digest and the instant the freshness rules
were applied. The validator re-runs this module against the committed document
and rejects any snapshot that claims more than the heartbeat supports.

The freshness rules mirror ``status-model.js``. ``snapshot_contract_test.mjs``
compares both implementations over the shared fixture list.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timedelta, timezone

MAX_AGE = timedelta(minutes=15)
FUTURE_SKEW = timedelta(minutes=5)
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

STATUSES = (
    "operational",
    "degraded",
    "partial_outage",
    "major_outage",
    "maintenance",
    "unknown",
)
STATUS_LABELS_FR = {
    "operational": "Opérationnel",
    "degraded": "Dégradé",
    "partial_outage": "Panne partielle",
    "major_outage": "Panne majeure",
    "maintenance": "Maintenance",
    "unknown": "Inconnu",
}
OVERALL_WORDS_FR = {
    "operational": "opérationnel",
    "degraded": "dégradé",
    "partial_outage": "partiellement en panne",
    "major_outage": "en panne",
    "maintenance": "en maintenance",
    "unknown": "inconnu",
}
INCIDENT_LABELS_FR = {
    "investigating": "Enquête en cours",
    "identified": "Cause identifiée",
    "monitoring": "Sous surveillance",
    "resolved": "Résolu",
}
SUMMARY_FR = {
    "fresh": None,
    "never": (
        "Aucun signal reçu : le service n'est pas confirmé en panne, et la page "
        "se met à jour dès qu'un signal arrive."
    ),
    "future": (
        "L'horodatage du signal est incohérent : le service n'est pas confirmé "
        "en panne, et la page se met à jour dès qu'un signal valide arrive."
    ),
    "expired": (
        "Le dernier signal est trop ancien : le service n'est pas confirmé en "
        "panne, et la page se met à jour dès qu'un nouveau signal arrive."
    ),
    "aged": (
        "Le dernier signal est trop ancien : le service n'est pas confirmé en "
        "panne, et la page se met à jour dès qu'un nouveau signal arrive."
    ),
}
NOTE_FR = {
    "fresh": "États issus du dernier signal.",
    "never": "Les états ci-dessous restent inconnus.",
    "future": "Les états ci-dessous ne sont pas confirmés.",
    "expired": "Les états ci-dessous ne sont pas confirmés.",
    "aged": "Les états ci-dessous ne sont pas confirmés.",
}
STALE_TITLE_FR = {
    "never": "Aucun signal reçu",
    "future": "Horodatage incohérent",
    "expired": "Signal périmé",
    "aged": "Signal périmé",
}
STALE_TEXT_FR = {
    "never": "L'état général et les composantes restent inconnus.",
    "future": (
        "Le signal publié est daté dans le futur. Il n'est pas utilisé comme "
        "état courant."
    ),
    "expired": "L'état général et les composantes restent inconnus.",
    "aged": "L'état général et les composantes restent inconnus.",
}

VITAL_KEYS = ("operational", "degraded", "outage", "maintenance", "unknown")
VITAL_GAP = 0.8
UNKNOWN_DASH = 1.2
UNKNOWN_GAP = 2


def vital_key(status: object) -> str:
    """Collapse the five board statuses onto the four vital segments."""
    if status in ("partial_outage", "major_outage"):
        return "outage"
    return status if status in VITAL_KEYS else "unknown"


def _number(value: float) -> str:
    """Match the browser's toFixed(3) with trailing zeros removed."""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def dashed_arc(length: float) -> str:
    """Mirror app.js: a dashed arc for the unknown segment."""
    if length <= 0:
        return "0 100"
    if length >= 99.999:
        return f"{_number(UNKNOWN_DASH)} {_number(UNKNOWN_GAP)}"
    parts: list[str] = []
    remaining = length
    while remaining > 0.001:
        dash = min(UNKNOWN_DASH, remaining)
        parts.append(_number(dash))
        remaining -= dash
        if remaining <= 0.001:
            break
        gap = min(UNKNOWN_GAP, remaining)
        parts.append(_number(gap))
        remaining -= gap
    tail = 100 - length
    if tail > 0.001:
        parts.append(_number(tail))
    return " ".join(parts)


def vitals_state(components: list[dict], fresh: bool) -> dict:
    """Return the publish-time values the vitals instrument must show.

    The server renders the same counts and arcs that app.js will render after
    the first read, so the instrument does not appear or resize later.
    """
    counts = {key: 0 for key in VITAL_KEYS}
    for component in components:
        counts[vital_key(component["status"])] += 1
    total = len(components)
    segments: dict[str, dict[str, str]] = {}
    offset = 0.0
    for key in VITAL_KEYS:
        length = (counts[key] / total) * 100 if total else 0.0
        dasharray = dashed_arc(length) if key == "unknown" else f"{_number(length)} 100"
        segments[key] = {
            "dasharray": dasharray,
            "dashoffset": _number(-offset) if offset else "0",
        }
        offset += length
    signals = total - counts["unknown"] if fresh else None
    signal_length = (signals / total) * 100 if signals is not None and total else 0.0
    scale_gap = (
        f"{_number(VITAL_GAP)} {_number(100 / total - VITAL_GAP)}"
        if total
        else "0 100"
    )
    return {
        "total": total,
        "counts": counts,
        "signals": signals,
        "signal_length": signal_length,
        "scale_gap": scale_gap,
        "segments": segments,
    }


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def iso(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_stamp(timestamp: datetime) -> str:
    value = timestamp.astimezone(timezone.utc)
    return f"{value:%Y-%m-%d} · {value:%H:%M} UTC"


def document_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def freshness(document: dict, now: datetime) -> tuple[str, datetime | None]:
    """Return the presentation reason and the parsed generated timestamp."""
    generated = parse_timestamp(document.get("generated_at"))
    expires = parse_timestamp(document.get("expires_at"))
    if generated is None:
        return "never", None
    if generated - now > FUTURE_SKEW:
        return "future", generated
    if expires is not None and expires <= now:
        return "expired", generated
    if now - generated > MAX_AGE:
        return "aged", generated
    return "fresh", generated


def snapshot_state(document: dict, now: datetime) -> dict:
    """Return every value the static region is allowed to show.

    A stale or unconfirmed heartbeat forces the headline and every component
    row to unknown. Only a fresh heartbeat may repeat the publisher's own
    overall state and summary. The caller renders from this dictionary; the
    validator rebuilds it and compares.
    """
    reason, generated = freshness(document, now)
    fresh = reason == "fresh"
    headline = document.get("overall") if fresh else "unknown"
    if headline not in STATUSES:
        headline = "unknown"

    components = []
    raw_components = document.get("components")
    for component in raw_components if isinstance(raw_components, list) else []:
        if not isinstance(component, dict):
            continue
        component_id = str(component.get("id", ""))
        label = component.get("label_fr") or component_id or "Indisponible"
        status = component.get("status") if fresh else "unknown"
        if status not in STATUSES:
            status = "unknown"
        components.append(
            {
                "id": component_id,
                "label": str(label),
                "status": status,
                "status_label": STATUS_LABELS_FR[status],
            }
        )

    incidents = []
    raw_incidents = document.get("incidents")
    if isinstance(raw_incidents, list):
        for incident in raw_incidents:
            if not isinstance(incident, dict):
                continue
            status = incident.get("status")
            if status not in INCIDENT_LABELS_FR:
                status = "unknown"
            incidents.append(
                {
                    "status": status,
                    "status_label": INCIDENT_LABELS_FR.get(
                        status, STATUS_LABELS_FR["unknown"]
                    ),
                    "title": str(incident.get("title_fr") or "Incident sans titre"),
                    "summary": str(incident.get("summary_fr") or ""),
                    "started_at": incident.get("started_at"),
                    "updated_at": incident.get("updated_at"),
                }
            )

    summary = (
        str(document.get("summary_fr", ""))
        if fresh
        else SUMMARY_FR[reason]
    )
    summary_key = (
        "payload"
        if fresh
        else {
            "never": "unknownNeverSummary",
            "future": "unknownFutureSummary",
            "expired": "unknownStaleSummary",
            "aged": "unknownStaleSummary",
        }[reason]
    )
    note_key = {
        "fresh": "componentsNoteFresh",
        "never": "componentsNoteNever",
        "future": "componentsNoteFuture",
        "expired": "componentsNoteStale",
        "aged": "componentsNoteStale",
    }[reason]
    snapshot_kind = (
        "none"
        if reason == "never"
        else "unknown"
        if reason == "future"
        else "generated"
    )
    return {
        "reason": reason,
        "fresh": fresh,
        "generated": generated,
        "generated_iso": iso(generated) if generated is not None else None,
        "snapshot_at": now,
        "snapshot_iso": iso(now),
        "headline": headline,
        "overall_word": OVERALL_WORDS_FR[headline],
        "summary": summary,
        "summary_key": summary_key,
        "note": NOTE_FR[reason],
        "note_key": note_key,
        "snapshot_kind": snapshot_kind,
        "signal": "count" if fresh else "unknown",
        "banner": None
        if fresh
        else {
            "title": STALE_TITLE_FR[reason],
            "text": STALE_TEXT_FR[reason],
        },
        "vitals": vitals_state(components, fresh),
        "components": components,
        "incidents": incidents,
        "component_count_text": (
            "Aucune composante"
            if not components
            else f"{len(components)} suivie" + ("" if len(components) == 1 else "s")
        ),
        "incidents_count_text": "Aucun" if not incidents else f"{len(incidents):02d}",
        "snapshot_marker": (
            f'<time datetime="{iso(generated)}">{format_stamp(generated)}</time> '
            '<span class="hero__snapshot-note">Peut ne plus être à jour.</span>'
            if generated is not None
            else '<span class="hero__snapshot-note">Aucun signal reçu.</span>'
        ),
    }


def _time_or_missing(value: object) -> str:
    parsed = parse_timestamp(value)
    if parsed is None:
        return '<span class="incident-time__missing">Indisponible</span>'
    return f'<time datetime="{iso(parsed)}">{format_stamp(parsed)}</time>'


def _component_row(index: int, component: dict) -> str:
    label = html.escape(component["label"], quote=False)
    status = component["status"]
    status_label = html.escape(component["status_label"], quote=False)
    return f"""        <li class="component" data-status="{status}">
          <span class="component-index" aria-hidden="true">{index:02d}</span>
          <h3 class="component-name">{label}</h3>
          <div class="component-side"><span class="pill" data-status="{status}">{status_label}</span></div>
        </li>"""


def _incident_row(incident: dict) -> str:
    title = html.escape(incident["title"], quote=False)
    status = incident["status"]
    status_label = html.escape(incident["status_label"], quote=False)
    summary = html.escape(incident["summary"], quote=False)
    summary_html = (
        f'\n          <p class="incident-summary">{summary}</p>' if summary else ""
    )
    return f"""        <li class="incident" data-status="{status}">
          <div class="incident-time"><span class="micro">Début</span>{_time_or_missing(incident["started_at"])}</div>
          <div class="incident-rail" aria-hidden="true"><span class="incident-node"></span></div>
          <div class="incident-body">
            <div class="incident-head"><h3 class="incident-title">{title}</h3><span class="pill" data-status="{status}">{status_label}</span></div>{summary_html}
            <div class="incident-meta"><span><span class="micro">Mis à jour</span>{_time_or_missing(incident["updated_at"])}</span></div>
          </div>
        </li>"""


def _vitals_markup(vitals: dict) -> str:
    """Render the instrument from the same values app.js will render.

    The markup is never hidden: it is part of the static snapshot and its
    height must be reserved before status.json is read.
    """
    segments = []
    for key in VITAL_KEYS:
        segment = vitals["segments"][key]
        segments.append(
            f'              <circle class="vitals__segment" data-vital="{key}" '
            'cx="110" cy="110" r="90" pathLength="100" '
            f'stroke-dasharray="{segment["dasharray"]}" '
            f'stroke-dashoffset="{segment["dashoffset"]}" '
            'transform="rotate(-90 110 110)"></circle>'
        )
    counts = "\n".join(
        f'            <li data-vital="{key}"><span class="vitals__key" aria-hidden="true"></span>'
        f'<span data-i18n="{label}">{text}</span>'
        f'<span class="vitals__count" data-count="{key}">{vitals["counts"][key]}</span></li>'
        for key, label, text in (
            ("operational", "vitalOperational", "Opérationnelles"),
            ("degraded", "vitalDegraded", "Dégradées"),
            ("outage", "vitalOutage", "Interrompues"),
            ("maintenance", "vitalMaintenance", "Maintenance"),
            ("unknown", "vitalUnknown", "Inconnues"),
        )
    )
    if vitals["signals"] is None:
        signal = '<span data-i18n="unknown">Inconnu</span>'
    else:
        signal = (
            f'<span id="vitals-signals">{vitals["signals"]}</span>/'
            f'<span id="vitals-signal-total">{vitals["total"]}</span>'
        )
    return f"""        <div class="vitals" id="vitals" aria-hidden="true" data-total="{vitals["total"]}">
          <div class="vitals__head">
            <p class="vitals__title">Vitals <span aria-hidden="true">/</span> <span data-i18n="componentsTitle">Composantes</span></p>
            <p class="vitals__total"><span id="vitals-total">{vitals["total"]}</span></p>
          </div>
          <div class="vitals__dial">
            <svg class="vitals__svg" viewBox="0 0 220 220" role="presentation" focusable="false">
              <circle class="vitals__ticks" cx="110" cy="110" r="100" pathLength="100" stroke-dasharray="0.25 1.75"></circle>
              <circle class="vitals__track" cx="110" cy="110" r="90"></circle>
{chr(10).join(segments)}
              <circle class="vitals__scale" id="vitals-scale" cx="110" cy="110" r="90" pathLength="100" stroke-dasharray="{vitals["scale_gap"]}" stroke-dashoffset="0" transform="rotate(-90 110 110)"></circle>
              <circle class="vitals__signal-track" cx="110" cy="110" r="64"></circle>
              <circle class="vitals__signal" id="vitals-signal" cx="110" cy="110" r="64" pathLength="100" stroke-dasharray="{_number(vitals["signal_length"])} 100" transform="rotate(-90 110 110)"></circle>
            </svg>
            <div class="vitals__readout">
              <span class="vitals__number" id="vitals-number">{vitals["total"]}</span>
              <span class="vitals__unit" data-i18n="vitalsComponents">composantes</span>
            </div>
          </div>
          <ul class="vitals__legend">
{counts}
          </ul>
          <p class="vitals__signal-line"><span data-i18n="vitalSignals">Signaux reçus</span> <span class="vitals__signal-value">{signal}</span></p>
        </div>"""


def render_snapshot_region(document: dict, now: datetime) -> str:
    """Render the complete status region that replaces the template fallback."""
    state = snapshot_state(document, now)
    generated = state["generated"]
    signal_html = (
        f'<time datetime="{state["generated_iso"]}">{format_stamp(generated)}</time>'
        if generated is not None
        else "Aucun"
    )
    checked_html = (
        f'<time datetime="{state["snapshot_iso"]}">{format_stamp(now)}</time>'
    )
    stale_hidden = "" if state["banner"] is not None else " hidden"
    stale_reason = (
        f' data-reason="{state["reason"]}"' if state["banner"] is not None else ""
    )
    banner = state["banner"] or {"title": "", "text": ""}
    empty_hidden = "" if not state["components"] else " hidden"
    incidents_hidden = " hidden" if state["incidents"] else ""
    incident_rows = "\n".join(_incident_row(item) for item in state["incidents"])
    component_rows = "\n".join(
        _component_row(index, component)
        for index, component in enumerate(state["components"], 1)
    )
    incidents = (
        f'        <ol class="incidents" id="incidents-list">\n{incident_rows}\n        </ol>'
        if state["incidents"]
        else '        <ol class="incidents" id="incidents-list"></ol>'
    )
    components = (
        f'        <ul class="components" id="components-list">\n{component_rows}\n        </ul>'
        if state["components"]
        else '        <ul class="components" id="components-list"></ul>'
    )

    return f"""<div class="sbk-shell status-alerts">
      <div class="alert alert-error" id="fetch-error" role="alert" aria-labelledby="fetch-error-title" hidden>
        <div class="alert-copy">
          <p class="alert-title" id="fetch-error-title" data-i18n="errorTitle">Impossible de lire l'état du service</p>
          <p class="alert-text" data-i18n="errorText">Le fichier status.json n'a pas pu être chargé. Vérifiez votre connexion, puis réessayez.</p>
        </div>
        <button class="btn" id="retry-button" type="button" data-i18n="retry">Réessayer</button>
      </div>

      <div class="alert alert-stale" id="stale-banner" role="status" aria-labelledby="stale-title"{stale_reason}{stale_hidden}>
        <div class="alert-copy">
          <p class="alert-title" id="stale-title">{html.escape(banner["title"], quote=False)}</p>
          <p class="alert-text" id="stale-text">{html.escape(banner["text"], quote=False)}</p>
        </div>
      </div>
    </div>

    <section class="hero" id="hero" data-status="{state["headline"]}" aria-labelledby="hero-title">
      <div class="sbk-shell hero__inner">
        <div class="hero__rail">
          <p class="eyebrow"><span class="eyebrow__index" aria-hidden="true">01</span> <span data-i18n="overallEyebrow">État général</span></p>
          <p class="hero__host">status.ia.sbktechno.com</p>
        </div>

        <div class="hero__copy">
          <h1 class="hero-statement" id="hero-title">
            <span class="hero-statement__lead" data-i18n="overallLead">L'état du service est</span>
            <span class="hero-statement__value"><span id="hero-status-text">{html.escape(state["overall_word"], quote=False)}</span><span class="hero-statement__period" aria-hidden="true">.</span></span>
          </h1>
          <p class="hero-summary" id="overall-summary" aria-live="polite" aria-atomic="true">{html.escape(state["summary"], quote=False)}</p>
          <p class="hero__note">
            <span class="hero__note-mark" aria-hidden="true"></span>
            <span data-i18n="pageMetaStatic">Page statique indépendante</span>
          </p>
        </div>

{_vitals_markup(state["vitals"])}

        <div class="hero__foot">
          <p class="hero__signal"><span class="micro" data-i18n="heroSignal">Dernier signal :</span> <span id="hero-signal">{signal_html}</span></p>
          <p class="hero__check"><span class="micro" data-i18n="heroChecked">Dernière lecture :</span> <span id="hero-checked">{checked_html}</span></p>
          <p class="hero__snapshot"><span class="micro">Instantané</span> <span id="snapshot-marker" data-snapshot-state="{state["reason"]}">{state["snapshot_marker"]}</span></p>
          <p class="hero__source"><span class="micro" data-i18n="sourceLabel">Source :</span> <a href="status.json">status.json</a></p>
        </div>
      </div>
    </section>

    <section class="board" aria-labelledby="components-title">
      <div class="sbk-shell">
        <header class="section-head">
          <span class="section-index" aria-hidden="true">02</span>
          <h2 class="section-title" id="components-title"><span data-i18n="componentsTitle">Composantes</span></h2>
          <p class="section-count" id="components-count">{html.escape(state["component_count_text"], quote=False)}</p>
        </header>
        <p class="board-note" id="components-note">{html.escape(state["note"], quote=False)}</p>
{components}
        <p class="quiet" id="components-empty"{empty_hidden}>{html.escape(state["component_count_text"] if not state["components"] else "Aucune composante déclarée.")}</p>
      </div>
    </section>

    <section class="incidents-section" aria-labelledby="incidents-title">
      <div class="sbk-shell">
        <header class="section-head">
          <span class="section-index" aria-hidden="true">03</span>
          <h2 class="section-title" id="incidents-title"><span data-i18n="incidentsTitle">Incidents</span></h2>
          <p class="section-count" id="incidents-count">{html.escape(state["incidents_count_text"], quote=False)}</p>
        </header>
{incidents}
        <p class="quiet" id="incidents-empty"{incidents_hidden}>Aucun incident déclaré. Le journal est vide pour cet instantané.</p>
      </div>
    </section>"""
