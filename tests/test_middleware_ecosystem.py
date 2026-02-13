import asyncio
import gzip
import json

from turbo import (
    CORSMiddleware,
    CSRFMiddleware,
    GZipMiddleware,
    HTTPSRedirectMiddleware,
    MemorySessionBackend,
    ProxyHeadersMiddleware,
    SessionMiddleware,
    TrustedHostMiddleware,
    Turbo,
    Request,
)


async def run_http_events(app, method="GET", path="/", headers=None, body=b""):
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
    return sent


def _body_from_events(events):
    return b"".join(x.get("body", b"") for x in events if x["type"] == "http.response.body")


def _header_map(start_event):
    return {k.lower(): v for k, v in start_event.get("headers", [])}

def _header_values(start_event, key: bytes):
    target = key.lower()
    return [v for k, v in start_event.get("headers", []) if k.lower() == target]


def test_cors_preflight_and_simple_response():
    app = Turbo()
    app.use_asgi(CORSMiddleware(allow_origins=["https://client.example"], allow_methods=["GET", "POST"], allow_headers=["x-token"]))

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    preflight = asyncio.run(
        run_http_events(
            app,
            method="OPTIONS",
            path="/ping",
            headers=[
                (b"origin", b"https://client.example"),
                (b"access-control-request-method", b"POST"),
                (b"access-control-request-headers", b"x-token"),
            ],
        )
    )
    start = next(x for x in preflight if x["type"] == "http.response.start")
    h = _header_map(start)
    assert start["status"] == 200
    assert h[b"access-control-allow-origin"] == b"https://client.example"
    assert h[b"access-control-allow-methods"] == b"GET,POST"
    assert h[b"access-control-allow-headers"] == b"x-token"

    normal = asyncio.run(
        run_http_events(app, path="/ping", headers=[(b"origin", b"https://client.example")])
    )
    start = next(x for x in normal if x["type"] == "http.response.start")
    h = _header_map(start)
    assert h[b"access-control-allow-origin"] == b"https://client.example"

def test_cors_regex_and_wildcards():
    app = Turbo()
    app.use_asgi(
        CORSMiddleware(
            allow_origins=[],
            allow_origin_regex=r"https://.*\.example\.com",
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=True,
        )
    )

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    preflight = asyncio.run(
        run_http_events(
            app,
            method="OPTIONS",
            path="/ping",
            headers=[
                (b"origin", b"https://app.example.com"),
                (b"access-control-request-method", b"PATCH"),
                (b"access-control-request-headers", b"x-token,x-org"),
            ],
        )
    )
    start = next(x for x in preflight if x["type"] == "http.response.start")
    h = _header_map(start)
    assert h[b"access-control-allow-origin"] == b"https://app.example.com"
    assert h[b"access-control-allow-headers"] == b"x-token,x-org"
    assert b"PATCH" in h[b"access-control-allow-methods"]
    assert h[b"access-control-allow-credentials"] == b"true"


def test_gzip_middleware_compresses_response():
    app = Turbo()
    app.use_asgi(GZipMiddleware(minimum_size=20))

    @app.get("/data")
    async def data():
        return {"v": "x" * 200}

    events = asyncio.run(run_http_events(app, path="/data", headers=[(b"accept-encoding", b"gzip")]))
    start = next(x for x in events if x["type"] == "http.response.start")
    h = _header_map(start)
    body = _body_from_events(events)
    assert h[b"content-encoding"] == b"gzip"
    assert h[b"vary"] == b"Accept-Encoding"
    decoded = gzip.decompress(body)
    payload = json.loads(decoded.decode("utf-8"))
    assert payload["v"] == "x" * 200


def test_trusted_host_middleware_http_and_websocket():
    app = Turbo()
    app.use_asgi(TrustedHostMiddleware(["example.com", "*.example.com"]))

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    http_ok = asyncio.run(run_http_events(app, path="/ping", headers=[(b"host", b"api.example.com")]))
    start = next(x for x in http_ok if x["type"] == "http.response.start")
    assert start["status"] == 200

    http_bad = asyncio.run(run_http_events(app, path="/ping", headers=[(b"host", b"evil.com")]))
    start = next(x for x in http_bad if x["type"] == "http.response.start")
    assert start["status"] == 400

    sent = []
    ws_events = [{"type": "websocket.connect"}]

    async def receive():
        if ws_events:
            return ws_events.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "websocket", "path": "/ws", "query_string": b"", "headers": [(b"host", b"evil.com")]}
    asyncio.run(app(scope, receive, send))
    assert any(m.get("type") == "websocket.close" and m.get("code") == 1008 for m in sent)


def test_session_middleware_round_trip():
    app = Turbo()
    app.use_asgi(SessionMiddleware(secret_key="dev-secret", cookie_name="sid"))

    @app.get("/login")
    async def login(req: Request):
        req.set_session_value("user", "alice")
        return {"ok": True}

    @app.get("/me")
    async def me(req: Request):
        return {"user": req.session.get("user")}

    @app.get("/logout")
    async def logout(req: Request):
        req.clear_session()
        return {"ok": True}

    login_events = asyncio.run(run_http_events(app, path="/login"))
    login_start = next(x for x in login_events if x["type"] == "http.response.start")
    login_headers = dict(login_start["headers"])
    cookie_header = login_headers.get(b"set-cookie")
    assert cookie_header is not None
    cookie_value = cookie_header.decode("latin1").split(";", 1)[0]

    me_events = asyncio.run(run_http_events(app, path="/me", headers=[(b"cookie", cookie_value.encode("latin1"))]))
    me_payload = json.loads(_body_from_events(me_events).decode("utf-8"))
    assert me_payload["user"] == "alice"

    logout_events = asyncio.run(run_http_events(app, path="/logout", headers=[(b"cookie", cookie_value.encode("latin1"))]))
    logout_start = next(x for x in logout_events if x["type"] == "http.response.start")
    logout_headers = dict(logout_start["headers"])
    assert b"Max-Age=0" in logout_headers[b"set-cookie"]

def test_https_redirect_middleware():
    app = Turbo()
    app.use_asgi(HTTPSRedirectMiddleware())

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    events = asyncio.run(run_http_events(app, path="/ping", headers=[(b"host", b"example.com")]))
    start = next(x for x in events if x["type"] == "http.response.start")
    h = _header_map(start)
    assert start["status"] == 307
    assert h[b"location"] == b"https://example.com/ping"

def test_proxy_headers_middleware_applies_for_trusted_client():
    app = Turbo()
    app.use_asgi(ProxyHeadersMiddleware())

    @app.get("/who")
    async def who(req: Request):
        return {
            "scheme": req.scope.get("scheme"),
            "host": req.headers.get("host"),
            "client": (req.scope.get("client") or ["", 0])[0],
        }

    sent = []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/who",
        "query_string": b"",
        "headers": [
            (b"host", b"internal.local"),
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", b"api.example.com"),
            (b"x-forwarded-for", b"203.0.113.9, 127.0.0.1"),
        ],
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
    }
    events = [{"type": "http.request", "body": b"", "more_body": False}]
    async def receive():
        if events:
            return events.pop(0)
        return {"type": "http.disconnect"}
    async def send(msg):
        sent.append(msg)
    asyncio.run(app(scope, receive, send))
    body = json.loads(_body_from_events(sent).decode("utf-8"))
    assert body["scheme"] == "https"
    assert body["host"] == "api.example.com"
    assert body["client"] == "203.0.113.9"

def test_session_backend_and_signing_hardening():
    backend = MemorySessionBackend()
    app = Turbo()
    app.use_asgi(
        SessionMiddleware(
            secret_key="k-new",
            secret_key_fallbacks=["k-old"],
            cookie_name="sid",
            backend=backend,
            same_site="None",
            https_only=True,
            partitioned=True,
        )
    )

    @app.get("/set")
    async def set_session(req: Request):
        req.set_session_value("role", "admin")
        return {"ok": True}

    @app.get("/get")
    async def get_session(req: Request):
        return {"role": req.session.get("role")}

    set_events = asyncio.run(run_http_events(app, path="/set"))
    set_start = next(x for x in set_events if x["type"] == "http.response.start")
    set_headers = dict(set_start["headers"])
    cookie = set_headers[b"set-cookie"].decode("latin1")
    assert "Secure" in cookie
    assert "Partitioned" in cookie
    cookie_pair = cookie.split(";", 1)[0]

    get_events = asyncio.run(run_http_events(app, path="/get", headers=[(b"cookie", cookie_pair.encode("latin1"))]))
    payload = json.loads(_body_from_events(get_events).decode("utf-8"))
    assert payload["role"] == "admin"

def test_csrf_middleware_with_session():
    app = Turbo()
    app.use_asgi(SessionMiddleware(secret_key="dev-secret", cookie_name="sid"))
    app.use_asgi(CSRFMiddleware(cookie_name="csrftoken"))

    @app.get("/csrf")
    async def csrf(req: Request):
        return {"csrf": req.csrf_token}

    @app.post("/transfer")
    async def transfer():
        return {"ok": True}

    boot = asyncio.run(run_http_events(app, path="/csrf"))
    boot_start = next(x for x in boot if x["type"] == "http.response.start")
    cookies = [v.decode("latin1") for v in _header_values(boot_start, b"set-cookie")]
    cookie_pairs = [c.split(";", 1)[0] for c in cookies]
    cookie_header = "; ".join(cookie_pairs)
    csrf_pair = next(x for x in cookie_pairs if x.startswith("csrftoken="))
    csrf_token = csrf_pair.split("=", 1)[1]
    payload = json.loads(_body_from_events(boot).decode("utf-8"))
    assert payload["csrf"] == csrf_token

    denied = asyncio.run(run_http_events(app, method="POST", path="/transfer", headers=[(b"cookie", cookie_header.encode("latin1"))]))
    denied_start = next(x for x in denied if x["type"] == "http.response.start")
    denied_body = json.loads(_body_from_events(denied).decode("utf-8"))
    assert denied_start["status"] == 403
    assert denied_body["error"] == "CSRF token missing"

    denied2 = asyncio.run(
        run_http_events(
            app,
            method="POST",
            path="/transfer",
            headers=[(b"cookie", cookie_header.encode("latin1")), (b"x-csrf-token", b"wrong")],
        )
    )
    denied2_start = next(x for x in denied2 if x["type"] == "http.response.start")
    denied2_body = json.loads(_body_from_events(denied2).decode("utf-8"))
    assert denied2_start["status"] == 403
    assert denied2_body["error"] == "CSRF token invalid"

    ok = asyncio.run(
        run_http_events(
            app,
            method="POST",
            path="/transfer",
            headers=[(b"cookie", cookie_header.encode("latin1")), (b"x-csrf-token", csrf_token.encode("latin1"))],
        )
    )
    ok_start = next(x for x in ok if x["type"] == "http.response.start")
    ok_body = json.loads(_body_from_events(ok).decode("utf-8"))
    assert ok_start["status"] == 200
    assert ok_body["ok"] is True


def test_middleware_ordering_and_error_propagation():
    app = Turbo()
    calls = []

    @app.exception_handler(ValueError)
    async def handle_value_error(req, exc):
        return {"error": str(exc), "where": "exception_handler"}

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise ValueError("boom")

    def mw1(next_app):
        async def wrapped(scope, receive, send):
            calls.append("mw1:before")
            await next_app(scope, receive, send)
            calls.append("mw1:after")

        return wrapped

    def mw2(next_app):
        async def wrapped(scope, receive, send):
            calls.append("mw2:before")
            await next_app(scope, receive, send)
            calls.append("mw2:after")

        return wrapped

    async def bad_http_middleware(req, call_next):
        if req.path == "/boom":
            raise ValueError("middleware-broke")
        return await call_next(req)

    app.use_asgi(mw1)
    app.use_asgi(mw2)
    app.use(bad_http_middleware)

    ok_events = asyncio.run(run_http_events(app, path="/ok"))
    ok_start = next(x for x in ok_events if x["type"] == "http.response.start")
    assert ok_start["status"] == 200
    assert calls == ["mw1:before", "mw2:before", "mw2:after", "mw1:after"]

    boom_events = asyncio.run(run_http_events(app, path="/boom"))
    boom_start = next(x for x in boom_events if x["type"] == "http.response.start")
    boom_body = json.loads(_body_from_events(boom_events).decode("utf-8"))
    assert boom_start["status"] == 200
    assert boom_body["where"] == "exception_handler"
    assert boom_body["error"] == "middleware-broke"
