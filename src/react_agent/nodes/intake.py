"""Task intake graph node."""

from __future__ import annotations

import re
from typing import Any, TypeVar

from langgraph.runtime import Runtime
from langchain.chat_models import BaseChatModel

from react_agent.context import Context
from react_agent.utils import latest_human_text
from react_agent.state import State
from react_agent.utils import load_chat_model
from react_agent.domain.intake import PathCandidate, TaskIntake
from react_agent.prompts.intake_prompt import TASK_INTAKE_PROMPT


# 对本地模型来说，structured output 在“路径 + MCP + 函数名”混合输入里可能会漏抽字段，
# 例如把 `_main` 漏掉，或者把显式 Windows 路径抽错。
# 这里补一层“只提取确定事实”的兜底逻辑：
# - 只从用户原文中提取非常明确的模式、路径和函数目标
# - 不做存在性验证，不猜测路径类型，不替代后续 validate 节点
# - 这层只负责防止明显字段在 intake 阶段丢失
WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\r\n\"']+")
HEX_ENTRY_PATTERN = re.compile(r"0x[0-9A-Fa-f]+")
ASCII_SYMBOL_PATTERN = re.compile(r"[_A-Za-z][_A-Za-z0-9]*")
ENTRY_CALL_CHAIN_KEYWORDS = (
    "调用链",
    "入口函数",
    "入口点",
    "从入口",
    "递归分析",
    "往后还原",
    "全量还原",
)
FUNCTION_CUE_PATTERN = re.compile(
    r"(?:还原|分析|读取|获取|恢复|导出|写入|生成|处理)?\s*([_A-Za-z][_A-Za-z0-9]*|sub_[0-9A-Fa-f]+)\s*函数"
)
ENTRY_CUE_PATTERN = re.compile(
    r"(?:入口函数|入口点|从)\s*(?:为|是|:)?\s*(0x[0-9A-Fa-f]+|[_A-Za-z][_A-Za-z0-9]*)"
)
IGNORED_SYMBOLS = {
    "mcp",
    "ida",
    "asm",
    "lst",
    "txt",
    "disasm",
}


# 通过LLM从用户输出中分析任务类型、资源类型、用户目标、函数列表等所需必要信息
async def extract_task_intake(
    model: BaseChatModel,
    user_input: str,
) -> TaskIntake:

    extractor = TASK_INTAKE_PROMPT | model.with_structured_output(TaskIntake)
    return await extractor.ainvoke({"user_input": user_input})


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _dedupe_path_candidates(items: list[PathCandidate]) -> list[PathCandidate]:
    seen: set[tuple[str, str]] = set()
    result: list[PathCandidate] = []
    for item in items:
        key = (item.path, item.type)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _extract_path_candidates_from_text(user_input: str) -> list[PathCandidate]:
    candidates: list[PathCandidate] = []
    for raw_path in WINDOWS_PATH_PATTERN.findall(user_input):
        # 路径类型留给 validate_paths_node 判定，这里只保留原始候选。
        candidates.append(PathCandidate(path=raw_path.rstrip("，。,.;；"), type="unknown"))
    return _dedupe_path_candidates(candidates)


def _extract_function_targets_from_text(
    user_input: str,
) -> tuple[list[str], list[str], bool]:
    function_names: list[str] = []
    entry_points: list[str] = []
    has_call_chain_cue = any(keyword in user_input for keyword in ENTRY_CALL_CHAIN_KEYWORDS)

    for match in FUNCTION_CUE_PATTERN.findall(user_input):
        if match.lower() not in IGNORED_SYMBOLS:
            function_names.append(match)

    for match in ENTRY_CUE_PATTERN.findall(user_input):
        if match.lower() in IGNORED_SYMBOLS:
            continue
        entry_points.append(match)

    # 如果用户整句只有一个明确的函数名或入口点，也要稳稳识别出来。
    stripped = user_input.strip()
    if HEX_ENTRY_PATTERN.fullmatch(stripped):
        entry_points.append(stripped)
    elif ASCII_SYMBOL_PATTERN.fullmatch(stripped) and stripped.lower() not in IGNORED_SYMBOLS:
        function_names.append(stripped)

    return (
        _dedupe_keep_order(function_names),
        _dedupe_keep_order(entry_points),
        has_call_chain_cue,
    )


def _infer_source_mode_from_text(user_input: str) -> str:
    lowered = user_input.lower()
    if "mcp" in lowered or "ida mcp" in lowered:
        return "mcp"
    if any(token in lowered for token in (".asm", ".lst", "asm文件", "listing", "disasm", ".txt")):
        return "asm_files"
    return "unknown"


def apply_deterministic_intake_fallback(
    user_input: str,
    intake: TaskIntake,
) -> TaskIntake:
    """Patch obvious intake misses using deterministic signals from the raw text.

    这一步不是替代 LLM，而是在以下场景兜底：
    - 用户原话里已经明确写出 `_main函数`
    - 用户原话里已经明确带了 Windows 路径
    - 用户原话里已经明确提到 `mcp`

    这些都属于“文本中可直接确认的事实”，适合在本地模型不稳定时兜底。
    """

    fallback_paths = _extract_path_candidates_from_text(user_input)
    fallback_functions, fallback_entries, has_call_chain_cue = _extract_function_targets_from_text(user_input)
    fallback_source_mode = _infer_source_mode_from_text(user_input)

    function_names = _dedupe_keep_order(intake.function_names + fallback_functions)
    entry_points = _dedupe_keep_order(intake.entry_points + fallback_entries)
    path_candidates = _dedupe_path_candidates(fallback_paths + intake.path_candidates)

    task_mode = intake.task_mode
    if has_call_chain_cue:
        task_mode = "entry_call_chain"
    elif function_names:
        # 用户明确说“某某函数”时，这个信号强于本地模型误判出来的 entry_call_chain。
        task_mode = "single_functions"
    elif entry_points and intake.task_mode == "unknown":
        task_mode = "entry_call_chain"

    source_mode = intake.source_mode
    if fallback_source_mode != "unknown":
        source_mode = fallback_source_mode

    return intake.model_copy(
        update={
            "task_mode": task_mode,
            "source_mode": source_mode,
            "function_names": function_names,
            "entry_points": entry_points,
            "path_candidates": path_candidates,
        }
    )


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


# Agent的预处理，判断是否可以进入ready状态进行还原环路操作
def session_entry_node(state: State) -> dict[str, Any]:
    if state.session_phase == "new":
        return {"session_phase": "initializing"}

    if state.missing_requirements:
        return {"session_phase": "blocked"}

    if state.initialization_complete:
        return {"session_phase": "ready"}

    return {"session_phase": "initializing"}


# 做函数目标分析function_names/entry_points
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
        intake = await extract_task_intake(model, user_input)
        intake = apply_deterministic_intake_fallback(user_input, intake)
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
        intake = await extract_task_intake(model, user_input)
        intake = apply_deterministic_intake_fallback(user_input, intake)
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
        intake = await extract_task_intake(model, user_input)
        intake = apply_deterministic_intake_fallback(user_input, intake)
    except Exception as exc:
        return {
            "session_phase": "blocked",
            "needs_user_input": True,
            "blocking_reason": f"任务识别失败: {exc!r}",
            "missing_requirements": ["task_intake"],
            "last_blocking_node": "task_intake_node",
        }

    return merge_intake_with_state(state, intake)
