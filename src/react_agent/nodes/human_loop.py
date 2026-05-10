"""Human-in-the-loop node for missing required initialization data."""

from __future__ import annotations

from typing import Literal, Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command, interrupt

from react_agent.state import State


# 准备节点，当函数队列
def execution_prepare_node(state: State) -> dict[str, Any]:

    if not state.function_queue:
        return {
            "session_phase": "blocked",
            "initialization_complete": False,
            "needs_user_input": True,
            "blocking_reason": "函数队列为空，无法开始执行。",
            "missing_requirements": ["function_queue"],
            "last_blocking_node": "execution_prepare_node",
        }

    return {
        "session_phase": "ready",
        "initialization_complete": True,
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }


# 人机环路阻塞
def ask_missing_info_node(
    state: State,
) -> Command[Literal["session_entry_node"]]:

    reason = state.blocking_reason or "缺少继续执行所需的信息。"
    user_input = interrupt(
        {
            "question": reason,
            "missing_requirements": state.missing_requirements,
        }
    )

    # resume 后把补充内容作为新的 HumanMessage 写入 State，再回到 session_entry_node。
    # 注意：这里不要清空 missing_requirements / last_blocking_node。
    # 入口节点需要这些字段判断“用户这句话是在补函数、补路径，还是补 MCP”。
    return Command(
        update={
            "messages": [HumanMessage(content=str(user_input))],
            "needs_user_input": False,
            "blocking_reason": None,
        },
        goto="session_entry_node",
    )
