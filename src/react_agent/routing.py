
from __future__ import annotations
from typing import Literal
from langchain_core.messages import AIMessage
from react_agent.state import State


# 会话短期记忆维护，通过路由+人机环路来补充信息
def route_from_session_entry(
    state: State,
) -> Literal[
    "task_intake_node",     # 意图分析
    "target_intake_node",   # 模式检测
    "path_intake_node",     # 可操作路径检测
    "check_mcp_node",       # mcp开启检测
    "call_model",           # 开始运行
]:
    # 下一个恢复节点是否存在
    missing = set(state.missing_requirements)

    # 交集判断
    if missing & {"function_target", "function_names", "entry_points", "function_queue"}:
        return "target_intake_node"  # 函数目标分析

    if missing & {"asm_source", "authorized_path", "path_candidates", "workspace_path"}:
        return "path_intake_node" # 可操作路径分析

    if missing & {"mcp_connection", "mcp_tools"}:
        return "check_mcp_node" # mcp

    if state.initialization_complete:
        return "call_model"

    return "task_intake_node" # 全局分析


# 人机环路阻塞或跳转到mcp连接状态检测接待你
def route_after_paths(
    state: State,
) -> Literal["ask_missing_info_node", "check_mcp_node"]:
    if state.needs_user_input:
        return "ask_missing_info_node"
    return "check_mcp_node"


# 人机环路阻塞或跳转到目标函数验证节点
def route_after_mcp(
    state: State,
) -> Literal["ask_missing_info_node", "validate_targets_node"]:
    if state.needs_user_input:
        return "ask_missing_info_node"
    return "validate_targets_node"

# 人机环路阻塞或跳转到执行前准备节点
def route_after_targets(
    state: State,
) -> Literal["ask_missing_info_node", "execution_prepare_node"]:
    if state.needs_user_input:
        return "ask_missing_info_node"
    return "execution_prepare_node"


# 人机环路阻塞或跳转到执行任务节点
def route_after_prepare(
    state: State,
) -> Literal["ask_missing_info_node", "call_model"]:
    if state.needs_user_input:
        return "ask_missing_info_node"
    return "call_model"


# 路由工具调用节点还是直接结束推出
def route_model_output(state: State) -> Literal["__end__", "tools"]:

    last_message = state.messages[-1]
    if not isinstance(last_message, AIMessage):
        raise ValueError(
            f"没有收到LLM消息, goto {type(last_message).__name__}"
        )

    if not last_message.tool_calls:
        return "__end__"
    return "tools"
