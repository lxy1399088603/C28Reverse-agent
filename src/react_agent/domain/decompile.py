from typing import Literal
from pydantic import BaseModel, Field


class FunctionVerificationResult(BaseModel):
    status: Literal["verified", "partial", "need_more_evidence", "failed"] = Field(
        description="当前函数验收状态"
    )
    reason: str = Field(
        default="",
        description="验收结论原因"
    )
    evidence_gaps: list[str] = Field(
        default_factory=list,
        description="仍缺少的关键证据"
    )
    retry_hint: str = Field(
        default="",
        description="如果需要继续查证，下一轮应查询什么"
    )
    can_persist: bool = Field(
        default=False,
        description="当前结果是否允许落盘"
    )
