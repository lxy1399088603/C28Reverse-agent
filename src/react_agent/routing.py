"""Routing functions for the LangGraph workflow."""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import AIMessage

from react_agent.state import State


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
