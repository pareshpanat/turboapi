# TurboAPI Advanced

## Routing and architecture
- Split domains with `APIRouter`
- Compose apps via `include_router(...)`
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

## Security
- API key / Bearer / JWT / OAuth2 dependencies
- WS token/JWT helpers
- Trusted host and session middleware

## OpenAPI customization
- Route-level metadata: `deprecated`, `callbacks`, `webhooks`, `examples`, `openapi_extra`
- App-level schema hooks:
  - `app.set_openapi_extension("x-...", value)`
  - `@app.openapi_transform`
- WS docs extensions:
  - `x-turbo-websockets`
  - `x-turbo-websocket-conventions`

## Observability
- Request IDs and context propagation
- Structured logging hook middleware
- Metrics hook middleware + Prometheus endpoint
- Tracing hooks and OpenTelemetry-compatible adapter
