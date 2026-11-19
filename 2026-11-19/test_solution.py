from solution import (
    WebhookVerifier,
    canonical_message,
    naive_signatures_equal,
    secure_signatures_equal,
    sign,
)

SECRET = b"shh"
PAYLOAD = b'{"event":"paid"}'


class FakeClock:
    def __init__(self, start: int = 0) -> None:
        self.now = start

    def advance(self, dt: int) -> None:
        self.now += dt

    def __call__(self) -> int:
        return self.now


# --- sign / canonical_message ---------------------------------------------


def test_sign_is_deterministic_for_the_same_inputs():
    assert sign(PAYLOAD, SECRET, 1000) == sign(PAYLOAD, SECRET, 1000)


def test_sign_depends_on_the_secret():
    assert sign(PAYLOAD, SECRET, 1000) != sign(PAYLOAD, b"other-secret", 1000)


def test_canonical_message_binds_timestamp_and_payload():
    assert canonical_message(PAYLOAD, 1000) == b'1000.{"event":"paid"}'


# --- naive vs secure compare: same answers, only timing differs ----------


def test_naive_and_secure_compare_agree_on_matching_signatures():
    sig = sign(PAYLOAD, SECRET, 1000)
    assert naive_signatures_equal(sig, sig) is True
    assert secure_signatures_equal(sig, sig) is True


def test_naive_and_secure_compare_agree_on_wrong_secret():
    sig = sign(PAYLOAD, SECRET, 1000)
    wrong = sign(PAYLOAD, b"wrong-secret", 1000)
    assert naive_signatures_equal(sig, wrong) is False
    assert secure_signatures_equal(sig, wrong) is False


def test_naive_and_secure_compare_agree_on_tampered_payload():
    sig = sign(PAYLOAD, SECRET, 1000)
    tampered_sig = sign(b'{"event":"refunded"}', SECRET, 1000)
    assert naive_signatures_equal(sig, tampered_sig) is False
    assert secure_signatures_equal(sig, tampered_sig) is False


def test_naive_and_secure_compare_agree_on_a_single_flipped_character():
    sig = sign(PAYLOAD, SECRET, 1000)
    flipped = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    assert naive_signatures_equal(sig, flipped) is False
    assert secure_signatures_equal(sig, flipped) is False


# --- the default comparator must be the safe one --------------------------
# naive_signatures_equal and secure_signatures_equal are *functionally*
# identical on every input (that's the whole point of 2026-11-06's finding:
# the vulnerability was never about correctness) -- so no accept/reject test
# above can ever catch a default swapped to the naive one. The only thing
# that can is checking the default directly.


def test_default_compare_fn_is_the_constant_time_one_not_the_naive_one():
    clock = FakeClock(1000)
    verifier = WebhookVerifier(SECRET, tolerance=300, clock=clock)
    assert verifier._compare_fn is secure_signatures_equal


# --- WebhookVerifier: basic accept/reject ---------------------------------


def test_valid_signature_accepted():
    clock = FakeClock(1000)
    verifier = WebhookVerifier(SECRET, tolerance=300, clock=clock)
    sig = sign(PAYLOAD, SECRET, 1000)
    assert verifier.verify(PAYLOAD, 1000, sig) is True


def test_wrong_secret_rejected():
    clock = FakeClock(1000)
    verifier = WebhookVerifier(SECRET, tolerance=300, clock=clock)
    wrong_sig = sign(PAYLOAD, b"wrong-secret", 1000)
    assert verifier.verify(PAYLOAD, 1000, wrong_sig) is False


def test_tampered_payload_rejected():
    clock = FakeClock(1000)
    verifier = WebhookVerifier(SECRET, tolerance=300, clock=clock)
    sig = sign(PAYLOAD, SECRET, 1000)
    assert verifier.verify(b'{"event":"refunded"}', 1000, sig) is False


# --- symmetric freshness window, checked at both edges -------------------


def test_timestamp_exactly_at_past_tolerance_boundary_accepted():
    clock = FakeClock(1300)
    verifier = WebhookVerifier(SECRET, tolerance=300, clock=clock)
    sig = sign(PAYLOAD, SECRET, 1000)
    assert verifier.verify(PAYLOAD, 1000, sig) is True


def test_timestamp_one_past_tolerance_boundary_rejected():
    clock = FakeClock(1301)
    verifier = WebhookVerifier(SECRET, tolerance=300, clock=clock)
    sig = sign(PAYLOAD, SECRET, 1000)
    assert verifier.verify(PAYLOAD, 1000, sig) is False


def test_timestamp_exactly_at_future_tolerance_boundary_accepted():
    clock = FakeClock(1000)
    verifier = WebhookVerifier(SECRET, tolerance=300, clock=clock)
    sig = sign(PAYLOAD, SECRET, 1300)
    assert verifier.verify(PAYLOAD, 1300, sig) is True


def test_timestamp_one_beyond_future_tolerance_boundary_rejected():
    clock = FakeClock(1000)
    verifier = WebhookVerifier(SECRET, tolerance=300, clock=clock)
    sig = sign(PAYLOAD, SECRET, 1301)
    assert verifier.verify(PAYLOAD, 1301, sig) is False


# --- the headline measured gap: dedup on vs off under replay -------------


def test_replay_rejected_when_dedup_enabled():
    clock = FakeClock(5000)
    sig = sign(PAYLOAD, SECRET, 5000)
    verifier = WebhookVerifier(SECRET, tolerance=300, clock=clock, dedup=True)
    assert verifier.verify(PAYLOAD, 5000, sig) is True
    assert verifier.verify(PAYLOAD, 5000, sig) is False


def test_measured_gap_five_replays_dedup_on_vs_off():
    clock = FakeClock(5000)
    sig = sign(PAYLOAD, SECRET, 5000)
    verifier_on = WebhookVerifier(SECRET, tolerance=300, clock=clock, dedup=True)
    verifier_off = WebhookVerifier(SECRET, tolerance=300, clock=clock, dedup=False)

    on_results = [verifier_on.verify(PAYLOAD, 5000, sig) for _ in range(5)]
    off_results = [verifier_off.verify(PAYLOAD, 5000, sig) for _ in range(5)]

    assert sum(on_results) == 1
    assert sum(off_results) == 5


# --- bounded memory under continuous legitimate traffic ------------------


def test_seen_count_does_not_grow_unboundedly_under_continuous_traffic():
    clock = FakeClock(0)
    verifier = WebhookVerifier(SECRET, tolerance=100, clock=clock, dedup=True)

    max_seen = 0
    for i in range(50):
        timestamp = i * 101  # each one ages out just before the next arrives
        clock.now = timestamp
        sig = sign(PAYLOAD, SECRET, timestamp)
        assert verifier.verify(PAYLOAD, timestamp, sig) is True
        max_seen = max(max_seen, verifier.seen_count)

    assert max_seen == 1
