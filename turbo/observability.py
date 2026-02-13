from __future__ import annotations

import contextvars
import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

ASGIApp = Callable[[dict, Callable, Callable], Any]

_request_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("turbo_request_id", default=None)


def get_request_id() -> Optional[str]:
    return _request_id_ctx.get()


def set_request_id(request_id: Optional[str]):
    _request_id_ctx.set(request_id)


def _header_map(scope_headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in scope_headers or []:
        out[k.decode("latin1").lower()] = v.decode("latin1")
    return out


class RequestIDMiddleware:
    def __init__(
        self,
        *,
        header_name: str = "x-request-id",
        response_header_name: Optional[str] = "x-request-id",
        generator: Optional[Callable[[], str]] = None,
    ):
        self.header_name = header_name.lower()
        self.response_header_name = response_header_name.lower() if response_header_name else None
        self.generator = generator or (lambda: uuid.uuid4().hex)

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            scope_type = scope.get("type")
            if scope_type not in ("http", "websocket"):
                await app(scope, receive, send)
                return

            headers = _header_map(scope.get("headers"))
            request_id = headers.get(self.header_name) or self.generator()
            scope["turbo.request_id"] = request_id
            token = _request_id_ctx.set(request_id)
            try:
                if scope_type == "http" and self.response_header_name:
                    response_header_name = self.response_header_name
                    async def send_wrapper(msg):
                        if msg.get("type") == "http.response.start":
                            hs = list(msg.get("headers", []))
                            hs.append((response_header_name.encode("latin1"), request_id.encode("latin1")))
                            msg = dict(msg)
                            msg["headers"] = hs
                        await send(msg)

                    await app(scope, receive, send_wrapper)
                    return
                await app(scope, receive, send)
            finally:
                _request_id_ctx.reset(token)

        return wrapped


@dataclass(slots=True)
class LogEvent:
    scope_type: str
    method: str
    path: str
    route: str
    status_code: int
    duration_ms: float
    request_id: Optional[str]
    error: Optional[str] = None


class StructuredLogHook(Protocol):
    def __call__(self, event: LogEvent) -> Any:
        ...


class StructuredLoggingMiddleware:
    def __init__(self, hook: StructuredLogHook):
        self.hook = hook

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            scope_type = scope.get("type", "")
            if scope_type not in ("http", "websocket"):
                await app(scope, receive, send)
                return

            method = scope.get("method", "WS" if scope_type == "websocket" else "")
            path = scope.get("path", "")
            status_code = 500 if scope_type == "http" else 1011
            error: Optional[str] = None
            started = time.perf_counter()

            async def send_wrapper(msg):
                nonlocal status_code
                if msg.get("type") == "http.response.start":
                    status_code = int(msg.get("status", 200))
                elif msg.get("type") == "websocket.close":
                    status_code = int(msg.get("code", 1000))
                await send(msg)

            try:
                await app(scope, receive, send_wrapper)
            except Exception as exc:
                error = type(exc).__name__
                raise
            finally:
                duration_ms = (time.perf_counter() - started) * 1000.0
                event = LogEvent(
                    scope_type=scope_type,
                    method=method,
                    path=path,
                    route=scope.get("turbo.route", "__unmatched__"),
                    status_code=status_code,
                    duration_ms=duration_ms,
                    request_id=scope.get("turbo.request_id") or get_request_id(),
                    error=error,
                )
                result = self.hook(event)
                if hasattr(result, "__await__"):
                    await result

        return wrapped


@dataclass(slots=True)
class MetricEvent:
    scope_type: str
    method: str
    path: str
    route: str
    status_code: int
    duration_ms: float
    request_id: Optional[str]


class MetricsHook(Protocol):
    def __call__(self, event: MetricEvent) -> Any:
        ...


class MetricsMiddleware:
    def __init__(self, hooks: list[MetricsHook]):
        self.hooks = list(hooks)

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            scope_type = scope.get("type", "")
            if scope_type not in ("http", "websocket"):
                await app(scope, receive, send)
                return

            method = scope.get("method", "WS" if scope_type == "websocket" else "")
            path = scope.get("path", "")
            status_code = 500 if scope_type == "http" else 1011
            started = time.perf_counter()

            async def send_wrapper(msg):
                nonlocal status_code
                if msg.get("type") == "http.response.start":
                    status_code = int(msg.get("status", 200))
                elif msg.get("type") == "websocket.close":
                    status_code = int(msg.get("code", 1000))
                await send(msg)

            try:
                await app(scope, receive, send_wrapper)
            finally:
                event = MetricEvent(
                    scope_type=scope_type,
                    method=method,
                    path=path,
                    route=scope.get("turbo.route", "__unmatched__"),
                    status_code=status_code,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    request_id=scope.get("turbo.request_id") or get_request_id(),
                )
                for hook in self.hooks:
                    out = hook(event)
                    if hasattr(out, "__await__"):
                        await out

        return wrapped


def _prom_line(name: str, labels: dict[str, str], value: Any):
    if labels:
        pairs = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{name}{{{pairs}}} {value}"
    return f"{name} {value}"


class PrometheusMiddleware:
    def __init__(
        self,
        *,
        endpoint: str = "/metrics",
        duration_buckets: Optional[list[float]] = None,
        include_process_label: bool = True,
        multiprocess_dir: Optional[str] = None,
        aggregate_workers: bool = True,
    ):
        self.endpoint = endpoint
        self.include_process_label = bool(include_process_label)
        self.aggregate_workers = bool(aggregate_workers)
        self.multiprocess_dir = multiprocess_dir
        self._process_label = str(os.getpid())
        self.duration_buckets = sorted(duration_buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0])
        self._lock = threading.Lock()
        self._total: dict[tuple[str, str, str], int] = {}
        self._duration_count: dict[tuple[str, str], int] = {}
        self._duration_sum: dict[tuple[str, str], float] = {}
        self._duration_buckets: dict[tuple[str, str, float], int] = {}
        self._metrics_file: Optional[str] = None
        if self.multiprocess_dir:
            os.makedirs(self.multiprocess_dir, exist_ok=True)
            self._metrics_file = os.path.join(self.multiprocess_dir, f"turbo-metrics-{self._process_label}.json")

    @staticmethod
    def _encode_totals(totals: dict[tuple[str, str, str], int]):
        return {f"{m}|{r}|{s}": int(v) for (m, r, s), v in totals.items()}

    @staticmethod
    def _decode_totals(data: dict[str, Any]):
        out: dict[tuple[str, str, str], int] = {}
        for key, value in data.items():
            parts = str(key).split("|", 2)
            if len(parts) != 3:
                continue
            try:
                out[(parts[0], parts[1], parts[2])] = int(value)
            except Exception:
                continue
        return out

    @staticmethod
    def _encode_duration_count(values: dict[tuple[str, str], int]):
        return {f"{m}|{r}": int(v) for (m, r), v in values.items()}

    @staticmethod
    def _decode_duration_count(data: dict[str, Any]):
        out: dict[tuple[str, str], int] = {}
        for key, value in data.items():
            parts = str(key).split("|", 1)
            if len(parts) != 2:
                continue
            try:
                out[(parts[0], parts[1])] = int(value)
            except Exception:
                continue
        return out

    @staticmethod
    def _encode_duration_sum(values: dict[tuple[str, str], float]):
        return {f"{m}|{r}": float(v) for (m, r), v in values.items()}

    @staticmethod
    def _decode_duration_sum(data: dict[str, Any]):
        out: dict[tuple[str, str], float] = {}
        for key, value in data.items():
            parts = str(key).split("|", 1)
            if len(parts) != 2:
                continue
            try:
                out[(parts[0], parts[1])] = float(value)
            except Exception:
                continue
        return out

    @staticmethod
    def _encode_duration_buckets(values: dict[tuple[str, str, float], int]):
        return {f"{m}|{r}|{b}": int(v) for (m, r, b), v in values.items()}

    @staticmethod
    def _decode_duration_buckets(data: dict[str, Any]):
        out: dict[tuple[str, str, float], int] = {}
        for key, value in data.items():
            parts = str(key).split("|", 2)
            if len(parts) != 3:
                continue
            try:
                out[(parts[0], parts[1], float(parts[2]))] = int(value)
            except Exception:
                continue
        return out

    def _snapshot_locked(self):
        return {
            "totals": self._encode_totals(self._total),
            "duration_count": self._encode_duration_count(self._duration_count),
            "duration_sum": self._encode_duration_sum(self._duration_sum),
            "duration_buckets": self._encode_duration_buckets(self._duration_buckets),
        }

    def _save_snapshot_locked(self):
        if not self._metrics_file:
            return
        payload = json.dumps(self._snapshot_locked(), separators=(",", ":"), ensure_ascii=False)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=self.multiprocess_dir) as tmp:
            tmp.write(payload)
            temp_name = tmp.name
        os.replace(temp_name, self._metrics_file)

    def _load_snapshot_file(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.loads(fh.read())
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        return {
            "totals": self._decode_totals(raw.get("totals", {})),
            "duration_count": self._decode_duration_count(raw.get("duration_count", {})),
            "duration_sum": self._decode_duration_sum(raw.get("duration_sum", {})),
            "duration_buckets": self._decode_duration_buckets(raw.get("duration_buckets", {})),
        }

    def _aggregate_snapshots(self):
        aggregate: dict[str, dict[Any, Any]] = {
            "totals": {},
            "duration_count": {},
            "duration_sum": {},
            "duration_buckets": {},
        }
        if not self.multiprocess_dir:
            return aggregate
        for name in os.listdir(self.multiprocess_dir):
            if not name.startswith("turbo-metrics-") or not name.endswith(".json"):
                continue
            path = os.path.join(self.multiprocess_dir, name)
            snap = self._load_snapshot_file(path)
            if snap is None:
                continue
            for key, value in snap["totals"].items():
                aggregate["totals"][key] = aggregate["totals"].get(key, 0) + int(value)
            for key, value in snap["duration_count"].items():
                aggregate["duration_count"][key] = aggregate["duration_count"].get(key, 0) + int(value)
            for key, value in snap["duration_sum"].items():
                aggregate["duration_sum"][key] = aggregate["duration_sum"].get(key, 0.0) + float(value)
            for key, value in snap["duration_buckets"].items():
                aggregate["duration_buckets"][key] = aggregate["duration_buckets"].get(key, 0) + int(value)
        return aggregate

    def _record(self, method: str, route: str, status_code: int, duration_seconds: float):
        with self._lock:
            total_key = (method, route, str(status_code))
            self._total[total_key] = self._total.get(total_key, 0) + 1

            dur_key = (method, route)
            self._duration_count[dur_key] = self._duration_count.get(dur_key, 0) + 1
            self._duration_sum[dur_key] = self._duration_sum.get(dur_key, 0.0) + duration_seconds
            for bucket in self.duration_buckets:
                if duration_seconds <= bucket:
                    bkey = (method, route, bucket)
                    self._duration_buckets[bkey] = self._duration_buckets.get(bkey, 0) + 1
            self._save_snapshot_locked()

    def _render(self) -> bytes:
        if self.aggregate_workers and self.multiprocess_dir:
            snap = self._aggregate_snapshots()
            totals = dict(snap["totals"])
            duration_count = dict(snap["duration_count"])
            duration_sum = dict(snap["duration_sum"])
            duration_buckets = dict(snap["duration_buckets"])
        else:
            with self._lock:
                totals = dict(self._total)
                duration_count = dict(self._duration_count)
                duration_sum = dict(self._duration_sum)
                duration_buckets = dict(self._duration_buckets)
        lines: list[str] = []
        lines.append("# HELP turbo_requests_total Total HTTP requests")
        lines.append("# TYPE turbo_requests_total counter")
        for (method, route, status), value in sorted(totals.items()):
            labels = {"method": method, "route": route, "status": status}
            if self.include_process_label:
                labels["process"] = self._process_label
            lines.append(_prom_line("turbo_requests_total", labels, value))

        lines.append("# HELP turbo_request_duration_seconds HTTP request duration")
        lines.append("# TYPE turbo_request_duration_seconds histogram")
        for (method, route), count in sorted(duration_count.items()):
            cumulative = 0
            for bucket in self.duration_buckets:
                bkey = (method, route, bucket)
                cumulative = max(cumulative, duration_buckets.get(bkey, 0))
                labels = {"method": method, "route": route, "le": str(bucket)}
                if self.include_process_label:
                    labels["process"] = self._process_label
                lines.append(
                    _prom_line(
                        "turbo_request_duration_seconds_bucket",
                        labels,
                        cumulative,
                    )
                )
            labels_inf = {"method": method, "route": route, "le": "+Inf"}
            labels_count = {"method": method, "route": route}
            if self.include_process_label:
                labels_inf["process"] = self._process_label
                labels_count["process"] = self._process_label
            lines.append(_prom_line("turbo_request_duration_seconds_bucket", labels_inf, count))
            lines.append(_prom_line("turbo_request_duration_seconds_count", labels_count, count))
            lines.append(_prom_line("turbo_request_duration_seconds_sum", labels_count, duration_sum.get((method, route), 0.0)))
        return ("\n".join(lines) + "\n").encode("utf-8")

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            if scope.get("type") != "http":
                await app(scope, receive, send)
                return

            path = scope.get("path", "")
            if path == self.endpoint:
                body = self._render()
                headers = [
                    (b"content-type", b"text/plain; version=0.0.4"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ]
                await send({"type": "http.response.start", "status": 200, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return

            method = scope.get("method", "")
            status_code = 500
            started = time.perf_counter()

            async def send_wrapper(msg):
                nonlocal status_code
                if msg.get("type") == "http.response.start":
                    status_code = int(msg.get("status", 200))
                await send(msg)

            await app(scope, receive, send_wrapper)
            route = scope.get("turbo.route", "__unmatched__")
            self._record(method, route, status_code, time.perf_counter() - started)

        return wrapped


class TracingHook(Protocol):
    def on_request_start(self, scope: dict, attributes: dict[str, Any]) -> Any:
        ...

    def on_request_end(self, ctx: Any, *, status_code: int, error: Optional[BaseException]) -> Any:
        ...


class TracingMiddleware:
    def __init__(self, hook: TracingHook):
        self.hook = hook

    def __call__(self, app: ASGIApp):
        async def wrapped(scope, receive, send):
            scope_type = scope.get("type")
            if scope_type not in ("http", "websocket"):
                await app(scope, receive, send)
                return

            method = scope.get("method", "WS" if scope_type == "websocket" else "")
            path = scope.get("path", "")
            attrs = {
                "http.method": method if scope_type == "http" else None,
                "http.route": scope.get("turbo.route"),
                "http.target": path,
                "net.transport": "ip_tcp",
                "turbo.scope_type": scope_type,
                "turbo.request_id": scope.get("turbo.request_id") or get_request_id(),
            }
            ctx = self.hook.on_request_start(scope, attrs)
            status_code = 500 if scope_type == "http" else 1011
            error: Optional[BaseException] = None

            async def send_wrapper(msg):
                nonlocal status_code
                if msg.get("type") == "http.response.start":
                    status_code = int(msg.get("status", 200))
                elif msg.get("type") == "websocket.close":
                    status_code = int(msg.get("code", 1000))
                await send(msg)

            try:
                await app(scope, receive, send_wrapper)
            except Exception as exc:
                error = exc
                raise
            finally:
                out = self.hook.on_request_end(ctx, status_code=status_code, error=error)
                if hasattr(out, "__await__"):
                    await out

        return wrapped


class OpenTelemetryTracingHook:
    def __init__(self, tracer: Any = None, *, name: str = "turbo.request"):
        if tracer is None:
            from opentelemetry import trace as otel_trace

            tracer = otel_trace.get_tracer("turboapi")
        self.tracer = tracer
        self.name = name

    def on_request_start(self, scope: dict, attributes: dict[str, Any]):
        cm = self.tracer.start_as_current_span(self.name)
        span = cm.__enter__()
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        return cm, span

    def on_request_end(self, ctx: Any, *, status_code: int, error: Optional[BaseException]):
        cm, span = ctx
        span.set_attribute("http.status_code", int(status_code))
        if error is not None:
            span.record_exception(error)
        cm.__exit__(None, None, None)
