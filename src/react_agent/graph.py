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
from react_agent.nodes.mcp import check_mcp_node
from react_agent.nodes.model import call_model
from react_agent.nodes.paths import validate_paths_node
from react_agent.nodes.targets import validate_targets_node
from react_agent.routing import *
from react_agent.state import InputState, State
from react_agent.tools import tool_node

# 创建状态管理器，定义全局管理状态对象、输入对象以及上下文（Prompt）
builder = StateGraph(State, input_schema=InputState, context_schema=Context)

# 整体思路：
#   0、session会话处理
#   1、先做用户意图识别，意图识别主要是通过用户的描述来判断是否规定了可操作路径，是否开启使用mcp，是否提供函数入口或列表
#   2、通过人机环路来补充必须存在的信息，并对信息进行校验
#   3、维护队列开始进行任务、使用skill还原函数、验证函数
#   4、全局验证

# session会话处理，每次通过Agent短期记忆来恢复预处理状态
builder.add_node("session_entry_node", session_entry_node)

# 做用户意图分析
builder.add_node("task_intake_node", task_intake_node)

# 目标函数检测
builder.add_node("target_intake_node", target_intake_node)

# 可操作路径分析
builder.add_node("path_intake_node", path_intake_node)

# 验证候选路径
builder.add_node("validate_paths_node", validate_paths_node)

# 检测mcp连接状态
builder.add_node("check_mcp_node", check_mcp_node)

# 验证是否提供了有效函数
builder.add_node("validate_targets_node", validate_targets_node)

builder.add_node("execution_prepare_node", execution_prepare_node)
builder.add_node("ask_missing_info_node", ask_missing_info_node)

# ReAct 主循环。
builder.add_node("call_model", call_model)
# 工具列表
# 当模型输出tool_call后会路由到tools节点，有ToolNode执行工具函数
builder.add_node("tools", tool_node)

# 流程开始
builder.add_edge("__start__", "session_entry_node")

# 会话需要信息判断并路由去环路补充
builder.add_conditional_edges("session_entry_node", route_from_session_entry)

# 全局信息获取后进行路径检测
builder.add_edge("task_intake_node", "validate_paths_node")

# 向用户获取目标函数后进行验证，如果没有则继续人机阻塞等待用户提供函数
builder.add_edge("target_intake_node", "validate_targets_node")

# 路径输入分析后进行验证
builder.add_edge("path_intake_node", "validate_paths_node")

# 验证路径后通过模式状态判断下一个处理节点是mcp还是asm
builder.add_conditional_edges("validate_paths_node", route_after_paths)
builder.add_conditional_edges("check_mcp_node", route_after_mcp)
builder.add_conditional_edges("validate_targets_node", route_after_targets)
builder.add_conditional_edges("execution_prepare_node", route_after_prepare)

builder.add_conditional_edges("call_model", route_model_output)
builder.add_edge("tools", "call_model")

graph = builder.compile(name="C28x Reverse Agent", checkpointer=InMemorySaver())
