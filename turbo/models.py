from __future__ import annotations

import re
import types
from dataclasses import MISSING, dataclass, fields as dataclass_fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Any, Dict, Literal, Optional, Union, get_args, get_origin
from uuid import UUID

from .errors import HTTPError
from .pydantic_compat import is_pydantic_model_class, validate_pydantic_model

_MISSING = object()


@dataclass(frozen=True, slots=True)
class FieldInfo:
    min_len: Optional[int] = None
    max_len: Optional[int] = None
    ge: Optional[float] = None
    le: Optional[float] = None
    gt: Optional[float] = None
    lt: Optional[float] = None
    multiple_of: Optional[float] = None
    min_items: Optional[int] = None
    max_items: Optional[int] = None
    regex: Optional[str] = None
    discriminator: Optional[str] = None
    alias: Optional[str] = None


def field(*, min_len=None, max_len=None, ge=None, le=None, gt=None, lt=None, multiple_of=None, min_items=None, max_items=None, regex=None, discriminator=None, alias=None) -> Any:
    return FieldInfo(min_len=min_len, max_len=max_len, ge=ge, le=le, gt=gt, lt=lt, multiple_of=multiple_of, min_items=min_items, max_items=max_items, regex=regex, discriminator=discriminator, alias=alias)


def field_validator(*field_names: str, mode: str = "after"):
    mode = mode.lower().strip()
    if mode not in ("before", "after"):
        raise ValueError("field_validator mode must be 'before' or 'after'")

    def deco(fn):
        fn.__turbo_field_validator__ = {"fields": tuple(field_names), "mode": mode}
        return fn

    return deco


def model_validator(*, mode: str = "after"):
    mode = mode.lower().strip()
    if mode not in ("before", "after"):
        raise ValueError("model_validator mode must be 'before' or 'after'")

    def deco(fn):
        fn.__turbo_model_validator__ = {"mode": mode}
        return fn

    return deco


class Model:
    @classmethod
    def schema(cls) -> Dict[str, Any]:
        return model_to_json_schema(cls)


def _validation_error(loc: list[Any], msg: str, typ: str, ctx: Optional[dict[str, Any]] = None):
    error = {"loc": list(loc), "msg": msg, "type": typ}
    if ctx:
        error["ctx"] = ctx
    raise HTTPError(422, "Validation Error", {"errors": [error]})


def _with_prefixed_loc(exc: HTTPError, prefix: list[Any]):
    if exc.status != 422:
        raise exc
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("errors"), list):
        patched = []
        for err in detail["errors"]:
            if isinstance(err, dict):
                loc = err.get("loc", [])
                if not isinstance(loc, list):
                    loc = [loc]
                patched.append({**err, "loc": [*prefix, *loc]})
            else:
                patched.append({"loc": list(prefix), "msg": str(err), "type": "value_error"})
        raise HTTPError(422, "Validation Error", {"errors": patched})
    _validation_error(prefix, exc.message, "value_error", {"detail": detail})


def _union_args(tp):
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        return get_args(tp)
    return ()


def _split_annotated(tp):
    if get_origin(tp) is Annotated:
        args = get_args(tp)
        base = args[0]
        meta = args[1:]
        fi = next((m for m in meta if isinstance(m, FieldInfo)), None)
        return base, fi
    return tp, None


def is_optional(tp) -> bool:
    return type(None) in _union_args(tp)


def unwrap_optional(tp):
    args = [a for a in _union_args(tp) if a is not type(None)]
    if args:
        if len(args) == 1:
            return args[0]
        return Union[tuple(args)]
    return tp


def _model_config(model_cls: type[Model]) -> dict[str, Any]:
    cfg = getattr(model_cls, "model_config", {})
    return cfg if isinstance(cfg, dict) else {}


def _get_populate_by_name(model_cls: type[Model]) -> bool:
    return bool(_model_config(model_cls).get("populate_by_name", True))


def _get_schema_by_alias(model_cls: type[Model]) -> bool:
    return bool(_model_config(model_cls).get("schema_by_alias", False))


def _invoke_callable(fn, *args):
    try:
        return fn(*args)
    except TypeError:
        if len(args) >= 2:
            return fn(*args[1:])
        raise


def _collect_field_validators(model_cls: type[Model]):
    out: dict[str, dict[str, list[Any]]] = {}
    for cls in reversed(model_cls.__mro__):
        for name in dir(cls):
            obj = getattr(cls, name, None)
            meta = getattr(obj, "__turbo_field_validator__", None)
            if not isinstance(meta, dict):
                continue
            mode = meta.get("mode", "after")
            for field_name in meta.get("fields", ()):
                bucket = out.setdefault(field_name, {"before": [], "after": []})
                bucket[mode].append(obj)
    return out


def _collect_model_validators(model_cls: type[Model]):
    out = {"before": [], "after": []}
    for cls in reversed(model_cls.__mro__):
        for name in dir(cls):
            obj = getattr(cls, name, None)
            meta = getattr(obj, "__turbo_model_validator__", None)
            if not isinstance(meta, dict):
                continue
            mode = meta.get("mode", "after")
            out[mode].append(obj)
    return out


_VALIDATOR_CACHE: Dict[type, Any] = {}
_TYPE_VALIDATORS: dict[type, dict[str, list[Any]]] = {}


def type_validator(tp: type, *, mode: str = "after"):
    mode = mode.lower().strip()
    if mode not in ("before", "after"):
        raise ValueError("type_validator mode must be 'before' or 'after'")
    if not isinstance(tp, type):
        raise TypeError("type_validator target must be a type")

    def deco(fn):
        bucket = _TYPE_VALIDATORS.setdefault(tp, {"before": [], "after": []})
        bucket[mode].append(fn)
        return fn

    return deco


def compile_model_validator(model_cls: type[Model]):
    if model_cls in _VALIDATOR_CACHE:
        return _VALIDATOR_CACHE[model_cls]

    annotations = getattr(model_cls, "__annotations__", {})
    populate_by_name = _get_populate_by_name(model_cls)
    field_validators = _collect_field_validators(model_cls)
    model_validators = _collect_model_validators(model_cls)

    compiled = []
    for name, tp in annotations.items():
        base_tp, ann_fi = _split_annotated(tp)
        dv = getattr(model_cls, name, _MISSING)
        fi = None
        real_default = _MISSING
        if isinstance(dv, FieldInfo):
            fi = dv
        elif dv is not _MISSING:
            real_default = dv
        if ann_fi is not None:
            fi = ann_fi
        alias = fi.alias if fi is not None and fi.alias else None
        compiled.append((name, alias, base_tp, fi, real_default))

    def validate(data: Any, *, loc_prefix: tuple[Any, ...] = ()):
        raw = data
        for fn in model_validators["before"]:
            try:
                raw = _invoke_callable(fn, model_cls, raw)
            except HTTPError as exc:
                _with_prefixed_loc(exc, list(loc_prefix))
            except Exception as exc:
                _validation_error(list(loc_prefix) or ["body"], str(exc), "value_error.model_validator")

        if raw is None:
            _validation_error(list(loc_prefix) or ["body"], "Body required", "type_error.none_not_allowed")
        if not isinstance(raw, dict):
            _validation_error(list(loc_prefix) or ["body"], "Input should be an object", "type_error.object")

        out = {}
        for field_name, alias, tp, fi, default_value in compiled:
            optional = is_optional(tp)
            base = unwrap_optional(tp)

            present = False
            source_key = field_name
            if alias:
                if alias in raw:
                    present = True
                    source_key = alias
                elif populate_by_name and field_name in raw:
                    present = True
                    source_key = field_name
            elif field_name in raw:
                present = True
                source_key = field_name

            if not present:
                if default_value is not _MISSING:
                    out[field_name] = default_value
                    continue
                if optional:
                    out[field_name] = None
                    continue
                missing_key = alias or field_name
                _validation_error([*loc_prefix, missing_key], "Field required", "value_error.missing")

            value = raw[source_key]
            field_loc = [*loc_prefix, source_key]
            if value is None and optional:
                out[field_name] = None
                continue

            validators = field_validators.get(field_name, {"before": [], "after": []})
            parsed = value
            for fn in validators["before"]:
                try:
                    parsed = _invoke_callable(fn, model_cls, parsed)
                except HTTPError as exc:
                    _with_prefixed_loc(exc, field_loc)
                except Exception as exc:
                    _validation_error(field_loc, str(exc), "value_error.field_validator")

            parsed = validate_value(field_name, parsed, base, fi, loc=field_loc)

            for fn in validators["after"]:
                try:
                    parsed = _invoke_callable(fn, model_cls, parsed)
                except HTTPError as exc:
                    _with_prefixed_loc(exc, field_loc)
                except Exception as exc:
                    _validation_error(field_loc, str(exc), "value_error.field_validator")

            out[field_name] = parsed

        result = out
        for fn in model_validators["after"]:
            try:
                maybe = _invoke_callable(fn, model_cls, result)
                if maybe is not None:
                    result = maybe
            except HTTPError as exc:
                _with_prefixed_loc(exc, list(loc_prefix))
            except Exception as exc:
                _validation_error(list(loc_prefix) or ["body"], str(exc), "value_error.model_validator")
        return result

    _VALIDATOR_CACHE[model_cls] = validate
    return validate


def compile_type_validator(tp: Any):
    if isinstance(tp, type) and issubclass(tp, Model):
        return compile_model_validator(tp)
    if is_pydantic_model_class(tp):
        def _validate_pyd(data: Any, *, loc_prefix: tuple[Any, ...] = ("body",)):
            return validate_pydantic_model(tp, data, loc_prefix=loc_prefix)
        return _validate_pyd

    def validate(data: Any, *, loc_prefix: tuple[Any, ...] = ("body",)):
        return validate_value("body", data, tp, None, loc=list(loc_prefix))

    return validate


def _validate_literal(val: Any, tp: Any, loc: list[Any]):
    allowed = get_args(tp)
    if val not in allowed:
        _validation_error(loc, "Invalid literal value", "value_error.literal", {"allowed": list(allowed)})
    return val


def _discriminator_tags_for_arg(arg: Any, discriminator: str) -> list[Any]:
    annotation = None
    if isinstance(arg, type) and issubclass(arg, Model):
        annotation = getattr(arg, "__annotations__", {}).get(discriminator)
    elif _is_typed_dict_class(arg):
        annotation = getattr(arg, "__annotations__", {}).get(discriminator)
    elif isinstance(arg, type) and is_dataclass(arg):
        for f in dataclass_fields(arg):
            if f.name == discriminator:
                annotation = f.type
                break
    if annotation is None:
        return []
    annotation = _split_annotated(annotation)[0]
    if get_origin(annotation) is Literal:
        return list(get_args(annotation))
    return []


def _validate_union(val: Any, tp: Any, loc: list[Any], fi: Optional[FieldInfo] = None):
    if fi is None and get_origin(tp) is Annotated:
        _, fi = _split_annotated(tp)
        tp = unwrap_optional(tp)

    args = [a for a in _union_args(tp) if a is not type(None)]
    if fi and fi.discriminator:
        disc = fi.discriminator
        if not isinstance(val, dict):
            _validation_error(loc, "Discriminated union input must be an object", "type_error.object")
        if disc not in val:
            _validation_error([*loc, disc], "Discriminator field required", "value_error.discriminator.missing")
        tag = val.get(disc)
        mapping: dict[Any, Any] = {}
        for arg in args:
            for literal in _discriminator_tags_for_arg(arg, disc):
                mapping[literal] = arg
        if tag not in mapping:
            _validation_error([*loc, disc], "Invalid discriminator tag", "value_error.discriminator.invalid", {"expected": sorted(str(x) for x in mapping.keys())})
        return validate_value("union", val, mapping[tag], None, loc=loc)

    errors = []
    for idx, arg in enumerate(args):
        try:
            return validate_value("union", val, arg, None, loc=loc)
        except HTTPError as exc:
            if exc.status != 422:
                raise
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            variant_errors = detail.get("errors", [{"loc": loc, "msg": exc.message, "type": "value_error"}])
            errors.append({"variant": idx, "type": str(arg), "errors": variant_errors})
    _validation_error(loc, "Input does not match any union variant", "value_error.union", {"variants": errors})


def validate_value(name: str, val: Any, tp: Any, fi: Optional[FieldInfo], *, loc: Optional[list[Any]] = None):
    path = list(loc or [name])
    if isinstance(tp, type):
        typed = _TYPE_VALIDATORS.get(tp)
        if typed:
            for fn in typed["before"]:
                try:
                    val = _invoke_callable(fn, val)
                except Exception as exc:
                    _validation_error(path, str(exc), "value_error.type_validator")

    if isinstance(tp, type) and issubclass(tp, Model):
        try:
            return compile_model_validator(tp)(val, loc_prefix=tuple(path))
        except HTTPError as exc:
            raise exc
    if is_pydantic_model_class(tp):
        return validate_pydantic_model(tp, val, loc_prefix=tuple(path))
    if _is_typed_dict_class(tp):
        return _validate_typed_dict(val, tp, path)
    if isinstance(tp, type) and is_dataclass(tp):
        return _validate_dataclass(val, tp, path)

    origin = get_origin(tp)
    args = get_args(tp)
    if _union_args(tp):
        return _validate_union(val, tp, path, fi=fi)
    if origin is Literal:
        return _validate_literal(val, tp, path)
    if origin is list:
        if not isinstance(val, list):
            _validation_error(path, "Input should be a list", "type_error.list")
        if fi:
            if fi.min_items is not None and len(val) < fi.min_items:
                _validation_error(path, "List has too few items", "value_error.list.min_items", {"min_items": fi.min_items})
            if fi.max_items is not None and len(val) > fi.max_items:
                _validation_error(path, "List has too many items", "value_error.list.max_items", {"max_items": fi.max_items})
        inner = args[0] if args else Any
        out = [validate_value(name, item, inner, None, loc=[*path, idx]) for idx, item in enumerate(val)]
        if isinstance(tp, type):
            typed = _TYPE_VALIDATORS.get(tp)
            if typed:
                for fn in typed["after"]:
                    try:
                        out = _invoke_callable(fn, out)
                    except Exception as exc:
                        _validation_error(path, str(exc), "value_error.type_validator")
        return out
    if origin is dict:
        if not isinstance(val, dict):
            _validation_error(path, "Input should be an object", "type_error.object")
        inner = args[1] if len(args) == 2 else Any
        return {str(k): validate_value(name, v, inner, None, loc=[*path, str(k)]) for k, v in val.items()}

    if isinstance(tp, type) and issubclass(tp, Enum):
        try:
            return tp(val)
        except Exception:
            _validation_error(path, "Invalid enum value", "value_error.enum", {"allowed": [m.value for m in tp]})

    if tp is str:
        if not isinstance(val, str):
            _validation_error(path, "Input should be a valid string", "type_error.string")
        if fi:
            if fi.min_len is not None and len(val) < fi.min_len:
                _validation_error(path, "String too short", "value_error.string.min_length", {"min_length": fi.min_len})
            if fi.max_len is not None and len(val) > fi.max_len:
                _validation_error(path, "String too long", "value_error.string.max_length", {"max_length": fi.max_len})
            if fi.regex is not None and re.search(fi.regex, val) is None:
                _validation_error(path, "String does not match pattern", "value_error.string.pattern", {"pattern": fi.regex})
        return val

    if tp is bytes:
        if isinstance(val, bytes):
            return val
        if isinstance(val, str):
            return val.encode("utf-8")
        _validation_error(path, "Input should be bytes", "type_error.bytes")

    if tp is int:
        if not isinstance(val, int) or isinstance(val, bool):
            _validation_error(path, "Input should be a valid integer", "type_error.integer")
        if fi:
            if fi.ge is not None and val < fi.ge:
                _validation_error(path, "Value too small", "value_error.number.not_ge", {"ge": fi.ge})
            if fi.le is not None and val > fi.le:
                _validation_error(path, "Value too large", "value_error.number.not_le", {"le": fi.le})
            if fi.gt is not None and val <= fi.gt:
                _validation_error(path, "Value too small", "value_error.number.not_gt", {"gt": fi.gt})
            if fi.lt is not None and val >= fi.lt:
                _validation_error(path, "Value too large", "value_error.number.not_lt", {"lt": fi.lt})
            if fi.multiple_of is not None and (val % fi.multiple_of) != 0:
                _validation_error(path, "Value is not a multiple", "value_error.number.multiple_of", {"multiple_of": fi.multiple_of})
        out = val
        if isinstance(tp, type):
            typed = _TYPE_VALIDATORS.get(tp)
            if typed:
                for fn in typed["after"]:
                    try:
                        out = _invoke_callable(fn, out)
                    except Exception as exc:
                        _validation_error(path, str(exc), "value_error.type_validator")
        return out

    if tp is float:
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            _validation_error(path, "Input should be a valid number", "type_error.number")
        num = float(val)
        if fi:
            if fi.ge is not None and num < fi.ge:
                _validation_error(path, "Value too small", "value_error.number.not_ge", {"ge": fi.ge})
            if fi.le is not None and num > fi.le:
                _validation_error(path, "Value too large", "value_error.number.not_le", {"le": fi.le})
            if fi.gt is not None and num <= fi.gt:
                _validation_error(path, "Value too small", "value_error.number.not_gt", {"gt": fi.gt})
            if fi.lt is not None and num >= fi.lt:
                _validation_error(path, "Value too large", "value_error.number.not_lt", {"lt": fi.lt})
            if fi.multiple_of is not None and (num / fi.multiple_of) % 1 != 0:
                _validation_error(path, "Value is not a multiple", "value_error.number.multiple_of", {"multiple_of": fi.multiple_of})
        out = num
        if isinstance(tp, type):
            typed = _TYPE_VALIDATORS.get(tp)
            if typed:
                for fn in typed["after"]:
                    try:
                        out = _invoke_callable(fn, out)
                    except Exception as exc:
                        _validation_error(path, str(exc), "value_error.type_validator")
        return out

    if tp is Decimal:
        try:
            dec = Decimal(str(val))
        except (InvalidOperation, ValueError):
            _validation_error(path, "Input should be a valid decimal", "type_error.decimal")
        if fi:
            num = float(dec)
            if fi.ge is not None and num < fi.ge:
                _validation_error(path, "Value too small", "value_error.number.not_ge", {"ge": fi.ge})
            if fi.le is not None and num > fi.le:
                _validation_error(path, "Value too large", "value_error.number.not_le", {"le": fi.le})
            if fi.gt is not None and num <= fi.gt:
                _validation_error(path, "Value too small", "value_error.number.not_gt", {"gt": fi.gt})
            if fi.lt is not None and num >= fi.lt:
                _validation_error(path, "Value too large", "value_error.number.not_lt", {"lt": fi.lt})
        out = dec
        if isinstance(tp, type):
            typed = _TYPE_VALIDATORS.get(tp)
            if typed:
                for fn in typed["after"]:
                    try:
                        out = _invoke_callable(fn, out)
                    except Exception as exc:
                        _validation_error(path, str(exc), "value_error.type_validator")
        return out

    if tp is bool:
        if not isinstance(val, bool):
            _validation_error(path, "Input should be a valid boolean", "type_error.boolean")
        return val

    if tp is datetime:
        if not isinstance(val, str):
            _validation_error(path, "Input should be a valid datetime string", "type_error.datetime")
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            _validation_error(path, "Invalid datetime format", "value_error.datetime")

    if tp is date:
        if not isinstance(val, str):
            _validation_error(path, "Input should be a valid date string", "type_error.date")
        try:
            return date.fromisoformat(val)
        except ValueError:
            _validation_error(path, "Invalid date format", "value_error.date")

    if tp is time:
        if not isinstance(val, str):
            _validation_error(path, "Input should be a valid time string", "type_error.time")
        try:
            return time.fromisoformat(val)
        except ValueError:
            _validation_error(path, "Invalid time format", "value_error.time")

    if tp is UUID:
        if not isinstance(val, str):
            _validation_error(path, "Input should be a valid UUID string", "type_error.uuid")
        try:
            return UUID(val)
        except ValueError:
            _validation_error(path, "Invalid UUID format", "value_error.uuid")

    out = val
    if isinstance(tp, type):
        typed = _TYPE_VALIDATORS.get(tp)
        if typed:
            for fn in typed["after"]:
                try:
                    out = _invoke_callable(fn, out)
                except Exception as exc:
                    _validation_error(path, str(exc), "value_error.type_validator")
    return out


def model_to_json_schema(model_cls: type[Model]) -> Dict[str, Any]:
    annotations = getattr(model_cls, "__annotations__", {})
    required = []
    props = {}
    schema_by_alias = _get_schema_by_alias(model_cls)
    for name, tp in annotations.items():
        base_tp, ann_fi = _split_annotated(tp)
        dv = getattr(model_cls, name, _MISSING)
        fi = dv if isinstance(dv, FieldInfo) else ann_fi
        has_default = dv is not _MISSING and not isinstance(dv, FieldInfo)
        optional = is_optional(base_tp)
        key = fi.alias if schema_by_alias and fi is not None and fi.alias else name
        if not optional and not has_default:
            required.append(key)
        props[key] = type_to_schema(unwrap_optional(base_tp), fi)
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def type_to_schema(tp: Any, fi: Optional[FieldInfo]) -> Dict[str, Any]:
    tp, ann_fi = _split_annotated(tp)
    fi = ann_fi or fi
    if isinstance(tp, type) and issubclass(tp, Model):
        return model_to_json_schema(tp)
    if _is_typed_dict_class(tp):
        return _typed_dict_to_schema(tp)
    if isinstance(tp, type) and is_dataclass(tp):
        return _dataclass_to_schema(tp)
    if _union_args(tp):
        members = [a for a in _union_args(tp) if a is not type(None)]
        schema = {"oneOf": [type_to_schema(a, None) for a in members]}
        if fi and fi.discriminator:
            disc = {"propertyName": fi.discriminator}
            mapping = {}
            for arg in members:
                if isinstance(arg, type) and issubclass(arg, Model):
                    for tag in _discriminator_tags_for_arg(arg, fi.discriminator):
                        mapping[str(tag)] = f"#/components/schemas/{arg.__name__}"
            if mapping:
                disc["mapping"] = mapping
            schema["discriminator"] = disc
        return schema
    origin = get_origin(tp)
    args = get_args(tp)
    if origin is Literal:
        vals = list(args)
        val_types = {type(v) for v in vals}
        if len(val_types) == 1:
            base = type_to_schema(next(iter(val_types)), None)
            base["enum"] = vals
            return base
        return {"enum": vals}
    if origin is list:
        inner = args[0] if args else Any
        schema = {"type": "array", "items": type_to_schema(inner, None)}
        if fi:
            if fi.min_items is not None:
                schema["minItems"] = fi.min_items
            if fi.max_items is not None:
                schema["maxItems"] = fi.max_items
        return schema
    if origin is dict:
        inner = args[1] if len(args) == 2 else Any
        return {"type": "object", "additionalProperties": type_to_schema(inner, None)}
    if isinstance(tp, type) and issubclass(tp, Enum):
        values = [m.value for m in tp]
        val_types = {type(v) for v in values}
        if len(val_types) == 1:
            base = type_to_schema(next(iter(val_types)), None)
            base["enum"] = values
            return base
        return {"enum": values}
    if tp is str:
        schema = {"type": "string"}
        if fi:
            if fi.min_len is not None:
                schema["minLength"] = fi.min_len
            if fi.max_len is not None:
                schema["maxLength"] = fi.max_len
            if fi.regex is not None:
                schema["pattern"] = fi.regex
        return schema
    if tp is bytes:
        return {"type": "string", "format": "binary"}
    if tp is int:
        schema = {"type": "integer"}
        if fi:
            if fi.ge is not None:
                schema["minimum"] = fi.ge
            if fi.le is not None:
                schema["maximum"] = fi.le
            if fi.gt is not None:
                schema["exclusiveMinimum"] = fi.gt
            if fi.lt is not None:
                schema["exclusiveMaximum"] = fi.lt
            if fi.multiple_of is not None:
                schema["multipleOf"] = fi.multiple_of
        return schema
    if tp is float:
        schema = {"type": "number"}
        if fi:
            if fi.ge is not None:
                schema["minimum"] = fi.ge
            if fi.le is not None:
                schema["maximum"] = fi.le
            if fi.gt is not None:
                schema["exclusiveMinimum"] = fi.gt
            if fi.lt is not None:
                schema["exclusiveMaximum"] = fi.lt
            if fi.multiple_of is not None:
                schema["multipleOf"] = fi.multiple_of
        return schema
    if tp is Decimal:
        schema = {"type": "string", "format": "decimal"}
        if fi:
            if fi.ge is not None:
                schema["minimum"] = fi.ge
            if fi.le is not None:
                schema["maximum"] = fi.le
        return schema
    if tp is bool:
        return {"type": "boolean"}
    if tp is datetime:
        return {"type": "string", "format": "date-time"}
    if tp is date:
        return {"type": "string", "format": "date"}
    if tp is time:
        return {"type": "string", "format": "time"}
    if tp is UUID:
        return {"type": "string", "format": "uuid"}
    return {"type": "string"}


def _is_typed_dict_class(tp: Any):
    return isinstance(tp, type) and issubclass(tp, dict) and hasattr(tp, "__annotations__") and (hasattr(tp, "__required_keys__") or hasattr(tp, "__optional_keys__"))


def _validate_typed_dict(val: Any, tp: Any, loc: list[Any]):
    if not isinstance(val, dict):
        _validation_error(loc, "Input should be an object", "type_error.object")
    annotations = getattr(tp, "__annotations__", {})
    required = set(getattr(tp, "__required_keys__", set(annotations.keys())))
    out = {}
    for k, ann in annotations.items():
        if k not in val:
            if k in required:
                _validation_error([*loc, k], "Field required", "value_error.missing")
            continue
        out[k] = validate_value(k, val[k], ann, None, loc=[*loc, k])
    return out


def _typed_dict_to_schema(tp: Any):
    annotations = getattr(tp, "__annotations__", {})
    required = list(getattr(tp, "__required_keys__", set()))
    props = {k: type_to_schema(v, None) for k, v in annotations.items()}
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _validate_dataclass(val: Any, tp: Any, loc: list[Any]):
    if not isinstance(val, dict):
        _validation_error(loc, "Input should be an object", "type_error.object")
    kwargs: dict[str, Any] = {}
    for f in dataclass_fields(tp):
        if f.name not in val:
            if f.default is not MISSING:
                kwargs[f.name] = f.default
                continue
            if f.default_factory is not MISSING:
                kwargs[f.name] = f.default_factory()
                continue
            _validation_error([*loc, f.name], "Field required", "value_error.missing")
        kwargs[f.name] = validate_value(f.name, val[f.name], f.type, None, loc=[*loc, f.name])
    return tp(**kwargs)


def _dataclass_to_schema(tp: Any):
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in dataclass_fields(tp):
        props[f.name] = type_to_schema(f.type, None)
        if f.default is MISSING and f.default_factory is MISSING:
            required.append(f.name)
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema
