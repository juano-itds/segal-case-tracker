"""Where the estado-diario probe hooks into sync_lawyer_cases.

The probe is observe-only. These tests pin that the cycle does exactly the
same thing with the probe on or off, that it runs once and BEFORE the rotation
selection (delta-sync T1: the feed has to exist before the batch is chosen),
and that nothing it does can break the cycle.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings


class _Harness:
    def __init__(self, *, mov_errors=None, probe_effect=None):
        self.db = MagicMock()
        self.store = MagicMock()
        self.store.get_session_by_lawyer = AsyncMock(return_value=MagicMock())
        self.calls: list[str] = []

        self.page = MagicMock()
        self.page.is_closed.return_value = False
        self.page.evaluate = AsyncMock(return_value={"status": 200, "body": ""})
        self.scraper = MagicMock()
        self.scraper.live_page.return_value = self.page
        self.scraper.get_my_cases = AsyncMock(return_value=[])
        self.scraper.close = AsyncMock()

        self.rotation = [MagicMock(), MagicMock()]

        def select_effect(**kwargs):
            self.calls.append("select")
            return self.rotation

        self._select_effect = select_effect

        async def detect(**kwargs):
            self.calls.append("detect")
            return (0, 0, list(mov_errors or []))

        self.detect = AsyncMock(side_effect=detect)

        async def fake_probe(**kwargs):
            self.calls.append("probe")
            if probe_effect:
                raise probe_effect

        self.probe = AsyncMock(side_effect=fake_probe)

    async def run(self, *, enabled: bool) -> dict:
        from app.workers.sync_scheduler import sync_lawyer_cases

        with patch("app.workers.sync_scheduler.get_session_store", return_value=self.store), \
             patch("app.api.v1.pjud.get_scraper", return_value=self.scraper), \
             patch("app.workers.sync_scheduler.SyncService") as sync_service_cls, \
             patch(
                 "app.workers.sync_scheduler._select_cases_for_detail_rotation",
                 side_effect=self._select_effect,
             ) as select, \
             patch("app.workers.sync_scheduler.detect_and_sync_movements", self.detect), \
             patch("app.workers.sync_scheduler.run_estado_diario_probe", self.probe), \
             patch.object(settings, "ESTADO_DIARIO_PROBE_ENABLED", enabled):
            sync_service_cls.return_value.sync_cases.return_value = MagicMock(
                cases_total=0, cases_new=0
            )
            self.sync_cases = sync_service_cls.return_value.sync_cases
            self.select = select
            return await sync_lawyer_cases(lawyer_id=1, competencia="civil", db=self.db)


@pytest.mark.asyncio
async def test_flag_off_never_calls_the_probe():
    h = _Harness()
    await h.run(enabled=False)
    h.probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_flag_on_calls_the_probe_exactly_once_before_the_rotation_selection():
    h = _Harness()
    await h.run(enabled=True)
    assert h.probe.await_count == 1
    assert h.calls == ["probe", "select", "detect"]
    kwargs = h.probe.await_args.kwargs
    assert kwargs["scraper"] is h.scraper
    assert kwargs["lawyer_id"] == 1
    assert kwargs["competencia"] == "civil"


@pytest.mark.asyncio
async def test_sync_behaviour_is_identical_with_the_probe_on_and_off():
    off, on = _Harness(), _Harness()
    result_off = await off.run(enabled=False)
    result_on = await on.run(enabled=True)

    assert result_on == result_off
    # Same rotation selection and same detail-phase inputs.
    sel_on, sel_off = on.select.call_args.kwargs, off.select.call_args.kwargs
    for key in ("lawyer_id", "competencia", "batch_size"):
        assert sel_on[key] == sel_off[key]
    assert on.detect.await_args.kwargs["selected_cases"] is on.rotation
    for key in ("lawyer_id", "delay_between_fetches", "batch_size_cap"):
        assert on.detect.await_args.kwargs[key] == off.detect.await_args.kwargs[key]
    assert on.sync_cases.call_args == off.sync_cases.call_args
    # The cycle's own DB session is untouched by the probe (no writes, no alerts).
    assert [c[0] for c in on.db.mock_calls] == [c[0] for c in off.db.mock_calls]


@pytest.mark.asyncio
async def test_a_probe_that_raises_does_not_break_the_cycle():
    h = _Harness(probe_effect=RuntimeError("probe blew up"))
    result = await h.run(enabled=True)
    assert result["success"] is True


@pytest.mark.asyncio
async def test_a_failing_probe_leaves_the_selection_and_detail_phase_untouched():
    h = _Harness(probe_effect=RuntimeError("probe blew up"))
    result = await h.run(enabled=True)
    assert result["success"] is True
    assert h.calls == ["probe", "select", "detect"]
    assert h.detect.await_args.kwargs["selected_cases"] is h.rotation


@pytest.mark.asyncio
async def test_the_probe_still_runs_when_the_batch_is_later_cut_short():
    # The probe now runs BEFORE the batch, so a cut-short batch cannot gate it
    # any more (it used to: a stopped batch meant a flagged IP). The Shape-block
    # guard lives in the detail phase itself, which stops on its own.
    h = _Harness(mov_errors=["lote detenido: PJUD Shape challenge"])
    await h.run(enabled=True)
    h.probe.assert_awaited_once()
