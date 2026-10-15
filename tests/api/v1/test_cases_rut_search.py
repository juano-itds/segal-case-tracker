"""RUT search (``q``) and the per-row ``ruts`` field on GET /api/v1/cases.

The firm defends the debtor, so only the DEFENDANT's RUT (participante
"DDO.") is searchable and exposed; the plaintiff's RUT is the bank's and
would match half the portfolio.
"""

from datetime import datetime

import pytest
from sqlalchemy import event

from app.api.deps import get_current_lawyer
from app.main import app
from app.models.case import Case
from app.models.case_litigante import CaseLitigante
from app.models.court import Court
from app.models.lawyer import Lawyer
from tests.conftest import engine

DDO_RUT = "12345678-9"
DTE_RUT = "76543210-K"


@pytest.fixture
def lawyer(db):
    obj = Lawyer(rut="11111111-1", name="Admin Lawyer", role="admin")
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@pytest.fixture
def court(db):
    obj = Court(code="T1-SR", name="Primer Juzgado", region="RM", type="civil")
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@pytest.fixture
def authed_client(client, lawyer):
    async def _mock():
        return {"sub": str(lawyer.id)}

    app.dependency_overrides[get_current_lawyer] = _mock
    yield client
    app.dependency_overrides.pop(get_current_lawyer, None)


def _case(db, lawyer, court, rol, *, defendant="DEUDOR"):
    obj = Case(
        lawyer_id=lawyer.id,
        court_id=court.id,
        rol=rol,
        status="active",
        competencia="civil",
        plaintiff="BANCO SA",
        defendant=defendant,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def _lit(db, case, participante, rut):
    db.add(
        CaseLitigante(
            case_id=case.id,
            participante=participante,
            rut=rut,
            persona_type="NATURAL",
            nombre="N",
            natural_key=f"{participante}-{rut}",
        )
    )
    db.commit()


def _ids(resp):
    assert resp.status_code == 200
    return {i["id"] for i in resp.json()["items"]}


class TestRutSearch:
    def test_finds_case_by_defendant_rut(self, authed_client, db, lawyer, court):
        target = _case(db, lawyer, court, "C-1-2025")
        _lit(db, target, "DDO.", DDO_RUT)
        other = _case(db, lawyer, court, "C-2-2025")
        _lit(db, other, "DDO.", "11111111-1")

        assert _ids(authed_client.get(f"/api/v1/cases?q={DDO_RUT}")) == {target.id}

    def test_pasted_rut_with_dots_matches_stored_rut_without_dots(
        self, authed_client, db, lawyer, court
    ):
        target = _case(db, lawyer, court, "C-1-2025")
        _lit(db, target, "DDO.", DDO_RUT)

        assert _ids(authed_client.get("/api/v1/cases?q=12.345.678-9")) == {target.id}

    def test_rut_without_hyphen_and_lowercase_k(self, authed_client, db, lawyer, court):
        target = _case(db, lawyer, court, "C-1-2025")
        _lit(db, target, "DDO.", "9876543-K")

        assert _ids(authed_client.get("/api/v1/cases?q=9.876.543k")) == {target.id}

    def test_plaintiff_rut_does_not_match(self, authed_client, db, lawyer, court):
        case = _case(db, lawyer, court, "C-1-2025")
        _lit(db, case, "DTE.", DTE_RUT)
        _lit(db, case, "DDO.", DDO_RUT)

        assert _ids(authed_client.get(f"/api/v1/cases?q={DTE_RUT}")) == set()

    def test_rut_search_still_combines_with_other_text_matches(
        self, authed_client, db, lawyer, court
    ):
        by_rut = _case(db, lawyer, court, "C-1-2025")
        _lit(db, by_rut, "DDO.", DDO_RUT)
        by_name = _case(db, lawyer, court, "C-2-2025", defendant=DDO_RUT)

        assert _ids(authed_client.get(f"/api/v1/cases?q={DDO_RUT}")) == {
            by_rut.id,
            by_name.id,
        }

    def test_rut_with_several_litigantes_returns_case_once(
        self, authed_client, db, lawyer, court
    ):
        case = _case(db, lawyer, court, "C-1-2025")
        _lit(db, case, "DDO.", DDO_RUT)
        # Same RUT, different natural_key (e.g. a second DDO. row).
        db.add(
            CaseLitigante(
                case_id=case.id,
                participante="DDO.",
                rut=DDO_RUT,
                persona_type="NATURAL",
                nombre="Dup",
                natural_key="dup",
            )
        )
        db.commit()

        r = authed_client.get(f"/api/v1/cases?q={DDO_RUT}")
        assert [i["id"] for i in r.json()["items"]] == [case.id]
        assert r.json()["total"] == 1

    def test_text_search_does_not_pick_up_ruts(self, authed_client, db, lawyer, court):
        """A plain-text q must not change: no RUT shape, no RUT clause."""
        case = _case(db, lawyer, court, "C-1-2025", defendant="Juan Perez")
        _lit(db, case, "DDO.", "12345678-9")
        _case(db, lawyer, court, "C-2-2025", defendant="Maria")

        assert _ids(authed_client.get("/api/v1/cases?q=perez")) == {case.id}
        # A partial number is not a RUT: no prefix/substring match on RUTs.
        assert _ids(authed_client.get("/api/v1/cases?q=12345")) == set()

    def test_text_search_sql_is_unchanged(self, authed_client, db, lawyer, court):
        """Callers that do not search by RUT never pay for the litigantes lookup."""
        for n in range(3):
            _case(db, lawyer, court, f"C-{n}-2025", defendant="Juan Perez")

        # A name, a partial number and a ROL: none is shaped like a whole RUT.
        for term in ("perez", "12345", "C-1-2025"):
            statements = _capture(lambda: authed_client.get(f"/api/v1/cases?q={term}"))
            filtering = [s for s in statements if "case_litigantes" in s and "FROM cases" in s]
            assert filtering == [], term


def _capture(call):
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        call()
    finally:
        event.remove(engine, "before_cursor_execute", _listener)
    return statements


class TestRutsField:
    def test_row_carries_defendant_ruts_only(self, authed_client, db, lawyer, court):
        case = _case(db, lawyer, court, "C-1-2025")
        _lit(db, case, "DDO.", DDO_RUT)
        _lit(db, case, "DDO.", "11111111-1")
        _lit(db, case, "DTE.", DTE_RUT)
        _lit(db, case, "AB.DDO", "22222222-2")

        item = authed_client.get("/api/v1/cases").json()["items"][0]
        assert sorted(item["ruts"]) == sorted([DDO_RUT, "11111111-1"])

    def test_duplicate_rut_rows_are_listed_once(self, authed_client, db, lawyer, court):
        case = _case(db, lawyer, court, "C-1-2025")
        for key in ("a", "b"):
            db.add(
                CaseLitigante(
                    case_id=case.id,
                    participante="DDO.",
                    rut=DDO_RUT,
                    persona_type="NATURAL",
                    nombre=key,
                    natural_key=key,
                )
            )
        db.commit()
        item = authed_client.get("/api/v1/cases").json()["items"][0]
        assert item["ruts"] == [DDO_RUT]

    def test_case_without_litigantes_has_empty_list(self, authed_client, db, lawyer, court):
        _case(db, lawyer, court, "C-1-2025")
        item = authed_client.get("/api/v1/cases").json()["items"][0]
        assert item["ruts"] == []

    def test_blank_rut_is_not_exposed(self, authed_client, db, lawyer, court):
        case = _case(db, lawyer, court, "C-1-2025")
        _lit(db, case, "DDO.", "")
        item = authed_client.get("/api/v1/cases").json()["items"][0]
        assert item["ruts"] == []

    def test_ruts_are_attached_to_their_own_case(self, authed_client, db, lawyer, court):
        a = _case(db, lawyer, court, "C-1-2025")
        b = _case(db, lawyer, court, "C-2-2025")
        _lit(db, a, "DDO.", "11111111-1")
        _lit(db, b, "DDO.", "22222222-2")

        by_id = {i["id"]: i["ruts"] for i in authed_client.get("/api/v1/cases").json()["items"]}
        assert by_id == {a.id: ["11111111-1"], b.id: ["22222222-2"]}

    def test_ruts_use_one_query_regardless_of_page_size(
        self, authed_client, db, lawyer, court
    ):
        for n in range(6):
            c = _case(db, lawyer, court, f"C-{n}-2025")
            _lit(db, c, "DDO.", f"1000000{n}-1")

        statements = _capture(lambda: authed_client.get("/api/v1/cases?per_page=50"))
        litigante_reads = [s for s in statements if "FROM case_litigantes" in s]
        assert len(litigante_reads) == 1

    def test_case_detail_is_not_given_a_misleading_empty_ruts(
        self, authed_client, db, lawyer, court
    ):
        case = _case(db, lawyer, court, "C-1-2025")
        _lit(db, case, "DDO.", DDO_RUT)
        body = authed_client.get(f"/api/v1/cases/{case.id}").json()
        assert "ruts" not in body.get("case", body)
