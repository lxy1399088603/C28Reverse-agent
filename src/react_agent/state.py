from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from langgraph.managed import IsLastStep
from typing_extensions import Annotated

from react_agent.domain.intake import PathCandidate, SourceMode, TaskMode

SessionPhase = Literal["new",           # 新会话初始状态
                       "initializing",  # 初始化中，必须参数还没有补充完
                        "ready",        # 可执行状态
                        "blocked",      # 阻塞状态，缺少必要信息无法自动执行
                        "running",      # 正在执行任务状态
                        "error"         # 不可预期错误状态
                        ]


@dataclass
class InputState:

    # 同一个thread_id 下的短期记忆（对话历史记录）
    messages: Annotated[Sequence[AnyMessage], add_messages] = field(
        default_factory=list
    )


@dataclass
class State(InputState):

    # LangGraph 管理字段：即将达到递归上限时会被置为 True。
    is_last_step: IsLastStep = field(default=False)

    # 第一层初始化：任务理解结果。
    task_mode: TaskMode = "unknown" # 任务模式
    source_mode: SourceMode = "unknown" # 资源获取类型
    function_names: list[str] = field(default_factory=list) # 用户提供的函数列表
    path_candidates: list[PathCandidate] = field(default_factory=list) # 可操作路径集合

    # 第二层初始化：程序校验后的事实。
    authorized_paths: list[PathCandidate] = field(default_factory=list) # 已认证路径
    source_files: list[PathCandidate] = field(default_factory=list)     # 已认证文件列表
    mcp_required: bool = True      # mcp开启
    mcp_connect_status: bool = False    # mcp连接状态
    mcp_tool_names: list[str] = field(default_factory=list) # mcp工具名

    # 第三层初始化：真正执行前的准备结果。
    function_queue: list[str] = field(default_factory=list) # 函数还原队列
    initialization_complete: bool = False # 初始化完整

    # 会话生命周期字段。
    # thread_id 负责从 checkpointer 恢复 State；这些字段负责告诉入口节点
    # “当前会话走到哪一步了”，避免每次用户输入都重新做完整初始化。
    session_phase: SessionPhase = "new" # 预处理阶段
    # 暂时起提示作用
    source_locked: bool = False # 满足资源模式后禁止修改
    paths_locked: bool = False # 满足可操作路径提供后禁止修改
    mcp_locked: bool = False    # 满足mcp检测后禁止修改
    targets_locked: bool = False # 满足目标函数信息后禁止修改
    last_blocking_node: str | None = None # 上一个阻塞节点

    # 还原
    stop : bool = False 
    current_function: str | None = None
    current_round_start_message_id: str | None = None
    completed_functions: list[str] = field(default_factory=list)
    failed_functions: list[str] = field(default_factory=list)

    # 验证
    verification_status: str | None = None
    verification_reason: str | None = None
    verification_retry_count: int = 0
    verification_max_retries: int = 3
    current_decompile_text: str | None = None
    verification_evidence_gaps: list[str] = field(default_factory=list)
    verification_retry_hint: str | None = None

    # 人机环路控制字段。7x24 场景下，只在真正缺少必要信息时使用。
    needs_user_input: bool = False # 需要用户补充信息
    blocking_reason: str | None = None # 阻塞提示信息
    missing_requirements: list[str] = field(default_factory=list) # 下一个恢复节点

    # 安全
    max_chain_depth : int = 20 # 最大递归深度
