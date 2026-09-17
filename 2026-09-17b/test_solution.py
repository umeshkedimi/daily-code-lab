import pytest

from solution import ExtractiveSummarizer, LLMSummarizer, Message, RollingConversationMemory

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _fill(memory: RollingConversationMemory, n: int, fact_prefix: str = "fact") -> None:
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        await memory.add_message(role, f"{fact_prefix}_{i}: value {i}")


async def test_raw_messages_stay_bounded_regardless_of_conversation_length():
    memory = RollingConversationMemory(ExtractiveSummarizer(), max_messages=20, keep_recent=10)
    await _fill(memory, 200)
    assert len(memory.messages) <= 20


async def test_no_rollover_below_threshold():
    memory = RollingConversationMemory(ExtractiveSummarizer(), max_messages=20, keep_recent=10)
    await _fill(memory, 15)
    assert memory.rollovers == 0
    assert memory.summary is None
    assert len(memory.messages) == 15


async def test_rollover_fires_exactly_once_at_threshold_crossing():
    memory = RollingConversationMemory(ExtractiveSummarizer(), max_messages=20, keep_recent=10)
    await _fill(memory, 20)
    assert memory.rollovers == 0  # exactly at the threshold, not yet over it
    await memory.add_message("user", "one more")
    assert memory.rollovers == 1
    assert len(memory.messages) == 10


async def test_early_information_survives_in_summary_after_being_trimmed_from_raw():
    memory = RollingConversationMemory(ExtractiveSummarizer(), max_messages=20, keep_recent=10)
    await _fill(memory, 50)

    assert not any("fact_0:" in m.content for m in memory.messages)  # trimmed from raw storage
    assert "fact_0:" in memory.summary  # but preserved in the rolling summary


async def test_summary_accumulates_across_multiple_rollovers_not_just_the_latest_batch():
    memory = RollingConversationMemory(ExtractiveSummarizer(), max_messages=20, keep_recent=10)
    await _fill(memory, 50)  # crosses the threshold 3 times with these settings

    assert memory.rollovers >= 2
    # facts from before the *first* rollover and facts folded in at a *later*
    # rollover must both still be present -- proves each rollover incorporates
    # the prior summary rather than replacing it.
    assert "fact_0:" in memory.summary
    assert "fact_25:" in memory.summary


async def test_get_context_puts_summary_first_then_verbatim_recent_messages_in_order():
    memory = RollingConversationMemory(ExtractiveSummarizer(), max_messages=20, keep_recent=10)
    await _fill(memory, 25)

    context = memory.get_context()
    assert context[0].role == "system"
    assert "Summary of earlier conversation" in context[0].content
    recent = context[1:]
    assert len(recent) == len(memory.messages)
    assert [m.content for m in recent] == [m.content for m in memory.messages]
    assert recent[-1].content == "fact_24: value 24"  # last message added is last in context, unsummarized


async def test_get_context_has_no_summary_message_before_any_rollover():
    memory = RollingConversationMemory(ExtractiveSummarizer(), max_messages=20, keep_recent=10)
    await _fill(memory, 5)
    context = memory.get_context()
    assert all(m.role != "system" for m in context)
    assert len(context) == 5


async def test_keep_recent_must_be_less_than_max_messages():
    with pytest.raises(ValueError):
        RollingConversationMemory(ExtractiveSummarizer(), max_messages=10, keep_recent=10)
    with pytest.raises(ValueError):
        RollingConversationMemory(ExtractiveSummarizer(), max_messages=10, keep_recent=15)


async def test_llm_summarizer_prompt_includes_prior_summary_and_new_messages():
    captured = {}

    async def fake_complete(prompt: str) -> str:
        captured["prompt"] = prompt
        return "new summary"

    summarizer = LLMSummarizer(fake_complete)
    result = await summarizer.summarize("prior summary text", [Message("user", "hello there")])

    assert result == "new summary"
    assert "prior summary text" in captured["prompt"]
    assert "hello there" in captured["prompt"]


async def test_llm_summarizer_handles_no_prior_summary():
    captured = {}

    async def fake_complete(prompt: str) -> str:
        captured["prompt"] = prompt
        return "first summary"

    summarizer = LLMSummarizer(fake_complete)
    await summarizer.summarize(None, [Message("user", "hi")])
    assert "none yet" in captured["prompt"]
