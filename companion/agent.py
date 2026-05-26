"""
Agent mode for Elwin — multi-turn tool-use loop.
Python port of the agentic core from TOPSECRET (Claude Code).

The LLM is given a set of tools. It can call them, see the results,
and call more tools, until it produces a final text response.

Usage:
    from companion.agent import run_agent
    for token in run_agent(messages, on_tool_call=..., on_tool_result=...):
        print(token, end='', flush=True)
"""

import json
import logging
from typing import Callable, Generator

from . import config, llm_client, mcp_client
from .tools import all_api_schemas, execute_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10  # prevent runaway loops

_AGENT_SYSTEM_SUFFIX = """
You have access to tools that let you take real actions:

Local machine:
- bash: run shell commands (safety-checked)
- read_file, write_file, edit_file: work with files
- glob: find files by pattern
- grep: search file contents

Web:
- web_search: Brave Search for current information
- web_fetch: fetch and read a specific URL

Any MCP-connected tools (prefixed with server name) are also available.

Think step by step: plan, call tools one at a time, inspect results, then give a
final answer. Never fabricate tool results — if a tool errors, report it honestly.
"""


def run_agent(
    messages: list[dict],
    on_tool_call: Callable[[str, dict], None] | None = None,
    on_tool_result: Callable[[str, str], None] | None = None,
    on_llm_call: Callable[[], None] | None = None,
) -> Generator[str, None, None]:
    """
    Run the agent loop. Yields text tokens as the final response is produced.

    Args:
        messages:       Full conversation history (system + user turns).
        on_tool_call:   Called with (tool_name, params) before each execution.
        on_tool_result: Called with (tool_name, result_text) after each execution.

    Yields:
        str tokens of the final assistant response.
    """
    # Load MCP tools on first agent run (idempotent)
    mcp_client.load_mcp_tools()

    tools = all_api_schemas()

    # Inject agent instructions into the system prompt
    msgs = _inject_agent_suffix(messages)

    for round_num in range(MAX_TOOL_ROUNDS):
        if on_llm_call:
            on_llm_call()
        try:
            resp = llm_client.chat_with_tools(msgs, tools)
        except Exception as e:
            yield f"\n[Agent error: {e}]"
            return

        choice = resp["choices"][0]
        message = choice["message"]
        finish = choice.get("finish_reason", "stop")

        # Append the assistant's turn to history
        msgs.append(message)

        tool_calls = message.get("tool_calls") or []

        # No tool calls → final response, stream it
        if finish != "tool_calls" or not tool_calls:
            content = message.get("content") or ""
            yield from content
            return

        # Execute each tool call and collect results
        tool_results: list[dict] = []
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")

            try:
                params = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                params = {}

            logger.info("Agent calling tool: %s %s", name, params)

            if on_tool_call:
                on_tool_call(name, params)

            result = execute_tool(name, params)

            logger.info("Tool %s result length: %d", name, len(result))

            if on_tool_result:
                on_tool_result(name, result)

            tool_results.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "name": name,
                "content": result,
            })

        msgs.extend(tool_results)

    # Fallback after too many rounds: ask for a plain summary
    msgs.append({
        "role": "user",
        "content": "Please summarize what you found and give a final answer.",
    })
    try:
        resp = llm_client.chat_with_tools(msgs, [])
        content = resp["choices"][0]["message"].get("content") or ""
        yield from content
    except Exception as e:
        yield f"\n[Agent: max tool rounds reached, error getting summary: {e}]"


def _inject_agent_suffix(messages: list[dict]) -> list[dict]:
    """Append agent instructions to the system message."""
    msgs = list(messages)
    for i, m in enumerate(msgs):
        if m.get("role") == "system":
            msgs[i] = {**m, "content": m["content"] + _AGENT_SYSTEM_SUFFIX}
            return msgs
    # No system message found — prepend one
    msgs.insert(0, {"role": "system", "content": _AGENT_SYSTEM_SUFFIX.strip()})
    return msgs
