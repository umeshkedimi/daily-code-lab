from solution import (
    answer,
    count_tokens,
    importance_weighted,
    summarize_oldest,
    truncate_oldest,
)

TRUE_VALUE = "48213"


def filler_turn(i: int) -> str:
    return f"Turn {i}: just chatting about topic {i}, nothing important here today."


def fact_turn(value: str) -> str:
    return f"By the way, my account id is {value}."


def adversarial_fact_turn(value: str) -> str:
    return f"FYI: {value} is the account id you should use."


def build_history(fact_text: str, depth: int, before: int = 5) -> list:
    """`before` filler turns, then the fact, then `depth` filler turns."""
    history = [filler_turn(i) for i in range(before)]
    history.append(fact_text)
    history += [filler_turn(before + 1 + i) for i in range(depth)]
    return history


# --- count_tokens / answer, independent of budget ----------------------


def test_count_tokens_basic():
    assert count_tokens("one two three") == 3
    assert count_tokens("") == 0


def test_answer_extracts_fact_when_present():
    context = [filler_turn(0), fact_turn(TRUE_VALUE), filler_turn(1)]
    assert answer(context) == TRUE_VALUE


def test_answer_returns_none_when_fact_absent_from_context():
    context = [filler_turn(i) for i in range(3)]
    assert answer(context) is None


# --- truncate_oldest -----------------------------------------------------


def test_truncate_oldest_keeps_most_recent_within_budget():
    turns = [filler_turn(i) for i in range(5)]
    per_turn = count_tokens(turns[0])
    kept = truncate_oldest(turns, per_turn * 2)
    assert kept == turns[-2:]


def test_truncate_oldest_drops_oldest_first():
    turns = [filler_turn(i) for i in range(5)]
    per_turn = count_tokens(turns[0])
    kept = truncate_oldest(turns, per_turn * 3)
    assert kept == turns[-3:]


def test_truncate_oldest_has_a_sharp_depth_cliff():
    per_turn = count_tokens(filler_turn(0))
    budget = per_turn * 3
    results = []
    for depth in range(8):
        history = build_history(fact_turn(TRUE_VALUE), depth=depth, before=5)
        kept = truncate_oldest(history, budget)
        results.append(answer(kept) == TRUE_VALUE)

    # a step function: once it starts failing, it never recovers, and it
    # does fail somewhere in this range (the fact eventually falls outside
    # a fixed-size recent window as depth grows).
    first_false = next((i for i, ok in enumerate(results) if not ok), None)
    assert first_false is not None
    assert all(results[:first_false])
    assert not any(results[first_false:])


# --- summarize_oldest: extends recall, but has its own crowding limit ---


def test_summarize_oldest_extends_recall_beyond_naive_cliff():
    filler_tok = count_tokens(filler_turn(0))
    fact_clause_tok = count_tokens(f"my account id is {TRUE_VALUE}")
    # room for exactly the 3-turn recent window plus one leftover fact clause
    budget = filler_tok * 3 + fact_clause_tok
    depth = 6  # already past truncate_oldest's cliff at this budget

    history = build_history(fact_turn(TRUE_VALUE), depth=depth, before=5)
    assert answer(truncate_oldest(history, budget)) != TRUE_VALUE
    assert answer(summarize_oldest(history, budget)) == TRUE_VALUE


def test_summarize_oldest_eventually_fails_when_newer_decoys_crowd_it_out():
    filler_tok = count_tokens(filler_turn(0))
    fact_clause_tok = count_tokens(f"my account id is {TRUE_VALUE}")
    # leftover budget fits exactly one fact clause, not two -- a more
    # recently dropped decoy fact wins the only slot in the summary.
    budget = filler_tok * 3 + fact_clause_tok

    history = [filler_turn(i) for i in range(5)]
    history.append(fact_turn(TRUE_VALUE))        # real fact: oldest fact-shaped line
    history.append(fact_turn("11111"))            # decoy, more recent than the real fact
    history.append(fact_turn("22222"))             # decoy, most recent fact-shaped line
    history += [filler_turn(100 + i) for i in range(3)]  # fills the recent window

    assert answer(summarize_oldest(history, budget)) != TRUE_VALUE


# --- importance_weighted: no recency decay, but crowds out recent filler


def test_importance_weighted_retains_fact_regardless_of_depth():
    per_turn = count_tokens(filler_turn(0))
    budget = per_turn * 4
    for depth in (0, 5, 20, 100):
        history = build_history(fact_turn(TRUE_VALUE), depth=depth, before=5)
        assert answer(importance_weighted(history, budget)) == TRUE_VALUE


def test_importance_weighted_can_starve_recent_filler_that_truncate_oldest_keeps():
    per_turn = count_tokens(filler_turn(0))
    budget = per_turn * 4
    history = build_history(fact_turn(TRUE_VALUE), depth=10, before=0)

    naive_kept = truncate_oldest(history, budget)
    weighted_kept = importance_weighted(history, budget)

    assert fact_turn(TRUE_VALUE) not in naive_kept
    assert fact_turn(TRUE_VALUE) in weighted_kept
    # importance-weighting spent some of the budget on the old fact, so it
    # must have displaced at least one recent filler turn naive kept.
    assert set(naive_kept) - set(weighted_kept)


# --- the ceiling: retention can't fix an extractor that can't parse it --


def test_adversarial_phrasing_defeats_every_strategy_even_with_unbounded_budget():
    history = build_history(adversarial_fact_turn(TRUE_VALUE), depth=5, before=5)
    huge_budget = sum(count_tokens(t) for t in history) + 1000

    for strategy in (truncate_oldest, summarize_oldest, importance_weighted):
        kept = strategy(history, huge_budget)
        assert kept == history  # nothing was truncated -- full retention
        assert answer(kept) != TRUE_VALUE  # yet still unanswerable
