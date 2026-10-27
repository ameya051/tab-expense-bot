"""Run chart rendering off the event loop."""

import logging
import time

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


async def render_chart(render, *args) -> bytes:
    """Draw a chart in a worker thread (CPU-bound) and log its size and time."""
    started = time.perf_counter()
    chart = await run_in_threadpool(render, *args)
    logger.info(
        "%s -> <%d bytes> (%.0f ms)",
        render.__name__, len(chart), (time.perf_counter() - started) * 1000,
    )
    return chart
