from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from react_agent.state import State
from react_agent.utils import load_chat_model

class PathCandidate(BaseModel):
    path: str = Field(description="候选路径文本")
    role: str | None = Field(
        default=None,
        description="路径用途，例如 workspace/output/input/ida/database/unknown",
    )

class PathInference(BaseModel):
    has_paths: bool = Field(description="用户输入中是否提供了工作路径")
    candidates: list[PathCandidate] = Field(default_factory=list)