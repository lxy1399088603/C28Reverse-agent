from __future__ import annotations

import shlex
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.runtime import Runtime

from react_agent.context import Context
from react_agent.state import State
from react_agent.utils import truthy
from react_agent.utils import judge_CompleteState


# 构建多MCP客户端连接配置
# MultiServerMCPClient能对多个MCP Server连接进行管理，将他们呢提供的所有Tools、Resource和Prompt汇聚到一个统一的接口中
def build_mcp_connections(context: Context) -> dict[str, dict[str, Any]]:

    # 是否开启mcp
    if not truthy(context.mcp_enabled):
        return {}

    # mcp服务名及连接方式获取
    server_name = context.mcp_server_name or "ida-pro-mcp"
    transport = str(context.mcp_transport).strip().lower()

    if transport == "stdio":
        if not context.mcp_command:
            return {}

        # Windows 路径可能包含反斜杠和空格，用 shlex.split(posix=False) 更稳。
        args = shlex.split(context.mcp_args or "", posix=False)
        return {
            server_name: {
                "transport": "stdio",
                "command": context.mcp_command,
                "args": args,
            }
        }

    if transport in {"http", "streamable_http"}:
        if not context.mcp_url:
            return {}

        return {
            server_name: {
                "transport": transport,
                "url": context.mcp_url,
            }
        }

    return {}


# 创建出mcp客户端
def create_mcp_client(context: Context) -> MultiServerMCPClient | None:

    connections = build_mcp_connections(context)
    if not connections:
        return None

    return MultiServerMCPClient(connections)


# 加载mcp工具列表
async def load_mcp_tools(context: Context) -> list[BaseTool]:
    """Load tools exposed by configured MCP servers.

    这个函数会被两个地方调用：
    1. check_mcp_node：检测 MCP 是否可用，并记录工具名。
    2. load_runtime_tools：把 MCP 工具合并进模型和 ToolNode 使用的工具列表。
    """

    client = create_mcp_client(context)
    if client is None:
        return []

    # 获取工具列表
    return await client.get_tools()


# 检测mcp连接状态
async def check_mcp_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:

    if state.source_mode != "mcp" and not state.mcp_required:
        return {
            "needs_user_input": False,
            "blocking_reason": None,
            "missing_requirements": [],
        }

    try:
        tools = await load_mcp_tools(runtime.context)
    except Exception as exc:
        return {
            "session_phase": "blocked",
            "mcp_required": True,
            "mcp_connect_status": False,
            "mcp_tool_names": [],
            "needs_user_input": True,
            "blocking_reason": f"MCP 连接或工具加载失败: {exc!r}",
            "missing_requirements": ["mcp_connection"],
            "last_blocking_node": "check_mcp_node",
        }

    if not tools:
        return {
            "session_phase": "blocked",
            "mcp_required": True,
            "mcp_connect_status": False,
            "mcp_tool_names": [],
            "needs_user_input": True,
            "blocking_reason": "任务要求使用 MCP，但当前没有加载到任何 MCP 工具。",
            "missing_requirements": ["mcp_tools"],
            "last_blocking_node": "check_mcp_node",
        }

    return {
        "mcp_required": True,
        "mcp_connect_status": True,
        "mcp_tool_names": [tool.name for tool in tools],
        "mcp_locked": True,
        "source_locked": True,
        "session_phase": "initializing",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }

