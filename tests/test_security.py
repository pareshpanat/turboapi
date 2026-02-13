import asyncio
import base64
import hashlib
import hmac
import json
import time
from turbo import (
    Turbo,
    Depends,
    Security,
    api_key_auth,
    bearer_auth,
    jwt_auth,
    oauth2_bearer,
    oauth2_authorization_code,
    oauth2_client_credentials,
)

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def make_hs256_jwt(payload: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    message = f"{h}.{p}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"

async def run_http(app, method="GET", path="/", headers=None):
    sent = []
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }
    events = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)
    status = next(x for x in sent if x["type"] == "http.response.start")["status"]
    body = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    return status, json.loads(body.decode("utf-8"))

def test_api_key_dep_and_openapi_security():
    app = Turbo()
    key_dep = api_key_auth("X-API-Key")

    @app.get("/secure-key")
    async def secure_key(api_key=Depends(key_dep)):
        return {"ok": bool(api_key)}

    status, body = asyncio.run(run_http(app, path="/secure-key"))
    assert status == 401
    assert body["error"] == "Missing API key"

    status, body = asyncio.run(run_http(app, path="/secure-key", headers=[(b"x-api-key", b"secret")]))
    assert status == 200
    assert body["ok"] is True

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    op = body["paths"]["/secure-key"]["get"]
    assert op["security"] == [{"ApiKeyAuth": []}]
    assert body["components"]["securitySchemes"]["ApiKeyAuth"]["type"] == "apiKey"

def test_bearer_dep_and_openapi_security():
    app = Turbo()
    token_dep = bearer_auth()

    @app.get("/secure-bearer")
    async def secure_bearer(token=Depends(token_dep)):
        return {"token": token}

    status, body = asyncio.run(run_http(app, path="/secure-bearer", headers=[(b"authorization", b"Bearer abc")]))
    assert status == 200
    assert body["token"] == "abc"

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    assert body["paths"]["/secure-bearer"]["get"]["security"] == [{"BearerAuth": []}]
    assert body["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"

def test_jwt_dep_validates_signature_and_claims():
    app = Turbo()
    dep = jwt_auth("s3cr3t", issuer="turbo")

    @app.get("/secure-jwt")
    async def secure_jwt(payload=Depends(dep)):
        return {"sub": payload.get("sub")}

    good = make_hs256_jwt({"sub": "u1", "iss": "turbo", "exp": int(time.time()) + 60}, "s3cr3t")
    bad = make_hs256_jwt({"sub": "u1", "iss": "wrong", "exp": int(time.time()) + 60}, "s3cr3t")

    status, body = asyncio.run(run_http(app, path="/secure-jwt", headers=[(b"authorization", f"Bearer {good}".encode("utf-8"))]))
    assert status == 200
    assert body["sub"] == "u1"

    status, body = asyncio.run(run_http(app, path="/secure-jwt", headers=[(b"authorization", f"Bearer {bad}".encode("utf-8"))]))
    assert status == 401
    assert body["error"] == "Invalid JWT issuer"

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    assert body["paths"]["/secure-jwt"]["get"]["security"] == [{"JWTAuth": []}]
    assert body["components"]["securitySchemes"]["JWTAuth"]["bearerFormat"] == "JWT"

def test_oauth2_scopes_enforced_and_documented():
    app = Turbo()
    oauth = oauth2_bearer("/token", scopes={"read:items": "Read items"}, secret="s3cr3t")

    @app.get("/items")
    async def items(payload=Security(oauth, scopes=["read:items"])):
        return {"sub": payload.get("sub")}

    good = make_hs256_jwt({"sub": "u1", "scope": "read:items", "exp": int(time.time()) + 60}, "s3cr3t")
    bad = make_hs256_jwt({"sub": "u1", "scope": "write:items", "exp": int(time.time()) + 60}, "s3cr3t")

    status, body = asyncio.run(run_http(app, path="/items", headers=[(b"authorization", f"Bearer {good}".encode("utf-8"))]))
    assert status == 200
    assert body["sub"] == "u1"

    status, body = asyncio.run(run_http(app, path="/items", headers=[(b"authorization", f"Bearer {bad}".encode("utf-8"))]))
    assert status == 403
    assert body["error"] == "Insufficient scope"

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    assert body["paths"]["/items"]["get"]["security"] == [{"OAuth2": ["read:items"]}]
    assert body["components"]["securitySchemes"]["OAuth2"]["type"] == "oauth2"

def test_oauth2_authorization_code_flow_documented_and_enforced():
    app = Turbo()
    oauth = oauth2_authorization_code(
        "https://idp.example.com/oauth/authorize",
        "https://idp.example.com/oauth/token",
        refresh_url="https://idp.example.com/oauth/refresh",
        scopes={"items:read": "Read items"},
        secret="s3cr3t",
    )

    @app.get("/items-authcode")
    async def items_authcode(payload=Security(oauth, scopes=["items:read"])):
        return {"sub": payload.get("sub")}

    good = make_hs256_jwt({"sub": "u1", "scope": "items:read", "exp": int(time.time()) + 60}, "s3cr3t")
    bad = make_hs256_jwt({"sub": "u1", "scope": "items:write", "exp": int(time.time()) + 60}, "s3cr3t")

    status, body = asyncio.run(run_http(app, path="/items-authcode", headers=[(b"authorization", f"Bearer {good}".encode("utf-8"))]))
    assert status == 200
    assert body["sub"] == "u1"

    status, body = asyncio.run(run_http(app, path="/items-authcode", headers=[(b"authorization", f"Bearer {bad}".encode("utf-8"))]))
    assert status == 403
    assert body["error"] == "Insufficient scope"

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    op = body["paths"]["/items-authcode"]["get"]
    assert op["security"] == [{"OAuth2AuthorizationCode": ["items:read"]}]
    scheme = body["components"]["securitySchemes"]["OAuth2AuthorizationCode"]
    flow = scheme["flows"]["authorizationCode"]
    assert flow["authorizationUrl"] == "https://idp.example.com/oauth/authorize"
    assert flow["tokenUrl"] == "https://idp.example.com/oauth/token"
    assert flow["refreshUrl"] == "https://idp.example.com/oauth/refresh"

def test_oauth2_client_credentials_flow_documented_and_enforced():
    app = Turbo()
    oauth = oauth2_client_credentials(
        "https://idp.example.com/oauth/token",
        scopes={"metrics:write": "Write metrics"},
        secret="s3cr3t",
    )

    @app.post("/machine")
    async def machine(payload=Security(oauth, scopes=["metrics:write"])):
        return {"client_id": payload.get("sub")}

    good = make_hs256_jwt({"sub": "svc-1", "scope": "metrics:write", "exp": int(time.time()) + 60}, "s3cr3t")
    bad = make_hs256_jwt({"sub": "svc-1", "scope": "metrics:read", "exp": int(time.time()) + 60}, "s3cr3t")

    status, body = asyncio.run(run_http(app, method="POST", path="/machine", headers=[(b"authorization", f"Bearer {good}".encode("utf-8"))]))
    assert status == 200
    assert body["client_id"] == "svc-1"

    status, body = asyncio.run(run_http(app, method="POST", path="/machine", headers=[(b"authorization", f"Bearer {bad}".encode("utf-8"))]))
    assert status == 403
    assert body["error"] == "Insufficient scope"

    status, body = asyncio.run(run_http(app, path="/openapi.json"))
    assert status == 200
    op = body["paths"]["/machine"]["post"]
    assert op["security"] == [{"OAuth2ClientCredentials": ["metrics:write"]}]
    scheme = body["components"]["securitySchemes"]["OAuth2ClientCredentials"]
    flow = scheme["flows"]["clientCredentials"]
    assert flow["tokenUrl"] == "https://idp.example.com/oauth/token"
    assert flow["scopes"] == {"metrics:write": "Write metrics"}


def test_jwt_invalid_numeric_claim_rejected():
    app = Turbo()
    dep = jwt_auth("s3cr3t")

    @app.get("/secure-jwt-claim")
    async def secure_jwt_claim(payload=Depends(dep)):
        return {"sub": payload.get("sub")}

    bad = make_hs256_jwt({"sub": "u1", "exp": "not-a-number"}, "s3cr3t")
    status, body = asyncio.run(run_http(app, path="/secure-jwt-claim", headers=[(b"authorization", f"Bearer {bad}".encode("utf-8"))]))
    assert status == 401
    assert body["error"] == "Invalid JWT exp claim"


def test_bearer_header_too_large_rejected():
    app = Turbo()
    dep = bearer_auth()

    @app.get("/secure-bearer-big")
    async def secure_bearer_big(token=Depends(dep)):
        return {"token": token}

    huge = "Bearer " + ("a" * 9000)
    status, body = asyncio.run(run_http(app, path="/secure-bearer-big", headers=[(b"authorization", huge.encode("utf-8"))]))
    assert status == 401
    assert body["error"] == "Authorization header too large"
