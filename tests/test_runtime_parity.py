import asyncio
import json
import os
import tempfile
from turbo import Turbo, WebSocket, StreamingResponse, FileResponse, BackgroundTask, Host, Depends, websocket_token_auth

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

def test_streaming_and_background_task():
    app = Turbo()
    calls = []

    async def bg():
        calls.append("done")

    @app.get("/stream")
    async def stream():
        async def chunks():
            yield b"a"
            yield b"b"
        return StreamingResponse(chunks(), background=BackgroundTask(bg))

    events = asyncio.run(run_http_events(app, path="/stream"))
    assert _body_from_events(events) == b"ab"
    assert calls == ["done"]

def test_file_response_and_static_mount():
    app = Turbo()
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "hello.txt")
        with open(p, "wb") as f:
            f.write(b"hello")

        @app.get("/file")
        async def file_route():
            return FileResponse(p)

        app.mount_static("/static", tmp)
        events = asyncio.run(run_http_events(app, path="/file"))
        assert _body_from_events(events) == b"hello"

        events = asyncio.run(run_http_events(app, path="/static/hello.txt"))
        assert _body_from_events(events) == b"hello"

def test_file_response_conditional_and_range():
    app = Turbo()
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "r.txt")
        with open(p, "wb") as f:
            f.write(b"0123456789")

        @app.get("/r")
        async def file_route():
            return FileResponse(p)

        events = asyncio.run(run_http_events(app, path="/r"))
        start = next(x for x in events if x["type"] == "http.response.start")
        etag = dict(start["headers"]).get(b"etag")
        assert etag is not None

        events = asyncio.run(run_http_events(app, path="/r", headers=[(b"if-none-match", etag)]))
        start = next(x for x in events if x["type"] == "http.response.start")
        assert start["status"] == 304

        events = asyncio.run(run_http_events(app, path="/r", headers=[(b"range", b"bytes=2-5")]))
        start = next(x for x in events if x["type"] == "http.response.start")
        assert start["status"] == 206
        assert _body_from_events(events) == b"2345"

        events = asyncio.run(run_http_events(app, path="/r", headers=[(b"range", b"bytes=100-200")]))
        start = next(x for x in events if x["type"] == "http.response.start")
        assert start["status"] == 416

def test_asgi_middleware_v2_factory():
    app = Turbo()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    def add_header_middleware(next_app):
        async def wrapped(scope, receive, send):
            async def send_wrapper(msg):
                if msg.get("type") == "http.response.start":
                    headers = list(msg.get("headers", []))
                    headers.append((b"x-asgi-mw", b"1"))
                    msg = dict(msg)
                    msg["headers"] = headers
                await send(msg)
            await next_app(scope, receive, send_wrapper)
        return wrapped

    app.use_asgi(add_header_middleware)
    events = asyncio.run(run_http_events(app, path="/ping"))
    start = next(x for x in events if x["type"] == "http.response.start")
    assert (b"x-asgi-mw", b"1") in start["headers"]

def test_websocket_route():
    app = Turbo()
    sent = []

    @app.websocket("/ws/{name}")
    async def ws_chat(ws: WebSocket, name: str):
        await ws.accept()
        msg = await ws.receive_text()
        await ws.send_text(f"{name}:{msg}")
        await ws.close(1000)

    events = [
        {"type": "websocket.connect"},
        {"type": "websocket.receive", "text": "hi"},
    ]

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "websocket", "path": "/ws/alice", "query_string": b"", "headers": []}
    asyncio.run(app(scope, receive, send))
    assert any(m.get("type") == "websocket.accept" for m in sent)
    assert any(m.get("type") == "websocket.send" and m.get("text") == "alice:hi" for m in sent)

def test_subapp_mount_prefix():
    main = Turbo()
    sub = Turbo()

    @sub.get("/ping")
    async def sub_ping():
        return {"app": "sub"}

    main.mount("/v2", sub)
    events = asyncio.run(run_http_events(main, path="/v2/ping"))
    assert b'"sub"' in _body_from_events(events)

def test_host_based_mount_and_host_param():
    main = Turbo()
    sub = Turbo()

    @main.get("/who")
    async def who_main():
        return {"app": "main"}

    @sub.get("/who")
    async def who_sub(host: str = Host()):
        return {"app": "sub", "host": host}

    main.mount_host("*.example.com", sub)
    headers = [(b"host", b"api.example.com")]
    events = asyncio.run(run_http_events(main, path="/who", headers=headers))
    body = json.loads(_body_from_events(events).decode("utf-8"))
    assert body["app"] == "sub"
    assert body["host"] == "api.example.com"

def test_websocket_dependency_auth_and_openapi_ws_extension():
    app = Turbo()
    dep = websocket_token_auth("token")
    sent = []

    @app.websocket("/ws-sec")
    async def ws_sec(ws: WebSocket, token=Depends(dep)):
        await ws.accept()
        await ws.send_text(token)
        await ws.close()

    events = [{"type": "websocket.connect"}]
    async def receive():
        if events:
            return events.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "websocket", "path": "/ws-sec", "query_string": b"token=q123", "headers": []}
    asyncio.run(app(scope, receive, send))
    assert any(m.get("type") == "websocket.send" and m.get("text") == "q123" for m in sent)

    events = asyncio.run(run_http_events(app, path="/openapi.json"))
    doc = json.loads(_body_from_events(events).decode("utf-8"))
    assert "x-turbo-websockets" in doc
    ws_ops = doc["x-turbo-websockets"]
    assert any(op.get("path") == "/ws-sec" for op in ws_ops)
