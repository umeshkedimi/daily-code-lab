# Problem

**Category:** `security`

## Statement

Implement safe path resolution for "join a user-supplied relative path onto a fixed base directory" (the operation underneath file uploads, static-file serving, and archive extraction — the zip-slip vulnerability class is exactly this operation applied once per entry in an archive) and measure, against a fixed corpus of real attack strings, exactly how many each of three implementations actually lets escape the base directory. Not "the naive version is unsafe" — a specific count, for a specific implementation, against a specific corpus.

**Requirements:**

- `PathTraversalError(Exception)` — raised by any implementation that detects an escape.
- `naive_join(base_dir: str, user_path: str) -> str` — `os.path.normpath(os.path.join(base_dir, user_path))`. No containment check at all. Included as the baseline most code actually looks like before anyone thinks about this.
- `naive_string_check_join(base_dir: str, user_path: str) -> str` — rejects (`PathTraversalError`) if `".."` appears anywhere in `user_path`, otherwise behaves like `naive_join`. Included because this is the fix someone reaches for first, and it's worth measuring exactly how much it actually closes.
- `safe_join(base_dir: str, user_path: str) -> str` — resolves `base_dir` to an absolute, symlink-free path, resolves `base_dir / user_path` the same way, and raises `PathTraversalError` unless the resolved candidate is the resolved base itself or strictly inside it. The check happens **after** resolution, not on the raw string — this ordering is the entire reason it works where the other two don't.
- `escapes_base(base_dir: str, candidate_path: str) -> bool` — a measurement helper: resolves both `base_dir` and `candidate_path` and reports whether the candidate ends up outside the (resolved) base. Used to check what a non-raising function like `naive_join` actually produced, not what it claims.

## Constraints

- Standard library only (`os`, `pathlib`).
- The symlink-escape test needs a real filesystem (a `pytest` `tmp_path` fixture, an actual directory, and an actual symlink created and torn down within the test) — this is the one case in this exercise that can't be demonstrated with strings alone, because the vulnerability only exists once a real filesystem is involved.

## Approach

1. **The measured headline, against a fixed 5-string attack corpus** (`../../etc/passwd`, `../../../etc/shadow`, `/etc/passwd`, `subdir/../../etc/passwd`, ten `../` segments then `etc/passwd`): `naive_join` escapes the base directory on **all 5**. `safe_join` escapes on **0**. Not "much safer" — a specific 5-to-0 gap, measured by actually running both against the same corpus.
2. **`naive_string_check_join` is not useless, and that's the more interesting, more realistic finding**: it blocks the 4 attack strings that contain a literal `".."`, and lets exactly 1 through — `/etc/passwd`, which needs no `".."` at all, because `os.path.join` (and `pathlib`'s `/` operator) both discard the base directory entirely the moment the second argument is an absolute path. A blacklist check that happens to work on every attack the author tried against it is a specific, measured, incomplete defense, not a non-defense.
3. **The symlink case defeats the string check in a second, independent way**: a file named `innocuous.txt` — containing no `".."`, no leading slash, nothing textually suspicious at all — can be a symlink pointing anywhere on the filesystem. `naive_string_check_join` has no reason to ever look twice at it and allows it through; only `safe_join` catches it, specifically because resolving the path follows the symlink *before* the containment check runs, rather than checking containment on the pre-resolution string.
4. **No false positives on ordinary, legitimate paths**: a small set of benign relative paths (`report.txt`, `subdir/report.txt`, `a/b/c/report.txt`, `.`) must succeed under all three implementations, with `naive_join`'s output never flagged as escaping by `escapes_base` either — a safety mechanism that also breaks normal use isn't a mechanism anyone would actually ship.

## Stretch

- Actually building a `.zip` with a malicious entry name and extracting it through each of the three join functions, to show the exact same bug produces the exact same named vulnerability class (zip-slip) in a slightly different-looking real scenario.
- Windows-style backslash separators and drive-letter absolute paths (`C:\Windows\System32`) — this exercise's corpus and `safe_join` are written and tested against POSIX path semantics only.
- URL-decoding a user-supplied path before this check ever runs (`%2e%2e%2f`) — out of scope here since that's an HTTP-layer concern, not a filesystem-path-resolution one, but a very common way this exact vulnerability survives a textual blacklist in practice.
