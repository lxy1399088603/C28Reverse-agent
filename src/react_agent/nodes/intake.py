"""Task intake graph node."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime
from langchain.chat_models import BaseChatModel

from react_agent.context import Context
from react_agent.utils import latest_human_text
from react_agent.state import State
from react_agent.utils import load_chat_model
from react_agent.domain.intake import TaskIntake
from react_agent.prompts.intake_prompt import TASK_INTAKE_PROMPT


async def extract_task_intake(
    model: BaseChatModel,
    user_input: str,
) -> TaskIntake:
    """Extract task mode, source mode, paths, and targets from user input."""

    extractor = TASK_INTAKE_PROMPT | model.with_structured_output(TaskIntake)
    return await extractor.ainvoke({"user_input": user_input})

async def task_intake_node(
    state: State,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    """Understand the latest user request before deciding the execution path."""

    user_input = latest_human_text(state)
    if not user_input.strip():
        return {
            "needs_user_input": True,
            "blocking_reason": "缺少用户任务描述。",
            "missing_requirements": ["user_task"],
        }

    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
    )

    try:
        intake = await extract_task_intake(model, user_input)
    except Exception as exc:
        return {
            "needs_user_input": True,
            "blocking_reason": f"任务识别失败: {exc!r}",
            "missing_requirements": ["task_intake"],
        }

    return {
        "task_mode": intake.task_mode,
        "source_mode": intake.source_mode,
        "user_goal": intake.user_goal,
        "function_names": intake.function_names,
        "entry_points": intake.entry_points,
        "path_candidates": intake.path_candidates,
        "mcp_required": intake.source_mode == "mcp",
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }
