"""Configurable runtime parameters for the agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Annotated, Any

from react_agent.prompts.system_prompt import SYSTEM_PROMPT


def _coerce_env_value(default: Any, value: str) -> Any:
    """Convert environment strings to the field's expected primitive type."""

    if isinstance(default, bool):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return int(value)
    return value


@dataclass(kw_only=True)
class Context:
    """Runtime context passed to LangGraph nodes.

    字段会自动从环境变量读取，规则是字段名大写：
    - model -> MODEL
    - base_url -> BASE_URL
    - mcp_enabled -> MCP_ENABLED

    为了兼容 OpenAI 生态的命名，base_url 也会兼容 OPENAI_API_BASE。
    """

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

    max_search_results: int = field(
        default=10,
        metadata={"description": "Maximum Tavily search results per query."},
    )

    # MCP 配置。你的当前 MCP 是 HTTP：
    # [mcp_servers.ida-pro-mcp]
    # url = "http://127.0.0.1:13337/mcp"
    mcp_enabled: bool = field(
        default=False,
        metadata={"description": "Whether MCP integration is enabled."},
    )

    mcp_server_name: str = field(
        default="ida-pro-mcp",
        metadata={"description": "MCP server name used by MultiServerMCPClient."},
    )

    mcp_transport: str = field(
        default="http",
        metadata={"description": "MCP transport: http, streamable_http, or stdio."},
    )

    mcp_command: str | None = field(
        default=None,
        metadata={"description": "Command used to start a stdio MCP server."},
    )

    mcp_args: str | None = field(
        default=None,
        metadata={"description": "Arguments for a stdio MCP server."},
    )

    mcp_url: str | None = field(
        default="http://127.0.0.1:13337/mcp",
        metadata={"description": "HTTP MCP endpoint URL."},
    )

    def __post_init__(self) -> None:
        """Load omitted context fields from environment variables."""

        for item in fields(self):
            if not item.init:
                continue

            current_value = getattr(self, item.name)
            if current_value != item.default:
                continue

            env_value = os.environ.get(item.name.upper())
            if item.name == "base_url":
                env_value = env_value or os.environ.get("OPENAI_API_BASE")

            if env_value is not None:
                setattr(self, item.name, _coerce_env_value(item.default, env_value))
