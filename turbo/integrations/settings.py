from __future__ import annotations

from functools import lru_cache
from typing import Any

from ..deps import Depends


def load_pydantic_settings(settings_cls: type[Any], **kwargs):
    try:
        from pydantic_settings import BaseSettings  # type: ignore

        if isinstance(settings_cls, type) and issubclass(settings_cls, BaseSettings):
            return settings_cls(**kwargs)
    except Exception:
        pass
    return settings_cls(**kwargs)


def settings_dependency(settings_cls: type[Any], *, cache: bool = True, **kwargs):
    if cache:
        @lru_cache(maxsize=1)
        def _build():
            return load_pydantic_settings(settings_cls, **kwargs)

        def _dep():
            return _build()

        return Depends(_dep, cache=True)

    def _dep():
        return load_pydantic_settings(settings_cls, **kwargs)

    return Depends(_dep, cache=False)
