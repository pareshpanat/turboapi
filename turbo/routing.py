from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, Optional
from uuid import UUID

@dataclass(slots=True)
class Match:
    handler: Callable
    params: Dict[str,str]

def _is_valid_converter_value(converter: str, value: str) -> bool:
    if converter == "str":
        return len(value) > 0
    if converter == "int":
        try:
            int(value)
            return True
        except ValueError:
            return False
    if converter == "float":
        try:
            float(value)
            return True
        except ValueError:
            return False
    if converter == "uuid":
        try:
            UUID(value)
            return True
        except ValueError:
            return False
    if converter == "path":
        return True
    return False


def _parse_param_segment(seg: str):
    inner = seg[1:-1].strip()
    if ":" in inner:
        name, converter = inner.split(":", 1)
        name = name.strip()
        converter = converter.strip().lower()
    else:
        name = inner
        converter = "str"
    if converter not in {"str", "int", "float", "uuid", "path"}:
        raise ValueError(f"unsupported path converter: {converter}")
    return name, converter


class _Node:
    __slots__=("static","param","param_name","param_converter","handler")
    def __init__(self):
        self.static={}
        self.param=None
        self.param_name=None
        self.param_converter="str"
        self.handler=None

class Router:
    def __init__(self): self._trees={}
    def add(self, method:str, path:str, handler:Callable):
        method=method.upper()
        if not path.startswith("/"):
            raise ValueError("path must start with /")
        root=self._trees.setdefault(method, _Node())
        node=root
        segments = [s for s in path.split("/") if s]
        for idx, seg in enumerate(segments):
            if seg.startswith("{") and seg.endswith("}"):
                name, converter = _parse_param_segment(seg)
                if converter == "path" and idx != len(segments) - 1:
                    raise ValueError("path converter must be the final segment")
                if node.param is None:
                    node.param=_Node()
                    node.param_name=name
                    node.param_converter=converter
                elif node.param_name != name or node.param_converter != converter:
                    raise ValueError(f"conflicting dynamic route segment: {seg}")
                node=node.param
            else:
                node=node.static.setdefault(seg, _Node())
        if node.handler is not None:
            raise ValueError(f"duplicate route: {method} {path}")
        node.handler=handler

    def match(self, method:str, path:str)->Optional[Match]:
        root=self._trees.get(method.upper())
        if root is None:
            return None
        node=root
        params={}
        segments=[s for s in path.split("/") if s]
        i=0
        while i < len(segments):
            seg = segments[i]
            nxt=node.static.get(seg)
            if nxt is not None:
                node=nxt
                i += 1
                continue
            if node.param is not None:
                converter = node.param_converter or "str"
                if converter == "path":
                    params[node.param_name or "param"] = "/".join(segments[i:])
                    node = node.param
                    i = len(segments)
                    continue
                if not _is_valid_converter_value(converter, seg):
                    return None
                params[node.param_name or "param"]=seg
                node=node.param
                i += 1
                continue
            return None
        if node.handler is None:
            return None
        return Match(handler=node.handler, params=params)
