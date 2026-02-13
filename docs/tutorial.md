# TurboAPI Tutorial

This track is the quickest path from `pip install` to a production-ready CRUD API.

## 1. First app
- Create `app.py`
- Add `Turbo()` app
- Add one `@app.get("/ping")` route
- Run with `uvicorn app:app --reload`

## 2. Path/query/body params
- Path params via `/users/{user_id}`
- Query params via default values or `Query(...)`
- JSON body parsing via typed `Model` inputs

## 3. Validation models
- Define `Model` classes for request/response
- Use `field(...)` constraints for common rules
- Add `response_model=` for output contract

## 4. Dependencies
- Use `Depends(...)` for auth/services
- Compose nested dependencies
- Use yield-style dependencies for setup/cleanup

## 5. Errors and responses
- Raise `HTTPError(status, message, detail)`
- Register global handlers with `@app.exception_handler(...)`
- Return `JSONResponse`, `TextResponse`, `FileResponse`, `StreamingResponse`

## 6. OpenAPI and docs
- Open schema at `/openapi.json`
- Swagger UI `/docs`, ReDoc `/redoc`
- Add tags, summary, description to routes

## 7. Middleware and security basics
- Add CORS/GZip/TrustedHost/Session middleware
- Add auth primitives: API key, bearer, JWT, OAuth2 scope checks

## 8. Testing
- Build lightweight ASGI event tests for route behavior
- Add validation and security-path tests
- Run `pytest -q`
