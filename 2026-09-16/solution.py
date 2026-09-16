"""A tool-calling agent loop, built from raw chat-completion + tool-calling
primitives -- no LangChain agent executor, no agent framework. The wire
format (ToolCall/AssistantMessage) mirrors OpenAI's actual chat.completions
tools API exactly, so a real OpenAIChatClient drops into run_agent() without
the loop itself changing at all.
"""

from __future__ import annotations

import ast
import json
import operator
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Protocol

import httpx


# --- Wire format (mirrors OpenAI's chat.completions tool-calling shape) ----


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string, exactly as a real LLM API returns it


@dataclass(frozen=True)
class AssistantMessage:
    content: Optional[str]
    tool_calls: List[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    def complete(self, messages: List[dict], tool_schemas: List[dict]) -> AssistantMessage: ...


# --- Tools ------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters_schema: dict
    func: Callable[..., Any]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval(node.operand))
    raise ValueError(f"disallowed expression node: {ast.dump(node)}")


def calculate(expression: str) -> float:
    """Evaluate basic arithmetic safely -- parses to an AST and walks only a
    known-safe subset of nodes, never calls eval() on the raw string."""
    tree = ast.parse(expression, mode="eval")
    return _safe_eval(tree)


def get_weather(city: str) -> dict:
    """Real network call: geocode the city, then fetch current weather
    (open-meteo -- same free, no-API-key service used in the async fetch
    exercise). This is the one tool that's genuinely live, not simulated."""
    with httpx.Client(timeout=10.0) as client:
        geo = client.get(
            "https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1}
        ).json()
        if not geo.get("results"):
            raise ValueError(f"unknown city: {city!r}")
        lat, lon = geo["results"][0]["latitude"], geo["results"][0]["longitude"]
        weather = client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current_weather": "true"},
        ).json()
    current = weather["current_weather"]
    return {"city": city, "temperature_c": current["temperature"], "windspeed_kmh": current["windspeed"]}


CALCULATE_TOOL = Tool(
    name="calculate",
    description="Evaluate a basic arithmetic expression, e.g. '2 + 2 * 3'.",
    parameters_schema={
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
    func=calculate,
)

GET_WEATHER_TOOL = Tool(
    name="get_weather",
    description="Get the current temperature (C) and windspeed for a city.",
    parameters_schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
    func=get_weather,
)


# --- Agent loop ---------------------------------------------------------------


class AgentGaveUpError(Exception):
    def __init__(self, iterations: int):
        super().__init__(f"agent did not reach a final answer within {iterations} iterations")
        self.iterations = iterations


def run_agent(
    user_message: str,
    llm: LLMClient,
    tools: List[Tool],
    system_prompt: str = "You are a helpful assistant. Use tools when needed.",
    max_iterations: int = 6,
) -> tuple:
    """Runs the tool-calling loop until the model returns a message with no
    tool_calls (a final answer), or raises AgentGaveUpError past
    max_iterations -- the guard against an infinite loop / runaway cost.

    Returns (final_answer, transcript) so callers/tests can inspect exactly
    what happened at each step, not just the end result.
    """
    tools_by_name = {t.name: t for t in tools}
    schemas = [t.schema() for t in tools]
    messages: List[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for _ in range(max_iterations):
        assistant = llm.complete(messages, schemas)
        messages.append(
            {
                "role": "assistant",
                "content": assistant.content,
                "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in assistant.tool_calls],
            }
        )

        if not assistant.tool_calls:
            return assistant.content or "", messages

        for tool_call in assistant.tool_calls:
            result = _execute_tool_call(tool_call, tools_by_name)
            messages.append(
                {"role": "tool", "tool_call_id": tool_call.id, "name": tool_call.name, "content": result}
            )

    raise AgentGaveUpError(max_iterations)


def _execute_tool_call(tool_call: ToolCall, tools_by_name: dict) -> str:
    """Never raises -- every failure mode (unknown tool, malformed
    arguments, the tool itself throwing) becomes a tool-result string fed
    back to the model, so one bad tool call can't crash the whole agent."""
    tool = tools_by_name.get(tool_call.name)
    if tool is None:
        return json.dumps({"error": f"unknown tool {tool_call.name!r}"})
    try:
        args = json.loads(tool_call.arguments)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid JSON arguments: {exc}"})
    try:
        result = tool.func(**args)
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
    return json.dumps(result if isinstance(result, (dict, list)) else {"result": result})


# --- LLM clients --------------------------------------------------------------


class ScriptedLLMClient:
    """A deterministic stand-in LLM for testing/demo without a real API key.

    Takes a `policy(messages) -> AssistantMessage` callable that inspects
    the running transcript (including real tool results already in it) and
    decides the next step -- so it reacts to genuinely live data (e.g. a
    real temperature from get_weather) exactly like a real model would,
    while remaining fully deterministic and offline itself.
    """

    def __init__(self, policy: Callable[[List[dict]], AssistantMessage]):
        self._policy = policy
        self.calls = 0

    def complete(self, messages: List[dict], tool_schemas: List[dict]) -> AssistantMessage:
        self.calls += 1
        return self._policy(messages)


class OpenAIChatClient:
    """Real implementation targeting OpenAI's chat.completions tool-calling
    API. Not exercised live in this environment -- no OpenAI API key is
    available here, the same limitation noted for the FastAPI/LangChain
    gateway exercise. Included to show the wire format ScriptedLLMClient
    emulates is exactly this API's shape: swapping this in for the stub in
    run_agent() requires no change to the loop itself.
    """

    def __init__(self, model: str = "gpt-4o-mini"):
        import openai

        self._client = openai.OpenAI()
        self._model = model

    def complete(self, messages: List[dict], tool_schemas: List[dict]) -> AssistantMessage:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tool_schemas or None,
        )
        choice = response.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            for tc in (choice.tool_calls or [])
        ]
        return AssistantMessage(content=choice.content, tool_calls=tool_calls)


# --- Demo ----------------------------------------------------------------------


def _tool_result(messages: List[dict], name: str) -> Optional[dict]:
    for m in messages:
        if m["role"] == "tool" and m["name"] == name:
            return json.loads(m["content"])
    return None


def _weather_then_calc_policy(messages: List[dict]) -> AssistantMessage:
    """Simulates: 'What's the temperature in Berlin, plus 10?' Reacts to the
    *real* temperature returned by the live get_weather call."""
    weather = _tool_result(messages, "get_weather")
    if weather is None:
        return AssistantMessage(None, [ToolCall("call_1", "get_weather", json.dumps({"city": "Berlin"}))])
    if "error" in weather:
        return AssistantMessage(f"I couldn't get the weather for Berlin: {weather['error']}", [])

    calc = _tool_result(messages, "calculate")
    if calc is None:
        expr = f"{weather['temperature_c']} + 10"
        return AssistantMessage(None, [ToolCall("call_2", "calculate", json.dumps({"expression": expr}))])

    return AssistantMessage(f"The temperature in Berlin is {weather['temperature_c']}C; plus 10 is {calc['result']}.", [])


def _unknown_tool_then_recover_policy(messages: List[dict]) -> AssistantMessage:
    """Simulates a model hallucinating a tool that doesn't exist, then
    recovering once it sees the error fed back."""
    errors = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
    if not errors:
        return AssistantMessage(None, [ToolCall("call_1", "lookup_stock_price", json.dumps({"ticker": "ACME"}))])
    return AssistantMessage(f"I don't have a way to do that: {errors[0].get('error')}", [])


def _bad_json_then_recover_policy(messages: List[dict]) -> AssistantMessage:
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    if not tool_msgs:
        return AssistantMessage(None, [ToolCall("call_1", "calculate", "{not valid json")])
    error = json.loads(tool_msgs[0]["content"])
    return AssistantMessage(f"My tool call was malformed: {error['error']}", [])


def _tool_exception_then_recover_policy(messages: List[dict]) -> AssistantMessage:
    weather = _tool_result(messages, "get_weather")
    if weather is None:
        return AssistantMessage(
            None, [ToolCall("call_1", "get_weather", json.dumps({"city": "Nonexistentcityxyz123"}))]
        )
    return AssistantMessage(f"That city lookup failed: {weather.get('error')}", [])


def _never_finishes_policy(messages: List[dict]) -> AssistantMessage:
    """Always asks for another (harmless) tool call -- proves max_iterations
    actually bounds the loop instead of spinning forever."""
    return AssistantMessage(None, [ToolCall("call_x", "calculate", json.dumps({"expression": "1 + 1"}))])


def _no_tool_needed_policy(messages: List[dict]) -> AssistantMessage:
    return AssistantMessage("Hello! How can I help you today?", [])


def demo():
    tools = [GET_WEATHER_TOOL, CALCULATE_TOOL]

    print("1) Simple case: no tool needed at all")
    answer, transcript = run_agent("hi", ScriptedLLMClient(_no_tool_needed_policy), tools)
    print(f"   answer: {answer!r} ({len(transcript)} messages)")

    print("\n2) Multi-step tool chain with a REAL live tool call (get_weather -> calculate)")
    answer, transcript = run_agent(
        "What's the temperature in Berlin, plus 10?", ScriptedLLMClient(_weather_then_calc_policy), tools
    )
    print(f"   answer: {answer}")
    print(f"   transcript had {sum(1 for m in transcript if m['role'] == 'tool')} tool result(s)")

    print("\n3) Unknown tool requested: fails fast into a fed-back error, then the model recovers")
    answer, _ = run_agent("look up ACME's stock price", ScriptedLLMClient(_unknown_tool_then_recover_policy), tools)
    print(f"   answer: {answer}")

    print("\n4) Malformed tool-call arguments: caught, fed back, model recovers")
    answer, _ = run_agent("do some math", ScriptedLLMClient(_bad_json_then_recover_policy), tools)
    print(f"   answer: {answer}")

    print("\n5) Tool itself raises (real API call, real 'unknown city' error): caught, fed back, model recovers")
    answer, _ = run_agent("weather in a fake city", ScriptedLLMClient(_tool_exception_then_recover_policy), tools)
    print(f"   answer: {answer}")

    print("\n6) Runaway loop guard: a policy that never produces a final answer")
    try:
        run_agent("never stop", ScriptedLLMClient(_never_finishes_policy), tools, max_iterations=4)
        print("   ERROR: should have raised AgentGaveUpError")
    except AgentGaveUpError as exc:
        print(f"   correctly gave up: {exc} (iterations={exc.iterations})")


if __name__ == "__main__":
    demo()
