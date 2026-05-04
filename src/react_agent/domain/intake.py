"""Task intake domain models.

这些模型只描述“用户输入里表达了什么”，不代表这些信息已经可信。
例如 path_candidates 是 AI 识别出的路径候选，必须经过路径校验后才能成为
authorized_paths 或 source_files。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


TaskMode = Literal["single_functions", "entry_call_chain", "unknown"]
SourceMode = Literal["mcp", "asm_files", "unknown"]
PathKind = Literal["file", "directory", "unknown"]


class PathCandidate(BaseModel):
    """A path-like item mentioned by the user or verified by the program."""

    path: str = Field(description="本地文件或目录路径")
    type: PathKind = Field(default="unknown", description="路径类型")
    role: str | None = Field(
        default=None,
        description="路径用途，例如 workspace/output/input/ida/database/unknown",
    )


class TaskIntake(BaseModel):
    """Structured interpretation of the latest user request."""

    task_mode: TaskMode = Field(
        default="unknown",
        description="single_functions 表示只还原指定函数；entry_call_chain 表示从入口追调用链",
    )
    source_mode: SourceMode = Field(
        default="unknown",
        description="mcp 表示从 IDA/MCP 获取信息；asm_files 表示从本地汇编文件获取信息",
    )
    user_goal: str = Field(default="", description="用户任务目标摘要")
    function_names: list[str] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    path_candidates: list[PathCandidate] = Field(default_factory=list)
