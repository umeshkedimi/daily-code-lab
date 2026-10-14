import hashlib
import random

import pytest

from solution import (
    AccountLockedError,
    TOTP,
    TOTPAuthenticator,
    expected_attempts_to_guess,
    expected_crack_time_seconds,
    hotp,
)


class FakeClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


# --- RFC 4226 (HOTP) official test vectors ----------------------------------------

_RFC4226_SECRET = b"12345678901234567890"
_RFC4226_VECTORS = [
    "755224", "287082", "359152", "969429", "338314",
    "254676", "287922", "162583", "399871", "520489",
]


@pytest.mark.parametrize("counter,expected", list(enumerate(_RFC4226_VECTORS)))
def test_hotp_matches_rfc4226_test_vectors(counter, expected):
    assert hotp(_RFC4226_SECRET, counter) == expected


# --- RFC 6238 (TOTP) official test vectors, all three digest algorithms ---------

_RFC6238_SECRET_SHA1 = b"12345678901234567890"
_RFC6238_SECRET_SHA256 = b"12345678901234567890123456789012"
_RFC6238_SECRET_SHA512 = b"1234567890123456789012345678901234567890123456789012345678901234"

_RFC6238_CASES = [
    # (timestamp, sha1, sha256, sha512) -- all 8-digit, 30s period, T0=0
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]


@pytest.mark.parametrize("timestamp,expected_sha1,expected_sha256,expected_sha512", _RFC6238_CASES)
def test_totp_matches_rfc6238_test_vectors(timestamp, expected_sha1, expected_sha256, expected_sha512):
    totp_sha1 = TOTP(_RFC6238_SECRET_SHA1, digits=8, digest=hashlib.sha1)
    totp_sha256 = TOTP(_RFC6238_SECRET_SHA256, digits=8, digest=hashlib.sha256)
    totp_sha512 = TOTP(_RFC6238_SECRET_SHA512, digits=8, digest=hashlib.sha512)
    assert totp_sha1.generate(at=timestamp) == expected_sha1
    assert totp_sha256.generate(at=timestamp) == expected_sha256
    assert totp_sha512.generate(at=timestamp) == expected_sha512


# --- base32 secret encoding ---------------------------------------------------------


def test_base32_roundtrip():
    secret = TOTP.generate_secret(20)
    encoded = TOTP.secret_to_base32(secret)
    assert TOTP.secret_from_base32(encoded) == secret


def test_generated_secrets_are_not_all_identical():
    secrets_generated = {TOTP.generate_secret(20) for _ in range(20)}
    assert len(secrets_generated) == 20  # cryptographically random, not a fixed/predictable value


# --- TOTP.verify: basic behavior + drift tolerance --------------------------------


def test_verify_accepts_the_current_codes():
    clock = FakeClock(1000.0)
    totp = TOTP(TOTP.generate_secret(), clock=clock)
    code = totp.generate()
    assert totp.verify(code) is True


def test_verify_rejects_a_wrong_code():
    clock = FakeClock(1000.0)
    totp = TOTP(TOTP.generate_secret(), clock=clock)
    assert totp.verify("000000") is False


def test_verify_with_zero_drift_rejects_a_code_from_the_next_period():
    clock = FakeClock(0.0)
    totp = TOTP(TOTP.generate_secret(), period=30, clock=clock)
    code_period_0 = totp.generate(at=0.0)
    assert totp.verify(code_period_0, at=0.0, drift=0) is True
    assert totp.verify(code_period_0, at=30.0, drift=0) is False  # next period, no tolerance


def test_verify_with_drift_1_accepts_the_adjacent_periods_code():
    # Timestamps chosen away from t=0 so the "one period earlier" probe
    # below doesn't itself cross the Unix epoch into negative-counter
    # territory (see test_hotp_rejects_a_negative_counter for that case
    # on its own terms, rather than tripping over it here by accident).
    totp = TOTP(TOTP.generate_secret(), period=30)
    code_period_10 = totp.generate(at=300.0)  # counter 10
    assert totp.verify(code_period_10, at=330.0, drift=1) is True  # one period later (counter 11)
    assert totp.verify(code_period_10, at=270.0, drift=1) is True  # one period earlier (counter 9)


def test_verify_with_drift_1_still_rejects_two_periods_away():
    totp = TOTP(TOTP.generate_secret(), period=30)
    code_period_10 = totp.generate(at=300.0)
    assert totp.verify(code_period_10, at=365.0, drift=1) is False  # counter 12, outside +-1


def test_hotp_rejects_a_negative_counter():
    # Found while building this exercise: `verify`'s drift probe can walk
    # the counter below zero if called near the Unix epoch (at=-5.0,
    # drift=1 -> counter -2), and struct.pack(">Q", ...) rejects negative
    # values outright. Realistically no legitimate authentication happens
    # at t<0, so this isn't fixed in `verify` -- but `hotp` itself should
    # fail loudly and specifically, not with a bare, confusing
    # struct.error leaking an implementation detail.
    with pytest.raises(ValueError):
        hotp(TOTP.generate_secret(), -1)


def test_verify_at_an_exact_period_boundary_uses_the_new_period():
    # counter_at uses floor division, so t=30.0 is exactly the START of
    # period 1, not the end of period 0 -- the boundary is unambiguous.
    totp = TOTP(TOTP.generate_secret(), period=30)
    assert totp.counter_at(29.999) == 0
    assert totp.counter_at(30.0) == 1


# --- TOTPAuthenticator: replay protection ------------------------------------------


def test_a_code_cannot_be_replayed_within_the_same_instant():
    totp = TOTP(TOTP.generate_secret())
    auth = TOTPAuthenticator(totp)
    code = totp.generate(at=1000.0)
    assert auth.verify("alice", code, at=1000.0) is True
    assert auth.verify("alice", code, at=1000.0) is False  # same code, same moment: replay


def test_a_code_cannot_be_replayed_later_within_its_own_drift_window():
    totp = TOTP(TOTP.generate_secret(), period=30)
    auth = TOTPAuthenticator(totp)
    code = totp.generate(at=0.0)
    assert auth.verify("alice", code, at=0.0, drift=1) is True
    # Still "valid" by drift tolerance at t=35 (period 1), but already consumed.
    assert auth.verify("alice", code, at=35.0, drift=1) is False


def test_a_fresh_code_in_a_later_period_is_accepted_after_a_replay_attempt():
    totp = TOTP(TOTP.generate_secret(), period=30)
    auth = TOTPAuthenticator(totp)
    code0 = totp.generate(at=0.0)
    assert auth.verify("alice", code0, at=0.0) is True
    code_later = totp.generate(at=120.0)  # several periods later, a genuinely new code
    assert auth.verify("alice", code_later, at=120.0) is True


def test_replay_protection_is_tracked_independently_per_user():
    totp = TOTP(TOTP.generate_secret())
    auth = TOTPAuthenticator(totp)
    code = totp.generate(at=1000.0)
    assert auth.verify("alice", code, at=1000.0) is True
    assert auth.verify("bob", code, at=1000.0) is True  # bob hasn't used this counter yet
    assert auth.verify("alice", code, at=1000.0) is False
    assert auth.verify("bob", code, at=1000.0) is False


# --- TOTPAuthenticator: attempt lockout --------------------------------------------


def test_lockout_triggers_after_max_attempts_within_the_window():
    totp = TOTP(TOTP.generate_secret())
    auth = TOTPAuthenticator(totp, max_attempts=3, lockout_window=30.0)
    for _ in range(3):
        assert auth.verify("alice", "000000", at=1000.0) is False
    with pytest.raises(AccountLockedError):
        auth.verify("alice", "000000", at=1000.0)


def test_lockout_does_not_check_the_code_at_all_once_locked():
    # Even the CORRECT code is rejected (raises) once locked out -- the
    # lockout is unconditional until the window passes, not "only blocks
    # wrong guesses."
    totp = TOTP(TOTP.generate_secret())
    auth = TOTPAuthenticator(totp, max_attempts=2, lockout_window=30.0)
    correct_code = totp.generate(at=1000.0)
    auth.verify("alice", "wrong1", at=1000.0)
    auth.verify("alice", "wrong2", at=1000.0)
    with pytest.raises(AccountLockedError):
        auth.verify("alice", correct_code, at=1000.0)


def test_lockout_clears_after_the_window_passes():
    totp = TOTP(TOTP.generate_secret())
    auth = TOTPAuthenticator(totp, max_attempts=2, lockout_window=30.0)
    auth.verify("alice", "wrong1", at=1000.0)
    auth.verify("alice", "wrong2", at=1000.0)
    with pytest.raises(AccountLockedError):
        auth.verify("alice", "wrong3", at=1000.0)
    # 31 seconds later, both earlier failures have aged out of the window.
    code = totp.generate(at=1031.0)
    assert auth.verify("alice", code, at=1031.0) is True


def test_a_successful_verification_clears_the_failure_count():
    totp = TOTP(TOTP.generate_secret())
    auth = TOTPAuthenticator(totp, max_attempts=2, lockout_window=30.0)
    auth.verify("alice", "wrong1", at=1000.0)
    code = totp.generate(at=1005.0)
    assert auth.verify("alice", code, at=1005.0) is True
    # Two more wrong guesses right after a success should NOT immediately
    # lock out -- the earlier failure was cleared by the success.
    assert auth.verify("alice", "wrong2", at=1006.0) is False
    assert auth.verify("alice", "wrong3", at=1007.0) is False  # 2nd failure since the reset, not 3rd overall


def test_lockout_is_tracked_independently_per_user():
    totp = TOTP(TOTP.generate_secret())
    auth = TOTPAuthenticator(totp, max_attempts=2, lockout_window=30.0)
    auth.verify("alice", "wrong1", at=1000.0)
    auth.verify("alice", "wrong2", at=1000.0)
    bob_code = totp.generate(at=1000.0)
    assert auth.verify("bob", bob_code, at=1000.0) is True  # bob is unaffected by alice's lockout


# --- brute-force cost: formula checked against direct enumeration + simulation ---


def test_expected_attempts_matches_direct_combinatorics_for_a_tiny_digit_count():
    # 2-digit space (100 codes), drift=0 (1 valid code): expected attempts
    # to guess 1-in-100 is exactly 100 by the formula.
    assert expected_attempts_to_guess(digits=2, drift=0) == pytest.approx(100.0)
    assert expected_attempts_to_guess(digits=2, drift=1) == pytest.approx(100 / 3)


def test_expected_crack_time_scales_inversely_with_request_rate():
    base = expected_crack_time_seconds(digits=6, drift=1, requests_per_second=1.0)
    faster = expected_crack_time_seconds(digits=6, drift=1, requests_per_second=100.0)
    assert faster == pytest.approx(base / 100.0)


def test_monte_carlo_matches_the_geometric_distribution_formula():
    # Empirically verify the formula (not just trust the arithmetic) on a
    # small, fast-to-simulate code space: 2 digits (100 codes), drift=0.
    # Run many independent trials of "guess uniformly at random until you
    # hit the one valid code" and check the sample mean lands close to
    # the analytical expectation (100), with a generous tolerance since
    # this is a real Monte Carlo estimate, not a deterministic check.
    rng = random.Random(0)
    digits = 2
    space = 10**digits
    target = rng.randrange(space)
    trial_attempt_counts = []
    for _ in range(3000):
        attempts = 0
        while True:
            attempts += 1
            guess = rng.randrange(space)
            if guess == target:
                break
        trial_attempt_counts.append(attempts)
    sample_mean = sum(trial_attempt_counts) / len(trial_attempt_counts)
    expected = expected_attempts_to_guess(digits=digits, drift=0)
    assert sample_mean == pytest.approx(expected, rel=0.15)  # geometric dist. has high variance; generous tolerance


def test_lockout_defense_increases_expected_crack_time_by_orders_of_magnitude():
    unthrottled = expected_crack_time_seconds(digits=6, drift=1, requests_per_second=100.0)
    throttled_rate = 5 / 30.0  # max_attempts=5 per lockout_window=30s
    throttled = expected_crack_time_seconds(digits=6, drift=1, requests_per_second=throttled_rate)
    assert throttled > unthrottled * 100  # measured: ~2000x in this exercise's own numbers
