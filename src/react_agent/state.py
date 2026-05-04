"""Define the state structures for the agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from langgraph.managed import IsLastStep
from typing_extensions import Annotated

from react_agent.domain.intake import PathCandidate, SourceMode, TaskMode


@dataclass
class InputState:
    """External input accepted by the graph.

    `messages` 是 LangGraph 的短期会话记忆入口。UI 不需要把完整历史传入，
    只要保持同一个 thread_id，checkpointer 会帮我们恢复前序状态。
    """

    messages: Annotated[Sequence[AnyMessage], add_messages] = field(
        default_factory=list
    )


@dataclass
class State(InputState):
    """Global state shared by all graph nodes.

    设计原则：
    1. AI 识别出的候选信息和程序校验后的可信事实分开保存。
    2. 只有跨节点需要使用的信息才进入 State。
    3. 控制流字段集中在 needs_user_input / blocking_reason / missing_requirements。
    """

    # LangGraph 管理字段：即将达到递归上限时会被置为 True。
    is_last_step: IsLastStep = field(default=False)

    # 第一层初始化：任务理解结果。
    task_mode: TaskMode = "unknown"
    source_mode: SourceMode = "unknown"
    user_goal: str = ""
    function_names: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    path_candidates: list[PathCandidate] = field(default_factory=list)

    # 第二层初始化：程序校验后的事实。
    authorized_paths: list[PathCandidate] = field(default_factory=list)
    source_files: list[PathCandidate] = field(default_factory=list)
    mcp_required: bool = False
    mcp_connect_status: bool = False

    # 第三层初始化：真正执行前的准备结果。
    function_queue: list[str] = field(default_factory=list)
    initialization_complete: bool = False

    # 人机环路控制字段。7x24 场景下，只在真正缺少必要信息时使用。
    needs_user_input: bool = False
    blocking_reason: str | None = None
    missing_requirements: list[str] = field(default_factory=list)
