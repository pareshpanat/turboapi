from __future__ import annotations

import asyncio
import inspect
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Protocol

from .utils import call_callable


@dataclass(slots=True)
class RetryPolicy:
    max_retries: int = 0
    base_delay: float = 1.0
    backoff: float = 2.0
    max_delay: float = 60.0
    jitter: float = 0.0

    def next_delay(self, attempt: int) -> float:
        delay = float(self.base_delay) * (float(self.backoff) ** max(0, attempt - 1))
        delay = min(delay, float(self.max_delay))
        if self.jitter > 0:
            delay = delay + random.uniform(0.0, float(self.jitter))
        return max(0.0, delay)


@dataclass(slots=True)
class JobRecord:
    id: str
    name: str
    payload: Any
    run_at: float
    retry_policy: RetryPolicy
    idempotency_key: Optional[str] = None
    status: str = "queued"
    attempts: int = 0
    result: Any = None
    error: Optional[str] = None
    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())


class JobQueueAdapter(Protocol):
    async def enqueue(self, *args, **kwargs):
        ...


def _run_at_timestamp(*, delay_seconds: float | None = None, run_at: datetime | float | None = None) -> float:
    if run_at is not None:
        if isinstance(run_at, datetime):
            return float(run_at.timestamp())
        return float(run_at)
    return time.time() + max(0.0, float(delay_seconds or 0.0))


async def _call_job_handler(handler: Callable[..., Any], payload: Any, *, job: JobRecord, queue: "InMemoryJobQueue"):
    kwargs = {"payload": payload, "job": job, "queue": queue}
    try:
        sig = inspect.signature(handler)
        params = sig.parameters
        if "payload" in params:
            return await call_callable(handler, **kwargs)
        if len(params) == 0:
            return await call_callable(handler)
        if len(params) == 1:
            return await call_callable(handler, payload)
        filtered = {k: v for k, v in kwargs.items() if k in params}
        return await call_callable(handler, **filtered)
    except (TypeError, ValueError):
        return await call_callable(handler, payload)


class InMemoryJobQueue:
    def __init__(self):
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._jobs: dict[str, JobRecord] = {}
        self._schedule: list[tuple[float, int, str]] = []
        self._seq = 0
        self._lock = asyncio.Lock()
        self._wake = asyncio.Condition()
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._active_jobs = 0
        self._idempotency: dict[str, str] = {}

    def register(self, name: str, handler: Callable[..., Any]):
        self._handlers[name] = handler

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    async def enqueue(
        self,
        name: str,
        payload: Any = None,
        *,
        delay_seconds: float | None = None,
        run_at: datetime | float | None = None,
        retry: RetryPolicy | None = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        if idempotency_key and idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]

        if name not in self._handlers:
            raise ValueError(f"Unknown job handler: {name}")

        job_id = uuid.uuid4().hex
        record = JobRecord(
            id=job_id,
            name=name,
            payload=payload,
            run_at=_run_at_timestamp(delay_seconds=delay_seconds, run_at=run_at),
            retry_policy=retry or RetryPolicy(),
            idempotency_key=idempotency_key,
        )
        self._jobs[job_id] = record
        if idempotency_key:
            self._idempotency[idempotency_key] = job_id
        await self._schedule_job(record)
        return job_id

    async def _schedule_job(self, job: JobRecord):
        async with self._lock:
            self._seq += 1
            self._schedule.append((job.run_at, self._seq, job.id))
            self._schedule.sort(key=lambda x: (x[0], x[1]))
        async with self._wake:
            self._wake.notify_all()

    async def start(self, *, workers: int = 1):
        if self._running:
            return
        self._running = True
        for idx in range(max(1, int(workers))):
            self._workers.append(asyncio.create_task(self._worker_loop(idx)))

    async def stop(self):
        self._running = False
        async with self._wake:
            self._wake.notify_all()
        for t in self._workers:
            t.cancel()
        for t in self._workers:
            try:
                await t
            except BaseException:
                pass
        self._workers.clear()

    async def join(self, *, timeout: Optional[float] = None):
        started = time.time()
        while True:
            async with self._lock:
                pending = bool(self._schedule) or self._active_jobs > 0
            if not pending:
                return
            if timeout is not None and (time.time() - started) > float(timeout):
                raise TimeoutError("Timed out waiting for queue to drain")
            await asyncio.sleep(0.01)

    async def _worker_loop(self, _worker_id: int):
        while self._running:
            job = await self._next_due_job()
            if job is None:
                continue
            await self._run_job(job)

    async def _next_due_job(self) -> Optional[JobRecord]:
        while self._running:
            wait_for = None
            async with self._lock:
                if self._schedule:
                    run_at, _seq, job_id = self._schedule[0]
                    now = time.time()
                    if run_at <= now:
                        self._schedule.pop(0)
                        job = self._jobs.get(job_id)
                        if job is None:
                            continue
                        self._active_jobs += 1
                        return job
                    wait_for = max(0.0, run_at - now)
            async with self._wake:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=wait_for if wait_for is not None else 0.5)
                except TimeoutError:
                    pass
        return None

    async def _run_job(self, job: JobRecord):
        try:
            handler = self._handlers[job.name]
            job.status = "running"
            job.attempts += 1
            job.updated_at = time.time()
            job.result = await _call_job_handler(handler, job.payload, job=job, queue=self)
            job.status = "succeeded"
            job.error = None
            job.updated_at = time.time()
        except Exception as exc:
            can_retry = job.attempts <= int(job.retry_policy.max_retries)
            if can_retry:
                delay = job.retry_policy.next_delay(job.attempts)
                job.status = "queued"
                job.error = str(exc)
                job.run_at = time.time() + delay
                job.updated_at = time.time()
                await self._schedule_job(job)
            else:
                job.status = "failed"
                job.error = str(exc)
                job.updated_at = time.time()
        finally:
            async with self._lock:
                self._active_jobs = max(0, self._active_jobs - 1)


class CeleryQueueAdapter:
    def __init__(self, celery_app):
        self.celery_app = celery_app

    async def enqueue(self, task_name: str, payload: Any = None, *, delay_seconds: float | None = None, run_at: datetime | None = None, idempotency_key: Optional[str] = None):
        kwargs = {"payload": payload}
        options: dict[str, Any] = {}
        if delay_seconds is not None:
            options["countdown"] = float(delay_seconds)
        if run_at is not None:
            options["eta"] = run_at
        if idempotency_key:
            options["task_id"] = idempotency_key
        return await call_callable(self.celery_app.send_task, task_name, kwargs=kwargs, **options)


class RQQueueAdapter:
    def __init__(self, rq_queue):
        self.rq_queue = rq_queue

    async def enqueue(self, func: Callable[..., Any], payload: Any = None, *, delay_seconds: float | None = None, run_at: datetime | None = None, idempotency_key: Optional[str] = None):
        call_kwargs: dict[str, Any] = {"func": func, "kwargs": {"payload": payload}}
        if idempotency_key:
            call_kwargs["job_id"] = idempotency_key
        if run_at is not None:
            call_kwargs["at_front"] = False
            call_kwargs["enqueue_at"] = run_at
        elif delay_seconds is not None:
            call_kwargs["enqueue_in"] = float(delay_seconds)

        def _invoke():
            return self.rq_queue.enqueue_call(**call_kwargs)

        return await call_callable(_invoke)


class RedisQueueAdapter:
    def __init__(self, redis_client, *, list_name: str = "turbo:jobs"):
        self.redis_client = redis_client
        self.list_name = list_name

    async def enqueue(self, job_name: str, payload: Any = None, *, delay_seconds: float | None = None, run_at: datetime | None = None, idempotency_key: Optional[str] = None):
        body = {
            "job_name": job_name,
            "payload": payload,
            "delay_seconds": delay_seconds,
            "run_at": run_at.isoformat() if isinstance(run_at, datetime) else None,
            "idempotency_key": idempotency_key,
        }
        return await call_callable(self.redis_client.rpush, self.list_name, json.dumps(body, separators=(",", ":"), ensure_ascii=False))
