"""Redact credential-shaped text before it reaches reports or clients."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# Keep these deliberately credential-format-specific so ordinary complaint text
# such as "the token expired" remains useful evidence.
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(?:"
        r"xox[baprs]-[a-z0-9-]{8,}|"
        r"gh[pousr]_[a-z0-9_]{10,}|"
        r"github_pat_[a-z0-9_]{10,}|"
        r"lin_api_[a-z0-9_-]{8,}|"
        r"sk-(?:proj-)?[a-z0-9_-]{12,}"
        r")\b"
    ),
    re.compile(
        r"(?i)\bbearer\s+[a-z0-9._~+/-]{12,}={0,2}\b"
    ),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
        r"password|secret)\s*[:=]\s*[\"']?[a-z0-9._~+/-]{12,}"
        r"={0,2}[\"']?"
    ),
    re.compile(
        r"\beyJ[a-zA-Z0-9_-]{8,}\."
        r"[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b"
    ),
)


def redact_text(value: str) -> str:
    """Replace credential-shaped substrings without interpreting other text."""

    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def redact_data(value: Any) -> Any:
    """Recursively redact string values in JSON-like report data."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item) for item in value)
    return value
