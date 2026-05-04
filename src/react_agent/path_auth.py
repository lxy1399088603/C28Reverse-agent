from pydantic import BaseModel, Field
from typing import Literal
from langchain_core.prompts import ChatPromptTemplate

# 路径信息类
class PathCandidate(BaseModel):
    path: str = Field(description="候选路径文本")
    type: Literal["file", "directory"] = Field(description="路径类型")
    role: str | None = Field(
        default=None,
        description="路径用途，例如 workspace/output/input/ida/database/unknown",
    )

class PathCandidates(BaseModel):
    candidates: list[PathCandidate] = Field(default_factory=list)

PATH_INFERENCE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """
你是路径识别器，只负责从用户输入中提取本地路径和文件。

要求：
1. 提取所有 Windows/Linux/macOS 风格的路径。
2. 不要编造路径。
3. 如果用户提供多个路径或文件，必须全部返回。
4. type 只能是 file 或 directory。
5. role 表示用途，例如 workspace/output/input/ida/database/unknown。
6. 你不判断路径是否存在，存在性由程序验证。
7. 必须返回对象，不能返回数组。
8. 如果没有识别到路径，返回：
{{"candidates": []}}
"""),
    ("human", "{user_input}"),
])
