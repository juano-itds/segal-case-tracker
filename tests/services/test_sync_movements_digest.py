"""sync_movements feeds the digest: alerts persisted as before, one email."""
from unittest.mock import patch

from app.models.alert import Alert
from app.models.case import Case
from app.models.court import Court
from app.models.lawyer import Lawyer
from app.services.sync_service import ScrapedMovement, SyncService


def _mov(i):
    return ScrapedMovement(
        folio=str(i), fecha="15/01/2026", tipo_tramite="Resolucion",
        descripcion=f"Mov {i}", etapa="Tramitacion",
    )


def _setup(db):
    lawyer = Lawyer(rut="16021492-9", name="Ana", email="ana@segal.cl", is_active=True)
    db.add(lawyer)
    db.flush()
    court = Court(code="DG-1", name="Juzgado Digest", region="RM", type="civil")
    db.add(court)
    db.flush()
    cases = [
        Case(lawyer_id=lawyer.id, court_id=court.id, rol=f"C-{n}-2026", competencia="civil")
        for n in (1, 2)
    ]
    db.add_all(cases)
    db.commit()
    return lawyer, cases


def test_five_new_movements_send_one_email_and_persist_five_alerts(db):
    lawyer, (case, _) = _setup(db)
    with patch("app.services.notification_service.settings") as st, patch(
        "app.services.notification_service.smtplib.SMTP"
    ) as MockSMTP:
        st.SMTP_HOST = "smtp.x.cl"; st.SMTP_PORT = 587; st.SMTP_USER = ""
        st.SMTP_PASSWORD = ""; st.SMTP_USE_TLS = False
        st.SMTP_FROM = "n@segal.cl"; st.FROM_EMAIL = "n@segal.cl"
        new, alerts = SyncService(db).sync_movements(case.id, [_mov(i) for i in range(5)])

    assert (new, alerts) == (5, 5)
    rows = db.query(Alert).filter(Alert.type == "new_movement").all()
    assert len(rows) == 5
    assert all(a.email_sent for a in rows)
    assert MockSMTP.return_value.send_message.call_count == 1


def test_shared_service_defers_until_the_batch_closes_it(db):
    from app.services.notification_service import NotificationService

    lawyer, (c1, c2) = _setup(db)
    svc = NotificationService(db)
    with patch("app.services.notification_service.settings") as st, patch(
        "app.services.notification_service.smtplib.SMTP"
    ) as MockSMTP:
        st.SMTP_HOST = "smtp.x.cl"; st.SMTP_PORT = 587; st.SMTP_USER = ""
        st.SMTP_PASSWORD = ""; st.SMTP_USE_TLS = False
        st.SMTP_FROM = "n@segal.cl"; st.FROM_EMAIL = "n@segal.cl"
        sync = SyncService(db)
        sync.sync_movements(c1.id, [_mov(1), _mov(2)], notification_svc=svc)
        sync.sync_movements(c2.id, [_mov(3)], notification_svc=svc)
        assert MockSMTP.return_value.send_message.call_count == 0
        svc.close()

    assert MockSMTP.return_value.send_message.call_count == 1
    msg = MockSMTP.return_value.send_message.call_args.args[0]
    assert msg["Subject"] == "3 movimientos nuevos en 2 causas"


def test_digest_failure_does_not_break_sync_and_alerts_stay_unmarked(db):
    lawyer, (case, _) = _setup(db)
    with patch("app.services.notification_service.settings") as st, patch(
        "app.services.notification_service.smtplib.SMTP", side_effect=OSError("down")
    ):
        st.SMTP_HOST = "smtp.x.cl"; st.SMTP_PORT = 587; st.SMTP_USER = ""
        st.SMTP_PASSWORD = ""; st.SMTP_USE_TLS = False
        st.SMTP_FROM = "n@segal.cl"; st.FROM_EMAIL = "n@segal.cl"
        new, alerts = SyncService(db).sync_movements(case.id, [_mov(1), _mov(2)])

    assert (new, alerts) == (2, 2)
    rows = db.query(Alert).filter(Alert.type == "new_movement").all()
    assert len(rows) == 2
    assert not any(a.email_sent for a in rows)
