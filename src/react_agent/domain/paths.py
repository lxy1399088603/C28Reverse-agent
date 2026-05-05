"""Path validation domain models."""

from __future__ import annotations
from pydantic import BaseModel, Field
from react_agent.domain.intake import PathCandidate

# 路径验证结果对象
class PathValidationResult(BaseModel):
    authorized_paths: list[PathCandidate] = Field(default_factory=list) # 已认证路径
    source_files: list[PathCandidate] = Field(default_factory=list)     # 已认证文件
    invalid_paths: list[str] = Field(default_factory=list)              # 不存在的路径
