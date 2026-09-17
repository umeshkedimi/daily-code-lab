# Problem

**Category:** `genai-agentic`

**Note:** this is a second problem for 2026-09-17, an explicit exception to the repo's "one problem per day" rule (logged here rather than silently breaking it) — folder suffixed `b` to keep it distinct from the day's primary entry.

## Statement

Build a **rolling conversation-history summarizer**: if the conversation's message history exceeds 20 messages, summarize the overflow with a model call and fold that summary into the context, so nothing is missed even though the context handed to a model stays bounded.

**Requirements:**
- Track a growing list of conversation messages.
- Once the count exceeds 20, summarize the overflow (via a model call) rather than just dropping it.
- The summary must be incorporated into the context used going forward — not lost, not replaced wholesale on the next rollover.
- Nothing should be "missed": information from early in a long conversation must still be recoverable (in summarized form) even after the raw messages carrying it are gone.

## Constraints

- The context assembled for a model call must stay bounded in size regardless of how long the actual conversation has run (this is the entire point — an unbounded history eventually exceeds any model's context window).
- Each rollover must incorporate the *previous* summary, not just the newly-overflowed messages — otherwise information from before the most recent rollover would be silently lost the next time it fires, defeating the "nothing missed" requirement over a conversation with more than one rollover.
- The most recent messages should stay verbatim (not summarized) so the immediate conversational context stays high-fidelity; only the older overflow gets compressed.

## Approach

1. **Two different message stores, one for verbatim recency, one for compressed history.** `RollingConversationMemory` keeps a raw `messages` list (always the most recent `keep_recent` after any rollover) and a separate `summary: Optional[str]` (the compressed record of everything older). `get_context()` assembles both into what a model would actually see: the summary as a leading system message, then the verbatim recent messages in order.

2. **"Rolling" means each summarization call receives the *previous* summary as an input, not just the new overflow.** `Summarizer.summarize(prior_summary, messages)` takes both — this is what prevents the second, third, ... rollover from quietly discarding everything the first rollover had already compressed. Without this, a conversation with multiple rollovers would only ever remember the most recent summarization window, silently violating "nothing missed" for anything further back.

3. **The summarization step is a real model call, injected behind a `Summarizer` protocol — not hardcoded to one provider.** `LLMSummarizer` builds a real prompt (prior summary + rendered overflow messages, asked for an updated summary that preserves both) around an injected `complete(prompt) -> str` callable, so it can bind to any real model call (this week's LLM gateway, a raw provider call, whatever) without `RollingConversationMemory` itself knowing or caring which. No API key is available in this environment, so `ExtractiveSummarizer` — a deterministic, offline stand-in that still genuinely incorporates the prior summary and folds in one line per overflowed message — is what's actually exercised in the demo and tests. It proves the *mechanism* (nothing silently dropped, prior summary always carried forward) independently of summary *quality*, which depends on the real model used.

4. **Proved "nothing missed" concretely, not just by inspection.** The demo/tests feed 50 messages, each carrying a unique, checkable fact, through settings that force 3 separate rollovers — then directly assert that a fact from message 0 (long gone from raw storage) and a fact from message 25 (folded in at a *different* rollover than message 0's) are both still findable in the final summary text.
