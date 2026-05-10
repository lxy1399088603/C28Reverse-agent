"""Main ReAct model node."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Dict, List, cast

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from react_agent.context import Context
from react_agent.state import State
from react_agent.utils import load_chat_model
from react_agent.tools import load_runtime_tools


# 结构化上下文信息
def _format_initialization_context(state: State) -> str:

    return "\n".join(
        [
            f"task_mode: {state.task_mode}",
            f"source_mode: {state.source_mode}",
            f"function_queue: {state.function_queue}",
            f"authorized_paths: {[item.model_dump() for item in state.authorized_paths]}",
            f"source_files: {[item.model_dump() for item in state.source_files]}",
            f"mcp_required: {state.mcp_required}",
            f"mcp_connect_status: {state.mcp_connect_status}",
            f"mcp_tool_names: {state.mcp_tool_names}",
        ]
    )


async def call_model_decompile(
    state: State, runtime: Runtime[Context]
) -> Dict[str, List[AIMessage]]:

    # 绑定工具列表到模型
    # tools = await load_runtime_tools(state, runtime.context)
    # model = load_chat_model(
    #     runtime.context.model,
    #     base_url=runtime.context.base_url,
    #     api_key=runtime.context.api_key,
    # ).bind_tools(tools)

    # system_message = runtime.context.system_prompt.format(
    #     system_time=datetime.now(tz=UTC).isoformat()
    # )
    # system_message = (
    #     f"{system_message}\n\n"
    #     f"{_format_initialization_context(state)}"
    # )

    # response = cast(
    #     AIMessage,
    #     await model.ainvoke(
    #         [{"role": "system", "content": system_message}, *state.messages]
    #     ),
    # )

    # if state.is_last_step and response.tool_calls:
    #     return {
    #         "messages": [
    #             AIMessage(
    #                 id=response.id,
    #                 content="抱歉，我没有在指定步数内完成任务。",
    #             )
    #         ]
    #     }

    # return {"messages": [response]}
