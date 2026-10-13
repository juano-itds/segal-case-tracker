"""The digest tells the truth about volume, independently of the webhook cap.

Measured 2026-10-13: one batch detected 1901 new movements but the digest said
"399 movimientos nuevos" -- the per-case ``NotifyBudget`` (a webhook cost
control) was also deciding what the email counted. Contract pinned here:

- every new_movement of the batch counts toward the digest, budget or not;
- webhooks stay capped per case exactly as before;
- memory is bounded: at most MAX_DIGEST_ENTRIES entries kept, plus counters;
- the body renders at most that many entries and says what was left out;
- the subject carries the TRUE total;
- ``email_sent`` is set only on alerts the lawyer actually saw.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.models.alert import Alert
from app.models.case import Case
from app.models.court import Court
from app.models.lawyer import Lawyer
from app.services.movement_digest import (
    MAX_DIGEST_ENTRIES,
    DigestBucket,
    DigestEntry,
)
from app.services.notification_service import NotificationService
from app.services.sync_service import NotifyBudget, ScrapedMovement, SyncService
from tests.services.test_movement_digest import (
    _flush_capturing,
    _movement_for,
    _notify,
    _sent,
)
from tests.services.test_notification_service import _make_case, _make_lawyer


def _entry(i, case_id=1):
    return DigestEntry(
        alert=MagicMock(), case_id=case_id, rol=f"C-{case_id}-2026",
        caratulado="A/B", court_name="Juzgado", date_str="01-01-2026",
        procedure="Resolución", description=f"Descripción {i}",
    )


class TestBucket:
    def test_keeps_at_most_the_renderable_entries_but_counts_everything(self):
        bucket = DigestBucket()
        for i in range(1000):
            bucket.add(_entry(i, case_id=i % 40))
        assert len(bucket.entries) == MAX_DIGEST_ENTRIES == 150
        assert bucket.total_movements == 1000
        assert len(bucket.case_ids) == 40

    def test_under_the_cap_keeps_everything(self):
        bucket = DigestBucket()
        for i in range(10):
            bucket.add(_entry(i))
        assert len(bucket.entries) == 10
        assert bucket.total_movements == 10

    def test_dropped_entries_release_their_alert(self):
        bucket = DigestBucket()
        for i in range(MAX_DIGEST_ENTRIES + 5):
            bucket.add(_entry(i))
        kept = {id(e.alert) for e in bucket.entries}
        assert len(kept) == MAX_DIGEST_ENTRIES


def _many(service, lawyer, n, per_case=1):
    alerts = []
    for i in range(1, n + 1):
        case = _make_case(id=(i - 1) // per_case + 1, rol=f"C-{(i - 1) // per_case + 1}-2026")
        a, c, m, _ = _movement_for(i, case=case, lawyer=lawyer)
        alerts.append(a)
        _notify(service, a, c, m, lawyer)
    return alerts


class TestRenderCap:
    @pytest.fixture
    def service(self):
        return NotificationService(MagicMock())

    def test_subject_reports_the_true_total_not_the_rendered_count(self, service):
        _many(service, _make_lawyer(), 200)
        _, server = _flush_capturing(service)
        assert _sent(server)[0]["Subject"] == "200 movimientos nuevos en 200 causas"

    def test_body_renders_at_most_150_entries(self, service):
        _many(service, _make_lawyer(), 200)
        _, server = _flush_capturing(service)
        msg = _sent(server)[0]
        text = msg.get_body(("plain",)).get_content()
        html = msg.get_body(("html",)).get_content()
        assert text.count("  - Resolución") == 150
        assert html.count("Descripción ") == 150

    def test_closing_line_names_movements_and_causas_left_out(self, service):
        _many(service, _make_lawyer(), 200)
        _, server = _flush_capturing(service)
        msg = _sent(server)[0]
        text = msg.get_body(("plain",)).get_content()
        html = msg.get_body(("html",)).get_content()
        line = "y 50 movimientos más en 50 causas — míralos en la app"
        assert line in text
        assert line in html

    def test_closing_line_when_the_rest_lives_in_already_listed_causas(self, service):
        # 1 causa with 200 movements: nothing is left out at causa level.
        _many(service, _make_lawyer(), 200, per_case=200)
        _, server = _flush_capturing(service)
        msg = _sent(server)[0]
        assert msg["Subject"] == "200 movimientos nuevos en 1 causa"
        text = msg.get_body(("plain",)).get_content()
        assert "y 50 movimientos más" in text
        assert "causas ya listadas" in text

    def test_singular_forms(self, service):
        _many(service, _make_lawyer(), 151)
        _, server = _flush_capturing(service)
        text = _sent(server)[0].get_body(("plain",)).get_content()
        assert "y 1 movimiento más en 1 causa — míralos en la app" in text

    def test_exactly_150_has_no_closing_line(self, service):
        _many(service, _make_lawyer(), 150)
        _, server = _flush_capturing(service)
        msg = _sent(server)[0]
        for body in (
            msg.get_body(("plain",)).get_content(),
            msg.get_body(("html",)).get_content(),
        ):
            assert "míralos en la app" not in body

    def test_copy_is_tuteo(self, service):
        _many(service, _make_lawyer(), 200)
        _, server = _flush_capturing(service)
        msg = _sent(server)[0]
        body = msg.get_body(("plain",)).get_content() + msg.get_body(("html",)).get_content()
        for voseo in ("mirá", "tenés", "podés", "sos "):
            assert voseo not in body

    def test_email_sent_only_on_the_alerts_the_lawyer_saw(self, service):
        alerts = _many(service, _make_lawyer(), 200)
        _flush_capturing(service)
        assert all(a.email_sent is True for a in alerts[:150])
        assert all(a.email_sent is False for a in alerts[150:])
        assert all(a.email_sent_at is None for a in alerts[150:])

    def test_memory_stays_bounded_while_accumulating(self, service):
        lawyer = _make_lawyer()
        _many(service, lawyer, 400)
        _, bucket = service._digests[lawyer.id]
        assert len(bucket.entries) == 150
        assert bucket.total_movements == 400

    def test_digest_only_path_does_not_fire_webhooks(self, service):
        lawyer = _make_lawyer()
        a, c, m, _ = _movement_for(1, lawyer=lawyer)
        with patch.object(service, "_dispatch") as dispatch:
            service.accumulate_movement(a, c, m, lawyer)
        dispatch.assert_not_called()
        assert service._digests[lawyer.id][1].total_movements == 1

    def test_accumulate_never_raises(self, service):
        lawyer = _make_lawyer()
        a, c, m, _ = _movement_for(1, lawyer=lawyer)
        with patch(
            "app.services.movement_digest.DigestBucket.add",
            side_effect=RuntimeError("boom"),
        ):
            service.accumulate_movement(a, c, m, lawyer)  # must not raise


# ---------------------------------------------------------------------------
# Through sync_movements: the budget gates webhooks, not the digest.
# ---------------------------------------------------------------------------


def _mov(i):
    return ScrapedMovement(
        folio=str(i), fecha="15/01/2026", tipo_tramite="Resolucion",
        descripcion=f"Mov {i}", etapa="Tramitacion",
    )


def _setup(db):
    lawyer = Lawyer(rut="16021492-9", name="Ana", email="ana@segal.cl", is_active=True)
    db.add(lawyer)
    db.flush()
    court = Court(code="DG-2", name="Juzgado Cap", region="RM", type="civil")
    db.add(court)
    db.flush()
    case = Case(lawyer_id=lawyer.id, court_id=court.id, rol="C-9-2026", competencia="civil")
    db.add(case)
    db.commit()
    return lawyer, case


def _smtp(st):
    st.SMTP_HOST = "smtp.x.cl"; st.SMTP_PORT = 587; st.SMTP_USER = ""
    st.SMTP_PASSWORD = ""; st.SMTP_USE_TLS = False
    st.SMTP_FROM = "n@segal.cl"; st.FROM_EMAIL = "n@segal.cl"


def test_exhausted_budget_still_feeds_the_digest_but_not_the_webhooks(db):
    lawyer, case = _setup(db)
    svc = NotificationService(db)
    with patch("app.services.notification_service.settings") as st, patch(
        "app.services.notification_service.smtplib.SMTP"
    ) as MockSMTP, patch.object(
        NotificationService, "_dispatch"
    ) as dispatch:
        _smtp(st)
        new, alerts = SyncService(db).sync_movements(
            case.id, [_mov(i) for i in range(5)],
            notification_svc=svc, budget=NotifyBudget(2),
        )
        svc.close()

    assert (new, alerts) == (5, 5)
    # Webhook path: exactly as before -- capped at the budget.
    assert dispatch.call_count == 2
    # Digest path: all five, and the subject says so.
    assert MockSMTP.return_value.send_message.call_count == 1
    msg = MockSMTP.return_value.send_message.call_args.args[0]
    assert msg["Subject"] == "5 movimientos nuevos en 1 causa"
    rows = db.query(Alert).filter(Alert.type == "new_movement").all()
    assert len(rows) == 5 and all(a.email_sent for a in rows)


def test_zero_budget_means_zero_webhooks_but_a_full_digest(db):
    lawyer, case = _setup(db)
    svc = NotificationService(db)
    with patch("app.services.notification_service.settings") as st, patch(
        "app.services.notification_service.smtplib.SMTP"
    ) as MockSMTP, patch.object(NotificationService, "_dispatch") as dispatch:
        _smtp(st)
        SyncService(db).sync_movements(
            case.id, [_mov(i) for i in range(3)],
            notification_svc=svc, budget=NotifyBudget(0),
        )
        svc.close()
    assert dispatch.call_count == 0
    msg = MockSMTP.return_value.send_message.call_args.args[0]
    assert msg["Subject"] == "3 movimientos nuevos en 1 causa"


def test_a_digest_failure_on_the_over_budget_path_never_breaks_the_sync(db):
    lawyer, case = _setup(db)
    svc = NotificationService(db)
    with patch.object(
        NotificationService, "accumulate_movement", side_effect=RuntimeError("boom")
    ):
        new, alerts = SyncService(db).sync_movements(
            case.id, [_mov(i) for i in range(3)],
            notification_svc=svc, budget=NotifyBudget(0),
        )
    assert (new, alerts) == (3, 3)
    assert db.query(Alert).filter(Alert.type == "new_movement").count() == 3
