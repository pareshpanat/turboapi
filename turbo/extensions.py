from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class TurboExtension(Protocol):
    name: str

    def setup(self, app: Any):
        ...


@dataclass(slots=True)
class ExtensionRegistry:
    auth_providers: dict[str, Any]
    telemetry_exporters: dict[str, Any]
    cache_backends: dict[str, Any]


def register_extension_hook(app: Any, hook: Callable[..., Any]):
    if not hasattr(app, "_extension_hooks"):
        app._extension_hooks = []
    app._extension_hooks.append(hook)
    return hook


def run_extension_hooks(app: Any, event: str, **kwargs):
    hooks = getattr(app, "_extension_hooks", [])
    for hook in hooks:
        hook(event=event, app=app, **kwargs)


def setup_extension(extension: Any, app: Any):
    if hasattr(extension, "setup"):
        return extension.setup(app)
    if callable(extension):
        return extension(app)
    raise TypeError("Extension must be callable(app) or provide setup(app)")
