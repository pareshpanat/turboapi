# TurboAPI API Reference

This document covers the full public API exported by `from turbo import *` in `py-turbo-api`.

## Package Exports

The package exports these symbols:

- App and router: `Turbo`, `APIRouter`, `TurboSettings`
- Request and WebSocket: `Request`, `WebSocket`, `UploadFile`, `ConnectionManager`, `normalize_ws_close_code`, `ws_close_reason`
- Responses: `Response`, `JSONResponse`, `TextResponse`, `HTMLResponse`, `RedirectResponse`, `StreamingResponse`, `EventSourceResponse`, `SSEEvent`, `encode_sse_event`, `negotiate_content_type`, `NegotiatedResponse`, `build_cache_control`, `with_cache_headers`, `FileResponse`, `BackgroundTask`, `register_json_encoder`
- Errors: `HTTPError`
- Dependencies and params: `Depends`, `Security`, `ClassDepends`, `DependencyGroup`, `dependency_group`, `Query`, `Header`, `Cookie`, `Form`, `File`, `Host`, `Body`
- Lifespan helpers: `app_state_dependency`, `get_app_state`
- Validation models: `Model`, `field`, `field_validator`, `model_validator`, `type_validator`
- Security helpers: `api_key_auth`, `bearer_auth`, `jwt_auth`, `oauth2_bearer`, `oauth2_authorization_code`, `oauth2_client_credentials`, `csrf_token`, `csrf_protect`, `JWKSCache`, `websocket_token_auth`, `websocket_jwt_auth`
- Middleware: `CORSMiddleware`, `GZipMiddleware`, `CompressionMiddleware`, `RateLimitMiddleware`, `ResponseCacheMiddleware`, `TrustedHostMiddleware`, `SessionMiddleware`, `CSRFMiddleware`, `HTTPSRedirectMiddleware`, `ProxyHeadersMiddleware`, `MemorySessionBackend`
- Observability: `RequestIDMiddleware`, `StructuredLoggingMiddleware`, `MetricsMiddleware`, `PrometheusMiddleware`, `TracingMiddleware`, `OpenTelemetryTracingHook`, `LogEvent`, `MetricEvent`, `get_request_id`, `set_request_id`
- Testing: `TestClient`, `AsyncTestClient`, `WebSocketTestSession`, `TestResponse`
- Jobs: `InMemoryJobQueue`, `RetryPolicy`, `JobRecord`, `CeleryQueueAdapter`, `RQQueueAdapter`, `RedisQueueAdapter`

## Usage Quickstart

```python
from turbo import Turbo, Model, field, Query, Depends, HTTPError

app = Turbo(title="Todo API", version="1.0.0")

class TodoIn(Model):
    title: str = field(min_len=1, max_len=200)

class TodoOut(Model):
    id: int
    title: str

def require_tenant(x_tenant: str = Query(alias="tenant")):
    if not x_tenant:
        raise HTTPError(400, "tenant is required")
    return x_tenant

@app.post("/todos", response_model=TodoOut, status_code=201)
async def create_todo(body: TodoIn, tenant=Depends(require_tenant)):
    return {"id": 1, "title": body.title}
```

## 1. App and Routing

### `Turbo`

Create the ASGI app and register routes, middleware, docs, and lifecycle handlers.

Constructor:

```python
Turbo(
    *,
    request_timeout=10.0,
    max_body_bytes=1_000_000,
    max_concurrency=200,
    title="TurboAPI",
    version="0.1.0",
    multipart_max_fields=1000,
    multipart_max_file_size=10_000_000,
    multipart_spool_threshold=1_000_000,
    multipart_max_part_size=10_000_000,
    redirect_slashes=True,
    redirect_status_code=307,
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_js_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js",
    swagger_css_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css",
    redoc_js_url="https://unpkg.com/redoc@2/bundles/redoc.standalone.js",
    docs_auth=None,
    operation_id_strategy="function",
    operation_id_generator=None,
    shutdown_drain_timeout=10.0,
    dependencies=None,
)
```

Main methods:

- Routing: `route`, `get`, `post`, `put`, `delete`, `patch`, `head`, `options`, `add_api_route`, `websocket`
- Router composition: `include_router`
- Middleware: `use` (request/response middleware), `use_asgi` (ASGI middleware)
- Mounting: `mount_static`, `mount`, `mount_host`
- Lifespan resources: `add_state_resource`, `remove_state_resource`, `state_dependency`
- OpenAPI customization: `set_openapi_transform`, `openapi_transform`, `set_openapi_extension`, `remove_openapi_extension`, `set_operation_id_generator`, `set_openapi_servers`, `add_openapi_server`, `clear_openapi_servers`, `set_openapi_security`, `add_openapi_security_requirement`, `clear_openapi_security`, `set_openapi_reuse_parameters`, `enable_docs_self_host`
- Docs protection: `set_docs_auth`, `docs_auth`
- Lifecycle: `on_event`, `startup`, `shutdown`
- Error handling: `exception_handler`
- Dependency override helpers: `override_dependency`, `override_dependencies`, `override_scope`, `clear_dependency_overrides`
- Dependency graph helpers: `dependency_graph`, `dependency_graph_for_route`, `format_dependency_graph`
- Extensions and registries: `use_extension`, `get_extension`, `register_auth_provider`, `get_auth_provider`, `register_telemetry_exporter`, `get_telemetry_exporter`, `register_cache_backend`, `get_cache_backend`
- JSON encoder registration on app: `json_encoder`
- Settings constructors: `Turbo.from_settings(...)`, `Turbo.from_env(prefix="TURBO_")`

Route decorator extras are available on HTTP methods and `route(...)`:

- `name`, `operation_id`
- `response_model`, `status_code`
- `include_in_schema`, `internal`
- `tags`, `summary`, `description`
- `responses`, `security`, `deprecated`
- `callbacks`, `webhooks`, `examples`
- `response_description`, `openapi_extra`
- `dependencies` (list of `Depends(...)` or callables, executed before handler)

WebSocket decorator extras on `websocket(...)`:

- `name`, `operation_id`
- `include_in_schema`
- `tags`, `summary`, `description`
- `deprecated`, `examples`, `openapi_extra`
- `subprotocols`
- `dependencies` (list of `Depends(...)` or callables, executed before handler)

### `APIRouter`

Sub-router for route grouping and composition.

```python
APIRouter(*, prefix="", tags=None, dependencies=None)
```

`dependencies` applies shared dependencies to all routes in the router.

Methods:

- `route`, `get`, `post`, `put`, `delete`, `patch`, `head`, `options`
- `add_api_route`

`include_router(router, prefix="", tags=None, dependencies=None)` can add extra dependencies to all included routes.

Usage:

```python
from turbo import Turbo, APIRouter

app = Turbo()
users = APIRouter(prefix="/users", tags=["users"])

@users.get("/{user_id:int}")
async def get_user(user_id: int):
    return {"id": user_id}

app.include_router(users, prefix="/v1")
```

### `TurboSettings`

Dataclass for env-driven runtime config.

```python
TurboSettings(
    request_timeout=10.0,
    max_body_bytes=1_000_000,
    max_concurrency=200,
    multipart_max_fields=1000,
    multipart_max_file_size=10_000_000,
    multipart_spool_threshold=1_000_000,
    multipart_max_part_size=10_000_000,
    redirect_slashes=True,
    redirect_status_code=307,
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    shutdown_drain_timeout=10.0,
    title="TurboAPI",
    version="0.1.0",
)
```

Methods:

- `TurboSettings.from_env(prefix="TURBO_")`
- `to_turbo_kwargs()`

## 2. Request and WebSocket API

### `Request`

HTTP request wrapper.

Key properties:

- `method`, `path`
- `app`
- `state`
- `path_params`
- `headers`, `headers_multi`
- `query_params`, `query_params_multi`
- `cookies`
- `request_id`
- `csrf_token`
- `session`

Methods:

- `stream(receive)`
- `body(receive)`
- `json(receive)`
- `form_multi(receive)`
- `form(receive)`
- `parse_payload(receive, media_type=None)`
- `set_session(data)`
- `set_session_value(key, value)`
- `clear_session()`

Notes:

- `request.app` references the current `Turbo` application instance.
- `request.state` is request-scoped mutable state.
- `app.state` is process-scoped mutable state shared across requests.

### Lifespan state helpers

- `app.add_state_resource(name, factory, cleanup=None)` registers startup bootstrapping + shutdown cleanup for `app.state.<name>`.
- `app.remove_state_resource(name)` removes a registered resource.
- `app.state_dependency(name, default=..., required=True)` creates a dependency that reads `app.state`.
- `app_state_dependency(name, default=..., required=True, expected_type=None)` helper version that returns `Depends(...)`.
- `get_app_state(request, name, default=..., expected_type=None)` typed getter.

### `UploadFile`

Uploaded multipart file object.

- Fields: `filename`, `content_type`, `file`, `size`
- Methods: `read()`, `seek(offset)`, `close()`
- Property: `spooled_to_disk`

### `WebSocket`

WebSocket connection wrapper with helpers.

Key properties:

- `path`, `path_params`
- `headers`, `headers_multi`
- `query_params`, `query_params_multi`
- `cookies`
- `requested_subprotocols`
- `accepted`, `closed`
- `close_code`, `close_reason`
- `idle_seconds`

Methods:

- Handshake: `accept(subprotocol=None)`, `accept_subprotocol(allowed, fallback=None)`, `select_subprotocol(allowed)`
- Receive: `receive()`, `receive_text()`, `receive_json()`, `receive_with_idle_timeout(timeout, close_code=1001, reason="Idle timeout")`
- Send: `send_text(text)`, `send_bytes(data)`, `send_json(data)`, `send_ping(payload="turbo:ping")`, `send_pong(payload="turbo:pong")`
- Lifecycle: `touch()`, `start_heartbeat(...)`, `close(code=1000, reason=None)`, `close_with_reason(...)`

### `ConnectionManager`

Tracks active sockets and group membership.

Methods:

- Connection lifecycle: `connect`, `add`, `remove`, `disconnect`
- Group ops: `join`, `leave`, `list_groups`
- Messaging: `send_text`, `send_json`, `broadcast_text`, `broadcast_json`

Property:

- `active_count`

### Utilities

- `normalize_ws_close_code(code)`
- `ws_close_reason(code)`

## 3. Responses

### Base types

- `Response(status=200, headers=None, body=b"", background=None)`
- `JSONResponse(data, status=200, headers=None, dumps=None, encoders=None, background=None)`
- `TextResponse(text, status=200, headers=None, background=None)`
- `HTMLResponse(html, status=200, headers=None, background=None)`
- `RedirectResponse(url, status=307, headers=None, background=None)`
- `StreamingResponse(content, status=200, headers=None, media_type="application/octet-stream", background=None)`
- `EventSourceResponse(events, status=200, headers=None, ping_interval=15.0, ping_message="ping", background=None)`
- `FileResponse(path, status=200, headers=None, filename=None, chunk_size=65536, background=None)`
- `NegotiatedResponse(accept_header, variants, status=200, headers=None, default_media_type=None, background=None)`

### SSE helpers

- `SSEEvent(data, event=None, id=None, retry=None, comment=None)`
- `encode_sse_event(event)`

### Content negotiation and cache helpers

- `negotiate_content_type(accept_header, available, default=None)`
- `build_cache_control(...)`
- `with_cache_headers(headers=None, cache_control=None, etag=None, last_modified=None)`

### Background work

- `BackgroundTask(fn, *args, **kwargs)`

### JSON encoders

- Global registration: `register_json_encoder(type_, encoder_fn)`
- Per-app registration: `app.json_encoder(type_, encoder_fn)`

## 4. Errors and Dependency Injection

### `HTTPError`

```python
HTTPError(status, message="Error", detail=None)
```

Raised to return structured error responses (for example `raise HTTPError(404, "Not Found")`).

### Dependency primitives

- `Depends(call, cache=True, scopes=None)`
- `Security(call, scopes=None, cache=True)`
- `ClassDepends(cls, cache=True)` for class-based dependency constructors.
- `dependency_group(*items)` returns `DependencyGroup(...)` for reusable dependency sets.

### Parameter markers

- `Query(alias=None, required=True, description=None, example=None, examples=None, deprecated=False, schema=None)`
- `Header(alias=None, required=True, description=None, example=None, examples=None, deprecated=False, schema=None)`
- `Cookie(alias=None, required=True, description=None, example=None, examples=None, deprecated=False, schema=None)`
- `Form(alias=None, required=True, description=None, example=None, examples=None, deprecated=False, schema=None)`
- `File(alias=None, required=True, description=None, example=None, examples=None, deprecated=False, schema=None)`
- `Host(alias=None, required=True, description=None, example=None, examples=None, deprecated=False, schema=None)`
- `Body(alias=None, required=True, media_type=None, embed=None, description=None, example=None, examples=None, schema=None)`

Example:

```python
from turbo import Turbo, Query, Header, Depends

app = Turbo()

def auth(authorization: str = Header(alias="authorization")):
    return authorization

@app.get("/items/{item_id:int}")
async def read_item(
    item_id: int,
    q: str = Query(required=False),
    token=Depends(auth),
):
    return {"item_id": item_id, "q": q, "token": token}
```

## 5. Validation Models

### `Model`

Typed validation and schema base class.

Optional compatibility:

- If `pydantic>=2` is installed, `pydantic.BaseModel` can also be used for request bodies and `response_model`.

### Field and validator decorators

- `field(min_len=None, max_len=None, ge=None, le=None, gt=None, lt=None, multiple_of=None, min_items=None, max_items=None, regex=None, discriminator=None, alias=None)`
- `field_validator(*field_names, mode="after")`
- `model_validator(mode="after")`
- `type_validator(type_, mode="after")` for non-`Model` custom types.

Example:

```python
from turbo import Model, field, field_validator, model_validator

class UserIn(Model):
    email: str = field(min_len=5, max_len=320)
    age: int = field(ge=13)

    @field_validator("email")
    def normalize_email(cls, value: str):
        return value.strip().lower()

    @model_validator()
    def ensure_domain(cls, data):
        if "@example.com" not in data["email"]:
            raise ValueError("email domain must be @example.com")
        return data
```

## 6. Security API

### HTTP auth builders

- `api_key_auth(header_name="x-api-key", scheme_name="ApiKeyAuth", auto_error=True)`
- `bearer_auth(scheme_name="BearerAuth", bearer_format=None, auto_error=True)`
- `jwt_auth(secret=None, scheme_name="JWTAuth", issuer=None, audience=None, leeway=0, auto_error=True, algorithms=None, public_key=None, jwks_url=None, jwks_cache=None)`

### OAuth2 builders

- `oauth2_bearer(token_url, scheme_name="OAuth2", scopes=None, secret=None, public_key=None, jwks_url=None, algorithms=None, issuer=None, audience=None, leeway=0, auto_error=True)`
- `oauth2_authorization_code(authorization_url, token_url, refresh_url=None, scheme_name="OAuth2AuthorizationCode", scopes=None, secret=None, public_key=None, jwks_url=None, algorithms=None, issuer=None, audience=None, leeway=0, auto_error=True)`
- `oauth2_client_credentials(token_url, refresh_url=None, scheme_name="OAuth2ClientCredentials", scopes=None, secret=None, public_key=None, jwks_url=None, algorithms=None, issuer=None, audience=None, leeway=0, auto_error=True)`

### CSRF helpers

- `csrf_token(req, auto_error=True)`
- `csrf_protect(header_name="x-csrf-token", auto_error=True)`

### WebSocket auth

- `websocket_token_auth(query_param="token", scheme_name="WebSocketTokenAuth", auto_error=True)`
- `websocket_jwt_auth(secret=None, query_param="token", scheme_name="WebSocketJWTAuth", issuer=None, audience=None, leeway=0, auto_error=True, algorithms=None, public_key=None, jwks_url=None)`

### JWKS cache

- `JWKSCache(ttl_seconds=300, fetcher=None)`
- Method: `get(url)`

## 7. Middleware

Add middleware via `app.use(...)` or `app.use_asgi(...)`.

- `CORSMiddleware(allow_origins=None, allow_methods=None, allow_headers=None, expose_headers=None, allow_credentials=False, max_age=600, allow_origin_regex=None)`
- `GZipMiddleware(minimum_size=500)`
- `CompressionMiddleware(minimum_size=500, prefer=None, brotli_quality=5, gzip_level=6, deflate_level=6)`
- `RateLimitMiddleware(max_requests=60, window_seconds=60, key_fn=None, include_headers=True)`
- `ResponseCacheMiddleware(ttl_seconds=5, max_entries=512, methods=None, cache_statuses=None, key_fn=None, vary_headers=None)`
- `TrustedHostMiddleware(allowed_hosts)`
- `SessionMiddleware(secret_key, cookie_name="session", max_age=1209600, same_site="Lax", https_only=False, path="/", domain=None, http_only=True, partitioned=False, signer_salt="turbo.session", signer_digest="sha256", secret_key_fallbacks=None, backend=None, session_id_bytes=24)`
- `CSRFMiddleware(cookie_name="csrftoken", header_name="x-csrf-token", safe_methods=None, exempt_paths=None, use_session=True, session_key="csrf_token", same_site="Lax", https_only=False, path="/", domain=None)`
- `HTTPSRedirectMiddleware(redirect_status_code=307)`
- `ProxyHeadersMiddleware(trusted_hosts=None, trusted_cidrs=None, forwarded_proto_header="x-forwarded-proto", forwarded_for_header="x-forwarded-for", forwarded_host_header="x-forwarded-host")`
- `MemorySessionBackend()` with methods `get`, `set`, `delete`

## 8. Observability

- `RequestIDMiddleware(header_name="x-request-id", response_header_name="x-request-id", generator=None)`
- `StructuredLoggingMiddleware(hook)`
- `MetricsMiddleware(hooks)`
- `PrometheusMiddleware(endpoint="/metrics", duration_buckets=None, include_process_label=True, multiprocess_dir=None, aggregate_workers=True)`
- `TracingMiddleware(hook)`
- `OpenTelemetryTracingHook(tracer=None, name="turbo.request")`

Events:

- `LogEvent(scope_type, method, path, route, status_code, duration_ms, request_id, error=None)`
- `MetricEvent(scope_type, method, path, route, status_code, duration_ms, request_id)`

Helpers:

- `get_request_id()`
- `set_request_id(request_id)`

## 9. Testing Helpers

### `TestClient`

Synchronous test client for app-level testing.

Constructor:

```python
TestClient(app)
```

Request methods:

- `request(method, path, headers=None, params=None, json_body=None, data=None, content=None)`
- `get`, `post`, `put`, `patch`, `delete`, `head`, `options`
- `dependency_override(original, override)` context manager
- `dependency_overrides({original: override, ...})` context manager
- `override_scope(name="scope")` context manager

### `TestResponse`

- Fields: `status_code`, `headers`, `content`
- Method: `json()`

Example:

```python
from turbo import Turbo, TestClient

app = Turbo()

@app.get("/ping")
async def ping():
    return {"ok": True}

def test_ping():
    client = TestClient(app)
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
```

### `AsyncTestClient`

Async test client with lifespan support and HTTP/WebSocket helpers.

- Async context manager: `async with AsyncTestClient(app) as client: ...`
- HTTP methods: `request`, `get`, `post`, `put`, `patch`, `delete`, `head`, `options`
- WebSocket helper: `await client.websocket_connect(path, headers=None, params=None, subprotocols=None)`
- Dependency contexts: `dependency_override(...)`, `dependency_overrides(...)`, `override_scope(...)`

### `WebSocketTestSession`

Returned by `AsyncTestClient.websocket_connect(...)`.

- Send: `send_text`, `send_bytes`, `send_json`
- Receive: `receive`, `receive_text`, `receive_json`
- Lifecycle: `close`
- Properties: `accepted`, `closed`, `subprotocol`

## 10. Job Queue Primitives

### `InMemoryJobQueue`

In-process async queue for background jobs.

- Registration: `register(name, handler)`
- Worker lifecycle: `start(workers=1)`, `stop()`, `join(timeout=None)`
- Enqueue: `enqueue(name, payload=None, delay_seconds=None, run_at=None, retry=None, idempotency_key=None)`
- Inspection: `get_job(job_id)`

### Retry and records

- `RetryPolicy(max_retries=0, base_delay=1.0, backoff=2.0, max_delay=60.0, jitter=0.0)`
- `JobRecord(id, name, payload, run_at, status, attempts, result, error, idempotency_key, ...)`

### External queue adapters

- `CeleryQueueAdapter(celery_app)`
- `RQQueueAdapter(rq_queue)`
- `RedisQueueAdapter(redis_client, list_name="turbo:jobs")`
## 11. Extension and Integration APIs

### Extension helpers (`turbo.extensions`)

- `TurboExtension` protocol (`name`, `setup(app)`)
- `ExtensionRegistry(auth_providers, telemetry_exporters, cache_backends)`
- `register_extension_hook(app, hook)`
- `run_extension_hooks(app, event, **kwargs)`
- `setup_extension(extension, app)`

### Integration bridges (`turbo.integrations`)

- SQLAlchemy:
  - `create_sqlalchemy_engine(url, **kwargs)`
  - `register_sqlalchemy(app, url, ...)`
  - `make_sqlalchemy_session_dependency(...)`
- Auth starter helpers:
  - `AuthContext`
  - `build_bearer_guard(...)`
  - `build_scope_guard(auth_dep, required_scopes=[...])`
- Pagination/filtering:
  - `PageParams`
  - `parse_pagination(...)`
  - `apply_pagination(items, params)`
  - `apply_sorting(items, sort=..., order=...)`
  - `apply_filters(items, filters)`
- Settings bridge:
  - `load_pydantic_settings(SettingsCls, **kwargs)`
  - `settings_dependency(SettingsCls, cache=True, **kwargs)`

## 12. GitHub Pages Publishing

Use `/docs` as the Pages source:

1. Push docs changes to GitHub.
2. In your repository settings, set **Pages** source to branch `main` (or `master`) and folder `/docs`.
3. Use `docs/index.md` as landing page and link all sections from there.
