import asyncio
import json
import os
import shutil
import zlib
from typing import Annotated, Literal, Union

from turbo import (
    Body,
    CompressionMiddleware,
    Query,
    RateLimitMiddleware,
    ResponseCacheMiddleware,
    Turbo,
    field,
    type_validator,
)
from turbo.errors import HTTPError
from turbo.models import Model, compile_model_validator


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


def test_validation_annotated_constraints_union_and_custom_type_validators():
    class A(Model):
        kind: Literal["a"]
        value: int = field(gt=0)

    class B(Model):
        kind: Literal["b"]
        value: int = field(lt=0)

    class Slug(str):
        pass

    @type_validator(Slug, mode="after")
    def _slug(v):
        s = str(v)
        if "-" not in s:
            raise ValueError("slug must contain '-'")
        return Slug(s)

    class Payload(Model):
        tags: Annotated[list[int], field(min_items=2, max_items=3)]
        modulo: int = field(multiple_of=3)
        item: Annotated[Union[A, B], field(discriminator="kind")]
        slug: Slug

    v = compile_model_validator(Payload)
    out = v({"tags": [1, 2], "modulo": 6, "item": {"kind": "a", "value": 2}, "slug": "hello-world"})
    assert out["tags"] == [1, 2]
    assert out["slug"] == "hello-world"

    try:
        v({"tags": [1], "modulo": 5, "item": {"kind": "z", "value": 2}, "slug": "bad"})
        assert False, "expected HTTPError"
    except HTTPError as exc:
        errs = exc.detail["errors"]
        types = {e["type"] for e in errs}
        assert "value_error.list.min_items" in types or "value_error.number.multiple_of" in types or "value_error.discriminator.invalid" in types

    class U(Model):
        x: Union[int, float]

    uv = compile_model_validator(U)
    try:
        uv({"x": "nope"})
        assert False, "expected union error"
    except HTTPError as exc:
        ctx = exc.detail["errors"][0].get("ctx", {})
        assert "variants" in ctx
        assert isinstance(ctx["variants"], list)


def test_openapi_parity_schema_overrides_parameter_components_and_docs_self_host():
    app = Turbo()
    app.set_openapi_reuse_parameters(True)
    app.add_openapi_server("https://api.example.com", description="prod")
    app.add_openapi_security_requirement({"BearerAuth": []})

    @app.post("/items")
    async def items(
        q: int = Query(description="q desc", example=1, deprecated=True, schema={"type": "integer", "minimum": 10}),
        body: dict = Body(description="body desc", example={"x": 1}, schema={"type": "object", "properties": {"x": {"type": "integer"}}}),
    ):
        return {"q": q, "body": body}

    @app.post(
        "/examples",
        examples={
            "request": {"application/json": {"ok": {"value": {"x": 1}}}},
            "responses": {"200": {"application/json": {"ok": {"value": {"ok": True}}}}},
        },
    )
    async def examples(body: dict = Body()):
        return {"ok": True}

    assets_dir = os.path.join("tests", "_docs_assets")
    os.makedirs(assets_dir, exist_ok=True)
    with open(os.path.join(assets_dir, "swagger-ui-bundle.js"), "w", encoding="utf-8") as f:
        f.write("console.log('ok')")
    with open(os.path.join(assets_dir, "swagger-ui.css"), "w", encoding="utf-8") as f:
        f.write("body{}")
    with open(os.path.join(assets_dir, "redoc.standalone.js"), "w", encoding="utf-8") as f:
        f.write("console.log('ok')")
    try:
        app.enable_docs_self_host(assets_dir, prefix="/_assets")
        start, body = asyncio.run(run_http(app, path="/openapi.json"))
        assert start["status"] == 200
        doc = json.loads(body.decode("utf-8"))
        assert doc["servers"][0]["url"] == "https://api.example.com"
        assert doc["security"] == [{"BearerAuth": []}]
        op = doc["paths"]["/items"]["post"]
        p_ref = op["parameters"][0]["$ref"]
        comp_key = p_ref.rsplit("/", 1)[-1]
        p_obj = doc["components"]["parameters"][comp_key]
        assert p_obj["description"] == "q desc"
        assert p_obj["deprecated"] is True
        assert p_obj["schema"]["minimum"] == 10
        rb = op["requestBody"]
        assert rb["description"] == "body desc"
        assert rb["content"]["application/json"]["example"]["x"] == 1

        ex_op = doc["paths"]["/examples"]["post"]
        assert "examples" in ex_op["requestBody"]["content"]["application/json"]

        start, docs_html = asyncio.run(run_http(app, path="/docs"))
        assert start["status"] == 200
        assert b"/_assets/swagger-ui-bundle.js" in docs_html
        start, static_js = asyncio.run(run_http(app, path="/_assets/swagger-ui-bundle.js"))
        assert start["status"] == 200
        assert b"console.log('ok')" in static_js
    finally:
        shutil.rmtree(assets_dir, ignore_errors=True)


def test_middleware_parity_rate_limit_response_cache_and_compression():
    app = Turbo()
    app.use_asgi(RateLimitMiddleware(max_requests=2, window_seconds=30))
    counter = {"n": 0}

    @app.get("/limited")
    async def limited():
        counter["n"] += 1
        return {"n": counter["n"]}

    s1, b1 = asyncio.run(run_http(app, path="/limited"))
    s2, b2 = asyncio.run(run_http(app, path="/limited"))
    s3, b3 = asyncio.run(run_http(app, path="/limited"))
    assert s1["status"] == 200 and s2["status"] == 200 and s3["status"] == 429
    assert json.loads(b3.decode("utf-8"))["error"] == "Too Many Requests"

    app2 = Turbo()
    app2.use_asgi(ResponseCacheMiddleware(ttl_seconds=30))
    calls = {"n": 0}

    @app2.get("/cached")
    async def cached():
        calls["n"] += 1
        return {"value": calls["n"]}

    s1, b1 = asyncio.run(run_http(app2, path="/cached"))
    s2, b2 = asyncio.run(run_http(app2, path="/cached"))
    assert s1["status"] == 200 and s2["status"] == 200
    assert json.loads(b1.decode("utf-8"))["value"] == 1
    assert json.loads(b2.decode("utf-8"))["value"] == 1

    app3 = Turbo()
    app3.use_asgi(CompressionMiddleware(minimum_size=1, prefer=["deflate"]))

    @app3.get("/big")
    async def big():
        return {"text": "x" * 256}

    s, b = asyncio.run(run_http(app3, path="/big", headers=[(b"accept-encoding", b"deflate")]))
    headers = dict(s["headers"])
    assert headers.get(b"content-encoding") == b"deflate"
    plain = zlib.decompress(b)
    assert b'"text":"' in plain
