import random

import pytest

from solution import NaiveWordSet, Trie

WORDS = ["cat", "car", "care", "cart", "card", "careful", "carbon", "dog", "do", "done", "door", "dorm"]


# --- basic behaviour -------------------------------------------------------------


def test_empty_trie_has_nothing():
    t = Trie()
    assert len(t) == 0
    assert "anything" not in t
    assert not t.starts_with("a")
    assert t.words_with_prefix("a") == []
    assert t.count_with_prefix("") == 0


def test_insert_and_contains_exact_word():
    t = Trie()
    t.insert("cat")
    assert "cat" in t
    assert "ca" not in t  # a prefix of a stored word is not itself stored
    assert "cats" not in t  # a superstring is not stored either


def test_insert_returns_whether_it_was_new():
    t = Trie()
    assert t.insert("cat") is True
    assert t.insert("cat") is False  # already present
    assert len(t) == 1


def test_insert_rejects_empty_string():
    t = Trie()
    with pytest.raises(ValueError):
        t.insert("")


def test_one_word_being_a_prefix_of_another_is_handled_correctly():
    t = Trie(["car", "carpet"])
    assert "car" in t and "carpet" in t
    assert t.starts_with("car") and t.starts_with("carp")
    assert not t.starts_with("carpets")
    assert sorted(t.words_with_prefix("car")) == ["car", "carpet"]


def test_starts_with_is_true_for_a_stored_words_own_prefixes():
    t = Trie(["hello"])
    for i in range(1, 6):
        assert t.starts_with("hello"[:i])
    assert not t.starts_with("hellox")


def test_words_with_prefix_returns_sorted_results():
    t = Trie(WORDS)
    assert t.words_with_prefix("car") == sorted(w for w in WORDS if w.startswith("car"))
    assert t.words_with_prefix("") == sorted(WORDS)  # empty prefix matches everything


def test_words_with_prefix_respects_limit_without_just_truncating():
    t = Trie(["a", "aa", "aaa", "aab", "ab"])
    assert t.words_with_prefix("a", limit=2) == ["a", "aa"]  # first two in sorted order
    assert t.words_with_prefix("a", limit=0) == []
    assert t.words_with_prefix("a", limit=100) == t.words_with_prefix("a")  # limit above the true count is a no-op


def test_count_with_prefix_matches_the_number_of_matching_words():
    t = Trie(WORDS)
    for prefix in ("", "c", "ca", "car", "d", "do", "z"):
        assert t.count_with_prefix(prefix) == len([w for w in WORDS if w.startswith(prefix)])


# --- deletion, including pruning ----------------------------------------------------------


def test_delete_removes_a_word_and_reports_whether_it_existed():
    t = Trie(["cat"])
    assert t.delete("cat") is True
    assert "cat" not in t
    assert len(t) == 0
    assert t.delete("cat") is False  # already gone


def test_delete_of_a_word_that_is_a_prefix_of_another_keeps_the_other():
    t = Trie(["car", "carpet"])
    t.delete("car")
    assert "car" not in t
    assert "carpet" in t
    assert t.starts_with("car")  # carpet still starts with "car"


def test_delete_of_a_word_whose_prefix_is_shared_does_not_touch_the_sibling():
    t = Trie(["cat", "car"])
    t.delete("cat")
    assert "cat" not in t and "car" in t
    assert t.starts_with("ca")


def test_delete_prunes_now_dead_branches():
    t = Trie(["cat"])
    t.delete("cat")
    # internal check: the root should have no children left, not just a cleared is_word flag deep in the tree
    assert t._root.children == {}


def test_delete_does_not_over_prune_a_shared_branch():
    t = Trie(["cat", "cats"])
    t.delete("cat")
    assert "cat" not in t and "cats" in t
    node = t._find_node("cat")
    assert node is not None and not node.is_word and node.children  # the node survives: "cats" still needs it


def test_delete_nonexistent_word_is_a_no_op():
    t = Trie(["cat"])
    assert t.delete("dog") is False
    assert t.delete("ca") is False  # a prefix that was never itself inserted as a word
    assert len(t) == 1 and "cat" in t


def test_word_count_is_maintained_through_inserts_and_deletes():
    t = Trie()
    t.insert("car")
    t.insert("cart")
    t.insert("care")
    assert t.count_with_prefix("car") == 3
    t.delete("cart")
    assert t.count_with_prefix("car") == 2
    assert t.count_with_prefix("") == len(t) == 2


# --- differential testing against a naive word list -------------------------------------------------


@pytest.mark.parametrize("seed", range(15))
def test_matches_naive_word_set_under_randomized_operations(seed):
    rng = random.Random(seed)
    alphabet = "abc"
    trie: Trie = Trie()
    naive: NaiveWordSet = NaiveWordSet()
    for _ in range(150):
        w = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 4)))
        op = rng.choice(["insert", "insert", "delete", "contains", "prefix", "count"])
        if op == "insert":
            naive_had = w in naive._words
            assert trie.insert(w) == (not naive_had)
            if not naive_had:
                naive._words.append(w)
        elif op == "delete":
            naive_had = w in naive._words
            assert trie.delete(w) == naive_had
            if naive_had:
                naive._words.remove(w)
        elif op == "contains":
            assert (w in trie) == (w in naive)
        elif op == "count":
            prefix = w[: rng.randint(0, len(w))]
            assert trie.count_with_prefix(prefix) == len([x for x in naive._words if x.startswith(prefix)])
        else:
            prefix = w[: rng.randint(0, len(w))]
            assert sorted(trie.words_with_prefix(prefix)) == naive.words_with_prefix(prefix)
        assert len(trie) == len(naive)


def test_naive_word_set_dedupes_on_construction():
    n = NaiveWordSet(["a", "a", "b"])
    assert len(n) == 2


# --- realistic-scale correctness -----------------------------------------------------------------


def test_matches_naive_on_a_larger_realistic_word_list():
    words = [f"user{i}" for i in range(0, 5000, 3)]  # every 3rd, so lookups for the others are genuine misses
    trie = Trie(words)
    naive = NaiveWordSet(words)
    assert len(trie) == len(naive) == len(words)
    for i in range(0, 5000, 7):
        key = f"user{i}"
        assert (key in trie) == (key in naive)
    assert sorted(trie.words_with_prefix("user12")) == naive.words_with_prefix("user12")


def test_query_cost_does_not_grow_with_the_number_of_stored_words():
    """Not a strict timing assertion (flaky by nature) -- a coarse, generous
    sanity check that 40x more stored words does not make lookups 40x slower."""
    import time

    small = Trie([f"item{i}" for i in range(1_000)])
    large = Trie([f"item{i}" for i in range(40_000)])
    probe = "item999999"  # a guaranteed miss in both, same length class

    t0 = time.perf_counter()
    for _ in range(3000):
        probe in small
    small_time = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(3000):
        probe in large
    large_time = time.perf_counter() - t0

    assert large_time < small_time * 5  # nowhere near the 40x a linear scan would show
