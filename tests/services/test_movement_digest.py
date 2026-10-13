"""One digest email per lawyer for new movements, instead of one per movement.

Measured 2026-10-12: 624 of the 630 emails sent in a day were `new_movement`;
one lawyer received 204. Contract pinned here:

- new_movement emails accumulate and go out as ONE email per lawyer;
- actionable alerts (deadlines, semaforo...) are NEVER deferred;
- webhooks keep firing per movement, immediately;
- alerts are persisted as before; `email_sent` is truthful;
- a digest failure never raises, and nothing accumulated sends nothing.
"""
import smtplib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.services.movement_digest import DigestBucket
from app.services.notification_service import NotificationService
from tests.services.test_notification_service import (
    _make_alert,
    _make_case,
    _make_lawyer,
    _make_movement,
    _make_webhook,
    _smtp_settings,
)


@pytest.fixture
def service():
    return NotificationService(MagicMock())


def _movement_for(i, case=None, lawyer=None, **kw):
    case = case or _make_case()
    lawyer = lawyer or _make_lawyer()
    alert = _make_alert(id=i)
    movement = _make_movement(
        id=i, procedure="Resolución", description=f"Descripción {i}", **kw
    )
    return alert, case, movement, lawyer


def _notify(service, alert, case, movement, lawyer, webhooks=()):
    service.notify_new_movement(alert, case, movement, lawyer, list(webhooks))


def _flush_capturing(service):
    """Flush with SMTP mocked; return (smtp_class_mock, server_mock)."""
    with patch("app.services.notification_service.settings") as st:
        _smtp_settings(st)
        with patch("app.services.notification_service.smtplib.SMTP") as MockSMTP:
            service.flush_digests()
    return MockSMTP, MockSMTP.return_value


def _sent(server):
    return [c.args[0] for c in server.send_message.call_args_list]


class TestGrouping:
    def test_many_movements_of_one_lawyer_send_exactly_one_email(self, service):
        lawyer = _make_lawyer()
        for i in range(1, 6):
            _notify(service, *_movement_for(i, lawyer=lawyer))

        _, server = _flush_capturing(service)

        assert server.send_message.call_count == 1

    def test_two_lawyers_get_two_emails_each_to_its_own_address(self, service):
        a = _make_lawyer(id=1, email="a@x.cl")
        b = _make_lawyer(id=2, email="b@x.cl")
        _notify(service, *_movement_for(1, lawyer=a))
        _notify(service, *_movement_for(2, lawyer=b))
        _notify(service, *_movement_for(3, lawyer=a))

        _, server = _flush_capturing(service)

        recipients = sorted(m["To"] for m in _sent(server))
        assert recipients == ["a@x.cl", "b@x.cl"]

    def test_subject_counts_movements_and_causas(self, service):
        lawyer = _make_lawyer()
        c1 = _make_case(id=1, rol="C-1-2026")
        c2 = _make_case(id=2, rol="C-2-2026")
        for i, c in enumerate([c1, c1, c2], start=1):
            _notify(service, *_movement_for(i, case=c, lawyer=lawyer))

        _, server = _flush_capturing(service)

        assert _sent(server)[0]["Subject"] == "3 movimientos nuevos en 2 causas"

    def test_subject_singular(self, service):
        _notify(service, *_movement_for(1))
        _, server = _flush_capturing(service)
        assert _sent(server)[0]["Subject"] == "1 movimiento nuevo en 1 causa"

    def test_body_groups_movements_under_their_causa(self, service):
        lawyer = _make_lawyer()
        c1 = _make_case(id=1, rol="C-111-2026")
        c2 = _make_case(id=2, rol="C-222-2026")
        _notify(service, *_movement_for(1, case=c1, lawyer=lawyer))
        _notify(service, *_movement_for(2, case=c2, lawyer=lawyer))
        _notify(service, *_movement_for(3, case=c1, lawyer=lawyer))

        _, server = _flush_capturing(service)

        text = _sent(server)[0].get_body(("plain",)).get_content()
        # Each causa appears once, with its own movements beneath it.
        assert text.count("C-111-2026") == 1
        assert text.count("C-222-2026") == 1
        c111, c222 = text.index("C-111-2026"), text.index("C-222-2026")
        assert c111 < text.index("Descripción 1") < text.index("Descripción 3") < c222
        assert text.index("Descripción 2") > c222

    def test_copy_is_neutral_tuteo_never_voseo(self, service):
        _notify(service, *_movement_for(1))
        _, server = _flush_capturing(service)
        msg = _sent(server)[0]
        body = msg.get_body(("plain",)).get_content() + msg.get_body(("html",)).get_content()
        assert "tienes" in body
        for voseo in ("tenés", "podés", "mirá", "fijate", "tenes "):
            assert voseo not in body

    def test_html_escapes_scraped_text(self, service):
        alert, case, movement, lawyer = _movement_for(1)
        movement.description = "<script>alert(1)</script>"
        _notify(service, alert, case, movement, lawyer)
        _, server = _flush_capturing(service)
        html = _sent(server)[0].get_body(("html",)).get_content()
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestEmptyDigest:
    def test_nothing_accumulated_sends_nothing(self, service):
        MockSMTP, server = _flush_capturing(service)
        assert server.send_message.call_count == 0
        assert MockSMTP.call_count == 0, "must not even open a connection"

    def test_an_empty_bucket_never_produces_an_email(self, service):
        service._digests = {99: (_make_lawyer(), DigestBucket())}
        MockSMTP, server = _flush_capturing(service)
        assert server.send_message.call_count == 0

    def test_flushing_twice_does_not_resend(self, service):
        _notify(service, *_movement_for(1))
        _, server = _flush_capturing(service)
        assert server.send_message.call_count == 1
        _, server2 = _flush_capturing(service)
        assert server2.send_message.call_count == 0


class TestActionableStaysImmediate:
    def test_deadline_alert_emails_immediately_and_is_not_queued(self, service):
        alert = _make_alert(type="deadline_fatal")
        case = _make_case()
        lawyer = _make_lawyer()
        with patch.object(service, "send_email_alert", return_value=True) as email:
            with patch.object(service, "send_webhook", return_value=True):
                service.notify_deadline_alert(alert, case, lawyer, [_make_webhook()])

        email.assert_called_once_with(alert, lawyer)
        assert service._digests == {}, "an actionable alert must never wait for a digest"

    def test_notificacion_escrito_exhorto_are_not_deferred(self, service):
        # Only new_movement is deferred; the other notify_* keep emailing now.
        lawyer = _make_lawyer()
        with patch.object(service, "send_email_alert", return_value=True) as email:
            with patch.object(service, "send_webhook", return_value=True):
                service._dispatch(_make_alert(), lawyer, [], {"event": "x"})
        email.assert_called_once()


class TestWebhooksStayImmediate:
    def test_webhook_fires_per_movement_before_any_flush(self, service):
        lawyer = _make_lawyer()
        hook = _make_webhook()
        with patch.object(service, "send_webhook", return_value=True) as wh:
            for i in range(1, 4):
                alert, case, movement, _ = _movement_for(i, lawyer=lawyer)
                _notify(service, alert, case, movement, lawyer, [hook])
            assert wh.call_count == 3, "one webhook per movement, no grouping"
            assert alert.webhook_sent is True

    def test_no_email_is_sent_at_notify_time(self, service):
        with patch.object(service, "send_email_alert") as email:
            _notify(service, *_movement_for(1))
        email.assert_not_called()


class TestEmailSentBookkeeping:
    def test_alerts_covered_by_a_sent_digest_are_marked(self, service):
        lawyer = _make_lawyer()
        alerts = []
        for i in range(1, 4):
            a, c, m, _ = _movement_for(i, lawyer=lawyer)
            alerts.append(a)
            _notify(service, a, c, m, lawyer)
        # Not marked while still waiting.
        assert all(a.email_sent is False for a in alerts)

        _flush_capturing(service)

        assert all(a.email_sent is True for a in alerts)
        assert all(isinstance(a.email_sent_at, datetime) for a in alerts)

    def test_failed_digest_does_not_mark_and_does_not_raise(self, service):
        a, c, m, lawyer = _movement_for(1)
        _notify(service, a, c, m, lawyer)
        with patch("app.services.notification_service.settings") as st:
            _smtp_settings(st)
            with patch("app.services.notification_service.smtplib.SMTP") as MockSMTP:
                MockSMTP.return_value.send_message.side_effect = (
                    smtplib.SMTPRecipientsRefused({"x@y.cl": (550, b"no")})
                )
                service.flush_digests()  # must not raise

        assert a.email_sent is False
        assert a.email_sent_at is None

    def test_one_lawyers_failure_does_not_stop_the_next(self, service):
        bad = _make_lawyer(id=1, email="bad@x.cl")
        good = _make_lawyer(id=2, email="good@x.cl")
        ab, cb, mb, _ = _movement_for(1, lawyer=bad)
        ag, cg, mg, _ = _movement_for(2, lawyer=good)
        _notify(service, ab, cb, mb, bad)
        _notify(service, ag, cg, mg, good)
        with patch("app.services.notification_service.settings") as st:
            _smtp_settings(st)
            with patch("app.services.notification_service.smtplib.SMTP") as MockSMTP:
                MockSMTP.return_value.send_message.side_effect = [
                    smtplib.SMTPRecipientsRefused({"bad@x.cl": (550, b"no")}),
                    None,
                ]
                service.flush_digests()

        assert ab.email_sent is False
        assert ag.email_sent is True

    def test_lawyer_without_email_is_skipped_unmarked(self, service):
        lawyer = _make_lawyer(email=None)
        a, c, m, _ = _movement_for(1, lawyer=lawyer)
        _notify(service, a, c, m, lawyer)
        MockSMTP, server = _flush_capturing(service)
        assert server.send_message.call_count == 0
        assert a.email_sent is False

    def test_unconfigured_smtp_never_raises(self, service):
        a, c, m, lawyer = _movement_for(1)
        _notify(service, a, c, m, lawyer)
        with patch("app.services.notification_service.settings") as st:
            st.SMTP_HOST = ""
            service.flush_digests()
        assert a.email_sent is False


class TestFlushSemantics:
    def test_close_flushes_so_forgetting_flush_never_loses_emails(self, service):
        a, c, m, lawyer = _movement_for(1)
        _notify(service, a, c, m, lawyer)
        with patch("app.services.notification_service.settings") as st:
            _smtp_settings(st)
            with patch("app.services.notification_service.smtplib.SMTP") as MockSMTP:
                service.close()
        assert MockSMTP.return_value.send_message.call_count == 1
        assert a.email_sent is True

    def test_leaving_a_with_block_flushes(self):
        svc = NotificationService(MagicMock())
        a, c, m, lawyer = _movement_for(1)
        with patch("app.services.notification_service.settings") as st:
            _smtp_settings(st)
            with patch("app.services.notification_service.smtplib.SMTP") as MockSMTP:
                with svc:
                    _notify(svc, a, c, m, lawyer)
                    assert MockSMTP.return_value.send_message.call_count == 0
        assert MockSMTP.return_value.send_message.call_count == 1

    def test_a_render_failure_is_swallowed(self, service):
        a, c, m, lawyer = _movement_for(1)
        _notify(service, a, c, m, lawyer)
        with patch(
            "app.services.movement_digest.render_movement_digest",
            side_effect=RuntimeError("boom"),
        ):
            with patch("app.services.notification_service.settings") as st:
                _smtp_settings(st)
                service.flush_digests()  # must not raise
        assert a.email_sent is False
