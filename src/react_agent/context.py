"""Define the configurable parameters for the agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Annotated

from react_agent.prompts.system_prompt import SYSTEM_PROMPT

# 上下文定义
@dataclass(kw_only=True)
class Context:
    """agent上下文."""

    system_prompt: str = field(
        default=SYSTEM_PROMPT,
        metadata={
            "description": "系统提示词为agent提供介绍. "
            "该提示为agent设置上下文和行为."
        },
    )

    model: Annotated[str, {"__template_metadata__": {"kind": "llm"}}] = field(
        default="anthropic/claude-sonnet-4-5-20250929",
        metadata={
            "description": "大模型名字. "
            "Should be in the form: provider/model-name."
        },
    )

    base_url: str | None = field(
        default=None,
        metadata={
            "description": "Optional custom base URL for OpenAI-compatible or provider API requests."
        },
    )

    max_search_results: int = field(
        default=10,
        metadata={
            "description": "每个搜索查询返回的最大搜索结果和数量."
        },
    )

    def __post_init__(self) -> None:
        """没有作为参数传入的属性获取环境变量"""
        for f in fields(self):
            if not f.init:
                continue

            if getattr(self, f.name) == f.default:
                setattr(self, f.name, os.environ.get(f.name.upper(), f.default))
