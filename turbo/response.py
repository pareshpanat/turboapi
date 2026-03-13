from __future__ import annotations
import asyncio
import inspect
import json
import mimetypes
import os
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from email.utils import formatdate
from enum import Enum
from typing import Iterable, Tuple, Union, Optional, Any, Callable
from uuid import UUID
from .pydantic_compat import is_pydantic_model_instance, dump_pydantic_model

Headers = Iterable[Tuple[bytes, bytes]]
Body = Union[bytes, bytearray, memoryview]
Encoder = Callable[[Any], Any]
Background = Callable[[], Any]

_JSON_ENCODERS: dict[type, Encoder] = {}

def register_json_encoder(tp: type, fn: Encoder):
    _JSON_ENCODERS[tp] = fn

def _find_encoder(value: Any, encoders: Optional[dict[type, Encoder]] = None):
    merged = dict(_JSON_ENCODERS)
    if encoders:
        merged.update(encoders)
    for tp, fn in merged.items():
        if isinstance(value, tp):
            return fn
    return None

def to_jsonable(value: Any, *, encoders: Optional[dict[type, Encoder]] = None):
    encoder = _find_encoder(value, encoders=encoders)
    if encoder is not None:
        return encoder(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v, encoders=encoders) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, encoders=encoders) for k, v in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value), encoders=encoders)
    if is_pydantic_model_instance(value):
        return to_jsonable(dump_pydantic_model(value), encoders=encoders)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return to_jsonable(value.value, encoders=encoders)
    return str(value)

def dumps_json(data: Any, *, encoders: Optional[dict[type, Encoder]] = None):
    return json.dumps(to_jsonable(data, encoders=encoders), separators=(",", ":"), ensure_ascii=False)

class Response:
    status: int = 200
    headers: Optional[Headers] = None
    body: Body = b""
    background: Optional[Background] = None

    def __init__(self, status: int = 200, headers: Optional[Headers] = None, body: Body = b"", background: Optional[Background] = None):
        self.status = status
        self.headers = headers
        self.body = body
        self.background = background

    async def _run_background(self):
        if self.background is None:
            return
        val = self.background()
        if inspect.isawaitable(val):
            await val

    async def send(self, send):
        headers = list(self.headers or [])
        await send({"type":"http.response.start","status":self.status,"headers":headers})
        await send({"type":"http.response.body","body":bytes(self.body)})
        await self._run_background()

class TextResponse(Response):
    def __init__(self, text: str, status: int=200, headers: Optional[Headers]=None, background: Optional[Background]=None):
        hdrs=list(headers or [])
        hdrs.append((b"content-type", b"text/plain; charset=utf-8"))
        super().__init__(status=status, headers=hdrs, body=text.encode("utf-8"), background=background)

class HTMLResponse(Response):
    def __init__(self, html: str, status: int=200, headers: Optional[Headers]=None, background: Optional[Background]=None):
        hdrs=list(headers or [])
        hdrs.append((b"content-type", b"text/html; charset=utf-8"))
        super().__init__(status=status, headers=hdrs, body=html.encode("utf-8"), background=background)

class JSONResponse(Response):
    def __init__(self, data: Any, status: int=200, headers: Optional[Headers]=None, dumps=None, encoders: Optional[dict[type, Encoder]] = None, background: Optional[Background]=None):
        dumps_fn = dumps or (lambda v: dumps_json(v, encoders=encoders))
        payload=dumps_fn(data).encode("utf-8")
        hdrs=list(headers or [])
        hdrs.append((b"content-type", b"application/json; charset=utf-8"))
        super().__init__(status=status, headers=hdrs, body=payload, background=background)

class RedirectResponse(Response):
    def __init__(self, url: str, status: int = 307, headers: Optional[Headers] = None, background: Optional[Background] = None):
        hdrs = list(headers or [])
        hdrs.append((b"location", url.encode("latin1")))
        hdrs.append((b"content-length", b"0"))
        super().__init__(status=status, headers=hdrs, body=b"", background=background)

def build_cache_control(
    *,
    max_age: Optional[int] = None,
    s_maxage: Optional[int] = None,
    public: bool = False,
    private: bool = False,
    no_cache: bool = False,
    no_store: bool = False,
    must_revalidate: bool = False,
    immutable: bool = False,
) -> str:
    parts: list[str] = []
    if no_store:
        parts.append("no-store")
    if no_cache:
        parts.append("no-cache")
    if public:
        parts.append("public")
    if private:
        parts.append("private")
    if max_age is not None:
        parts.append(f"max-age={int(max_age)}")
    if s_maxage is not None:
        parts.append(f"s-maxage={int(s_maxage)}")
    if must_revalidate:
        parts.append("must-revalidate")
    if immutable:
        parts.append("immutable")
    return ", ".join(parts)

def with_cache_headers(
    headers: Optional[Headers] = None,
    *,
    cache_control: Optional[str] = None,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> list[tuple[bytes, bytes]]:
    out = list(headers or [])
    if cache_control:
        out.append((b"cache-control", cache_control.encode("latin1")))
    if etag:
        out.append((b"etag", etag.encode("latin1")))
    if last_modified:
        out.append((b"last-modified", last_modified.encode("latin1")))
    return out

def negotiate_content_type(accept_header: str, available: list[str], default: Optional[str] = None) -> Optional[str]:
    if not available:
        return None
    if not accept_header.strip():
        return default or available[0]
    ranges = [x.strip() for x in accept_header.split(",") if x.strip()]
    parsed: list[tuple[str, float]] = []
    for item in ranges:
        media = item
        q = 1.0
        if ";" in item:
            media, *params = [x.strip() for x in item.split(";")]
            for param in params:
                if param.startswith("q="):
                    try:
                        q = float(param[2:])
                    except ValueError:
                        q = 0.0
        parsed.append((media.lower(), q))
    parsed.sort(key=lambda x: x[1], reverse=True)
    lowers = [x.lower() for x in available]
    bases = [x.split(";", 1)[0].strip().lower() for x in available]
    for media, q in parsed:
        if q <= 0:
            continue
        if media == "*/*":
            return default or available[0]
        if media in lowers:
            return available[lowers.index(media)]
        if media in bases:
            return available[bases.index(media)]
        if media.endswith("/*"):
            prefix = media.split("/", 1)[0] + "/"
            for actual, base in zip(available, bases):
                if base.startswith(prefix):
                    return actual
    return default

class NegotiatedResponse(Response):
    def __init__(self, accept_header: str, variants: dict[str, Any], *, status: int = 200, headers: Optional[Headers] = None, default_media_type: Optional[str] = None, background: Optional[Background] = None):
        chosen = negotiate_content_type(accept_header, list(variants.keys()), default=default_media_type)
        if chosen is None:
            super().__init__(status=406, headers=[(b"content-length", b"0")], body=b"", background=background)
            return
        value = variants[chosen]
        if isinstance(value, bytes):
            body = value
        elif isinstance(value, str):
            body = value.encode("utf-8")
        else:
            body = dumps_json(value).encode("utf-8")
        hdrs = list(headers or [])
        hdrs.append((b"content-type", chosen.encode("latin1")))
        super().__init__(status=status, headers=hdrs, body=body, background=background)

class StreamingResponse(Response):
    def __init__(self, content, status: int = 200, headers: Optional[Headers] = None, media_type: str = "application/octet-stream", background: Optional[Background]=None):
        hdrs = list(headers or [])
        hdrs.append((b"content-type", media_type.encode("latin1")))
        super().__init__(status=status, headers=hdrs, body=b"", background=background)
        self.content = content

    async def send(self, send):
        headers = list(self.headers or [])
        await send({"type":"http.response.start","status":self.status,"headers":headers})
        content = self.content
        if hasattr(content, "__aiter__"):
            async for chunk in content:
                b = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
                await send({"type":"http.response.body","body":b,"more_body":True})
        else:
            for chunk in content:
                b = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
                await send({"type":"http.response.body","body":b,"more_body":True})
        await send({"type":"http.response.body","body":b"","more_body":False})
        await self._run_background()

@dataclass(slots=True)
class SSEEvent:
    data: Any
    event: Optional[str] = None
    id: Optional[str] = None
    retry: Optional[int] = None
    comment: Optional[str] = None

def encode_sse_event(event: Any) -> bytes:
    if isinstance(event, bytes):
        if event.endswith(b"\n\n"):
            return event
        return event + b"\n\n"
    if isinstance(event, str):
        payload = SSEEvent(data=event)
    elif isinstance(event, dict) and "data" in event:
        payload = SSEEvent(
            data=event.get("data"),
            event=event.get("event"),
            id=event.get("id"),
            retry=event.get("retry"),
            comment=event.get("comment"),
        )
    elif isinstance(event, SSEEvent):
        payload = event
    else:
        payload = SSEEvent(data=event)

    lines: list[str] = []
    if payload.comment is not None:
        for line in str(payload.comment).splitlines() or [""]:
            lines.append(f": {line}")
    if payload.id is not None:
        lines.append(f"id: {payload.id}")
    if payload.event is not None:
        lines.append(f"event: {payload.event}")
    if payload.retry is not None:
        lines.append(f"retry: {int(payload.retry)}")
    data = payload.data
    if isinstance(data, (dict, list, int, float, bool)) or data is None:
        text = dumps_json(data)
    else:
        text = str(data)
    for line in text.splitlines() or [""]:
        lines.append(f"data: {line}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")

class EventSourceResponse(StreamingResponse):
    def __init__(self, events, *, status: int = 200, headers: Optional[Headers] = None, ping_interval: Optional[float] = 15.0, ping_message: str = "ping", background: Optional[Background] = None):
        hdrs = list(headers or [])
        hdrs.append((b"cache-control", b"no-cache"))
        hdrs.append((b"connection", b"keep-alive"))
        hdrs.append((b"x-accel-buffering", b"no"))
        super().__init__(events, status=status, headers=hdrs, media_type="text/event-stream; charset=utf-8", background=background)
        self.ping_interval = ping_interval
        self.ping_message = ping_message

    async def send(self, send):
        headers = list(self.headers or [])
        await send({"type":"http.response.start","status":self.status,"headers":headers})
        content = self.content
        if hasattr(content, "__aiter__"):
            iterator = content.__aiter__()
            while True:
                try:
                    if self.ping_interval and self.ping_interval > 0:
                        item = await asyncio.wait_for(iterator.__anext__(), timeout=float(self.ping_interval))
                    else:
                        item = await iterator.__anext__()
                except TimeoutError:
                    ping_chunk = encode_sse_event(SSEEvent(data=self.ping_message, event="ping"))
                    await send({"type":"http.response.body","body":ping_chunk,"more_body":True})
                    continue
                except StopAsyncIteration:
                    break
                await send({"type":"http.response.body","body":encode_sse_event(item),"more_body":True})
        else:
            for item in content:
                await send({"type":"http.response.body","body":encode_sse_event(item),"more_body":True})
        await send({"type":"http.response.body","body":b"","more_body":False})
        await self._run_background()

class FileResponse(Response):
    def __init__(self, path: str, status: int = 200, headers: Optional[Headers] = None, filename: Optional[str] = None, chunk_size: int = 64 * 1024, background: Optional[Background]=None):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        self.path = path
        self.chunk_size = chunk_size
        self.filename = filename
        self.range_start: Optional[int] = None
        self.range_end: Optional[int] = None
        self.content_type = mimetypes.guess_type(filename or path)[0] or "application/octet-stream"
        self.file_size = os.path.getsize(path)
        stat = os.stat(path)
        self.last_modified = formatdate(stat.st_mtime, usegmt=True)
        self.etag = f'W/"{self.file_size:x}-{int(stat.st_mtime_ns):x}"'
        hdrs = list(headers or [])
        if filename:
            hdrs.append((b"content-disposition", f'attachment; filename="{filename}"'.encode("latin1", "ignore")))
        super().__init__(status=status, headers=hdrs, body=b"", background=background)

    def _header_map(self):
        out = {}
        for k, v in list(self.headers or []):
            out[k.lower()] = v
        return out

    def _set_base_headers(self):
        hm = self._header_map()
        hm[b"content-type"] = self.content_type.encode("latin1")
        hm[b"accept-ranges"] = b"bytes"
        hm[b"etag"] = self.etag.encode("latin1")
        hm[b"last-modified"] = self.last_modified.encode("latin1")
        if self.range_start is None:
            hm[b"content-length"] = str(self.file_size).encode("ascii")
            hm.pop(b"content-range", None)
        else:
            range_end = self.range_end if self.range_end is not None else (self.file_size - 1)
            length = range_end - self.range_start + 1
            hm[b"content-length"] = str(length).encode("ascii")
            hm[b"content-range"] = f"bytes {self.range_start}-{range_end}/{self.file_size}".encode("ascii")
        self.headers = [(k, v) for k, v in hm.items()]

    def _parse_range(self, value: str):
        if not value.startswith("bytes="):
            return None
        part = value[len("bytes="):].strip()
        if "," in part:
            return None
        if "-" not in part:
            return None
        a, b = part.split("-", 1)
        a = a.strip()
        b = b.strip()
        if not a and not b:
            return None
        if a:
            try:
                start = int(a)
            except ValueError:
                return None
            if b:
                try:
                    end = int(b)
                except ValueError:
                    return None
            else:
                end = self.file_size - 1
        else:
            try:
                suffix = int(b)
            except ValueError:
                return None
            if suffix <= 0:
                return None
            if suffix >= self.file_size:
                start = 0
            else:
                start = self.file_size - suffix
            end = self.file_size - 1
        if start < 0 or end < start or start >= self.file_size:
            return None
        end = min(end, self.file_size - 1)
        return start, end

    def prepare_for_request(self, headers: dict[str, str], method: str = "GET"):
        self.range_start = None
        self.range_end = None
        inm = headers.get("if-none-match")
        if inm:
            tags = [x.strip() for x in inm.split(",")]
            if "*" in tags or self.etag in tags:
                self.status = 304
                self.body = b""
                self._set_base_headers()
                hm = self._header_map()
                hm.pop(b"content-length", None)
                hm.pop(b"content-range", None)
                self.headers = [(k, v) for k, v in hm.items()]
                return
        if method.upper() == "GET":
            rng = headers.get("range")
            if rng:
                parsed = self._parse_range(rng)
                if parsed is None:
                    self.status = 416
                    hm = self._header_map()
                    hm[b"content-range"] = f"bytes */{self.file_size}".encode("ascii")
                    hm[b"content-length"] = b"0"
                    hm[b"content-type"] = self.content_type.encode("latin1")
                    hm[b"accept-ranges"] = b"bytes"
                    hm[b"etag"] = self.etag.encode("latin1")
                    hm[b"last-modified"] = self.last_modified.encode("latin1")
                    self.headers = [(k, v) for k, v in hm.items()]
                    self.body = b""
                    return
                self.status = 206
                self.range_start, self.range_end = parsed
        self._set_base_headers()

    async def send(self, send):
        self._set_base_headers() if self.headers is None else None
        headers = list(self.headers or [])
        await send({"type":"http.response.start","status":self.status,"headers":headers})
        if self.status in (304, 416):
            await send({"type":"http.response.body","body":b""})
            await self._run_background()
            return
        start: int = self.range_start if self.range_start is not None else 0
        end: int = self.range_end if self.range_end is not None else (self.file_size - 1)
        with open(self.path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(self.chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                await send({"type":"http.response.body","body":chunk,"more_body":remaining > 0})
        if end < start:
            await send({"type":"http.response.body","body":b""})
        await self._run_background()

class BackgroundTask:
    def __init__(self, fn: Callable[..., Any], *args, **kwargs):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
    def __call__(self):
        return self.fn(*self.args, **self.kwargs)
