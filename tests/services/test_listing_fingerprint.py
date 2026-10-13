"""Listing fingerprint experiment: observe the "Mis Causas" row, never steer the scrape.

Pins the four constraints of odd/tasks/huella-del-listado.md:
1. observation only (no scraping behaviour change) -- covered by the sync tests
   still passing; here we only assert what is recorded,
2. ``case_token`` is excluded from the fingerprint,
3. nothing listing_* is written unless the fingerprint changed,
4. a fingerprint failure never breaks the sync.
"""
import logging
import re
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.services import sync_service as sync_module
from app.services.listing_fingerprint import (
    LISTING_RAW_MAX_LEN,
    compute_listing_fingerprint,
)
from app.services.sync_service import ScrapedCase, SyncService


def _row(**kw):
    base = dict(
        rol="C-1234-2025",
        tribunal="24º Juzgado Civil de Santiago",
        caratulado="BANCO ITAU/FERNANDEZ",
        fecha_ingreso="01/01/2025",
        estado_cuaderno="Tramitación",
        cuaderno="Principal",
        institucion="Corte de Apelaciones",
    )
    base.update(kw)
    return ScrapedCase(**base)


# --------------------------------------------------------------------------
# Pure fingerprint
# --------------------------------------------------------------------------

class TestComputeListingFingerprint:
    def test_is_deterministic_sha256_hex(self):
        fp = compute_listing_fingerprint(_row())
        assert re.fullmatch(r"[0-9a-f]{64}", fp)
        assert fp == compute_listing_fingerprint(_row())

    @pytest.mark.parametrize(
        "field,value",
        [
            ("estado_cuaderno", "Archivada"),
            ("cuaderno", "Apremio Ejecutivo Obligación de Dar"),
            ("tribunal", "1º Juzgado Civil de Santiago"),
            ("caratulado", "OTRO/OTRO"),
            ("fecha_ingreso", "02/01/2025"),
            ("institucion", "Otra"),
        ],
    )
    def test_each_listing_cell_moves_the_fingerprint(self, field, value):
        assert compute_listing_fingerprint(_row()) != compute_listing_fingerprint(
            _row(**{field: value})
        )

    def test_case_token_is_excluded(self):
        """The token is a rotating JWT: if it entered the hash every row would
        change on every pass and the experiment would measure nothing."""
        a = SimpleNamespace(**vars(_row()), case_token="jwt-AAA")
        b = SimpleNamespace(**vars(_row()), case_token="jwt-BBB")
        assert compute_listing_fingerprint(a) == compute_listing_fingerprint(b)

    def test_none_and_empty_string_are_the_same_cell(self):
        # The converter yields None or "" for a missing cell depending on the caller.
        assert compute_listing_fingerprint(
            _row(institucion=None)
        ) == compute_listing_fingerprint(_row(institucion=""))

    def test_cell_boundaries_are_unambiguous(self):
        assert compute_listing_fingerprint(
            _row(estado_cuaderno="ab", cuaderno="c")
        ) != compute_listing_fingerprint(_row(estado_cuaderno="a", cuaderno="bc"))


# --------------------------------------------------------------------------
# Persistence through the real listing upsert
# --------------------------------------------------------------------------

@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def lawyer(db):
    lw = Lawyer(
        rut="12345678-9", name="Abogada Test", email="t@segal.cl", is_active=True,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(lw)
    db.commit()
    return lw


@pytest.fixture()
def sync_logs():
    """Own handler on the sync logger. caplog is polluted by other tests in the
    full suite (25 known failures), so this must not depend on it."""
    records = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    lg = logging.getLogger(sync_module.__name__)
    h = _H(level=logging.DEBUG)
    old = (lg.level, lg.disabled, logging.root.manager.disable)
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    lg.disabled = False
    logging.disable(logging.NOTSET)
    try:
        yield records
    finally:
        lg.removeHandler(h)
        lg.setLevel(old[0])
        lg.disabled = old[1]
        logging.disable(old[2])


def _sync(db, lawyer, row):
    return SyncService(db).sync_cases(
        lawyer_id=lawyer.id, scraped_cases=[row], competencia="civil"
    )


def _case(db):
    db.expire_all()
    return db.query(Case).one()


def _changed_lines(records):
    return [r for r in records if "listing fingerprint changed" in r.getMessage()]


@contextmanager
def captured_sql(engine):
    stmts = []

    def _l(conn, cur, statement, params, ctx, many):
        stmts.append(statement)

    event.listen(engine, "before_cursor_execute", _l)
    try:
        yield stmts
    finally:
        event.remove(engine, "before_cursor_execute", _l)


class TestPersistence:
    def test_new_case_gets_baseline_without_a_change_log(self, db, lawyer, sync_logs):
        _sync(db, lawyer, _row())
        c = _case(db)
        assert c.listing_fingerprint == compute_listing_fingerprint(_row())
        assert c.listing_fingerprint_at is not None
        assert c.listing_estado_raw == "Tramitación"
        assert c.listing_cuaderno_raw == "Principal"
        assert _changed_lines(sync_logs) == []

    def test_unchanged_row_writes_nothing_listing(self, db, engine, lawyer, sync_logs):
        _sync(db, lawyer, _row())
        first = _case(db).listing_fingerprint_at
        with captured_sql(engine) as stmts:
            _sync(db, lawyer, _row())
        assert _case(db).listing_fingerprint_at == first
        updates = [s for s in stmts if s.lstrip().upper().startswith("UPDATE CASES")]
        assert updates, "sanity: the pre-existing updated_at touch still happens"
        assert not any("listing_" in s for s in updates)
        assert _changed_lines(sync_logs) == []

    def test_changed_row_rewrites_and_logs_old_and_new(self, db, lawyer, sync_logs):
        _sync(db, lawyer, _row())
        old_fp = _case(db).listing_fingerprint
        _sync(db, lawyer, _row(estado_cuaderno="Archivada", cuaderno="Apremio"))
        c = _case(db)
        assert c.listing_fingerprint != old_fp
        assert c.listing_estado_raw == "Archivada"
        assert c.listing_cuaderno_raw == "Apremio"
        lines = _changed_lines(sync_logs)
        assert len(lines) == 1
        msg = lines[0].getMessage()
        for needle in ("C-1234-2025", "Tramitación", "Archivada", "Principal", "Apremio"):
            assert needle in msg

    def test_row_without_baseline_is_baselined_silently(self, db, lawyer, sync_logs):
        """Cases that existed before the migration have NULL fingerprints: the
        first pass records them, but that is not a "change"."""
        _sync(db, lawyer, _row())
        c = _case(db)
        c.listing_fingerprint = None
        c.listing_fingerprint_at = None
        c.listing_estado_raw = None
        c.listing_cuaderno_raw = None
        db.commit()
        _sync(db, lawyer, _row())
        assert _case(db).listing_fingerprint == compute_listing_fingerprint(_row())
        assert _changed_lines(sync_logs) == []

    def test_oversized_raw_values_are_truncated_not_fatal(self, db, lawyer):
        long = "x" * (LISTING_RAW_MAX_LEN + 500)
        result = _sync(db, lawyer, _row(estado_cuaderno=long))
        assert result.errors == []
        c = _case(db)
        assert len(c.listing_estado_raw) == LISTING_RAW_MAX_LEN
        # the hash covers the FULL value, not the truncated one
        assert c.listing_fingerprint == compute_listing_fingerprint(_row(estado_cuaderno=long))


class TestFailureNeverBreaksSync:
    def test_fingerprint_error_is_logged_and_sync_continues(
        self, db, lawyer, sync_logs, monkeypatch
    ):
        _sync(db, lawyer, _row())

        def boom(_):
            raise RuntimeError("fingerprint exploded")

        monkeypatch.setattr(sync_module, "compute_listing_fingerprint", boom)
        result = _sync(db, lawyer, _row(cuaderno="Apremio"))
        assert result.errors == []
        assert result.cases_updated == 1
        # the rest of the upsert still happened
        assert _case(db).procedure == "Apremio"
        assert any(
            "fingerprint exploded" in r.getMessage() or r.exc_info for r in sync_logs
            if r.levelno >= logging.WARNING
        )
