"""Rolling conversation-history summarization.

Once the raw message history exceeds a threshold, the overflow (everything
except a window of the most recent messages) is folded into a running
summary via a model call. The context handed to a model stays bounded in
size no matter how long the conversation runs, but nothing is silently
dropped -- older turns live on in summarized form instead of raw form.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Protocol


@dataclass(frozen=True)
class Message:
    role: str
    content: str


class Summarizer(Protocol):
    async def summarize(self, prior_summary: Optional[str], messages: List[Message]) -> str: ...


def _render_messages(messages: List[Message]) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


class LLMSummarizer:
    """Real implementation: one model call per rollover, given the prior
    summary (if any) plus the newly-overflowed messages, asked to fold them
    into one updated summary that preserves the important facts from both
    -- not just the new batch, which is what makes this 'rolling' rather
    than a fresh, amnesiac summary every time.
    """

    def __init__(self, complete: Callable[[str], Awaitable[str]]):
        # `complete` is injected -- bind it to a real model call (e.g. the
        # LLM gateway built earlier today, or a raw provider call) rather
        # than this class owning a specific provider client. Not exercised
        # live in this environment (no API key), the same limitation as
        # this week's other genai-agentic exercises.
        self._complete = complete

    async def summarize(self, prior_summary: Optional[str], messages: List[Message]) -> str:
        prior = prior_summary or "(none yet -- this is the first summary)"
        prompt = (
            "You maintain a running summary of an ongoing conversation.\n\n"
            f"Prior summary:\n{prior}\n\n"
            "New messages to fold in. Preserve every important fact from "
            "both the prior summary and these new messages -- do not drop "
            "anything material:\n"
            f"{_render_messages(messages)}\n\n"
            "Updated summary:"
        )
        return await self._complete(prompt)


class ExtractiveSummarizer:
    """Deterministic, offline stand-in for testing/demo without a real
    model call. Not a real abstractive summary -- a naive extractive fold
    -- but it still demonstrably (a) incorporates the prior summary on
    every rollover, so nothing before the very first rollover is lost, and
    (b) carries forward one line per message rather than the whole
    transcript verbatim, which is enough to prove the *mechanism* (nothing
    silently dropped) independent of summary *quality* (which depends on
    the real model used).
    """

    async def summarize(self, prior_summary: Optional[str], messages: List[Message]) -> str:
        lines = [] if prior_summary is None else [prior_summary]
        lines += [f"- {m.role} said: {m.content}" for m in messages]
        return "\n".join(lines)


class RollingConversationMemory:
    def __init__(self, summarizer: Summarizer, max_messages: int = 20, keep_recent: int = 10):
        if keep_recent >= max_messages:
            raise ValueError("keep_recent must be less than max_messages, or every rollover would be a no-op")
        self._summarizer = summarizer
        self._max_messages = max_messages
        self._keep_recent = keep_recent
        self.messages: List[Message] = []
        self.summary: Optional[str] = None
        self.rollovers = 0  # observability: how many times summarization has actually fired

    async def add_message(self, role: str, content: str) -> None:
        self.messages.append(Message(role, content))
        if len(self.messages) > self._max_messages:
            await self._rollover()

    async def _rollover(self) -> None:
        overflow = self.messages[: len(self.messages) - self._keep_recent]
        self.summary = await self._summarizer.summarize(self.summary, overflow)
        self.messages = self.messages[len(self.messages) - self._keep_recent :]
        self.rollovers += 1

    def get_context(self) -> List[Message]:
        """What you'd actually hand to a model: the rolling summary (if one
        exists yet) as a system message, followed by the verbatim recent
        messages -- bounded in size regardless of how long the real
        conversation has run."""
        if self.summary is None:
            return list(self.messages)
        return [Message("system", f"Summary of earlier conversation:\n{self.summary}")] + list(self.messages)


# --- Demo --------------------------------------------------------------------


async def demo() -> None:
    memory = RollingConversationMemory(ExtractiveSummarizer(), max_messages=20, keep_recent=10)

    print("1) Feeding 50 messages, each carrying a unique, checkable fact")
    for i in range(50):
        role = "user" if i % 2 == 0 else "assistant"
        await memory.add_message(role, f"fact_{i}: the secret number for turn {i} is {i * 7}")

    print(f"   raw messages retained: {len(memory.messages)} (bounded, not 50)")
    print(f"   rollovers fired: {memory.rollovers}")

    context = memory.get_context()
    print(f"   get_context() size: {len(context)} messages (summary + recent window)")

    print("\n2) Proving nothing was silently lost: fact_0 is long gone from raw storage...")
    raw_has_fact_0 = any("fact_0:" in m.content for m in memory.messages)
    print(f"   fact_0 present in raw messages? {raw_has_fact_0}")
    summary_has_fact_0 = "fact_0:" in (memory.summary or "")
    print(f"   fact_0 present in the rolling summary? {summary_has_fact_0}")

    print("\n3) ...and so is a fact from partway through, folded in across multiple rollovers")
    summary_has_fact_25 = "fact_25:" in (memory.summary or "")
    print(f"   fact_25 present in the rolling summary? {summary_has_fact_25}")

    print("\n4) Recent messages stay verbatim (not summarized) in the returned context")
    recent_contents = [m.content for m in context if m.role != "system"]
    print(f"   most recent message in context: {recent_contents[-1]!r}")

    print("\n5) LLMSummarizer builds a correct prompt around a real (here: stubbed) model call")

    captured_prompts = []

    async def stub_complete(prompt: str) -> str:
        captured_prompts.append(prompt)
        return "STUBBED MODEL SUMMARY"

    llm_memory = RollingConversationMemory(LLMSummarizer(stub_complete), max_messages=20, keep_recent=10)
    for i in range(21):
        await llm_memory.add_message("user", f"message {i}")
    print(f"   prompt sent to the model call included prior summary placeholder: "
          f"{'(none yet' in captured_prompts[0]}")
    print(f"   resulting summary: {llm_memory.summary!r}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(demo())
