import asyncio

from turbo import AsyncTestClient, Request, SessionMiddleware, Turbo, WebSocket


def test_async_testclient_lifespan_and_http_requests():
    async def scenario():
        app = Turbo()
        events = []

        @app.on_event("startup")
        async def startup():
            events.append("startup")

        @app.on_event("shutdown")
        async def shutdown():
            events.append("shutdown")

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        async with AsyncTestClient(app) as client:
            resp = await client.get("/ping")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
        assert events == ["startup", "shutdown"]

    asyncio.run(scenario())


def test_async_testclient_cookie_session_roundtrip():
    async def scenario():
        app = Turbo()
        app.use_asgi(SessionMiddleware(secret_key="dev-secret", cookie_name="sid"))

        @app.post("/login")
        async def login(req: Request):
            req.set_session_value("user", "alice")
            return {"ok": True}

        @app.get("/me")
        async def me(req: Request):
            return {"user": req.session.get("user")}

        async with AsyncTestClient(app) as client:
            assert (await client.post("/login")).status_code == 200
            me = await client.get("/me")
            assert me.status_code == 200
            assert me.json()["user"] == "alice"

    asyncio.run(scenario())


def test_async_testclient_websocket_api():
    async def scenario():
        app = Turbo()

        @app.websocket("/ws/{name}", subprotocols=["chat.v1"])
        async def ws_chat(ws: WebSocket, name: str):
            chosen = await ws.accept_subprotocol(["chat.v1"])
            msg = await ws.receive_json()
            await ws.send_json({"echo": f"{name}:{msg['v']}", "subprotocol": chosen})
            await ws.close(1000)

        async with AsyncTestClient(app) as client:
            ws = await client.websocket_connect("/ws/alice", subprotocols=["chat.v1"])
            await ws.send_json({"v": "hi"})
            out = await ws.receive_json()
            assert out["echo"] == "alice:hi"
            assert out["subprotocol"] == "chat.v1"
            await ws.close()

    asyncio.run(scenario())
