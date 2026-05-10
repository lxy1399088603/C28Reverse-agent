"""Task intake graph node."""

from __future__ import annotations

from typing import Any, TypeVar

from langgraph.runtime import Runtime
from langchain.chat_models import BaseChatModel

from react_agent.context import Context
from react_agent.utils import latest_human_text
from react_agent.state import State
from react_agent.utils import load_chat_model
from react_agent.domain.intake import TaskIntake
from react_agent.prompts.intake_prompt import *


# 通过LLM从用户输出中分析任务类型、资源类型、用户目标、函数列表等所需必要信息
async def extract_task_intake(
    model: BaseChatModel,
    user_input: str,
    prompt: str,
) -> TaskIntake:

    extractor = prompt | model.with_structured_output(TaskIntake)
    return await extractor.ainvoke({"user_input": user_input})


# 更新字符状态，确保全局状态不被覆盖
def _merge_mode(current: str, incoming: str) -> str:
    if incoming != "unknown":
        return incoming
    return current

T = TypeVar("T")
# 更新列表状态，确保全局状态不被覆盖，T是泛型
def _merge_list(current: list[T], incoming: list[T]) -> list[T]:
    if incoming:
        return incoming
    return current

# 合并所有状态，暂时不考虑删除之前的状态
def merge_intake_with_state(state: State, intake: TaskIntake) -> dict[str, Any]:
    task_mode = _merge_mode(state.task_mode, intake.task_mode)
    source_mode = _merge_mode(state.source_mode, intake.source_mode)
    function_names = _merge_list(state.function_names, intake.function_names)
    path_candidates = _merge_list(state.path_candidates, intake.path_candidates)

    result = {
        "task_mode": task_mode,
        "source_mode": source_mode,
        "function_names": function_names,
        "path_candidates": path_candidates,
        "mcp_required": source_mode == "mcp",
        "session_phase": "initializing",
        "source_locked": source_mode != "unknown" or state.source_locked,
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }
    print(f"合并状态结果：{result}")
    return result


# Agent的预处理，判断是否可以进入ready状态进行还原环路操作
def session_entry_node(state: State) -> dict[str, Any]:
    if state.session_phase == "new":
        return {"session_phase": "initializing"}

    if state.missing_requirements:
        return {"session_phase": "blocked"}

    if state.initialization_complete:
        return {"session_phase": "ready"}

    return {"session_phase": "initializing"}


# 做函数目标分析function_names/function_names
async def target_intake_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:

    # 没有用户输入直接阻塞
    user_input = latest_human_text(state)
    if not user_input.strip():
        return {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": "缺少需要还原的函数名或入口点。",
            "missing_requirements": ["function_target"],
            "last_blocking_node": "target_intake_node",
        }

    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
        api_key=runtime.context.api_key,
    )

    try:
        intake = await extract_task_intake(model, user_input, TARGET_INTAKE_PROMPT)
    except Exception as exc:
        result = {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": f"没有识别到提供的函数列表及相关提示: {exc!r}",
            "missing_requirements": ["function_target"],
            "last_blocking_node": "target_intake_node",
        }
        return result

    result = {
        "task_mode": _merge_mode(state.task_mode, intake.task_mode),
        "function_names": _merge_list(state.function_names, intake.function_names),
        "source_mode": _merge_mode(state.source_mode, intake.source_mode),
        "session_phase": "initializing",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }

    print(f"处理目标分析后的全局状态：{result}")
    return result


# 可操作路径是否存在检测
async def path_intake_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    user_input = latest_human_text(state)
    if not user_input.strip():
        return {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": "缺少可操作目录或汇编文件路径。",
            "missing_requirements": ["asm_source"],
            "last_blocking_node": "path_intake_node", # 循环当前节点，因为当前节点是维护必要信息的
        }

    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
        api_key=runtime.context.api_key,
    )

    try:
        intake = await extract_task_intake(model, user_input, PATH_INTAKE_PROMPT)
    except Exception as exc:
        result = {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": f"路径识别失败: {exc!r}",
            "missing_requirements": ["asm_source"],
            "last_blocking_node": "path_intake_node",
        }
        return result

    result = {
        "path_candidates": _merge_list(state.path_candidates, intake.path_candidates),
        "session_phase": "initializing",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }

    print(f"处理路径获取后的全局状态：{result}")
    return result

# 明确用户需求，通过LLM分析用户需求，更新Agent运行时状态
async def task_intake_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:

    user_input = latest_human_text(state)
    if not user_input.strip():
        return {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": "缺少用户任务描述。",
            "missing_requirements": ["user_task"],
            "last_blocking_node": "task_intake_node",
        }

    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
        api_key=runtime.context.api_key,
    )

    try:
        intake = await extract_task_intake(model, user_input, TASK_INTAKE_PROMPT)
    except Exception as exc:
        return {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": f"任务识别失败: {exc!r}",
            "missing_requirements": ["task_intake"],
            "last_blocking_node": "task_intake_node",
        }

    return merge_intake_with_state(state, intake)
