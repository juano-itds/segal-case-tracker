"""Per-document time budget for PJUD downloads.

Kept in its own module (no logger) so both the station path and the API path
can share one constant without importing the downloader.
"""

import asyncio

# Hard ceiling for ONE document download attempt (get a page + browser fetch).
# ``Page.evaluate`` has no timeout of its own and ``_get_page`` was measured at
# 16.6 minutes, so without this an unresolved fetch freezes the whole cycle.
# 120s comes from 19,616 real downloads in the station log (``fetching`` ->
# ``downloaded``): median 1.4s, p90 2.7s, p99 10.6s; only 4 exceeded 120s and
# those were the hangs, not slow downloads. 120s is ~11x the p99.
# It is per DOCUMENT (and per retry attempt), never per case: documents are
# fetched one at a time, so a case with many documents is not penalised.
DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS = 120.0


async def download_with_budget(scraper, **kwargs) -> bytes:
    """Run ``scraper.download_document_generic`` under the per-document budget.

    Raises ``asyncio.TimeoutError`` when the budget elapses. The constant is read
    at call time so tests (and ops) can override it on the module.
    """
    return await asyncio.wait_for(
        scraper.download_document_generic(**kwargs),
        timeout=DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS,
    )
