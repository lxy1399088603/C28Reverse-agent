"""Define the state structures for the agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence
from pydantic import BaseModel, Field

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from langgraph.managed import IsLastStep
from typing_extensions import Annotated

@dataclass
class InputState:
    messages: Annotated[Sequence[AnyMessage], add_messages] = field(
        default_factory=list
    )
    """
    追踪Agent执行状态的消息序列

    写入模式:
    1. HumanMessage - 用户输入
    2. AIMessage with .tool_calls - agent使用工具去收集信息
    3. ToolMessage(s) - 工具执行后返回的响应数据或错误信息
    4. AIMessage without .tool_calls - agent返回的信息。非工具调用
    5. HumanMessage - 用户做出响应，开启下一轮对话

    步骤 2-5 可以重复执行.

    The `add_messages` 注解确保新消息能和现有消息进行合并, 通过ID进行维护，实现追加的效果，除非ID相同。
    """

class PathCandidate(BaseModel):
    path: str = Field(description="候选路径文本")
    role: str | None = Field(
        default=None,
        description="路径用途，例如 workspace/output/input/ida/database/unknown",
    )

class State(InputState):
    # 这个变量用户无法控制，当步数达到recursion_limit - 1时，该变量被设置为true.
    is_last_step: IsLastStep = Field(default=False)
    
    # 可操作目录
    authorized_paths: List[PathCandidate] = None
    # 是否需要用户补充信息
    needs_user_input: bool = False
    # 无法继续执行的原因
    blocking_reason: str | None = None
    # mcp连接状态
    mcp_connect_status: bool = False
