from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


TaskMode = Literal["single_functions", "entry_call_chain", "unknown"]
SourceMode = Literal["mcp", "asm_files", "unknown"]
PathKind = Literal["file", "directory", "unknown"]

# 可操作类路径描述对象
class PathCandidate(BaseModel):
    path: str = Field(description="本地文件或目录路径")
    type: PathKind = Field(default="unknown", description="路径类型")
    role: str | None = Field(
        default=None,
        description="路径用途",
    )


# 任务必要信息
class TaskIntake(BaseModel):

    # 任务模式
    task_mode: TaskMode = Field(
        default="unknown",
        description="single_functions 表示只还原指定函数；entry_call_chain 表示从入口追调用链",
    )
    # 资源模式 mcp/asm
    source_mode: SourceMode = Field(
        default="unknown",
        description="mcp 表示从 IDA/MCP 获取信息；asm_files 表示从本地汇编文件获取信息",
    )
    # 函数列表
    function_names: list[str] = Field(
        default_factory=list,
        description="用户提供的目标函数列表；包括单函数目标，也包括入口调用链的入口函数。",
    )
    # 可操作路径列表
    path_candidates: list[PathCandidate] = Field(default_factory=list)
