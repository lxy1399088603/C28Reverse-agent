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
from react_agent.prompts.intake_prompt import TASK_INTAKE_PROMPT


T = TypeVar("T")


async def extract_task_intake(
    model: BaseChatModel,
    user_input: str,
) -> TaskIntake:
    """Extract task mode, source mode, paths, and targets from user input."""

    extractor = TASK_INTAKE_PROMPT | model.with_structured_output(TaskIntake)
    return await extractor.ainvoke({"user_input": user_input})


def _merge_mode(current: str, incoming: str) -> str:
    """Keep the existing mode when the latest user input is ambiguous."""

    if incoming != "unknown":
        return incoming
    return current


def _merge_list(current: list[T], incoming: list[T]) -> list[T]:
    """Use newly extracted list values only when the user actually provided them."""

    if incoming:
        return incoming
    return current


def merge_intake_with_state(state: State, intake: TaskIntake) -> dict[str, Any]:
    """Merge latest intake facts without clearing trusted state from prior turns.

    LangGraph checkpointers restore the previous State by thread_id, but normal
    State fields still use last-write-wins semantics. That means returning
    source_mode="unknown" or an empty list from this node would overwrite useful
    facts collected in earlier turns. This merge keeps old values unless the
    latest user message clearly supplies a replacement.
    """

    task_mode = _merge_mode(state.task_mode, intake.task_mode)
    source_mode = _merge_mode(state.source_mode, intake.source_mode)
    function_names = _merge_list(state.function_names, intake.function_names)
    entry_points = _merge_list(state.entry_points, intake.entry_points)
    path_candidates = _merge_list(state.path_candidates, intake.path_candidates)

    return {
        "task_mode": task_mode,
        "source_mode": source_mode,
        "user_goal": intake.user_goal or state.user_goal,
        "function_names": function_names,
        "entry_points": entry_points,
        "path_candidates": path_candidates,
        "mcp_required": source_mode == "mcp",
        "session_phase": "initializing",
        "source_locked": source_mode != "unknown" or state.source_locked,
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }


def session_entry_node(state: State) -> dict[str, Any]:
    """Decide which phase should handle the latest user input.

    这个节点是每次 graph run 的真正入口。它不解析业务内容，只根据
    上一次保存下来的 State 判断“这次输入应该补哪个环节”。

    这样做的原因：
    - thread_id 只能恢复 State，不能阻止节点覆盖 State。
    - 如果每次都从 task_intake_node 开始，模式选择就会反复执行。
    - 进入 blocked 后，resume 的输入应该回到缺失项对应的小节点。
    """

    if state.session_phase == "new":
        return {"session_phase": "initializing"}

    if state.missing_requirements:
        return {"session_phase": "blocked"}

    if state.initialization_complete:
        return {"session_phase": "ready"}

    return {"session_phase": "initializing"}


async def target_intake_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    """Extract only function targets from a follow-up user message.

    当缺少函数名/入口点时，用户后续可能只输入 `main`。这类输入不应该重新
    判断 source_mode，也不应该清空 MCP/路径等已确认状态。
    """

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
    )

    try:
        intake = await extract_task_intake(model, user_input)
    except Exception as exc:
        return {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": f"函数目标识别失败: {exc!r}",
            "missing_requirements": ["function_target"],
            "last_blocking_node": "target_intake_node",
        }

    return {
        "task_mode": _merge_mode(state.task_mode, intake.task_mode),
        "function_names": _merge_list(state.function_names, intake.function_names),
        "entry_points": _merge_list(state.entry_points, intake.entry_points),
        "user_goal": intake.user_goal or state.user_goal,
        "session_phase": "initializing",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }


async def path_intake_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    """Extract only local path candidates from a follow-up user message."""

    user_input = latest_human_text(state)
    if not user_input.strip():
        return {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": "缺少可操作目录或汇编文件路径。",
            "missing_requirements": ["asm_source"],
            "last_blocking_node": "path_intake_node",
        }

    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
    )

    try:
        intake = await extract_task_intake(model, user_input)
    except Exception as exc:
        return {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": f"路径识别失败: {exc!r}",
            "missing_requirements": ["asm_source"],
            "last_blocking_node": "path_intake_node",
        }

    return {
        "source_mode": _merge_mode(state.source_mode, intake.source_mode),
        "path_candidates": _merge_list(state.path_candidates, intake.path_candidates),
        "user_goal": intake.user_goal or state.user_goal,
        "session_phase": "initializing",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }

async def task_intake_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    """Understand the latest user request before deciding the execution path."""

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
    )

    try:
        intake = await extract_task_intake(model, user_input)
    except Exception as exc:
        return {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": f"任务识别失败: {exc!r}",
            "missing_requirements": ["task_intake"],
            "last_blocking_node": "task_intake_node",
        }

    return merge_intake_with_state(state, intake)
