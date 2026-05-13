from __future__ import annotations

import asyncio
from typing import Any, Optional, cast

from langchain_core.tools import BaseTool, tool
from langchain_tavily import TavilySearch
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime, get_runtime

from react_agent.context import Context
from react_agent.file_tools import build_file_tools
from react_agent.nodes.mcp import load_mcp_tools
from react_agent.state import State


# 搜索工具
@tool(
    "search",
    description="Search the web for recent information.",
)
async def search(query: str) -> Optional[dict[str, Any]]:
    runtime = get_runtime(Context)
    wrapped = TavilySearch(max_results=runtime.context.max_search_results)
    return cast(
        dict[str, Any],
        await asyncio.to_thread(wrapped.invoke, {"query": query}),
    )


# 定义静态工具列表，文件操作、mcp工具都是运行时动态加载进去的
TOOLS: list[BaseTool] = [search]


# 工具调用失败的错误处理
def tool_error(error: Exception) -> str:
    error_text = str(error).strip() 
    lines = [line.strip() for line in error_text.splitlines() if line.strip()]

    for line in reversed(lines):
        if any(marker in line for marker in ("Error:", "Exception:", "AttributeError:", "ValueError:")):
            return f"工具调用失败: {line}"
    if lines:
        return f"工具调用失败: {lines[-1]}"

    return f"工具调用失败: {error.__class__.__name__}"


# 动态加载工具
async def load_runtime_tools(
    state: State,
    context: Context,
) -> list[BaseTool]:

    tools: list[BaseTool] = list(TOOLS)

    # 当前任务是否允许Agent操作文件，可以使用文件操作工具
    if state.authorized_paths or state.source_files:
        tools.extend(build_file_tools(state))

    # 模式为mcp，并且mcp连接检测通过，可以使用mcp工具
    if state.source_mode == "mcp" and state.mcp_connect_status:
        tools.extend(await load_mcp_tools(context))

    return tools


# 执行调用工具
async def tool_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    
    # 工具列表
    tools = await load_runtime_tools(state, runtime.context)
    # 检测上一条AIMessage中有没有tool_calls，如果有就调用对应工具，然后将执行结果写回messages
    # AIMessage(
    #   content="我需要读取这个文件",
    #   tool_calls=[
    #      {"name": "read_file", "args": {"path": "xxx.c"}, "id": "call_123"}
    #   ]
    # )
    node = ToolNode(tools, handle_tool_errors=tool_error)
    # ToolNode会找到名为read_file的工具然后执行，执行完成后产生一条ToolMessage
    return await node.ainvoke(state)
