import asyncio
import json
from uuid import uuid4

from turbo import APIRouter, Turbo


async def run_http(app, method="GET", path="/", query="", headers=None, body=b""):
    sent = []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode("utf-8"),
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
    start = next(x for x in sent if x["type"] == "http.response.start")
    payload = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    return start, payload


def test_patch_head_options_and_add_api_route():
    app = Turbo()

    @app.patch("/items/{item_id:int}", status_code=202)
    async def patch_item(item_id: int):
        return {"id": item_id, "updated": True}

    @app.head("/ping")
    async def ping_head():
        return {"ok": True}

    async def options_handler():
        return {"ok": True}

    app.add_api_route("/opts", options_handler, methods=["OPTIONS"])

    start, payload = asyncio.run(run_http(app, method="PATCH", path="/items/12"))
    assert start["status"] == 202
    assert json.loads(payload.decode("utf-8"))["id"] == 12

    start, _ = asyncio.run(run_http(app, method="HEAD", path="/ping"))
    assert start["status"] == 200

    start, payload = asyncio.run(run_http(app, method="OPTIONS", path="/opts"))
    assert start["status"] == 200
    assert json.loads(payload.decode("utf-8"))["ok"] is True


def test_route_level_extras_operation_id_include_schema_status_code():
    app = Turbo()

    @app.get("/visible", operation_id="customVisible", status_code=201)
    async def visible():
        return {"ok": True}

    @app.get("/hidden", include_in_schema=False)
    async def hidden():
        return {"ok": True}

    start, payload = asyncio.run(run_http(app, path="/visible"))
    assert start["status"] == 201
    assert json.loads(payload.decode("utf-8"))["ok"] is True

    start, payload = asyncio.run(run_http(app, path="/openapi.json"))
    assert start["status"] == 200
    doc = json.loads(payload.decode("utf-8"))
    assert "/visible" in doc["paths"]
    assert "/hidden" not in doc["paths"]
    assert doc["paths"]["/visible"]["get"]["operationId"] == "customVisible"
    assert "201" in doc["paths"]["/visible"]["get"]["responses"]


def test_trailing_slash_redirect_configurable():
    app_redirect = Turbo(redirect_slashes=True, redirect_status_code=308)

    @app_redirect.get("/items")
    async def items():
        return {"ok": True}

    start, _ = asyncio.run(run_http(app_redirect, path="/items/"))
    assert start["status"] == 308
    headers = dict(start["headers"])
    assert headers[b"location"] == b"/items"

    app_no_redirect = Turbo(redirect_slashes=False)

    @app_no_redirect.get("/items")
    async def items2():
        return {"ok": True}

    start, _ = asyncio.run(run_http(app_no_redirect, path="/items/"))
    assert start["status"] == 404


def test_path_converters_int_uuid_path():
    app = Turbo()
    uid = str(uuid4())

    @app.get("/users/{user_id:int}")
    async def user(user_id: int):
        return {"id": user_id}

    @app.get("/keys/{key:uuid}")
    async def key(key: str):
        return {"key": key}

    @app.get("/files/{file_path:path}")
    async def file_path(file_path: str):
        return {"path": file_path}

    start, payload = asyncio.run(run_http(app, path="/users/42"))
    assert start["status"] == 200
    assert json.loads(payload.decode("utf-8"))["id"] == 42

    start, _ = asyncio.run(run_http(app, path="/users/not-int"))
    assert start["status"] == 404

    start, payload = asyncio.run(run_http(app, path=f"/keys/{uid}"))
    assert start["status"] == 200
    assert json.loads(payload.decode("utf-8"))["key"] == uid

    start, _ = asyncio.run(run_http(app, path="/keys/not-a-uuid"))
    assert start["status"] == 404

    start, payload = asyncio.run(run_http(app, path="/files/a/b/c.txt"))
    assert start["status"] == 200
    assert json.loads(payload.decode("utf-8"))["path"] == "a/b/c.txt"


def test_api_router_new_methods_passthrough():
    app = Turbo()
    router = APIRouter(prefix="/r")

    @router.patch("/p")
    async def rp():
        return {"ok": "patch"}

    @router.options("/o")
    async def ro():
        return {"ok": "options"}

    @router.head("/h")
    async def rh():
        return {"ok": "head"}

    app.include_router(router)

    start, payload = asyncio.run(run_http(app, method="PATCH", path="/r/p"))
    assert start["status"] == 200
    assert json.loads(payload.decode("utf-8"))["ok"] == "patch"

    start, payload = asyncio.run(run_http(app, method="OPTIONS", path="/r/o"))
    assert start["status"] == 200
    assert json.loads(payload.decode("utf-8"))["ok"] == "options"

    start, _ = asyncio.run(run_http(app, method="HEAD", path="/r/h"))
    assert start["status"] == 200
