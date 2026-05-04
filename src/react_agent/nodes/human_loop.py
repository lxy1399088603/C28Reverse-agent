"""Human-in-the-loop node for missing required initialization data."""

from __future__ import annotations

from typing import Literal, Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command, interrupt

from react_agent.state import State


def execution_prepare_node(state: State) -> dict[str, Any]:
    """Mark initialization complete once the minimum execution context exists."""

    if state.needs_user_input:
        return {}

    if not state.function_queue:
        return {
            "initialization_complete": False,
            "needs_user_input": True,
            "blocking_reason": "函数队列为空，无法开始执行。",
            "missing_requirements": ["function_queue"],
        }

    return {
        "initialization_complete": True,
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }

def ask_missing_info_node(
    state: State,
) -> Command[Literal["task_intake_node"]]:
    """Pause the graph and wait for the user to provide missing information."""

    reason = state.blocking_reason or "缺少继续执行所需的信息。"
    user_input = interrupt(
        {
            "question": reason,
            "missing_requirements": state.missing_requirements,
        }
    )

    # resume 后把补充内容作为新的 HumanMessage 写入 State，再回到 intake 重新理解。
    return Command(
        update={
            "messages": [HumanMessage(content=str(user_input))],
            "needs_user_input": False,
            "blocking_reason": None,
            "missing_requirements": [],
        },
        goto="task_intake_node",
    )
