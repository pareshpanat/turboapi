from __future__ import annotations

import inspect
import types
from typing import Any, Dict, Set, Union, get_args, get_origin

from .deps import Param
from .models import Model, model_to_json_schema, type_to_schema
from .request import UploadFile


def build_openapi(app, *, title="TurboAPI", version="0.1.0") -> Dict[str, Any]:
    components = {"schemas": {}, "securitySchemes": {}}
    http_methods = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}
    ws_docs: list[dict[str, Any]] = []
    seen: set[type] = set()
    webhooks: dict[str, Any] = {}

    def model_ref(m: type[Model]):
        seen.add(m)
        return {"$ref": f"#/components/schemas/{m.__name__}"}

    def ensure():
        queue = list(seen)
        done = set()
        while queue:
            model = queue.pop()
            if model in done:
                continue
            done.add(model)
            components["schemas"][model.__name__] = model_to_json_schema(model)
            for _, tp in getattr(model, "__annotations__", {}).items():
                for nested in _find_models(tp):
                    if nested not in done:
                        seen.add(nested)
                        queue.append(nested)

    paths = {}
    used_operation_ids: set[str] = set()
    for route in app._routes:
        if route.internal or not getattr(route, "include_in_schema", True):
            continue

        method_upper = route.method.upper()
        if method_upper not in http_methods:
            if method_upper == "WS":
                if route.security_schemes:
                    for name, scheme in route.security_schemes.items():
                        components["securitySchemes"][name] = scheme
                ws_docs.append(_build_ws_doc(route))
            continue

        path_item = paths.setdefault(route.path, {})
        op_id = _unique_operation_id((route.operation_id or route.name), used_operation_ids)
        operation: dict[str, Any] = {"operationId": op_id, "responses": {}}

        if route.tags:
            operation["tags"] = route.tags
        if route.summary:
            operation["summary"] = route.summary
        if route.description:
            operation["description"] = route.description
        if route.security:
            operation["security"] = route.security
        if route.deprecated:
            operation["deprecated"] = True
        if route.callbacks:
            operation["callbacks"] = route.callbacks
        if route.security_schemes:
            for name, scheme in route.security_schemes.items():
                components["securitySchemes"][name] = scheme

        params = []
        form_props: dict[str, Any] = {}
        form_required: list[str] = []
        body_props: dict[str, Any] = {}
        body_required: list[str] = []
        body_media_types: set[str] = set()
        has_files = False
        request_media_type: str | None = None

        for p in route.param_specs:
            if p.kind in ("request", "dep"):
                continue
            if p.kind == "param" and p.param is not None:
                explicit: Param = p.param
                kind = explicit.source
                param_name = explicit.alias or p.name
            else:
                kind = "path" if p.name in route.path_param_names else "query"
                param_name = p.name

            ann = str if (p.annotation is inspect._empty or p.annotation is None) else p.annotation
            if kind in ("form", "file"):
                schema = _schema_for_param(ann, components, seen)
                if kind == "file":
                    has_files = True
                form_props[param_name] = schema
                if p.default is inspect._empty:
                    form_required.append(param_name)
                continue
            if kind == "body":
                schema = model_ref(ann) if (isinstance(ann, type) and issubclass(ann, Model)) else _schema_for_param(ann, components, seen)
                body_props[param_name] = schema
                mt = (p.param.media_type if p.param is not None and p.param.media_type else "application/json")
                body_media_types.add(mt)
                if p.default is inspect._empty:
                    body_required.append(param_name)
                continue
            schema = model_ref(ann) if (isinstance(ann, type) and issubclass(ann, Model)) else _schema_for_param(ann, components, seen)
            in_kind = "header" if kind == "host" else kind
            pname = "host" if kind == "host" else param_name
            params.append({"name": pname, "in": in_kind, "required": (kind == "path") or (p.default is inspect._empty), "schema": schema})

        if params:
            operation["parameters"] = params

        if form_props:
            request_media_type = "multipart/form-data" if has_files else "application/x-www-form-urlencoded"
            schema = {"type": "object", "properties": form_props}
            if form_required:
                schema["required"] = form_required
            operation["requestBody"] = {"required": bool(form_required), "content": {request_media_type: {"schema": schema}}}
        elif body_props:
            if len(body_props) == 1:
                only_name = next(iter(body_props.keys()))
                schema = body_props[only_name]
                required = only_name in body_required
            else:
                schema = {"type": "object", "properties": body_props}
                if body_required:
                    schema["required"] = body_required
                required = bool(body_required)
            content: dict[str, Any] = {}
            mts = body_media_types or {"application/json"}
            for mt in mts:
                content[mt] = {"schema": schema}
            request_media_type = next(iter(mts))
            operation["requestBody"] = {"required": required, "content": content}
        elif route.request_body_model is not None:
            request_media_type = "application/json"
            operation["requestBody"] = {"required": True, "content": {request_media_type: {"schema": model_ref(route.request_body_model)}}}
        elif route.request_body_type is not None:
            request_media_type = "application/json"
            operation["requestBody"] = {"required": True, "content": {request_media_type: {"schema": _schema_for_param(route.request_body_type, components, seen)}}}

        if route.responses:
            for code, payload in route.responses.items():
                code_key = str(code)
                if isinstance(payload, dict):
                    operation["responses"][code_key] = payload
                else:
                    operation["responses"][code_key] = {"description": str(payload)}

        default_desc = route.response_description or "OK"
        success_code = str(route.status_code if route.status_code is not None else 200)
        if route.response_model is not None and isinstance(route.response_model, type) and issubclass(route.response_model, Model):
            operation["responses"][success_code] = {"description": default_desc, "content": {"application/json": {"schema": model_ref(route.response_model)}}}
        elif success_code not in operation["responses"]:
            operation["responses"][success_code] = {"description": default_desc}

        _apply_route_examples(operation, route.examples, request_media_type)

        if route.openapi_extra:
            for key, value in route.openapi_extra.items():
                operation[key] = value

        _attach_default_error_responses(operation)

        path_item[route.method.lower()] = operation

        if route.webhooks:
            for key, value in route.webhooks.items():
                webhooks[key] = value

    ensure()
    _install_error_schemas(components)
    if not components["securitySchemes"]:
        components.pop("securitySchemes", None)

    doc: dict[str, Any] = {"openapi": "3.0.3", "info": {"title": title, "version": version}, "paths": paths, "components": components}
    if webhooks:
        doc["webhooks"] = webhooks
    if ws_docs:
        doc["x-turbo-websockets"] = ws_docs
        doc["x-turbo-websocket-conventions"] = {
            "summary": "TurboAPI WebSocket conventions",
            "subprotocolNegotiation": "Use WebSocket.select_subprotocol() or accept_subprotocol().",
            "heartbeat": {
                "defaultPing": "turbo:ping",
                "defaultPong": "turbo:pong",
                "helpers": ["WebSocket.start_heartbeat()", "WebSocket.receive_with_idle_timeout()"],
            },
            "closeCodes": {
                "1000": "Normal Closure",
                "1001": "Going Away",
                "1008": "Policy Violation",
                "1011": "Internal Error",
                "3000-4999": "Application Defined",
            },
        }
    return doc


def _find_models(tp) -> Set[type]:
    out = set()
    if isinstance(tp, type) and issubclass(tp, Model):
        out.add(tp)
    for a in getattr(tp, "__args__", ()) or ():
        out |= _find_models(a)
    return out


def _schema_for_param_impl(tp: Any, components: dict[str, Any], seen: set[type]):
    if tp is UploadFile:
        return {"type": "string", "format": "binary"}
    origin = get_origin(tp)
    args = get_args(tp) or ()
    if isinstance(tp, type):
        if issubclass(tp, Model):
            seen.add(tp)
            return {"$ref": f"#/components/schemas/{tp.__name__}"}
        if _is_typed_dict_cls(tp) or _is_dataclass_cls(tp):
            name = tp.__name__
            if name not in components["schemas"]:
                components["schemas"][name] = type_to_schema(tp, None)
            return {"$ref": f"#/components/schemas/{name}"}
    if origin is list and args and args[0] is UploadFile:
        return {"type": "array", "items": {"type": "string", "format": "binary"}}
    if origin in (list, tuple) and args:
        return {"type": "array", "items": _schema_for_param_impl(args[0], components, seen)}
    if origin is dict and len(args) == 2:
        return {"type": "object", "additionalProperties": _schema_for_param_impl(args[1], components, seen)}
    if origin in (Union, types.UnionType) and args:
        return {"oneOf": [_schema_for_param_impl(a, components, seen) for a in args if a is not type(None)]}
    return type_to_schema(tp, None)

def _schema_for_param(tp: Any, components: dict[str, Any], seen: set[type]):
    return _schema_for_param_impl(tp, components, seen)


def _apply_route_examples(operation: dict[str, Any], examples: dict[str, Any] | None, request_media_type: str | None):
    if not examples:
        return

    request_examples = examples.get("request")
    if request_examples is not None and "requestBody" in operation:
        media = request_media_type or "application/json"
        content = operation["requestBody"].setdefault("content", {})
        media_obj = content.setdefault(media, {})
        if isinstance(request_examples, dict):
            media_obj["examples"] = request_examples
        else:
            media_obj["example"] = request_examples

    response_examples = examples.get("responses")
    if isinstance(response_examples, dict):
        for code, value in response_examples.items():
            code_key = str(code)
            response_obj = operation["responses"].setdefault(code_key, {"description": "Response"})
            content = response_obj.setdefault("content", {})
            media_obj = content.setdefault("application/json", {})
            if isinstance(value, dict):
                media_obj["examples"] = value
            else:
                media_obj["example"] = value

    extra = {k: v for k, v in examples.items() if k not in {"request", "responses"}}
    if extra:
        operation["x-turbo-examples"] = extra


def _build_ws_doc(route):
    op: dict[str, Any] = {"operationId": (route.operation_id or route.name), "path": route.path}
    if route.summary:
        op["summary"] = route.summary
    if route.description:
        op["description"] = route.description
    if route.tags:
        op["tags"] = route.tags
    if route.security:
        op["security"] = route.security
    if route.deprecated:
        op["deprecated"] = True
    if route.subprotocols:
        op["subprotocols"] = list(route.subprotocols)
    if route.examples:
        op["examples"] = route.examples
    if route.openapi_extra:
        for key, value in route.openapi_extra.items():
            op[key] = value

    params = []
    for p in route.param_specs:
        if p.kind in ("request", "dep"):
            continue
        if p.kind == "param" and p.param is not None:
            src = p.param.source
            pname = p.param.alias or p.name
            required = p.default is inspect._empty
        else:
            src = "path" if p.name in route.path_param_names else "query"
            pname = p.name
            required = p.default is inspect._empty
        if src in ("form", "file"):
            continue
        in_kind = "header" if src == "host" else src
        pname = "host" if src == "host" else pname
        ann = str if (p.annotation is inspect._empty or p.annotation is None) else p.annotation
        params.append({"name": pname, "in": in_kind, "required": required, "schema": _schema_for_param_impl(ann, {"schemas": {}}, set())})
    if params:
        op["parameters"] = params
    return op

def _unique_operation_id(base: str, used: set[str]):
    candidate = base
    idx = 2
    while candidate in used:
        candidate = f"{base}_{idx}"
        idx += 1
    used.add(candidate)
    return candidate

def _install_error_schemas(components: dict[str, Any]):
    schemas = components.setdefault("schemas", {})
    schemas.setdefault(
        "ErrorResponse",
        {
            "type": "object",
            "properties": {
                "error": {"type": "string"},
                "detail": {"type": "object"},
            },
            "required": ["error"],
        },
    )
    schemas.setdefault(
        "ValidationErrorItem",
        {
            "type": "object",
            "properties": {
                "loc": {"type": "array", "items": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
                "msg": {"type": "string"},
                "type": {"type": "string"},
                "ctx": {"type": "object"},
            },
            "required": ["loc", "msg", "type"],
        },
    )
    schemas.setdefault(
        "HTTPValidationError",
        {
            "type": "object",
            "properties": {
                "error": {"type": "string", "example": "Validation Error"},
                "detail": {
                    "type": "object",
                    "properties": {
                        "errors": {"type": "array", "items": {"$ref": "#/components/schemas/ValidationErrorItem"}}
                    },
                },
            },
            "required": ["error", "detail"],
        },
    )

def _attach_default_error_responses(operation: dict[str, Any]):
    responses = operation.setdefault("responses", {})
    responses.setdefault(
        "422",
        {
            "description": "Validation Error",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}}},
        },
    )
    responses.setdefault(
        "500",
        {
            "description": "Internal Server Error",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
        },
    )

def _is_typed_dict_cls(tp: Any):
    return isinstance(tp, type) and issubclass(tp, dict) and hasattr(tp, "__annotations__") and (hasattr(tp, "__required_keys__") or hasattr(tp, "__optional_keys__"))

def _is_dataclass_cls(tp: Any):
    return isinstance(tp, type) and hasattr(tp, "__dataclass_fields__")
