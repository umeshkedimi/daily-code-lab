## Implementation

- `safe_join`'s containment check runs on `candidate` -- the fully resolved result of `(base / user_path).resolve()` -- never on `user_path` itself. This ordering is the entire reason it catches what the other two miss: a textual check can only ever reason about what the string says, and both the absolute-path override and the symlink case are attacks where the string says nothing suspicious at all.
- `escapes_base` resolves **both** arguments before comparing, specifically so it can be used to check `naive_join`'s output (which is never resolved against symlinks, only `os.path.normpath`-normalized) on equal footing with `safe_join`'s. Without resolving the candidate, a symlink pointing outside the base would look contained by a simple string-prefix check even though it isn't -- confirmed directly by a mutation that tried exactly that (see Key Learnings).
- `naive_string_check_join`'s `if ".." in user_path` (not `"../" in user_path`) is a one-character-feeling decision that turns out to matter: a bare `".."` with nothing after it contains no slash, so a check for the three-character substring `"../"` would let it straight through, while it still escapes the base directory by exactly one level. The corpus initially missed this case entirely -- every hand-picked attack string happened to have a trailing `/` after its `..` -- until a mutation test revealed the gap by changing the real check from `".."` to `"../"` and watching nothing fail.
- Not built: actual zip-file construction and extraction (the real zip-slip scenario, not just its underlying path-join mechanics), Windows-style path handling, URL-decoding -- all listed as stretch in `problem.md`.

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `naive_join` / `naive_string_check_join` / `safe_join` | O(len(path)) | O(1) |
| `escapes_base` | O(len(path)) | O(1) |

## Measured Results

- **The measured headline, against the final 6-string attack corpus**: `naive_join` escapes the base directory on **all 6**. `safe_join` escapes on **0**.
- **`naive_string_check_join` blocks 5, lets exactly 1 through**: every attack string containing a literal `".."` is blocked (including the bare `".."` added after mutation testing found the gap); the one that gets through is `/etc/passwd`, which needs no `".."` at all -- `os.path.join` and `pathlib`'s `/` operator both discard the base directory entirely the moment the second argument is absolute.
- **The symlink case defeats both naive implementations in a second, independent way**, confirmed against a real filesystem (`tmp_path`, a real symlink): a file named `innocuous.txt`, containing no `".."` and no leading slash, resolves (via the symlink) to a file outside the base directory. `naive_join` and `naive_string_check_join` both allow it through -- neither has any reason to look twice at a filename with nothing textually suspicious. `safe_join` blocks it, because resolving the path follows the symlink before the containment check ever runs.
- **No false positives**: all three implementations accept every one of 4 ordinary, legitimate relative paths, and `naive_join`'s output is never flagged as escaping for any of them.
- **9 tests, 3/3 consecutive clean runs.**

## Follow-up Questions

**Why?**
"Validate the path doesn't contain `..`" is the single most common home-grown defense against this vulnerability class, and it's worth measuring exactly how much it actually closes rather than treating it as either "a real fix" or "security theater" -- the honest answer here is specific: it closes 5 of 6 corpus attacks, and misses an entire independent category (symlinks) that has nothing to do with the substring it's checking for.

**How exactly?**
`Path.resolve()` does two things a textual check can't: it collapses every `..` and `.` component the way the filesystem actually would, and it follows every symlink it encounters along the way, substituting the real target path. `safe_join`'s containment check (`candidate == base or base in candidate.parents`) only has to be correct about one thing -- whether the final, fully-resolved destination sits inside the fully-resolved base -- because resolution has already done all the work of figuring out where a path *actually* points, regardless of how indirect the string that produced it was.

**What happens internally?**
`os.path.join(base, user_path)` (and `Path(base) / user_path`) both implement the POSIX rule that an absolute second path component discards everything before it -- this isn't a bug in either function, it's `os.path.join`'s documented behavior, and it's exactly why `/etc/passwd` needs no `".."` to escape: it isn't escaping via traversal at all, it's simply never being joined to the base in the first place.

**What if this fails?**
- **Someone reviews `naive_string_check_join` and sees it correctly blocks "the obvious attacks"**: `test_naive_string_check_join_specifically_lets_absolute_path_through` is the specific, falsifiable counterexample -- correctly blocking 5 attacks doesn't imply blocking the 6th, and there was never a reason to assume it would.
- **Someone assumes a textual blacklist's blind spots are limited to patterns they didn't think to add to the blacklist**: the symlink case is blind to the check for a reason that has nothing to do with pattern coverage at all -- the attack string itself is indistinguishable from a legitimate filename, no matter how thorough the blacklist.
- **A test corpus is built entirely by brainstorming attack strings by hand**: this is exactly what produced the undetected `"../"`-vs-`".."` gap here -- every hand-picked string happened to share a structural feature (a trailing slash after each `..`) that a mutation, not a brainstorm, is what actually revealed.

**What trade-offs did you consider?**
- **A fixed, explicit attack corpus (chosen) vs. property-based/generated test inputs**: a fixed corpus is exactly reproducible and each entry is individually understandable as "this specific attack," which matches this repo's deterministic, non-`random` convention -- but as the bare-`".."` gap shows, a hand-built corpus is only as good as the attacks someone thought to include, which generated or fuzzed inputs might have found faster. The corpus stays fixed here deliberately, with mutation testing filling the role a fuzzer would otherwise play.
- **Three separate functions (chosen) vs. one function with a `mode` parameter selecting the check strategy**: separate functions make the measured comparison direct -- call all three with the identical corpus, read off three independent counts -- rather than needing to thread a mode flag through test assertions that would otherwise look identical across runs.

**How do you evaluate it?**
- **9 tests, 3/3 consecutive clean runs.**
- **Mutation check, 3 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - `safe_join` checks the raw `user_path` string for `".."` instead of resolving and checking the resolved candidate's containment: 2/9 failed -- the attack-corpus test and the symlink test, exactly the two that depend on resolution actually happening.
  - `escapes_base` replaced with a plain string-prefix (`startswith`) check instead of resolving both paths: 1/9 failed -- only the symlink test, because every other scenario's candidate already happens to contain the base directory's path as a literal string prefix even without resolving, and only the symlink case's target genuinely lives somewhere a prefix check can't see.
  - `naive_string_check_join`'s check changed from `".."` to `"../"`: 0/9 failed on the first attempt -- no attack string in the original corpus had a bare, slash-less `".."`. Fixed by adding one (`".."` itself) to `ATTACK_STRINGS` and a dedicated test; re-run with both present, 2/9 failed, exactly the two built to catch it.

## Key Learnings

- **A hand-built attack corpus inherits whatever blind spot its author had, and mutation testing is what catches "every one of my examples happens to share this one structural feature I didn't notice."** The fix wasn't a smarter attack string chosen in hindsight -- it was running the suite against a deliberately weakened check and watching for silence, the same technique that caught 2026-11-18's greedy skip-vs-stop gap and 2026-11-23's TTL boundary gap, applied here to a security corpus instead of an algorithm's behavior.
- **A naive defense's failure modes are often not "more of the same category of attack" but a structurally unrelated one.** `naive_string_check_join` fails on the absolute-path case because of `os.path.join` semantics it never considered, and fails on the symlink case for an entirely different reason (nothing textual to check at all) -- two independent gaps, not one gap with two examples. Measuring both separately, rather than reporting one aggregate "it failed" count, is what makes each gap individually fixable.
- Consistent with 2026-10-15's parity-checking pattern: `escapes_base` had to resolve the exact same way `safe_join` does internally, or the measurement itself would have the blind spot being tested for -- a measurement tool built with a shortcut the thing it's measuring doesn't take is not actually measuring the right thing, confirmed directly when the `startswith`-based mutation of `escapes_base` itself passed every test except the one case it was specifically unable to see.
