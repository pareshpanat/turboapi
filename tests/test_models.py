import pytest
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal, Union
from uuid import UUID
from turbo.models import Model, field, field_validator, model_validator, compile_model_validator
from turbo.errors import HTTPError

class U(Model):
    name: str = field(min_len=2)

def test_min_len():
    v=compile_model_validator(U)
    with pytest.raises(HTTPError):
        v({'name':'A'})

class Role(str, Enum):
    ADMIN = "admin"
    USER = "user"

class Advanced(Model):
    role: Role
    code: Literal["A", "B"]
    when: datetime
    day: date
    amount: Decimal
    uid: UUID
    short: Annotated[str, field(min_len=2)]

class Cat(Model):
    kind: Literal["cat"]
    meows: int

class Dog(Model):
    kind: Literal["dog"]
    barks: int

class Wrapper(Model):
    pet: Annotated[Union[Cat, Dog], field(discriminator="kind")]

def test_advanced_types_are_validated():
    v = compile_model_validator(Advanced)
    result = v({
        "role": "admin",
        "code": "A",
        "when": "2026-01-01T12:00:00Z",
        "day": "2026-01-01",
        "amount": "12.34",
        "uid": "2dc3f898-7f9e-4018-b94f-fdb65586df3f",
        "short": "ok",
    })
    assert result["role"].value == "admin"
    assert isinstance(result["when"], datetime)
    assert isinstance(result["day"], date)
    assert isinstance(result["amount"], Decimal)
    assert isinstance(result["uid"], UUID)

def test_advanced_literal_validation_error():
    v = compile_model_validator(Advanced)
    with pytest.raises(HTTPError):
        v({
            "role": "admin",
            "code": "C",
            "when": "2026-01-01T12:00:00Z",
            "day": "2026-01-01",
            "amount": "12.34",
            "uid": "2dc3f898-7f9e-4018-b94f-fdb65586df3f",
            "short": "ok",
        })

def test_discriminated_union_validation():
    v = compile_model_validator(Wrapper)
    data = v({"pet": {"kind": "dog", "barks": 2}})
    assert data["pet"]["kind"] == "dog"
    with pytest.raises(HTTPError):
        v({"pet": {"kind": "bird", "wings": 2}})

class Validated(Model):
    name: str = field(min_len=2)
    age: int

    @field_validator("name", mode="before")
    def strip_name(cls, value):
        return value.strip()

    @field_validator("age")
    def age_non_negative(cls, value):
        if value < 0:
            raise ValueError("age must be non-negative")
        return value

    @model_validator(mode="after")
    def no_reserved_name(cls, data):
        if data["name"].lower() == "admin":
            raise ValueError("reserved name")
        return data

def test_custom_field_and_model_validators():
    v = compile_model_validator(Validated)
    out = v({"name": "  user  ", "age": 20})
    assert out["name"] == "user"
    with pytest.raises(HTTPError):
        v({"name": "x", "age": 20})
    with pytest.raises(HTTPError):
        v({"name": "ok", "age": -1})
    with pytest.raises(HTTPError):
        v({"name": "admin", "age": 20})

class Aliased(Model):
    model_config = {"populate_by_name": False, "schema_by_alias": True}
    full_name: str = field(alias="fullName")

def test_alias_and_populate_by_name_controls():
    v = compile_model_validator(Aliased)
    out = v({"fullName": "Paresh"})
    assert out["full_name"] == "Paresh"
    with pytest.raises(HTTPError):
        v({"full_name": "Paresh"})
    sch = Aliased.schema()
    assert "fullName" in sch["properties"]
    assert "full_name" not in sch["properties"]

def test_location_rich_validation_error():
    v = compile_model_validator(Wrapper)
    with pytest.raises(HTTPError) as exc:
        v({"pet": {"kind": "bird", "wings": 2}})
    assert exc.value.status == 422
    errs = exc.value.detail["errors"]
    assert errs[0]["loc"] == ["pet", "kind"]
