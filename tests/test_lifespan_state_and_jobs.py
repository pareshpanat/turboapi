import asyncio
import json
import time

from turbo import (
    CeleryQueueAdapter,
    InMemoryJobQueue,
    RedisQueueAdapter,
    RQQueueAdapter,
    Request,
    RetryPolicy,
    TestClient,
    Turbo,
    app_state_dependency,
    get_app_state,
)


def test_lifespan_state_resource_bootstrap_and_cleanup():
    app = Turbo()
    events: list[str] = []

    async def open_db(app_ref):
        events.append("open")
        return {"dsn": "sqlite://demo"}

    async def close_db(value, app_ref):
        events.append(f"close:{value['dsn']}")

    app.add_state_resource("db", open_db, cleanup=close_db)

    @app.get("/db")
    async def db_info(req: Request, db=app_state_dependency("db", expected_type=dict)):
        dsn = get_app_state(req, "db", expected_type=dict)["dsn"]
        return {"dsn": dsn, "dep_dsn": db["dsn"]}

    with TestClient(app) as client:
        r = client.get("/db")
        assert r.status_code == 200
        body = r.json()
        assert body["dsn"] == "sqlite://demo"
        assert body["dep_dsn"] == "sqlite://demo"

    assert events == ["open", "close:sqlite://demo"]


def test_app_state_dependency_missing_is_500():
    app = Turbo()

    @app.get("/x")
    async def x(value=app_state_dependency("missing")):
        return {"value": value}

    with TestClient(app) as client:
        r = client.get("/x")
        assert r.status_code == 500
        assert r.json()["error"] == "Missing app state"


def test_inmemory_job_queue_retry_delay_and_idempotency():
    async def scenario():
        queue = InMemoryJobQueue()
        attempts = {"n": 0}

        async def handler(payload):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient")
            return {"ok": payload["to"]}

        queue.register("email.send", handler)
        await queue.start(workers=1)
        try:
            retry = RetryPolicy(max_retries=2, base_delay=0.01, backoff=1.0)
            job_id = await queue.enqueue("email.send", {"to": "alice@example.com"}, retry=retry, idempotency_key="email:alice")
            dup_id = await queue.enqueue("email.send", {"to": "alice@example.com"}, retry=retry, idempotency_key="email:alice")
            assert dup_id == job_id

            await queue.join(timeout=2.0)
            rec = queue.get_job(job_id)
            assert rec is not None
            assert rec.status == "succeeded"
            assert rec.attempts == 2
            assert rec.result == {"ok": "alice@example.com"}
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_inmemory_job_queue_schedule_time():
    async def scenario():
        queue = InMemoryJobQueue()
        observed: list[float] = []
        started = time.time()

        async def handler(payload):
            observed.append(time.time())
            return payload

        queue.register("demo.wait", handler)
        await queue.start(workers=1)
        try:
            await queue.enqueue("demo.wait", {"ok": True}, delay_seconds=0.05)
            await queue.join(timeout=2.0)
            assert observed
            assert (observed[0] - started) >= 0.04
        finally:
            await queue.stop()

    asyncio.run(scenario())


def test_queue_adapters_basic_wiring():
    async def scenario():
        class FakeCelery:
            def __init__(self):
                self.calls = []

            def send_task(self, name, **kwargs):
                self.calls.append((name, kwargs))
                return "task-1"

        class FakeRQ:
            def __init__(self):
                self.calls = []

            def enqueue_call(self, **kwargs):
                self.calls.append(kwargs)
                return "rq-1"

        class FakeRedis:
            def __init__(self):
                self.calls = []

            def rpush(self, key, value):
                self.calls.append((key, value))
                return 1

        celery = FakeCelery()
        rq = FakeRQ()
        redis = FakeRedis()

        celery_adapter = CeleryQueueAdapter(celery)
        rq_adapter = RQQueueAdapter(rq)
        redis_adapter = RedisQueueAdapter(redis, list_name="jobs:list")

        out1 = await celery_adapter.enqueue("send.email", {"to": "a"}, delay_seconds=3, idempotency_key="k1")
        out2 = await rq_adapter.enqueue(lambda payload: payload, {"to": "b"}, idempotency_key="k2")
        out3 = await redis_adapter.enqueue("send.email", {"to": "c"}, delay_seconds=1, idempotency_key="k3")

        assert out1 == "task-1"
        assert out2 == "rq-1"
        assert out3 == 1
        assert celery.calls and celery.calls[0][0] == "send.email"
        assert rq.calls and rq.calls[0]["job_id"] == "k2"
        body = json.loads(redis.calls[0][1])
        assert body["job_name"] == "send.email"
        assert body["idempotency_key"] == "k3"

    asyncio.run(scenario())
