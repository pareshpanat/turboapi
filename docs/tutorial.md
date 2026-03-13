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
from turbo import APIRouter, Depends, Query, Header

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

# App-level dependency (runs on every route)
async def mark_request(req):
    req.app.state.total_requests = int(req.app.state.get("total_requests", 0)) + 1
    req.state.request_number = req.app.state.total_requests

app = Turbo(dependencies=[Depends(mark_request)])

# Router-level + include-time dependencies
router = APIRouter(prefix="/v1")
@router.get("/items", dependencies=[Depends(require_api_version)])
async def list_items():
    return {"ok": True}
app.include_router(router, dependencies=[Depends(require_api_version)])
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

## 6. Lifespan state resources and job queue

```python
from turbo import InMemoryJobQueue, RetryPolicy, app_state_dependency

jobs = InMemoryJobQueue()

async def start_jobs(app):
    jobs.register("email.send", lambda payload: {"to": payload["to"]})
    await jobs.start(workers=1)
    return jobs

async def stop_jobs(queue, app):
    await queue.stop()

app.add_state_resource("jobs", start_jobs, cleanup=stop_jobs)

@app.post("/jobs/email")
async def queue_email(jobs=app_state_dependency("jobs", expected_type=InMemoryJobQueue)):
    job_id = await jobs.enqueue(
        "email.send",
        {"to": "dev@example.com"},
        delay_seconds=0.2,
        retry=RetryPolicy(max_retries=2, base_delay=0.1),
        idempotency_key="email:dev@example.com",
    )
    return {"job_id": job_id}
```

## 7. WebSocket endpoint

```python
from turbo import WebSocket

@app.websocket("/ws")
async def ws_echo(ws: WebSocket):
    await ws.accept()
    while True:
        msg = await ws.receive_text()
        await ws.send_text(f"echo:{msg}")
```

## 8. Testing with `TestClient` and `AsyncTestClient`

```python
from turbo import AsyncTestClient, TestClient
from app import app
import asyncio

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

def test_ws_async():
    async def scenario():
        async with AsyncTestClient(app) as c:
            ws = await c.websocket_connect("/ws")
            await ws.send_text("hi")
            assert (await ws.receive_text()) == "echo:hi"
            await ws.close()
    asyncio.run(scenario())
```

Run tests:

```bash
pytest -q
```

## 9. Optional: Pydantic v2 models

If installed, you can use `pydantic.BaseModel` for request and response models.

```bash
pip install "py-turbo-api[pydantic]"
```

```python
from pydantic import BaseModel

class TodoIn(BaseModel):
    title: str

class TodoOut(BaseModel):
    id: int
    title: str
```

## 10. Next docs

- Full symbol-level API: `api-reference.md`
- Security patterns: `security-recipes.md`
- Deployment checklist: `deployment.md`

