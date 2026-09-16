"""Fast, offline, deterministic tests for the agent loop and its tools.

Uses synthetic in-memory tools here (not the real get_weather network call)
so this suite runs instantly and doesn't depend on network availability --
the live integration with the real open-meteo API is exercised separately
in solution.py's demo().
"""

import json

import pytest

from solution import (
    AgentGaveUpError,
    AssistantMessage,
    ScriptedLLMClient,
    Tool,
    ToolCall,
    _execute_tool_call,
    calculate,
    run_agent,
)

ECHO_TOOL = Tool(
    name="echo",
    description="Echoes back whatever it's given.",
    parameters_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    func=lambda text: {"echoed": text},
)


def _raising_tool_func(**_kwargs):
    raise RuntimeError("boom")


RAISING_TOOL = Tool(
    name="explode",
    description="Always raises.",
    parameters_schema={"type": "object", "properties": {}},
    func=_raising_tool_func,
)


# --- calculate() safety -----------------------------------------------------


@pytest.mark.parametrize(
    "expression, expected",
    [("2 + 2", 4), ("2 + 3 * 4", 14), ("(2 + 3) * 4", 20), ("10 / 4", 2.5), ("2 ** 8", 256), ("-5 + 3", -2)],
)
def test_calculate_arithmetic(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize(
    "malicious_expression",
    [
        "__import__('os').system('echo hacked')",
        "open('/etc/passwd').read()",
        "[].__class__.__base__.__subclasses__()",
        "1 if True else 2",
    ],
)
def test_calculate_rejects_anything_beyond_arithmetic(malicious_expression):
    with pytest.raises((ValueError, SyntaxError)):
        calculate(malicious_expression)


# --- _execute_tool_call: every failure mode becomes data, never raises -----


def test_execute_tool_call_unknown_tool_returns_error_json():
    result = _execute_tool_call(ToolCall("id1", "does_not_exist", "{}"), {})
    assert json.loads(result) == {"error": "unknown tool 'does_not_exist'"}


def test_execute_tool_call_malformed_json_returns_error_json():
    result = _execute_tool_call(ToolCall("id1", "echo", "{not json"), {"echo": ECHO_TOOL})
    assert "invalid JSON arguments" in json.loads(result)["error"]


def test_execute_tool_call_tool_exception_returns_error_json():
    result = _execute_tool_call(ToolCall("id1", "explode", "{}"), {"explode": RAISING_TOOL})
    assert json.loads(result) == {"error": "RuntimeError: boom"}


def test_execute_tool_call_success_returns_result_json():
    result = _execute_tool_call(ToolCall("id1", "echo", json.dumps({"text": "hi"})), {"echo": ECHO_TOOL})
    assert json.loads(result) == {"echoed": "hi"}


# --- run_agent: the orchestration loop --------------------------------------


def test_run_agent_no_tool_needed():
    policy = lambda messages: AssistantMessage("just an answer", [])
    answer, transcript = run_agent("hi", ScriptedLLMClient(policy), [ECHO_TOOL])
    assert answer == "just an answer"
    assert transcript[-1]["role"] == "assistant"


def test_run_agent_single_tool_round_trip():
    calls = {"n": 0}

    def policy(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return AssistantMessage(None, [ToolCall("c1", "echo", json.dumps({"text": "hello"}))])
        tool_result = json.loads(next(m["content"] for m in messages if m["role"] == "tool"))
        return AssistantMessage(f"the tool said: {tool_result['echoed']}", [])

    answer, transcript = run_agent("please echo hello", ScriptedLLMClient(policy), [ECHO_TOOL])
    assert answer == "the tool said: hello"
    tool_messages = [m for m in transcript if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "c1"


def test_run_agent_recovers_from_tool_error_instead_of_crashing():
    calls = {"n": 0}

    def policy(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return AssistantMessage(None, [ToolCall("c1", "explode", "{}")])
        error = json.loads(next(m["content"] for m in messages if m["role"] == "tool"))
        return AssistantMessage(f"handled: {error['error']}", [])

    answer, _ = run_agent("try the broken tool", ScriptedLLMClient(policy), [ECHO_TOOL, RAISING_TOOL])
    assert answer == "handled: RuntimeError: boom"


def test_run_agent_raises_after_max_iterations():
    policy = lambda messages: AssistantMessage(None, [ToolCall("c", "echo", json.dumps({"text": "again"}))])
    with pytest.raises(AgentGaveUpError) as excinfo:
        run_agent("never stop", ScriptedLLMClient(policy), [ECHO_TOOL], max_iterations=3)
    assert excinfo.value.iterations == 3
