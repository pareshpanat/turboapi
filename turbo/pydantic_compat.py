from __future__ import annotations

from typing import Any

from .errors import HTTPError

try:
    from pydantic import BaseModel as _PydanticBaseModel
    from pydantic import ValidationError as _PydanticValidationError
except Exception:  # pragma: no cover
    _PydanticBaseModel = None
    _PydanticValidationError = None


def is_pydantic_model_class(tp: Any) -> bool:
    return bool(_PydanticBaseModel is not None and isinstance(tp, type) and issubclass(tp, _PydanticBaseModel))


def is_pydantic_model_instance(value: Any) -> bool:
    return bool(_PydanticBaseModel is not None and isinstance(value, _PydanticBaseModel))


def validate_pydantic_model(model_cls: Any, data: Any, *, loc_prefix: tuple[Any, ...] = ("body",)):
    try:
        return model_cls.model_validate(data)
    except Exception as exc:
        if _PydanticValidationError is None or not isinstance(exc, _PydanticValidationError):
            raise
        errors = []
        for err in exc.errors(include_url=False):
            loc = [*loc_prefix, *list(err.get("loc", ()) or ())]
            if not loc:
                loc = ["body"]
            errors.append(
                {
                    "loc": loc,
                    "msg": str(err.get("msg", "Validation error")),
                    "type": str(err.get("type", "value_error")),
                }
            )
        raise HTTPError(422, "Validation Error", {"errors": errors})


def dump_pydantic_model(value: Any):
    if is_pydantic_model_instance(value):
        return value.model_dump(mode="json")
    return value


def pydantic_model_json_schema(model_cls: Any):
    return model_cls.model_json_schema(ref_template="#/components/schemas/{model}")
