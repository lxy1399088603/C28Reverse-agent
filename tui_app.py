"""Terminal chat UI for the LangGraph ReAct agent.

Run with:
    python tui_app.py --inline
"""

from __future__ import annotations

import argparse
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AIMessageChunk
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static, TextArea

from react_agent import graph
from react_agent.context import Context
from react_agent.utils import get_message_text
from langgraph.types import Command

def _assistant_chunk_text(chunk: Any, metadata: dict[str, Any] | None = None) -> str:
    """从 LangGraph 的流式消息事件中，只提取 Assistant 的文本内容。

    stream_mode="messages" 会流出多种消息，例如 AIMessageChunk、ToolMessage 等。
    UI 聊天框只应该显示最终面向用户的 Assistant 文本，因此这里过滤掉工具消息
    和初始化节点里的结构化 LLM 输出。
    """
    if metadata and metadata.get("langgraph_node") != "call_model":
        return ""

    if isinstance(chunk, AIMessage | AIMessageChunk):
        return get_message_text(chunk)
    return ""


def _short_error_text(error: Exception) -> str:
    """Keep unexpected UI errors readable instead of rendering full tracebacks."""

    text = str(error).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if any(marker in line for marker in ("Error:", "Exception:", "AttributeError:", "ValueError:")):
            return f"{error.__class__.__name__}: {line}"
    if lines:
        return f"{error.__class__.__name__}: {lines[-1]}"
    return error.__class__.__name__


class AgentTui(App[None]):
    """Textual 终端 UI。

    这个类只负责界面展示、用户输入、流式渲染和聊天记录落盘。
    Agent 的短期记忆不由 self.history 管理，而是由 LangGraph checkpointer
    根据 thread_id 管理。
    """

    TITLE = "C28x Reverse Agent"
    SUB_TITLE = "Textual Console"

    # Textual 的 CSS 样式：整体垂直布局，上方状态栏，中间聊天区，下方输入区。
    CSS = """
    Screen {
        layout: vertical;
        background: #14161a;
    }

    Header {
        background: #1a222b;
        color: #d7dde4;
    }

    #status-row {
        height: 3;
        padding: 0 1;
        background: #181d23;
        border-bottom: solid #2d3742;
        layout: horizontal;
    }

    #status {
        width: 1fr;
        content-align: left middle;
        color: #b9c3cf;
    }

    #meta {
        width: 52;
        content-align: right middle;
        color: #7f8b99;
    }

    #chat {
        height: 1fr;
        border: round #3a4755;
        background: #171a1f;
        color: #d9dee5;
        padding: 1 1;
        margin: 1 1 0 1;
    }

    #streaming {
        height: 12;
        min-height: 6;
        max-height: 16;
        border: round #275d8f;
        background: #12171d;
        color: #d8e6f5;
        margin: 1 1 0 1;
        display: none;
    }

    #input-row {
        height: 7;
        padding: 0 1 1 1;
        margin-top: 1;
    }

    #input-panel {
        border: round #2f80d1;
        background: #1a1f26;
        padding: 0 1;
        height: 3;
        content-align: center middle;
    }

    #prompt {
        width: 1fr;
        border: none;
        background: transparent;
        color: #eef3f8;
    }

    #input-help {
        height: 2;
        padding: 0 1;
        color: #7f8b99;
        content-align: left middle;
    }

    Footer {
        background: #182029;
        color: #d1d8e0;
    }
    """

    # 快捷键定义：
    # Ctrl+C 退出程序；Ctrl+L 只清空 UI 显示，不清空 LangGraph thread 记忆。
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_chat", "Clear UI"),
    ]

    def __init__(self) -> None:
        super().__init__()
        # UI 展示历史，只用于渲染聊天框和写入 chat_logs，不作为 Agent 推理输入。
        self.history: list[tuple[str, str]] = []
        # LangGraph 短期记忆的会话 ID。只要 thread_id 不变，graph 会保留会话状态。
        self.thread_id = uuid.uuid4().hex
        # 当前正在流式生成的 Assistant 回复缓存。
        self._assistant_buffer = ""
        # 流式 UI 渲染节流状态。
        # Agent 可能每个 token 都推送一次消息，如果每次都清屏并重画完整历史，
        # 长回答会非常卡。这里记录上次刷新时间和长度，只按固定间隔刷新 UI。
        self._last_stream_render_at = 0.0
        self._last_stream_render_len = 0
        # UI 聊天记录落盘文件，方便后续复制、追踪和回放。
        self.transcript_path = self._create_transcript_path()
        # 中断等待
        self.awaiting_resume = False

    def compose(self) -> ComposeResult:
        """声明 Textual 界面结构。"""
        yield Header(show_clock=True)
        with Horizontal(id="status-row"):
            yield Static("Ready", id="status")
            yield Static("", id="meta")
        yield RichLog(id="chat", wrap=True, markup=True, highlight=True)
        yield TextArea(
            "",
            id="streaming",
            read_only=True,
            soft_wrap=True,
            show_line_numbers=False,
            show_cursor=False,
        )
        with Vertical(id="input-row"):
            with Horizontal(id="input-panel"):
                yield Input(placeholder="输入问题并按 Enter", id="prompt")
            yield Static("", id="input-help")
        yield Footer()

    def on_mount(self) -> None:
        """Textual 应用挂载完成后执行。"""
        # 启动后让输入框自动获得焦点，用户可以直接打字。
        self.query_one("#prompt", Input).focus()
        self.query_one("#chat", RichLog).border_title = " Conversation "
        self.query_one("#streaming", TextArea).border_title = " Live Output "
        self._refresh_meta()
        self._render_chat()

    def action_clear_chat(self) -> None:
        """只清空 UI 展示历史，不清空 LangGraph thread memory。"""
        self.history.clear()
        self._assistant_buffer = ""
        self._last_stream_render_at = 0.0
        self._last_stream_render_len = 0
        self.query_one("#chat", RichLog).clear()
        self._hide_streaming_answer()
        self._render_chat()
        self.query_one("#status", Static).update("UI cleared; thread memory kept")
        self._refresh_meta()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """用户在输入框按 Enter 后触发。"""
        prompt = event.value.strip()
        if not prompt:
            return

        # 清空并临时禁用输入框，避免当前轮 Agent 未完成时重复提交。
        prompt_input = self.query_one("#prompt", Input)
        prompt_input.value = ""
        prompt_input.disabled = True

        # self.history 仅用于 UI 显示和 transcript 落盘。
        # 注意：这里不会把 history 传给 Agent，Agent 短期记忆交给 LangGraph 管理。
        self.history.append(("user", prompt))
        self._append_transcript("User", prompt)
        self._assistant_buffer = ""
        self._last_stream_render_at = 0.0
        self._last_stream_render_len = 0

        # 立即把用户输入渲染到聊天框，并显示 Agent 正在思考。
        self._render_chat()
        self._show_streaming_answer("Thinking...")
        self.query_one("#status", Static).update(
            f"Thinking... thread={self.thread_id[:8]}"
        )
        self._refresh_meta()

        # 启动后台异步任务调用 LangGraph Agent。
        self.run_agent(prompt)


    @work(exclusive=True)
    async def run_agent(self, prompt: str) -> None:
        """运行 LangGraph graph，并把 Assistant 的流式 token 渲染到 UI。"""
        # thread_id 是 LangGraph checkpointer 识别短期记忆的关键。
        # 同一个 thread_id 下，每轮只需要传当前用户消息，历史由 graph 状态恢复。
        config = {"configurable": {"thread_id": self.thread_id}}

        try:
            graph_input = (
                Command(resume=prompt)
                if self.awaiting_resume # 流程恢复标识
                else {"messages": [("user", prompt)]}
            )
            async for mode, payload in graph.astream(
                graph_input,
                config=config,
                context=Context(),
                stream_mode=["messages", "updates"],
            ):
                if mode == "updates":
                    if "__interrupt__" in payload:
                        interrupt_value = payload["__interrupt__"][0].value

                        question = interrupt_value
                        if isinstance(interrupt_value, dict):
                            question = interrupt_value.get("question", str(interrupt_value))

                        self.awaiting_resume = True
                        self._assistant_buffer = str(question)
                        self.history.append(("assistant", self._assistant_buffer))
                        self._append_transcript("Agent", self._assistant_buffer)
                        self._hide_streaming_answer()
                        self._render_chat()
                        self.query_one("#status", Static).update(
                            f"Waiting for user input; thread={self.thread_id[:8]}"
                        )
                        self._refresh_meta()
                        return

                elif mode == "messages":
                    chunk = payload[0] if isinstance(payload, tuple) else payload
                    metadata = payload[1] if isinstance(payload, tuple) and len(payload) > 1 else None
                    text = _assistant_chunk_text(chunk, metadata)
                    if text:
                        self._assistant_buffer += text
                        self._render_streaming_answer()

            # 本轮完成后，把最终 Assistant 回复保存到 UI history 和 transcript。
            self.awaiting_resume = False
            self.history.append(("assistant", self._assistant_buffer))
            self._append_transcript("Agent", self._assistant_buffer)
            self._hide_streaming_answer()
            self._render_chat()
            self.query_one("#status", Static).update(
                f"Ready; thread={self.thread_id[:8]}"
            )
            self._refresh_meta()
        except Exception as exc:
            # 出错时也写入 UI history 和 transcript，方便排查。
            self.awaiting_resume = False
            error = _short_error_text(exc)
            self.history.append(("error", error))
            self._append_transcript("Error", f"{exc!r}")
            self._hide_streaming_answer()
            self._render_chat()
            self.query_one("#status", Static).update("Error")
            self._refresh_meta()
        finally:
            # 无论成功还是失败，都恢复输入框。
            prompt_input = self.query_one("#prompt", Input)
            prompt_input.disabled = False
            prompt_input.focus()

    def _render_streaming_answer(self, *, force: bool = False) -> None:
        """渲染当前正在流式生成的 Assistant 回复。"""

        now = time.monotonic()
        new_chars = len(self._assistant_buffer) - self._last_stream_render_len

        # 性能关键点：
        # 历史消息在 RichLog 中保持稳定，当前回答只更新 #streaming。
        # 即便如此，超长文本频繁 update 仍然会有成本，所以继续保留时间
        # 和字符数量双重节流。
        if not force and now - self._last_stream_render_at < 0.08 and new_chars < 96:
            return

        start = self._last_stream_render_len
        delta = self._assistant_buffer[start:]
        if not delta and not force:
            return

        self._append_streaming_answer(delta, reset=start == 0)
        self._last_stream_render_at = now
        self._last_stream_render_len = len(self._assistant_buffer)

    def _show_streaming_answer(self, content: str) -> None:
        """Show a short placeholder before real streaming text arrives."""

        streaming = self.query_one("#streaming", TextArea)
        streaming.display = True
        streaming.load_text(f"Agent:\n{content}")
        streaming.scroll_end(animate=False)

    def _append_streaming_answer(self, content: str, *, reset: bool = False) -> None:
        """Append new stream text without rebuilding the whole TextArea document.

        之前用 load_text(full_answer) 每次重载全文，回答越长越卡，甚至看起来像
        活动框停住。这里改成只追加本次新增片段，TextArea 自己负责滚动。
        """

        if not content and not reset:
            return
        streaming = self.query_one("#streaming", TextArea)
        streaming.display = True
        if reset:
            streaming.load_text("Agent:\n")
        if content:
            streaming.insert(content, streaming.document.end)
        streaming.scroll_end(animate=False)

    def _hide_streaming_answer(self) -> None:
        """Hide the temporary streaming panel after the answer is finalized."""

        streaming = self.query_one("#streaming", TextArea)
        streaming.load_text("")
        streaming.display = False

    def _refresh_meta(self) -> None:
        """Render compact session metadata outside the main chat area."""

        # 运行信息和快捷提示放在固定区域里，避免挤占主聊天区内容。
        runtime_context = Context()
        transcript_name = self.transcript_path.name
        model_name = runtime_context.model.split("/", maxsplit=1)[-1]
        self.query_one("#meta", Static).update(
            f"thread {self.thread_id[:8]}  |  log {transcript_name}"
        )
        self.query_one("#input-help", Static).update(
            f"profile {runtime_context.llm_profile}  |  model {model_name}  |  Enter 发送  |  Ctrl+L 清空 UI  |  Ctrl+C 退出"
        )

    def _render_chat(self) -> None:
        """渲染 UI 展示历史。

        这里读取的是 self.history。它不参与 Agent 推理，只负责 UI 显示。
        """
        chat = self.query_one("#chat", RichLog)
        chat.clear()
        if not self.history:
            chat.write(
                "[bold #9FE870]C28x Reverse Agent[/bold #9FE870]\n"
                "[#9aa7b4]在下方输入任务描述、函数名、入口点或路径，当前 thread 的短期记忆会持续保留在会话里。[/#9aa7b4]\n"
                "[#6f7c8a]支持从 MCP、ASM 文件或直接函数还原这几类流程开始。[/#6f7c8a]"
            )
            return

        for role, content in self.history:
            if role == "user":
                chat.write(f"\n[bold cyan]You:[/bold cyan] {content}", expand=True)
            elif role == "assistant":
                chat.write(f"[bold magenta]Agent:[/bold magenta]\n{content}", expand=True)
            elif role == "error":
                chat.write(f"[bold red]Error:[/bold red] {content}", expand=True)

    def _create_transcript_path(self) -> Path:
        """创建本次 UI 会话的 Markdown 聊天记录文件。"""
        logs_dir = Path("chat_logs")
        logs_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = logs_dir / f"{stamp}_{self.thread_id[:8]}.md"
        path.write_text(
            "# LangGraph Agent Chat\n\n"
            f"- Thread: `{self.thread_id}`\n"
            f"- Started: `{datetime.now().isoformat(timespec='seconds')}`\n\n",
            encoding="utf-8",
        )
        return path

    def _append_transcript(self, role: str, content: str) -> None:
        """向 transcript 文件追加一轮 UI 消息。"""
        with self.transcript_path.open("a", encoding="utf-8") as file:
            file.write(f"## {role}\n\n{content}\n\n")


def main() -> None:
    """Run the TUI app."""
    # --inline 用于在当前终端内运行，适合 PowerShell / VS Code 调试。
    # 不加 --inline 时，Textual 会使用 alternate screen 全屏终端界面。
    parser = argparse.ArgumentParser(description="Run the LangGraph Agent TUI.")
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Run inside the current terminal instead of the alternate screen.",
    )
    args = parser.parse_args()

    # 加载 .env。模型、API base URL、API key、Tavily key 等配置都从这里进入。
    load_dotenv(override=True)
    try:
        AgentTui().run(inline=args.inline, inline_no_clear=args.inline)
    except Exception:
        # 启动阶段或 UI 主循环异常时写入日志，避免 TUI 直接退出后看不到错误。
        Path("tui_app.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
