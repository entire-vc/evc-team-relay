"""MinIO bucket-size collector: never two walks at once, and it runs on its own interval."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY

from app.core.config import Settings
from app.workers import metrics_worker


class _SlowMinio:
    """Stand-in for the MinIO client (an external boundary): a walk that outlasts the interval.

    Records how many walks are in flight at once so the test can assert the max.
    """

    in_flight = 0
    max_in_flight = 0
    walks_started = 0
    _guard = threading.Lock()
    release = threading.Event()

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    def list_objects(self, bucket: str, recursive: bool = False):  # noqa: ANN201
        cls = _SlowMinio
        with cls._guard:
            cls.in_flight += 1
            cls.walks_started += 1
            cls.max_in_flight = max(cls.max_in_flight, cls.in_flight)
        try:
            cls.release.wait(timeout=5)
            yield SimpleNamespace(size=100)
            yield SimpleNamespace(size=23)
        finally:
            with cls._guard:
                cls.in_flight -= 1


@pytest.fixture(autouse=True)
def _reset_slow_minio(monkeypatch: pytest.MonkeyPatch):
    _SlowMinio.in_flight = 0
    _SlowMinio.max_in_flight = 0
    _SlowMinio.walks_started = 0
    _SlowMinio.release = threading.Event()
    monkeypatch.setattr(metrics_worker, "Minio", _SlowMinio)
    yield
    _SlowMinio.release.set()


def _skipped() -> float:
    return REGISTRY.get_sample_value("metrics_collection_errors_total", {"collector": "minio"}) or 0


def test_overlapping_minio_walks_run_at_most_one_at_a_time() -> None:
    """A walk longer than the cycle must not be joined by the next cycle's walk."""
    skipped_before = _skipped()

    first = threading.Thread(target=metrics_worker._collect_minio_metrics)
    first.start()
    deadline = time.monotonic() + 2
    while _SlowMinio.walks_started < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _SlowMinio.walks_started == 1

    # Next cycles fire while the first walk is still running (what wait_for timeout leaves behind).
    for _ in range(3):
        assert metrics_worker._collect_minio_metrics() is False

    assert _SlowMinio.walks_started == 1, "a second walk started while the first was running"
    assert _SlowMinio.max_in_flight == 1
    assert _skipped() - skipped_before == 3

    _SlowMinio.release.set()
    first.join(timeout=5)
    assert not first.is_alive()

    # Once the walk is done the next cycle is allowed to run again and publishes the size.
    assert metrics_worker._collect_minio_metrics() is True
    assert _SlowMinio.walks_started == 2
    settings = metrics_worker.get_settings()
    size = REGISTRY.get_sample_value("minio_bucket_size_bytes", {"bucket": settings.minio_bucket})
    assert size == 123


def test_minio_walk_is_due_on_its_own_interval() -> None:
    due = metrics_worker._minio_collection_due
    assert due(None, 1000.0, 900) is True  # first cycle after startup
    assert due(1000.0, 1060.0, 900) is False  # the 60 s DB cadence must not trigger it
    assert due(1000.0, 1899.0, 900) is False
    assert due(1000.0, 1900.0, 900) is True


def test_failed_minio_walk_reports_not_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop retries next cycle only if it can tell a failed walk from a good one."""

    class _Broken(_SlowMinio):
        def list_objects(self, bucket: str, recursive: bool = False):  # noqa: ANN201
            raise RuntimeError("minio down")

    monkeypatch.setattr(metrics_worker, "Minio", _Broken)
    before = _skipped()
    assert metrics_worker._collect_minio_metrics() is False
    assert _skipped() - before == 1
    # the lock must be free again after a failure
    assert metrics_worker._minio_walk_lock.acquire(blocking=False)
    metrics_worker._minio_walk_lock.release()


def test_minio_interval_default_is_much_slower_than_db_cycle() -> None:
    interval = Settings.model_fields["metrics_minio_interval_seconds"].default
    assert interval == 900
    assert interval > metrics_worker.COLLECT_INTERVAL_SECONDS
