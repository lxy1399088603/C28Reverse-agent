from __future__ import annotations

import asyncio
from typing import Any, Optional, cast

from langchain_core.tools import BaseTool, tool
from langchain_tavily import TavilySearch
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime, get_runtime

from react_agent.context import Context
from react_agent.nodes.mcp import load_mcp_tools
from react_agent.state import State


@tool(
    "search",
    description="在网络上搜索最新的信息.",
)
async def search(query: str) -> Optional[dict[str, Any]]:
    """Search the web using Tavily."""

    runtime = get_runtime(Context)
    wrapped = TavilySearch(max_results=runtime.context.max_search_results)
    return cast(
        dict[str, Any],
        await asyncio.to_thread(wrapped.invoke, {"query": query}),
    )


# 本地静态工具列表。
# MCP 需要根据当前 Context 在运行时连接和加载。
TOOLS: list[BaseTool] = [search]

# 加载运行时工具-MCP
async def load_runtime_tools(
    state: State,
    context: Context,
) -> list[BaseTool]:

    # 静态工具列表
    tools: list[BaseTool] = list(TOOLS)

    # 动态工具MCP列表
    if state.source_mode == "mcp" and state.mcp_connect_status:
        tools.extend(await load_mcp_tools(context))

    return tools


# 返回工具列表
async def tool_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:

    tools = await load_runtime_tools(state, runtime.context)
    node = ToolNode(tools)
    return await node.ainvoke(state)
