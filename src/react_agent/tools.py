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


# 网络搜索工具Tavily
@tool(
    "search",
    description="在网络上搜索最新的信息.",
)
async def search(query: str) -> Optional[dict[str, Any]]:

    runtime = get_runtime(Context)
    wrapped = TavilySearch(max_results=runtime.context.max_search_results)
    return cast(
        dict[str, Any],
        await asyncio.to_thread(wrapped.invoke, {"query": query}),
    )


# 本地静态工具列表。
# MCP 需要根据当前 Context 在运行时连接和加载。
TOOLS: list[BaseTool] = [search]


# MCP 返回错误格式化
def tool_error(error: Exception) -> str:
    """
    MCP 工具失败时可能会返回很长的插件栈，例如 IDA Python traceback。
    这些栈不应该直接冒泡到 Textual UI；这里把异常压缩成一段可读文本，
    再交给模型决定如何向用户说明或选择其他工具重试。
    """

    error_text = str(error).strip()
    lines = [line.strip() for line in error_text.splitlines() if line.strip()]

    # 优先保留真正的异常原因，通常在 traceback 末尾，例如：
    # AttributeError: module 'ida_nalt' has no attribute 'get_entry_qty'
    for line in reversed(lines):
        if any(marker in line for marker in ("Error:", "Exception:", "AttributeError:", "ValueError:")):
            return f"工具调用失败：{line}"

    if lines:
        return f"工具调用失败：{lines[-1]}"

    return f"工具调用失败：{error.__class__.__name__}"

# 加载运行时工具-MCP
async def load_runtime_tools(
    state: State,
    context: Context,
) -> list[BaseTool]:

    # 静态工具列表
    tools: list[BaseTool] = list(TOOLS)

    # 动态工具MCP列表
    # 鏂囦欢宸ュ叿鏄繍琛屾椂鍔ㄦ€佹瀯寤虹殑锛岃繖鏍峰彲浠ョ洿鎺ョ粦瀹氬綋鍓嶄細璇濈殑鎺堟潈璺緞銆?
    if state.authorized_paths or state.source_files:
        tools.extend(build_file_tools(state))
    if state.source_mode == "mcp" and state.mcp_connect_status:
        tools.extend(await load_mcp_tools(context))

    return tools


# 返回工具列表
async def tool_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:

    tools = await load_runtime_tools(state, runtime.context)
    node = ToolNode(tools, handle_tool_errors=tool_error)
    return await node.ainvoke(state)
