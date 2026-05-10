from __future__ import annotations

import re
from typing import Any, cast

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from react_agent.context import Context
from react_agent.prompts.decompile_prompt import build_decompile_system_prompt
from react_agent.prompts.equivalence_prompt import build_verify_system_prompt
from react_agent.state import State
from react_agent.tools import load_runtime_tools
from react_agent.utils import get_message_text, latest_ai_text, load_chat_model





def decompile_loop_router_node(state: State) -> dict[str, Any]:
    if not state.function_queue:
        return {
            "stop": True,
            "current_function": None,
            "session_phase": "ready",
        }

    current_function = state.function_queue[0]
    remaining_queue = state.function_queue[1:]
    return {
        "stop": False,
        "current_function": current_function,
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
                *state.messages,
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
                *state.messages,
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

    asm_text = ""
    last_error: Exception | None = None

    tool_attempts = [
        (
            "analyze_function",
            [
                {"name": current_function, "include_asm": True},
                {"function": current_function, "include_asm": True},
                {"function_name": current_function, "include_asm": True},
            ],
        ),
        (
            "disasm",
            [
                {"name": current_function},
                {"function": current_function},
                {"function_name": current_function},
            ],
        ),
    ]

    for tool_name, payloads in tool_attempts:
        tool = tool_by_name.get(tool_name)
        if tool is None:
            continue

        for payload in payloads:
            try:
                result = await tool.ainvoke(payload)
                asm_text = result if isinstance(result, str) else str(result)
                if asm_text.strip():
                    break
            except Exception as exc:
                last_error = exc

        if asm_text.strip():
            break

    discovered_callees: list[str] = []
    discovered_seen: set[str] = set()

    call_pattern = re.compile(
        r"\b(?:"
        r"lcr\s+(?P<lcr>[A-Za-z_.$?@][\w.$?@]*)"
        r"|"
        r"ffc\s+[^,\n]+,\s*(?P<ffc>[A-Za-z_.$?@][\w.$?@]*)"
        r")",
        flags=re.IGNORECASE,
    )

    for match in call_pattern.finditer(asm_text):
        callee = (match.group("lcr") or match.group("ffc") or "").strip()

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

    remaining_queue = []
    for name in state.function_queue:
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

    updated_queue = discovered_callees + remaining_queue

    evidence_gaps = list(state.verification_evidence_gaps)
    if not asm_text.strip():
        reason = "scan_callees_node 未能通过 MCP 获取当前函数反汇编"
        if last_error is not None:
            reason = f"{reason}: {last_error}"
        evidence_gaps.append(reason)

    return {
        "completed_functions": completed_functions,
        "function_queue": updated_queue,
        "current_function": None,
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


def decompile_finish_node(state: State) -> dict[str, Any]:
    return {
        "stop": True,
        "current_function": None,
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
