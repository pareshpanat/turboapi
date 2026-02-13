from __future__ import annotations
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.request import urlopen
from .errors import HTTPError
from .request import Request

MAX_AUTH_HEADER_BYTES = 8192

try:
    import jwt as _pyjwt
except Exception:  # pragma: no cover - optional dependency
    _pyjwt = None

def _mark_security(dep, *, scheme_name: str, scheme: dict[str, Any], scopes: Optional[list[str]] = None):
    dep.__turbo_security_requirement__ = {scheme_name: list(scopes or [])}
    dep.__turbo_security_scheme__ = (scheme_name, scheme)
    return dep

def _extract_bearer_token(req: Request, *, auto_error: bool) -> Optional[str]:
    header = req.headers.get("authorization", "")
    if len(header.encode("utf-8", "ignore")) > MAX_AUTH_HEADER_BYTES:
        raise HTTPError(401, "Authorization header too large")
    if not header:
        if auto_error:
            raise HTTPError(401, "Missing Authorization header")
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPError(401, "Invalid Authorization header")
    return parts[1].strip()

def api_key_auth(header_name: str = "x-api-key", *, scheme_name: str = "ApiKeyAuth", auto_error: bool = True):
    header_key = header_name.lower()
    async def _dep(req: Request):
        val = req.headers.get(header_key)
        if not val:
            if auto_error:
                raise HTTPError(401, "Missing API key", {"header": header_name})
            return None
        return val
    return _mark_security(
        _dep,
        scheme_name=scheme_name,
        scheme={"type": "apiKey", "in": "header", "name": header_name},
    )

def bearer_auth(*, scheme_name: str = "BearerAuth", bearer_format: Optional[str] = None, auto_error: bool = True):
    async def _dep(req: Request):
        return _extract_bearer_token(req, auto_error=auto_error)
    scheme = {"type": "http", "scheme": "bearer"}
    if bearer_format:
        scheme["bearerFormat"] = bearer_format
    return _mark_security(_dep, scheme_name=scheme_name, scheme=scheme)

def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    raw = (value + pad).encode("ascii", "strict")
    return base64.b64decode(raw, altchars=b"-_", validate=True)

def _claim_int(payload: dict[str, Any], name: str) -> Optional[int]:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPError(401, f"Invalid JWT {name} claim")
    try:
        return int(value)
    except Exception as exc:
        raise HTTPError(401, f"Invalid JWT {name} claim") from exc

def _decode_jwt_hs256(token: str, *, secret: str, issuer: Optional[str], audience: Optional[str], leeway: int) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPError(401, "Invalid JWT format")
    head_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(head_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        raise HTTPError(401, "Invalid JWT payload")
    if header.get("alg") != "HS256":
        raise HTTPError(401, "Unsupported JWT algorithm")
    signing_input = f"{head_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    given = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected, given):
        raise HTTPError(401, "Invalid JWT signature")

    _validate_registered_claims(payload, issuer=issuer, audience=audience, leeway=leeway)
    return payload

def _validate_registered_claims(payload: dict[str, Any], *, issuer: Optional[str], audience: Optional[str], leeway: int):
    now = int(time.time())
    exp = _claim_int(payload, "exp")
    if exp is not None and exp < now - int(leeway):
        raise HTTPError(401, "JWT expired")
    nbf = _claim_int(payload, "nbf")
    if nbf is not None and nbf > now + int(leeway):
        raise HTTPError(401, "JWT not active yet")
    iat = _claim_int(payload, "iat")
    if iat is not None and iat > now + int(leeway):
        raise HTTPError(401, "JWT issued-at is in the future")
    if issuer is not None and payload.get("iss") != issuer:
        raise HTTPError(401, "Invalid JWT issuer")
    if audience is not None:
        aud = payload.get("aud")
        if isinstance(aud, list):
            if any(not isinstance(item, str) for item in aud):
                raise HTTPError(401, "Invalid JWT audience")
            if audience not in aud:
                raise HTTPError(401, "Invalid JWT audience")
        elif isinstance(aud, str):
            if aud != audience:
                raise HTTPError(401, "Invalid JWT audience")
        else:
            raise HTTPError(401, "Invalid JWT audience")

@dataclass
class JWKSCache:
    ttl_seconds: int = 300
    fetcher: Optional[Callable[[str], dict[str, Any]]] = None
    _cache_by_url: dict[str, tuple[float, dict[str, Any]]] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self._cache_by_url is None:
            self._cache_by_url = {}

    def get(self, url: str) -> dict[str, Any]:
        now = time.time()
        cached = self._cache_by_url.get(url)
        if cached and (now - cached[0]) < self.ttl_seconds:
            return cached[1]
        data = self._fetch(url)
        self._cache_by_url[url] = (now, data)
        return data

    def _fetch(self, url: str) -> dict[str, Any]:
        if self.fetcher is not None:
            return self.fetcher(url)
        with urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

def _decode_jwt_with_pyjwt(token: str, *, algorithms: list[str], secret: Optional[str], public_key: Optional[str], jwks_url: Optional[str], jwks_cache: Optional[JWKSCache], issuer: Optional[str], audience: Optional[str], leeway: int):
    if _pyjwt is None:
        raise HTTPError(500, "PyJWT is required for RS256/ES256/JWKS verification")
    options = {"verify_signature": True}
    kwargs: dict[str, Any] = {"algorithms": algorithms, "options": options, "leeway": leeway}
    if issuer is not None:
        kwargs["issuer"] = issuer
    if audience is not None:
        kwargs["audience"] = audience

    if jwks_url:
        header = _pyjwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise HTTPError(401, "Missing kid in JWT header")
        cache = jwks_cache or JWKSCache()
        jwks = cache.get(jwks_url)
        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                key = _pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
                break
        if key is None:
            raise HTTPError(401, "No matching JWK for kid")
        return _pyjwt.decode(token, key=key, **kwargs)

    key = secret if secret is not None else public_key
    if key is None:
        raise HTTPError(401, "JWT key material missing")
    return _pyjwt.decode(token, key=key, **kwargs)

def jwt_auth(
    secret: Optional[str] = None,
    *,
    scheme_name: str = "JWTAuth",
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
    leeway: int = 0,
    auto_error: bool = True,
    algorithms: Optional[list[str]] = None,
    public_key: Optional[str] = None,
    jwks_url: Optional[str] = None,
    jwks_cache: Optional[JWKSCache] = None,
):
    algorithms = list(algorithms or ["HS256"])
    if "HS256" in algorithms and not secret and not (public_key or jwks_url):
        raise ValueError("secret is required for HS256")
    async def _dep(req: Request):
        token = _extract_bearer_token(req, auto_error=auto_error)
        if token is None:
            return None
        try:
            if algorithms == ["HS256"] and secret and not (public_key or jwks_url):
                return _decode_jwt_hs256(token, secret=secret, issuer=issuer, audience=audience, leeway=leeway)
            return _decode_jwt_with_pyjwt(
                token,
                algorithms=algorithms,
                secret=secret,
                public_key=public_key,
                jwks_url=jwks_url,
                jwks_cache=jwks_cache,
                issuer=issuer,
                audience=audience,
                leeway=leeway,
            )
        except HTTPError:
            raise
        except Exception:
            raise HTTPError(401, "Invalid JWT token")
    return _mark_security(
        _dep,
        scheme_name=scheme_name,
        scheme={"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
    )

def oauth2_bearer(
    token_url: str,
    *,
    scheme_name: str = "OAuth2",
    scopes: Optional[dict[str, str]] = None,
    secret: Optional[str] = None,
    public_key: Optional[str] = None,
    jwks_url: Optional[str] = None,
    algorithms: Optional[list[str]] = None,
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
    leeway: int = 0,
    auto_error: bool = True,
):
    defined_scopes = dict(scopes or {})
    verify = jwt_auth(
        secret=secret,
        scheme_name=scheme_name,
        issuer=issuer,
        audience=audience,
        leeway=leeway,
        auto_error=auto_error,
        algorithms=algorithms,
        public_key=public_key,
        jwks_url=jwks_url,
    )
    async def _dep(req: Request):
        return await verify(req)
    return _mark_security(
        _dep,
        scheme_name=scheme_name,
        scheme={
            "type": "oauth2",
            "flows": {
                "password": {
                    "tokenUrl": token_url,
                    "scopes": defined_scopes,
                }
            },
        },
    )

def oauth2_authorization_code(
    authorization_url: str,
    token_url: str,
    *,
    refresh_url: Optional[str] = None,
    scheme_name: str = "OAuth2AuthorizationCode",
    scopes: Optional[dict[str, str]] = None,
    secret: Optional[str] = None,
    public_key: Optional[str] = None,
    jwks_url: Optional[str] = None,
    algorithms: Optional[list[str]] = None,
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
    leeway: int = 0,
    auto_error: bool = True,
):
    defined_scopes = dict(scopes or {})
    verify = jwt_auth(
        secret=secret,
        scheme_name=scheme_name,
        issuer=issuer,
        audience=audience,
        leeway=leeway,
        auto_error=auto_error,
        algorithms=algorithms,
        public_key=public_key,
        jwks_url=jwks_url,
    )
    async def _dep(req: Request):
        return await verify(req)
    flow: dict[str, Any] = {
        "authorizationUrl": authorization_url,
        "tokenUrl": token_url,
        "scopes": defined_scopes,
    }
    if refresh_url:
        flow["refreshUrl"] = refresh_url
    return _mark_security(
        _dep,
        scheme_name=scheme_name,
        scheme={"type": "oauth2", "flows": {"authorizationCode": flow}},
    )

def oauth2_client_credentials(
    token_url: str,
    *,
    refresh_url: Optional[str] = None,
    scheme_name: str = "OAuth2ClientCredentials",
    scopes: Optional[dict[str, str]] = None,
    secret: Optional[str] = None,
    public_key: Optional[str] = None,
    jwks_url: Optional[str] = None,
    algorithms: Optional[list[str]] = None,
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
    leeway: int = 0,
    auto_error: bool = True,
):
    defined_scopes = dict(scopes or {})
    verify = jwt_auth(
        secret=secret,
        scheme_name=scheme_name,
        issuer=issuer,
        audience=audience,
        leeway=leeway,
        auto_error=auto_error,
        algorithms=algorithms,
        public_key=public_key,
        jwks_url=jwks_url,
    )
    async def _dep(req: Request):
        return await verify(req)
    flow: dict[str, Any] = {
        "tokenUrl": token_url,
        "scopes": defined_scopes,
    }
    if refresh_url:
        flow["refreshUrl"] = refresh_url
    return _mark_security(
        _dep,
        scheme_name=scheme_name,
        scheme={"type": "oauth2", "flows": {"clientCredentials": flow}},
    )

def csrf_token(req: Request, *, auto_error: bool = True) -> Optional[str]:
    token = req.scope.get("turbo.csrf_token")
    if isinstance(token, str) and token:
        return token
    if auto_error:
        raise HTTPError(500, "CSRF middleware is not configured")
    return None

def csrf_protect(
    *,
    header_name: str = "x-csrf-token",
    auto_error: bool = True,
):
    key = header_name.lower()
    async def _dep(req: Request):
        expected = csrf_token(req, auto_error=auto_error)
        if expected is None:
            return None
        provided = req.headers.get(key, "").strip()
        if not provided:
            if auto_error:
                raise HTTPError(403, "CSRF token missing", {"header": header_name})
            return None
        if not hmac.compare_digest(provided, expected):
            if auto_error:
                raise HTTPError(403, "CSRF token invalid")
            return None
        return expected
    return _dep

def websocket_token_auth(query_param: str = "token", *, scheme_name: str = "WebSocketTokenAuth", auto_error: bool = True):
    async def _dep(req: Request):
        token = req.query_params.get(query_param)
        if token:
            return token
        if auto_error:
            raise HTTPError(401, "Missing websocket token", {"query": query_param})
        return None
    return _mark_security(
        _dep,
        scheme_name=scheme_name,
        scheme={"type": "apiKey", "in": "query", "name": query_param},
    )

def websocket_jwt_auth(
    secret: Optional[str] = None,
    *,
    query_param: str = "token",
    scheme_name: str = "WebSocketJWTAuth",
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
    leeway: int = 0,
    auto_error: bool = True,
    algorithms: Optional[list[str]] = None,
    public_key: Optional[str] = None,
    jwks_url: Optional[str] = None,
):
    token_dep = websocket_token_auth(query_param=query_param, scheme_name=scheme_name, auto_error=auto_error)
    async def _dep(req: Request):
        token = await token_dep(req)
        if token is None:
            return None
        if list(algorithms or ["HS256"]) == ["HS256"] and secret and not (public_key or jwks_url):
            return _decode_jwt_hs256(token, secret=secret, issuer=issuer, audience=audience, leeway=leeway)
        try:
            return _decode_jwt_with_pyjwt(
                token,
                algorithms=list(algorithms or ["HS256"]),
                secret=secret,
                public_key=public_key,
                jwks_url=jwks_url,
                jwks_cache=None,
                issuer=issuer,
                audience=audience,
                leeway=leeway,
            )
        except HTTPError:
            raise
        except Exception:
            raise HTTPError(401, "Invalid JWT token")
    return _mark_security(
        _dep,
        scheme_name=scheme_name,
        scheme={"type": "apiKey", "in": "query", "name": query_param},
    )
