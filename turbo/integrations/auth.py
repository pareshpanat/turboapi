from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..deps import Depends, Header
from ..errors import HTTPError


@dataclass(slots=True)
class AuthContext:
    subject: str
    scopes: set[str]
    raw_token: str
    claims: dict[str, Any]


def build_bearer_guard(*, header_name: str = "authorization", token_parser=None):
    parser = token_parser or (lambda token: {"sub": "anonymous", "scope": ""})

    async def _dep(authorization: str = Header(alias=header_name)):
        parts = authorization.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise HTTPError(401, "Invalid bearer token")
        token = parts[1].strip()
        claims = parser(token)
        if hasattr(claims, "__await__"):
            claims = await claims
        if not isinstance(claims, dict):
            raise HTTPError(401, "Invalid token payload")
        subject = str(claims.get("sub", ""))
        scope_raw = claims.get("scope")
        if isinstance(scope_raw, str):
            scopes = {s for s in scope_raw.split() if s}
        else:
            scopes = set(str(s) for s in (claims.get("scopes") or []))
        return AuthContext(subject=subject, scopes=scopes, raw_token=token, claims=claims)

    return Depends(_dep)


def build_scope_guard(auth_dep: Depends, *, required_scopes: Optional[list[str]] = None):
    required = set(required_scopes or [])

    async def _dep(ctx=Depends(auth_dep.call)):
        have = set(getattr(ctx, "scopes", set()))
        missing = sorted(required - have)
        if missing:
            raise HTTPError(403, "Insufficient scope", {"missing": missing, "required": sorted(required)})
        return ctx

    return Depends(_dep)
