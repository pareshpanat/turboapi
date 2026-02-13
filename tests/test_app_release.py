import asyncio
import json
from turbo import Turbo, APIRouter, Depends, HTTPError, Request, Model

async def run_http(app, method="GET", path="/", query="", headers=None, body_chunks=None):
    chunks = list(body_chunks or [b""])
    sent = []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode("utf-8"),
        "headers": headers or [],
    }
    events = []
    for i, chunk in enumerate(chunks):
        events.append({"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1})

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)
    start = next(x for x in sent if x["type"] == "http.response.start")
    body = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    return start["status"], body

def test_include_router_and_openapi_metadata():
    app = Turbo()
    router = APIRouter(prefix="/v1", tags=["items"])

    @router.get("/items/{item_id}", summary="Get item", description="Fetches one item")
    async def get_item(item_id: int):
        return {"id": item_id}

    app.include_router(router)
    status, body = asyncio.run(run_http(app, path="/v1/items/7"))
    assert status == 200
    assert json.loads(body.decode("utf-8"))["id"] == 7

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    doc = json.loads(body.decode("utf-8"))
    op = doc["paths"]["/v1/items/{item_id}"]["get"]
    assert op["summary"] == "Get item"
    assert op["tags"] == ["items"]

def test_exception_handler_registration():
    app = Turbo()

    @app.exception_handler(ValueError)
    async def handle_value_error(req, exc):
        return {"error": str(exc), "kind": "value"}

    @app.get("/boom")
    async def boom():
        raise ValueError("bad")

    status, body = asyncio.run(run_http(app, path="/boom"))
    assert status == 200
    assert json.loads(body.decode("utf-8"))["kind"] == "value"

def test_dependency_nested_and_cleanup():
    app = Turbo()
    calls = []

    async def get_token(req: Request):
        return req.headers.get("x-token", "")

    async def check_auth(token=Depends(get_token)):
        return token == "ok"

    async def resource():
        calls.append("open")
        try:
            yield "db"
        finally:
            calls.append("close")

    @app.get("/secure")
    async def secure(allowed=Depends(check_auth), conn=Depends(resource)):
        if not allowed:
            raise HTTPError(401, "Unauthorized")
        return {"conn": conn}

    headers = [(b"x-token", b"ok")]
    status, body = asyncio.run(run_http(app, path="/secure", headers=headers))
    assert status == 200
    assert json.loads(body.decode("utf-8"))["conn"] == "db"
    assert calls == ["open", "close"]

def test_max_body_bytes_across_multiple_chunks():
    app = Turbo(max_body_bytes=5)

    class Payload(Model):
        v: str

    @app.post("/echo")
    async def echo(payload: Payload):
        return {"ok": True}

    status, _ = asyncio.run(run_http(app, method="POST", path="/echo", body_chunks=[b'{"v":"ab', b'cdef"}']))
    assert status == 413

def test_lifespan_startup_shutdown():
    app = Turbo()
    state = []

    @app.on_event("startup")
    async def on_startup():
        state.append("startup")

    @app.on_event("shutdown")
    async def on_shutdown():
        state.append("shutdown")

    sent = []
    receive_events = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

    async def receive():
        return receive_events.pop(0)

    async def send(msg):
        sent.append(msg)

    asyncio.run(app({"type": "lifespan"}, receive, send))
    assert state == ["startup", "shutdown"]
    assert sent[0]["type"] == "lifespan.startup.complete"
    assert sent[1]["type"] == "lifespan.shutdown.complete"
