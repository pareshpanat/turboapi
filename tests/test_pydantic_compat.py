import asyncio
import json

import pytest

pytest.importorskip("pydantic")
from pydantic import BaseModel  # noqa: E402

from turbo import Turbo


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
    payload = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    return status, json.loads(payload.decode("utf-8"))


class UserIn(BaseModel):
    name: str
    age: int


class UserOut(BaseModel):
    id: int
    name: str


def test_pydantic_request_body_validation():
    app = Turbo()

    @app.post("/users")
    async def create_user(user: UserIn):
        return {"name": user.name, "age": user.age}

    status, body = asyncio.run(
        run_http(
            app,
            method="POST",
            path="/users",
            headers=[(b"content-type", b"application/json")],
            body=b'{"name":"alice","age":"21"}',
        )
    )
    assert status == 200
    assert body == {"name": "alice", "age": 21}

    status, body = asyncio.run(
        run_http(
            app,
            method="POST",
            path="/users",
            headers=[(b"content-type", b"application/json")],
            body=b'{"name":"alice","age":"bad"}',
        )
    )
    assert status == 422
    assert body["error"] == "Validation Error"


def test_pydantic_response_model_validation_and_dump():
    app = Turbo()

    @app.get("/users/1", response_model=UserOut)
    async def read_user():
        return {"id": "1", "name": "alice"}

    status, body = asyncio.run(run_http(app, path="/users/1"))
    assert status == 200
    assert body == {"id": 1, "name": "alice"}


def test_pydantic_openapi_schema_component():
    app = Turbo()

    @app.post("/users", response_model=UserOut)
    async def create_user(user: UserIn):
        return {"id": 1, "name": user.name}

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    op = body["paths"]["/users"]["post"]
    req_schema = op["requestBody"]["content"]["application/json"]["schema"]
    res_schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert req_schema["$ref"].endswith("/UserIn")
    assert res_schema["$ref"].endswith("/UserOut")
    assert "UserIn" in body["components"]["schemas"]
    assert "UserOut" in body["components"]["schemas"]
