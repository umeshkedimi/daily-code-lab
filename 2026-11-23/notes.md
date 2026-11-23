## Implementation

TODO

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `Ledger.execute_charge` | O(1) | O(1) per charge |
| `NaiveChargeService.charge` | O(1) | O(1) |
| `IdempotentChargeService.charge` | O(number of currently-remembered keys), for eviction | O(number of currently-remembered keys) |

## Follow-up Questions

**Why?** TODO
**How exactly?** TODO
**What happens internally?** TODO
**What if this fails?** TODO
**What trade-offs did you consider?** TODO
**How do you evaluate it?** TODO

## Key Learnings

TODO
