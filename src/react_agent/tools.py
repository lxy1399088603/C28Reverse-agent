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


# Static tools are always available. Runtime tools are attached per session
# based on validated state such as MCP connectivity or authorized file scope.
TOOLS: list[BaseTool] = [search]


def tool_error(error: Exception) -> str:
    """Compress tool failures into UI-friendly text."""

    error_text = str(error).strip()
    lines = [line.strip() for line in error_text.splitlines() if line.strip()]

    for line in reversed(lines):
        if any(marker in line for marker in ("Error:", "Exception:", "AttributeError:", "ValueError:")):
            return f"Tool call failed: {line}"

    if lines:
        return f"Tool call failed: {lines[-1]}"

    return f"Tool call failed: {error.__class__.__name__}"


async def load_runtime_tools(
    state: State,
    context: Context,
) -> list[BaseTool]:
    """Load dynamic tools for the current session."""

    tools: list[BaseTool] = list(TOOLS)

    # File tools are scoped to the validated session paths instead of the full
    # workspace so the model only sees the file surface the user has authorized.
    if state.authorized_paths or state.source_files:
        tools.extend(build_file_tools(state))

    if state.source_mode == "mcp" and state.mcp_connect_status:
        tools.extend(await load_mcp_tools(context))

    return tools


async def tool_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    """Invoke the runtime tool node."""

    tools = await load_runtime_tools(state, runtime.context)
    node = ToolNode(tools, handle_tool_errors=tool_error)
    return await node.ainvoke(state)
