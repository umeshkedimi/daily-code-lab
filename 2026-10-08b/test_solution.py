import random

import pytest

from solution import (
    AuthService,
    ExpiredRefreshToken,
    InvalidRefreshToken,
    InvalidSignature,
    JWT,
    JWTError,
    MalformedToken,
    ReplayDetected,
    RevokedRefreshToken,
    TokenExpired,
    WrongTokenType,
    _b64url_decode,
    _b64url_encode,
)


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0  # arbitrary epoch-like start, far from 0

    def __call__(self):
        return self.now


# --- JWT: encode/decode roundtrip ---------------------------------------------------


def test_roundtrip_preserves_claims():
    token = JWT.encode({"sub": "alice", "exp": 2_000_000_000}, "secret")
    assert JWT.decode(token, "secret") == {"sub": "alice", "exp": 2_000_000_000}


def test_token_has_three_dot_separated_segments():
    token = JWT.encode({"sub": "alice"}, "secret")
    assert len(token.split(".")) == 3


def test_decode_rejects_wrong_secret():
    token = JWT.encode({"sub": "alice"}, "correct-secret")
    with pytest.raises(InvalidSignature):
        JWT.decode(token, "wrong-secret")


def test_decode_rejects_expired_token():
    token = JWT.encode({"sub": "alice", "exp": 1000}, "secret")
    with pytest.raises(TokenExpired):
        JWT.decode(token, "secret", now=1000)  # exp is exclusive: now == exp has expired


def test_decode_accepts_token_one_tick_before_expiry():
    token = JWT.encode({"sub": "alice", "exp": 1000}, "secret")
    JWT.decode(token, "secret", now=999.999)  # must not raise


def test_decode_accepts_token_with_no_exp_claim():
    token = JWT.encode({"sub": "alice"}, "secret")
    JWT.decode(token, "secret", now=99999999)  # no exp -> never expires, must not raise


@pytest.mark.parametrize("bad_token", ["not-a-jwt", "a.b", "a.b.c.d", ""])
def test_decode_rejects_wrong_segment_count(bad_token):
    with pytest.raises(MalformedToken):
        JWT.decode(bad_token, "secret")


def test_decode_rejects_non_base64_signature_segment():
    header_b64, payload_b64, _ = JWT.encode({"sub": "alice"}, "secret").split(".")
    with pytest.raises(MalformedToken):
        JWT.decode(f"{header_b64}.{payload_b64}.not!valid!base64!", "secret")


# --- JWT: tampering is always detected (deterministic bit-flip, not base64-text flip) ---


def _flip_one_bit(data: bytes, bit_index: int) -> bytes:
    i, b = divmod(bit_index, 8)
    out = bytearray(data)
    out[i] ^= 1 << b
    return bytes(out)


@pytest.mark.parametrize("seed", range(20))
def test_flipping_any_bit_of_the_signature_is_detected(seed):
    rng = random.Random(seed)
    header_b64, payload_b64, sig_b64 = JWT.encode({"sub": "alice", "n": rng.randint(0, 10**6)}, "secret").split(".")
    sig_bytes = _b64url_decode(sig_b64)
    flipped = _flip_one_bit(sig_bytes, rng.randrange(len(sig_bytes) * 8))
    tampered = f"{header_b64}.{payload_b64}.{_b64url_encode(flipped)}"
    with pytest.raises(InvalidSignature):
        JWT.decode(tampered, "secret")


@pytest.mark.parametrize("seed", range(20))
def test_flipping_any_bit_of_the_payload_is_detected(seed):
    rng = random.Random(seed)
    header_b64, payload_b64, sig_b64 = JWT.encode({"sub": "alice", "n": rng.randint(0, 10**6)}, "secret").split(".")
    payload_bytes = _b64url_decode(payload_b64)
    flipped = _flip_one_bit(payload_bytes, rng.randrange(len(payload_bytes) * 8))
    tampered = f"{header_b64}.{_b64url_encode(flipped)}.{sig_b64}"
    # Payload bytes changed but the signature was computed over the
    # original -- must be rejected as a signature mismatch, regardless of
    # whether the flipped payload still happens to parse as JSON.
    with pytest.raises(InvalidSignature):
        JWT.decode(tampered, "secret")


def test_alg_none_downgrade_is_rejected():
    # A classic JWT attack: craft a token claiming alg=none with an empty
    # signature, hoping a lenient verifier trusts the header and skips
    # verification. decode() never reads the header's alg at all -- it
    # always computes the HMAC itself -- so this is rejected the same way
    # any other wrong signature would be, not via a special-case check.
    header_b64 = _b64url_encode(b'{"alg":"none","typ":"JWT"}')
    _, payload_b64, _ = JWT.encode({"sub": "alice", "admin": True}, "secret").split(".")
    forged = f"{header_b64}.{payload_b64}."  # empty signature segment
    with pytest.raises((InvalidSignature, MalformedToken)):
        JWT.decode(forged, "secret")


# --- AuthService: login / refresh happy path ----------------------------------------


def test_login_issues_a_valid_access_and_refresh_token():
    clock = FakeClock()
    auth = AuthService("secret", access_ttl=900, refresh_ttl=1000, clock=clock)
    pair = auth.login("alice")
    claims = auth.verify_access_token(pair.access_token)
    assert claims["sub"] == "alice"
    assert claims["type"] == "access"


def test_refresh_issues_a_new_pair_and_rotates_the_old_token_out():
    clock = FakeClock()
    auth = AuthService("secret", access_ttl=900, refresh_ttl=1000, clock=clock)
    pair1 = auth.login("alice")
    pair2 = auth.refresh(pair1.refresh_token)
    assert pair2.refresh_token != pair1.refresh_token
    assert pair2.access_token != pair1.access_token
    assert auth.verify_access_token(pair2.access_token)["sub"] == "alice"


def test_a_chain_of_rotations_keeps_working():
    clock = FakeClock()
    auth = AuthService("secret", access_ttl=900, refresh_ttl=100_000, clock=clock)
    pair = auth.login("alice")
    for _ in range(10):
        pair = auth.refresh(pair.refresh_token)
    assert auth.verify_access_token(pair.access_token)["sub"] == "alice"


def test_unknown_refresh_token_is_rejected():
    auth = AuthService("secret", clock=FakeClock())
    with pytest.raises(InvalidRefreshToken):
        auth.refresh("some-token-that-was-never-issued")


def test_expired_refresh_token_is_rejected():
    clock = FakeClock()
    auth = AuthService("secret", access_ttl=900, refresh_ttl=1000, clock=clock)
    pair = auth.login("alice")
    clock.now += 1000
    with pytest.raises(ExpiredRefreshToken):
        auth.refresh(pair.refresh_token)


def test_logout_revokes_the_refresh_token():
    clock = FakeClock()
    auth = AuthService("secret", clock=clock)
    pair = auth.login("alice")
    auth.logout(pair.refresh_token)
    with pytest.raises(RevokedRefreshToken):
        auth.refresh(pair.refresh_token)


# --- AuthService: replay detection (the actual point of this exercise) ------------


def test_reusing_a_rotated_away_refresh_token_is_detected_as_replay():
    clock = FakeClock()
    auth = AuthService("secret", clock=clock)
    pair1 = auth.login("alice")
    auth.refresh(pair1.refresh_token)  # rotates pair1 away
    with pytest.raises(ReplayDetected):
        auth.refresh(pair1.refresh_token)  # reusing the now-stale token


def test_replay_revokes_the_entire_family_not_just_the_reused_token():
    # The real point: a legitimate, never-yet-used refresh token in the
    # SAME family must also stop working once a replay is detected on an
    # older token from that family -- because the attacker's replay is
    # indistinguishable from "the real chain has been compromised."
    clock = FakeClock()
    auth = AuthService("secret", clock=clock)
    pair1 = auth.login("alice")
    pair2 = auth.refresh(pair1.refresh_token)  # legitimate rotation: pair2 is currently valid
    with pytest.raises(ReplayDetected):
        auth.refresh(pair1.refresh_token)  # attacker replays the stolen, stale pair1 token
    with pytest.raises(RevokedRefreshToken):
        auth.refresh(pair2.refresh_token)  # pair2 is legitimate but now locked out too


def test_replay_can_be_detected_even_several_rotations_back():
    clock = FakeClock()
    auth = AuthService("secret", clock=clock)
    pair = auth.login("alice")
    stale = pair
    for _ in range(5):
        pair = auth.refresh(pair.refresh_token)
    with pytest.raises(ReplayDetected):
        auth.refresh(stale.refresh_token)  # replaying the very first token, 5 rotations later
    with pytest.raises(RevokedRefreshToken):
        auth.refresh(pair.refresh_token)  # the current (5th-generation) token is also dead now


def test_two_independent_login_families_do_not_affect_each_other():
    clock = FakeClock()
    auth = AuthService("secret", clock=clock)
    session_a = auth.login("alice")
    session_b = auth.login("alice")  # a second, independent session for the same user
    auth.refresh(session_a.refresh_token)
    with pytest.raises(ReplayDetected):
        auth.refresh(session_a.refresh_token)  # replay inside family A
    auth.refresh(session_b.refresh_token)  # family B is untouched, still works


# --- AuthService: access-token edge cases -------------------------------------------


def test_verify_access_token_rejects_expired_access_token():
    clock = FakeClock()
    auth = AuthService("secret", access_ttl=10, clock=clock)
    pair = auth.login("alice")
    clock.now += 10
    with pytest.raises(JWTError):
        auth.verify_access_token(pair.access_token)


def test_verify_access_token_rejects_a_token_of_the_wrong_type():
    # A validly-signed JWT that just isn't an access token (e.g. hand-crafted,
    # or -- in a system with more token kinds -- an ID token) must still be
    # rejected: signature validity alone isn't enough, the claimed purpose
    # of the token has to match what's being verified.
    clock = FakeClock()
    auth = AuthService("secret", clock=clock)
    forged = JWT.encode({"sub": "alice", "type": "refresh", "exp": clock.now + 900}, "secret")
    with pytest.raises(WrongTokenType):
        auth.verify_access_token(forged)


def test_verify_access_token_rejects_tokens_signed_with_a_different_secret():
    clock = FakeClock()
    auth = AuthService("secret", clock=clock)
    forged = JWT.encode({"sub": "alice", "type": "access", "exp": clock.now + 900}, "wrong-secret")
    with pytest.raises(InvalidSignature):
        auth.verify_access_token(forged)


# --- randomized: a long random sequence of logins/refreshes/replays stays consistent ---


@pytest.mark.parametrize("seed", range(15))
def test_random_sequences_of_operations_never_violate_family_revocation(seed):
    # Property: once ANY replay has been detected for a family, EVERY token
    # that ever belonged to that family must be rejected from then on --
    # checked after a long randomized sequence of logins and refreshes with
    # occasional deliberate replays mixed in, not just the hand-picked cases above.
    rng = random.Random(seed)
    clock = FakeClock()
    auth = AuthService("secret", refresh_ttl=10**9, clock=clock)

    families = []  # list of lists of {raw refresh token}, in issuance order, one list per family
    compromised_families = set()

    for _ in range(100):
        action = rng.random()
        if action < 0.3 or not families:
            pair = auth.login("user")
            families.append([pair.refresh_token])
        else:
            fam_idx = rng.randrange(len(families))
            chain = families[fam_idx]
            token = rng.choice(chain)  # may be the latest token, or a stale earlier one
            is_latest = token == chain[-1]
            try:
                new_pair = auth.refresh(token)
            except ReplayDetected:
                assert not is_latest  # a replay can only legitimately be a non-latest token
                compromised_families.add(fam_idx)
                continue
            except RevokedRefreshToken:
                assert fam_idx in compromised_families
                continue
            # Success: must not have been a token from an already-compromised family.
            assert fam_idx not in compromised_families
            chain.append(new_pair.refresh_token)
