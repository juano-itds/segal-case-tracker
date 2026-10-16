"""Migration 069: nullable ``estado_diario_last_read_on`` date on lawyers."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

_PATH = (
    Path(__file__).parent.parent.parent
    / "alembic/versions/069_lawyer_estado_diario_last_read.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_069", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _engine():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE lawyers (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO lawyers (id) VALUES (1)"))
    return engine


def _run(conn, mod, fn):
    with Operations.context(MigrationContext.configure(conn)):
        getattr(mod, fn)()


def _cols(conn):
    return {c["name"] for c in sa.inspect(conn).get_columns("lawyers")}


def test_revision_chain():
    mod = _load()
    assert (mod.revision, mod.down_revision) == ("069", "068")


def test_upgrade_adds_nullable_column_existing_rows_read_null():
    mod, engine = _load(), _engine()
    with engine.begin() as conn:
        _run(conn, mod, "upgrade")
        assert "estado_diario_last_read_on" in _cols(conn)
        value = conn.execute(
            sa.text("SELECT estado_diario_last_read_on FROM lawyers WHERE id = 1")
        ).scalar()
        assert value is None


def test_downgrade_removes_column():
    mod, engine = _load(), _engine()
    with engine.begin() as conn:
        _run(conn, mod, "upgrade")
        _run(conn, mod, "downgrade")
        assert "estado_diario_last_read_on" not in _cols(conn)
