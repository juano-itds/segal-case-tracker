"""``plazo_fatal_incumplido`` is exposed on GET /cases rows and /cases/{id}."""

from datetime import datetime

import pytest

from app.api.deps import get_current_lawyer
from app.main import app
from app.models.case import Case
from app.models.court import Court
from app.models.lawyer import Lawyer


@pytest.fixture
def admin(db):
    obj = Lawyer(rut="11111111-1", name="Admin PF", role="admin")
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@pytest.fixture
def court(db):
    obj = Court(code="T1-PF", name="Juzgado PF", region="RM", type="civil")
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@pytest.fixture
def authed_client(client, admin):
    async def _mock():
        return {"sub": str(admin.id)}

    app.dependency_overrides[get_current_lawyer] = _mock
    yield client


def _case(db, admin, court, rol, **flags) -> Case:
    obj = Case(
        lawyer_id=admin.id, court_id=court.id, rol=rol, status="active",
        competencia="civil", created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(), **flags,
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def test_list_rows_carry_the_flag(authed_client, db, admin, court):
    lost = _case(db, admin, court, "C-1-2026", plazo_fatal_incumplido=True, semaforo="verde")
    fine = _case(db, admin, court, "C-2-2026", semaforo="verde")

    resp = authed_client.get("/api/v1/cases", params={"limit": 50})

    assert resp.status_code == 200
    by_id = {row["id"]: row for row in resp.json()["items"]}
    assert by_id[lost.id]["plazo_fatal_incumplido"] is True
    assert by_id[fine.id]["plazo_fatal_incumplido"] is False
    # The semáforo is the pending-action axis and is not touched by the flag.
    assert by_id[lost.id]["semaforo"] == "verde"


def test_detail_carries_the_flag(authed_client, db, admin, court):
    lost = _case(db, admin, court, "C-3-2026", plazo_fatal_incumplido=True)

    resp = authed_client.get(f"/api/v1/cases/{lost.id}")

    assert resp.status_code == 200
    assert resp.json()["case"]["plazo_fatal_incumplido"] is True
