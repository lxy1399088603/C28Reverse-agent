from __future__ import annotations

from typing import Any, cast

from langchain_core.messages import AIMessage, AnyMessage, RemoveMessage
from langgraph.runtime import Runtime

from react_agent.context import Context
from react_agent.prompts.decompile_prompt import build_decompile_system_prompt
from react_agent.prompts.equivalence_prompt import build_verify_system_prompt
from react_agent.state import State
from react_agent.tools import load_runtime_tools
from react_agent.utils import latest_ai_text, load_chat_model


def _first_human_message(messages: list[AnyMessage]) -> AnyMessage | None:
    for message in messages:
        if getattr(message, "type", None) == "human":
            return message
    return None


def _messages_after_round_boundary(
    messages: list[AnyMessage],
    boundary_message_id: str | None,
) -> list[AnyMessage]:
    """Return messages written after the workflow selected current_function."""

    if not boundary_message_id:
        return messages

    for index, message in enumerate(messages):
        if getattr(message, "id", None) == boundary_message_id:
            return messages[index + 1 :]

    return messages


def _slice_current_round_messages(state: State) -> list[AnyMessage]:
    """Keep the original user task plus only this function round's evidence."""

    messages = list(state.messages)
    first_human = _first_human_message(messages)
    tail = _messages_after_round_boundary(
        messages,
        state.current_round_start_message_id,
    )

    sliced: list[AnyMessage] = []
    if first_human is not None and first_human not in tail:
        sliced.append(first_human)
    sliced.extend(tail)
    return sliced


def _remove_current_round_messages(state: State) -> list[RemoveMessage]:
    """Build removals for bulky AI/tool messages from the completed round."""

    if not state.current_round_start_message_id:
        return []

    messages = list(state.messages)
    if not any(
        getattr(message, "id", None) == state.current_round_start_message_id
        for message in messages
    ):
        return []

    removals: list[RemoveMessage] = []
    for message in _messages_after_round_boundary(
        messages,
        state.current_round_start_message_id,
    ):
        if getattr(message, "type", None) not in {"ai", "tool"}:
            continue
        message_id = getattr(message, "id", None)
        if message_id:
            removals.append(RemoveMessage(id=message_id))
    return removals


def _is_symbol_name(name: str) -> bool:
    if not name:
        return False

    allowed_punct = {"_", ".", "$", "?", "@"}
    first = name[0]
    if not (first.isalpha() or first in allowed_punct):
        return False

    for char in name[1:]:
        if char.isalnum() or char in allowed_punct:
            continue
        return False

    return True


def _extract_callee_names(raw_result: Any) -> list[str]:
    """Read direct callee names from ida-pro-mcp get_callee_name JSON output."""

    if isinstance(raw_result, dict):
        names = raw_result.get("names", [])
        if isinstance(names, list):
            return [str(item).strip() for item in names]
        return []

    if isinstance(raw_result, list):
        return [str(item).strip() for item in raw_result]

    return []


def _filter_discovered_callees(
    raw_names: list[str],
    current_function: str,
    completed_functions: list[str],
) -> list[str]:
    completed = set(completed_functions)
    seen: set[str] = set()
    filtered: list[str] = []

    for raw_name in raw_names:
        name = raw_name.strip()
        if not name:
            continue
        if name == current_function:
            continue
        if name in completed:
            continue
        if not _is_symbol_name(name):
            continue
        if name in seen:
            continue
        seen.add(name)
        filtered.append(name)

    return filtered


def _merge_discovered_callees(
    current_function: str,
    completed_functions: list[str],
    queued_functions: list[str],
    *callee_groups: list[str],
) -> list[str]:
    """Depth-first merge for newly discovered callees and the remaining queue."""

    discovered_seen: set[str] = set()
    discovered_callees: list[str] = []

    for group in callee_groups:
        for raw_name in group:
            callee = raw_name.strip()
            if not callee:
                continue
            if callee == current_function:
                continue
            if callee in completed_functions:
                continue
            if callee in discovered_seen:
                continue
            discovered_seen.add(callee)
            discovered_callees.append(callee)

    remaining_queue: list[str] = []
    for name in queued_functions:
        function_name = name.strip()
        if not function_name:
            continue
        if function_name == current_function:
            continue
        if function_name in completed_functions:
            continue
        if function_name in discovered_seen:
            continue
        remaining_queue.append(function_name)

    return discovered_callees + remaining_queue


def decompile_loop_router_node(state: State) -> dict[str, Any]:
    if len(state.completed_functions) >= state.max_chain_depth:
        return {
            "stop": True,
            "current_function": None,
            "current_round_start_message_id": None,
            "session_phase": "ready",
        }

    if not state.function_queue:
        return {
            "stop": True,
            "current_function": None,
            "current_round_start_message_id": None,
            "session_phase": "ready",
        }

    current_function = state.function_queue[0]
    remaining_queue = state.function_queue[1:]
    last_message = state.messages[-1] if state.messages else None
    return {
        "stop": False,
        "current_function": current_function,
        "current_round_start_message_id": getattr(last_message, "id", None),
        "function_queue": remaining_queue,
        "session_phase": "running",
    }


async def call_model_decompile(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    if not state.current_function:
        raise ValueError("call_model_decompile requires state.current_function")

    tools = await load_runtime_tools(state, runtime.context)
    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
        api_key=runtime.context.api_key,
    ).bind_tools(tools)

    system_message = build_decompile_system_prompt(
        state=state,
        base_system_prompt=runtime.context.system_prompt,
    )

    response = cast(
        AIMessage,
        await model.ainvoke(
            [
                {"role": "system", "content": system_message},
                *_slice_current_round_messages(state),
            ]
        ),
    )

    if state.is_last_step and response.tool_calls:
        return {
            "messages": [
                AIMessage(
                    id=response.id,
                    content=(
                        "当前函数还原未能在限定步数内完成。"
                        "请将该函数标记为 partial 或 failed，并记录需要补充的证据。"
                    ),
                )
            ],
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": "当前函数 MCP/LLM 工具调用轮次耗尽。",
            "missing_requirements": ["decompile_step_budget"],
        }

    return {
        "messages": [response],
        "session_phase": "running",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }


async def function_verify_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    if not state.current_function:
        raise ValueError("function_verify_node requires state.current_function")

    decompile_text = state.current_decompile_text or latest_ai_text(state)
    if not decompile_text.strip():
        raise ValueError("function_verify_node requires decompile result")

    tools = await load_runtime_tools(state, runtime.context)
    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
        api_key=runtime.context.api_key,
    ).bind_tools(tools)

    system_message = build_verify_system_prompt(
        state=state,
        decompile_text=decompile_text,
        base_system_prompt=runtime.context.system_prompt,
    )

    response = cast(
        AIMessage,
        await model.ainvoke(
            [
                {"role": "system", "content": system_message},
                *_slice_current_round_messages(state),
            ]
        ),
    )

    return {
        "messages": [response],
        "session_phase": "running",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
        "current_decompile_text": decompile_text,
    }


async def scan_callees_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    current_function = (state.current_function or "").strip()
    if not current_function:
        raise ValueError("scan_callees_node requires state.current_function")

    completed_functions = list(state.completed_functions)
    if current_function not in completed_functions:
        completed_functions.append(current_function)

    tools = await load_runtime_tools(state, runtime.context)
    tool_by_name = {tool.name: tool for tool in tools}

    discovered_from_mcp: list[str] = []
    evidence_gaps = list(state.verification_evidence_gaps)
    callee_tool = tool_by_name.get("get_callee_name")

    if callee_tool is None:
        evidence_gaps.append(
            "scan_callees_node could not get direct callees: "
            "ida-pro-mcp tool get_callee_name is unavailable"
        )
    else:
        try:
            raw_result = await callee_tool.ainvoke({"target": current_function})
            discovered_from_mcp = _filter_discovered_callees(
                _extract_callee_names(raw_result),
                current_function,
                completed_functions,
            )
        except Exception as exc:
            evidence_gaps.append(
                "scan_callees_node could not get direct callees "
                f"from get_callee_name: {exc}"
            )

    updated_queue = _merge_discovered_callees(
        current_function,
        completed_functions,
        state.function_queue,
        discovered_from_mcp,
    )

    result: dict[str, Any] = {
        "completed_functions": completed_functions,
        "function_queue": updated_queue,
        "current_function": None,
        "current_round_start_message_id": None,
        "current_decompile_text": None,
        "verification_retry_count": 0,
        "verification_reason": None,
        "verification_status": None,
        "verification_evidence_gaps": evidence_gaps,
        "verification_retry_hint": None,
        "session_phase": "running" if updated_queue else "ready",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }
    removals = _remove_current_round_messages(state)
    if removals:
        result["messages"] = removals
    return result


def decompile_finish_node(state: State) -> dict[str, Any]:
    return {
        "stop": True,
        "current_function": None,
        "current_round_start_message_id": None,
        "function_queue": [],
        "completed_functions": list(state.completed_functions),
        "failed_functions": list(state.failed_functions),
        "current_decompile_text": None,
        "verification_retry_count": 0,
        "verification_reason": None,
        "verification_status": None,
        "verification_evidence_gaps": [],
        "verification_retry_hint": None,
        "session_phase": "ready",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }
