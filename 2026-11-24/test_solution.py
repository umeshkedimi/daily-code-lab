import os

import pytest

from solution import (
    PathTraversalError,
    escapes_base,
    naive_join,
    naive_string_check_join,
    safe_join,
)

ATTACK_STRINGS = [
    "../../etc/passwd",
    "../../../etc/shadow",
    "/etc/passwd",
    "subdir/../../etc/passwd",
    ("../" * 10) + "etc/passwd",
    "..",  # bare, no trailing slash -- escapes by exactly one level
]

BENIGN_STRINGS = [
    "report.txt",
    "subdir/report.txt",
    "a/b/c/report.txt",
    ".",
]


@pytest.fixture
def base_dir(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    return str(base)


# --- escapes_base itself --------------------------------------------------


def test_escapes_base_true_for_a_path_outside(base_dir, tmp_path):
    outside = tmp_path / "outside"
    assert escapes_base(base_dir, str(outside)) is True


def test_escapes_base_false_for_the_base_itself_and_a_child(base_dir):
    assert escapes_base(base_dir, base_dir) is False
    assert escapes_base(base_dir, os.path.join(base_dir, "child.txt")) is False


# --- the measured headline: naive_join escapes on every attack ----------


def test_naive_join_escapes_on_every_attack_string(base_dir):
    escape_count = sum(
        escapes_base(base_dir, naive_join(base_dir, attack)) for attack in ATTACK_STRINGS
    )
    assert escape_count == len(ATTACK_STRINGS)


# --- naive_string_check_join: blocks 4, lets exactly 1 through ----------


def test_naive_string_check_join_blocks_dotdot_attacks_but_not_absolute_override(base_dir):
    blocked = 0
    escaped = 0
    for attack in ATTACK_STRINGS:
        try:
            result = naive_string_check_join(base_dir, attack)
        except PathTraversalError:
            blocked += 1
        else:
            if escapes_base(base_dir, result):
                escaped += 1

    assert blocked == 5
    assert escaped == 1


def test_naive_string_check_join_specifically_lets_absolute_path_through():
    # the one it misses needs no '..' at all: os.path.join (and pathlib's
    # / operator) both discard the base entirely for an absolute 2nd arg.
    result = naive_string_check_join("/var/data", "/etc/passwd")
    assert result == "/etc/passwd"


def test_naive_string_check_join_catches_bare_dotdot_without_trailing_slash():
    # a check for the substring '../' specifically (rather than '..')
    # would miss this -- a bare '..' with nothing after it contains no
    # slash at all, and still escapes by exactly one level.
    with pytest.raises(PathTraversalError):
        naive_string_check_join("/var/data", "..")


# --- safe_join: blocks every attack, zero escapes ------------------------


def test_safe_join_blocks_every_attack_string(base_dir):
    for attack in ATTACK_STRINGS:
        with pytest.raises(PathTraversalError):
            safe_join(base_dir, attack)


# --- no false positives on ordinary, legitimate paths --------------------


def test_all_three_implementations_accept_every_benign_path(base_dir):
    for benign in BENIGN_STRINGS:
        naive_result = naive_join(base_dir, benign)
        assert escapes_base(base_dir, naive_result) is False

        string_check_result = naive_string_check_join(base_dir, benign)
        assert escapes_base(base_dir, string_check_result) is False

        safe_result = safe_join(base_dir, benign)
        assert escapes_base(base_dir, safe_result) is False


# --- the symlink case: defeats the string check in a second, ------------
# independent way, and needs a real filesystem to demonstrate -----------


def test_symlink_escape_defeats_both_naive_implementations_but_not_safe_join(base_dir, tmp_path):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret = outside_dir / "secret.txt"
    secret.write_text("TOP SECRET")

    link = os.path.join(base_dir, "innocuous.txt")
    os.symlink(str(secret), link)

    # the filename itself contains no '..' and is not absolute -- nothing
    # textual distinguishes it from a normal file.
    naive_result = naive_join(base_dir, "innocuous.txt")
    assert escapes_base(base_dir, naive_result) is True

    string_check_result = naive_string_check_join(base_dir, "innocuous.txt")
    assert escapes_base(base_dir, string_check_result) is True

    with pytest.raises(PathTraversalError):
        safe_join(base_dir, "innocuous.txt")
