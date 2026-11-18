"""Token allocation strategies across competing context pools (docs,
history, scratch) -- a complement to 2026-10-24's single-sequence
truncation strategies, for the case where several separate pools
compete for one shared budget instead of one growing conversation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

FACT_RE = re.compile(r"the deployment secret is (\w+)", re.IGNORECASE)


@dataclass(frozen=True)
class Candidate:
    text: str
    score: float


def count_tokens(text: str) -> int:
    return len(text.split())


def answer(context: List[str]) -> Optional[str]:
    for line in context:
        match = FACT_RE.search(line)
        if match:
            return match.group(1)
    return None


def _take_prefix(candidates: List[Candidate], sub_budget: int):
    selected: List[Candidate] = []
    used = 0
    for candidate in candidates:
        cost = count_tokens(candidate.text)
        if used + cost > sub_budget:
            break
        selected.append(candidate)
        used += cost
    return selected, used


def fixed_proportion_allocate(
    pools: Dict[str, List[Candidate]], budget: int, proportions: Dict[str, float]
) -> Dict[str, List[Candidate]]:
    """Each pool gets a fixed int(budget * proportions[name]) sub-budget,
    filled from the front of its own list. Leftover sub-budget never
    moves to another pool.
    """
    out: Dict[str, List[Candidate]] = {}
    for name, candidates in pools.items():
        sub_budget = int(budget * proportions[name])
        selected, _ = _take_prefix(candidates, sub_budget)
        out[name] = selected
    return out


def priority_ordered_allocate(
    pools: Dict[str, List[Candidate]], budget: int, order: List[str]
) -> Dict[str, List[Candidate]]:
    """Walks order; each pool in turn gets whatever remains of budget,
    filled from the front of its own list. A pool late in order can
    receive nothing.
    """
    out: Dict[str, List[Candidate]] = {name: [] for name in pools}
    remaining = budget
    for name in order:
        selected, used = _take_prefix(pools[name], remaining)
        out[name] = selected
        remaining -= used
    return out


def greedy_marginal_allocate(
    pools: Dict[str, List[Candidate]], budget: int
) -> Dict[str, List[Candidate]]:
    """Flattens every pool's items, ranks by score descending (ties:
    pool position then item position), fills from the top, skipping
    (not stopping at) any item that doesn't fit, to keep using
    remaining budget on the next-best candidate.
    """
    flat = []
    for pool_idx, name in enumerate(pools):
        for item_idx, candidate in enumerate(pools[name]):
            flat.append((candidate.score, pool_idx, item_idx, name))
    flat.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))

    selected_indices: Dict[str, set] = {name: set() for name in pools}
    used = 0
    for _, pool_idx, item_idx, name in flat:
        cost = count_tokens(pools[name][item_idx].text)
        if used + cost > budget:
            continue
        selected_indices[name].add(item_idx)
        used += cost

    return {
        name: [candidate for idx, candidate in enumerate(pools[name]) if idx in selected_indices[name]]
        for name in pools
    }


def assemble(allocation: Dict[str, List[Candidate]], pool_order: List[str]) -> List[str]:
    """Flattens an allocation into one list of strings, pool by pool in
    pool_order, each pool's own selected items in original order.
    """
    lines: List[str] = []
    for name in pool_order:
        for candidate in allocation[name]:
            lines.append(candidate.text)
    return lines
