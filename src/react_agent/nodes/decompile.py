from __future__ import annotations

from typing import Any, cast
import json
import re

from langchain_core.messages import AIMessage, AnyMessage, RemoveMessage
from langgraph.runtime import Runtime

from react_agent.context import Context
from react_agent.prompts.decompile_prompt import build_decompile_system_prompt
from react_agent.prompts.equivalence_prompt import build_verify_system_prompt
from react_agent.state import State
from react_agent.tools import load_runtime_tools
from react_agent.utils import latest_ai_text, load_chat_model


# 获取第一条用户信息
def first_human_message(messages: list[AnyMessage]) -> AnyMessage | None:
    for message in messages:
        if getattr(message, "type", None) == "human":
            return message
    return None


# 根据当前任务获取有效的AIMessage
def messages_after_round_boundary(
    messages: list[AnyMessage],
    boundary_message_id: str | None,
) -> list[AnyMessage]:

    if not boundary_message_id:
        return messages

    # 返回boundary_message_id之后的消息
    for index, message in enumerate(messages):
        if getattr(message, "id", None) == boundary_message_id:
            return messages[index + 1 :]

    return messages


# 从消息历史中裁剪出当前函数处理的这一轮消息给模型
def slice_current_round_messages(state: State) -> list[AnyMessage]:

    messages = list(state.messages)
    first_human = first_human_message(messages)
    tail = messages_after_round_boundary(
        messages,
        state.current_round_start_message_id,
    )

    # 合并消息列表
    sliced: list[AnyMessage] = []
    if first_human is not None and first_human not in tail:
        sliced.append(first_human)
    sliced.extend(tail)
    return sliced


# 当前函数处理完成后，把这一轮产生的AI消息和工具消息从state.messages中删除，避免历史消息无限膨胀
def remove_current_round_messages(state: State) -> list[RemoveMessage]:

    if not state.current_round_start_message_id:
        return []

    # 检测标记的消息是否存在，如果找不到就不删除
    messages = list(state.messages)
    if not any(
        getattr(message, "id", None) == state.current_round_start_message_id
        for message in messages
    ):
        return []

    # 准备一个删除指令列表
    removals: list[RemoveMessage] = []
    for message in messages_after_round_boundary(
        messages,
        state.current_round_start_message_id,
    ):
        # 只删除ai消息和tool消息
        if getattr(message, "type", None) not in {"ai", "tool"}:
            continue
        message_id = getattr(message, "id", None)
        # LangGraph 会根据这些删除指令，从消息历史里删掉对应消息
        if message_id:
            removals.append(RemoveMessage(id=message_id))
    return removals

# {
#   "content": [
#     {
#       "type": "text",
#       "text": "{ "target": "_main", "addr": "0x89a58", "names": [...], "count": 24 }"
#     }
#   ],
#   "structuredContent": {
#     "target": "_main",
#     "addr": "0x89a58",
#     "names": [...],
#     "count": 24
#   },
#   "isError": false
# }
# 
# 从get_callee_name 工具中返回结果，提取被调用函数列表，解析json
def extract_callee_names(raw_result: Any) -> list[str]:
    if isinstance(raw_result, dict):
        structured = raw_result.get("structuredContent")
        if isinstance(structured, dict):
            names = structured.get("names")
            if isinstance(names, list):
                return [str(item).strip() for item in names if str(item).strip()]

        names = raw_result.get("names")
        if isinstance(names, list):
            return [str(item).strip() for item in names if str(item).strip()]

        content = raw_result.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                names = parsed.get("names") if isinstance(parsed, dict) else None
                if isinstance(names, list):
                    return [str(name).strip() for name in names if str(name).strip()]

        return []

    if isinstance(raw_result, list):
        return [str(item).strip() for item in raw_result if str(item).strip()]

    return []


def _filter_discovered_callees(
    raw_names: list[str],
    current_function: str,
    completed_functions: list[str],
) -> list[str]:
    completed = set(completed_functions)
    seen: set[str] = set()
    filtered: list[str] = []

    for raw_name in raw_names:
        name = raw_name.strip()
        if not name:
            continue
        if name == current_function:
            continue
        if name in completed:
            continue
        if name in seen:
            continue
        seen.add(name)
        filtered.append(name)

    return filtered


# 通过工具名查找工具
def find_tool_by_name(tools: list[Any], tool_name: str) -> Any | None:

    for tool in tools:
        if getattr(tool, "name", None) == tool_name:
            return tool

    suffix = f".{tool_name}"
    for tool in tools:
        candidate = str(getattr(tool, "name", "") or "")
        if candidate.endswith(suffix) or candidate.endswith(tool_name):
            return tool

    return None


# 把当前函数新发现的被调函数，合并到原来的函数队列前面
def merge_discovered_callees(
    current_function: str,
    completed_functions: list[str],
    failed_functions: list[str],
    his_function_queue: list[str],
    discovered_names: list[str],
) -> list[str]:

    completed = set(completed_functions)
    failed = set(failed_functions)

    discovered_seen: set[str] = set()
    discovered_callees: list[str] = []

    for raw_name in discovered_names:
        callee = raw_name.strip()
        if not callee:
            continue
        if callee == current_function:
            continue
        if callee in completed_functions:
            continue
        if callee in discovered_seen:
            continue
        discovered_seen.add(callee)
        discovered_callees.append(callee)

    remaining_queue: list[str] = []
    for name in his_function_queue:
        function_name = name.strip()
        if not function_name:
            continue
        if function_name == current_function:
            continue
        if function_name in completed:
            continue
        if function_name in failed:
            continue
        if function_name in discovered_seen:
            continue
        remaining_queue.append(function_name)

    return discovered_callees + remaining_queue


# 编译循环，主要设置当前还原函数，更新函数队列
def decompile_loop_node(state: State) -> dict[str, Any]:
    # 临时做限制，最多还原20个函数
    if len(state.completed_functions) >= state.max_chain_depth:
        return {
            "stop": True,
            "current_function": None,
            "current_round_start_message_id": None,
            "session_phase": "ready",
            "verification_retry_count": 0
        }

    # 队列为空
    if not state.function_queue:
        return {
            "stop": True,
            "current_function": None,
            "current_round_start_message_id": None,
            "session_phase": "ready",
            "verification_retry_count": 0
        }

    # 头取并删除
    current_function = state.function_queue[0]
    remaining_queue = state.function_queue[1:]
    
    # 获取最后一条消息id
    last_message = state.messages[-1] if state.messages else None
    return {
        "stop": False,
        "current_function": current_function,
        "current_round_start_message_id": getattr(last_message, "id", None),
        "function_queue": remaining_queue,
        "session_phase": "running",
        "verification_retry_count": 0
    }


def extract_decompile_code(text: str) -> str:
    """从 decompile LLM 输出中提取还原代码段。
    
    基于 prompt 要求的固定标题锚点切割，不依赖 markdown fence。
    提取不到则返回原文。
    """
    # 找"还原代码:"之后的内容
    markers = ["还原代码:", "还原代码："]
    start = -1
    for marker in markers:
        idx = text.find(marker)
        if idx != -1:
            start = idx + len(marker)
            break
    
    if start == -1:
        return text
    
    # 找下一个段落标题作为结束点
    end_markers = ["未确认点:", "未确认点：", "完成状态:", "完成状态："]
    end = len(text)
    for marker in end_markers:
        idx = text.find(marker, start)
        if idx != -1 and idx < end:
            end = idx
    
    code = text[start:end].strip()
    
    if code.startswith("```"):
        first_newline = code.find("\n")
        if first_newline != -1:
            code = code[first_newline + 1:]
    if code.endswith("```"):
        code = code[:-3]
    
    return code.strip() if code.strip() else text


# LLM调用进行函数还原
async def call_model_decompile(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    if not state.current_function:
        raise ValueError("当前还原函数为空，无法继续还原。")

    # 创建模型连接并绑定工具列表
    tools = await load_runtime_tools(state, runtime.context)
    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
        api_key=runtime.context.api_key,
    ).bind_tools(tools)

    # 构建出系统提示词
    decompile_prompt = build_decompile_system_prompt(
        state=state,
        base_system_prompt=runtime.context.system_prompt,
    )

    response = cast(
        AIMessage,
        await model.ainvoke(
            [
                {"role": "system", "content": decompile_prompt},
                *slice_current_round_messages(state),
            ]
        ),
    )

    # 在最后一步是阻止工具调用
    if state.is_last_step and response.tool_calls:
        return {
                "session_phase": "running",
                "needs_user_input": False,
                "blocking_reason": "当前函数 MCP/LLM 工具调用轮次耗尽。",
                "missing_requirements": [],
                "verification_status": "failed",
                "verification_reason": "当前函数 MCP/LLM 工具调用轮次耗尽。",
            }

    result =  {
        "messages": [response],
        "session_phase": "running",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }


    if not response.tool_calls:
        response_text = response.content if isinstance(response.content, str) else str(response.content)
        result["current_decompile_text"] = extract_decompile_code(response_text)

    return result
    


# 还原代码失败节点
def decompile_fail_node(state: State) -> dict[str, Any]:
    current_function = (state.current_function or "").strip()
    failed_functions = list(state.failed_functions)
    if current_function and current_function not in failed_functions:
        failed_functions.append(current_function)
    
    # 失败原因
    failure_reason = state.blocking_reason or "当前函数在限定步数内未完成还原。"
    
    # 把当前函数为什么失败记录进列表里
    evidence_gaps = list(state.verification_evidence_gaps)
    if current_function:
        evidence_gaps.append(
            f"{current_function}: {failure_reason}"
        )
    else:
        evidence_gaps.append(failure_reason)

    return {
        "failed_functions": failed_functions,
        "current_function": None,
        "current_round_start_message_id": None,
        "current_decompile_text": None,
        "verification_retry_count": 0,
        "verification_reason": failure_reason,
        "verification_status": "failed",
        "verification_evidence_gaps": evidence_gaps,
        "verification_retry_hint": None,
        "session_phase": "running" if state.function_queue else "ready",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }



# 从verify LLM 输出中提取状态和缺失证据，返回 (status, reason)。
#  status: "verified" | "need_more_evidence" | "failed"
def parse_verify_status(text: str) -> tuple[str, str | None]:
    status = None
    reason = None
    
    for line in text.splitlines():
        stripped = line.strip()
        
        # 匹配 "Status: xxx"
        if stripped.lower().startswith("status:"):
            raw = stripped.split(":", 1)[1].strip().lower()
            # 归一化状态值
            if raw in ("verified", "persisted", "partial", "partial_persisted"):
                status = "verified"
            elif raw in ("need_more_evidence",):
                status = "need_more_evidence"
            elif raw in ("failed",):
                status = "failed"
        
        # 匹配 "Verification Summary: xxx"
        if stripped.lower().startswith("verification summary:"):
            reason = stripped.split(":", 1)[1].strip()
    
    return status or "verified", reason

# 函数验证节点
async def function_verify_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    # 
    if not state.current_function:
        raise ValueError("当前还原函数为空，无法继续验证。")

    # 获取待验证函数的还原结果
    decompile_text = state.current_decompile_text or latest_ai_text(state)
    if not decompile_text.strip():
        raise ValueError("获取不到还原结果。")

    # 创建模型绑定工具
    tools = await load_runtime_tools(state, runtime.context)
    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
        api_key=runtime.context.api_key,
    ).bind_tools(tools)

    # 合成验证提示提
    verify_prompt = build_verify_system_prompt(
        state=state,
        decompile_text=decompile_text,
        base_system_prompt=runtime.context.system_prompt,
    )

    response = cast(
        AIMessage,
        await model.ainvoke(
            [
                {"role": "system", "content": verify_prompt},
                *slice_current_round_messages(state),
            ]
        ),
    )

    result = {
        "messages": [response],
        "session_phase": "running",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
        "current_decompile_text": decompile_text,
    }

    if not response.tool_calls:
        response_text = response.content if isinstance(response.content, str) else str(response.content)
        status, reason = parse_verify_status(response_text)
        
        retry_count = state.verification_retry_count
        
        # need_more_evidence 时递增 retry
        if status == "need_more_evidence":
            retry_count += 1
            # 超过上限则降级为 failed
            if retry_count > state.verification_max_retries:
                status = "failed"
                reason = f"重试 {state.verification_max_retries} 次后仍证据不足"
        
        result["verification_status"] = status
        result["verification_reason"] = reason
        result["verification_retry_count"] = retry_count

    return result



# 扫描函数内部调用
async def scan_callees_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    current_function = (state.current_function or "").strip()
    if not current_function:
        raise ValueError("当前还原函数为空，无法扫描内部调用。")

    completed_functions = list(state.completed_functions)
    if current_function not in completed_functions:
        completed_functions.append(current_function)

    tools = await load_runtime_tools(state, runtime.context)
    discovered_from_mcp: list[str] = []
    evidence_gaps = list(state.verification_evidence_gaps)
    callee_tool = find_tool_by_name(tools, "get_callee_name")

    if callee_tool is None:
        evidence_gaps.append(
            "扫描函数调用失败: "
            "mcp 中没有找到get_callee_name工具"
        )
    else:
        try:
            raw_result = await callee_tool.ainvoke({"target": current_function})
            discovered_from_mcp = extract_callee_names(raw_result)
        except Exception as exc:
            evidence_gaps.append(
                "扫描函数调用失败: "
                f"{exc}"
            )

    updated_queue = merge_discovered_callees(
        current_function,
        completed_functions,
        state.failed_functions,
        state.function_queue,
        discovered_from_mcp,
    )

    result: dict[str, Any] = {
        "completed_functions": completed_functions,
        "function_queue": updated_queue,
        "current_function": None,
        "current_round_start_message_id": None,
        "current_decompile_text": None,
        "verification_retry_count": 0,
        "verification_reason": None,
        "verification_status": None,
        "verification_evidence_gaps": evidence_gaps,
        "verification_retry_hint": None,
        "session_phase": "running" if updated_queue else "ready",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }
    removals = remove_current_round_messages(state)
    if removals:
        result["messages"] = removals
    return result


# 结束节点
def decompile_finish_node(state: State) -> dict[str, Any]:
    return {
        "stop": True,
        "current_function": None,
        "current_round_start_message_id": None,
        "function_queue": [],
        "completed_functions": list(state.completed_functions),
        "failed_functions": list(state.failed_functions),
        "current_decompile_text": None,
        "verification_retry_count": 0,
        "verification_reason": None,
        "verification_status": None,
        "verification_evidence_gaps": [],
        "verification_retry_hint": None,
        "session_phase": "ready",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }
