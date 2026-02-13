import asyncio
import json

from turbo import Body, Model, Turbo


async def run_http(app, method="GET", path="/", headers=None, body_chunks=None):
    sent = []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }
    chunks = list(body_chunks or [b""])
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
    status = next(x for x in sent if x["type"] == "http.response.start")["status"]
    payload = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    return status, payload


def test_multiple_body_params_auto():
    app = Turbo()

    class User(Model):
        name: str
        age: int

    class Meta(Model):
        env: str

    @app.post("/multi")
    async def multi(user: User, meta: Meta):
        return {"user": user, "meta": meta}

    body = b'{"user":{"name":"paresh","age":30},"meta":{"env":"prod"}}'
    status, payload = asyncio.run(run_http(app, method="POST", path="/multi", headers=[(b"content-type", b"application/json")], body_chunks=[body]))
    assert status == 200
    data = json.loads(payload.decode("utf-8"))
    assert data["user"]["name"] == "paresh"
    assert data["meta"]["env"] == "prod"


def test_non_json_body_negotiation_plain_and_binary():
    app = Turbo()

    @app.post("/plain")
    async def plain(note: str = Body(media_type="text/plain")):
        return {"note": note}

    @app.post("/bin")
    async def binary(data: bytes = Body(media_type="application/octet-stream")):
        return {"size": len(data)}

    status, payload = asyncio.run(run_http(app, method="POST", path="/plain", headers=[(b"content-type", b"text/plain")], body_chunks=[b"hello"]))
    assert status == 200
    assert json.loads(payload.decode("utf-8"))["note"] == "hello"

    status, payload = asyncio.run(run_http(app, method="POST", path="/bin", headers=[(b"content-type", b"application/octet-stream")], body_chunks=[b"\x01\x02\x03"]))
    assert status == 200
    assert json.loads(payload.decode("utf-8"))["size"] == 3


def test_urlencoded_negotiation_for_model_body():
    app = Turbo()

    class Login(Model):
        username: str
        password: str

    @app.post("/login")
    async def login(payload: Login):
        return {"u": payload["username"]}

    status, payload = asyncio.run(
        run_http(
            app,
            method="POST",
            path="/login",
            headers=[(b"content-type", b"application/x-www-form-urlencoded")],
            body_chunks=[b"username=alice&password=secret"],
        )
    )
    assert status == 200
    assert json.loads(payload.decode("utf-8"))["u"] == "alice"


def test_streaming_multipart_chunked_upload():
    app = Turbo(multipart_spool_threshold=4)

    @app.post("/upload")
    async def upload(name: str = Body(alias="name", embed=True, media_type="multipart/form-data"), file_data: object = Body(alias="avatar", embed=True, media_type="multipart/form-data")):
        # Body(...) plus multipart negotiation returns parsed form fields/files.
        # file_data is UploadFile at runtime; keep object annotation here for test simplicity.
        data = await file_data.read()
        return {"name": name, "size": len(data), "rolled": file_data.spooled_to_disk}

    boundary = "streamBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="name"\r\n\r\n'
        "paresh\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="avatar"; filename="a.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "abcdefghij\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    chunks = [body[:15], body[15:33], body[33:58], body[58:79], body[79:]]
    status, payload = asyncio.run(
        run_http(
            app,
            method="POST",
            path="/upload",
            headers=[(b"content-type", f"multipart/form-data; boundary={boundary}".encode("utf-8"))],
            body_chunks=chunks,
        )
    )
    assert status == 200
    data = json.loads(payload.decode("utf-8"))
    assert data["name"] == "paresh"
    assert data["size"] == 10
    assert data["rolled"] is True


def test_malformed_json_returns_422():
    app = Turbo()

    @app.post("/broken-json")
    async def broken(payload: dict = Body()):
        return payload

    status, payload = asyncio.run(
        run_http(
            app,
            method="POST",
            path="/broken-json",
            headers=[(b"content-type", b"application/json")],
            body_chunks=[b'{"x":'],
        )
    )
    assert status == 422
    body = json.loads(payload.decode("utf-8"))
    assert body["error"] == "Malformed JSON body"


def test_query_field_flood_rejected():
    app = Turbo()
    qs = "&".join(f"k{i}=v" for i in range(2500)).encode("utf-8")
    sent = []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/q",
        "query_string": qs,
        "headers": [],
    }
    events = [{"type": "http.request", "body": b"", "more_body": False}]

    @app.get("/q")
    async def q(name: str):
        return {"name": name}

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    asyncio.run(app(scope, receive, send))
    status = next(x for x in sent if x["type"] == "http.response.start")["status"]
    body = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    payload = json.loads(body.decode("utf-8"))
    assert status == 413
    assert payload["error"] == "Too many query parameters"
