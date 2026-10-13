"""Per-lawyer digest of new-movement notifications.

A sync batch used to send one email per new movement (624 of the 630 emails
sent in a measured day; one lawyer received 204). This module holds the pure
pieces of the replacement: the accumulated entry and the renderer. It reuses
the visual language of ``daily_agenda`` (header band, section cards, row
style) so lawyers see one consistent family of emails. Neutral Spanish,
tuteo (tú).

No I/O here -- sending and the ``Alert.email_sent`` bookkeeping live in
``NotificationService.flush_digests``.
"""

import html
from dataclasses import dataclass
from typing import Any, List, Set, Tuple

from app.services.daily_agenda import _BRAND, _section_html

# A single movement description can be a full court resolution; cap what the
# email carries. The complete text stays in the app (the alert is persisted).
MAX_DESCRIPTION_CHARS = 280

# A digest renders at most this many movements; the rest are only counted (and
# announced in a closing line). A backfill batch can detect ~2000 movements for
# one lawyer, which is not an email anyone reads. It is also the memory bound:
# the accumulator never keeps more entries than will be rendered.
MAX_DIGEST_ENTRIES = 150


@dataclass
class DigestEntry:
    """Snapshot of one new movement, taken when it was detected.

    Plain values (not ORM objects) so rendering never lazy-loads. ``alert`` is
    kept only so the flush can mark ``email_sent`` on the alerts a sent digest
    actually covered.
    """

    alert: Any
    case_id: Any
    rol: str
    caratulado: str
    court_name: str
    date_str: str
    procedure: str
    description: str


class DigestBucket:
    """Bounded accumulator for one lawyer's digest.

    Keeps the first ``MAX_DIGEST_ENTRIES`` entries (the ones that will be
    rendered, and the only alerts later marked ``email_sent``) plus the true
    totals: a movement counter and the set of causa ids seen. Memory is
    O(MAX_DIGEST_ENTRIES + causas), never O(movements).
    """

    def __init__(self) -> None:
        self.entries: List[DigestEntry] = []
        self.total_movements: int = 0
        self.case_ids: Set[Any] = set()

    def add(self, entry: DigestEntry) -> None:
        self.total_movements += 1
        self.case_ids.add(entry.case_id)
        if len(self.entries) < MAX_DIGEST_ENTRIES:
            self.entries.append(entry)


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def digest_subject(movements: int, causas: int) -> str:
    return (
        f"{movements} {_plural(movements, 'movimiento nuevo', 'movimientos nuevos')} "
        f"en {causas} {_plural(causas, 'causa', 'causas')}"
    )


def _truncate(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_DESCRIPTION_CHARS:
        return text
    return text[: MAX_DESCRIPTION_CHARS - 1].rstrip() + "…"


def group_by_case(entries: List[DigestEntry]) -> List[Tuple[DigestEntry, List[DigestEntry]]]:
    """Group entries by causa, keeping first-seen order. Returns
    ``[(first_entry_of_case, [entries...]), ...]``."""
    groups: dict = {}
    for e in entries:
        groups.setdefault(e.case_id, []).append(e)
    return [(items[0], items) for items in groups.values()]


def _movement_row_html(e: DigestEntry) -> str:
    procedure = html.escape(e.procedure or "Movimiento")
    date = (
        f'<span style="color:#6b7280; font-size:12px; font-weight:normal; margin-left:8px;">'
        f"{html.escape(e.date_str)}</span>"
        if e.date_str
        else ""
    )
    description = (
        f'<div style="color:#374151; font-size:13px; margin-top:2px;">'
        f"{html.escape(_truncate(e.description))}</div>"
        if e.description
        else ""
    )
    return f"""\
              <tr>
                <td style="padding:12px 16px; border-bottom:1px solid #f0f1f3;">
                  <div style="color:#111827; font-size:14px; font-weight:bold;">{procedure}{date}</div>
                  {description}
                </td>
              </tr>"""


def _case_title(first: DigestEntry, count: int) -> str:
    rol = first.rol or "—"
    caratulado = f" · {first.caratulado}" if first.caratulado.strip("/ ") else ""
    court = f" ({first.court_name})" if first.court_name else ""
    return f"{rol}{caratulado}{court} — {count}"


def _omitted_line(omitted_movements: int, omitted_causas: int) -> str:
    """Closing line naming what the body left out (both numbers)."""
    movements = (
        f"{omitted_movements} {_plural(omitted_movements, 'movimiento', 'movimientos')} más"
    )
    if omitted_causas > 0:
        where = f"en {omitted_causas} {_plural(omitted_causas, 'causa', 'causas')}"
    else:
        where = "en causas ya listadas"
    return f"y {movements} {where} — míralos en la app"


def render_movement_digest(lawyer: Any, bucket: DigestBucket) -> Tuple[str, str, str]:
    """Render ``(subject, html_body, text_body)`` for one lawyer's digest.

    Pure function. The caller guarantees the bucket is non-empty (an empty
    digest is never sent). The subject and greeting carry the TRUE totals; the
    body renders ``bucket.entries`` and, when the bucket dropped some, closes
    with a line saying how many movements and causas were left out.
    """
    entries = bucket.entries
    groups = group_by_case(entries)
    movements = bucket.total_movements
    causas = len(bucket.case_ids)
    omitted_movements = movements - len(entries)
    omitted_causas = causas - len(groups)
    omitted = _omitted_line(omitted_movements, omitted_causas) if omitted_movements > 0 else ""
    subject = digest_subject(movements, causas)

    raw_name = getattr(lawyer, "name", None) or "Abogado(a)"
    name = html.escape(raw_name)
    summary = (
        f"{movements} {_plural(movements, 'movimiento nuevo', 'movimientos nuevos')} "
        f"en {causas} {_plural(causas, 'causa', 'causas')}"
    )

    sections_html = ""
    for first, items in groups:
        rows = "\n".join(_movement_row_html(e) for e in items)
        sections_html += _section_html(html.escape(_case_title(first, len(items))), rows)

    omitted_html = (
        f"""
          <tr>
            <td style="padding:8px 32px 0 32px;">
              <p style="margin:0; color:#374151; font-size:14px; font-weight:bold;">{html.escape(omitted)}</p>
            </td>
          </tr>
"""
        if omitted
        else ""
    )

    html_body = f"""\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{subject}</title>
</head>
<body style="margin:0; padding:0; background-color:#f4f5f7; font-family:Arial, Helvetica, sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f5f7; padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px; width:100%; background-color:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,0.08);">
          <tr>
            <td style="background-color:#0b2e4f; padding:20px 32px; color:#ffffff; font-size:15px; font-weight:bold; letter-spacing:0.3px;">
              {_BRAND}
            </td>
          </tr>
          <tr>
            <td style="padding:32px 32px 8px 32px;">
              <p style="margin:0 0 4px 0; color:#0b2e4f; font-size:13px; font-weight:bold; text-transform:uppercase; letter-spacing:0.5px;">Movimientos nuevos</p>
              <h1 style="margin:0; color:#111827; font-size:20px; line-height:1.4;">Hola {name}, tienes {summary}</h1>
            </td>
          </tr>

{sections_html}
{omitted_html}
          <tr>
            <td style="padding:24px 32px 32px 32px;">
              <hr style="border:none; border-top:1px solid #e5e7eb; margin:16px 0 16px 0;">
              <p style="margin:0; color:#9ca3af; font-size:12px; line-height:1.5;">
                Resumen de los movimientos detectados en esta revisión, agrupados por causa. Los
                plazos y cambios que requieren acción te llegan por separado, en el momento. Correo
                automático de {_BRAND}. No responder directamente a este mensaje.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    lines = [f"MOVIMIENTOS NUEVOS — {_BRAND}", "", f"Hola {raw_name}, tienes {summary}:", ""]
    for first, items in groups:
        lines.append(_case_title(first, len(items)).upper())
        for e in items:
            date = f" ({e.date_str})" if e.date_str else ""
            desc = f": {_truncate(e.description)}" if e.description else ""
            lines.append(f"  - {e.procedure or 'Movimiento'}{date}{desc}")
        lines.append("")
    if omitted:
        lines.append(omitted)
        lines.append("")
    lines.append(
        "Los plazos y cambios que requieren acción te llegan por separado, en el momento."
    )
    text_body = "\n".join(lines) + "\n"

    return subject, html_body, text_body
