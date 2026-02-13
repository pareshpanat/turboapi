import asyncio

from turbo import Turbo


async def _run_one(app, path="/ping"):
    sent = []
    scope = {"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": []}
    events = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)
    status = next(x for x in sent if x["type"] == "http.response.start")["status"]
    return status


def test_runtime_soak_parallel_requests():
    app = Turbo(max_concurrency=200)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    async def scenario():
        batch = 250
        rounds = 8
        for _ in range(rounds):
            statuses = await asyncio.gather(*[_run_one(app) for _ in range(batch)])
            assert all(code == 200 for code in statuses)

    asyncio.run(scenario())
