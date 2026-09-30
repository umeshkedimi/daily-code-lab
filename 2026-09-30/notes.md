## Implementation

- `sliding_window_maximum`: one pass over `nums`, maintaining `idx_deque`, a `collections.deque` of indices whose values are strictly decreasing left to right (front = largest).
- At each index `i`: pop from the back while the back's value is `<= nums[i]` (those indices are now permanently dominated), push `i`, then pop from the front if it has aged out of the window (`front <= i - k`). Once `i >= k - 1` (the window is full for the first time), the front is appended to the result.
- `sliding_window_maximum_naive`: the direct reference — `max(nums[i:i+k])` for every window. Deliberately simple so its correctness needs no separate argument; it's the ground truth the fast version is checked against, not a competing implementation to trust equally.
- Both reject `k <= 0` and `k > len(nums)` the same way, so the differential tests can assert both raise together rather than the fast path silently doing something different on bad input.
- Not built: a generic "sliding window reduce" for other associative ops (min, sum) — this is specifically the max case, where the deque's monotonic-ordering trick applies; sum would just use a running total, and min is the mirror image with the inequality flipped.

## Complexity

- **Time:** O(n) total for the full slide, not O(n) per window. Each of the n indices is pushed onto `idx_deque` exactly once and popped at most once (from either end) over the entire run, so the combined cost of all pushes and pops across every iteration is O(n) — the fact that a single eviction can walk back through several elements doesn't change the amortized total, because every element it walks through is removed and never revisited.
- **Space:** O(k) worst case for `idx_deque` (strictly increasing or constant input keeps every index in-window on the deque at once); O(n) for the output list, which is unavoidable since that's the return value's size.
- **Naive reference:** O(n·k) — `max()` over a length-k slice, once per window, `n - k + 1` times.
- Measured, k held fixed (50) while n grows 2,000 → 20,000 → 200,000: both implementations scale roughly linearly in n (expected, since k is constant — naive is O(nk) = O(n) here too), deque staying ~2.7-3.1x faster throughout, consistent with the fixed per-window cost difference rather than a diverging growth rate.
- Measured, n held fixed (20,000) while k grows 10 → 100 → 1,000 → 5,000 — this is the test that actually isolates the O(n) vs O(n·k) difference: deque time stayed flat (2.7-3.4ms) regardless of k, while naive grew roughly linearly with k (3.2ms → 617.7ms), a 230x gap at k=5,000. This is the real shape of the claim: deque cost depends on n alone; naive cost depends on n times k.

## Follow-up Questions

**Why?**
Sliding-window maximum shows up directly in streaming/monitoring (rolling max over the last N samples), and the same deque trick underlies harder problems (e.g. the "shortest subarray with sum at least K" class, longest-subarray-with-constraint problems) where a monotonic deque prunes candidates that can never be optimal again.

**How exactly?**
Indices go in the back of a deque in an order that keeps their values strictly decreasing; the front is always the current window's max because anything that could have beaten it already knocked it out on the way in.

**Which algorithm?**
Monotonic deque (also called a monotonic queue) — standard technique for sliding-window min/max, distinct from a priority-queue/heap approach (which would need lazy deletion to handle expiring entries and costs O(log k) per step instead of amortized O(1)).

**Which library?**
`collections.deque` only — O(1) append/pop from both ends is the one property this algorithm actually needs; a plain `list` would make front-eviction O(k) per pop instead of O(1).

**What happens internally?**
`deque` is a doubly-linked list of fixed-size blocks under the hood, so `popleft()`/`pop()`/`append()` are all O(1) — no shifting of remaining elements the way `list.pop(0)` would require.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **The max value repeats** (`[9, 1, 9, 1, 1]`, k=2): handled by construction — the second `9` evicts the first from the back (since `9 <= 9`), so once the first `9` would have expired, the second is already correctly positioned as the new front. Verified directly (`test_duplicates_of_the_max_stay_valid_after_the_first_expires`).
- **Strictly increasing input** (`[1,2,3,4,5]`): every new element evicts the entire deque, so the deque never holds more than one index — max is always the newest. Verified.
- **Strictly decreasing input** (`[5,4,3,2,1]`): nothing ever gets evicted from the back (each new value is smaller than everything before it), so the deque holds the full window and the front only changes via front-eviction. Verified.
- **`k == 1`:** every window is a single element; the function degenerates to returning `nums` unchanged. Verified.
- **`k == len(nums)`:** exactly one window, one result. Verified.

**What trade-offs did you consider?**
- **Monotonic deque of indices (chosen) vs a max-heap with lazy deletion:** a heap needs `O(log k)` per push and must lazily skip stale (expired) entries when reading the top, which still amortizes reasonably but is strictly worse in both constant factor and complexity than the deque's amortized O(1) — the heap doesn't buy anything here because window membership can be checked by index comparison alone, which the deque already does for free.
- **Storing indices (chosen) vs storing values directly:** storing values makes front-expiry impossible to check (you can't tell *which* occurrence of a repeated value is expiring) — indices are required, and `nums[idx]` is looked up on demand.
- **Two separate public functions (chosen) vs one function with a `naive=False` flag:** keeping the reference implementation as its own top-level function makes it trivially importable and comparable in tests without a conditional branch inside the real implementation that could itself hide a bug.

**How do you debug it?**
- `idx_deque` should always satisfy two invariants after every iteration: values at its indices are strictly decreasing front-to-back, and every index in it is `> i - k` (inside the current window). A debug assertion checking both after each step localizes a violation to the exact iteration that broke it.
- Differential testing against the naive reference on a *minimized* failing case (shrink `n` and the value range down from a random failure) is faster than reasoning about deque contents abstractly — this is exactly how the off-by-one front-eviction mutation below was isolated to a 3-element reproduction.

**How do you evaluate it?**
- **54 tests, 5/5 consecutive clean runs** (seeds fixed via `pytest.mark.parametrize`, so reruns are for import/environment stability, not randomness — the randomized coverage itself comes from 40 distinct seeds baked into the parametrization).
- **Mutation check, 4 targeted mutations, isolated one at a time with `__pycache__` cleared between runs** (an early combined run gave a false "all pass" for one mutation due to a stale bytecode cache across back-to-back `sed`+`cp` edits in the same shell session — rerun individually and reproduced twice to confirm before trusting the result):
  - Back-eviction `<=` → `<`: **0 failures — genuinely behavior-preserving, not a real bug.** Keeping equal-valued duplicates on the deque longer doesn't change which value ends up at the front, since the newer, equal-or-greater element still ends up at the front on its own turn; it only costs a few extra (harmless) comparisons. Confirmed twice, isolated.
  - Front-eviction `<=` → `<` (off-by-one on window-expiry): **22/54 failed.** Minimized to `nums=[5,1,1], k=2`, where the mutated code leaves the stale index `0` (value `5`) at the front one step too long and reports `5` for the window `[1,1]` instead of `1`.
  - Window-start check `i >= k - 1` → `i >= k`: **50/54 failed** — drops the first valid window entirely, which also breaks the `len(result) == n - k + 1` invariant test directly.
  - `idx_deque.popleft()` → `idx_deque.pop()` (evicting the wrong end on window-expiry): **22/54 failed** — evicts the *largest* remaining candidate instead of the stale one, corrupting the front.
