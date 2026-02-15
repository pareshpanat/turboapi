# TurboAPI Tutorial

This is the shortest production-ready path from `pip install py-turbo-api` to a validated, tested API.

## 1. Install and run

```bash
pip install py-turbo-api uvicorn
```

Create `app.py`:

```python
from turbo import Turbo

app = Turbo(title="Turbo Tutorial", version="1.0.0")

@app.get("/ping")
async def ping():
    return {"ok": True}
```

Start server:

```bash
uvicorn app:app --reload
```

Open:

- `http://127.0.0.1:8000/ping`
- `http://127.0.0.1:8000/openapi.json`
- `http://127.0.0.1:8000/docs`

## 2. Add models and CRUD routes

```python
from turbo import Turbo, Model, field, HTTPError

app = Turbo(title="Todo API", version="1.0.0")
db: dict[int, dict] = {}

class TodoIn(Model):
    title: str = field(min_len=1, max_len=200)

class TodoOut(Model):
    id: int
    title: str

@app.post("/todos", response_model=TodoOut, status_code=201, tags=["todos"])
async def create_todo(body: TodoIn):
    todo_id = (max(db.keys()) + 1) if db else 1
    row = {"id": todo_id, "title": body.title}
    db[todo_id] = row
    return row

@app.get("/todos/{todo_id:int}", response_model=TodoOut, tags=["todos"])
async def get_todo(todo_id: int):
    row = db.get(todo_id)
    if not row:
        raise HTTPError(404, "Todo not found", {"todo_id": todo_id})
    return row
```

## 3. Query, header, and dependency injection

```python
from turbo import Depends, Query, Header

def require_api_version(x_api_version: str = Header(alias="x-api-version")):
    if x_api_version != "2026-01":
        raise HTTPError(400, "Invalid x-api-version")
    return x_api_version

@app.get("/search", tags=["search"])
async def search(
    q: str = Query(required=True),
    version: str = Depends(require_api_version),
):
    return {"q": q, "api_version": version}
```

## 4. Security (API key example)

```python
from turbo import api_key_auth, Depends

api_key = api_key_auth("x-api-key")

@app.get("/private")
async def private(_: str = Depends(api_key)):
    return {"ok": True}
```

## 5. Middleware and runtime settings

```python
from turbo import CORSMiddleware, GZipMiddleware, TrustedHostMiddleware

app.use(TrustedHostMiddleware(["localhost", "127.0.0.1"]))
app.use(CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]))
app.use(GZipMiddleware(minimum_size=500))
```

You can tune runtime limits in `Turbo(...)`:

- `request_timeout`
- `max_body_bytes`
- `max_concurrency`
- multipart limits (`multipart_max_fields`, `multipart_max_file_size`, `multipart_spool_threshold`, `multipart_max_part_size`)

## 6. WebSocket endpoint

```python
from turbo import WebSocket

@app.websocket("/ws")
async def ws_echo(ws: WebSocket):
    await ws.accept()
    while True:
        msg = await ws.receive_text()
        await ws.send_text(f"echo:{msg}")
```

## 7. Testing with `TestClient`

```python
from turbo import TestClient
from app import app

def test_ping():
    c = TestClient(app)
    r = c.get("/ping")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

def test_create_todo():
    c = TestClient(app)
    r = c.post("/todos", json_body={"title": "write docs"})
    assert r.status_code == 201
    assert r.json()["title"] == "write docs"
```

Run tests:

```bash
pytest -q
```

## 8. Next docs

- Full symbol-level API: `api-reference.md`
- Security patterns: `security-recipes.md`
- Deployment checklist: `deployment.md`

