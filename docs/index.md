# TurboAPI Documentation

`py-turbo-api` API docs and usage guide for GitHub Pages publishing from `/docs`.

## Start Here

- [Complete API Reference](api-reference.md)
- [Tutorial](tutorial.md)
- [Advanced](advanced.md)
- [Security Recipes](security-recipes.md)
- [Deployment](deployment.md)
- [Why TurboAPI](why-turboapi.md)

## Quick Install

```bash
pip install py-turbo-api
```

## Quick Example

```python
from turbo import Turbo

app = Turbo(title="Example API", version="1.0.0")

@app.get("/ping")
async def ping():
    return {"ok": True}
```

## Built-in Docs Endpoints

- OpenAPI JSON: `/openapi.json`
- Swagger UI: `/docs`
- ReDoc: `/redoc`
