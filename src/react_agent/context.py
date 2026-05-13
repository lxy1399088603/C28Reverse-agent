
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Annotated, Any

from react_agent.prompts.system_prompt import SYSTEM_PROMPT


# 转换数据类型
def _coerce_env_value(default: Any, value: str) -> Any:
    if isinstance(default, bool):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return int(value)
    return value


def _first_non_empty(*values: str | None) -> str | None:
    """Return the first non-empty environment value."""

    for value in values:
        if value is not None and value != "":
            return value
    return None


@dataclass(kw_only=True)
class Context:
    """Runtime context passed to LangGraph nodes.

    字段会自动从环境变量读取，规则是字段名大写：
    - llm_profile -> LLM_PROFILE
    - model -> MODEL
    - base_url -> BASE_URL
    - api_key -> API_KEY
    - mcp_enabled -> MCP_ENABLED

    为了兼容 OpenAI 生态的命名：
    - base_url 也会兼容 OPENAI_API_BASE
    - api_key 也会兼容 OPENAI_API_KEY

    此外支持基于 LLM_PROFILE 的一键切换：
    - LLM_PROFILE=openai -> 读取 OPENAI_MODEL / OPENAI_BASE_URL / OPENAI_API_KEY
    - LLM_PROFILE=local  -> 读取 LOCAL_MODEL / LOCAL_BASE_URL / LOCAL_API_KEY
    """

    llm_profile: str = field(
        default="openai",
        metadata={"description": "Selected LLM profile: openai or local."},
    )

    system_prompt: str = field(
        default=SYSTEM_PROMPT,
        metadata={"description": "System prompt used by the agent."},
    )

    model: Annotated[str, {"__template_metadata__": {"kind": "llm"}}] = field(
        default="openai/gpt-5.4",
        metadata={"description": "Model name in provider/model-name form."},
    )

    base_url: str | None = field(
        default=None,
        metadata={
            "description": "Optional custom base URL for OpenAI-compatible requests."
        },
    )

    api_key: str | None = field(
        default=None,
        metadata={"description": "Optional API key for the selected model provider."},
    )

    max_search_results: int = field(
        default=10,
        metadata={"description": "Maximum Tavily search results per query."},
    )

    # MCP 配置。你的当前 MCP 是 HTTP：
    # [mcp_servers.ida-pro-mcp]
    # url = "http://127.0.0.1:13337/mcp"
    mcp_enabled: bool = field(
        default=False,
        metadata={"description": "是否启用 MCP."},
    )

    mcp_server_name: str = field(
        default="ida-pro-mcp",
        metadata={"description": "MCP服务名."},
    )

    mcp_transport: str = field(
        default="http",
        metadata={"description": "MCP传输类型."},
    )

    mcp_command: str | None = field(
        default=None,
        metadata={"description": "用于启动 stdio MCP 服务器的命令."},
    )

    mcp_args: str | None = field(
        default=None,
        metadata={"description": "stdio MCP 服务器的参数."},
    )

    mcp_url: str | None = field(
        default="http://127.0.0.1:13337/mcp",
        metadata={"description": "HTTP MCP URL."},
    )

    # 从环境变量加载被省略的上下文字段
    def __post_init__(self) -> None:

        for item in fields(self):
            if not item.init:
                continue

            current_value = getattr(self, item.name)
            if current_value != item.default:
                continue

            env_value = os.environ.get(item.name.upper())
            if item.name == "base_url":
                env_value = env_value or os.environ.get("OPENAI_API_BASE")
            if item.name == "api_key":
                env_value = env_value or os.environ.get("OPENAI_API_KEY")

            if env_value is not None:
                setattr(self, item.name, _coerce_env_value(item.default, env_value))

        self._apply_llm_profile()

    # LLM_PROFILE一键切换模型
    def _apply_llm_profile(self) -> None:

        profile = str(self.llm_profile).strip().lower()

        if profile == "local":
            selected_model = _first_non_empty(os.environ.get("LOCAL_MODEL"))
            selected_base_url = _first_non_empty(os.environ.get("LOCAL_BASE_URL"))
            selected_api_key = _first_non_empty(os.environ.get("LOCAL_API_KEY"))
        else:
            selected_model = _first_non_empty(
                os.environ.get("OPENAI_MODEL"),
                os.environ.get("MODEL"),
            )
            selected_base_url = _first_non_empty(
                os.environ.get("OPENAI_BASE_URL"),
                os.environ.get("OPENAI_API_BASE"),
                os.environ.get("BASE_URL"),
            )
            selected_api_key = _first_non_empty(os.environ.get("OPENAI_API_KEY"))

        if selected_model:
            self.model = selected_model
        if selected_base_url:
            self.base_url = selected_base_url
        if selected_api_key:
            self.api_key = selected_api_key
