# TurboAPI Security Recipes

## API key authentication
- Use `api_key_auth("X-API-Key")`
- Inject with `Depends(...)`
- Fail closed on missing key

## Bearer token auth
- Use `bearer_auth()`
- Validate token format and enforce `Authorization: Bearer ...`

## JWT auth (HS256/JWKS)
- Use `jwt_auth(...)` for token validation
- Enforce issuer/audience/expiry
- Prefer key rotation + `kid` for asymmetric verification paths

## OAuth2 scopes
- Use `oauth2_bearer(...)`
- Use `oauth2_authorization_code(...)` for user-agent/browser login flows
- Use `oauth2_client_credentials(...)` for service-to-service flows
- Enforce scopes via `Security(dep, scopes=[...])`
- Keep scope mapping in OpenAPI for client generation

## Session cookies
- Use `SessionMiddleware(secret_key=...)`
- Set `https_only=True` in production
- Use `SameSite` and bounded `max_age`

## CSRF protection
- Use `CSRFMiddleware(...)` together with `SessionMiddleware(...)`
- Expose token from a safe endpoint (for example `GET /csrf`) using `req.csrf_token`
- Send token back on unsafe methods in `X-CSRF-Token`
- Optional route-level checks: `Depends(csrf_protect())`

## Host and transport hardening
- Use `TrustedHostMiddleware([...])`
- Terminate TLS at edge and pass secure headers safely

## WebSocket auth patterns
- Token in query/header via dependency
- Fail with policy close (`1008`) for unauthorized connections
- Use subprotocol allow-lists where relevant

## Recommended defaults
- Keep request size limits strict
- Keep multipart file/part/field limits explicit
- Log security decisions with request IDs
