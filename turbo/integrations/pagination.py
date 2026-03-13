from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from ..deps import Param, Query
from ..errors import HTTPError


@dataclass(slots=True)
class PageParams:
    page: int
    size: int
    offset: int
    limit: int
    sort: Optional[str] = None
    order: str = "asc"


def parse_pagination(
    page: Optional[int] = Query(required=False),
    size: Optional[int] = Query(required=False),
    offset: Optional[int] = Query(required=False),
    limit: Optional[int] = Query(required=False),
    sort: Optional[str] = Query(required=False),
    order: Optional[str] = Query(required=False),
    *,
    default_size: int = 20,
    max_size: int = 100,
) -> PageParams:
    if isinstance(page, Param):
        page = None
    if isinstance(size, Param):
        size = None
    if isinstance(offset, Param):
        offset = None
    if isinstance(limit, Param):
        limit = None
    if isinstance(sort, Param):
        sort = None
    if isinstance(order, Param):
        order = None

    page_val = int(page if page is not None else 1)
    size_val = int(size if size is not None else default_size)
    offset_val = int(offset if offset is not None else (page_val - 1) * size_val)
    limit_val = int(limit if limit is not None else size_val)
    order_val = str(order or "asc").lower()

    if page_val < 1:
        raise HTTPError(422, "Validation Error", {"errors": [{"loc": ["query", "page"], "msg": "must be >= 1", "type": "value_error.number.not_ge"}]})
    if size_val < 1:
        raise HTTPError(422, "Validation Error", {"errors": [{"loc": ["query", "size"], "msg": "must be >= 1", "type": "value_error.number.not_ge"}]})
    if size_val > max_size:
        raise HTTPError(422, "Validation Error", {"errors": [{"loc": ["query", "size"], "msg": f"must be <= {max_size}", "type": "value_error.number.not_le"}]})
    if offset_val < 0:
        raise HTTPError(422, "Validation Error", {"errors": [{"loc": ["query", "offset"], "msg": "must be >= 0", "type": "value_error.number.not_ge"}]})
    if limit_val < 1:
        raise HTTPError(422, "Validation Error", {"errors": [{"loc": ["query", "limit"], "msg": "must be >= 1", "type": "value_error.number.not_ge"}]})
    if limit_val > max_size:
        raise HTTPError(422, "Validation Error", {"errors": [{"loc": ["query", "limit"], "msg": f"must be <= {max_size}", "type": "value_error.number.not_le"}]})
    if order_val not in ("asc", "desc"):
        raise HTTPError(422, "Validation Error", {"errors": [{"loc": ["query", "order"], "msg": "must be 'asc' or 'desc'", "type": "value_error.enum"}]})

    return PageParams(
        page=page_val,
        size=size_val,
        offset=offset_val,
        limit=limit_val,
        sort=sort or None,
        order=order_val,
    )


def apply_pagination(items: Iterable[Any], params: PageParams):
    seq = list(items)
    return seq[params.offset : params.offset + params.limit]


def apply_sorting(items: Iterable[Any], *, sort: Optional[str], order: str = "asc"):
    seq = list(items)
    if not sort:
        return seq
    reverse = str(order).lower() == "desc"
    return sorted(seq, key=lambda item: _sort_key(item, sort), reverse=reverse)


def apply_filters(items: Iterable[Any], filters: dict[str, Any] | None):
    if not filters:
        return list(items)
    out = []
    for item in items:
        if _matches_filters(item, filters):
            out.append(item)
    return out


def _sort_key(item: Any, field_name: str):
    if isinstance(item, dict):
        return item.get(field_name)
    return getattr(item, field_name, None)


def _matches_filters(item: Any, filters: dict[str, Any]):
    for key, expected in filters.items():
        if isinstance(item, dict):
            value = item.get(key)
        else:
            value = getattr(item, key, None)
        if value != expected:
            return False
    return True
