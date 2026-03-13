# py-turbo-api

TurboAPI is a small, fast ASGI web framework built from scratch (stdlib-only) with a focus on predictable performance.

## Features
- Trie router with `{param}` path params
- `APIRouter` composition with prefixes/tags via `include_router()`
- Compiled route plans (signature inspection happens at route registration, not per request)
- Dependency injection with `Depends()` including nested and yield-based dependencies
- Parameter source markers: `Query`, `Header`, `Cookie`, `Form`, `File`
- Built-in validation with `Model` + `field()` (supports `Annotated`, `Literal`, `Enum`, `datetime`, `UUID`, `Decimal`)
- Optional Pydantic v2 compatibility (request/response models + OpenAPI schema integration when installed)
- Multipart/form-data parsing with `UploadFile`
- Multipart hardening: per-file/part/field limits + upload spooling to disk
- `StreamingResponse`, `FileResponse`, `BackgroundTask`, and static file mounting
- Response primitives: `RedirectResponse`, `NegotiatedResponse`, cache header helpers, and `EventSourceResponse` for SSE
- File hardening: `ETag`/`If-None-Match` + byte range (`206`/`416`) support
- WebSocket route support via `@app.websocket(...)`
- Sub-app mounting via `app.mount("/prefix", sub_app)` and host routing via `app.mount_host("*.example.com", app)`
- WebSocket auth helpers (`websocket_token_auth`, `websocket_jwt_auth`) and OpenAPI WS extension docs (`x-turbo-websockets`)
- Security primitives: `api_key_auth()`, `bearer_auth()`, `jwt_auth()`, OAuth2 password/auth-code/client-credentials helpers with OpenAPI security schemas
- Session + CSRF protection primitives (`SessionMiddleware`, `CSRFMiddleware`, CSRF helpers)
- Testing utilities: sync `TestClient` and async `AsyncTestClient` with WebSocket test sessions
- OpenAPI at `/openapi.json`
- Swagger UI at `/docs` and ReDoc at `/redoc`
- Lifespan startup/shutdown handlers
- Lifespan state resources with cleanup guarantees (`app.add_state_resource(...)`)
- App-state dependency helpers (`app.state_dependency(...)`, `app_state_dependency(...)`, `get_app_state(...)`)
- Custom exception handlers with `@app.exception_handler(...)`
- Reliability defaults: request timeout, max body size, max concurrency
- Runtime settings model (`TurboSettings`) + `Turbo.from_env()` for env-driven deploy config
- Graceful shutdown request draining (`shutdown_drain_timeout`)
- Background job primitives: `InMemoryJobQueue` with retries, delay/schedule, and idempotency keys
- Queue adapters: `CeleryQueueAdapter`, `RQQueueAdapter`, `RedisQueueAdapter`
- CI release gates: Python matrix tests/lint + package build and `twine check`, with trusted publishing workflow
- Benchmark suite + perf regression gates in CI (`benchmarks/bench_runtime.py`)

## Stability
- Compatibility policy: `API_COMPATIBILITY.md`
- Current status: pre-1.0 (public API is stabilizing toward `v1.0.0`)

## Install

### From PyPI
```bash
pip install py-turbo-api
```

### Optional: Pydantic v2 compatibility
```bash
pip install "py-turbo-api[pydantic]"
```

PyPI: https://pypi.org/project/py-turbo-api/

## Run example
```bash
uvicorn app:app --reload
```

Open:
- http://127.0.0.1:8000/docs
- http://127.0.0.1:8000/openapi.json

## Documentation Tracks
- Docs home (GitHub Pages entry): `docs/index.md`
- Complete API reference: `docs/api-reference.md`
- Tutorial: `docs/tutorial.md`
- Advanced: `docs/advanced.md`
- Deployment: `docs/deployment.md`
- Security recipes: `docs/security-recipes.md`
- Why TurboAPI + benchmark method: `docs/why-turboapi.md`

## Benchmarks
```bash
python benchmarks/bench_runtime.py --baseline benchmarks/baseline.json --tolerance 1.20 --gate
```

## License
Apache-2.0 (see LICENSE).
