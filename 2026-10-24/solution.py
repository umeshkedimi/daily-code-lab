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
    for turn in context:
        match = FACT_RE.search(turn)
        if match:
            return match.group(1)
    return None


def truncate_oldest(turns: List[str], budget: int) -> List[str]:
    """Keep the most recent turns that fit within `budget`; drop the rest."""
    kept: List[str] = []
    used = 0
    for turn in reversed(turns):
        cost = count_tokens(turn)
        if used + cost > budget:
            break
        kept.insert(0, turn)
        used += cost
    return kept


def _old_fact_summary(old_turns: List[str], budget: int) -> str:
    """Fact-pattern clauses from `old_turns`, most-recently-dropped first,
    kept as long as they fit within `budget`; stops at the first clause
    that doesn't fit (older clauses are never skipped past)."""
    clauses = []
    for turn in reversed(old_turns):
        match = FACT_RE.search(turn)
        if match:
            clauses.append(match.group(0))

    kept = []
    used = 0
    for clause in clauses:
        cost = count_tokens(clause)
        if used + cost > budget:
            break
        kept.append(clause)
        used += cost
    return "; ".join(kept)


def summarize_oldest(turns: List[str], budget: int) -> List[str]:
    """Like `truncate_oldest`, but the dropped turns are reduced to a
    single prepended summary turn of their fact-pattern clauses
    (most-recently-dropped first), filling whatever budget is left over
    after the recent window.
    """
    recent: List[str] = []
    used = 0
    cutoff = len(turns)
    for turn in reversed(turns):
        cost = count_tokens(turn)
        if used + cost > budget:
            break
        recent.insert(0, turn)
        used += cost
        cutoff -= 1

    old = turns[:cutoff]
    remaining = budget - used
    if not old or remaining <= 0:
        return recent

    summary = _old_fact_summary(old, remaining)
    return ([summary] if summary else []) + recent


def importance_weighted(turns: List[str], budget: int) -> List[str]:
    """Retain turns importance-first (fact-pattern match), recency second,
    within `budget` -- trying every candidate in priority order so a
    cheaper, lower-priority turn can still claim leftover room.
    """
    indexed = list(enumerate(turns))
    ranked = sorted(indexed, key=lambda pair: (FACT_RE.search(pair[1]) is None, -pair[0]))

    selected = set()
    used = 0
    for index, turn in ranked:
        cost = count_tokens(turn)
        if used + cost > budget:
            continue
        selected.add(index)
        used += cost

    return [turns[i] for i in sorted(selected)]
