"""Bounded in-process scheduler for idle Session Memory extraction."""

from __future__ import annotations

import heapq
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Condition, Event, Thread
from typing import Any, Callable

from agent.logging_utils import log_event

memory_logger = logging.getLogger("zcagent.agent.memory")
JobKey = tuple[str, str]


@dataclass
class MemoryExtractionJob:
    """Latest scheduled extraction for one actor and Session."""

    actor_key: str
    actor: Any
    session_id: str
    due_at: float
    generation: int
    attempt: int = 0
    cancelled: Event = field(default_factory=Event, repr=False)

    @property
    def key(self) -> JobKey:
        return self.actor_key, self.session_id


class MemoryExtractionScheduler:
    """Coalesce idle jobs and execute them with bounded global/user concurrency."""

    def __init__(
        self,
        callback: Callable[[MemoryExtractionJob], Any],
        *,
        idle_seconds: float = 300.0,
        max_workers: int = 2,
        max_pending_jobs: int = 1000,
        max_retries: int = 2,
        retry_delays_seconds: tuple[float, ...] = (30.0, 120.0),
        retryable: Callable[[BaseException], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if idle_seconds < 0:
            raise ValueError("idle_seconds must be non-negative")
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if max_pending_jobs < 1:
            raise ValueError("max_pending_jobs must be positive")
        self.callback = callback
        self.idle_seconds = idle_seconds
        self.max_workers = max_workers
        self.max_pending_jobs = max_pending_jobs
        self.max_retries = max_retries
        self.retry_delays_seconds = retry_delays_seconds
        self.retryable = retryable or (lambda _exc: False)
        self.clock = clock
        self._condition = Condition()
        self._jobs: dict[JobKey, MemoryExtractionJob] = {}
        self._heap: list[tuple[float, int, int, JobKey]] = []
        self._running_jobs: dict[JobKey, MemoryExtractionJob] = {}
        self._running_actor_keys: set[str] = set()
        self._generation = 0
        self._sequence = 0
        self._inflight = 0
        self._accepting = True
        self._stopping = False
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="zcagent-memory",
        )
        self._coordinator = Thread(
            target=self._run,
            name="zcagent-memory-scheduler",
            daemon=True,
        )
        self._coordinator.start()
        log_event(memory_logger, logging.DEBUG, "memory.scheduler.start", max_workers=max_workers)

    def schedule(
        self,
        actor_key: str,
        actor: Any,
        session_id: str,
        *,
        delay_seconds: float | None = None,
    ) -> bool:
        """Schedule or replace one actor/Session job without blocking the caller."""

        key = actor_key, session_id
        with self._condition:
            if not self._accepting:
                return False
            if (
                key not in self._jobs
                and key not in self._running_jobs
                and len(self._jobs) >= self.max_pending_jobs
            ):
                log_event(
                    memory_logger,
                    logging.WARNING,
                    "memory.extraction.queue_full",
                    actor_user_id=_actor_user_id(actor),
                    session_id=session_id,
                    queue_size=len(self._jobs),
                )
                return False
            running = self._running_jobs.get(key)
            if running is not None:
                running.cancelled.set()
            self._generation += 1
            self._sequence += 1
            job = MemoryExtractionJob(
                actor_key=actor_key,
                actor=actor,
                session_id=session_id,
                due_at=self.clock()
                + (self.idle_seconds if delay_seconds is None else max(0.0, delay_seconds)),
                generation=self._generation,
            )
            self._jobs[key] = job
            heapq.heappush(
                self._heap,
                (job.due_at, self._sequence, job.generation, key),
            )
            self._compact_heap_if_needed()
            self._condition.notify_all()
            return True

    def cancel(self, actor_key: str, session_id: str) -> bool:
        """Cancel the pending job and invalidate an already-running job result."""

        key = actor_key, session_id
        with self._condition:
            pending = self._jobs.pop(key, None)
            running = self._running_jobs.get(key)
            if running is not None:
                running.cancelled.set()
            self._condition.notify_all()
            return pending is not None or running is not None

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop scheduling, cancel pending work, and close the bounded executor."""

        with self._condition:
            if self._stopping:
                return
            self._accepting = False
            self._stopping = True
            self._jobs.clear()
            for job in self._running_jobs.values():
                job.cancelled.set()
            self._condition.notify_all()
        self._coordinator.join(timeout=5.0 if wait else 0.0)
        self._executor.shutdown(wait=wait, cancel_futures=True)
        log_event(memory_logger, logging.DEBUG, "memory.scheduler.stop")

    def pending_count(self) -> int:
        with self._condition:
            return len(self._jobs)

    def inflight_count(self) -> int:
        with self._condition:
            return self._inflight

    def _run(self) -> None:
        while True:
            submissions: list[MemoryExtractionJob] = []
            with self._condition:
                while not self._stopping:
                    submissions = self._take_runnable_jobs()
                    if submissions:
                        break
                    timeout = self._next_wait_seconds()
                    self._condition.wait(timeout=timeout)
                if self._stopping:
                    return
            for job in submissions:
                try:
                    future = self._executor.submit(self.callback, job)
                except RuntimeError:
                    with self._condition:
                        self._running_jobs.pop(job.key, None)
                        self._running_actor_keys.discard(job.actor_key)
                        self._inflight = max(0, self._inflight - 1)
                        self._condition.notify_all()
                    continue
                future.add_done_callback(
                    lambda item, scheduled=job: self._complete(scheduled, item)
                )

    def _take_runnable_jobs(self) -> list[MemoryExtractionJob]:
        capacity = self.max_workers - self._inflight
        if capacity <= 0:
            return []
        now = self.clock()
        blocked: list[tuple[float, int, int, JobKey]] = []
        runnable: list[MemoryExtractionJob] = []
        while self._heap and len(runnable) < capacity:
            due_at, sequence, generation, key = heapq.heappop(self._heap)
            job = self._jobs.get(key)
            if job is None or job.generation != generation:
                continue
            if due_at > now:
                heapq.heappush(self._heap, (due_at, sequence, generation, key))
                break
            if job.actor_key in self._running_actor_keys:
                blocked.append((due_at, sequence, generation, key))
                continue
            self._jobs.pop(key, None)
            self._running_jobs[key] = job
            self._running_actor_keys.add(job.actor_key)
            self._inflight += 1
            runnable.append(job)
        for item in blocked:
            heapq.heappush(self._heap, item)
        return runnable

    def _complete(self, job: MemoryExtractionJob, future: Future[Any]) -> None:
        try:
            error = future.exception()
        except BaseException as exc:  # cancelled futures still need bookkeeping.
            error = exc
        with self._condition:
            self._running_jobs.pop(job.key, None)
            self._running_actor_keys.discard(job.actor_key)
            self._inflight = max(0, self._inflight - 1)
            if error is not None and not job.cancelled.is_set():
                if self.retryable(error) and job.attempt < self.max_retries:
                    delay = self._retry_delay(job.attempt)
                    if job.key not in self._jobs and self._accepting:
                        self._generation += 1
                        self._sequence += 1
                        retry_job = MemoryExtractionJob(
                            actor_key=job.actor_key,
                            actor=job.actor,
                            session_id=job.session_id,
                            due_at=self.clock() + delay,
                            generation=self._generation,
                            attempt=job.attempt + 1,
                        )
                        self._jobs[job.key] = retry_job
                        heapq.heappush(
                            self._heap,
                            (
                                retry_job.due_at,
                                self._sequence,
                                retry_job.generation,
                                retry_job.key,
                            ),
                        )
                        log_event(
                            memory_logger,
                            logging.WARNING,
                            "memory.extraction.retry",
                            actor_user_id=_actor_user_id(job.actor),
                            session_id=job.session_id,
                            attempt=retry_job.attempt,
                            retry_in_seconds=delay,
                            error_type=type(error).__name__,
                            reason_code=str(getattr(error, "code", "")),
                        )
                else:
                    log_event(
                        memory_logger,
                        logging.ERROR,
                        "memory.extraction.error",
                        actor_user_id=_actor_user_id(job.actor),
                        session_id=job.session_id,
                        attempt=job.attempt,
                        error_type=type(error).__name__,
                        reason_code=str(getattr(error, "code", "")),
                    )
            self._condition.notify_all()

    def _next_wait_seconds(self) -> float | None:
        self._discard_stale_heap_head()
        if not self._heap or self._inflight >= self.max_workers:
            return None
        eligible_due_times = []
        for due_at, _sequence, generation, key in self._heap:
            job = self._jobs.get(key)
            if (
                job is not None
                and job.generation == generation
                and job.actor_key not in self._running_actor_keys
            ):
                eligible_due_times.append(due_at)
        if not eligible_due_times:
            return None
        return max(0.0, min(eligible_due_times) - self.clock())

    def _discard_stale_heap_head(self) -> None:
        while self._heap:
            _due_at, _sequence, generation, key = self._heap[0]
            job = self._jobs.get(key)
            if job is not None and job.generation == generation:
                return
            heapq.heappop(self._heap)

    def _compact_heap_if_needed(self) -> None:
        if len(self._heap) <= len(self._jobs) * 2 + 100:
            return
        self._heap = [
            (job.due_at, index, job.generation, job.key)
            for index, job in enumerate(self._jobs.values(), start=1)
        ]
        heapq.heapify(self._heap)

    def _retry_delay(self, completed_attempt: int) -> float:
        if not self.retry_delays_seconds:
            return 0.0
        index = min(completed_attempt, len(self.retry_delays_seconds) - 1)
        return max(0.0, self.retry_delays_seconds[index])


def _actor_user_id(actor: Any) -> str:
    return str(getattr(actor, "user_id", "") or "")
