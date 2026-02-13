import asyncio
import json
import tempfile

from turbo import (
    MetricsMiddleware,
    PrometheusMiddleware,
    Request,
    RequestIDMiddleware,
    StructuredLoggingMiddleware,
    TracingMiddleware,
    Turbo,
    get_request_id,
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


def test_request_id_middleware_context_and_header():
    app = Turbo()
    app.use_asgi(RequestIDMiddleware())

    @app.get("/rid")
    async def rid_endpoint(req: Request):
        return {"scope_rid": req.request_id, "ctx_rid": get_request_id()}

    events = asyncio.run(run_http_events(app, path="/rid"))
    start = next(x for x in events if x["type"] == "http.response.start")
    headers = dict(start["headers"])
    request_id = headers.get(b"x-request-id")
    assert request_id is not None
    payload = json.loads(_body_from_events(events).decode("utf-8"))
    assert payload["scope_rid"] == request_id.decode("latin1")
    assert payload["ctx_rid"] == request_id.decode("latin1")


def test_structured_logging_hook_receives_event():
    app = Turbo()
    events = []
    app.use_asgi(RequestIDMiddleware())
    app.use_asgi(StructuredLoggingMiddleware(lambda event: events.append(event)))

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    asyncio.run(run_http_events(app, path="/ok"))
    assert len(events) == 1
    evt = events[0]
    assert evt.route == "/ok"
    assert evt.status_code == 200
    assert evt.request_id is not None
    assert evt.duration_ms >= 0


def test_metrics_hook_has_route_labels_and_status():
    app = Turbo()
    seen = []
    app.use_asgi(MetricsMiddleware([lambda event: seen.append(event)]))

    @app.get("/users/{user_id}")
    async def user(user_id: int):
        return {"id": user_id}

    asyncio.run(run_http_events(app, path="/users/7"))
    assert len(seen) == 1
    evt = seen[0]
    assert evt.method == "GET"
    assert evt.route == "/users/{user_id}"
    assert evt.status_code == 200
    assert evt.duration_ms >= 0


def test_prometheus_middleware_records_and_exposes_metrics():
    app = Turbo()
    app.use_asgi(PrometheusMiddleware(endpoint="/metrics"))

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    asyncio.run(run_http_events(app, path="/ping"))
    metrics_events = asyncio.run(run_http_events(app, path="/metrics"))
    body = _body_from_events(metrics_events).decode("utf-8")
    assert "turbo_requests_total" in body
    assert 'route="/ping"' in body
    assert "turbo_request_duration_seconds_bucket" in body


def test_prometheus_middleware_aggregates_worker_snapshots():
    with tempfile.TemporaryDirectory() as tmp:
        app = Turbo()
        app.use_asgi(PrometheusMiddleware(endpoint="/metrics", multiprocess_dir=tmp, aggregate_workers=True, include_process_label=False))

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        asyncio.run(run_http_events(app, path="/ping"))
        asyncio.run(run_http_events(app, path="/ping"))

        # Simulate another worker snapshot file.
        other = {
            "totals": {"GET|/ping|200": 3},
            "duration_count": {"GET|/ping": 3},
            "duration_sum": {"GET|/ping": 0.03},
            "duration_buckets": {"GET|/ping|0.1": 3},
        }
        with open(f"{tmp}/turbo-metrics-other.json", "w", encoding="utf-8") as fh:
            json.dump(other, fh)

        metrics_events = asyncio.run(run_http_events(app, path="/metrics"))
        body = _body_from_events(metrics_events).decode("utf-8")
        # 2 local + 3 simulated worker
        assert 'turbo_requests_total{method="GET",route="/ping",status="200"} 5' in body


def test_tracing_hook_lifecycle():
    app = Turbo()
    started = []
    ended = []

    class Hook:
        def on_request_start(self, scope, attributes):
            started.append((scope.get("path"), attributes))
            return {"path": scope.get("path")}

        def on_request_end(self, ctx, *, status_code, error):
            ended.append((ctx["path"], status_code, error))

    app.use_asgi(TracingMiddleware(Hook()))

    @app.get("/trace")
    async def trace():
        return {"ok": True}

    asyncio.run(run_http_events(app, path="/trace"))
    assert len(started) == 1
    assert started[0][0] == "/trace"
    assert len(ended) == 1
    assert ended[0][0] == "/trace"
    assert ended[0][1] == 200
    assert ended[0][2] is None
