from __future__ import annotations

import time
from collections import defaultdict
from threading import Event, Lock

from agent.memory import MemoryStoreError
from agent.memory.scheduler import MemoryExtractionScheduler


def test_scheduler_coalesces_repeated_session_schedules():
    calls = []
    scheduler = MemoryExtractionScheduler(
        lambda job: calls.append(job.generation),
        idle_seconds=0.05,
        max_workers=1,
    )
    try:
        for _ in range(10):
            assert scheduler.schedule("user-a", _Actor("user-a"), "session-a") is True

        assert _wait_until(lambda: len(calls) == 1)
        assert scheduler.pending_count() == 0
    finally:
        scheduler.shutdown()


def test_scheduler_limits_global_concurrency_and_serializes_one_actor():
    lock = Lock()
    release = Event()
    started = Event()
    active = 0
    max_active = 0
    active_by_actor = defaultdict(int)
    max_by_actor = defaultdict(int)
    completed = []

    def callback(job):
        nonlocal active, max_active
        with lock:
            active += 1
            active_by_actor[job.actor_key] += 1
            max_active = max(max_active, active)
            max_by_actor[job.actor_key] = max(
                max_by_actor[job.actor_key], active_by_actor[job.actor_key]
            )
            if active == 2:
                started.set()
        release.wait(timeout=2)
        with lock:
            completed.append(job.key)
            active_by_actor[job.actor_key] -= 1
            active -= 1

    scheduler = MemoryExtractionScheduler(callback, idle_seconds=0, max_workers=2)
    try:
        scheduler.schedule("user-a", _Actor("user-a"), "session-a")
        scheduler.schedule("user-a", _Actor("user-a"), "session-b")
        scheduler.schedule("user-b", _Actor("user-b"), "session-c")

        assert started.wait(timeout=2)
        with lock:
            assert max_active == 2
            assert max_by_actor["user-a"] == 1
        release.set()
        assert _wait_until(lambda: len(completed) == 3)
        assert max_by_actor["user-a"] == 1
    finally:
        release.set()
        scheduler.shutdown()


def test_scheduler_retries_only_retryable_provider_failure():
    calls = []

    def callback(job):
        calls.append(job.attempt)
        if job.attempt == 0:
            raise MemoryStoreError("MEMORY_EXTRACTION_PROVIDER_FAILED", "temporary")

    scheduler = MemoryExtractionScheduler(
        callback,
        idle_seconds=0,
        max_workers=1,
        retry_delays_seconds=(0.01,),
        retryable=lambda exc: (
            isinstance(exc, MemoryStoreError)
            and exc.code == "MEMORY_EXTRACTION_PROVIDER_FAILED"
        ),
    )
    try:
        scheduler.schedule("user-a", _Actor("user-a"), "session-a")
        assert _wait_until(lambda: calls == [0, 1])
    finally:
        scheduler.shutdown()


def test_scheduler_does_not_retry_non_retryable_failure():
    calls = []

    def callback(job):
        calls.append(job.attempt)
        raise MemoryStoreError("MEMORY_EXTRACTION_INVALID", "invalid")

    scheduler = MemoryExtractionScheduler(
        callback,
        idle_seconds=0,
        max_workers=1,
        retry_delays_seconds=(0.01,),
        retryable=lambda exc: (
            isinstance(exc, MemoryStoreError)
            and exc.code == "MEMORY_EXTRACTION_PROVIDER_FAILED"
        ),
    )
    try:
        scheduler.schedule("user-a", _Actor("user-a"), "session-a")
        assert _wait_until(lambda: calls == [0])
        time.sleep(0.05)
        assert calls == [0]
    finally:
        scheduler.shutdown()


def test_scheduler_cancel_prevents_pending_callback():
    calls = []
    scheduler = MemoryExtractionScheduler(
        lambda job: calls.append(job.key),
        idle_seconds=0.1,
        max_workers=1,
    )
    try:
        scheduler.schedule("user-a", _Actor("user-a"), "session-a")
        assert scheduler.cancel("user-a", "session-a") is True
        time.sleep(0.15)
        assert calls == []
    finally:
        scheduler.shutdown()


def test_scheduler_queue_limit_allows_existing_key_update_only():
    scheduler = MemoryExtractionScheduler(
        lambda _job: None,
        idle_seconds=60,
        max_workers=1,
        max_pending_jobs=1,
    )
    try:
        assert scheduler.schedule("user-a", _Actor("user-a"), "session-a") is True
        assert scheduler.schedule("user-a", _Actor("user-a"), "session-a") is True
        assert scheduler.schedule("user-b", _Actor("user-b"), "session-b") is False
        assert scheduler.pending_count() == 1
    finally:
        scheduler.shutdown()


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _Actor:
    def __init__(self, user_id: str):
        self.user_id = user_id
