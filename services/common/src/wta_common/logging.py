"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from typing import Any

import structlog

_SENSITIVE = re.compile(r"(key|token|secret|authorization|password|cookie)", re.IGNORECASE)


def _redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for k in list(event_dict):
        if _SENSITIVE.search(k):
            event_dict[k] = "***"
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
