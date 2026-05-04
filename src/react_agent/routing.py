"""Routing functions for the LangGraph workflow."""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import AIMessage

from react_agent.state import State


def route_from_session_entry(
    state: State,
) -> Literal[
    "task_intake_node",
    "target_intake_node",
    "path_intake_node",
    "check_mcp_node",
    "call_model",
]:
    """Route a restored thread to the right phase instead of restarting intake.

    missing_requirements 是 human loop 留下来的“补什么”信号。resume 后入口
    根据它把用户补充内容送到对应节点，而不是重新跑完整模式识别。
    """

    missing = set(state.missing_requirements)

    if missing & {"function_target", "function_names", "entry_points", "function_queue"}:
        return "target_intake_node"

    if missing & {"asm_source", "authorized_path", "path_candidates", "workspace_path"}:
        return "path_intake_node"

    if missing & {"mcp_connection", "mcp_tools"}:
        return "check_mcp_node"

    if state.initialization_complete:
        return "call_model"

    return "task_intake_node"


def route_after_paths(
    state: State,
) -> Literal["ask_missing_info_node", "check_mcp_node"]:
    if state.needs_user_input:
        return "ask_missing_info_node"
    return "check_mcp_node"


def route_after_mcp(
    state: State,
) -> Literal["ask_missing_info_node", "validate_targets_node"]:
    if state.needs_user_input:
        return "ask_missing_info_node"
    return "validate_targets_node"


def route_after_targets(
    state: State,
) -> Literal["ask_missing_info_node", "execution_prepare_node"]:
    if state.needs_user_input:
        return "ask_missing_info_node"
    return "execution_prepare_node"


def route_after_prepare(
    state: State,
) -> Literal["ask_missing_info_node", "call_model"]:
    if state.needs_user_input:
        return "ask_missing_info_node"
    return "call_model"


def route_model_output(state: State) -> Literal["__end__", "tools"]:
    """Continue the ReAct loop only when the model emitted tool calls."""

    last_message = state.messages[-1]
    if not isinstance(last_message, AIMessage):
        raise ValueError(
            f"Expected AIMessage in model output, got {type(last_message).__name__}"
        )

    if not last_message.tool_calls:
        return "__end__"
    return "tools"
