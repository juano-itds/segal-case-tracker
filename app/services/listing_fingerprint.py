"""Fingerprint of a "Mis Causas" listing row (experiment, observation only).

Question being answered: does the listing row change when a case advances?
If it does, the listing (cheap, covers every case each pass) could tell the
detail scraper (expensive) what to visit. See odd/tasks/huella-del-listado.md.

``case_token`` is deliberately NOT part of the fingerprint: it is a JWT that
rotates on every request, so including it would make every row "change" on
every pass and the experiment would measure nothing.
"""
import hashlib
import json

# Listing cells that make up the fingerprint, in a fixed order. This is an
# allow-list on purpose: a new attribute on the scraped object must never
# silently enter the hash.
LISTING_FINGERPRINT_FIELDS = (
    "rol",
    "tribunal",
    "caratulado",
    "fecha_ingreso",
    "estado_cuaderno",
    "cuaderno",
    "institucion",
)

# Size of cases.listing_estado_raw / listing_cuaderno_raw. Matches
# cases.procedure (String(255)), which already stores the very same
# ``cuaderno`` cell for every case without ever overflowing; ``estado`` is a
# short status label from the same table row. Values are truncated to this
# length on write so a freak cell can never raise a DataError and take a
# whole sync chunk down; the hash itself always covers the full value.
LISTING_RAW_MAX_LEN = 255


def compute_listing_fingerprint(row) -> str:
    """sha256 hex of the listing cells of ``row`` (None and "" are the same)."""
    cells = [(getattr(row, name, None) or "") for name in LISTING_FINGERPRINT_FIELDS]
    # JSON list: unambiguous cell boundaries ("ab","c" != "a","bc").
    payload = json.dumps(cells, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def truncate_raw(value) -> "str | None":
    if value is None:
        return None
    return value[:LISTING_RAW_MAX_LEN]
