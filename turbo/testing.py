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
        self._override_stack: list[dict] = []

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

    @contextmanager
    def dependency_overrides(self, overrides: dict):
        with self.app.override_dependencies(overrides):
            yield

    @contextmanager
    def override_scope(self, name: str = "scope"):
        snapshot = dict(self.app.dependency_overrides)
        self._override_stack.append(snapshot)
        try:
            with self.app.override_scope(name):
                yield
        finally:
            prev = self._override_stack.pop() if self._override_stack else snapshot
            self.app.dependency_overrides = prev


class WebSocketTestSession:
    __test__ = False

    def __init__(self, app, scope: dict):
        self.app = app
        self.scope = scope
        self._to_app: asyncio.Queue[dict] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict] = asyncio.Queue()
        self._prefetched: list[dict] = []
        self._task: asyncio.Task | None = None
        self.accepted = False
        self.closed = False
        self.subprotocol: str | None = None

    async def _receive(self):
        return await self._to_app.get()

    async def _send(self, msg):
        await self._from_app.put(msg)

    async def connect(self, *, timeout: float = 1.0):
        await self._to_app.put({"type": "websocket.connect"})
        self._task = asyncio.create_task(self.app(self.scope, self._receive, self._send))
        while True:
            msg = await asyncio.wait_for(self._from_app.get(), timeout=timeout)
            typ = msg.get("type")
            if typ == "websocket.accept":
                self.accepted = True
                self.subprotocol = msg.get("subprotocol")
                return self
            if typ == "websocket.close":
                self.closed = True
                raise RuntimeError("WebSocket connection closed during handshake")
            self._prefetched.append(msg)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def send_text(self, text: str):
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def send_bytes(self, data: bytes):
        await self._to_app.put({"type": "websocket.receive", "bytes": bytes(data)})

    async def send_json(self, data):
        await self.send_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))

    async def receive(self):
        if self._prefetched:
            msg = self._prefetched.pop(0)
        else:
            msg = await self._from_app.get()
        if msg.get("type") == "websocket.close":
            self.closed = True
        return msg

    async def receive_text(self) -> str:
        while True:
            msg = await self.receive()
            typ = msg.get("type")
            if typ == "websocket.send":
                text = msg.get("text")
                if text is not None:
                    return str(text)
                data = msg.get("bytes", b"")
                return bytes(data).decode("utf-8", "ignore")
            if typ == "websocket.close":
                raise RuntimeError("WebSocket closed")

    async def receive_json(self):
        return json.loads(await self.receive_text())

    async def close(self, code: int = 1000):
        if self.closed:
            return
        await self._to_app.put({"type": "websocket.disconnect", "code": int(code)})
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=1.0)
            except TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except BaseException:
                    pass
        self.closed = True


class AsyncTestClient:
    __test__ = False

    def __init__(self, app):
        self.app = app
        self._cookies: dict[str, str] = {}
        self._entered = False
        self._override_stack: list[dict] = []

    async def __aenter__(self):
        await self._lifespan_startup()
        self._entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._lifespan_shutdown()
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

    def _apply_cookie_jar(self, headers: dict[str, str]):
        if self._cookies:
            cookie = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            headers.setdefault("cookie", cookie)

    def _update_cookie_jar(self, response_headers: list[tuple[bytes, bytes]]):
        for k, v in response_headers:
            key = k.decode("latin1").lower()
            if key != "set-cookie":
                continue
            raw = v.decode("latin1")
            pair = raw.split(";", 1)[0]
            if "=" not in pair:
                continue
            ck, cv = pair.split("=", 1)
            if cv:
                self._cookies[ck] = cv
            else:
                self._cookies.pop(ck, None)

    async def request(self, method: str, path: str, *, headers: dict[str, str] | None = None, params: dict[str, str] | None = None, json_body=None, data=None, content: bytes | str | None = None) -> TestResponse:
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

        self._apply_cookie_jar(hdrs)
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
        self._update_cookie_jar(start.get("headers", []))
        out_headers = {}
        for k, v in start.get("headers", []):
            out_headers[k.decode("latin1").lower()] = v.decode("latin1")
        return TestResponse(status_code=int(start["status"]), headers=out_headers, content=body_bytes)

    async def get(self, path: str, **kwargs):
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs):
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs):
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs):
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs):
        return await self.request("DELETE", path, **kwargs)

    async def options(self, path: str, **kwargs):
        return await self.request("OPTIONS", path, **kwargs)

    async def head(self, path: str, **kwargs):
        return await self.request("HEAD", path, **kwargs)

    async def websocket_connect(self, path: str, *, headers: dict[str, str] | None = None, params: dict[str, str] | None = None, subprotocols: list[str] | None = None):
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        self._apply_cookie_jar(hdrs)
        if subprotocols:
            hdrs["sec-websocket-protocol"] = ",".join(subprotocols)
        query_string = urlencode(params or {}, doseq=True).encode("utf-8")
        scope = {
            "type": "websocket",
            "path": path,
            "query_string": query_string,
            "headers": [(k.encode("latin1"), v.encode("latin1")) for k, v in hdrs.items()],
        }
        session = WebSocketTestSession(self.app, scope)
        await session.connect()
        return session

    @contextmanager
    def dependency_override(self, original, override):
        with self.app.override_dependency(original, override):
            yield

    @contextmanager
    def dependency_overrides(self, overrides: dict):
        with self.app.override_dependencies(overrides):
            yield

    @contextmanager
    def override_scope(self, name: str = "scope"):
        snapshot = dict(self.app.dependency_overrides)
        self._override_stack.append(snapshot)
        try:
            with self.app.override_scope(name):
                yield
        finally:
            prev = self._override_stack.pop() if self._override_stack else snapshot
            self.app.dependency_overrides = prev
