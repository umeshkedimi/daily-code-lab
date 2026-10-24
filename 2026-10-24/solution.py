"""Context-window truncation/retention strategies for long conversations,
measured against a fact-recall task as the fact gets buried deeper."""

from __future__ import annotations

import re
from typing import List, Optional

FACT_RE = re.compile(r"my account id is (\d+)", re.IGNORECASE)


def count_tokens(text: str) -> int:
    """Whitespace-split word count -- the stand-in for a tokenizer."""
    return len(text.split())


def answer(context: List[str]) -> Optional[str]:
    """Search `context` turns, in order, for the fact pattern.

    Returns the first captured account id found, or None if no turn in
    this context matches. This is the simulated "LLM": it can only
    answer from what it's given, and only recognize the one phrasing
    its pattern knows.
    """
    raise NotImplementedError


def truncate_oldest(turns: List[str], budget: int) -> List[str]:
    """Keep the most recent turns that fit within `budget`; drop the rest."""
    raise NotImplementedError


def summarize_oldest(turns: List[str], budget: int) -> List[str]:
    """Like `truncate_oldest`, but the dropped turns are reduced to a
    single prepended summary turn of their fact-pattern clauses
    (most-recently-dropped first), filling whatever budget is left over
    after the recent window.
    """
    raise NotImplementedError


def importance_weighted(turns: List[str], budget: int) -> List[str]:
    """Retain turns importance-first (fact-pattern match), recency second,
    within `budget` -- trying every candidate in priority order so a
    cheaper, lower-priority turn can still claim leftover room.
    """
    raise NotImplementedError
