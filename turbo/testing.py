from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlencode


@dataclass(slots=True)
class TestResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "ignore")

    def json(self):
        if not self.content:
            return None
        return json.loads(self.content.decode("utf-8"))


class TestClient:
    __test__ = False

    def __init__(self, app):
        self.app = app
        self._cookies: dict[str, str] = {}
        self._entered = False

    def __enter__(self):
        asyncio.run(self._lifespan_startup())
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        asyncio.run(self._lifespan_shutdown())
        self._entered = False

    async def _lifespan_startup(self):
        sent = []
        recv_q = [{"type": "lifespan.startup"}]

        async def receive():
            if recv_q:
                return recv_q.pop(0)
            await asyncio.sleep(3600)
            return {"type": "lifespan.shutdown"}

        async def send(msg):
            sent.append(msg)

        task = asyncio.create_task(self.app({"type": "lifespan"}, receive, send))
        while True:
            if any(m.get("type") == "lifespan.startup.complete" for m in sent):
                break
            if any(m.get("type") == "lifespan.startup.failed" for m in sent):
                raise RuntimeError("lifespan startup failed")
            if task.done():
                break
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except BaseException:
            pass

    async def _lifespan_shutdown(self):
        sent = []
        recv_q = [{"type": "lifespan.shutdown"}]

        async def receive():
            if recv_q:
                return recv_q.pop(0)
            return {"type": "lifespan.shutdown"}

        async def send(msg):
            sent.append(msg)

        await self.app({"type": "lifespan"}, receive, send)
        while True:
            if any(m.get("type") == "lifespan.shutdown.complete" for m in sent):
                break
            if any(m.get("type") == "lifespan.shutdown.failed" for m in sent):
                raise RuntimeError("lifespan shutdown failed")
            await asyncio.sleep(0)

    def request(self, method: str, path: str, *, headers: dict[str, str] | None = None, params: dict[str, str] | None = None, json_body=None, data=None, content: bytes | str | None = None) -> TestResponse:
        return asyncio.run(self._request(method, path, headers=headers, params=params, json_body=json_body, data=data, content=content))

    async def _request(self, method: str, path: str, *, headers: dict[str, str] | None = None, params: dict[str, str] | None = None, json_body=None, data=None, content: bytes | str | None = None) -> TestResponse:
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        if params:
            qs = urlencode(params, doseq=True).encode("utf-8")
        else:
            qs = b""
        if json_body is not None:
            body = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("content-type", "application/json")
        elif data is not None:
            if isinstance(data, dict):
                body = urlencode(data, doseq=True).encode("utf-8")
                hdrs.setdefault("content-type", "application/x-www-form-urlencoded")
            elif isinstance(data, bytes):
                body = data
            else:
                body = str(data).encode("utf-8")
        elif content is not None:
            body = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        else:
            body = b""

        if self._cookies:
            cookie = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            hdrs.setdefault("cookie", cookie)

        scope_headers = [(k.encode("latin1"), v.encode("latin1")) for k, v in hdrs.items()]
        sent = []
        scope = {
            "type": "http",
            "method": method.upper(),
            "path": path,
            "query_string": qs,
            "headers": scope_headers,
        }
        events = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive():
            if events:
                return events.pop(0)
            return {"type": "http.disconnect"}

        async def send(msg):
            sent.append(msg)

        await self.app(scope, receive, send)
        start = next(x for x in sent if x["type"] == "http.response.start")
        body_bytes = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
        out_headers = {}
        for k, v in start.get("headers", []):
            key = k.decode("latin1").lower()
            out_headers[key] = v.decode("latin1")
            if key == "set-cookie":
                pair = out_headers[key].split(";", 1)[0]
                if "=" in pair:
                    ck, cv = pair.split("=", 1)
                    if cv:
                        self._cookies[ck] = cv
                    else:
                        self._cookies.pop(ck, None)
        return TestResponse(status_code=int(start["status"]), headers=out_headers, content=body_bytes)

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs):
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("DELETE", path, **kwargs)

    def options(self, path: str, **kwargs):
        return self.request("OPTIONS", path, **kwargs)

    def head(self, path: str, **kwargs):
        return self.request("HEAD", path, **kwargs)

    @contextmanager
    def dependency_override(self, original, override):
        with self.app.override_dependency(original, override):
            yield
