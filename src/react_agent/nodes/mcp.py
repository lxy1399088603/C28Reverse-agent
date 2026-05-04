"""MCP validation graph node."""

from __future__ import annotations

from typing import Any

from react_agent.state import State


async def check_mcp_available() -> bool:
    """Return whether the required IDA/MCP service is available."""

    return False

async def check_mcp_node(state: State) -> dict[str, Any]:
    """Check MCP only when the task explicitly requires MCP."""

    if state.source_mode != "mcp":
        return {
            "mcp_required": False,
            "needs_user_input": False,
            "blocking_reason": None,
            "missing_requirements": [],
        }

    mcp_ok = await check_mcp_available()
    if not mcp_ok:
        return {
            "mcp_required": True,
            "mcp_connect_status": False,
            "needs_user_input": True,
            "blocking_reason": "任务要求使用 MCP，但当前 MCP 未连接或不可用。",
            "missing_requirements": ["mcp_connection"],
        }

    return {
        "mcp_required": True,
        "mcp_connect_status": True,
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }
