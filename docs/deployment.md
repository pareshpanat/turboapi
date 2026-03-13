# TurboAPI Deployment

## Runtime baseline
- Python 3.11+
- ASGI server: `uvicorn` (or equivalent)
- Reverse proxy (Nginx/Traefik/Cloud LB) recommended

## Production startup
- Use multiple workers based on CPU and latency profile
- Set explicit timeout/body/concurrency limits in `Turbo(...)`
- Enable health checks via lightweight route (`/ping`)

## Container guidance
- Keep image minimal and pinned
- Install app with wheel/sdist artifact in CI
- Run as non-root
- Expose only required port

## Security deployment
- Enable `TrustedHostMiddleware`
- Enforce HTTPS at edge
- Use secure cookie settings in `SessionMiddleware`
- Rotate JWT keys and secrets

## Observability deployment
- Add `RequestIDMiddleware`
- Configure structured log sink via hook
- Enable metrics hooks and optional Prometheus endpoint
- Wire tracing middleware to existing telemetry backend

## CI/CD release flow
- CI gates: lint/test/build/twine check
- Tag release: `vX.Y.Z`
- Publish via trusted publishing workflow
- Auto-create draft changelog and release notes from merged PR labels
