"""Function target validation graph node."""

from __future__ import annotations

from typing import Any

from react_agent.state import State
from react_agent.domain.intake import TaskMode
from react_agent.utils import judge_CompleteState

def _preview_names(names: list[str], limit: int = 8) -> str:
    """Format a long name list as a compact preview for the UI."""

    if not names:
        return ""

    preview = ", ".join(names[:limit])
    remaining = len(names) - limit
    if remaining > 0:
        return f"{preview} ... 另有 {remaining} 个"
    return preview

# 获取mcp状态，如果mcp开启
def get_mcp_status(state: State) -> str:

    if not state.mcp_required:
        return ""

    if not state.mcp_connect_status:
        return "初始化状态：\n- MCP：未连接或工具未加载成功\n\n"

    if state.mcp_tool_names:
        tool_preview = _preview_names(state.mcp_tool_names)
        return (
            "初始化状态：\n"
            f"- MCP：已连接，加载 {len(state.mcp_tool_names)} 个工具\n"
            f"- 工具示例：{tool_preview}\n\n"
        )

    return "初始化状态：\n- MCP：已连接\n\n"

# 验证目标函数
def validate_targets_node(state: State) -> dict[str, Any]:

    complete_result = judge_CompleteState(state)
    # 如果缺少函数信息，没提供任何函数
    if not complete_result["complete"]:
        mcp_status = get_mcp_status(state)
        return {
            "function_queue": [],
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": (
                f"{mcp_status}"
                "阻塞项：缺少需要还原的函数名或入口点。\n"
                "请继续输入函数名或入口点，例如：反编译入口点main，还原函数sub_1234、0x401000。"
            ),
            "missing_requirements": complete_result["missing_requirements"],
            "last_blocking_node": "validate_targets_node", # 继续去验证输入函数
        }

    function_queue = [item.strip() for item in state.function_names if item.strip()]
    return {
        "function_queue": function_queue, # 函数队列
        "targets_locked": True,
        "session_phase": "ready" if complete_result["complete"] else "initializing",
        "needs_user_input": not complete_result["complete"],
        "blocking_reason": None if complete_result["complete"] else complete_result["blocking_reason"],
        "missing_requirements": [] if complete_result["complete"] else complete_result["missing_requirements"],
    }
