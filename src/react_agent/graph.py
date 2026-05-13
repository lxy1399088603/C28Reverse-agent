"""LangGraph assembly for the C28x reverse agent.

这个文件只负责“把节点接起来”，不放业务逻辑。
业务逻辑在 services/，节点适配在 nodes/，路由在 routing.py。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph

from react_agent.context import Context
from react_agent.nodes.human_loop import ask_missing_info_node, execution_prepare_node
from react_agent.nodes.intake import *
from react_agent.nodes.decompile import *
from react_agent.nodes.mcp import check_mcp_node
from react_agent.nodes.decompile import call_model_decompile
from react_agent.nodes.paths import validate_paths_node
from react_agent.nodes.targets import validate_targets_node
from react_agent.routing import *
from react_agent.state import InputState, State
from react_agent.tools import tool_node

# 创建状态管理器，定义全局管理状态对象、输入对象以及上下文（Prompt）
builder = StateGraph(State, input_schema=InputState, context_schema=Context)

# 用户意图理解
builder.add_node("session_entry_node", session_entry_node)
builder.add_node("ask_missing_info_node", ask_missing_info_node)
builder.add_node("task_intake_node", task_intake_node)
builder.add_node("validate_paths_node", validate_paths_node)
builder.add_node("path_intake_node", path_intake_node)
builder.add_node("check_mcp_node", check_mcp_node)
builder.add_node("target_intake_node", target_intake_node)
builder.add_node("validate_targets_node", validate_targets_node)
builder.add_node("execution_prepare_node", execution_prepare_node)

# 还原主循环
# 判断队列中是否有任务路由
builder.add_node("decompile_loop_node", decompile_loop_node)
# 单函数还原节点
builder.add_node("call_model_decompile", call_model_decompile)
builder.add_node("decompile_fail_node", decompile_fail_node)
# 函数验收落盘节点
builder.add_node("function_verify_node", function_verify_node)
# 扫描子函数节点，并更新队列
builder.add_node("scan_callees_node", scan_callees_node)
# 还原收尾
builder.add_node("decompile_finish_node", decompile_finish_node)

# 工具列表
# 当模型输出tool_call后会路由到tools节点，有ToolNode执行工具函数
builder.add_node("decompile_tools", tool_node)
builder.add_node("verify_tools", tool_node)

# 意图识别
builder.add_edge("__start__", "session_entry_node")
builder.add_conditional_edges("session_entry_node", route_from_session_entry)
builder.add_edge("task_intake_node", "validate_paths_node")
builder.add_edge("target_intake_node", "validate_targets_node")
builder.add_edge("path_intake_node", "validate_paths_node")
builder.add_conditional_edges("validate_paths_node", route_after_paths)
builder.add_conditional_edges("check_mcp_node", route_after_mcp)
builder.add_conditional_edges("validate_targets_node", route_after_targets)
builder.add_conditional_edges("execution_prepare_node", route_after_prepare)

# 还原流程
builder.add_conditional_edges("decompile_loop_node", route_decompile_loop)
builder.add_conditional_edges("call_model_decompile", route_model_output)
builder.add_edge("decompile_fail_node", "decompile_loop_node")
builder.add_edge("decompile_tools","call_model_decompile")
builder.add_conditional_edges("function_verify_node",route_after_function_verify)
builder.add_edge("verify_tools","function_verify_node")
builder.add_edge("scan_callees_node", "decompile_loop_node")
builder.add_edge("decompile_finish_node","__end__")

graph = builder.compile(name="C28x Reverse Agent", checkpointer=InMemorySaver())
