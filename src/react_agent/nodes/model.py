"""Main ReAct model node."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Dict, List, cast

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from react_agent.context import Context
from react_agent.state import State
from react_agent.tools import TOOLS
from react_agent.utils import load_chat_model


def _format_initialization_context(state: State) -> str:
    """Expose trusted initialization facts to the LLM."""

    return "\n".join(
        [
            f"task_mode: {state.task_mode}",
            f"source_mode: {state.source_mode}",
            f"user_goal: {state.user_goal}",
            f"function_queue: {state.function_queue}",
            f"authorized_paths: {[item.model_dump() for item in state.authorized_paths]}",
            f"source_files: {[item.model_dump() for item in state.source_files]}",
            f"mcp_required: {state.mcp_required}",
            f"mcp_connect_status: {state.mcp_connect_status}",
        ]
    )


async def call_model(
    state: State, runtime: Runtime[Context]
) -> Dict[str, List[AIMessage]]:
    """Call the LLM after initialization has prepared a task context."""

    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
    ).bind_tools(TOOLS)

    system_message = runtime.context.system_prompt.format(
        system_time=datetime.now(tz=UTC).isoformat()
    )
    system_message = (
        f"{system_message}\n\n"
        "## Initialization Context\n"
        f"{_format_initialization_context(state)}"
    )

    response = cast(
        AIMessage,
        await model.ainvoke(
            [{"role": "system", "content": system_message}, *state.messages]
        ),
    )

    if state.is_last_step and response.tool_calls:
        return {
            "messages": [
                AIMessage(
                    id=response.id,
                    content="抱歉，我没有在指定步数内完成任务。",
                )
            ]
        }

    return {"messages": [response]}
