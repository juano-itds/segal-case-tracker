"""One NotificationService -- and therefore one SMTP connection -- per batch.

Measured 2026-10-12: `email` was 51-55% of the scraping cycle because a fresh
SMTP connection, TLS handshake and login were paid per email (~3.4s each).
Reusing the connection fixes that only if the service itself survives more
than one causa: it used to be built inside the per-case `_do_fetch` closure,
so a batch of 80 causas still opened 80 connections.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.case import Case
from app.models.court import Court
from app.models.lawyer import Lawyer


def _detail(rol):
    d = MagicMock()
    d.case_documents = []
    d.movements = []
    d.litigantes = []
    d.notificaciones = []
    d.escritos = []
    d.exhortos = []
    d.case = MagicMock()
    d.case.rol = rol
    return d


@pytest.mark.asyncio
async def test_one_notification_service_for_the_whole_batch(db):
    from app.services import sync_service
    from app.services.sync_service import detect_and_sync_movements

    lawyer = Lawyer(rut="40000009-9", name="Lawyer Batch", is_active=True)
    db.add(lawyer)
    db.flush()
    court = Court(code="BATCH-COURT", name="Batch Court", region="RM", type="civil")
    db.add(court)
    db.flush()
    roles = ["C-11-2026", "C-22-2026", "C-33-2026"]
    for rol in roles:
        db.add(Case(lawyer_id=lawyer.id, court_id=court.id, rol=rol,
                    competencia="civil", status="active"))
    db.commit()

    api_cases = []
    for rol in roles:
        ac = MagicMock()
        ac.rol = rol
        ac.case_token = f"tok-{rol}"
        api_cases.append(ac)

    scraper = MagicMock()
    scraper.get_case_detail = AsyncMock(side_effect=[_detail(r) for r in roles])

    with patch.object(sync_service, "NotificationService") as MockSvc:
        await detect_and_sync_movements(
            db=db, scraper=scraper, pjud_session=MagicMock(),
            lawyer_id=lawyer.id, api_cases=api_cases, selected_cases=api_cases,
        )

    assert MockSvc.call_count == 1, (
        f"built {MockSvc.call_count} NotificationService for {len(roles)} causas; "
        "one per causa means one SMTP connection per causa"
    )
    MockSvc.return_value.close.assert_called_once()
