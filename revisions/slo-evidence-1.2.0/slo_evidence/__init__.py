"""Versioned, dependency-light evidence checking. No SLO approval decisions."""

from .core import (
    VERSION, Policy, summarize, statistical_value, legacy_route,
    scope_hash, decide, derive_child, file_hash, canonical_hash,
)

__all__ = ["VERSION", "Policy", "summarize", "statistical_value", "legacy_route",
           "scope_hash", "decide", "derive_child", "file_hash", "canonical_hash"]
