import asyncio
import json

from turbo import (
    ConnectionManager,
    Model,
    Turbo,
    WebSocket,
    normalize_ws_close_code,
    ws_close_reason,
)


async def run_http_events(app, method="GET", path="/", headers=None, body=b""):
    sent = []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }
    events = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)
    return sent


def _body_from_events(events):
    return b"".join(x.get("body", b"") for x in events if x["type"] == "http.response.body")


def test_websocket_subprotocol_negotiation_helper():
    app = Turbo()
    sent = []

    @app.websocket("/ws", subprotocols=["chat", "json"])
    async def ws_route(ws: WebSocket):
        chosen = ws.select_subprotocol(["json", "chat"])
        await ws.accept(subprotocol=chosen)
        await ws.send_text(chosen or "none")
        await ws.close()

    events = [{"type": "websocket.connect"}]

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "websocket",
        "path": "/ws",
        "query_string": b"",
        "headers": [(b"sec-websocket-protocol", b"chat, other")],
    }
    asyncio.run(app(scope, receive, send))
    accept = next(x for x in sent if x.get("type") == "websocket.accept")
    assert accept["subprotocol"] == "chat"


def test_websocket_idle_timeout_and_close_helpers():
    sent = []
    recv_events = [{"type": "websocket.connect"}]

    async def receive():
        if recv_events:
            return recv_events.pop(0)
        await asyncio.sleep(0.05)
        return {"type": "websocket.connect"}

    async def send(msg):
        sent.append(msg)

    ws = WebSocket({"type": "websocket", "path": "/ws", "query_string": b"", "headers": []}, receive, send)
    raised = False
    try:
        asyncio.run(ws.receive_with_idle_timeout(0.01, close_code=1008, reason="Idle"))
    except TimeoutError:
        raised = True
    assert raised is True
    close_msg = next(x for x in sent if x.get("type") == "websocket.close")
    assert close_msg["code"] == 1008
    assert close_msg["reason"] == "Idle"
    assert normalize_ws_close_code(9999) == 1000
    assert ws_close_reason(1008) == "Policy Violation"


def test_connection_manager_groups_and_broadcast():
    manager = ConnectionManager()
    out1, out2, out3 = [], [], []

    async def noop_receive():
        return {"type": "websocket.disconnect", "code": 1000}

    async def send1(msg):
        out1.append(msg)

    async def send2(msg):
        out2.append(msg)

    async def send3(msg):
        out3.append(msg)

    ws1 = WebSocket({"type": "websocket", "path": "/ws", "query_string": b"", "headers": []}, noop_receive, send1)
    ws2 = WebSocket({"type": "websocket", "path": "/ws", "query_string": b"", "headers": []}, noop_receive, send2)
    ws3 = WebSocket({"type": "websocket", "path": "/ws", "query_string": b"", "headers": []}, noop_receive, send3)
    manager.add(ws1, groups=["room-a"])
    manager.add(ws2, groups=["room-a", "room-b"])
    manager.add(ws3, groups=["room-b"])

    asyncio.run(manager.broadcast_text("hello-a", group="room-a"))
    asyncio.run(manager.broadcast_json({"hello": "b"}, group="room-b", exclude=ws3))

    assert any(m.get("type") == "websocket.send" and m.get("text") == "hello-a" for m in out1)
    assert any(m.get("type") == "websocket.send" and m.get("text") == "hello-a" for m in out2)
    assert not any(m.get("type") == "websocket.send" and m.get("text") == "hello-a" for m in out3)
    assert any(m.get("type") == "websocket.send" and m.get("text") == '{"hello":"b"}' for m in out2)
    assert not any(m.get("type") == "websocket.send" and m.get("text") == '{"hello":"b"}' for m in out3)


def test_openapi_route_metadata_extensions_and_transform():
    app = Turbo()

    class ItemIn(Model):
        name: str

    class ItemOut(Model):
        id: int
        name: str

    callbacks = {
        "onStatus": {
            "{$request.body#/callback_url}": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"200": {"description": "Received"}},
                }
            }
        }
    }
    webhooks = {
        "item.created": {
            "post": {
                "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                "responses": {"200": {"description": "ok"}},
            }
        }
    }

    @app.post(
        "/items",
        response_model=ItemOut,
        deprecated=True,
        callbacks=callbacks,
        webhooks=webhooks,
        examples={"request": {"sample": {"value": {"name": "book"}}}, "responses": {"200": {"ok": {"value": {"id": 1, "name": "book"}}}}},
        response_description="Item result",
        openapi_extra={"x-operation-tier": "gold"},
        responses={202: {"description": "Accepted", "headers": {"x-job-id": {"schema": {"type": "string"}}}}},
    )
    async def create_item(item: ItemIn):
        return {"id": 1, "name": item["name"]}

    @app.websocket("/ws-meta", subprotocols=["chat.v1"], openapi_extra={"x-ws-kind": "chat"})
    async def ws_meta(ws: WebSocket):
        await ws.accept()
        await ws.close()

    app.set_openapi_extension("x-company", {"team": "turbo"})

    @app.openapi_transform
    def transform_schema(doc):
        doc["info"]["x-transformed"] = True
        return doc

    events = asyncio.run(run_http_events(app, path="/openapi.json"))
    doc = json.loads(_body_from_events(events).decode("utf-8"))
    op = doc["paths"]["/items"]["post"]
    assert op["deprecated"] is True
    assert op["callbacks"] == callbacks
    assert op["x-operation-tier"] == "gold"
    assert op["responses"]["200"]["description"] == "Item result"
    assert "examples" in op["requestBody"]["content"]["application/json"]
    assert "x-job-id" in op["responses"]["202"]["headers"]
    assert "item.created" in doc["webhooks"]
    assert doc["x-company"]["team"] == "turbo"
    assert doc["info"]["x-transformed"] is True
    assert "x-turbo-websockets" in doc
    assert "x-turbo-websocket-conventions" in doc
    ws_op = next(x for x in doc["x-turbo-websockets"] if x["path"] == "/ws-meta")
    assert ws_op["subprotocols"] == ["chat.v1"]
    assert ws_op["x-ws-kind"] == "chat"
