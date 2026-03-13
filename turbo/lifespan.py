from __future__ import annotations

from typing import Any, TypeVar

from .deps import Depends
from .errors import HTTPError
from .request import Request

T = TypeVar("T")
_MISSING = object()


def get_app_state(req: Request, name: str, *, default: Any = _MISSING, expected_type: type[T] | None = None) -> T:
    value = req.app.state.get(name, _MISSING)
    if value is _MISSING:
        if default is _MISSING:
            raise KeyError(name)
        value = default
    if expected_type is not None and not isinstance(value, expected_type):
        raise TypeError(f"app.state.{name} is not {expected_type.__name__}")
    return value  # type: ignore[return-value]


def app_state_dependency(name: str, *, default: Any = _MISSING, required: bool = True, expected_type: type[T] | None = None):
    async def _dep(req: Request):
        value = req.app.state.get(name, _MISSING)
        if value is _MISSING:
            if default is not _MISSING:
                value = default
            elif required:
                raise HTTPError(500, "Missing app state", {"name": name})
            else:
                return None
        if expected_type is not None and value is not None and not isinstance(value, expected_type):
            raise HTTPError(500, "Invalid app state type", {"name": name, "expected": expected_type.__name__})
        return value

    return Depends(_dep)
