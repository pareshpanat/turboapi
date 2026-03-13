import json

from turbo import (
    APIRouter,
    ClassDepends,
    Depends,
    Header,
    Request,
    TestClient,
    Turbo,
    dependency_group,
    register_extension_hook,
    run_extension_hooks,
)
from turbo.integrations import (
    apply_filters,
    apply_pagination,
    apply_sorting,
    load_pydantic_settings,
    make_sqlalchemy_session_dependency,
    parse_pagination,
    register_sqlalchemy,
    settings_dependency,
)


def run_http(app, method="GET", path="/", headers=None):
    sent = []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }
    events = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    import asyncio

    asyncio.run(app(scope, receive, send))
    start = next(x for x in sent if x["type"] == "http.response.start")
    body = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    return start["status"], json.loads(body.decode("utf-8"))


def test_class_dep_and_dependency_group_route_and_router():
    calls = []

    async def app_guard():
        calls.append("app")

    async def router_guard():
        calls.append("router")

    async def route_guard():
        calls.append("route")

    class TraceContext:
        def __init__(self, req: Request, trace_id: str = Header(alias="x-trace-id")):
            self.trace_id = trace_id
            self.path = req.path

    app = Turbo(dependencies=[dependency_group(app_guard)])
    router = APIRouter(prefix="/v1", dependencies=[dependency_group(router_guard)])

    @router.get("/ctx", dependencies=[dependency_group(route_guard)])
    async def ctx_view(ctx=ClassDepends(TraceContext)):
        calls.append("handler")
        return {"trace_id": ctx.trace_id, "path": ctx.path}

    app.include_router(router)
    status, body = run_http(app, path="/v1/ctx", headers=[(b"x-trace-id", b"trace-1")])
    assert status == 200
    assert body == {"trace_id": "trace-1", "path": "/v1/ctx"}
    assert calls == ["app", "router", "route", "handler"]


def test_override_scope_restores_dependency_overrides():
    app = Turbo()

    async def dep():
        return "base"

    async def dep_override():
        return "overridden"

    @app.get("/value")
    async def value(v=Depends(dep)):
        return {"v": v}

    with TestClient(app) as client:
        assert client.get("/value").json() == {"v": "base"}
        with client.override_scope("test-case"):
            with client.dependency_override(dep, dep_override):
                assert client.get("/value").json() == {"v": "overridden"}
        assert client.get("/value").json() == {"v": "base"}


def test_dependency_graph_debug_output():
    app = Turbo()

    async def dep_c():
        return "c"

    async def dep_b(c=Depends(dep_c)):
        return f"b:{c}"

    async def dep_a(b=Depends(dep_b)):
        return f"a:{b}"

    @app.get("/graph")
    async def graph_route(a=Depends(dep_a)):
        return {"a": a}

    graph = app.dependency_graph_for_route("GET", "/graph")
    assert graph["name"] == "graph_route"
    text = app.format_dependency_graph(graph_route)
    assert "graph_route" in text
    assert "dep_a" in text and "dep_b" in text and "dep_c" in text
    assert "`-- " in text or "|-- " in text


def test_extension_hooks_and_registries():
    app = Turbo()
    events = []

    class DemoExtension:
        name = "demo"

        def setup(self, app_obj):
            app_obj.state.ext_ready = True

    app.use_extension(DemoExtension())
    assert app.get_extension("demo") is not None
    assert app.state.ext_ready is True

    app.register_auth_provider("bearer", {"kind": "jwt"})
    app.register_telemetry_exporter("otlp", {"endpoint": "http://otel"})
    app.register_cache_backend("redis", {"url": "redis://localhost/0"})
    assert app.get_auth_provider("bearer") == {"kind": "jwt"}
    assert app.get_telemetry_exporter("otlp")["endpoint"] == "http://otel"
    assert app.get_cache_backend("redis")["url"].startswith("redis://")

    register_extension_hook(app, lambda event, app, **kwargs: events.append((event, kwargs.get("x"))))
    run_extension_hooks(app, "startup", x=1)
    assert events == [("startup", 1)]


def test_integrations_pagination_settings_and_sqlalchemy_bridge():
    params = parse_pagination(page=2, size=2, sort="id", order="desc")
    rows = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    filtered = apply_filters(rows, {"id": 3})
    assert filtered == [{"id": 3}]
    sorted_rows = apply_sorting(rows, sort=params.sort, order=params.order)
    assert [r["id"] for r in sorted_rows] == [4, 3, 2, 1]
    paged = apply_pagination(sorted_rows, params)
    assert [r["id"] for r in paged] == [2, 1]

    class DemoSettings:
        def __init__(self, mode: str = "dev"):
            self.mode = mode

    loaded = load_pydantic_settings(DemoSettings, mode="prod")
    assert loaded.mode == "prod"

    app = Turbo()
    settings_dep = settings_dependency(DemoSettings, cache=True, mode="test")
    session_dep = make_sqlalchemy_session_dependency(commit_on_exit=True)
    sessions = []
    engines = []

    class DummySession:
        def __init__(self):
            self.committed = False
            self.closed = False
            sessions.append(self)

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    class DummyEngine:
        def __init__(self):
            self.disposed = False
            engines.append(self)

        def dispose(self):
            self.disposed = True

    def fake_create_engine(url, **kwargs):
        return DummyEngine()

    def fake_sessionmaker(bind):
        return lambda: DummySession()

    register_sqlalchemy(
        app,
        "sqlite://",
        create_engine_fn=fake_create_engine,
        sessionmaker_fn=fake_sessionmaker,
    )

    @app.get("/integrations")
    async def integrations_route(cfg=settings_dep, session=session_dep):
        return {"mode": cfg.mode, "session_type": session.__class__.__name__}

    with TestClient(app) as client:
        r1 = client.get("/integrations").json()
        r2 = client.get("/integrations").json()
        assert r1["mode"] == "test"
        assert r2["session_type"] == "DummySession"
    assert len(sessions) == 2
    assert all(s.committed and s.closed for s in sessions)
    assert engines and engines[0].disposed is True
