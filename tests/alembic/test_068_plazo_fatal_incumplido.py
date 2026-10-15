"""Migration 068: ``plazo_fatal_incumplido`` boolean on cases (NOT NULL, default false)."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

_PATH = Path(__file__).parent.parent.parent / "alembic/versions/068_case_plazo_fatal_incumplido.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_068", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _engine():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE cases (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO cases (id) VALUES (1)"))
    return engine


def _run(conn, mod, fn):
    with Operations.context(MigrationContext.configure(conn)):
        getattr(mod, fn)()


def _cols(conn):
    return {c["name"] for c in sa.inspect(conn).get_columns("cases")}


def test_revision_chain():
    mod = _load()
    assert (mod.revision, mod.down_revision) == ("068", "067")


def test_upgrade_adds_flag_defaulting_to_false_for_existing_rows():
    mod, engine = _load(), _engine()
    with engine.begin() as conn:
        _run(conn, mod, "upgrade")
        assert "plazo_fatal_incumplido" in _cols(conn)
        value = conn.execute(sa.text("SELECT plazo_fatal_incumplido FROM cases WHERE id = 1")).scalar()
        assert not value


def test_downgrade_removes_flag():
    mod, engine = _load(), _engine()
    with engine.begin() as conn:
        _run(conn, mod, "upgrade")
        _run(conn, mod, "downgrade")
        assert "plazo_fatal_incumplido" not in _cols(conn)
