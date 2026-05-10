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
from langgraph.types import Command
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static, TextArea

from react_agent import graph
from react_agent.context import Context
from react_agent.utils import get_message_text


def _assistant_chunk_text(chunk: Any, metadata: dict[str, Any] | None = None) -> str:
    """Return only assistant-facing text from streamed LangGraph events."""

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
    """Textual terminal UI for the local agent."""

    TITLE = "C28x Reverse Agent"
    SUB_TITLE = "Textual Console"

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
        color: $text;
    }

    Header {
        background: $panel;
        color: $text;
    }

    #status-row {
        height: 3;
        padding: 0 1;
        background: $panel;
        border-bottom: solid $primary;
        layout: horizontal;
    }

    #status {
        width: 1fr;
        content-align: left middle;
        color: $text;
    }

    #meta {
        width: 72;
        content-align: right middle;
        color: $text-muted;
    }

    #workspace {
        height: 1fr;
        layout: vertical;
        padding: 1;
        min-height: 0;
    }

    #conversation-panel {
        height: 1fr;
        border: round $primary;
        background: $boost;
        padding: 1;
        min-height: 12;
        overflow: hidden;
    }

    #chat {
        height: 1fr;
        border: none;
        background: $boost;
        color: $text;
        padding: 1;
    }

    #streaming {
        height: 9;
        min-height: 5;
        max-height: 12;
        border: round $secondary;
        background: $panel;
        color: $text;
        margin-top: 1;
        display: none;
    }

    #composer-panel {
        height: 5;
        min-height: 5;
        border: none;
        background: $boost;
        padding: 0;
        margin-top: 1;
        layout: vertical;
    }

    #input-panel {
        border: round $accent;
        background: $surface;
        padding: 0 1;
        height: 4;
        min-height: 4;
        content-align: center middle;
    }

    #prompt {
        width: 1fr;
        border: none;
        background: transparent;
        color: $text;
    }

    #input-help {
        height: 1;
        color: $text-muted;
        padding: 0 1;
        content-align: left middle;
    }

    Footer {
        background: $panel;
        color: $text;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_chat", "Clear UI"),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Use theme tokens so later palette tweaks do not require editing every
        # individual widget style again.
        self.theme = "textual-dark"
        self.history: list[tuple[str, str]] = []
        self.thread_id = uuid.uuid4().hex
        self._assistant_buffer = ""
        self._last_stream_render_at = 0.0
        self._last_stream_render_len = 0
        self.transcript_path = self._create_transcript_path()
        self.awaiting_resume = False
        self._status_text = "Ready"

    def compose(self) -> ComposeResult:
        """Declare the Textual layout."""

        yield Header(show_clock=True)
        with Horizontal(id="status-row"):
            yield Static("Ready", id="status")
            yield Static("", id="meta")
        with Vertical(id="workspace"):
            with Vertical(id="conversation-panel"):
                yield RichLog(id="chat", wrap=True, markup=True, highlight=True)
                yield TextArea(
                    "",
                    id="streaming",
                    read_only=True,
                    soft_wrap=True,
                    show_line_numbers=False,
                    show_cursor=False,
                )
            with Vertical(id="composer-panel"):
                with Horizontal(id="input-panel"):
                    yield Input(placeholder="输入问题并按 Enter", id="prompt")
                yield Static("", id="input-help")
        yield Footer()

    def on_mount(self) -> None:
        """Run after the UI is mounted."""

        self.query_one("#prompt", Input).focus()
        self.query_one("#conversation-panel", Vertical).border_title = " Conversation "
        self.query_one("#streaming", TextArea).border_title = " Live Output "
        self._set_status("Waiting")
        self._refresh_meta()
        self._render_chat()

    def _set_status(self, text: str) -> None:
        """Keep status formatting in one place."""

        self._status_text = text
        self.query_one("#status", Static).update(f"{text}; thread={self.thread_id[:8]}")

    def action_clear_chat(self) -> None:
        """Clear only the UI-visible chat, not LangGraph thread memory."""

        self.history.clear()
        self._assistant_buffer = ""
        self._last_stream_render_at = 0.0
        self._last_stream_render_len = 0
        self.query_one("#chat", RichLog).clear()
        self._hide_streaming_answer()
        self._render_chat()
        self._set_status("UI cleared; thread memory kept")
        self._refresh_meta()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input submission."""

        prompt = event.value.strip()
        if not prompt:
            return

        prompt_input = self.query_one("#prompt", Input)
        prompt_input.value = ""
        prompt_input.disabled = True

        self.history.append(("user", prompt))
        self._append_transcript("User", prompt)
        self._assistant_buffer = ""
        self._last_stream_render_at = 0.0
        self._last_stream_render_len = 0

        self._render_chat()
        self._show_streaming_answer("Thinking...")
        self._set_status("Thinking")
        self._refresh_meta()

        self.run_agent(prompt)

    @work(exclusive=True)
    async def run_agent(self, prompt: str) -> None:
        """Run the LangGraph agent and stream UI updates."""

        config = {"configurable": {"thread_id": self.thread_id}}

        try:
            graph_input = (
                Command(resume=prompt)
                if self.awaiting_resume
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
                        self._set_status("Waiting for user input")
                        self._refresh_meta()
                        return

                elif mode == "messages":
                    chunk = payload[0] if isinstance(payload, tuple) else payload
                    metadata = payload[1] if isinstance(payload, tuple) and len(payload) > 1 else None
                    text = _assistant_chunk_text(chunk, metadata)
                    if text:
                        self._assistant_buffer += text
                        self._render_streaming_answer()

            self.awaiting_resume = False
            self.history.append(("assistant", self._assistant_buffer))
            self._append_transcript("Agent", self._assistant_buffer)
            self._hide_streaming_answer()
            self._render_chat()
            self._set_status("Ready")
            self._refresh_meta()
        except Exception as exc:
            self.awaiting_resume = False
            error = _short_error_text(exc)
            self.history.append(("error", error))
            self._append_transcript("Error", f"{exc!r}")
            self._hide_streaming_answer()
            self._render_chat()
            self._set_status("Error")
            self._refresh_meta()
        finally:
            prompt_input = self.query_one("#prompt", Input)
            prompt_input.disabled = False
            prompt_input.focus()

    def _render_streaming_answer(self, *, force: bool = False) -> None:
        """Render the current streaming assistant response."""

        now = time.monotonic()
        new_chars = len(self._assistant_buffer) - self._last_stream_render_len

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
        """Append new stream text without rebuilding the whole TextArea."""

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
        """Render the UI-visible chat history."""

        chat = self.query_one("#chat", RichLog)
        chat.clear()
        if not self.history:
            chat.write(
                "[bold #9FE870]C28x Reverse Agent[/bold #9FE870]\n"
                "[#90a2b5]Start with a task description, function name, entry point, or path.[/#90a2b5]\n"
                "[#6f8297]Short-term memory stays in this thread while the app is open.[/#6f8297]"
            )
            return

        for role, content in self.history:
            if role == "user":
                chat.write(
                    f"\n[bold #66d6ff]You:[/bold #66d6ff] {content}",
                    expand=True,
                )
            elif role == "assistant":
                chat.write(
                    f"\n[bold #ff2b7a]Agent:[/bold #ff2b7a]\n{content}",
                    expand=True,
                )
            elif role == "error":
                chat.write(
                    f"\n[bold #ff6b6b]Error:[/bold #ff6b6b] {content}",
                    expand=True,
                )

    def _create_transcript_path(self) -> Path:
        """Create the markdown transcript file for this UI session."""

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
        """Append a UI message to the transcript file."""

        with self.transcript_path.open("a", encoding="utf-8") as file:
            file.write(f"## {role}\n\n{content}\n\n")


def main() -> None:
    """Run the TUI app."""

    parser = argparse.ArgumentParser(description="Run the LangGraph Agent TUI.")
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Run inside the current terminal instead of the alternate screen.",
    )
    args = parser.parse_args()

    load_dotenv(override=True)
    try:
        AgentTui().run(inline=args.inline, inline_no_clear=args.inline)
    except Exception:
        Path("tui_app.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
