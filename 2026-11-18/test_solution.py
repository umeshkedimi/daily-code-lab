from solution import (
    Candidate,
    answer,
    assemble,
    count_tokens,
    fixed_proportion_allocate,
    greedy_marginal_allocate,
    priority_ordered_allocate,
)

DECOY = "filler filler filler filler filler filler"  # 6 tokens, exactly
FACT = "the deployment secret is ZX9Q7"  # 5 tokens, exactly
POOL_ORDER = ["docs", "history", "scratch"]
BUDGET = 40


def C(text, score):
    return Candidate(text, score)


# --- primitives -----------------------------------------------------------


def test_count_tokens_counts_words():
    assert count_tokens(DECOY) == 6
    assert count_tokens(FACT) == 5


def test_answer_finds_the_fact_in_any_line():
    assert answer([DECOY, DECOY, FACT, DECOY]) == "ZX9Q7"


def test_answer_returns_none_when_absent():
    assert answer([DECOY, DECOY, DECOY]) is None


# --- Scenario A: fact buried in the pool with the biggest fixed share ----
# fixed_proportion's rigid 30%-of-40=12-token cap cuts off before position 4;
# priority_ordered (docs first, no cap) and greedy (fact scores high enough)
# both reach it.

SCENARIO_A = {
    "docs": [C(DECOY, 19), C(DECOY, 18), C(DECOY, 17), C(FACT, 16), C(DECOY, 15)],
    "history": [C(DECOY, 14), C(DECOY, 13), C(DECOY, 12), C(DECOY, 11), C(DECOY, 10)],
    "scratch": [C(DECOY, 9), C(DECOY, 8), C(DECOY, 7), C(DECOY, 6), C(DECOY, 5)],
}
PROPORTIONS_A = {"docs": 0.3, "history": 0.4, "scratch": 0.3}


def test_scenario_a_fixed_proportion_fails_rigid_share_too_small():
    alloc = fixed_proportion_allocate(SCENARIO_A, BUDGET, PROPORTIONS_A)
    assert answer(assemble(alloc, POOL_ORDER)) is None


def test_scenario_a_priority_ordered_succeeds_no_cap_on_first_pool():
    alloc = priority_ordered_allocate(SCENARIO_A, BUDGET, POOL_ORDER)
    assert answer(assemble(alloc, POOL_ORDER)) == "ZX9Q7"


def test_scenario_a_greedy_marginal_succeeds_fact_scores_high_enough():
    alloc = greedy_marginal_allocate(SCENARIO_A, BUDGET)
    assert answer(assemble(alloc, POOL_ORDER)) == "ZX9Q7"


# --- Scenario B: fact first in the lowest-priority pool ------------------
# priority_ordered drains the budget on docs+history before scratch gets
# anything; fixed_proportion (fact is first in its pool's own sub-budget)
# and greedy (fact's score beats most decoys) both still reach it.

SCENARIO_B = {
    "docs": [C(DECOY, 25), C(DECOY, 24), C(DECOY, 23), C(DECOY, 22), C(DECOY, 21)],
    "history": [C(DECOY, 20), C(DECOY, 19), C(DECOY, 18), C(DECOY, 17), C(DECOY, 16)],
    "scratch": [C(FACT, 21.5), C(DECOY, 14), C(DECOY, 13), C(DECOY, 12), C(DECOY, 11)],
}
PROPORTIONS_B = {"docs": 0.3, "history": 0.4, "scratch": 0.3}


def test_scenario_b_fixed_proportion_succeeds_fact_is_first_in_its_pool():
    alloc = fixed_proportion_allocate(SCENARIO_B, BUDGET, PROPORTIONS_B)
    assert answer(assemble(alloc, POOL_ORDER)) == "ZX9Q7"


def test_scenario_b_priority_ordered_fails_scratch_starved_by_order():
    alloc = priority_ordered_allocate(SCENARIO_B, BUDGET, POOL_ORDER)
    assert answer(assemble(alloc, POOL_ORDER)) is None


def test_scenario_b_greedy_marginal_succeeds_fact_outranks_most_decoys():
    alloc = greedy_marginal_allocate(SCENARIO_B, BUDGET)
    assert answer(assemble(alloc, POOL_ORDER)) == "ZX9Q7"


# --- Scenario C: decoys with inflated scores crowd out the fact ----------
# greedy ranks globally by score and the inflated decoys win; fixed_proportion
# and priority_ordered never look at score at all, so the fact (near the
# front of its own pool) survives either way.

SCENARIO_C = {
    "docs": [C(DECOY, 30), C(DECOY, 29), C(FACT, 10), C(DECOY, 9), C(DECOY, 8)],
    "history": [C(DECOY, 28), C(DECOY, 27), C(DECOY, 7), C(DECOY, 6), C(DECOY, 5)],
    "scratch": [C(DECOY, 26), C(DECOY, 25), C(DECOY, 4), C(DECOY, 3), C(DECOY, 2)],
}
PROPORTIONS_C = {"docs": 0.5, "history": 0.25, "scratch": 0.25}


def test_scenario_c_fixed_proportion_succeeds_score_irrelevant_to_it():
    alloc = fixed_proportion_allocate(SCENARIO_C, BUDGET, PROPORTIONS_C)
    assert answer(assemble(alloc, POOL_ORDER)) == "ZX9Q7"


def test_scenario_c_priority_ordered_succeeds_score_irrelevant_to_it():
    alloc = priority_ordered_allocate(SCENARIO_C, BUDGET, POOL_ORDER)
    assert answer(assemble(alloc, POOL_ORDER)) == "ZX9Q7"


def test_scenario_c_greedy_marginal_fails_inflated_decoys_win_globally():
    alloc = greedy_marginal_allocate(SCENARIO_C, BUDGET)
    assert answer(assemble(alloc, POOL_ORDER)) is None


# --- sanity checks: the budget is what's doing the work, not a rigged gap -


def test_generous_budget_all_three_strategies_recover_the_fact():
    generous_budget = 100
    results = []
    results.append(
        answer(assemble(fixed_proportion_allocate(SCENARIO_A, generous_budget, PROPORTIONS_A), POOL_ORDER))
    )
    results.append(
        answer(assemble(priority_ordered_allocate(SCENARIO_A, generous_budget, POOL_ORDER), POOL_ORDER))
    )
    results.append(answer(assemble(greedy_marginal_allocate(SCENARIO_A, generous_budget), POOL_ORDER)))
    assert results == ["ZX9Q7", "ZX9Q7", "ZX9Q7"]


def test_starved_budget_all_three_strategies_fail_every_scenario():
    starved_budget = 4  # less than FACT's own 5-token cost
    for scenario, proportions in ((SCENARIO_A, PROPORTIONS_A), (SCENARIO_B, PROPORTIONS_B), (SCENARIO_C, PROPORTIONS_C)):
        assert answer(assemble(fixed_proportion_allocate(scenario, starved_budget, proportions), POOL_ORDER)) is None
        assert answer(assemble(priority_ordered_allocate(scenario, starved_budget, POOL_ORDER), POOL_ORDER)) is None
        assert answer(assemble(greedy_marginal_allocate(scenario, starved_budget), POOL_ORDER)) is None


# --- structural: greedy skips a non-fitting item, it doesn't stop there --


def test_greedy_marginal_skips_a_non_fitting_item_instead_of_stopping():
    # BIG (score 100, 8 tokens) is ranked first and fits within budget 10,
    # using 8. The next-ranked item would need 5 more (13 > 10) and must be
    # skipped -- but a cheap, lower-ranked item that still fits in the 2
    # tokens left over must still be picked up. A greedy that stops at the
    # first non-fitting item instead of skipping it would miss this one
    # entirely, even though it fits.
    big = "big big big big big big big big"
    tiny = "tiny tiny"
    pools = {
        "a": [Candidate(big, 100), Candidate("mid mid mid mid mid", 90)],
        "b": [Candidate(tiny, 10)],
    }
    alloc = greedy_marginal_allocate(pools, 10)
    assert alloc["a"] == [Candidate(big, 100)]
    assert alloc["b"] == [Candidate(tiny, 10)]


# --- structural: fixed_proportion wastes leftover sub-budget silently -----


def test_fixed_proportion_leftover_subbudget_never_reaches_another_pool():
    # docs gets int(40*0.3)=12 tokens: exactly two 6-token decoys, no slack
    # left for a 6-token third item. Confirm the 0 leftover tokens from
    # docs's exact-fit sub-budget don't somehow inflate another pool's share.
    alloc = fixed_proportion_allocate(SCENARIO_A, BUDGET, PROPORTIONS_A)
    assert len(alloc["docs"]) == 2
    used_by_docs = sum(count_tokens(c.text) for c in alloc["docs"])
    assert used_by_docs == 12
