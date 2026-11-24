"""Path traversal (zip-slip) defense -- measures, against a fixed
attack corpus, exactly how many escapes each of three join
implementations actually allows, rather than asserting "unsafe" or
"safe" from inspection alone."""

from __future__ import annotations

import os
from pathlib import Path


class PathTraversalError(Exception):
    pass


def naive_join(base_dir: str, user_path: str) -> str:
    """No containment check at all -- what most code looks like
    before anyone thinks about this.
    """
    raise NotImplementedError


def naive_string_check_join(base_dir: str, user_path: str) -> str:
    """Rejects if '..' appears anywhere in user_path, otherwise
    behaves like naive_join. The fix someone reaches for first.
    """
    raise NotImplementedError


def safe_join(base_dir: str, user_path: str) -> str:
    """Resolves both paths fully (following symlinks), then checks
    containment on the resolved result -- never on the raw string.
    """
    raise NotImplementedError


def escapes_base(base_dir: str, candidate_path: str) -> bool:
    """Measurement helper: resolves both paths and reports whether
    candidate_path ends up outside the resolved base_dir.
    """
    raise NotImplementedError
