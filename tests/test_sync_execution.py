import asyncio
import json
import threading

from turbo import Depends, Turbo


async def run_http(app, method="GET", path="/", headers=None, body=b""):
    sent = []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }
    events = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)
    status = next(x for x in sent if x["type"] == "http.response.start")["status"]
    body_bytes = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    return status, json.loads(body_bytes.decode("utf-8"))


def test_sync_handler_runs_off_event_loop():
    app = Turbo()

    @app.get("/sync")
    def sync_route():
        return {"thread_id": threading.get_ident()}

    loop_thread_id = None

    async def _run():
        nonlocal loop_thread_id
        loop_thread_id = threading.get_ident()
        return await run_http(app, path="/sync")

    status, payload = asyncio.run(_run())
    assert status == 200
    assert payload["thread_id"] != loop_thread_id


def test_sync_dependency_runs_off_event_loop():
    app = Turbo()

    def sync_dep():
        return threading.get_ident()

    @app.get("/dep")
    async def dep_route(dep_thread=Depends(sync_dep)):
        return {"dep_thread": dep_thread, "handler_thread": threading.get_ident()}

    loop_thread_id = None

    async def _run():
        nonlocal loop_thread_id
        loop_thread_id = threading.get_ident()
        return await run_http(app, path="/dep")

    status, payload = asyncio.run(_run())
    assert status == 200
    assert payload["handler_thread"] == loop_thread_id
    assert payload["dep_thread"] != loop_thread_id
