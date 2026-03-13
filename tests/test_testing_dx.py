from turbo import Depends, Request, SessionMiddleware, TestClient, Turbo


def test_testclient_lifespan_and_basic_requests():
    app = Turbo()
    state = []

    @app.on_event("startup")
    async def startup():
        state.append("startup")

    @app.on_event("shutdown")
    async def shutdown():
        state.append("shutdown")

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    with TestClient(app) as client:
        resp = client.get("/ping")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
    assert state == ["startup", "shutdown"]


def test_testclient_dependency_override_context():
    app = Turbo()

    async def get_user():
        return "real"

    @app.get("/me")
    async def me(user=Depends(get_user)):
        return {"user": user}

    with TestClient(app) as client:
        assert client.get("/me").json()["user"] == "real"
        with client.dependency_override(get_user, lambda: "override"):
            assert client.get("/me").json()["user"] == "override"
        assert client.get("/me").json()["user"] == "real"


def test_testclient_cookie_and_session_roundtrip():
    app = Turbo()
    app.use_asgi(SessionMiddleware(secret_key="dev-secret", cookie_name="sid"))

    @app.post("/login")
    async def login(req: Request):
        req.set_session_value("user", "alice")
        return {"ok": True}

    @app.get("/me")
    async def me(req: Request):
        return {"user": req.session.get("user")}

    with TestClient(app) as client:
        assert client.post("/login").status_code == 200
        me = client.get("/me")
        assert me.status_code == 200
        assert me.json()["user"] == "alice"


def test_request_and_app_state():
    app = Turbo()
    app.state.counter = 0
    app.state.label = "turbo"

    @app.get("/state")
    async def state(req: Request):
        had_local = hasattr(req.state, "local")
        req.state.local = "only-this-request"
        req.app.state.counter += 1
        return {
            "label": req.app.state.label,
            "counter": req.app.state.counter,
            "had_local": had_local,
            "local": req.state.local,
        }

    with TestClient(app) as client:
        r1 = client.get("/state")
        assert r1.status_code == 200
        b1 = r1.json()
        assert b1["label"] == "turbo"
        assert b1["counter"] == 1
        assert b1["had_local"] is False
        assert b1["local"] == "only-this-request"

        r2 = client.get("/state")
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["counter"] == 2
        assert b2["had_local"] is False
