"""Function target validation graph node."""

from __future__ import annotations

from typing import Any

from react_agent.state import State
from react_agent.domain.intake import TaskMode



def build_function_queue(
    task_mode: TaskMode,
    function_names: list[str],
    entry_points: list[str],
) -> tuple[TaskMode, list[str], list[str]]:
    """Build the execution FIFO queue from the interpreted task target.

    返回 `(normalized_task_mode, function_queue, missing_requirements)`。
    这里不访问 LLM，也不访问文件系统，方便单独测试。
    """

    clean_functions = [item.strip() for item in function_names if item.strip()]
    clean_entries = [item.strip() for item in entry_points if item.strip()]

    if task_mode == "single_functions":
        if clean_functions:
            return task_mode, clean_functions, []
        return task_mode, [], ["function_names"]

    if task_mode == "entry_call_chain":
        if clean_entries:
            return task_mode, clean_entries, []
        return task_mode, [], ["entry_points"]

    # 兜底：如果用户只输入 main/sub_xxx 这种函数名，则按单函数/多函数模式处理。
    if clean_functions:
        return "single_functions", clean_functions, []

    if clean_entries:
        return "entry_call_chain", clean_entries, []

    return "unknown", [], ["function_target"]

def validate_targets_node(state: State) -> dict[str, Any]:
    """Validate function targets and prepare the FIFO function queue."""

    task_mode, function_queue, missing = build_function_queue(
        state.task_mode,
        state.function_names,
        state.entry_points,
    )

    if missing:
        return {
            "task_mode": task_mode,
            "function_queue": [],
            "needs_user_input": True,
            "blocking_reason": "缺少需要还原的函数名或入口点。",
            "missing_requirements": missing,
        }

    return {
        "task_mode": task_mode,
        "function_queue": function_queue,
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }
