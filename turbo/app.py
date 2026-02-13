from __future__ import annotations
import asyncio
import inspect
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional
from .routing import Router
from .request import Request, WebSocket
from .response import Response, JSONResponse, TextResponse, HTMLResponse, FileResponse
from .errors import HTTPError
from .middleware import MiddlewareStack
from .utils import timeout, call_callable
from .deps import Depends, ParamSpec, compile_route_plan, resolve_dependencies
from dataclasses import is_dataclass
from .models import Model, compile_model_validator, compile_type_validator
from .openapi import build_openapi

Handler = Callable[..., Awaitable[Any]]

@dataclass(slots=True)
class RouteDef:
    method: str
    path: str
    handler: Handler
    name: Optional[str] = None
    operation_id: Optional[str] = None
    response_model: Optional[Any] = None
    status_code: Optional[int] = None
    include_in_schema: bool = True
    internal: bool = False
    tags: Optional[list[str]] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    responses: Optional[dict[Any, Any]] = None
    security: Optional[list[dict[str, list[str]]]] = None
    deprecated: bool = False
    callbacks: Optional[dict[str, Any]] = None
    webhooks: Optional[dict[str, Any]] = None
    examples: Optional[dict[str, Any]] = None
    response_description: Optional[str] = None
    openapi_extra: Optional[dict[str, Any]] = None
    subprotocols: Optional[list[str]] = None

@dataclass(slots=True)
class Route:
    method: str
    path: str
    handler: Handler
    name: str
    operation_id: str
    param_specs: list[ParamSpec]
    sig: inspect.Signature
    path_param_names: set[str]
    request_body_model: Optional[type[Model]] = None
    request_body_type: Optional[Any] = None
    request_body_param_name: Optional[str] = None
    request_body_param_names: set[str] | None = None
    validate_body_fn: Optional[Callable] = None
    response_model: Optional[Any] = None
    status_code: Optional[int] = None
    validate_response_fn: Optional[Callable] = None
    include_in_schema: bool = True
    internal: bool = False
    tags: list[str] | None = None
    summary: Optional[str] = None
    description: Optional[str] = None
    responses: dict[Any, Any] | None = None
    security: list[dict[str, list[str]]] | None = None
    security_schemes: dict[str, dict[str, Any]] | None = None
    deprecated: bool = False
    callbacks: dict[str, Any] | None = None
    webhooks: dict[str, Any] | None = None
    examples: dict[str, Any] | None = None
    response_description: Optional[str] = None
    openapi_extra: dict[str, Any] | None = None
    subprotocols: list[str] | None = None

class APIRouter:
    def __init__(self, *, prefix: str = "", tags: Optional[list[str]] = None):
        if prefix and not prefix.startswith("/"):
            raise ValueError("prefix must start with /")
        self.prefix = prefix.rstrip("/") if prefix not in ("", "/") else ""
        self.tags = list(tags or [])
        self._routes: list[RouteDef] = []

    def route(self, method: str, path: str, *, name: Optional[str] = None, operation_id: Optional[str] = None, response_model: Optional[Any] = None, status_code: Optional[int] = None, include_in_schema: bool = True, internal: bool = False, tags: Optional[list[str]] = None, summary: Optional[str] = None, description: Optional[str] = None, responses: Optional[dict[Any, Any]] = None, security: Optional[list[dict[str, list[str]]]] = None, deprecated: bool = False, callbacks: Optional[dict[str, Any]] = None, webhooks: Optional[dict[str, Any]] = None, examples: Optional[dict[str, Any]] = None, response_description: Optional[str] = None, openapi_extra: Optional[dict[str, Any]] = None):
        def deco(fn: Handler):
            merged_tags = list(dict.fromkeys([*self.tags, *(tags or [])]))
            self._routes.append(RouteDef(method=method.upper(), path=path, handler=fn, name=name, operation_id=operation_id, response_model=response_model, status_code=status_code, include_in_schema=include_in_schema, internal=internal, tags=merged_tags or None, summary=summary, description=description, responses=responses, security=security, deprecated=deprecated, callbacks=callbacks, webhooks=webhooks, examples=examples, response_description=response_description, openapi_extra=openapi_extra))
            return fn
        return deco

    def get(self, path: str, *, name: Optional[str] = None, operation_id: Optional[str] = None, response_model: Optional[Any] = None, status_code: Optional[int] = None, include_in_schema: bool = True, internal: bool = False, tags: Optional[list[str]] = None, summary: Optional[str] = None, description: Optional[str] = None, responses: Optional[dict[Any, Any]] = None, security: Optional[list[dict[str, list[str]]]] = None, deprecated: bool = False, callbacks: Optional[dict[str, Any]] = None, webhooks: Optional[dict[str, Any]] = None, examples: Optional[dict[str, Any]] = None, response_description: Optional[str] = None, openapi_extra: Optional[dict[str, Any]] = None):
        return self.route("GET", path, name=name, operation_id=operation_id, response_model=response_model, status_code=status_code, include_in_schema=include_in_schema, internal=internal, tags=tags, summary=summary, description=description, responses=responses, security=security, deprecated=deprecated, callbacks=callbacks, webhooks=webhooks, examples=examples, response_description=response_description, openapi_extra=openapi_extra)

    def post(self, path: str, *, name: Optional[str] = None, operation_id: Optional[str] = None, response_model: Optional[Any] = None, status_code: Optional[int] = None, include_in_schema: bool = True, internal: bool = False, tags: Optional[list[str]] = None, summary: Optional[str] = None, description: Optional[str] = None, responses: Optional[dict[Any, Any]] = None, security: Optional[list[dict[str, list[str]]]] = None, deprecated: bool = False, callbacks: Optional[dict[str, Any]] = None, webhooks: Optional[dict[str, Any]] = None, examples: Optional[dict[str, Any]] = None, response_description: Optional[str] = None, openapi_extra: Optional[dict[str, Any]] = None):
        return self.route("POST", path, name=name, operation_id=operation_id, response_model=response_model, status_code=status_code, include_in_schema=include_in_schema, internal=internal, tags=tags, summary=summary, description=description, responses=responses, security=security, deprecated=deprecated, callbacks=callbacks, webhooks=webhooks, examples=examples, response_description=response_description, openapi_extra=openapi_extra)

    def put(self, path: str, *, name: Optional[str] = None, operation_id: Optional[str] = None, response_model: Optional[Any] = None, status_code: Optional[int] = None, include_in_schema: bool = True, internal: bool = False, tags: Optional[list[str]] = None, summary: Optional[str] = None, description: Optional[str] = None, responses: Optional[dict[Any, Any]] = None, security: Optional[list[dict[str, list[str]]]] = None, deprecated: bool = False, callbacks: Optional[dict[str, Any]] = None, webhooks: Optional[dict[str, Any]] = None, examples: Optional[dict[str, Any]] = None, response_description: Optional[str] = None, openapi_extra: Optional[dict[str, Any]] = None):
        return self.route("PUT", path, name=name, operation_id=operation_id, response_model=response_model, status_code=status_code, include_in_schema=include_in_schema, internal=internal, tags=tags, summary=summary, description=description, responses=responses, security=security, deprecated=deprecated, callbacks=callbacks, webhooks=webhooks, examples=examples, response_description=response_description, openapi_extra=openapi_extra)

    def delete(self, path: str, *, name: Optional[str] = None, operation_id: Optional[str] = None, response_model: Optional[Any] = None, status_code: Optional[int] = None, include_in_schema: bool = True, internal: bool = False, tags: Optional[list[str]] = None, summary: Optional[str] = None, description: Optional[str] = None, responses: Optional[dict[Any, Any]] = None, security: Optional[list[dict[str, list[str]]]] = None, deprecated: bool = False, callbacks: Optional[dict[str, Any]] = None, webhooks: Optional[dict[str, Any]] = None, examples: Optional[dict[str, Any]] = None, response_description: Optional[str] = None, openapi_extra: Optional[dict[str, Any]] = None):
        return self.route("DELETE", path, name=name, operation_id=operation_id, response_model=response_model, status_code=status_code, include_in_schema=include_in_schema, internal=internal, tags=tags, summary=summary, description=description, responses=responses, security=security, deprecated=deprecated, callbacks=callbacks, webhooks=webhooks, examples=examples, response_description=response_description, openapi_extra=openapi_extra)

    def patch(self, path: str, **kwargs):
        return self.route("PATCH", path, **kwargs)

    def head(self, path: str, **kwargs):
        return self.route("HEAD", path, **kwargs)

    def options(self, path: str, **kwargs):
        return self.route("OPTIONS", path, **kwargs)

    def add_api_route(self, path: str, endpoint: Handler, *, methods: list[str], **kwargs):
        for method in methods:
            self.route(method, path, **kwargs)(endpoint)
        return endpoint

class Turbo:
    def __init__(self, *, request_timeout:float=10.0, max_body_bytes:int=1_000_000, max_concurrency:int=200, title:str="TurboAPI", version:str="0.1.0", multipart_max_fields:int=1000, multipart_max_file_size:int=10_000_000, multipart_spool_threshold:int=1_000_000, multipart_max_part_size:int=10_000_000, redirect_slashes: bool = True, redirect_status_code: int = 307, openapi_url: Optional[str] = "/openapi.json", docs_url: Optional[str] = "/docs", redoc_url: Optional[str] = "/redoc", swagger_js_url: str = "https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js", swagger_css_url: str = "https://unpkg.com/swagger-ui-dist@5/swagger-ui.css", redoc_js_url: str = "https://unpkg.com/redoc@2/bundles/redoc.standalone.js", docs_auth: Optional[Callable[..., Any]] = None, operation_id_strategy: str = "function", operation_id_generator: Optional[Callable[..., str]] = None, shutdown_drain_timeout: float = 10.0):
        self.router=Router()
        self.middleware=MiddlewareStack()
        self.request_timeout=request_timeout
        self.max_body_bytes=max_body_bytes
        self._sem=asyncio.Semaphore(max_concurrency)
        self._routes=[]
        self._route_by_handler={}
        self._openapi_cache=None
        self._title=title
        self._version=version
        self._startup_handlers: list[Callable[..., Any]] = []
        self._shutdown_handlers: list[Callable[..., Any]] = []
        self._exception_handlers: dict[type[BaseException], Callable[..., Any]] = {}
        self.dependency_overrides: dict[Callable[..., Any], Callable[..., Any]] = {}
        self._json_encoders: dict[type, Callable[[Any], Any]] = {}
        self._multipart_limits = {
            "max_fields": int(multipart_max_fields),
            "max_file_size": int(multipart_max_file_size),
            "spool_threshold": int(multipart_spool_threshold),
            "max_part_size": int(multipart_max_part_size),
        }
        self._static_mounts: list[tuple[str, str]] = []
        self._subapps: list[tuple[str, Any]] = []
        self._host_mounts: list[tuple[str, Any]] = []
        self.redirect_slashes = bool(redirect_slashes)
        self.redirect_status_code = int(redirect_status_code)
        self._openapi_url = openapi_url
        self._docs_url = docs_url
        self._redoc_url = redoc_url
        self._swagger_js_url = swagger_js_url
        self._swagger_css_url = swagger_css_url
        self._redoc_js_url = redoc_js_url
        self._docs_auth = docs_auth
        self._operation_id_strategy = operation_id_strategy
        self._operation_id_generator = operation_id_generator
        self._shutdown_drain_timeout = float(shutdown_drain_timeout)
        self._inflight_http = 0
        self._drain_event = asyncio.Event()
        self._drain_event.set()
        self._http_handler = self.middleware.build_http(self._final_handler)
        self._asgi_app = self.middleware.build_asgi(self._dispatch_asgi)
        self._openapi_transform: Optional[Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = None
        self._openapi_vendor_extensions: dict[str, Any] = {}
        self._install_openapi_and_docs()

    @classmethod
    def from_settings(cls, settings: Any):
        if hasattr(settings, "to_turbo_kwargs"):
            kwargs = settings.to_turbo_kwargs()
            return cls(**kwargs)
        raise TypeError("settings must provide to_turbo_kwargs()")

    @classmethod
    def from_env(cls, *, prefix: str = "TURBO_"):
        from .config import TurboSettings
        return cls.from_settings(TurboSettings.from_env(prefix=prefix))

    def _rebuild_execution_chains(self):
        self._http_handler = self.middleware.build_http(self._final_handler)
        self._asgi_app = self.middleware.build_asgi(self._dispatch_asgi)

    def _install_openapi_and_docs(self):
        if self._openapi_url:
            @self.get(self._openapi_url, internal=True, include_in_schema=False)
            async def _openapi(req: Request):
                await self._authorize_docs(req)
                if self._openapi_cache is None:
                    self._openapi_cache = self._build_openapi_document()
                return self._openapi_cache

        if self._docs_url:
            @self.get(self._docs_url, internal=True, include_in_schema=False)
            async def _docs(req: Request):
                await self._authorize_docs(req)
                spec_url = self._openapi_url or "/openapi.json"
                html=f'''<!doctype html><html><head><meta charset="utf-8"/><title>{self._title} - Docs</title>
<link rel="stylesheet" href="{self._swagger_css_url}"/></head>
<body><div id="swagger-ui"></div>
<script src="{self._swagger_js_url}"></script>
<script>window.onload=()=>{{SwaggerUIBundle({{url:"{spec_url}",dom_id:"#swagger-ui"}});}};</script>
</body></html>'''
                return HTMLResponse(html)

        if self._redoc_url:
            @self.get(self._redoc_url, internal=True, include_in_schema=False)
            async def _redoc(req: Request):
                await self._authorize_docs(req)
                spec_url = self._openapi_url or "/openapi.json"
                html=f'''<!doctype html><html><head><meta charset="utf-8"/><title>{self._title} - ReDoc</title></head>
<body><redoc spec-url="{spec_url}"></redoc><script src="{self._redoc_js_url}"></script></body></html>'''
                return HTMLResponse(html)

    async def _authorize_docs(self, req: Request):
        if self._docs_auth is None:
            return
        result = self._docs_auth(req)
        if inspect.isawaitable(result):
            result = await result
        if result is False:
            raise HTTPError(401, "Unauthorized docs access")

    def set_docs_auth(self, fn: Optional[Callable[..., Any]]):
        self._docs_auth = fn
        return fn

    def docs_auth(self, fn: Callable[..., Any]):
        return self.set_docs_auth(fn)

    def set_operation_id_generator(self, fn: Optional[Callable[..., str]], *, strategy: Optional[str] = None):
        self._operation_id_generator = fn
        if strategy is not None:
            self._operation_id_strategy = strategy
        self._openapi_cache = None
        return fn

    def _default_operation_id(self, method: str, path: str, fn_name: str):
        if self._operation_id_strategy == "method_path":
            safe = path.strip("/").replace("/", "_").replace("{", "").replace("}", "").replace(":", "_")
            safe = safe or "root"
            return f"{method.lower()}_{safe}"
        return fn_name

    def _make_operation_id(self, method: str, path: str, fn: Handler, explicit: Optional[str]):
        if explicit:
            return explicit
        if self._operation_id_generator is not None:
            try:
                return str(self._operation_id_generator(method=method, path=path, handler=fn))
            except TypeError:
                return str(self._operation_id_generator(method, path, fn))
        return self._default_operation_id(method, path, getattr(fn, "__name__", "operation"))

    def _build_openapi_document(self):
        doc = build_openapi(self, title=self._title, version=self._version)
        for name, value in self._openapi_vendor_extensions.items():
            doc[name] = value
        if self._openapi_transform is not None:
            transformed = self._openapi_transform(doc)
            if transformed is not None:
                doc = transformed
        return doc

    def set_openapi_transform(self, fn: Optional[Callable[[dict[str, Any]], Optional[dict[str, Any]]]]):
        self._openapi_transform = fn
        self._openapi_cache = None
        return fn

    def openapi_transform(self, fn: Callable[[dict[str, Any]], Optional[dict[str, Any]]]):
        return self.set_openapi_transform(fn)

    def set_openapi_extension(self, name: str, value: Any):
        if not name.startswith("x-"):
            raise ValueError("OpenAPI extension keys must start with 'x-'")
        self._openapi_vendor_extensions[name] = value
        self._openapi_cache = None

    def remove_openapi_extension(self, name: str):
        self._openapi_vendor_extensions.pop(name, None)
        self._openapi_cache = None

    def route(self, method:str, path:str, *, name:Optional[str]=None, operation_id: Optional[str] = None, response_model:Optional[Any]=None, status_code: Optional[int] = None, include_in_schema: bool = True, internal:bool=False, tags: Optional[list[str]] = None, summary: Optional[str] = None, description: Optional[str] = None, responses: Optional[dict[Any, Any]] = None, security: Optional[list[dict[str, list[str]]]] = None, deprecated: bool = False, callbacks: Optional[dict[str, Any]] = None, webhooks: Optional[dict[str, Any]] = None, examples: Optional[dict[str, Any]] = None, response_description: Optional[str] = None, openapi_extra: Optional[dict[str, Any]] = None):
        method=method.upper()
        def deco(fn:Handler):
            self.router.add(method, path, fn)
            specs,sig=compile_route_plan(fn, request_type=Request)
            path_params=_path_param_names(path)
            body_model = None
            request_body_type = None
            body_param_name = None
            body_param_names: set[str] | None = None
            validate_body_fn = None
            body_candidates=[p for p in specs if _is_body_candidate(p)]
            if body_candidates:
                compiled_body = []
                for candidate in body_candidates:
                    ann = candidate.annotation
                    validator = compile_model_validator(ann) if (isinstance(ann, type) and issubclass(ann, Model)) else compile_type_validator(ann)
                    alias = None
                    embed = None
                    media_type = None
                    if candidate.kind == "param" and candidate.param is not None:
                        alias = candidate.param.alias
                        embed = candidate.param.embed
                        media_type = candidate.param.media_type
                    required = (candidate.default is inspect._empty) if candidate.kind == "auto" else bool(candidate.param.required if candidate.param is not None else True)
                    compiled_body.append((candidate.name, alias, ann, validator, required, embed, media_type))
                if len(compiled_body) == 1:
                    _single = compiled_body[0]
                    request_body_type = _single[2]
                    body_param_name = _single[0]
                    if isinstance(request_body_type, type) and issubclass(request_body_type, Model):
                        body_model = request_body_type

                body_param_names = {x[0] for x in compiled_body}
                async def _vb(req:Request, receive):
                    primary_media = None
                    if len(compiled_body) == 1 and compiled_body[0][6]:
                        primary_media = compiled_body[0][6]
                    raw = await req.parse_payload(receive, media_type=primary_media)
                    out = {}
                    multi = len(compiled_body) > 1
                    for name, alias, ann, validator, required, embed, _media_type in compiled_body:
                        key = alias or name
                        use_embed = embed if embed is not None else multi
                        if use_embed:
                            if not isinstance(raw, dict):
                                raise HTTPError(422, "Validation Error", {"errors": [{"loc": ["body"], "msg": "Input should be an object", "type": "type_error.object"}]})
                            if key not in raw:
                                if required:
                                    raise HTTPError(422, "Validation Error", {"errors": [{"loc": ["body", key], "msg": "Field required", "type": "value_error.missing"}]})
                                out[name] = None
                                continue
                            value = raw.get(key)
                            loc_prefix = ("body", key)
                        else:
                            value = raw
                            loc_prefix = ("body",)
                        out[name] = validator(value, loc_prefix=loc_prefix)
                    return out

                validate_body_fn=_vb
            validate_resp=None
            if response_model is not None and isinstance(response_model,type) and issubclass(response_model,Model):
                rv=compile_model_validator(response_model)
                def _vr(obj:Any):
                    if obj is None or not isinstance(obj,dict):
                        raise HTTPError(500,"Response must be a JSON object for response_model")
                    return rv(obj)
                validate_resp=_vr
            inferred_security, security_schemes = _extract_security(specs)
            effective_security = security if security is not None else inferred_security
            r = Route(
                method=method,
                path=path,
                handler=fn,
                name=name or fn.__name__,
                operation_id=self._make_operation_id(method, path, fn, operation_id),
                param_specs=specs,
                sig=sig,
                path_param_names=path_params,
                request_body_model=body_model,
                request_body_type=request_body_type,
                request_body_param_name=body_param_name,
                request_body_param_names=body_param_names,
                validate_body_fn=validate_body_fn,
                response_model=response_model,
                status_code=status_code,
                validate_response_fn=validate_resp,
                include_in_schema=include_in_schema,
                internal=internal,
                tags=tags,
                summary=summary,
                description=description,
                responses=responses,
                security=effective_security,
                security_schemes=security_schemes,
                deprecated=deprecated,
                callbacks=callbacks,
                webhooks=webhooks,
                examples=examples,
                response_description=response_description,
                openapi_extra=openapi_extra,
                subprotocols=None,
            )
            self._routes.append(r)
            self._route_by_handler[fn] = r
            self._openapi_cache = None
            return fn
        return deco

    def get(self, path:str, **kwargs):
        return self.route("GET", path, **kwargs)

    def post(self, path:str, **kwargs):
        return self.route("POST", path, **kwargs)

    def put(self, path:str, **kwargs):
        return self.route("PUT", path, **kwargs)

    def delete(self, path:str, **kwargs):
        return self.route("DELETE", path, **kwargs)

    def patch(self, path:str, **kwargs):
        return self.route("PATCH", path, **kwargs)

    def head(self, path:str, **kwargs):
        return self.route("HEAD", path, **kwargs)

    def options(self, path:str, **kwargs):
        return self.route("OPTIONS", path, **kwargs)

    def add_api_route(self, path: str, endpoint: Handler, *, methods: list[str], **kwargs):
        for method in methods:
            self.route(method, path, **kwargs)(endpoint)
        return endpoint

    def websocket(self, path: str, *, name: Optional[str]=None, operation_id: Optional[str] = None, include_in_schema: bool = True, tags: Optional[list[str]] = None, summary: Optional[str] = None, description: Optional[str] = None, deprecated: bool = False, examples: Optional[dict[str, Any]] = None, openapi_extra: Optional[dict[str, Any]] = None, subprotocols: Optional[list[str]] = None):
        def deco(fn: Handler):
            self.router.add("WS", path, fn)
            specs,sig=compile_route_plan(fn, request_type=WebSocket)
            path_params=_path_param_names(path)
            inferred_security, security_schemes = _extract_security(specs)
            r = Route(
                method="WS",
                path=path,
                handler=fn,
                name=name or fn.__name__,
                operation_id=self._make_operation_id("WS", path, fn, operation_id),
                param_specs=specs,
                sig=sig,
                path_param_names=path_params,
                request_body_model=None,
                request_body_type=None,
                request_body_param_name=None,
                request_body_param_names=None,
                validate_body_fn=None,
                response_model=None,
                status_code=None,
                validate_response_fn=None,
                include_in_schema=include_in_schema,
                internal=False,
                tags=tags,
                summary=summary,
                description=description,
                responses=None,
                security=inferred_security,
                security_schemes=security_schemes,
                deprecated=deprecated,
                callbacks=None,
                webhooks=None,
                examples=examples,
                response_description=None,
                openapi_extra=openapi_extra,
                subprotocols=subprotocols,
            )
            self._routes.append(r)
            self._route_by_handler[fn] = r
            self._openapi_cache = None
            return fn
        return deco

    def use(self, mw):
        self.middleware.add(mw)
        self._rebuild_execution_chains()
        return mw

    def use_asgi(self, mw):
        self.middleware.add_asgi(mw)
        self._rebuild_execution_chains()
        return mw

    def json_encoder(self, tp: type, fn: Callable[[Any], Any]):
        self._json_encoders[tp] = fn
        return fn

    def mount_static(self, prefix: str, directory: str):
        if not prefix.startswith("/"):
            raise ValueError("static prefix must start with /")
        self._static_mounts.append((prefix.rstrip("/"), os.path.realpath(directory)))

    def mount(self, prefix: str, app: Any):
        if not prefix.startswith("/"):
            raise ValueError("mount prefix must start with /")
        self._subapps.append((prefix.rstrip("/"), app))

    def mount_host(self, host_pattern: str, app: Any):
        self._host_mounts.append((host_pattern.lower(), app))

    def include_router(self, router: APIRouter, *, prefix: str = "", tags: Optional[list[str]] = None):
        base_prefix = prefix.rstrip("/") if prefix not in ("", "/") else ""
        extra_tags = list(tags or [])
        for rd in router._routes:
            route_path = f"{router.prefix}{rd.path}"
            path = f"{base_prefix}{route_path}" or "/"
            merged_tags = list(dict.fromkeys([*(rd.tags or []), *extra_tags])) or None
            self.route(rd.method, path, name=rd.name, operation_id=rd.operation_id, response_model=rd.response_model, status_code=rd.status_code, include_in_schema=rd.include_in_schema, internal=rd.internal, tags=merged_tags, summary=rd.summary, description=rd.description, responses=rd.responses, security=rd.security, deprecated=rd.deprecated, callbacks=rd.callbacks, webhooks=rd.webhooks, examples=rd.examples, response_description=rd.response_description, openapi_extra=rd.openapi_extra)(rd.handler)

    def on_event(self, event: str):
        if event not in ("startup", "shutdown"):
            raise ValueError("event must be 'startup' or 'shutdown'")
        target = self._startup_handlers if event == "startup" else self._shutdown_handlers
        def deco(fn: Callable[..., Any]):
            target.append(fn)
            return fn
        return deco

    def startup(self, fn: Callable[..., Any]):
        self._startup_handlers.append(fn)
        return fn

    def shutdown(self, fn: Callable[..., Any]):
        self._shutdown_handlers.append(fn)
        return fn

    def exception_handler(self, exc_type: type[BaseException]):
        def deco(fn: Callable[..., Any]):
            self._exception_handlers[exc_type] = fn
            return fn
        return deco

    @contextmanager
    def override_dependency(self, original: Callable[..., Any], override: Callable[..., Any]):
        prev = self.dependency_overrides.get(original, None)
        had_prev = original in self.dependency_overrides
        self.dependency_overrides[original] = override
        try:
            yield
        finally:
            if had_prev:
                self.dependency_overrides[original] = prev  # type: ignore[assignment]
            else:
                self.dependency_overrides.pop(original, None)

    def clear_dependency_overrides(self):
        self.dependency_overrides.clear()

    async def _run_event_handlers(self, handlers: list[Callable[..., Any]]):
        for fn in handlers:
            await call_callable(fn)

    async def _handle_exception(self, req: Request, exc: Exception) -> Response:
        for exc_type in type(exc).mro():
            fn = self._exception_handlers.get(exc_type)
            if fn is None:
                continue
            value = await call_callable(fn, req, exc)
            return self._to_response(value, req=req)
        if isinstance(exc, HTTPError):
            return JSONResponse({"error":exc.message,"detail":exc.detail}, status=exc.status, encoders=self._json_encoders)
        if isinstance(exc, TimeoutError):
            return JSONResponse({"error":"Request Timeout"}, status=408, encoders=self._json_encoders)
        return JSONResponse({"error":"Internal Server Error"}, status=500, encoders=self._json_encoders)

    async def _handle(self, req:Request, receive, send)->Response:
        static_res = self._serve_static(req.path, req=req)
        if static_res is not None:
            req.scope["turbo.route"] = req.path
            return static_res
        if self.redirect_slashes and req.path != "/":
            alt = _alternate_slash_path(req.path)
            if alt:
                has_exact = any(r.method == req.method and r.path == req.path for r in self._routes)
                has_alt = any(r.method == req.method and r.path == alt for r in self._routes)
                if has_alt and not has_exact:
                    q = req.scope.get("query_string", b"")
                    loc = alt + (("?" + q.decode("latin1")) if q else "")
                    return Response(status=self.redirect_status_code, headers=[(b"location", loc.encode("latin1")), (b"content-length", b"0")], body=b"")
        m=self.router.match(req.method, req.path)
        if m is None:
            if self.redirect_slashes:
                alt = _alternate_slash_path(req.path)
                if alt:
                    target_exists = self.router.match(req.method, alt) is not None or self._serve_static(alt, req=req) is not None
                    if target_exists:
                        q = req.scope.get("query_string", b"")
                        loc = alt + (("?" + q.decode("latin1")) if q else "")
                        return Response(status=self.redirect_status_code, headers=[(b"location", loc.encode("latin1")), (b"content-length", b"0")], body=b"")
            req.scope["turbo.route"] = "__unmatched__"
            return JSONResponse({"error":"Not Found"}, status=404)
        req.path_params=m.params
        route=self._route_by_handler.get(m.handler)
        if route is not None and not route.path_param_names and req.path != route.path:
            req.scope["turbo.route"] = "__unmatched__"
            return JSONResponse({"error":"Not Found"}, status=404)
        req.scope["turbo.route"] = route.path if route is not None else req.path
        total = 0

        async def guarded_receive():
            nonlocal total
            while True:
                event=await receive()
                if event.get("type")=="http.request":
                    b = event.get("body", b"")
                    total += len(b)
                    if total > self.max_body_bytes:
                        raise HTTPError(413,"Payload Too Large",{"max_body_bytes":self.max_body_bytes})
                return event

        if route is None:
            res = await call_callable(m.handler, req)
        else:
            dep_cache={}
            kwargs, cleanups=await resolve_dependencies(route.param_specs, req=req, receive=guarded_receive, path_param_names=route.path_param_names, validate_body_fn=route.validate_body_fn, body_param_name=route.request_body_param_name, body_param_names=route.request_body_param_names, dep_cache=dep_cache, dependency_overrides=self.dependency_overrides)
            try:
                res = await call_callable(route.handler, **kwargs)
                if route.validate_response_fn is not None:
                    if isinstance(res, Response):
                        raise HTTPError(500,"response_model cannot be used with raw Response objects")
                    res=route.validate_response_fn(res)
            finally:
                for cb in reversed(cleanups):
                    await call_callable(cb)
        return self._to_response(res, req=req, status_code_override=route.status_code if route is not None else None)

    def _serve_static(self, path: str, req: Optional[Request] = None):
        for prefix, root in self._static_mounts:
            if path == prefix or path.startswith(prefix + "/"):
                rel = path[len(prefix):].lstrip("/")
                target = os.path.realpath(os.path.join(root, rel))
                if not target.startswith(root):
                    return JSONResponse({"error":"Forbidden"}, status=403, encoders=self._json_encoders)
                if not os.path.isfile(target):
                    return JSONResponse({"error":"Not Found"}, status=404, encoders=self._json_encoders)
                fr = FileResponse(target)
                if req is not None:
                    fr.prepare_for_request(req.headers, method=req.method)
                return fr
        return None

    def _to_response(self, result:Any, req: Optional[Request]=None, status_code_override: Optional[int]=None)->Response:
        if isinstance(result, Response):
            if isinstance(result, FileResponse) and req is not None:
                result.prepare_for_request(req.headers, method=req.method)
            return result
        status = int(status_code_override) if status_code_override is not None else 200
        if isinstance(result, (dict,list,int,float,bool)) or result is None:
            return JSONResponse(result, status=status, encoders=self._json_encoders)
        if isinstance(result, str):
            return TextResponse(result, status=status)
        return JSONResponse({"ok":True,"result":result}, status=status, encoders=self._json_encoders)

    async def __call__(self, scope, receive, send):
        await self._asgi_app(scope, receive, send)

    async def _drain_inflight_requests(self):
        if self._inflight_http <= 0:
            return
        timeout_seconds = max(0.0, self._shutdown_drain_timeout)
        if timeout_seconds == 0:
            return
        try:
            await asyncio.wait_for(self._drain_event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            return

    async def _dispatch_asgi(self, scope, receive, send):
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            while True:
                msg = await receive()
                msg_type = msg.get("type")
                if msg_type == "lifespan.startup":
                    try:
                        await self._run_event_handlers(self._startup_handlers)
                        await send({"type": "lifespan.startup.complete"})
                    except Exception as exc:
                        await send({"type": "lifespan.startup.failed", "message": str(exc)})
                elif msg_type == "lifespan.shutdown":
                    try:
                        await self._drain_inflight_requests()
                        await self._run_event_handlers(self._shutdown_handlers)
                        await send({"type": "lifespan.shutdown.complete"})
                    except Exception as exc:
                        await send({"type": "lifespan.shutdown.failed", "message": str(exc)})
                    return
            return
        mounted = self._select_mounted_app(scope)
        if mounted is not None:
            app, child_scope = mounted
            await app(child_scope, receive, send)
            return
        if scope_type=="websocket":
            await self._handle_websocket(scope, receive, send)
            return
        if scope_type!="http":
            return
        req=Request(scope, multipart_limits=self._multipart_limits)
        self._inflight_http += 1
        self._drain_event.clear()
        async with self._sem:
            try:
                async with timeout(self.request_timeout):
                    resp=await self._http_handler(req, receive, send)
            except Exception as exc:
                resp=await self._handle_exception(req, exc)
            try:
                await resp.send(send)
            finally:
                self._inflight_http -= 1
                if self._inflight_http <= 0:
                    self._drain_event.set()

    async def _final_handler(self, req:Request, receive, send)->Response:
        return await self._handle(req, receive, send)

    async def _handle_websocket(self, scope, receive, send):
        ws = WebSocket(scope, receive, send)
        m = self.router.match("WS", ws.path)
        if m is None:
            ws.scope["turbo.route"] = "__unmatched__"
            await ws.close(1000)
            return
        ws.path_params = m.params
        route = self._route_by_handler.get(m.handler)
        ws.scope["turbo.route"] = route.path if route is not None else ws.path
        if route is None:
            await call_callable(m.handler, ws)
            if not ws.closed:
                await ws.close(1000)
            return
        dep_cache = {}
        kwargs, cleanups = await resolve_dependencies(route.param_specs, req=ws, receive=receive, path_param_names=route.path_param_names, validate_body_fn=None, body_param_name=None, body_param_names=None, dep_cache=dep_cache, dependency_overrides=self.dependency_overrides)
        try:
            await call_callable(route.handler, **kwargs)
        except HTTPError:
            await ws.close(1008)
        except Exception:
            await ws.close(1011)
        finally:
            for cb in reversed(cleanups):
                await call_callable(cb)
        if not ws.closed:
            await ws.close(1000)

    def _select_mounted_app(self, scope):
        host = _scope_host(scope)
        for pattern, app in self._host_mounts:
            if _host_matches(pattern, host):
                return app, dict(scope)
        path = scope.get("path", "")
        for prefix, app in self._subapps:
            if path == prefix or path.startswith(prefix + "/"):
                child_scope = dict(scope)
                child_scope["path"] = path[len(prefix):] or "/"
                child_scope["root_path"] = (scope.get("root_path", "") + prefix)
                return app, child_scope
        return None

def _extract_security(specs: list[ParamSpec]):
    requirements: list[dict[str, list[str]]] = []
    schemes: dict[str, dict[str, Any]] = {}
    seen_calls: set[Callable[..., Any]] = set()

    def collect_from_call(dep: Callable[..., Any], scope_override: Optional[list[str]] = None):
        if dep in seen_calls:
            return
        seen_calls.add(dep)
        req = getattr(dep, "__turbo_security_requirement__", None)
        sch = getattr(dep, "__turbo_security_scheme__", None)
        if isinstance(req, dict):
            r = {}
            for name, scopes in req.items():
                r[name] = list(scope_override if scope_override is not None else scopes)
            requirements.append(r)
        if isinstance(sch, tuple) and len(sch) == 2:
            name, scheme = sch
            if isinstance(name, str) and isinstance(scheme, dict):
                schemes[name] = scheme
        try:
            sig = inspect.signature(dep)
        except (TypeError, ValueError):
            return
        for p in sig.parameters.values():
            default = p.default
            if isinstance(default, Depends):
                collect_from_call(default.call, default.scopes)

    for spec in specs:
        if spec.kind != "dep" or spec.dep is None:
            continue
        collect_from_call(spec.dep.call, spec.dep.scopes)
    return (requirements or None), (schemes or None)

def _is_typed_dict_cls(tp: Any):
    return isinstance(tp, type) and issubclass(tp, dict) and hasattr(tp, "__annotations__") and (hasattr(tp, "__required_keys__") or hasattr(tp, "__optional_keys__"))

def _is_body_candidate(spec: ParamSpec):
    if spec.annotation is inspect._empty:
        return False
    if spec.kind == "param":
        return spec.param is not None and spec.param.source == "body"
    if spec.kind != "auto":
        return False
    ann = spec.annotation
    if isinstance(ann, type):
        if issubclass(ann, Model):
            return True
        if is_dataclass(ann):
            return True
        if _is_typed_dict_cls(ann):
            return True
    return False

def _scope_host(scope: dict) -> str:
    for k, v in scope.get("headers", []):
        if k.decode("latin1").lower() == "host":
            host = v.decode("latin1").split(":", 1)[0].strip().lower()
            return host
    return ""

def _host_matches(pattern: str, host: str) -> bool:
    if not pattern:
        return False
    if pattern.startswith("*."):
        suffix = pattern[1:].lower()
        return host.endswith(suffix)
    return host == pattern.lower()

def _path_param_names(path: str):
    out = set()
    for seg in path.split("/"):
        if not (seg.startswith("{") and seg.endswith("}")):
            continue
        inner = seg[1:-1].strip()
        name = inner.split(":", 1)[0].strip()
        if name:
            out.add(name)
    return out

def _alternate_slash_path(path: str):
    if path == "/":
        return None
    if path.endswith("/"):
        alt = path[:-1]
        return alt or "/"
    return path + "/"
