"""Fingerprint of a "Mis Causas" listing row (experiment, observation only).

Question being answered: does the listing row change when a case advances?
If it does, the listing (cheap, covers every case each pass) could tell the
detail scraper (expensive) what to visit. See odd/tasks/huella-del-listado.md.

The fingerprint covers the SET of rows PJUD returns for a ROL, not the last
row processed (see compute_listing_set_fingerprint).

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


def _cells(row) -> tuple:
    return tuple((getattr(row, name, None) or "") for name in LISTING_FINGERPRINT_FIELDS)


def canonical_listing_rows(rows) -> list:
    """The rows of one ROL as sorted, de-duplicated tuples of cells.

    PJUD returns several rows per case (Principal, Medida prejudicial,
    Incidente...). Sorting makes arrival order irrelevant; de-duplication makes
    a row repeated by paging irrelevant; a genuinely new row still shows up.
    """
    return sorted({_cells(r) for r in rows})


def compute_listing_set_fingerprint(rows) -> str:
    """sha256 hex of the SET of listing rows of one ROL.

    With exactly one distinct row the payload is the flat cell list, i.e. the
    very bytes PR #339 hashed: every fingerprint already stored stays valid and
    a one-row ROL behaves exactly as before. Two or more rows hash the sorted
    list of cell lists (a different JSON shape, so it cannot collide with one).
    """
    canonical = canonical_listing_rows(rows)
    body = list(canonical[0]) if len(canonical) == 1 else [list(c) for c in canonical]
    # JSON: unambiguous cell and row boundaries ("ab","c" != "a","bc").
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_listing_fingerprint(row) -> str:
    """sha256 hex of one listing row (None and "" are the same cell)."""
    return compute_listing_set_fingerprint([row])


def serialize_listing_rows(rows) -> str:
    """Canonical rows as JSON, stored so the NEXT change can log old values."""
    return json.dumps(
        [list(c) for c in canonical_listing_rows(rows)],
        ensure_ascii=False, separators=(",", ":"),
    )


def per_field_values(canonical_rows) -> dict:
    """{field: distinct values of that cell across the rows, joined by " | "}.

    With one row this is just the cell, so raw columns and log are unchanged.
    """
    return {
        name: " | ".join(sorted({row[i] for row in canonical_rows}))
        for i, name in enumerate(LISTING_FINGERPRINT_FIELDS)
    }


def parse_listing_rows(text):
    """Inverse of serialize_listing_rows; None if absent or unreadable."""
    if not text:
        return None
    try:
        data = json.loads(text)
        rows = [tuple(r) for r in data]
        if all(len(r) == len(LISTING_FINGERPRINT_FIELDS) for r in rows):
            return rows
    except (ValueError, TypeError):
        pass
    return None


def format_listing_change(old_rows, new_rows, old_estado, old_cuaderno) -> str:
    """One greppable line body: row counts, which cells changed, old->new of all.

    ``old_rows`` is None when the old state predates the rows column (a case
    baselined by PR #339): unknown cells print ``?``, never a guess. The two
    raw columns #339 did store still supply the old estado/cuaderno.
    """
    new = per_field_values(new_rows)
    if old_rows is not None:
        old = per_field_values(old_rows)
    else:
        old = {name: None for name in LISTING_FINGERPRINT_FIELDS}
        old["estado_cuaderno"], old["cuaderno"] = old_estado, old_cuaderno
    changed = [n for n in LISTING_FINGERPRINT_FIELDS if old[n] is not None and old[n] != new[n]]
    parts = [
        f"rows={'?' if old_rows is None else len(old_rows)}->{len(new_rows)}",
        f"changed={','.join(changed) if changed else 'row-set'}",
    ]
    for name in LISTING_FINGERPRINT_FIELDS:
        o = "?" if old[name] is None else repr(old[name])
        parts.append(f"{name}={o}->{new[name]!r}")
    return " ".join(parts)


def truncate_raw(value) -> "str | None":
    if value is None:
        return None
    return value[:LISTING_RAW_MAX_LEN]
