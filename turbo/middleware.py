from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import inspect
import ipaddress
import json
import re
import secrets
from typing import Any, Awaitable, Callable, Optional

HTTPMiddleware = Callable[..., Awaitable[Any]]
ASGIApp = Callable[[dict, Callable, Callable], Awaitable[Any]]


def _headers_to_map(scope_headers: list[tuple[bytes, bytes]] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, v in scope_headers or []:
        kk = k.decode("latin1").lower()
        out.setdefault(kk, []).append(v.decode("latin1"))
    return out


def _match_host(pattern: str, host: str) -> bool:
    p = pattern.lower()
    h = host.lower()
    if p == "*":
        return True
    if p.startswith("*."):
        return h.endswith(p[1:])
    return h == p


def _upsert_header(headers: list[tuple[bytes, bytes]], key: bytes, value: bytes):
    target = key.lower()
    headers[:] = [(k, v) for k, v in headers if k.lower() != target]
    headers.append((key, value))


def _append_vary(headers: list[tuple[bytes, bytes]], value: str):
    existing_values = []
    for k, v in headers:
        if k.lower() == b"vary":
            existing_values.extend([x.strip() for x in v.decode("latin1").split(",") if x.strip()])
    merged = {x.lower(): x for x in existing_values}
    if value.lower() not in merged:
        existing_values.append(value)
    if existing_values:
        _upsert_header(headers, b"vary", ", ".join(existing_values).encode("latin1"))


async def _call_maybe_await(fn, *args, **kwargs):
    out = fn(*args, **kwargs)
    if inspect.isawaitable(out):
        return await out
    return out


class HTTPSRedirectMiddleware:
    def __init__(self, *, redirect_status_code: int = 307):
        self.redirect_status_code = int(redirect_status_code)

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            scope_type = scope.get("type")
            if scope_type not in ("http", "websocket"):
                await app(scope, receive, send)
                return

            scheme = (scope.get("scheme") or ("ws" if scope_type == "websocket" else "http")).lower()
            secure = scheme in ("https", "wss")
            if secure:
                await app(scope, receive, send)
                return

            headers = _headers_to_map(scope.get("headers"))
            host = (headers.get("host") or [""])[-1]
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": "HTTPS required"})
                return
            path = scope.get("root_path", "") + scope.get("path", "")
            qs = scope.get("query_string", b"")
            location = f"https://{host}{path}"
            if qs:
                location += "?" + qs.decode("latin1")
            await send(
                {
                    "type": "http.response.start",
                    "status": self.redirect_status_code,
                    "headers": [(b"location", location.encode("latin1")), (b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b""})

        return wrapped


class ProxyHeadersMiddleware:
    def __init__(
        self,
        *,
        trusted_hosts: Optional[list[str]] = None,
        trusted_cidrs: Optional[list[str]] = None,
        forwarded_proto_header: str = "x-forwarded-proto",
        forwarded_for_header: str = "x-forwarded-for",
        forwarded_host_header: str = "x-forwarded-host",
    ):
        self.trusted_hosts = set((trusted_hosts or ["127.0.0.1", "::1"]))
        self.trusted_cidrs = [ipaddress.ip_network(c) for c in (trusted_cidrs or ["127.0.0.0/8", "::1/128"])]
        self.forwarded_proto_header = forwarded_proto_header.lower()
        self.forwarded_for_header = forwarded_for_header.lower()
        self.forwarded_host_header = forwarded_host_header.lower()

    def _is_trusted(self, client_ip: str) -> bool:
        if client_ip in self.trusted_hosts:
            return True
        try:
            ip = ipaddress.ip_address(client_ip)
        except ValueError:
            return False
        return any(ip in net for net in self.trusted_cidrs)

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            if scope.get("type") not in ("http", "websocket"):
                await app(scope, receive, send)
                return
            client = scope.get("client")
            client_ip = client[0] if isinstance(client, (tuple, list)) and client else ""
            if not client_ip or not self._is_trusted(client_ip):
                await app(scope, receive, send)
                return
            headers = _headers_to_map(scope.get("headers"))
            child_scope = dict(scope)
            child_headers = list(scope.get("headers", []))

            proto = (headers.get(self.forwarded_proto_header) or [""])[-1].split(",")[-1].strip().lower()
            if proto:
                child_scope["scheme"] = proto
            host = (headers.get(self.forwarded_host_header) or [""])[-1].split(",")[-1].strip()
            if host:
                _upsert_header(child_headers, b"host", host.encode("latin1"))
                child_scope["headers"] = child_headers
            xff = (headers.get(self.forwarded_for_header) or [""])[-1]
            if xff:
                first = xff.split(",")[0].strip()
                port = child_scope.get("client", ("", 0))[1] if child_scope.get("client") else 0
                child_scope["client"] = (first, port)
            await app(child_scope, receive, send)

        return wrapped


class CORSMiddleware:
    def __init__(
        self,
        allow_origins: Optional[list[str]] = None,
        allow_methods: Optional[list[str]] = None,
        allow_headers: Optional[list[str]] = None,
        expose_headers: Optional[list[str]] = None,
        allow_credentials: bool = False,
        max_age: int = 600,
        allow_origin_regex: Optional[str] = None,
    ):
        self.allow_origins = allow_origins or ["*"]
        methods = [m.upper() for m in (allow_methods or ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])]
        self.allow_methods_any = "*" in methods
        self.allow_methods = methods
        headers = [h.lower() for h in (allow_headers or ["*"])]
        self.allow_headers_any = "*" in headers
        self.allow_headers = headers
        self.expose_headers = expose_headers or []
        self.allow_credentials = allow_credentials
        self.max_age = int(max_age)
        self.allow_origin_regex = re.compile(allow_origin_regex) if allow_origin_regex else None

    def _origin_allowed(self, origin: str) -> bool:
        if "*" in self.allow_origins:
            return True
        if origin in self.allow_origins:
            return True
        if self.allow_origin_regex and self.allow_origin_regex.fullmatch(origin):
            return True
        return False

    def _method_allowed(self, method: str) -> bool:
        return self.allow_methods_any or method in self.allow_methods

    def _allow_origin_value(self, origin: str) -> str:
        if "*" in self.allow_origins and not self.allow_credentials and self.allow_origin_regex is None:
            return "*"
        return origin

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            if scope.get("type") != "http":
                await app(scope, receive, send)
                return

            headers = _headers_to_map(scope.get("headers"))
            origin = (headers.get("origin") or [""])[-1]
            method = scope.get("method", "").upper()
            req_method = (headers.get("access-control-request-method") or [""])[-1].upper()
            req_headers = (headers.get("access-control-request-headers") or [""])[-1]
            is_preflight = method == "OPTIONS" and bool(origin) and bool(req_method)

            def cors_headers() -> list[tuple[bytes, bytes]]:
                out: list[tuple[bytes, bytes]] = []
                if not origin or not self._origin_allowed(origin):
                    return out
                out.append((b"access-control-allow-origin", self._allow_origin_value(origin).encode("latin1")))
                if "*" not in self.allow_origins or self.allow_credentials or self.allow_origin_regex is not None:
                    out.append((b"vary", b"Origin"))
                if self.allow_credentials:
                    out.append((b"access-control-allow-credentials", b"true"))
                if self.expose_headers:
                    out.append((b"access-control-expose-headers", ",".join(self.expose_headers).encode("latin1")))
                return out

            if is_preflight and self._origin_allowed(origin) and self._method_allowed(req_method):
                if self.allow_headers_any:
                    allowed_headers = req_headers or "*"
                else:
                    allowed_headers = ",".join(self.allow_headers)
                methods = ",".join(self.allow_methods if not self.allow_methods_any else ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
                h = cors_headers()
                h.extend(
                    [
                        (b"access-control-allow-methods", methods.encode("latin1")),
                        (b"access-control-allow-headers", allowed_headers.encode("latin1")),
                        (b"access-control-max-age", str(self.max_age).encode("ascii")),
                        (b"content-length", b"0"),
                    ]
                )
                await send({"type": "http.response.start", "status": 200, "headers": h})
                await send({"type": "http.response.body", "body": b""})
                return

            async def send_wrapper(msg):
                if msg.get("type") == "http.response.start":
                    headers0 = list(msg.get("headers", []))
                    extras = cors_headers()
                    for k, v in extras:
                        if k.lower() == b"vary":
                            _append_vary(headers0, v.decode("latin1"))
                        else:
                            headers0.append((k, v))
                    msg = dict(msg)
                    msg["headers"] = headers0
                await send(msg)

            await app(scope, receive, send_wrapper)

        return wrapped


class GZipMiddleware:
    def __init__(self, minimum_size: int = 500):
        self.minimum_size = int(minimum_size)

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            if scope.get("type") != "http":
                await app(scope, receive, send)
                return

            req_headers = _headers_to_map(scope.get("headers"))
            accepted_encodings = ",".join(req_headers.get("accept-encoding") or []).lower()
            if "gzip" not in accepted_encodings:
                await app(scope, receive, send)
                return

            start_msg: Optional[dict[str, Any]] = None
            body_chunks: list[bytes] = []
            stream_more = False

            async def capture_send(msg):
                nonlocal start_msg, stream_more
                msg_type = msg.get("type")
                if msg_type == "http.response.start":
                    start_msg = dict(msg)
                    return
                if msg_type == "http.response.body":
                    body_chunks.append(bytes(msg.get("body", b"")))
                    if msg.get("more_body"):
                        stream_more = True
                    return
                await send(msg)

            await app(scope, receive, capture_send)
            if start_msg is None:
                return

            raw = b"".join(body_chunks)
            status = int(start_msg.get("status", 200))
            headers0 = list(start_msg.get("headers", []))
            header_map = {k.lower(): v for k, v in headers0}
            content_type = header_map.get(b"content-type", b"").decode("latin1").lower()

            should_skip = (
                stream_more
                or len(raw) < self.minimum_size
                or b"content-encoding" in header_map
                or status < 200
                or status in (204, 304)
                or content_type.startswith("text/event-stream")
            )
            if should_skip:
                await send(start_msg)
                await send({"type": "http.response.body", "body": raw, "more_body": False})
                return

            gz = gzip.compress(raw)
            _upsert_header(headers0, b"content-encoding", b"gzip")
            _append_vary(headers0, "Accept-Encoding")
            _upsert_header(headers0, b"content-length", str(len(gz)).encode("ascii"))

            start_msg = dict(start_msg)
            start_msg["headers"] = headers0
            await send(start_msg)
            await send({"type": "http.response.body", "body": gz, "more_body": False})

        return wrapped


class TrustedHostMiddleware:
    def __init__(self, allowed_hosts: list[str]):
        self.allowed_hosts = [h.lower() for h in allowed_hosts]

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            if scope.get("type") not in ("http", "websocket"):
                await app(scope, receive, send)
                return

            headers = _headers_to_map(scope.get("headers"))
            host = ((headers.get("host") or [""])[-1]).split(":", 1)[0].strip().lower()
            if not any(_match_host(pattern, host) for pattern in self.allowed_hosts):
                if scope.get("type") == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await send(
                    {
                        "type": "http.response.start",
                        "status": 400,
                        "headers": [(b"content-type", b"application/json; charset=utf-8")],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"error":"Invalid Host header"}'})
                return

            await app(scope, receive, send)

        return wrapped


class MemorySessionBackend:
    def __init__(self):
        self._store: dict[str, tuple[dict[str, Any], Optional[float]]] = {}

    def get(self, session_id: str):
        item = self._store.get(session_id)
        if item is None:
            return None
        data, exp = item
        if exp is not None and exp <= __import__("time").time():
            self._store.pop(session_id, None)
            return None
        return dict(data)

    def set(self, session_id: str, data: dict[str, Any], max_age: int):
        exp = __import__("time").time() + int(max_age) if max_age > 0 else None
        self._store[session_id] = (dict(data), exp)

    def delete(self, session_id: str):
        self._store.pop(session_id, None)


class SessionMiddleware:
    def __init__(
        self,
        secret_key: str,
        cookie_name: str = "session",
        max_age: int = 14 * 24 * 3600,
        same_site: str = "Lax",
        https_only: bool = False,
        path: str = "/",
        domain: Optional[str] = None,
        http_only: bool = True,
        partitioned: bool = False,
        signer_salt: str = "turbo.session",
        signer_digest: str = "sha256",
        secret_key_fallbacks: Optional[list[str]] = None,
        backend: Optional[Any] = None,
        session_id_bytes: int = 24,
    ):
        if not secret_key:
            raise ValueError("secret_key required")
        if same_site.lower() not in ("lax", "strict", "none"):
            raise ValueError("same_site must be one of: Lax, Strict, None")
        if same_site.lower() == "none" and not https_only:
            raise ValueError("SameSite=None requires https_only=True")
        if signer_digest.lower() not in hashlib.algorithms_available:
            raise ValueError(f"unsupported signer_digest: {signer_digest}")

        self.cookie_name = cookie_name
        self.max_age = int(max_age)
        self.same_site = same_site
        self.https_only = https_only
        self.path = path
        self.domain = domain
        self.http_only = bool(http_only)
        self.partitioned = bool(partitioned)
        self.signer_salt = signer_salt
        self.signer_digest = signer_digest.lower()
        self.backend = backend
        self.session_id_bytes = int(session_id_bytes)
        self._keys = [secret_key.encode("utf-8")] + [k.encode("utf-8") for k in (secret_key_fallbacks or [])]

    def _sign(self, value: str, key: bytes) -> str:
        msg = f"{self.signer_salt}.{value}".encode("utf-8")
        return hmac.new(key, msg, getattr(hashlib, self.signer_digest)).hexdigest()

    def _encode_token(self, value: str) -> str:
        return f"{value}.{self._sign(value, self._keys[0])}"

    def _decode_token(self, token: str) -> Optional[str]:
        if "." not in token:
            return None
        value, sig = token.rsplit(".", 1)
        for key in self._keys:
            if hmac.compare_digest(self._sign(value, key), sig):
                return value
        return None

    def _encode_payload(self, data: dict[str, Any]) -> str:
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        return self._encode_token(b64)

    def _decode_payload(self, token: str) -> dict[str, Any]:
        b64 = self._decode_token(token)
        if not b64:
            return {}
        pad = "=" * (-len(b64) % 4)
        try:
            data = json.loads(base64.urlsafe_b64decode((b64 + pad).encode("ascii")).decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _cookie_value(self, scope_headers: list[tuple[bytes, bytes]] | None) -> str:
        headers = _headers_to_map(scope_headers)
        raw = (headers.get("cookie") or [""])[-1]
        for item in raw.split(";"):
            item = item.strip()
            if not item or "=" not in item:
                continue
            key, value = item.split("=", 1)
            if key.strip() == self.cookie_name:
                return value.strip()
        return ""

    def _build_set_cookie(self, value: str, *, clear: bool = False) -> bytes:
        bits = [f"{self.cookie_name}={value}", f"Path={self.path}", f"SameSite={self.same_site}"]
        if self.http_only:
            bits.append("HttpOnly")
        if self.domain:
            bits.append(f"Domain={self.domain}")
        if clear:
            bits.append("Max-Age=0")
        else:
            bits.append(f"Max-Age={self.max_age}")
        if self.https_only:
            bits.append("Secure")
        if self.partitioned:
            bits.append("Partitioned")
        return "; ".join(bits).encode("latin1")

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            if scope.get("type") != "http":
                await app(scope, receive, send)
                return

            token = self._cookie_value(scope.get("headers"))
            scope["turbo.session_dirty"] = False
            scope["turbo.session_clear"] = False

            session_id: Optional[str] = None
            if self.backend is None:
                scope["turbo.session"] = self._decode_payload(token) if token else {}
            else:
                session_id = self._decode_token(token) if token else None
                scope["turbo.session_id"] = session_id
                if session_id:
                    loaded = await _call_maybe_await(self.backend.get, session_id)
                    scope["turbo.session"] = dict(loaded or {})
                else:
                    scope["turbo.session"] = {}

            async def send_wrapper(msg):
                if msg.get("type") == "http.response.start":
                    headers = list(msg.get("headers", []))
                    if scope.get("turbo.session_clear"):
                        sid = scope.get("turbo.session_id")
                        if self.backend is not None and sid:
                            await _call_maybe_await(self.backend.delete, sid)
                        headers.append((b"set-cookie", self._build_set_cookie("", clear=True)))
                    elif scope.get("turbo.session_dirty"):
                        session_data = scope.get("turbo.session") or {}
                        if self.backend is None:
                            cookie_val = self._encode_payload(session_data)
                        else:
                            sid = scope.get("turbo.session_id")
                            if not sid:
                                sid = secrets.token_urlsafe(self.session_id_bytes)
                                scope["turbo.session_id"] = sid
                            await _call_maybe_await(self.backend.set, sid, session_data, self.max_age)
                            cookie_val = self._encode_token(sid)
                        headers.append((b"set-cookie", self._build_set_cookie(cookie_val, clear=False)))
                    msg = dict(msg)
                    msg["headers"] = headers
                await send(msg)

            await app(scope, receive, send_wrapper)

        return wrapped


class CSRFMiddleware:
    def __init__(
        self,
        *,
        cookie_name: str = "csrftoken",
        header_name: str = "x-csrf-token",
        safe_methods: Optional[list[str]] = None,
        exempt_paths: Optional[list[str]] = None,
        use_session: bool = True,
        session_key: str = "csrf_token",
        same_site: str = "Lax",
        https_only: bool = False,
        path: str = "/",
        domain: Optional[str] = None,
    ):
        if same_site.lower() not in ("lax", "strict", "none"):
            raise ValueError("same_site must be one of: Lax, Strict, None")
        if same_site.lower() == "none" and not https_only:
            raise ValueError("SameSite=None requires https_only=True")
        self.cookie_name = cookie_name
        self.header_name = header_name.lower()
        self.safe_methods = {m.upper() for m in (safe_methods or ["GET", "HEAD", "OPTIONS", "TRACE"])}
        self.exempt_paths = set(exempt_paths or [])
        self.use_session = bool(use_session)
        self.session_key = session_key
        self.same_site = same_site
        self.https_only = bool(https_only)
        self.path = path
        self.domain = domain

    def _cookie_value(self, scope_headers: list[tuple[bytes, bytes]] | None) -> str:
        headers = _headers_to_map(scope_headers)
        raw = (headers.get("cookie") or [""])[-1]
        for item in raw.split(";"):
            item = item.strip()
            if not item or "=" not in item:
                continue
            key, value = item.split("=", 1)
            if key.strip() == self.cookie_name:
                return value.strip()
        return ""

    def _build_set_cookie(self, token: str) -> bytes:
        bits = [f"{self.cookie_name}={token}", f"Path={self.path}", f"SameSite={self.same_site}"]
        if self.domain:
            bits.append(f"Domain={self.domain}")
        if self.https_only:
            bits.append("Secure")
        return "; ".join(bits).encode("latin1")

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            if scope.get("type") != "http":
                await app(scope, receive, send)
                return

            headers = _headers_to_map(scope.get("headers"))
            method = scope.get("method", "GET").upper()
            path = scope.get("path", "")

            session = scope.get("turbo.session") if self.use_session else None
            session_token = None
            if isinstance(session, dict):
                raw = session.get(self.session_key)
                if isinstance(raw, str) and raw:
                    session_token = raw

            cookie_token = self._cookie_value(scope.get("headers"))
            token = session_token or cookie_token
            new_token = False
            if not token:
                token = secrets.token_urlsafe(32)
                new_token = True

            if isinstance(session, dict):
                if session.get(self.session_key) != token:
                    session[self.session_key] = token
                    scope["turbo.session"] = session
                    scope["turbo.session_dirty"] = True
                    scope["turbo.session_clear"] = False
            scope["turbo.csrf_token"] = token

            if method not in self.safe_methods and path not in self.exempt_paths:
                provided = (headers.get(self.header_name) or [""])[-1].strip()
                if not provided:
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 403,
                            "headers": [(b"content-type", b"application/json; charset=utf-8")],
                        }
                    )
                    await send({"type": "http.response.body", "body": b'{"error":"CSRF token missing"}'})
                    return
                if not hmac.compare_digest(provided, token):
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 403,
                            "headers": [(b"content-type", b"application/json; charset=utf-8")],
                        }
                    )
                    await send({"type": "http.response.body", "body": b'{"error":"CSRF token invalid"}'})
                    return

            async def send_wrapper(msg):
                if msg.get("type") == "http.response.start":
                    response_headers = list(msg.get("headers", []))
                    if new_token or cookie_token != token:
                        response_headers.append((b"set-cookie", self._build_set_cookie(token)))
                    msg = dict(msg)
                    msg["headers"] = response_headers
                await send(msg)

            await app(scope, receive, send_wrapper)

        return wrapped


class MiddlewareStack:
    def __init__(self):
        self._http_mws: list[HTTPMiddleware] = []
        self._asgi_mws: list[Callable[..., Any]] = []

    def add(self, mw: HTTPMiddleware):
        self._http_mws.append(mw)

    def add_asgi(self, mw: Callable[..., Any]):
        self._asgi_mws.append(mw)

    def build_http(self, final_handler):
        handler = final_handler
        for mw in reversed(self._http_mws):
            nxt = handler

            async def wrapped(req, receive, send, _mw=mw, _next=nxt):
                return await _mw(req, lambda r=req: _next(r, receive, send))

            handler = wrapped
        return handler

    def build_asgi(self, final_app: ASGIApp):
        app = final_app
        for mw in reversed(self._asgi_mws):
            try:
                param_count = len(inspect.signature(mw).parameters)
            except (TypeError, ValueError):
                param_count = 1
            if param_count == 1:
                app = mw(app)
            else:
                nxt = app

                async def wrapped(scope, receive, send, _mw=mw, _next=nxt):
                    return await _mw(scope, receive, send, _next)

                app = wrapped
        return app
