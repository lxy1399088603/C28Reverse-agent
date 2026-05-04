"""LangGraph assembly for the C28x reverse agent.

这个文件只负责“把节点接起来”，不放业务逻辑。
业务逻辑在 services/，节点适配在 nodes/，路由在 routing.py。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph

from react_agent.context import Context
from react_agent.nodes.human_loop import ask_missing_info_node, execution_prepare_node
from react_agent.nodes.intake import (
    path_intake_node,
    session_entry_node,
    target_intake_node,
    task_intake_node,
)
from react_agent.nodes.mcp import check_mcp_node
from react_agent.nodes.model import call_model
from react_agent.nodes.paths import validate_paths_node
from react_agent.nodes.targets import validate_targets_node
from react_agent.routing import (
    route_after_mcp,
    route_after_paths,
    route_after_prepare,
    route_after_targets,
    route_from_session_entry,
    route_model_output,
)
from react_agent.state import InputState, State
from react_agent.tools import tool_node


builder = StateGraph(State, input_schema=InputState, context_schema=Context)

# 三层初始化：
# 1. task_intake_node：理解任务模式、来源模式、函数目标和路径候选。
# 2. validate/check 节点：校验路径、MCP、函数目标是否满足执行前提。
# 3. execution_prepare_node：整理 FIFO 队列并标记初始化完成。
builder.add_node("session_entry_node", session_entry_node)
builder.add_node("task_intake_node", task_intake_node)
builder.add_node("target_intake_node", target_intake_node)
builder.add_node("path_intake_node", path_intake_node)
builder.add_node("validate_paths_node", validate_paths_node)
builder.add_node("check_mcp_node", check_mcp_node)
builder.add_node("validate_targets_node", validate_targets_node)
builder.add_node("execution_prepare_node", execution_prepare_node)
builder.add_node("ask_missing_info_node", ask_missing_info_node)

# ReAct 主循环。
builder.add_node("call_model", call_model)
# 工具列表
# 当模型输出tool_call后会路由到tools节点，有ToolNode执行工具函数
builder.add_node("tools", tool_node)

builder.add_edge("__start__", "session_entry_node")
builder.add_conditional_edges("session_entry_node", route_from_session_entry)
builder.add_edge("task_intake_node", "validate_paths_node")
builder.add_edge("target_intake_node", "validate_targets_node")
builder.add_edge("path_intake_node", "validate_paths_node")

builder.add_conditional_edges("validate_paths_node", route_after_paths)
builder.add_conditional_edges("check_mcp_node", route_after_mcp)
builder.add_conditional_edges("validate_targets_node", route_after_targets)
builder.add_conditional_edges("execution_prepare_node", route_after_prepare)

builder.add_conditional_edges("call_model", route_model_output)
builder.add_edge("tools", "call_model")

graph = builder.compile(name="C28x Reverse Agent", checkpointer=InMemorySaver())
