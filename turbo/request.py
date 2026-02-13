from __future__ import annotations
import asyncio
import json
import time
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import Dict, Optional, Any, AsyncIterator
from urllib.parse import parse_qs
from .errors import HTTPError

MAX_QUERY_FIELDS = 2048

def _parse_content_type(value: str):
    parts = [p.strip() for p in value.split(";") if p.strip()]
    if not parts:
        return "", {}
    media_type = parts[0].lower()
    params: dict[str, str] = {}
    for p in parts[1:]:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        params[k.strip().lower()] = v.strip().strip('"')
    return media_type, params

def _parse_disposition(value: str):
    parts = [p.strip() for p in value.split(";") if p.strip()]
    disp = parts[0].lower() if parts else ""
    params: dict[str, str] = {}
    for p in parts[1:]:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        params[k.strip().lower()] = v.strip().strip('"')
    return disp, params

@dataclass(slots=True)
class UploadFile:
    filename: str
    content_type: str
    file: Any
    size: int

    async def read(self) -> bytes:
        self.file.seek(0)
        return self.file.read()

    async def close(self):
        self.file.close()

    def seek(self, offset: int):
        self.file.seek(offset)

    @property
    def spooled_to_disk(self) -> bool:
        return bool(getattr(self.file, "_rolled", False))

@dataclass(slots=True)
class WebSocket:
    scope: dict
    _receive: Any
    _send: Any
    path_params: Optional[Dict[str, str]] = None
    accepted: bool = False
    closed: bool = False
    _query_multi: Optional[Dict[str, list[str]]] = None
    _headers_multi: Optional[Dict[str, list[str]]] = None
    _cookies: Optional[Dict[str, str]] = None
    _last_activity: float = 0.0
    close_code: Optional[int] = None
    close_reason: Optional[str] = None

    def __post_init__(self):
        self._last_activity = time.monotonic()

    @property
    def path(self)->str: return self.scope["path"]

    @property
    def headers(self)->Dict[str,str]:
        out={}
        for k,v in self.headers_multi.items():
            out[k] = v[-1] if v else ""
        return out

    @property
    def headers_multi(self)->Dict[str,list[str]]:
        if self._headers_multi is not None:
            return self._headers_multi
        out: Dict[str, list[str]] = {}
        for k, v in self.scope.get("headers", []):
            name = k.decode("latin1").lower()
            out.setdefault(name, []).append(v.decode("latin1"))
        self._headers_multi = out
        return out

    @property
    def query_params_multi(self)->Dict[str,list[str]]:
        if self._query_multi is not None:
            return self._query_multi
        raw=self.scope.get("query_string", b"").decode("utf-8","ignore")
        try:
            parsed = parse_qs(raw, keep_blank_values=True, max_num_fields=MAX_QUERY_FIELDS)
        except ValueError as exc:
            raise HTTPError(413, "Too many query parameters", {"max_fields": MAX_QUERY_FIELDS}) from exc
        self._query_multi = {k: list(v) for k, v in parsed.items()}
        return self._query_multi

    @property
    def query_params(self)->Dict[str,str]:
        out={}
        for k,v in self.query_params_multi.items():
            out[k] = v[0] if v else ""
        return out

    @property
    def cookies(self)->Dict[str,str]:
        if self._cookies is not None:
            return self._cookies
        raw = self.headers.get("cookie", "")
        out: Dict[str, str] = {}
        for token in raw.split(";"):
            token = token.strip()
            if not token or "=" not in token:
                continue
            k, v = token.split("=", 1)
            out[k.strip()] = v.strip()
        self._cookies = out
        return out

    @property
    def requested_subprotocols(self) -> list[str]:
        raw = self.headers.get("sec-websocket-protocol", "")
        if not raw:
            return []
        return [x.strip() for x in raw.split(",") if x.strip()]

    @property
    def idle_seconds(self) -> float:
        return max(0.0, time.monotonic() - self._last_activity)

    def touch(self):
        self._last_activity = time.monotonic()

    def select_subprotocol(self, allowed: list[str]) -> Optional[str]:
        allowed_set = {x.strip().lower(): x for x in allowed}
        for requested in self.requested_subprotocols:
            match = allowed_set.get(requested.lower())
            if match is not None:
                return match
        return None

    async def accept_subprotocol(self, allowed: list[str], fallback: Optional[str] = None):
        chosen = self.select_subprotocol(allowed)
        if chosen is None:
            chosen = fallback
        await self.accept(subprotocol=chosen)
        return chosen

    async def accept(self, subprotocol: Optional[str] = None):
        msg = {"type": "websocket.accept"}
        if subprotocol:
            msg["subprotocol"] = subprotocol
        await self._send(msg)
        self.accepted = True
        self.touch()

    async def receive(self):
        while True:
            event = await self._receive()
            if event.get("type") == "websocket.connect":
                continue
            self.touch()
            if event.get("type") == "websocket.disconnect":
                code = event.get("code")
                self.close_code = normalize_ws_close_code(code if isinstance(code, int) else 1000)
                reason = event.get("reason")
                self.close_reason = str(reason) if isinstance(reason, str) and reason else ws_close_reason(self.close_code)
                self.closed = True
            return event

    async def receive_text(self) -> str:
        event = await self.receive()
        if event.get("type") == "websocket.disconnect":
            raise RuntimeError("WebSocket disconnected")
        if "text" in event and event["text"] is not None:
            return event["text"]
        data = event.get("bytes", b"")
        return data.decode("utf-8", "ignore")

    async def receive_json(self):
        return json.loads(await self.receive_text())

    async def send_text(self, text: str):
        await self._send({"type": "websocket.send", "text": text})
        self.touch()

    async def send_bytes(self, data: bytes):
        await self._send({"type": "websocket.send", "bytes": bytes(data)})
        self.touch()

    async def send_json(self, data: Any):
        await self.send_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))

    async def send_ping(self, payload: str = "turbo:ping"):
        await self.send_text(payload)

    async def send_pong(self, payload: str = "turbo:pong"):
        await self.send_text(payload)

    async def receive_with_idle_timeout(self, timeout: float, close_code: int = 1001, reason: str = "Idle timeout"):
        try:
            return await asyncio.wait_for(self.receive(), timeout=float(timeout))
        except TimeoutError as exc:
            await self.close(close_code, reason=reason)
            raise exc

    def start_heartbeat(self, *, interval: float = 20.0, idle_timeout: float = 60.0, ping_payload: str = "turbo:ping", close_code: int = 1001, close_reason: str = "Idle timeout"):
        async def _runner():
            while not self.closed:
                await asyncio.sleep(float(interval))
                if self.closed:
                    return
                if idle_timeout > 0 and self.idle_seconds >= float(idle_timeout):
                    await self.close(close_code, reason=close_reason)
                    return
                await self.send_ping(ping_payload)
        return asyncio.create_task(_runner())

    async def close(self, code: int = 1000, reason: Optional[str] = None):
        if self.closed:
            return
        normalized = normalize_ws_close_code(code)
        message = {"type": "websocket.close", "code": normalized}
        if reason:
            message["reason"] = str(reason)
        await self._send(message)
        self.close_code = normalized
        self.close_reason = str(reason) if reason else ws_close_reason(normalized)
        self.closed = True

    async def close_with_reason(self, code: int = 1000, reason: Optional[str] = None):
        await self.close(code=code, reason=reason)


WS_CLOSE_REASONS: dict[int, str] = {
    1000: "Normal Closure",
    1001: "Going Away",
    1002: "Protocol Error",
    1003: "Unsupported Data",
    1007: "Invalid Payload Data",
    1008: "Policy Violation",
    1009: "Message Too Big",
    1010: "Mandatory Extension",
    1011: "Internal Error",
    1012: "Service Restart",
    1013: "Try Again Later",
    1014: "Bad Gateway",
}


def normalize_ws_close_code(code: int) -> int:
    try:
        value = int(code)
    except Exception:
        return 1000
    if value in WS_CLOSE_REASONS:
        return value
    if 3000 <= value <= 4999:
        return value
    return 1000


def ws_close_reason(code: int) -> str:
    normalized = normalize_ws_close_code(code)
    if 3000 <= normalized <= 4999 and normalized not in WS_CLOSE_REASONS:
        return "Application Defined"
    return WS_CLOSE_REASONS.get(normalized, "Normal Closure")


class ConnectionManager:
    def __init__(self):
        self._connections: dict[int, WebSocket] = {}
        self._groups: dict[str, set[int]] = {}

    def _key(self, ws: WebSocket) -> int:
        return id(ws)

    @property
    def active_count(self) -> int:
        return len(self._connections)

    def list_groups(self) -> list[str]:
        return sorted(name for name, members in self._groups.items() if members)

    async def connect(self, ws: WebSocket, *, subprotocol: Optional[str] = None, groups: Optional[list[str]] = None):
        await ws.accept(subprotocol=subprotocol)
        self.add(ws, groups=groups)

    def add(self, ws: WebSocket, *, groups: Optional[list[str]] = None):
        key = self._key(ws)
        self._connections[key] = ws
        for group in groups or []:
            self.join(group, ws)

    def remove(self, ws: WebSocket):
        key = self._key(ws)
        self._connections.pop(key, None)
        for group_name in list(self._groups.keys()):
            members = self._groups[group_name]
            members.discard(key)
            if not members:
                self._groups.pop(group_name, None)

    async def disconnect(self, ws: WebSocket, code: int = 1000, reason: Optional[str] = None):
        self.remove(ws)
        if not ws.closed:
            await ws.close(code, reason=reason)

    def join(self, group: str, ws: WebSocket):
        key = self._key(ws)
        self._connections[key] = ws
        self._groups.setdefault(group, set()).add(key)

    def leave(self, group: str, ws: WebSocket):
        key = self._key(ws)
        members = self._groups.get(group)
        if members is None:
            return
        members.discard(key)
        if not members:
            self._groups.pop(group, None)

    async def send_text(self, ws: WebSocket, message: str):
        await ws.send_text(message)

    async def send_json(self, ws: WebSocket, payload: Any):
        await ws.send_json(payload)

    async def broadcast_text(self, message: str, *, group: Optional[str] = None, exclude: Optional[WebSocket] = None):
        await self._broadcast(lambda ws: ws.send_text(message), group=group, exclude=exclude)

    async def broadcast_json(self, payload: Any, *, group: Optional[str] = None, exclude: Optional[WebSocket] = None):
        await self._broadcast(lambda ws: ws.send_json(payload), group=group, exclude=exclude)

    async def _broadcast(self, sender, *, group: Optional[str], exclude: Optional[WebSocket]):
        exclude_key = self._key(exclude) if exclude is not None else None
        if group is None:
            keys = list(self._connections.keys())
        else:
            keys = list(self._groups.get(group, set()))
        stale: list[int] = []
        for key in keys:
            if exclude_key is not None and key == exclude_key:
                continue
            ws = self._connections.get(key)
            if ws is None or ws.closed:
                stale.append(key)
                continue
            try:
                value = sender(ws)
                if asyncio.iscoroutine(value):
                    await value
            except Exception:
                stale.append(key)
        for key in stale:
            ws = self._connections.get(key)
            if ws is not None:
                self.remove(ws)

@dataclass(slots=True)
class Request:
    scope: dict
    _body: Optional[bytes] = None
    path_params: Optional[Dict[str, str]] = None
    _query_multi: Optional[Dict[str, list[str]]] = None
    _headers_multi: Optional[Dict[str, list[str]]] = None
    _cookies: Optional[Dict[str, str]] = None
    _form_multi: Optional[Dict[str, list[Any]]] = None
    multipart_limits: Optional[dict[str, int]] = None

    @property
    def method(self)->str: return self.scope["method"]
    @property
    def path(self)->str: return self.scope["path"]
    @property
    def request_id(self)->Optional[str]:
        rid = self.scope.get("turbo.request_id")
        return str(rid) if rid is not None else None

    @property
    def csrf_token(self)->Optional[str]:
        token = self.scope.get("turbo.csrf_token")
        return str(token) if token is not None else None

    @property
    def headers(self)->Dict[str,str]:
        out={}
        for k,v in self.headers_multi.items():
            out[k] = v[-1] if v else ""
        return out

    @property
    def headers_multi(self)->Dict[str,list[str]]:
        if self._headers_multi is not None:
            return self._headers_multi
        out: Dict[str, list[str]] = {}
        for k, v in self.scope.get("headers", []):
            name = k.decode("latin1").lower()
            out.setdefault(name, []).append(v.decode("latin1"))
        self._headers_multi = out
        return out

    @property
    def query_params(self)->Dict[str,str]:
        out={}
        for k,v in self.query_params_multi.items():
            out[k] = v[0] if v else ""
        return out

    @property
    def query_params_multi(self)->Dict[str,list[str]]:
        if self._query_multi is not None:
            return self._query_multi
        raw=self.scope.get("query_string", b"").decode("utf-8","ignore")
        try:
            parsed = parse_qs(raw, keep_blank_values=True, max_num_fields=MAX_QUERY_FIELDS)
        except ValueError as exc:
            raise HTTPError(413, "Too many query parameters", {"max_fields": MAX_QUERY_FIELDS}) from exc
        self._query_multi = {k: list(v) for k, v in parsed.items()}
        return self._query_multi

    @property
    def cookies(self)->Dict[str,str]:
        if self._cookies is not None:
            return self._cookies
        raw = self.headers.get("cookie", "")
        out: Dict[str, str] = {}
        for token in raw.split(";"):
            token = token.strip()
            if not token or "=" not in token:
                continue
            k, v = token.split("=", 1)
            out[k.strip()] = v.strip()
        self._cookies = out
        return out

    @property
    def session(self) -> Dict[str, Any]:
        data = self.scope.setdefault("turbo.session", {})
        if not isinstance(data, dict):
            data = {}
            self.scope["turbo.session"] = data
        return data

    def set_session(self, data: Dict[str, Any]):
        self.scope["turbo.session"] = dict(data)
        self.scope["turbo.session_dirty"] = True
        self.scope["turbo.session_clear"] = False

    def set_session_value(self, key: str, value: Any):
        data = self.session
        data[key] = value
        self.scope["turbo.session"] = data
        self.scope["turbo.session_dirty"] = True
        self.scope["turbo.session_clear"] = False

    def clear_session(self):
        self.scope["turbo.session"] = {}
        self.scope["turbo.session_dirty"] = False
        self.scope["turbo.session_clear"] = True

    async def stream(self, receive):
        if self._body is not None:
            yield self._body
            return
        chunks = []
        more = True
        while more:
            event=await receive()
            if event["type"]!="http.request":
                continue
            chunk = event.get("body", b"")
            chunks.append(chunk)
            yield chunk
            more=bool(event.get("more_body", False))
        self._body=b"".join(chunks)

    async def body(self, receive)->bytes:
        if self._body is not None:
            return self._body
        chunks = []
        async for c in self.stream(receive):
            chunks.append(c)
        if self._body is None:
            self._body = b"".join(chunks)
        return self._body

    async def json(self, receive):
        b=await self.body(receive)
        if not b:
            return None
        try:
            return json.loads(b.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPError(422, "Malformed JSON body") from exc

    def _limit(self, key: str, default: int):
        if not self.multipart_limits:
            return default
        return int(self.multipart_limits.get(key, default))

    async def form_multi(self, receive)->Dict[str,list[Any]]:
        if self._form_multi is not None:
            return self._form_multi
        content_type = self.headers.get("content-type", "")
        media_type, params = _parse_content_type(content_type)
        out: Dict[str, list[Any]] = {}

        max_fields = self._limit("max_fields", 1000)
        max_file_size = self._limit("max_file_size", 10_000_000)
        max_part_size = self._limit("max_part_size", 10_000_000)
        spool_threshold = self._limit("spool_threshold", 1_000_000)
        if media_type == "application/x-www-form-urlencoded":
            b = await self.body(receive)
            field_count = 0
            try:
                parsed = parse_qs(b.decode("utf-8", "ignore"), keep_blank_values=True, max_num_fields=max_fields)
            except ValueError as exc:
                raise HTTPError(413, "Too many form fields", {"max_fields": max_fields}) from exc
            for k, vals in parsed.items():
                field_count += len(vals)
                if field_count > max_fields:
                    raise HTTPError(413, "Too many form fields", {"max_fields": max_fields})
                out[k] = list(vals)
            self._form_multi = out
            return out
        if media_type != "multipart/form-data":
            self._form_multi = out
            return out
        boundary = params.get("boundary")
        if not boundary:
            self._form_multi = out
            return out
        if self._body is not None:
            chunks = [self._body]
            async def _receive_ignored():
                return {"type": "http.disconnect"}
            await self._parse_multipart_stream(chunks, out, boundary=boundary, max_fields=max_fields, max_file_size=max_file_size, max_part_size=max_part_size, spool_threshold=spool_threshold)
        else:
            await self._parse_multipart_stream(self._iter_http_chunks(receive), out, boundary=boundary, max_fields=max_fields, max_file_size=max_file_size, max_part_size=max_part_size, spool_threshold=spool_threshold)
        self._form_multi = out
        return out

    async def form(self, receive)->Dict[str, Any]:
        multi = await self.form_multi(receive)
        out: Dict[str, Any] = {}
        for k, vals in multi.items():
            out[k] = vals if len(vals) > 1 else vals[0]
        return out

    async def parse_payload(self, receive, *, media_type: Optional[str] = None):
        content_type = media_type or self.headers.get("content-type", "")
        ctype, _ = _parse_content_type(content_type)
        if not ctype:
            ctype = "application/json"
        if ctype == "application/json" or ctype.endswith("+json"):
            return await self.json(receive)
        if ctype == "application/x-www-form-urlencoded":
            b = await self.body(receive)
            max_fields = self._limit("max_fields", 1000)
            try:
                parsed = parse_qs(b.decode("utf-8", "ignore"), keep_blank_values=True, max_num_fields=max_fields)
            except ValueError as exc:
                raise HTTPError(413, "Too many form fields", {"max_fields": max_fields}) from exc
            out = {}
            for k, vals in parsed.items():
                out[k] = vals if len(vals) > 1 else vals[0]
            return out
        if ctype == "multipart/form-data":
            return await self.form(receive)
        if ctype.startswith("text/"):
            b = await self.body(receive)
            return b.decode("utf-8", "ignore")
        return await self.body(receive)

    async def _iter_http_chunks(self, receive) -> AsyncIterator[bytes]:
        while True:
            event = await receive()
            if event.get("type") != "http.request":
                if event.get("type") == "http.disconnect":
                    return
                continue
            chunk = bytes(event.get("body", b""))
            if chunk:
                yield chunk
            if not event.get("more_body", False):
                return

    async def _parse_multipart_stream(self, chunks, out: Dict[str, list[Any]], *, boundary: str, max_fields: int, max_file_size: int, max_part_size: int, spool_threshold: int):
        delim = ("--" + boundary).encode("latin1")
        boundary_marker = b"\r\n" + delim
        buffer = b""
        iterator = chunks.__aiter__() if hasattr(chunks, "__aiter__") else iter(chunks)
        field_count = 0

        async def _next_chunk():
            if hasattr(iterator, "__anext__"):
                try:
                    return await iterator.__anext__()
                except StopAsyncIteration:
                    return None
            try:
                return next(iterator)
            except StopIteration:
                return None

        while True:
            if delim in buffer:
                start = buffer.index(delim)
                buffer = buffer[start + len(delim):]
                break
            nxt = await _next_chunk()
            if nxt is None:
                return
            buffer += nxt

        if buffer.startswith(b"--"):
            return
        if buffer.startswith(b"\r\n"):
            buffer = buffer[2:]

        while True:
            while b"\r\n\r\n" not in buffer:
                nxt = await _next_chunk()
                if nxt is None:
                    return
                buffer += nxt
            raw_headers, buffer = buffer.split(b"\r\n\r\n", 1)
            headers: dict[str, str] = {}
            for line in raw_headers.split(b"\r\n"):
                if b":" not in line:
                    continue
                k, v = line.split(b":", 1)
                headers[k.decode("latin1").lower().strip()] = v.decode("latin1").strip()
            disp, disp_params = _parse_disposition(headers.get("content-disposition", ""))
            if disp != "form-data":
                return
            name = disp_params.get("name")
            if not name:
                return
            field_count += 1
            if field_count > max_fields:
                raise HTTPError(413, "Too many form fields", {"max_fields": max_fields})

            filename = disp_params.get("filename")
            size = 0
            text_chunks: list[bytes] = []
            file_obj = SpooledTemporaryFile(max_size=spool_threshold, mode="w+b") if filename is not None else None

            while True:
                idx = buffer.find(boundary_marker)
                if idx >= 0:
                    data = buffer[:idx]
                    buffer = buffer[idx + 2:]  # now starts with --boundary
                    if data:
                        size += len(data)
                        if size > max_part_size:
                            raise HTTPError(413, "Multipart part too large", {"max_part_size": max_part_size})
                        if file_obj is not None:
                            if size > max_file_size:
                                raise HTTPError(413, "Uploaded file too large", {"max_file_size": max_file_size})
                            file_obj.write(data)
                        else:
                            text_chunks.append(data)
                    break
                keep = len(boundary_marker) + 8
                if len(buffer) > keep:
                    data = buffer[:-keep]
                    buffer = buffer[-keep:]
                    if data:
                        size += len(data)
                        if size > max_part_size:
                            raise HTTPError(413, "Multipart part too large", {"max_part_size": max_part_size})
                        if file_obj is not None:
                            if size > max_file_size:
                                raise HTTPError(413, "Uploaded file too large", {"max_file_size": max_file_size})
                            file_obj.write(data)
                        else:
                            text_chunks.append(data)
                nxt = await _next_chunk()
                if nxt is None:
                    data = buffer
                    buffer = b""
                    if data:
                        size += len(data)
                        if size > max_part_size:
                            raise HTTPError(413, "Multipart part too large", {"max_part_size": max_part_size})
                        if file_obj is not None:
                            if size > max_file_size:
                                raise HTTPError(413, "Uploaded file too large", {"max_file_size": max_file_size})
                            file_obj.write(data)
                        else:
                            text_chunks.append(data)
                    break
                buffer += nxt

            if file_obj is not None:
                file_obj.seek(0)
                value: Any = UploadFile(
                    filename=filename or "",
                    content_type=headers.get("content-type", "application/octet-stream"),
                    file=file_obj,
                    size=size,
                )
            else:
                value = b"".join(text_chunks).decode("utf-8", "ignore")
            out.setdefault(name, []).append(value)

            if not buffer.startswith(delim):
                while not buffer.startswith(delim):
                    nxt = await _next_chunk()
                    if nxt is None:
                        return
                    buffer += nxt
            buffer = buffer[len(delim):]
            if buffer.startswith(b"--"):
                return
            if buffer.startswith(b"\r\n"):
                buffer = buffer[2:]
