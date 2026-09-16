# Problem

**Category:** `genai-agentic`

## Statement

Build a **tool-calling agent loop from scratch** — the mechanism behind every agent framework (LangChain agents, LangGraph, OpenAI's Assistants API, etc.) — without using any of those frameworks' agent executors. Given a user message, a set of tools, and an LLM client:

- The model can request a tool call instead of answering directly.
- The tool gets executed, its result is fed back to the model.
- This repeats until the model produces a final answer (no more tool calls).
- The loop must be safe: a bad tool call (unknown tool, malformed arguments, a tool that raises) must not crash the agent, and a model that never stops calling tools must not loop forever.

## Constraints

- The wire format for tool calls/results must match a real LLM tool-calling API (this exercise targets OpenAI's `chat.completions` `tools` shape) — not an invented format — so a real client is a drop-in, not a rewrite.
- Every tool-execution failure mode (unknown tool name, invalid JSON arguments, the tool itself throwing) must become a result fed back to the model, never an unhandled exception that kills the loop.
- The loop must terminate within a bounded number of iterations even if the model never produces a final answer, and must say so clearly (not hang, not silently stop).
- At least one tool must do real work with real, unpredictable output (not a canned string), and the loop must correctly react to that real output.

## Approach

1. **Model the wire format first, the loop second.** `ToolCall` (id, name, raw JSON arguments string) and `AssistantMessage` (content, list of tool calls) mirror exactly what OpenAI's `chat.completions.create(..., tools=...)` response looks like. Getting this shape right up front is what makes `OpenAIChatClient` (real) and `ScriptedLLMClient` (test/demo stand-in) interchangeable behind one `LLMClient` protocol — `run_agent()` never knows or cares which one it's talking to.

2. **The loop is a plain `for` over `max_iterations`, not recursion or an unbounded `while True`.** Bounding it structurally (a `for` over a fixed range) rather than relying on a manually-incremented counter with a `while` makes "this cannot loop forever" true by construction, not by discipline.

3. **Failure isolation lives inside `_execute_tool_call`, at the same seam used for the retry exercise and the async fetch exercise**: it never raises. Unknown tool, bad JSON, and a raising tool all become a `{"error": ...}` string appended as a `tool` role message — the model sees its own mistake in the next turn and gets a chance to correct course, which is both more robust *and* more realistic than crashing (a real LLM does sometimes hallucinate a tool name or malform arguments).

4. **One tool does real, unpredictable work.** `get_weather` makes a genuine network call (geocoding + current weather via open-meteo, no API key needed) — its result can't be known in advance, which is what actually proves the loop is reacting to tool output rather than following a scripted path. `calculate` is a safe arithmetic evaluator built on Python's `ast` module (walking a whitelist of node types), specifically to demonstrate *not* reaching for `eval()` on model-supplied input — a real security property, not just a style choice, verified with tests that try to smuggle code execution through it.

5. **No real OpenAI API key is available in this environment** (same limitation as the FastAPI/LangChain gateway exercise), so the "brain" deciding what to do next is `ScriptedLLMClient`, driven by small Python policy functions that inspect the running transcript — including genuinely live tool results — and decide the next step. This keeps the orchestration mechanics fully, deterministically testable while still proving real integration on the tool-execution side, and `OpenAIChatClient` is written to the identical interface specifically so it's a swap-in, not a rewrite, once a real key is available.
