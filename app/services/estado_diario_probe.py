"""Observe-only probe of the OJV "estado diario" feed.

Measured 2026-10-13/14: the rotation learns about a movement a median of 17
days late (p99 99), while OJV emails every lawyer, daily, the list of causas
notified by estado diario. This module asks one question and changes nothing:
is the endpoint behind that email usable from the session the cycle already
holds, and how stale would the rotation have been for those causas?

It makes ONE in-page AJAX POST per lawyer per cycle (the same ``$.ajax``
pattern the listing uses, which Shape already tolerates) and writes ONE
greppable log line. It never visits, creates or reorders cases, never alerts,
never opens a page/context/session and never raises.

Four outcomes, deliberately distinct in the log:

* ``ok``               rows came back
* ``no_activity``      recognised table, empty ``<tbody>`` (expected on weekends)
* ``endpoint_changed`` dead endpoint or a response shape we do not recognise
* ``bounced``          login page, 401/403, or a Shape/TSPD challenge
  (if this shows up: switch ESTADO_DIARIO_PROBE_ENABLED off and report; never insist)

plus ``skipped`` (nothing to reuse) and ``probe_error`` (our own failure).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, List, Optional

from app.config import settings
from app.core.database import SessionLocal
from app.models.case import Case
from app.scrapper.pjud.base import (
    _DETAIL_SESSION_URL_MARKERS,
    classify_small_detail_modal,
    detect_shape_challenge,
)
from app.scrapper.pjud.resilience.rate_limiter import pjud_action_limiter
from app.services.business_days import previous_business_day

logger = logging.getLogger("estado_diario_probe")

OK = "ok"
NO_ACTIVITY = "no_activity"
ENDPOINT_CHANGED = "endpoint_changed"
BOUNCED = "bounced"
SKIPPED = "skipped"
PROBE_ERROR = "probe_error"

from app.scrapper.pjud.base import PJUD_BASE_URL

ENDPOINT = "/miEstadoDiario/civil/estadoDiarioFechaCivil.php"
# The AJAX has to be issued from its own page, the way the listing's is: that
# one works because it runs on the loaded Mis Causas panel. The 2026-07
# capture of this endpoint recorded this loader as part of the flow. POSTing
# cold from whatever page the detail phase left behind risks a rejection we
# would read as "the endpoint is dead" -- and this feed was already written
# off once by mistake, for three months. Navigating costs nothing here: the
# browser is torn down between lawyers anyway.
LOADER = "/miEstadoDiario.php"
_BOUNCE_STATUSES = (401, 403)

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<t([dh])[^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class ProbeResult:
    outcome: str
    status: Optional[int] = None
    rols: List[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class Staleness:
    in_db: int
    not_in_db: int
    never_checked: int
    median_days: Optional[float]
    max_days: Optional[float]


def _clean(cell: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", cell)).strip()


def classify_response(response) -> ProbeResult:
    """Turn the raw in-page response into one of the outcomes. Pure."""
    if not isinstance(response, dict):
        return ProbeResult(ENDPOINT_CHANGED, detail="no response object")
    status = response.get("status")
    body = response.get("body")
    body = body if isinstance(body, str) else ""
    status_n = status if isinstance(status, int) else None

    is_shape, marker = detect_shape_challenge(body)
    if is_shape:
        return ProbeResult(BOUNCED, status_n, detail=f"shape challenge ({marker})")
    if status_n in _BOUNCE_STATUSES:
        return ProbeResult(BOUNCED, status_n, detail=f"http {status_n}")
    if status_n is not None and status_n != 200:
        return ProbeResult(ENDPOINT_CHANGED, status_n, detail=f"http {status_n}")

    rows = _ROW_RE.findall(body)
    header_cells: List[str] = []
    data_rows: List[List[str]] = []
    for row in rows:
        cells = _CELL_RE.findall(row)
        if not cells:
            continue
        if all(kind.lower() == "h" for kind, _ in cells):
            header_cells = [_clean(c).lower() for _, c in cells]
        elif any(kind.lower() == "d" for kind, _ in cells):
            data_rows.append([_clean(c) for _, c in cells])

    if "rol" not in header_cells or "caratulado" not in header_cells:
        # Not the table we captured. A login page is a bounce; anything else
        # (empty body, other HTML) means the endpoint changed or is dead.
        low = body.lower()
        if body.strip() and (
            any(m in low for m in _DETAIL_SESSION_URL_MARKERS)
            or classify_small_detail_modal(body) == "session"
        ):
            return ProbeResult(BOUNCED, status_n, detail="login page")
        return ProbeResult(ENDPOINT_CHANGED, status_n, detail="unrecognised response")

    rol_idx = header_cells.index("rol")
    rols = [
        r[rol_idx].upper() for r in data_rows if len(r) > rol_idx and r[rol_idx]
    ]
    if not rols:
        return ProbeResult(NO_ACTIVITY, status_n)
    return ProbeResult(OK, status_n, rols=rols)


def staleness_stats(
    session_factory: Callable, rols: List[str], now: datetime
) -> Staleness:
    """How stale (days since last_detail_checked_at) are the ROLs we already have."""
    unique = sorted(set(rols))
    db = session_factory()
    try:
        found = (
            db.query(Case.rol, Case.last_detail_checked_at)
            .filter(Case.rol.in_(unique))
            .all()
        )
    finally:
        db.close()
    checked = {}
    for rol, ts in found:
        # keep the stalest timestamp if a ROL appears twice
        if rol not in checked or (ts is None) or (
            checked[rol] is not None and ts < checked[rol]
        ):
            checked[rol] = ts
    ages = [
        (now - ts).total_seconds() / 86400.0
        for ts in checked.values()
        if ts is not None
    ]
    return Staleness(
        in_db=len(checked),
        not_in_db=len(unique) - len(checked),
        never_checked=sum(1 for ts in checked.values() if ts is None),
        median_days=round(statistics.median(ages), 1) if ages else None,
        max_days=round(max(ages), 1) if ages else None,
    )


async def _fetch_via_fetch(page, fecha: str) -> dict:
    """Same request as the `$.ajax` one, for pages that do not load jQuery.

    Mirrors what `$.ajax` sends by default: form-encoded body and the
    X-Requested-With header the server uses to tell an AJAX call apart from a
    navigation. Same-origin with the session cookies, like the rest of the
    in-page calls already do.
    """
    js = f"""
        () => fetch({json.dumps(ENDPOINT)}, {{
            method: 'POST',
            credentials: 'include',
            headers: {{
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest'
            }},
            body: 'fechaEstDiaCiv=' + encodeURIComponent({json.dumps(fecha)})
        }})
        .then(r => r.text().then(t => ({{status: r.status, body: t}})))
        .catch(e => ({{status: 0, body: String(e)}}))
    """
    return await page.evaluate(js)


async def fetch_estado_diario(page, fecha: str) -> dict:
    """Load the estado-diario page, then ONE in-page ``$.ajax`` POST from it
    (the same shape the listing uses)."""
    await page.goto(f"{PJUD_BASE_URL}{LOADER}", wait_until="domcontentloaded")

    # Measured on the first real run (2026-10-14): `/miEstadoDiario.php` does
    # NOT give us jQuery the way the Mis Causas panel does, and `$.ajax` blew
    # up with "ReferenceError: $ is not defined". Prefer $.ajax when it IS
    # there (it is the request shape Shape already tolerates on the listing)
    # and fall back to fetch with the same headers and body encoding.
    has_jquery = bool(await page.evaluate("() => typeof window.jQuery !== 'undefined'"))
    if not has_jquery:
        return await _fetch_via_fetch(page, fecha)

    js = f"""
        () => new Promise((resolve) => {{
            $.ajax({{
                url: '{ENDPOINT}',
                type: 'POST',
                data: {{ fechaEstDiaCiv: {json.dumps(fecha)} }},
                success: function(data, textStatus, xhr) {{
                    resolve({{status: xhr.status, body: String(data)}});
                }},
                error: function(xhr) {{
                    resolve({{status: xhr.status, body: xhr.responseText || ''}});
                }}
            }});
        }})
    """
    return await page.evaluate(js)


def _fmt(value) -> str:
    return "n/a" if value is None else str(value)


async def run_estado_diario_probe(
    *,
    scraper,
    lawyer_id: int,
    competencia: str = "civil",
    today: Optional[date] = None,
    now: Optional[datetime] = None,
    session_factory: Callable = SessionLocal,
) -> None:
    """Probe once and log once. Never raises; does nothing when the flag is off."""
    if not settings.ESTADO_DIARIO_PROBE_ENABLED:
        return
    if (competencia or "").lower() != "civil":
        return
    fecha = "?"
    try:
        target = previous_business_day(today or date.today(), include_saturday=False)
        fecha = target.strftime("%d/%m/%Y")

        page = scraper.live_page()
        if page is None:
            logger.info(
                "ESTADO_DIARIO_PROBE lawyer=%s date=%s outcome=%s reason=no_live_page",
                lawyer_id, fecha, SKIPPED,
            )
            return

        await pjud_action_limiter("estado_diario").acquire()
        await asyncio.sleep(settings.DETAIL_FETCH_DELAY)
        result = classify_response(await fetch_estado_diario(page, fecha))

        if result.outcome == OK:
            stats = staleness_stats(session_factory, result.rols, now or datetime.utcnow())
            logger.info(
                "ESTADO_DIARIO_PROBE lawyer=%s date=%s outcome=%s rows=%d in_db=%d "
                "not_in_db=%d never_checked=%d age_days_median=%s age_days_max=%s",
                lawyer_id, fecha, OK, len(result.rols), stats.in_db, stats.not_in_db,
                stats.never_checked, _fmt(stats.median_days), _fmt(stats.max_days),
            )
        elif result.outcome == NO_ACTIVITY:
            logger.info(
                "ESTADO_DIARIO_PROBE lawyer=%s date=%s outcome=%s rows=0",
                lawyer_id, fecha, NO_ACTIVITY,
            )
        else:
            logger.warning(
                "ESTADO_DIARIO_PROBE lawyer=%s date=%s outcome=%s status=%s detail=%s",
                lawyer_id, fecha, result.outcome, _fmt(result.status), result.detail,
            )
    except Exception as exc:  # noqa: BLE001 — a probe failure never breaks the cycle
        logger.warning(
            "ESTADO_DIARIO_PROBE lawyer=%s date=%s outcome=%s error=%s: %s",
            lawyer_id, fecha, PROBE_ERROR, type(exc).__name__, exc,
        )
