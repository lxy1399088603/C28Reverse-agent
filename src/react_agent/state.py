"""Define the state structures for the agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from langgraph.managed import IsLastStep
from typing_extensions import Annotated
from react_agent.path_auth import PathCandidate

@dataclass
class InputState:
    messages: Annotated[Sequence[AnyMessage], add_messages] = field(
        default_factory=list
    )

@dataclass
class State(InputState):
    # 这个变量用户无法控制，当步数达到recursion_limit - 1时，该变量被设置为true.
    is_last_step: IsLastStep = field(default=False)
    
    # 可操作目录
    authorized_paths: List[PathCandidate] = field(default_factory=list)
    # 是否需要用户补充信息
    needs_user_input: bool = False
    # 无法继续执行的原因
    blocking_reason: str | None = None
    # mcp连接状态
    mcp_connect_status: bool = False
