import asyncio
import json
import os

from turbo import (
    EventSourceResponse,
    JSONResponse,
    NegotiatedResponse,
    RedirectResponse,
    Request,
    SSEEvent,
    Turbo,
    TurboSettings,
    build_cache_control,
    with_cache_headers,
)


async def run_http_events(app, method="GET", path="/", headers=None, body=b""):
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
    return sent


def _body_from_events(events):
    return b"".join(x.get("body", b"") for x in events if x["type"] == "http.response.body")


def test_redirect_response_and_cache_headers():
    app = Turbo()

    @app.get("/go")
    async def go():
        return RedirectResponse("/target", status=302)

    cc = build_cache_control(public=True, max_age=60, immutable=True)
    assert cc == "public, max-age=60, immutable"

    @app.get("/cache")
    async def cache():
        headers = with_cache_headers(cache_control=build_cache_control(public=True, max_age=120), etag='"abc"')
        return JSONResponse({"ok": True}, headers=headers)

    events = asyncio.run(run_http_events(app, path="/go"))
    start = next(x for x in events if x["type"] == "http.response.start")
    h = dict(start["headers"])
    assert start["status"] == 302
    assert h[b"location"] == b"/target"

    events = asyncio.run(run_http_events(app, path="/cache"))
    start = next(x for x in events if x["type"] == "http.response.start")
    h = dict(start["headers"])
    assert h[b"cache-control"] == b"public, max-age=120"
    assert h[b"etag"] == b'"abc"'


def test_negotiated_response():
    app = Turbo()

    @app.get("/n")
    async def n(req: Request):
        return NegotiatedResponse(
            req.headers.get("accept", ""),
            {
                "application/json": {"ok": True},
                "text/plain; charset=utf-8": "ok",
            },
            default_media_type="application/json",
        )

    events = asyncio.run(run_http_events(app, path="/n", headers=[(b"accept", b"text/plain")]))
    start = next(x for x in events if x["type"] == "http.response.start")
    h = dict(start["headers"])
    assert h[b"content-type"] == b"text/plain; charset=utf-8"
    assert _body_from_events(events) == b"ok"

    events = asyncio.run(run_http_events(app, path="/n", headers=[(b"accept", b"application/xml")]))
    start = next(x for x in events if x["type"] == "http.response.start")
    assert start["status"] == 200
    h = dict(start["headers"])
    assert h[b"content-type"] == b"application/json"
    assert json.loads(_body_from_events(events).decode("utf-8"))["ok"] is True


def test_event_source_response():
    app = Turbo()

    @app.get("/events")
    async def events():
        async def stream():
            yield SSEEvent(data={"v": 1}, event="update", id="1")
            yield {"data": "done", "event": "final"}
        return EventSourceResponse(stream(), ping_interval=None)

    out = asyncio.run(run_http_events(app, path="/events"))
    start = next(x for x in out if x["type"] == "http.response.start")
    h = dict(start["headers"])
    body = _body_from_events(out).decode("utf-8")
    assert h[b"content-type"] == b"text/event-stream; charset=utf-8"
    assert "event: update" in body
    assert "id: 1" in body
    assert 'data: {"v":1}' in body
    assert "event: final" in body
    assert "data: done" in body


def test_settings_from_env_and_factory():
    prev = dict(os.environ)
    try:
        os.environ["TURBO_REQUEST_TIMEOUT"] = "2.5"
        os.environ["TURBO_MAX_BODY_BYTES"] = "2048"
        os.environ["TURBO_REDIRECT_SLASHES"] = "false"
        os.environ["TURBO_TITLE"] = "Configured"
        settings = TurboSettings.from_env()
        app = Turbo.from_settings(settings)
        app2 = Turbo.from_env()
        assert settings.request_timeout == 2.5
        assert settings.max_body_bytes == 2048
        assert settings.redirect_slashes is False
        assert app.request_timeout == 2.5
        assert app.max_body_bytes == 2048
        assert app2.redirect_slashes is False
    finally:
        os.environ.clear()
        os.environ.update(prev)


def test_shutdown_drain_waits_for_inflight():
    async def scenario():
        app = Turbo(shutdown_drain_timeout=1.0)
        gate = asyncio.Event()

        @app.get("/slow")
        async def slow():
            await gate.wait()
            return {"ok": True}

        req_sent = []
        req_scope = {"type": "http", "method": "GET", "path": "/slow", "query_string": b"", "headers": []}
        req_events = [{"type": "http.request", "body": b"", "more_body": False}]

        async def req_receive():
            if req_events:
                return req_events.pop(0)
            return {"type": "http.disconnect"}

        async def req_send(msg):
            req_sent.append(msg)

        req_task = asyncio.create_task(app(req_scope, req_receive, req_send))
        await asyncio.sleep(0)

        life_sent = []
        life_scope = {"type": "lifespan"}
        life_events = [{"type": "lifespan.shutdown"}]

        async def life_receive():
            if life_events:
                return life_events.pop(0)
            await asyncio.sleep(0)
            return {"type": "lifespan.shutdown"}

        async def life_send(msg):
            life_sent.append(msg)

        life_task = asyncio.create_task(app(life_scope, life_receive, life_send))
        try:
            await asyncio.wait_for(asyncio.shield(life_task), timeout=0.05)
            finished_early = True
        except TimeoutError:
            finished_early = False
        assert finished_early is False

        gate.set()
        await req_task
        await life_task
        assert any(m.get("type") == "lifespan.shutdown.complete" for m in life_sent)

    asyncio.run(scenario())
