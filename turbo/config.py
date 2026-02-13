from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _parse_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


@dataclass(slots=True)
class TurboSettings:
    request_timeout: float = 10.0
    max_body_bytes: int = 1_000_000
    max_concurrency: int = 200
    multipart_max_fields: int = 1000
    multipart_max_file_size: int = 10_000_000
    multipart_spool_threshold: int = 1_000_000
    multipart_max_part_size: int = 10_000_000
    redirect_slashes: bool = True
    redirect_status_code: int = 307
    openapi_url: Optional[str] = "/openapi.json"
    docs_url: Optional[str] = "/docs"
    redoc_url: Optional[str] = "/redoc"
    shutdown_drain_timeout: float = 10.0
    title: str = "TurboAPI"
    version: str = "0.1.0"

    @classmethod
    def from_env(cls, *, prefix: str = "TURBO_"):
        env = os.environ
        def get(name: str):
            return env.get(prefix + name)

        raw_openapi_url = get("OPENAPI_URL")
        raw_docs_url = get("DOCS_URL")
        raw_redoc_url = get("REDOC_URL")
        openapi_url = "/openapi.json" if raw_openapi_url is None else (None if raw_openapi_url == "" else raw_openapi_url)
        docs_url = "/docs" if raw_docs_url is None else (None if raw_docs_url == "" else raw_docs_url)
        redoc_url = "/redoc" if raw_redoc_url is None else (None if raw_redoc_url == "" else raw_redoc_url)

        return cls(
            request_timeout=float(get("REQUEST_TIMEOUT") or 10.0),
            max_body_bytes=int(get("MAX_BODY_BYTES") or 1_000_000),
            max_concurrency=int(get("MAX_CONCURRENCY") or 200),
            multipart_max_fields=int(get("MULTIPART_MAX_FIELDS") or 1000),
            multipart_max_file_size=int(get("MULTIPART_MAX_FILE_SIZE") or 10_000_000),
            multipart_spool_threshold=int(get("MULTIPART_SPOOL_THRESHOLD") or 1_000_000),
            multipart_max_part_size=int(get("MULTIPART_MAX_PART_SIZE") or 10_000_000),
            redirect_slashes=_parse_bool(get("REDIRECT_SLASHES"), True),
            redirect_status_code=int(get("REDIRECT_STATUS_CODE") or 307),
            openapi_url=openapi_url,
            docs_url=docs_url,
            redoc_url=redoc_url,
            shutdown_drain_timeout=float(get("SHUTDOWN_DRAIN_TIMEOUT") or 10.0),
            title=get("TITLE") or "TurboAPI",
            version=get("VERSION") or "0.1.0",
        )

    def to_turbo_kwargs(self) -> dict:
        return {
            "request_timeout": self.request_timeout,
            "max_body_bytes": self.max_body_bytes,
            "max_concurrency": self.max_concurrency,
            "multipart_max_fields": self.multipart_max_fields,
            "multipart_max_file_size": self.multipart_max_file_size,
            "multipart_spool_threshold": self.multipart_spool_threshold,
            "multipart_max_part_size": self.multipart_max_part_size,
            "redirect_slashes": self.redirect_slashes,
            "redirect_status_code": self.redirect_status_code,
            "openapi_url": self.openapi_url,
            "docs_url": self.docs_url,
            "redoc_url": self.redoc_url,
            "shutdown_drain_timeout": self.shutdown_drain_timeout,
            "title": self.title,
            "version": self.version,
        }
