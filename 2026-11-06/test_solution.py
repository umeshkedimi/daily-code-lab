import pytest

from solution import (
    TimingOracle,
    constant_time_compare,
    naive_compare,
    recover_secret,
)

SECRET = b"4817"  # ASCII digits; byte values 52,56,49,55
ALPHABET = b"0123456789"  # byte values 48-57; never includes 0 (the filler)


# --- naive_compare -------------------------------------------------------


def test_naive_compare_matches_and_counts_full_length():
    assert naive_compare(b"abcd", b"abcd") == (True, 4)


def test_naive_compare_stops_at_first_mismatch():
    assert naive_compare(b"abcd", b"abXd") == (False, 3)


def test_naive_compare_mismatch_at_first_byte_costs_one():
    assert naive_compare(b"Xbcd", b"abcd") == (False, 1)


def test_naive_compare_raises_on_length_mismatch():
    with pytest.raises(ValueError):
        naive_compare(b"abc", b"ab")


# --- constant_time_compare ------------------------------------------------


def test_constant_time_compare_matches_full_length():
    assert constant_time_compare(b"abcd", b"abcd") == (True, 4)


def test_constant_time_compare_mismatch_still_full_length():
    # unlike naive_compare, every mismatch position costs the same: len(a).
    assert constant_time_compare(b"Xbcd", b"abcd") == (False, 4)
    assert constant_time_compare(b"abcX", b"abcd") == (False, 4)


def test_constant_time_compare_raises_on_length_mismatch():
    with pytest.raises(ValueError):
        constant_time_compare(b"abc", b"ab")


# --- TimingOracle: exposes only ops, never the match result -------------


def test_timing_oracle_returns_plain_ops_count():
    oracle = TimingOracle(b"abcd", naive_compare)
    assert oracle.probe(b"abcd") == 4
    assert oracle.probe(b"Xbcd") == 1


# --- recover_secret: the actual exploit, measured ------------------------


def test_recover_secret_against_naive_recovers_all_but_last_byte():
    oracle = TimingOracle(SECRET, naive_compare)
    recovered, undetermined = recover_secret(oracle, length=4, alphabet=ALPHABET)
    assert undetermined == {3}
    assert recovered[:3] == SECRET[:3]


def test_recover_secret_against_constant_time_recovers_nothing():
    oracle = TimingOracle(SECRET, constant_time_compare)
    recovered, undetermined = recover_secret(oracle, length=4, alphabet=ALPHABET)
    assert undetermined == {0, 1, 2, 3}


def test_recovered_prefix_plus_tiny_brute_force_finds_the_real_secret():
    # once undetermined == {3}, finishing needs only len(alphabet) tries
    # against the real comparison directly -- no oracle, no timing, just
    # a handful of ordinary attempts, because the search space left is
    # len(alphabet), not len(alphabet) ** length.
    oracle = TimingOracle(SECRET, naive_compare)
    recovered, undetermined = recover_secret(oracle, length=4, alphabet=ALPHABET)
    assert undetermined == {3}

    candidates = [bytes(recovered[:3]) + bytes([b]) for b in ALPHABET]
    matches = [c for c in candidates if naive_compare(c, SECRET)[0]]
    assert matches == [SECRET]


def test_measured_search_space_collapse_naive_vs_constant_time():
    length, alphabet_size = 4, len(ALPHABET)
    full_space = alphabet_size ** length  # 10,000

    _, naive_undetermined = recover_secret(
        TimingOracle(SECRET, naive_compare), length, ALPHABET
    )
    _, ct_undetermined = recover_secret(
        TimingOracle(SECRET, constant_time_compare), length, ALPHABET
    )

    naive_residual = alphabet_size ** len(naive_undetermined)
    ct_residual = alphabet_size ** len(ct_undetermined)

    assert naive_residual == alphabet_size  # == 10: exactly one digit left unknown
    assert ct_residual == full_space  # == 10,000: no reduction at all
    assert naive_residual < full_space / 100  # at least a 100x collapse, not a sliver
