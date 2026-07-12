"""Hot-reload polling: one idle SSE tick over a fully-populated manager cache.

Each open browser tab pays this every EVENTS_POLL_INTERVAL (0.3s), so the
per-tick cost times tabs x 3.3Hz is the steady-state background load.
"""

from __future__ import annotations

from cobrastyle.serve import poll_changes


def test_poll_tick_idle(benchmark, warm_manager):
    seen: dict[str, tuple[object, ...]] = {}
    poll_changes(warm_manager, seen)

    changed = benchmark(poll_changes, warm_manager, seen)
    assert changed == []
