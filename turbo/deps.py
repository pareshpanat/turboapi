from __future__ import annotations
import inspect
import types
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, get_origin, get_args, Union
from .errors import HTTPError
from .request import Request, UploadFile, WebSocket
from .utils import call_callable, run_sync

def _param_error(source: str, name: str, msg: str, typ: str, ctx: Optional[dict[str, Any]] = None):
    err = {"loc": [source, name], "msg": msg, "type": typ}
    if ctx:
        err["ctx"] = ctx
    raise HTTPError(422, "Validation Error", {"errors": [err]})

@dataclass(frozen=True, slots=True)
class Depends:
    call: Callable[..., Any]
    cache: bool = True
    scopes: Optional[list[str]] = None

def Security(call: Callable[..., Any], *, scopes: Optional[list[str]] = None, cache: bool = True):
    return Depends(call=call, cache=cache, scopes=list(scopes or []))

@dataclass(frozen=True, slots=True)
class Param:
    source: str
    alias: Optional[str] = None
    required: bool = True
    media_type: Optional[str] = None
    embed: Optional[bool] = None

def Query(*, alias: Optional[str] = None, required: bool = True):
    return Param(source="query", alias=alias, required=required)

def Header(*, alias: Optional[str] = None, required: bool = True):
    return Param(source="header", alias=alias, required=required)

def Cookie(*, alias: Optional[str] = None, required: bool = True):
    return Param(source="cookie", alias=alias, required=required)

def Form(*, alias: Optional[str] = None, required: bool = True):
    return Param(source="form", alias=alias, required=required)

def File(*, alias: Optional[str] = None, required: bool = True):
    return Param(source="file", alias=alias, required=required)

def Host(*, alias: Optional[str] = None, required: bool = True):
    return Param(source="host", alias=alias, required=required)

def Body(*, alias: Optional[str] = None, required: bool = True, media_type: Optional[str] = None, embed: Optional[bool] = None):
    return Param(source="body", alias=alias, required=required, media_type=media_type, embed=embed)

@dataclass(slots=True)
class ParamSpec:
    name: str
    kind: str
    annotation: Any
    default: Any
    dep: Optional[Depends]=None
    param: Optional[Param]=None

def unwrap_optional(tp):
    o=get_origin(tp)
    if o in (Union, types.UnionType):
        args=[a for a in get_args(tp) if a is not type(None)]
        return args[0] if args else tp
    return tp

def _is_list_annotation(ann: Any):
    return get_origin(ann) is list

def _list_inner_type(ann: Any):
    args = get_args(ann)
    return args[0] if args else str

def _as_header_key(name: str):
    return name.lower().replace("_", "-")

def compile_route_plan(handler: Callable, *, request_type):
    sig = inspect.signature(handler)
    specs = []
    for name, p in sig.parameters.items():
        ann = p.annotation
        default = p.default
        if ann is request_type or ann == request_type.__name__:
            specs.append(ParamSpec(name,"request",ann,default))
            continue
        if isinstance(default, Depends):
            specs.append(ParamSpec(name,"dep",ann,default,default))
            continue
        if isinstance(default, Param):
            specs.append(ParamSpec(name, "param", ann, default, param=default))
            continue
        specs.append(ParamSpec(name,"auto",ann,default))
    return specs, sig

def _cast_single(raw: str, ann):
    if ann is inspect._empty or ann is None:
        return raw
    ann = unwrap_optional(ann)
    try:
        if ann is str:
            return raw
        if ann is int:
            return int(raw)
        if ann is float:
            return float(raw)
        if ann is bool:
            v = raw.lower().strip()
            if v in ("true","1","yes","y","on"):
                return True
            if v in ("false","0","no","n","off"):
                return False
            raise ValueError()
    except Exception:
        raise ValueError(f"invalid value '{raw}' for {ann}")
    return raw

def cast_scalar(raw, ann):
    if _is_list_annotation(ann):
        values = raw if isinstance(raw, list) else ([raw] if raw is not None else [])
        inner = _list_inner_type(ann)
        return [_cast_single(v, inner) for v in values]
    if isinstance(raw, list):
        if not raw:
            return None
        raw = raw[0]
    return _cast_single(raw, ann)

async def _read_param_value(spec: ParamSpec, *, req: Request, receive, path: Dict[str, str], query_multi: Dict[str, list[str]], headers_multi: Dict[str, list[str]], cookies: Dict[str, str], path_param_names: set[str], validate_body_fn, body_param_name: Optional[str], body_param_names: Optional[set[str]], body_cache_ref):
    name = spec.name
    ann = spec.annotation
    default = spec.default
    if spec.kind == "param" and spec.param is not None:
        source = spec.param.source
        alias = spec.param.alias or name
        required = spec.param.required
        fallback_default = None
    else:
        source = "path" if name in path_param_names else ("body" if (validate_body_fn and ((body_param_name == name) or (body_param_names is not None and name in body_param_names))) else "query")
        alias = name
        required = default is inspect._empty
        fallback_default = None if default is inspect._empty else default
    if source == "path":
        if alias not in path:
            _param_error("path", alias, "Field required", "value_error.missing")
        try:
            return cast_scalar(path[alias], ann)
        except ValueError:
            _param_error("path", alias, "Invalid parameter type", "type_error.path", {"expected": str(ann)})
    if source == "query":
        vals = query_multi.get(alias)
        if vals is None:
            if required:
                _param_error("query", alias, "Field required", "value_error.missing")
            return fallback_default
        try:
            return cast_scalar(vals if _is_list_annotation(ann) else vals[0], ann)
        except ValueError:
            _param_error("query", alias, "Invalid parameter type", "type_error.query", {"expected": str(ann)})
    if source == "header":
        key = _as_header_key(alias)
        vals = headers_multi.get(key)
        if vals is None:
            if required:
                _param_error("header", alias, "Field required", "value_error.missing")
            return fallback_default
        try:
            return cast_scalar(vals if _is_list_annotation(ann) else vals[-1], ann)
        except ValueError:
            _param_error("header", alias, "Invalid parameter type", "type_error.header", {"expected": str(ann)})
    if source == "cookie":
        val = cookies.get(alias)
        if val is None:
            if required:
                _param_error("cookie", alias, "Field required", "value_error.missing")
            return fallback_default
        try:
            return cast_scalar(val, ann)
        except ValueError:
            _param_error("cookie", alias, "Invalid parameter type", "type_error.cookie", {"expected": str(ann)})
    if source == "host":
        vals = headers_multi.get("host")
        if vals is None or not vals:
            if required:
                _param_error("header", "host", "Field required", "value_error.missing")
            return fallback_default
        host = vals[-1].split(":", 1)[0].strip()
        try:
            return cast_scalar(host, ann)
        except ValueError:
            _param_error("header", "host", "Invalid parameter type", "type_error.header", {"expected": str(ann)})
    if source in ("form", "file"):
        form_multi = await req.form_multi(receive)
        vals = form_multi.get(alias)
        if vals is None:
            if required:
                _param_error(source, alias, "Field required", "value_error.missing")
            return fallback_default
        if source == "file":
            if _is_list_annotation(ann):
                files = [v for v in vals if isinstance(v, UploadFile)]
                return files
            file_value = next((v for v in vals if isinstance(v, UploadFile)), None)
            if file_value is None:
                _param_error("file", alias, "Expected uploaded file", "type_error.file")
            return file_value
        try:
            return cast_scalar(vals if _is_list_annotation(ann) else vals[0], ann)
        except ValueError:
            _param_error("form", alias, "Invalid parameter type", "type_error.form", {"expected": str(ann)})
    if source == "body":
        if body_cache_ref["value"] is None:
            if validate_body_fn:
                body_cache_ref["value"] = await validate_body_fn(req, receive)
            else:
                media_type = spec.param.media_type if (spec.kind == "param" and spec.param is not None) else None
                body_cache_ref["value"] = await req.parse_payload(receive, media_type=media_type)
        body = body_cache_ref["value"]
        if body is None:
            if required:
                _param_error("body", alias, "Body required", "value_error.missing")
            return fallback_default
        if isinstance(body, dict):
            if name in body:
                return body[name]
            if alias in body:
                return body[alias]
            if required and spec.kind == "param" and spec.param is not None and spec.param.embed and not validate_body_fn:
                _param_error("body", alias, "Field required", "value_error.missing")
            return body.get(name, body)
        return body
    raise HTTPError(500, "Unsupported parameter source", {"source": source})

async def resolve_dependencies(specs, *, req, receive, path_param_names:set[str], validate_body_fn, body_param_name: Optional[str], body_param_names: Optional[set[str]], dep_cache:Dict[Callable,Any], dependency_overrides: Optional[dict[Callable[..., Any], Callable[..., Any]]] = None):
    out={}
    query_multi=req.query_params_multi
    headers_multi=req.headers_multi
    cookies=req.cookies
    path=req.path_params or {}
    body_cache_ref = {"value": None}
    cleanups: list[Callable[[], Any]] = []
    overrides = dependency_overrides or {}

    async def resolve_dep_call(dep: Depends):
        fn = dep.call
        fn = overrides.get(fn, fn)
        if dep.cache and fn in dep_cache:
            return dep_cache[fn]
        val = await resolve_callable(fn, req=req, query_multi=query_multi, headers_multi=headers_multi, cookies=cookies, path=path, receive=receive, path_param_names=path_param_names, dep_cache=dep_cache, cleanups=cleanups, dependency_overrides=overrides)
        _enforce_scopes(dep, val)
        if dep.cache:
            dep_cache[fn] = val
        return val

    for s in specs:
        if s.kind=="request":
            out[s.name] = req
            continue
        if s.kind=="dep":
            out[s.name] = await resolve_dep_call(s.dep)
            continue
        out[s.name] = await _read_param_value(s, req=req, receive=receive, path=path, query_multi=query_multi, headers_multi=headers_multi, cookies=cookies, path_param_names=path_param_names, validate_body_fn=validate_body_fn, body_param_name=body_param_name, body_param_names=body_param_names, body_cache_ref=body_cache_ref)
    return out, cleanups

async def resolve_callable(fn: Callable[..., Any], *, req, query_multi: Dict[str, list[str]], headers_multi: Dict[str, list[str]], cookies: Dict[str, str], path: Dict[str, str], receive, path_param_names: set[str], dep_cache: Dict[Callable, Any], cleanups: list[Callable[[], Any]], dependency_overrides: Optional[dict[Callable[..., Any], Callable[..., Any]]] = None):
    overrides = dependency_overrides or {}
    fn = overrides.get(fn, fn)
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    body_cache_ref = {"value": None}
    for name, p in sig.parameters.items():
        ann = p.annotation
        default = p.default
        if ann in (Request, WebSocket) or ann in ("Request", "WebSocket"):
            kwargs[name] = req
            continue
        if isinstance(default, Depends):
            dep_fn = overrides.get(default.call, default.call)
            if default.cache and dep_fn in dep_cache:
                kwargs[name] = dep_cache[dep_fn]
                continue
            dep_val = await resolve_callable(dep_fn, req=req, query_multi=query_multi, headers_multi=headers_multi, cookies=cookies, path=path, receive=receive, path_param_names=path_param_names, dep_cache=dep_cache, cleanups=cleanups, dependency_overrides=overrides)
            _enforce_scopes(default, dep_val)
            if default.cache:
                dep_cache[dep_fn] = dep_val
            kwargs[name] = dep_val
            continue
        spec = ParamSpec(name=name, kind="param" if isinstance(default, Param) else "auto", annotation=ann, default=default, param=default if isinstance(default, Param) else None)
        kwargs[name] = await _read_param_value(spec, req=req, receive=receive, path=path, query_multi=query_multi, headers_multi=headers_multi, cookies=cookies, path_param_names=path_param_names, validate_body_fn=None, body_param_name=None, body_param_names=None, body_cache_ref=body_cache_ref)

    result = await call_callable(fn, **kwargs)
    if inspect.isasyncgen(result):
        try:
            value = await anext(result)
        except StopAsyncIteration:
            raise HTTPError(500, "Dependency generator did not yield a value")
        cleanups.append(result.aclose)
        return value
    if inspect.isgenerator(result):
        try:
            value = await run_sync(next, result)
        except StopIteration:
            raise HTTPError(500, "Dependency generator did not yield a value")
        async def _close_sync_generator(gen=result):
            await run_sync(gen.close)
        cleanups.append(_close_sync_generator)
        return value
    return result

def _extract_scopes_from_claims(value: Any):
    if not isinstance(value, dict):
        return set()
    raw = value.get("scope")
    if isinstance(raw, str):
        return {s for s in raw.split() if s}
    raw2 = value.get("scopes")
    if isinstance(raw2, list):
        return {str(s) for s in raw2}
    return set()

def _enforce_scopes(dep: Depends, value: Any):
    wanted = set(dep.scopes or [])
    if not wanted:
        return
    have = _extract_scopes_from_claims(value)
    missing = sorted(wanted - have)
    if missing:
        raise HTTPError(403, "Insufficient scope", {"required": sorted(wanted), "missing": missing})
