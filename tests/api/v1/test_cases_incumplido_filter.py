"""GET /cases?incumplido= filters on Case.plazo_fatal_incumplido, server side."""

from datetime import datetime, timedelta

import pytest

from app.api.deps import get_current_lawyer
from app.main import app
from app.models.case import Case
from app.models.court import Court
from app.models.lawyer import Lawyer


@pytest.fixture
def lawyer(db):
    obj = Lawyer(rut="11111111-1", name="Lawyer INC")
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@pytest.fixture
def other_lawyer(db):
    obj = Lawyer(rut="22222222-2", name="Other INC")
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@pytest.fixture
def court(db):
    obj = Court(code="T1-INC", name="Juzgado INC", region="RM", type="civil")
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


@pytest.fixture
def authed_client(client, lawyer):
    async def _mock():
        return {"sub": str(lawyer.id)}

    app.dependency_overrides[get_current_lawyer] = _mock
    yield client


def _case(db, owner, court, rol, **fields) -> Case:
    fields.setdefault("competencia", "civil")
    obj = Case(
        lawyer_id=owner.id, court_id=court.id, rol=rol, status="active",
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(), **fields,
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj


def _ids(resp):
    return {row["id"] for row in resp.json()["items"]}


def test_filter_returns_only_flagged_including_one_without_next_deadline(
    authed_client, db, lawyer, court
):
    # The central case: flagged and NO next_deadline_at. The filter must not
    # depend on that column.
    no_deadline = _case(db, lawyer, court, "C-1-2026", plazo_fatal_incumplido=True)
    with_deadline = _case(
        db, lawyer, court, "C-2-2026", plazo_fatal_incumplido=True,
        next_deadline_at=datetime.utcnow() + timedelta(days=3),
    )
    _case(db, lawyer, court, "C-3-2026")
    _case(db, lawyer, court, "C-4-2026", next_deadline_at=datetime.utcnow() + timedelta(days=1))

    resp = authed_client.get("/api/v1/cases", params={"incumplido": True})

    assert resp.status_code == 200
    assert no_deadline.next_deadline_at is None
    assert _ids(resp) == {no_deadline.id, with_deadline.id}


def test_filter_false_returns_only_unflagged(authed_client, db, lawyer, court):
    _case(db, lawyer, court, "C-5-2026", plazo_fatal_incumplido=True)
    fine = _case(db, lawyer, court, "C-6-2026")

    resp = authed_client.get("/api/v1/cases", params={"incumplido": False})

    assert _ids(resp) == {fine.id}


def test_absent_param_returns_everything(authed_client, db, lawyer, court):
    flagged = _case(db, lawyer, court, "C-7-2026", plazo_fatal_incumplido=True)
    fine = _case(db, lawyer, court, "C-8-2026")

    resp = authed_client.get("/api/v1/cases")

    assert resp.status_code == 200
    assert _ids(resp) == {flagged.id, fine.id}
    assert resp.json()["total"] == 2


def test_filter_respects_visibility_scope(authed_client, db, lawyer, other_lawyer, court):
    mine = _case(db, lawyer, court, "C-9-2026", plazo_fatal_incumplido=True)
    _case(db, other_lawyer, court, "C-10-2026", plazo_fatal_incumplido=True)

    resp = authed_client.get("/api/v1/cases", params={"incumplido": True})

    assert _ids(resp) == {mine.id}


def test_filter_composes_with_other_filters(authed_client, db, lawyer, court):
    civil = _case(db, lawyer, court, "C-11-2026", plazo_fatal_incumplido=True)
    laboral = _case(
        db, lawyer, court, "C-12-2026", plazo_fatal_incumplido=True, competencia="laboral",
    )

    resp = authed_client.get(
        "/api/v1/cases", params={"incumplido": True, "competencia": "laboral"},
    )

    assert _ids(resp) == {laboral.id}
    assert civil.id not in _ids(resp)


def test_total_reflects_the_filter_not_the_portfolio(authed_client, db, lawyer, court):
    for i in range(3):
        _case(db, lawyer, court, f"C-2{i}-2026", plazo_fatal_incumplido=True)
    for i in range(5):
        _case(db, lawyer, court, f"C-3{i}-2026")

    resp = authed_client.get("/api/v1/cases", params={"incumplido": True, "per_page": 2})

    body = resp.json()
    assert body["total"] == 3
    assert body["pages"] == 2
    assert len(body["items"]) == 2
