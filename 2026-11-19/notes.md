## Implementation

TODO

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `sign` / `canonical_message` | O(len(payload)) | O(1) |
| `WebhookVerifier.verify` | O(number of currently-remembered signatures), for eviction | O(number of currently-remembered signatures) |

## Follow-up Questions

**Why?** TODO
**How exactly?** TODO
**What happens internally?** TODO
**What if this fails?** TODO
**What trade-offs did you consider?** TODO
**How do you evaluate it?** TODO

## Key Learnings

TODO
