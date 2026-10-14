"""Estado-diario probe: observe-only measurement of the OJV daily feed.

The probe makes ONE in-page AJAX POST per lawyer per cycle and logs one
greppable line. These tests pin the classification (three outcomes that must
never be conflated), the staleness statistics, the flag, the single request,
and that a probe failure never escapes.
"""
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.case import Case
from app.models.court import Court
from app.models.lawyer import Lawyer
from app.services import estado_diario_probe as probe
from app.services.business_days import previous_business_day


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

HEADER = (
    "<table><thead><tr><th>Rol</th><th>Fecha</th><th>Caratulado</th>"
    "<th>Tribunal</th></tr></thead>"
)


def _table(rows: list[tuple[str, str, str, str]]) -> str:
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows
    )
    return f"{HEADER}<tbody>{body}</tbody></table>"


def _resp(body: str, status: int = 200) -> dict:
    return {"status": status, "body": body}


LOGIN_PAGE = (
    "<html><body><form action='/home/index.php'>Ingrese su usuario y clave"
    "</form></body></html>"
)
SHAPE_PAGE = (
    "<html><script>TSPD_101</script>failureConfig Support ID: 123 "
    "What code is in the image?</html>"
)


class FakeLimiter:
    def __init__(self) -> None:
        self.acquired = 0

    async def acquire(self) -> None:
        self.acquired += 1


@pytest.fixture
def limiter(monkeypatch) -> FakeLimiter:
    fake = FakeLimiter()
    monkeypatch.setattr(probe, "pjud_action_limiter", lambda action: fake)
    monkeypatch.setattr(probe.asyncio, "sleep", AsyncMock())
    return fake


@pytest.fixture
def log():
    with patch.object(probe, "logger") as mock_logger:
        yield mock_logger


def _logged(mock_logger) -> list[str]:
    out = []
    for level in ("info", "warning", "error"):
        for c in getattr(mock_logger, level).call_args_list:
            out.append(c.args[0] % c.args[1:] if len(c.args) > 1 else c.args[0])
    return out


def _page(response):
    page = MagicMock()
    page.is_closed.return_value = False
    if isinstance(response, Exception):
        page.evaluate = AsyncMock(side_effect=response)
    else:
        page.evaluate = AsyncMock(return_value=response)
    return page


def _scraper(page):
    scraper = MagicMock()
    scraper.live_page.return_value = page
    return scraper


def _seed(db, rol: str, checked: datetime | None) -> None:
    lawyer = db.query(Lawyer).first()
    if lawyer is None:
        lawyer = Lawyer(rut="11111111-1", name="L", is_active=True)
        db.add(lawyer)
        db.flush()
    court = db.query(Court).first()
    if court is None:
        court = Court(code="T", name="Tribunal", region="RM", type="civil")
        db.add(court)
        db.flush()
    db.add(
        Case(
            lawyer_id=lawyer.id, court_id=court.id, rol=rol, competencia="civil",
            plaintiff="A", defendant="B", status="active",
            last_detail_checked_at=checked,
        )
    )
    db.commit()


def _enable(monkeypatch, value=True):
    monkeypatch.setattr(probe.settings, "ESTADO_DIARIO_PROBE_ENABLED", value)


NOW = datetime(2026, 10, 14, 12, 0, 0)
TODAY = date(2026, 10, 14)  # Wednesday


# ---------------------------------------------------------------------------
# Date choice
# ---------------------------------------------------------------------------

class TestPreviousBusinessDay:
    def test_tuesday_goes_to_monday(self):
        assert previous_business_day(date(2026, 10, 14), include_saturday=False) == date(2026, 10, 13)

    def test_monday_skips_the_weekend_to_friday(self):
        assert previous_business_day(date(2026, 10, 19), include_saturday=False) == date(2026, 10, 16)

    def test_saturday_counts_as_business_day_by_default(self):
        assert previous_business_day(date(2026, 10, 19)) == date(2026, 10, 17)

    def test_skips_a_holiday(self):
        # 2026-10-12 (Monday) is Encuentro de Dos Mundos -> Tuesday looks back to Friday.
        assert previous_business_day(date(2026, 10, 13), include_saturday=False) == date(2026, 10, 9)


# ---------------------------------------------------------------------------
# Classification: the three outcomes must stay distinct
# ---------------------------------------------------------------------------

class TestClassification:
    def test_rows_are_ok_and_rols_extracted(self):
        r = probe.classify_response(_resp(_table([
            ("C-1-2026", "13/10/2026", "A / B", "1 Civil"),
            ("C-2-2026", "13/10/2026", "C / D", "2 Civil"),
        ])))
        assert r.outcome == probe.OK
        assert r.rols == ["C-1-2026", "C-2-2026"]

    def test_empty_tbody_is_no_activity_not_dead(self):
        r = probe.classify_response(_resp(_table([])))
        assert r.outcome == probe.NO_ACTIVITY
        assert r.rols == []

    def test_login_page_is_bounced_not_no_activity(self):
        assert probe.classify_response(_resp(LOGIN_PAGE)).outcome == probe.BOUNCED

    def test_shape_challenge_is_bounced(self):
        assert probe.classify_response(_resp(SHAPE_PAGE)).outcome == probe.BOUNCED

    def test_403_is_bounced(self):
        assert probe.classify_response(_resp("Forbidden", 403)).outcome == probe.BOUNCED

    def test_404_is_endpoint_changed(self):
        assert probe.classify_response(_resp("Not found", 404)).outcome == probe.ENDPOINT_CHANGED

    def test_500_is_endpoint_changed(self):
        assert probe.classify_response(_resp("boom", 500)).outcome == probe.ENDPOINT_CHANGED

    def test_unrelated_html_is_endpoint_changed(self):
        assert probe.classify_response(_resp("<html><p>hola</p></html>")).outcome == probe.ENDPOINT_CHANGED

    def test_empty_body_is_endpoint_changed(self):
        assert probe.classify_response(_resp("")).outcome == probe.ENDPOINT_CHANGED

    def test_table_without_expected_columns_is_endpoint_changed(self):
        html = "<table><thead><tr><th>Foo</th><th>Bar</th></tr></thead><tbody></tbody></table>"
        assert probe.classify_response(_resp(html)).outcome == probe.ENDPOINT_CHANGED

    def test_non_dict_response_is_endpoint_changed(self):
        assert probe.classify_response(None).outcome == probe.ENDPOINT_CHANGED

    def test_rol_column_found_by_header_not_position(self):
        html = (
            "<table><thead><tr><th>Tribunal</th><th>Rol</th><th>Caratulado</th>"
            "<th>Fecha</th></tr></thead><tbody>"
            "<tr><td>1 Civil</td><td>c-9-2026</td><td>A/B</td><td>13/10/2026</td></tr>"
            "</tbody></table>"
        )
        r = probe.classify_response(_resp(html))
        assert r.rols == ["C-9-2026"]


# ---------------------------------------------------------------------------
# Staleness statistics
# ---------------------------------------------------------------------------

class TestStats:
    def test_counts_found_missing_median_and_max(self, db):
        _seed(db, "C-1-2026", NOW - timedelta(days=2))
        _seed(db, "C-2-2026", NOW - timedelta(days=8))
        _seed(db, "C-3-2026", NOW - timedelta(days=96))
        s = probe.staleness_stats(
            lambda: db, ["C-1-2026", "C-2-2026", "C-3-2026", "C-9-2026"], NOW
        )
        assert (s.in_db, s.not_in_db) == (3, 1)
        assert s.median_days == 8.0
        assert s.max_days == 96.0
        assert s.never_checked == 0

    def test_never_checked_cases_are_counted_apart(self, db):
        _seed(db, "C-1-2026", None)
        _seed(db, "C-2-2026", NOW - timedelta(days=4))
        s = probe.staleness_stats(lambda: db, ["C-1-2026", "C-2-2026"], NOW)
        assert (s.in_db, s.never_checked) == (2, 1)
        assert s.median_days == 4.0 and s.max_days == 4.0

    def test_duplicate_rols_in_the_feed_count_once(self, db):
        _seed(db, "C-1-2026", NOW - timedelta(days=1))
        s = probe.staleness_stats(lambda: db, ["C-1-2026", "C-1-2026"], NOW)
        assert (s.in_db, s.not_in_db) == (1, 0)


# ---------------------------------------------------------------------------
# run_estado_diario_probe: flag, single request, log lines, failure isolation
# ---------------------------------------------------------------------------

async def _run(scraper, db, **kw):
    return await probe.run_estado_diario_probe(
        scraper=scraper, lawyer_id=8, competencia="civil",
        today=TODAY, now=NOW, session_factory=lambda: db, **kw,
    )


class TestFlag:
    @pytest.mark.asyncio
    async def test_off_makes_zero_requests(self, monkeypatch, limiter, db):
        _enable(monkeypatch, False)
        page = _page(_resp(_table([])))
        await _run(_scraper(page), db)
        page.evaluate.assert_not_awaited()
        assert limiter.acquired == 0

    def test_default_is_off(self):
        from app.config import Settings
        assert Settings.model_fields["ESTADO_DIARIO_PROBE_ENABLED"].default is False


class TestSingleRequest:
    @pytest.mark.asyncio
    async def test_exactly_one_post_for_the_previous_business_day(
        self, monkeypatch, limiter, db, log
    ):
        _enable(monkeypatch)
        page = _page(_resp(_table([])))
        await _run(_scraper(page), db)
        assert page.evaluate.await_count == 1
        js = page.evaluate.await_args.args[0]
        assert "estadoDiarioFechaCivil.php" in js
        assert "fechaEstDiaCiv" in js
        assert "13/10/2026" in js  # Wednesday 14/10 -> Tuesday 13/10
        assert "POST" in js
        assert limiter.acquired == 1

    @pytest.mark.asyncio
    async def test_on_a_monday_the_request_asks_for_the_friday(
        self, monkeypatch, limiter, db, log
    ):
        _enable(monkeypatch)
        page = _page(_resp(_table([])))
        await probe.run_estado_diario_probe(
            scraper=_scraper(page), lawyer_id=8, competencia="civil",
            today=date(2026, 10, 19), now=NOW, session_factory=lambda: db,
        )
        assert "16/10/2026" in page.evaluate.await_args.args[0]

    @pytest.mark.asyncio
    async def test_non_civil_competencia_makes_no_request(
        self, monkeypatch, limiter, db
    ):
        _enable(monkeypatch)
        page = _page(_resp(_table([])))
        await probe.run_estado_diario_probe(
            scraper=_scraper(page), lawyer_id=8, competencia="laboral",
            today=TODAY, now=NOW, session_factory=lambda: db,
        )
        page.evaluate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_live_page_means_no_request_and_no_new_page(
        self, monkeypatch, limiter, db, log
    ):
        _enable(monkeypatch)
        scraper = MagicMock()
        scraper.live_page.return_value = None
        await _run(scraper, db)
        scraper._get_page.assert_not_called()
        scraper._page_for.assert_not_called()
        assert any("outcome=skipped" in l for l in _logged(log))


class TestLogLines:
    @pytest.mark.asyncio
    async def test_ok_line(self, monkeypatch, limiter, db, log):
        _enable(monkeypatch)
        _seed(db, "C-1-2026", NOW - timedelta(days=8))
        _seed(db, "C-2-2026", NOW - timedelta(days=96))
        body = _table([
            ("C-1-2026", "13/10/2026", "A/B", "1 Civil"),
            ("C-2-2026", "13/10/2026", "A/B", "1 Civil"),
            ("C-3-2026", "13/10/2026", "A/B", "1 Civil"),
        ])
        await _run(_scraper(_page(_resp(body))), db)
        line = _logged(log)[-1]
        assert line.startswith("ESTADO_DIARIO_PROBE ")
        for part in (
            "lawyer=8", "date=13/10/2026", "outcome=ok", "rows=3", "in_db=2",
            "not_in_db=1", "age_days_median=52.0", "age_days_max=96.0",
        ):
            assert part in line, line

    @pytest.mark.asyncio
    async def test_no_activity_is_info_not_warning(self, monkeypatch, limiter, db, log):
        _enable(monkeypatch)
        await _run(_scraper(_page(_resp(_table([])))), db)
        line = _logged(log)[-1]
        assert "outcome=no_activity" in line and "rows=0" in line
        log.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_bounce_is_warning_and_says_bounced(self, monkeypatch, limiter, db, log):
        _enable(monkeypatch)
        await _run(_scraper(_page(_resp(LOGIN_PAGE))), db)
        line = _logged(log)[-1]
        assert "outcome=bounced" in line
        assert "no_activity" not in line and "endpoint_changed" not in line
        log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_dead_endpoint_is_warning_and_says_endpoint_changed(
        self, monkeypatch, limiter, db, log
    ):
        _enable(monkeypatch)
        await _run(_scraper(_page(_resp("Not found", 404))), db)
        line = _logged(log)[-1]
        assert "outcome=endpoint_changed" in line and "status=404" in line
        assert "no_activity" not in line
        log.warning.assert_called()


class TestFailureIsolation:
    @pytest.mark.asyncio
    async def test_page_error_never_escapes(self, monkeypatch, limiter, db, log):
        _enable(monkeypatch)
        await _run(_scraper(_page(RuntimeError("Execution context was destroyed"))), db)
        assert "outcome=probe_error" in _logged(log)[-1]

    @pytest.mark.asyncio
    async def test_limiter_timeout_never_escapes(self, monkeypatch, db, log):
        _enable(monkeypatch)

        class Boom:
            async def acquire(self):
                raise TimeoutError("rate limit wait exceeded")

        monkeypatch.setattr(probe, "pjud_action_limiter", lambda a: Boom())
        monkeypatch.setattr(probe.asyncio, "sleep", AsyncMock())
        page = _page(_resp(_table([])))
        await _run(_scraper(page), db)
        page.evaluate.assert_not_awaited()
        assert "outcome=probe_error" in _logged(log)[-1]

    @pytest.mark.asyncio
    async def test_database_error_in_stats_never_escapes(self, monkeypatch, limiter, log):
        _enable(monkeypatch)

        def broken():
            raise RuntimeError("db gone")

        body = _table([("C-1-2026", "13/10/2026", "A/B", "1 Civil")])
        await probe.run_estado_diario_probe(
            scraper=_scraper(_page(_resp(body))), lawyer_id=8, competencia="civil",
            today=TODAY, now=NOW, session_factory=broken,
        )
        assert "outcome=probe_error" in _logged(log)[-1]
