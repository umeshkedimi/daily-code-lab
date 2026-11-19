## Implementation

- `verify` checks things in a specific, deliberate order: freshness first, then signature, then replay. Freshness first means a stale request never even reaches signature comparison or the seen-set -- there's nothing to gain by letting an obviously-too-old request touch either. Signature before replay means an *invalid* signature is never recorded as "seen" (recording it would do nothing useful and would let an attacker pollute the seen-set with garbage signatures that were never actually valid).
- `_evict_stale` runs at the *start* of the dedup branch, before the current signature's own membership is checked or recorded -- so eviction always operates on the state as it was before this request, and the newly-recorded signature is never immediately eligible for eviction by its own request.
- The eviction rule (`abs(now - ts) > self.tolerance`) is exactly the same comparison `verify`'s own freshness check uses, just applied to a *stored* timestamp instead of an incoming one -- this is what makes the "evicting never creates a security gap" argument in `problem.md`'s Constraints true by construction rather than by coincidence: anything evicted is, by the same rule, already something the freshness check would reject if it came back.
- `compare_fn` is a constructor parameter specifically so `naive_signatures_equal` can be substituted *for the one test that needs it as a point of comparison* (none currently do, since every scenario tested uses the default) without needing a second `WebhookVerifier`-like class -- the same "one class, one flag, swap it for the comparison" shape as 2026-10-23's circuit breaker and 2026-11-18's three allocation strategies.
- Not built: actually mounting 2026-11-06's recovery attack against `compare_fn=naive_signatures_equal`, a nonce-based dedup alternative, clock-skew negotiation -- all listed as stretch in `problem.md`.

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `sign` / `canonical_message` | O(len(payload)) | O(1) |
| `WebhookVerifier.verify` | O(number of currently-remembered signatures), for eviction | O(number of currently-remembered signatures) |

## Measured Results

- **The headline gap, exact counts**: the same captured, valid request replayed 5 times accepts **5/5** with `dedup=False` and exactly **1/5** with `dedup=True` -- not "fewer," exactly one, confirmed by running both configurations against the identical signature, timestamp, and clock before writing either number into a test.
- **Bounded memory under continuous legitimate traffic, confirmed directly**: 50 distinct valid requests, each spaced `tolerance + 1` apart, keep `seen_count` at exactly **1** throughout -- never climbing toward 50 -- because each new request's arrival is exactly the moment the previous one ages out of the freshness window.
- **Symmetric freshness window, both edges measured**: a timestamp exactly `tolerance` away (past or future) is accepted; one token past that boundary, on either side, is rejected -- the same single `abs(...)` comparison produces both halves.
- **18 tests, 3/3 consecutive clean runs.**

## Follow-up Questions

**Why?**
"Verify the signature" is treated as the whole security story for webhooks far more often than it should be -- a correctly signed, completely unmodified request is still a live attack if it's simply resent, because nothing about the signature itself expires faster than the freshness window does.

**How exactly?**
The signature covers the timestamp (via `canonical_message`), so an attacker can't detach a valid signature from its original timestamp and pair it with a fresher one -- that pairing would fail signature verification, not just a separate "is this timestamp recent" check that could be bypassed independently. Replay protection is a second, genuinely separate mechanism layered on top: a per-signature "already used" memory, bounded by evicting anything the freshness check would reject anyway if it reappeared.

**What happens internally?**
`hmac.compare_digest` is implemented to take time that depends only on the *length* of its inputs, never on where or whether they differ -- the same property `constant_time_compare` built by hand on 2026-11-06, except this is the actual standard-library primitive meant for exactly this job, rather than a from-scratch teaching version of it.

**What if this fails?**
- **A caller swaps `compare_fn` to `naive_signatures_equal`, by mistake or because it "still gives correct answers"**: every accept/reject test in this suite still passes, because the two comparators *are* functionally identical -- 2026-11-06's exact point. Only `test_default_compare_fn_is_the_constant_time_one_not_the_naive_one`, which checks the default directly rather than through any accept/reject outcome, would catch a default actually swapped to the unsafe one.
- **A caller disables `dedup` in production "to save memory"**: `test_measured_gap_five_replays_dedup_on_vs_off` is the concrete cost of that choice -- not a warning, a number (5/5 instead of 1/5).
- **An attacker tries to pair a captured signature with a different timestamp to extend its replay window**: rejected by signature mismatch, not a separate check, because the timestamp is baked into what was signed (`test_tampered_payload_rejected`'s sibling case, not written separately since `canonical_message` makes a timestamp swap just another form of payload tampering).

**What trade-offs did you consider?**
- **A single `WebhookVerifier` with a `dedup` flag (chosen) vs. two separate classes (one with replay protection, one without)**: a flag makes the before/after comparison trivial -- same class, same clock, same secret, one boolean different -- exactly the shape 2026-10-23 and 2026-11-18 both used for their own headline comparisons, rather than two independently-written implementations that could silently drift apart on everything *except* the one thing being compared.
- **Checking the default comparator directly via `verifier._compare_fn is secure_signatures_equal` (chosen) vs. relying on behavioral tests**: accessing a "private" attribute to test it feels like testing an implementation detail, but here it's the *only* way to test the one requirement (`problem.md`: "must never be the default") that no behavioral test can observe, precisely because the naive and secure comparators are built to be behaviorally indistinguishable from the outside.

**How do you evaluate it?**
- **18 tests, 3/3 consecutive clean runs.**
- **Mutation check, 4 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - Freshness check loosened from `>` to `>=` (rejects exactly-at-boundary requests that should be accepted): 2/18 failed -- both boundary-acceptance tests, exactly as expected.
  - Dedup membership check reordered to record the signature *before* reading whether it was already present, instead of after: 0/18 failed -- re-derived by hand and confirmed this reordering is behaviorally identical to the original (the membership read still happens before the write in both versions), not a bug; a genuinely zero-impact mutation, not one requiring further investigation, in the same vein as 2026-11-18's `int()`/`round()` case.
  - `_evict_stale` call removed from the dedup branch (seen-set grows forever): 1/18 failed (`test_seen_count_does_not_grow_unboundedly_under_continuous_traffic`), exactly the test built to catch it.
  - Default `compare_fn` swapped from `secure_signatures_equal` to `naive_signatures_equal`: 0/18 failed on the first pass -- every behavioral test still passed, because the two comparators give identical answers by design. This is what prompted adding `test_default_compare_fn_is_the_constant_time_one_not_the_naive_one`; re-run with it present, 1/18 failed, exactly the new one.

## Key Learnings

- **A security requirement stated in prose ("must never be the default") is not actually tested until there's an assertion that would fail if it were violated -- and for a deliberately-behaviorally-equivalent pair of functions, that assertion can't be behavioral at all.** The instinct to avoid testing "implementation details" is usually right, but `_compare_fn`'s identity *is* the security property here, not an incidental detail of how it's built -- the whole reason `naive_signatures_equal` exists in this exercise is to be a trap that passes every functional test, which makes checking the default directly the only test that actually does its job.
- Consistent with 2026-11-18's greedy-skip finding: a mutation surviving every existing test isn't evidence the code is more correct than expected -- it's a direct pointer to a property none of the current tests happen to exercise, and the fix is almost always a smaller, more targeted test aimed at that exact mechanism, not a bigger pile of scenario tests shaped like the ones already there.
- The "zero-impact mutation" case here (reordering a read-then-write around the point where `signature in self._seen` is checked) is a good example of a mutation that *looks* like it changes something load-bearing but, traced by hand, doesn't -- worth confirming by tracing rather than assuming either "it's fine" or "the test suite missed a bug," since both conclusions are available and only one is correct.
