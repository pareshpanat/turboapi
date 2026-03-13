import asyncio
import json

from turbo import APIRouter, Depends, HTTPError, Turbo, api_key_auth


async def run_http(app, method="GET", path="/", headers=None):
    sent = []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }
    events = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)
    start = next(x for x in sent if x["type"] == "http.response.start")
    body = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    return start["status"], json.loads(body.decode("utf-8"))


def test_app_router_route_and_include_dependencies_execute():
    calls = []

    async def app_dep():
        calls.append("app")

    async def router_dep():
        calls.append("router")

    async def route_dep():
        calls.append("route")

    async def include_dep():
        calls.append("include")

    app = Turbo(dependencies=[Depends(app_dep)])
    router = APIRouter(prefix="/v1", dependencies=[Depends(router_dep)])

    @router.get("/items", dependencies=[Depends(route_dep)])
    async def items():
        calls.append("handler")
        return {"ok": True}

    app.include_router(router, dependencies=[Depends(include_dep)])

    status, body = asyncio.run(run_http(app, path="/v1/items"))
    assert status == 200
    assert body["ok"] is True
    assert calls == ["app", "router", "route", "include", "handler"]


def test_dependency_guard_blocks_handler():
    app = Turbo()
    calls = []

    async def guard():
        calls.append("guard")
        raise HTTPError(401, "Unauthorized")

    @app.get("/secure", dependencies=[Depends(guard)])
    async def secure():
        calls.append("handler")
        return {"ok": True}

    status, body = asyncio.run(run_http(app, path="/secure"))
    assert status == 401
    assert body["error"] == "Unauthorized"
    assert calls == ["guard"]


def test_route_dependencies_infer_openapi_security():
    app = Turbo()
    key_dep = api_key_auth("X-API-Key")

    @app.get("/secure", dependencies=[Depends(key_dep)])
    async def secure():
        return {"ok": True}

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    op = body["paths"]["/secure"]["get"]
    assert op["security"] == [{"ApiKeyAuth": []}]
    assert body["components"]["securitySchemes"]["ApiKeyAuth"]["type"] == "apiKey"
