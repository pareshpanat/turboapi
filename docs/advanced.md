# TurboAPI Advanced

## Routing and architecture
- Split domains with `APIRouter`
- Compose apps via `include_router(...)`
- Dependency scopes: app-level `Turbo(..., dependencies=[...])`, router-level `APIRouter(..., dependencies=[...])`, route-level `@app.get(..., dependencies=[...])`, and include-time `include_router(..., dependencies=[...])`
- Mount sub-apps with `app.mount("/v2", other_app)`
- Use host-based dispatch with `app.mount_host("*.example.com", app)`

## Data handling
- Multipart form/file parsing with limits
- Upload spooling and hardening controls
- Custom JSON encoders (`app.json_encoder(...)`)

## Runtime features
- Streaming responses and background tasks
- Conditional/range file serving
- WebSocket routes with dependency injection
- ConnectionManager for groups and broadcast
- Request/app mutable state via `request.state` and `app.state`
- Lifespan resource registration with cleanup (`app.add_state_resource(...)`)
- App state dependency helpers (`app.state_dependency(...)`, `app_state_dependency(...)`)

## Security
- API key / Bearer / JWT / OAuth2 dependencies
- WS token/JWT helpers
- Trusted host and session middleware

## OpenAPI customization
- Route-level metadata: `deprecated`, `callbacks`, `webhooks`, `examples`, `openapi_extra`
- App-level schema hooks:
  - `app.set_openapi_extension("x-...", value)`
  - `@app.openapi_transform`
- Optional Pydantic v2 request/response + schema integration when installed (`pip install "py-turbo-api[pydantic]"`)
- WS docs extensions:
  - `x-turbo-websockets`
  - `x-turbo-websocket-conventions`

## Observability
- Request IDs and context propagation
- Structured logging hook middleware
- Metrics hook middleware + Prometheus endpoint
- Tracing hooks and OpenTelemetry-compatible adapter

## Testing workflows
- Sync test surface: `TestClient`
- Async test surface: `AsyncTestClient` for async HTTP tests
- WebSocket tests: `await client.websocket_connect(...)` returning `WebSocketTestSession` with send/receive helpers

## Background jobs
- `InMemoryJobQueue` for in-process async workers
- Retry and backoff policy via `RetryPolicy`
- Delay/schedule support (`delay_seconds` / `run_at`)
- Idempotency keys on enqueue
- Adapter hooks for Celery/RQ/Redis queue bridges
