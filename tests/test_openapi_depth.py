import asyncio
import json

from turbo import HTTPError, Request, Turbo


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
    return start["status"], body


def test_operation_id_strategy_method_path_and_uniqueness():
    app = Turbo(operation_id_strategy="method_path")

    @app.get("/users/{user_id:int}")
    async def get_user(user_id: int):
        return {"id": user_id}

    @app.get("/a", operation_id="dupOp")
    async def op_a():
        return {"ok": "a"}

    @app.get("/b", operation_id="dupOp")
    async def op_b():
        return {"ok": "b"}

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    doc = json.loads(body.decode("utf-8"))
    ids = [doc["paths"][p]["get"]["operationId"] for p in doc["paths"] if "get" in doc["paths"][p]]
    assert any(i.startswith("get_users_user_id_int") for i in ids)
    assert "dupOp" in ids
    assert "dupOp_2" in ids
    assert len(ids) == len(set(ids))


def test_docs_custom_assets_and_docs_auth_guard():
    async def docs_guard(req: Request):
        if req.headers.get("x-docs-token") != "ok":
            raise HTTPError(401, "docs denied")
        return True

    app = Turbo(
        openapi_url="/schema",
        docs_url="/documentation",
        redoc_url="/readme",
        swagger_js_url="/static/swagger.js",
        swagger_css_url="/static/swagger.css",
        redoc_js_url="/static/redoc.js",
        docs_auth=docs_guard,
    )

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    status, _ = asyncio.run(run_http(app, path="/documentation"))
    assert status == 401

    headers = [(b"x-docs-token", b"ok")]
    status, body = asyncio.run(run_http(app, path="/documentation", headers=headers))
    assert status == 200
    html = body.decode("utf-8")
    assert "/static/swagger.js" in html
    assert "/static/swagger.css" in html
    assert 'url:"/schema"' in html

    status, body = asyncio.run(run_http(app, path="/readme", headers=headers))
    assert status == 200
    assert "/static/redoc.js" in body.decode("utf-8")


def test_default_error_schemas_and_component_reuse_for_dataclass():
    from dataclasses import dataclass

    @dataclass
    class Meta:
        env: str
        version: int

    app = Turbo()

    @app.post("/meta")
    async def create_meta(meta: Meta):
        return {"ok": True}

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    doc = json.loads(body.decode("utf-8"))
    op = doc["paths"]["/meta"]["post"]
    assert "422" in op["responses"]
    assert "500" in op["responses"]
    assert "HTTPValidationError" in doc["components"]["schemas"]
    assert "ErrorResponse" in doc["components"]["schemas"]
    assert "Meta" in doc["components"]["schemas"]
    rb_schema = op["requestBody"]["content"]["application/json"]["schema"]
    assert rb_schema["$ref"] == "#/components/schemas/Meta"
