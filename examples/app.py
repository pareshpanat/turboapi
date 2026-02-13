from turbo import (
    APIRouter,
    BackgroundTask,
    Body,
    ConnectionManager,
    CORSMiddleware,
    CSRFMiddleware,
    Depends,
    EventSourceResponse,
    GZipMiddleware,
    HTTPSRedirectMiddleware,
    HTTPError,
    Header,
    Host,
    MemorySessionBackend,
    MetricsMiddleware,
    Model,
    NegotiatedResponse,
    PrometheusMiddleware,
    ProxyHeadersMiddleware,
    Query,
    RedirectResponse,
    Request,
    RequestIDMiddleware,
    SSEEvent,
    Security,
    SessionMiddleware,
    StreamingResponse,
    StructuredLoggingMiddleware,
    TrustedHostMiddleware,
    Turbo,
    WebSocket,
    api_key_auth,
    bearer_auth,
    build_cache_control,
    field,
    field_validator,
    jwt_auth,
    model_validator,
    oauth2_authorization_code,
    oauth2_bearer,
    oauth2_client_credentials,
    websocket_token_auth,
    with_cache_headers,
)


# -------------------------------
# App setup and global dependencies
# -------------------------------
async def allow_docs(req: Request):
    # Optional docs auth gate. Return False to block /docs and /schema.
    return req.headers.get("x-docs-token") == "demo-docs"


app = Turbo(
    title="TurboAPI Demo",
    version="1.0.0",
    redirect_slashes=True,
    redirect_status_code=308,
    openapi_url="/schema",
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_js_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js",
    swagger_css_url="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css",
    redoc_js_url="https://unpkg.com/redoc@2/bundles/redoc.standalone.js",
    # docs_auth=allow_docs,  # enable when you want gated docs
    operation_id_strategy="method_path",
)

api = APIRouter(prefix="/api", tags=["demo"])
ws_manager = ConnectionManager()
session_backend = MemorySessionBackend()
metrics_events = []


# Security dependency instances reused by multiple routes.
api_key_dep = api_key_auth("X-API-Key")
bearer_dep = bearer_auth()
jwt_dep = jwt_auth("dev-secret", issuer="turbo-demo")
oauth_dep = oauth2_bearer("/token", scopes={"read:items": "Read items"}, secret="dev-secret")
oauth_authcode_dep = oauth2_authorization_code(
    "https://idp.example.com/oauth/authorize",
    "https://idp.example.com/oauth/token",
    refresh_url="https://idp.example.com/oauth/refresh",
    scopes={"read:profile": "Read profile"},
    secret="dev-secret",
)
oauth_client_dep = oauth2_client_credentials(
    "https://idp.example.com/oauth/token",
    scopes={"write:metrics": "Write metrics"},
    secret="dev-secret",
)
ws_token_dep = websocket_token_auth("token")


# -------------------------------
# Model and helper definitions
# -------------------------------
async def get_user_agent(req: Request):
    return req.headers.get("user-agent", "unknown")


class UserIn(Model):
    # Demonstrates aliasing + populate-by-name for incoming payloads.
    model_config = {"populate_by_name": True, "schema_by_alias": True}
    name: str = field(min_len=2, max_len=40, alias="fullName")
    age: int = field(ge=0, le=130)

    @field_validator("name", mode="before")
    def strip_name(cls, value):
        return str(value).strip()

    @model_validator(mode="after")
    def validate_business_rules(cls, data):
        if data["name"].lower() == "admin":
            raise ValueError("reserved name")
        return data


class UserOut(Model):
    id: int
    name: str
    age: int


class Address(Model):
    city: str
    country: str


# -------------------------------
# Middleware stack
# -------------------------------
app.use_asgi(RequestIDMiddleware())
app.use_asgi(ProxyHeadersMiddleware())
app.use_asgi(
    StructuredLoggingMiddleware(
        lambda e: print(
            f"[{e.scope_type}] {e.method} {e.route} -> {e.status_code} ({e.duration_ms:.2f}ms rid={e.request_id})"
        )
    )
)
app.use_asgi(MetricsMiddleware([lambda e: metrics_events.append((e.route, e.status_code, round(e.duration_ms, 2)))]))
app.use_asgi(PrometheusMiddleware(endpoint="/metrics"))
app.use_asgi(
    CORSMiddleware(
        allow_origins=["https://localhost:3000"],
        allow_origin_regex=r"https://.*\.example\.com",
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
)
app.use_asgi(GZipMiddleware(minimum_size=200))
app.use_asgi(TrustedHostMiddleware(["127.0.0.1", "localhost"]))
app.use_asgi(
    SessionMiddleware(
        secret_key="dev-session-secret",
        secret_key_fallbacks=["dev-session-secret-old"],
        cookie_name="turbo_session",
        backend=session_backend,
        same_site="None",
        https_only=True,
        partitioned=True,
    )
)
app.use_asgi(CSRFMiddleware(cookie_name="turbo_csrf", same_site="None", https_only=True))

ENABLE_HTTPS_REDIRECT = False
if ENABLE_HTTPS_REDIRECT:
    app.use_asgi(HTTPSRedirectMiddleware())


# -------------------------------
# OpenAPI customization
# -------------------------------
app.set_openapi_extension("x-demo", {"name": "TurboAPI example", "version": "latest"})


@app.openapi_transform
def tweak_openapi(doc):
    doc["info"]["description"] = "Demo app showing latest TurboAPI features."
    doc["info"]["x-docs-auth-header"] = "x-docs-token: demo-docs"
    return doc


@app.exception_handler(ValueError)
async def value_error_handler(req: Request, exc: ValueError):
    return {"error": str(exc), "kind": "value_error"}


# -------------------------------
# Core health and protocol surface
# -------------------------------
@app.get("/ping", summary="Health check")
async def ping():
    return {"ok": True}


@app.head("/ping-head")
async def ping_head():
    return {"ok": True}


@app.options("/ping-options")
async def ping_options():
    return {"ok": True}


@app.get("/moved")
async def moved():
    # Simple redirect primitive.
    return RedirectResponse("/ping", status=302)


# -------------------------------
# Request context and session/CSRF flows
# -------------------------------
@app.get("/whoami", summary="Request context")
async def whoami(req: Request):
    return {"request_id": req.request_id, "session": req.session, "csrf_token": req.csrf_token}


@app.post("/session/login")
async def session_login(req: Request):
    req.set_session_value("user", "demo-user")
    return {"ok": True, "session": req.session}


@app.post("/session/logout")
async def session_logout(req: Request):
    req.clear_session()
    return {"ok": True}


# -------------------------------
# Response patterns and content negotiation
# -------------------------------
@app.get("/negotiate")
async def negotiate(req: Request):
    # Returns plain text or JSON based on Accept header.
    return NegotiatedResponse(
        req.headers.get("accept", ""),
        {
            "application/json": {"message": "json"},
            "text/plain; charset=utf-8": "plain",
        },
        default_media_type="application/json",
    )


@app.get("/stream")
async def stream():
    async def chunks():
        yield "hello "
        yield "stream"

    cache = build_cache_control(public=True, max_age=60)
    headers = with_cache_headers(cache_control=cache)
    return StreamingResponse(
        chunks(),
        media_type="text/plain",
        headers=headers,
        background=BackgroundTask(lambda: None),
    )


@app.get("/sse")
async def sse():
    # SSE stream with typed event and dict event payloads.
    async def events():
        yield SSEEvent(data={"status": "start"}, event="status", id="1")
        yield {"data": "ready", "event": "status"}

    return EventSourceResponse(events(), ping_interval=None)


# -------------------------------
# Non-JSON request body examples
# -------------------------------
@app.post("/plain-note")
async def plain_note(note: str = Body(media_type="text/plain")):
    return {"note": note}


@app.post("/binary-note")
async def binary_note(data: bytes = Body(media_type="application/octet-stream")):
    return {"size": len(data)}


# -------------------------------
# WebSocket functionality
# -------------------------------
@app.websocket("/ws/{name}", subprotocols=["chat.v1", "json.v1"])
async def chat(ws: WebSocket, name: str):
    # Picks the first matching subprotocol and keeps heartbeat alive.
    chosen = await ws.accept_subprotocol(["chat.v1", "json.v1"])
    heartbeat = ws.start_heartbeat(interval=10, idle_timeout=45)
    try:
        text = await ws.receive_text()
        await ws.send_text(f"{name}:{text} ({chosen or 'no-subprotocol'})")
    finally:
        heartbeat.cancel()
    await ws.close_with_reason(1000, "done")


@app.websocket("/ws-secure")
async def secure_chat(ws: WebSocket, token=Depends(ws_token_dep)):
    await ws.accept()
    await ws.send_text(f"token:{token}")
    await ws.close()


@app.websocket("/ws/room/{room}")
async def room_chat(ws: WebSocket, room: str):
    await ws_manager.connect(ws, groups=[room])
    try:
        msg = await ws.receive_text()
        await ws_manager.broadcast_text(f"[{room}] {msg}", group=room)
    finally:
        await ws_manager.disconnect(ws, code=1000, reason="room-exit")


# -------------------------------
# Router-level API examples
# -------------------------------
@api.get("/hello/{name}", summary="Hello endpoint")
async def hello(name: str, ua=Depends(get_user_agent)):
    return {"hello": name, "ua": ua}


@api.get("/search")
async def search(tags: list[str] = Query(), trace_id: str = Header(alias="x-trace-id", required=False)):
    return {"tags": tags, "trace_id": trace_id}


@api.get("/host")
async def host_view(host: str = Host()):
    return {"host": host}


@api.patch("/users/{user_id:int}", operation_id="patchUser")
async def patch_user(user_id: int):
    return {"id": user_id, "patched": True}


@api.get("/keys/{key:uuid}")
async def key_view(key: str):
    return {"key": key}


@api.get("/files/{file_path:path}")
async def file_path_view(file_path: str):
    return {"path": file_path}


@api.post(
    "/users",
    response_model=UserOut,
    response_description="Created user payload",
    examples={
        "request": {"ok": {"value": {"fullName": "Paresh", "age": 28}}},
        "responses": {"200": {"ok": {"value": {"id": 1, "name": "Paresh", "age": 28}}}},
    },
    responses={409: {"description": "User conflict"}},
    openapi_extra={"x-demo-operation": "create-user"},
)
async def create_user(user: UserIn):
    if user["age"] == 0:
        raise HTTPError(409, "User conflict")
    return {"id": 1, "name": user["name"], "age": user["age"]}


@api.post("/users/with-address")
async def create_user_with_address(user: UserIn, address: Address):
    return {"user": user, "address": address}


# -------------------------------
# Security-focused API endpoints
# -------------------------------
@api.get("/secure/key")
async def secure_with_api_key(api_key=Depends(api_key_dep)):
    return {"auth": "api_key", "value": api_key}


@api.get("/secure/bearer")
async def secure_with_bearer(token=Depends(bearer_dep)):
    return {"auth": "bearer", "token": token}


@api.get("/secure/jwt")
async def secure_with_jwt(payload=Depends(jwt_dep)):
    return {"auth": "jwt", "claims": payload}


@api.get("/secure/oauth")
async def secure_with_oauth(payload=Security(oauth_dep, scopes=["read:items"])):
    return {"auth": "oauth2", "claims": payload}


@api.get("/secure/oauth-authcode")
async def secure_with_oauth_authcode(payload=Security(oauth_authcode_dep, scopes=["read:profile"])):
    return {"auth": "oauth2_auth_code", "claims": payload}


@api.post("/secure/oauth-client")
async def secure_with_oauth_client(payload=Security(oauth_client_dep, scopes=["write:metrics"])):
    return {"auth": "oauth2_client_credentials", "claims": payload}


app.include_router(api)
