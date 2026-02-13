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
