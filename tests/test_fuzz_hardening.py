import asyncio
import json
import random
import string

from turbo import Depends, Model, Turbo, jwt_auth


def _rand_token(rng: random.Random, size: int):
    alphabet = string.ascii_letters + string.digits + "-_./"
    return "".join(rng.choice(alphabet) for _ in range(size))


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
    return status, payload


def test_fuzz_invalid_jwt_inputs_fail_closed():
    app = Turbo()
    dep = jwt_auth("fuzz-secret")

    @app.get("/secure")
    async def secure(payload=Depends(dep)):
        return {"sub": payload.get("sub")}

    rng = random.Random(1337)
    for _ in range(200):
        size = rng.randint(1, 300)
        tok = _rand_token(rng, size)
        status, payload = asyncio.run(
            run_http(app, path="/secure", headers=[(b"authorization", f"Bearer {tok}".encode("utf-8"))])
        )
        assert status == 401, payload.decode("utf-8", "ignore")


def test_fuzz_malformed_json_inputs_are_422_not_500():
    app = Turbo()

    class Payload(Model):
        name: str

    @app.post("/json")
    async def endpoint(payload: Payload):
        return payload

    rng = random.Random(2025)
    for _ in range(120):
        size = rng.randint(1, 80)
        raw = bytes(rng.randint(0, 255) for _ in range(size))
        status, body = asyncio.run(
            run_http(
                app,
                method="POST",
                path="/json",
                headers=[(b"content-type", b"application/json")],
                body=raw,
            )
        )
        if status == 200:
            # Rare case where random payload is valid JSON + valid model.
            parsed = json.loads(body.decode("utf-8"))
            assert "name" in parsed
        else:
            assert status in (413, 422), body.decode("utf-8", "ignore")
