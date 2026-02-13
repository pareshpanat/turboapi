import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TypedDict
from uuid import UUID
from turbo import Turbo, Query, Header, Cookie, Form, File, UploadFile

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
    status = next(x for x in sent if x["type"] == "http.response.start")["status"]
    payload = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    return status, payload

def test_query_header_cookie_and_openapi_sources():
    app = Turbo()

    @app.get("/bind")
    async def bind(tags: list[str] = Query(), trace_id: str = Header(alias="x-trace-id"), sid: str = Cookie(alias="sid")):
        return {"tags": tags, "trace_id": trace_id, "sid": sid}

    headers = [(b"x-trace-id", b"t1"), (b"cookie", b"sid=abc")]
    status, body = asyncio.run(run_http(app, path="/bind", query="tags=a&tags=b", headers=headers))
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["tags"] == ["a", "b"]
    assert data["trace_id"] == "t1"
    assert data["sid"] == "abc"

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    op = json.loads(body.decode("utf-8"))["paths"]["/bind"]["get"]
    pins = {(p["name"], p["in"]) for p in op["parameters"]}
    assert ("tags", "query") in pins
    assert ("x-trace-id", "header") in pins
    assert ("sid", "cookie") in pins

    status, body = asyncio.run(run_http(app, path="/bind", query="tags=a", headers=[(b"cookie", b"sid=abc")]))
    assert status == 422
    err = json.loads(body.decode("utf-8"))
    assert err["detail"]["errors"][0]["loc"] == ["header", "x-trace-id"]

def test_form_and_file_binding_and_openapi():
    app = Turbo()

    @app.post("/upload")
    async def upload(name: str = Form(), avatar: UploadFile = File()):
        data = await avatar.read()
        return {"name": name, "filename": avatar.filename, "size": len(data)}

    boundary = "turboBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="name"\r\n\r\n'
        "paresh\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="avatar"; filename="a.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "hello\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    headers = [(b"content-type", f"multipart/form-data; boundary={boundary}".encode("utf-8"))]
    status, payload = asyncio.run(run_http(app, method="POST", path="/upload", headers=headers, body=body))
    assert status == 200
    data = json.loads(payload.decode("utf-8"))
    assert data["name"] == "paresh"
    assert data["filename"] == "a.txt"
    assert data["size"] == 5

    status, payload = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    op = json.loads(payload.decode("utf-8"))["paths"]["/upload"]["post"]
    assert "multipart/form-data" in op["requestBody"]["content"]

def test_multipart_limits_and_spooling():
    app = Turbo(multipart_spool_threshold=1, multipart_max_file_size=4)

    @app.post("/upload-check")
    async def upload_check(avatar: UploadFile = File()):
        return {"rolled": avatar.spooled_to_disk, "size": avatar.size}

    boundary = "limBoundary"
    body_ok = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="avatar"; filename="a.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "abc\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    headers = [(b"content-type", f"multipart/form-data; boundary={boundary}".encode("utf-8"))]
    status, payload = asyncio.run(run_http(app, method="POST", path="/upload-check", headers=headers, body=body_ok))
    assert status == 200
    data = json.loads(payload.decode("utf-8"))
    assert data["rolled"] is True
    assert data["size"] == 3

    body_big = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="avatar"; filename="b.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "abcde\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    status, payload = asyncio.run(run_http(app, method="POST", path="/upload-check", headers=headers, body=body_big))
    assert status == 413

def test_serialization_encoders():
    class Role(Enum):
        ADMIN = "admin"

    @dataclass
    class User:
        id: int
        created_at: datetime

    class Money:
        def __init__(self, amount):
            self.amount = amount

    app = Turbo()
    app.json_encoder(Money, lambda m: {"amount": str(m.amount)})

    @app.get("/serialize")
    async def serialize():
        return {
            "when": datetime(2026, 1, 1, 12, 0, 0),
            "uid": UUID("2dc3f898-7f9e-4018-b94f-fdb65586df3f"),
            "price": Decimal("12.50"),
            "role": Role.ADMIN,
            "user": User(1, datetime(2026, 1, 1, 0, 0, 0)),
            "money": Money(10),
        }

    status, payload = asyncio.run(run_http(app, path="/serialize"))
    assert status == 200
    data = json.loads(payload.decode("utf-8"))
    assert data["price"] == "12.50"
    assert data["role"] == "admin"
    assert data["money"]["amount"] == "10"

def test_dataclass_and_typeddict_body_binding():
    @dataclass
    class Item:
        id: int
        name: str

    class Meta(TypedDict):
        env: str
        version: int

    app = Turbo()

    @app.post("/dc")
    async def dc(item: Item):
        return {"id": item.id, "name": item.name}

    @app.post("/td")
    async def td(meta: Meta):
        return {"env": meta["env"], "version": meta["version"]}

    status, payload = asyncio.run(run_http(app, method="POST", path="/dc", headers=[(b"content-type", b"application/json")], body=b'{"id":1,"name":"x"}'))
    assert status == 200
    assert json.loads(payload.decode("utf-8"))["id"] == 1

    status, payload = asyncio.run(run_http(app, method="POST", path="/td", headers=[(b"content-type", b"application/json")], body=b'{"env":"prod","version":2}'))
    assert status == 200
    assert json.loads(payload.decode("utf-8"))["version"] == 2
