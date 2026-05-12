"""Terminal chat UI v2 for the LangGraph ReAct agent.

A Claude-Code-/Codex-/Gemini-CLI inspired Textual frontend with:

  1.  per-message widgets (no full redraw on stream)
  2.  append-only streaming via RichLog + tail Static
  3.  frozen Markdown rendering on completion (syntax-highlighted code blocks)
  4.  multi-line composer (Enter = send, Shift+Enter / Ctrl+J = newline)
  5.  in-app slash commands  (/clear, /save, /theme, /profile, /thread, /help)
  6.  collapsible tool-call cards parsed from LangGraph updates
  7.  collapsible historical messages (any message > N lines auto-folds)
  8.  warm CLI-style palette + box-drawn composer
  9.  unified bottom status bar (path · profile · model · tokens · state)
 10.  Esc interrupts the running agent worker
 11.  transcript written on completion (no half-chunks on disk)
 12.  ↑/↓ recall prior submitted prompts

Run with:
    python appv2.py --inline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.types import Command
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import (
    Collapsible,
    Markdown,
    RichLog,
    Rule,
    Static,
    TextArea,
)

from react_agent import graph
from react_agent.context import Context
from react_agent.utils import get_message_text


# ─── palette (single source of truth) ──────────────────────────────────────

CL = {
    "bg":        "#0a0a0a",   # near black
    "bg_alt":    "#050505",   # deeper black for nested surfaces
    "rule":      "#1c1c1c",   # subtle separator
    "border":    "#2a2a2a",   # input box border (unfocused)
    "accent":    "#e8e8e8",   # primary text accent — soft white, not amber
    "user":      "#9ab4cf",   # cool slate-blue for user prompt symbol
    "agent":     "#e8e8e8",   # near-white agent text
    "agent_dim": "#a0a0a0",   # neutral mid-grey
    "muted":     "#5a5a5a",   # dim chrome (hints, dots, status muted)
    "success":   "#7ab57a",   # desaturated green — only for ✓ markers
    "warn":      "#c8c8c8",   # streaming indicator — soft white instead of yellow
    "error":     "#e06666",   # softened red — only for failures
    "highlight": "#bfbfbf",   # generic emphasis
    "kw":        "#c8c8c8",   # slash-command emphasis
}

# threshold above which a message body auto-wraps in a Collapsible
FOLD_AFTER_LINES = 8


# ─── helpers ───────────────────────────────────────────────────────────────


def _assistant_chunk_text(chunk: Any, metadata: dict[str, Any] | None = None) -> str:
    """Return only assistant-facing text from streamed LangGraph events."""
    visible_nodes = {"call_model_decompile", "function_verify_node"}
    if metadata and metadata.get("langgraph_node") not in visible_nodes:
        return ""
    if isinstance(chunk, (AIMessage, AIMessageChunk)):
        return get_message_text(chunk)
    return ""


def _short_error_text(error: Exception) -> str:
    text = str(error).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if any(marker in line for marker in
               ("Error:", "Exception:", "AttributeError:", "ValueError:")):
            return f"{error.__class__.__name__}: {line}"
    if lines:
        return f"{error.__class__.__name__}: {lines[-1]}"
    return error.__class__.__name__


def _format_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k tokens"
    return f"{n} tokens"


def _format_args_inline(args: Any, max_total: int = 80) -> str:
    if isinstance(args, dict):
        items = []
        for k, v in args.items():
            v_str = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            if len(v_str) > 30:
                v_str = v_str[:27] + "…"
            items.append(f"{k}: {v_str}")
        joined = ", ".join(items)
    else:
        joined = str(args)
    if len(joined) > max_total:
        joined = joined[: max_total - 1] + "…"
    return joined


# ─── messages (Textual Message subclasses for widget→app comms) ────────────


class ComposerSubmit(Message):
    """Composer wants to submit a user message."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ComposerInterrupt(Message):
    """User pressed Esc — interrupt any running agent worker."""


# ─── message widgets ───────────────────────────────────────────────────────


class _MessageRow(Widget):
    """Common base. One row in the conversation."""

    DEFAULT_CSS = """
    _MessageRow {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        layout: horizontal;
    }
    _MessageRow .prompt {
        width: 3;
        content-align: left top;
        padding: 0 1 0 0;
    }
    _MessageRow .body {
        width: 1fr;
        height: auto;
    }
    """


class UserMessage(_MessageRow):
    """Frozen user message. Auto-folds when long."""

    DEFAULT_CSS = """
    UserMessage .prompt { color: """ + CL["user"] + """; text-style: bold; }
    UserMessage .body   { color: """ + CL["agent"] + """; }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        yield Static(">", classes="prompt")
        line_count = self.text.count("\n") + 1
        if line_count > FOLD_AFTER_LINES:
            first = next((ln for ln in self.text.splitlines() if ln.strip()), "")
            if len(first) > 72:
                first = first[:69] + "…"
            title = f"{first}  [dim]· {line_count} lines · click to fold[/dim]"
            with Collapsible(title=title, collapsed=False, classes="body"):
                yield Static(self.text)
        else:
            yield Static(self.text, classes="body")


class AssistantMessage(_MessageRow):
    """Frozen assistant message rendered as Markdown."""

    DEFAULT_CSS = """
    AssistantMessage .prompt { color: """ + CL["accent"] + """; text-style: bold; }
    AssistantMessage .body   { color: """ + CL["agent"] + """; }
    AssistantMessage Markdown { background: transparent; padding: 0; margin: 0; }
    AssistantMessage Markdown > * { background: transparent; }
    """

    def __init__(self, text: str, *, foldable: bool = True) -> None:
        super().__init__()
        self.text = text or "_(empty)_"
        self.foldable = foldable

    def compose(self) -> ComposeResult:
        yield Static("⏺", classes="prompt")
        line_count = self.text.count("\n") + 1
        body = Markdown(self.text)
        if self.foldable and line_count > FOLD_AFTER_LINES:
            first = next((ln for ln in self.text.splitlines() if ln.strip()), "")
            if len(first) > 72:
                first = first[:69] + "…"
            title = f"{first}  [dim]· {line_count} lines · click to fold[/dim]"
            with Collapsible(title=title, collapsed=False, classes="body"):
                yield body
        else:
            with Vertical(classes="body"):
                yield body


class ErrorMessage(_MessageRow):
    """Inline error row."""

    DEFAULT_CSS = """
    ErrorMessage .prompt { color: """ + CL["error"] + """; text-style: bold; }
    ErrorMessage .body   { color: #d88a8a; }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        yield Static("✗", classes="prompt")
        yield Static(self.text, classes="body")


class ToolCallCard(_MessageRow):
    """Collapsible tool-call card (claude-code style: ● name(args)  → result)."""

    DEFAULT_CSS = """
    ToolCallCard .prompt { color: """ + CL["success"] + """; text-style: bold; }
    ToolCallCard .body   { color: """ + CL["agent_dim"] + """; }
    ToolCallCard Collapsible { background: transparent; padding: 0; margin: 0; border: none; }
    ToolCallCard Collapsible > Contents { padding: 0 0 0 2; background: transparent; }
    ToolCallCard CollapsibleTitle { background: transparent; padding: 0; }
    ToolCallCard .arg-block, ToolCallCard .out-block {
        background: """ + CL["bg_alt"] + """;
        color: #d0d0d0;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    def __init__(
        self,
        name: str,
        args: Any,
        *,
        output: str = "",
        status: str = "running",
        duration: float = 0.0,
    ) -> None:
        super().__init__()
        self.tool_name = name
        self.tool_args = args
        self.tool_output = output
        self.status = status
        self.duration = duration
        self._collapsible: Collapsible | None = None
        self._output_static: Static | None = None
        self._rule: Rule | None = None

    def _status_dot(self) -> str:
        return {
            "running": f"[{CL['warn']}]●[/{CL['warn']}]",
            "ok":      f"[{CL['success']}]●[/{CL['success']}]",
            "err":     f"[{CL['error']}]●[/{CL['error']}]",
        }.get(self.status, f"[{CL['muted']}]●[/{CL['muted']}]")

    def _tail_text(self) -> str:
        if self.status == "running":
            return f"[{CL['muted']}]running…[/{CL['muted']}]"
        size = len(self.tool_output)
        size_s = f"{size}B" if size < 1024 else f"{size / 1024:.1f}KB"
        mark = "✓" if self.status == "ok" else "✗"
        return f"[{CL['muted']}]→ {size_s} · {self.duration:.1f}s {mark}[/{CL['muted']}]"

    def _title(self) -> str:
        return (
            f"{self._status_dot()} "
            f"[{CL['agent_dim']}]{self.tool_name}[/{CL['agent_dim']}]"
            f"[{CL['muted']}]({_format_args_inline(self.tool_args)})[/{CL['muted']}]  "
            f"{self._tail_text()}"
        )

    def compose(self) -> ComposeResult:
        # we render our own prompt dot ourselves via the title; keep the
        # column blank to align with other message rows
        yield Static(" ", classes="prompt")
        self._collapsible = Collapsible(
            title=self._title(),
            collapsed=True,
            classes="body",
        )
        with self._collapsible:
            pretty = (
                json.dumps(self.tool_args, indent=2, ensure_ascii=False)
                if isinstance(self.tool_args, dict)
                else str(self.tool_args)
            )
            yield Static(
                f"[{CL['muted']}]args[/{CL['muted']}]\n{pretty}",
                classes="arg-block",
            )
            self._rule = Rule(line_style="dashed")
            self._rule.display = bool(self.tool_output)
            yield self._rule
            self._output_static = Static(
                self._format_output_block(),
                classes="out-block",
            )
            self._output_static.display = bool(self.tool_output)
            yield self._output_static

    def _format_output_block(self) -> str:
        if not self.tool_output:
            return ""
        body = self.tool_output
        # cap rendered output to avoid massive scrollback when the tool dumps a lot
        if len(body) > 4000:
            body = body[:4000] + f"\n…[+{len(self.tool_output) - 4000} chars truncated]"
        return f"[{CL['muted']}]output[/{CL['muted']}]\n{body}"

    def update_result(self, output: str, *, status: str = "ok", duration: float = 0.0) -> None:
        """Patch the title and output block once the tool returns."""
        self.tool_output = output
        self.status = status
        self.duration = duration
        if self._collapsible is not None:
            try:
                self._collapsible.title = self._title()
            except Exception:
                pass
            try:
                # also force a refresh in case `title` is not a reactive
                self._collapsible.refresh()
            except Exception:
                pass
        if self._output_static is not None:
            self._output_static.update(self._format_output_block())
            self._output_static.display = True
        if self._rule is not None:
            self._rule.display = True


class LiveAssistantMessage(_MessageRow):
    """Currently-streaming assistant message.

    Architecture (the whole reason this rewrite exists):
      • completed lines are appended into a RichLog — Rich never re-parses them
      • the in-progress (partial) last line lives in a Static `tail`
      • only `tail` is updated per token; everything older is frozen
      • on finalize, the whole buffer is handed off to a Markdown widget
    """

    DEFAULT_CSS = """
    LiveAssistantMessage .prompt { color: """ + CL["accent"] + """; text-style: bold; }
    LiveAssistantMessage .body   { width: 1fr; height: auto; }
    LiveAssistantMessage RichLog {
        width: 1fr;
        height: auto;
        max-height: 30;
        min-height: 0;
        background: transparent;
        color: """ + CL["agent"] + """;
        border: none;
        padding: 0;
        scrollbar-size: 0 0;
    }
    LiveAssistantMessage .tail {
        width: 1fr;
        height: auto;
        color: """ + CL["agent"] + """;
        padding: 0;
    }
    LiveAssistantMessage .live-tag {
        height: 1;
        color: """ + CL["warn"] + """;
        padding: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.buffer = ""
        self._line_buf = ""
        self._log: RichLog | None = None
        self._tail: Static | None = None
        self._tag: Static | None = None
        self._chunks_since_render = 0
        self._last_render = 0.0

    def compose(self) -> ComposeResult:
        yield Static("⏺", classes="prompt")
        with Vertical(classes="body"):
            self._tag = Static(f"[{CL['warn']}]● live[/{CL['warn']}]", classes="live-tag")
            yield self._tag
            self._log = RichLog(
                wrap=True,
                markup=False,
                highlight=False,
                auto_scroll=True,
            )
            yield self._log
            self._tail = Static("", classes="tail")
            yield self._tail

    def append(self, chunk: str) -> None:
        """Append a streaming chunk. O(1) per token regardless of history size."""
        if not chunk or self._log is None or self._tail is None:
            return
        self.buffer += chunk
        combined = self._line_buf + chunk
        if "\n" in combined:
            *full_lines, partial = combined.split("\n")
            for line in full_lines:
                self._log.write(line, expand=True)
            self._line_buf = partial
        else:
            self._line_buf = combined
        # light throttle on tail updates to keep terminal redraws cheap
        now = time.monotonic()
        self._chunks_since_render += 1
        if now - self._last_render >= 0.04 or self._chunks_since_render >= 4:
            self._tail.update(self._line_buf)
            self._last_render = now
            self._chunks_since_render = 0

    def flush_tail(self) -> None:
        """Force-render any pending tail content."""
        if self._tail is not None:
            self._tail.update(self._line_buf)

    def mark_done(self) -> None:
        """Drop the live tag (called right before being replaced by a Markdown widget)."""
        if self._tag is not None:
            self._tag.update("")


# ─── composer ──────────────────────────────────────────────────────────────


class ComposerTextArea(TextArea):
    """Multi-line input area:
      • Enter           → submit
      • Shift+Enter     → newline
      • Ctrl+J          → newline (fallback for terminals that swallow shift+enter)
      • Up / Down       → cycle through prior submitted prompts
      • Esc             → fire ComposerInterrupt
    """

    BINDINGS: ClassVar = [
        Binding("enter", "send", "Send", show=False, priority=True),
        Binding("shift+enter", "newline", "Newline", show=False, priority=True),
        Binding("ctrl+j", "newline", "Newline", show=False, priority=True),
        Binding("up", "history_prev", "History prev", show=False, priority=True),
        Binding("down", "history_next", "History next", show=False, priority=True),
        Binding("escape", "interrupt", "Interrupt", show=False, priority=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # NB: don't name this `history` — TextArea owns that attr for undo.
        self._submit_history: list[str] = []
        self._hist_idx: int = 0
        self._draft: str = ""

    # ── actions ──────────────────────────────────────────────────────

    def action_send(self) -> None:
        text = self.text.strip()
        if not text:
            return
        self._submit_history.append(text)
        self._hist_idx = len(self._submit_history)
        self._draft = ""
        self.text = ""
        self.post_message(ComposerSubmit(text))

    def action_newline(self) -> None:
        self.insert("\n")

    def action_history_prev(self) -> None:
        # only intercept ↑ when cursor is on the first line; otherwise normal nav
        row, _ = self.cursor_location
        if row != 0:
            self.action_cursor_up()
            return
        if not self._submit_history:
            return
        if self._hist_idx == len(self._submit_history):
            self._draft = self.text
        self._hist_idx = max(0, self._hist_idx - 1)
        self._load_history_entry(self._submit_history[self._hist_idx])

    def action_history_next(self) -> None:
        row, _ = self.cursor_location
        last_row = self.document.line_count - 1
        if row != last_row:
            self.action_cursor_down()
            return
        if not self._submit_history:
            return
        if self._hist_idx >= len(self._submit_history) - 1:
            self._hist_idx = len(self._submit_history)
            self._load_history_entry(self._draft)
            self._draft = ""
        else:
            self._hist_idx += 1
            self._load_history_entry(self._submit_history[self._hist_idx])

    def action_interrupt(self) -> None:
        self.post_message(ComposerInterrupt())

    # ── helpers ──────────────────────────────────────────────────────

    def _load_history_entry(self, text: str) -> None:
        self.text = text
        try:
            self.move_cursor(self.document.end)
        except Exception:
            pass


# ─── main app ──────────────────────────────────────────────────────────────


class AgentTuiV2(App[None]):
    """Claude-Code-CLI styled Textual frontend for the LangGraph agent."""

    TITLE = "C28x Reverse Agent"
    SUB_TITLE = "v2"

    # CSS lives inline so this stays a single-file launcher; palette is in CL.
    CSS = f"""
    Screen {{
        background: {CL["bg"]};
        color: {CL["agent"]};
        layout: vertical;
    }}

    /* No default header — we render our own one-line banner. */
    Header {{ display: none; }}
    Footer {{ display: none; }}

    #topbar {{
        height: 1;
        padding: 0 2;
        background: {CL["bg"]};
        color: {CL["muted"]};
    }}

    #toprule, #statusrule {{
        height: 1;
        color: {CL["rule"]};
        background: {CL["bg"]};
        content-align: left middle;
    }}

    #conversation {{
        height: 1fr;
        padding: 1 2;
        background: {CL["bg"]};
        scrollbar-color: {CL["border"]} {CL["bg"]};
        scrollbar-background: {CL["bg"]};
        scrollbar-size: 1 1;
    }}

    #composer-wrap {{
        height: auto;
        padding: 0 2;
        background: {CL["bg"]};
    }}
    #composer {{
        height: auto;
        min-height: 3;
        max-height: 10;
        border: round {CL["border"]};
        background: {CL["bg"]};
        color: {CL["agent"]};
        padding: 0 1;
        scrollbar-size: 0 0;
    }}
    #composer:focus {{
        border: round {CL["accent"]};
    }}

    #hint {{
        height: 1;
        padding: 0 3;
        background: {CL["bg"]};
        color: {CL["muted"]};
    }}

    #statusbar {{
        height: 1;
        padding: 0 2;
        background: {CL["bg"]};
        color: {CL["muted"]};
        layout: horizontal;
    }}
    #statusbar Static {{
        height: 1;
        width: auto;
        margin-right: 1;
        color: {CL["agent_dim"]};
    }}
    #statusbar .dim {{ color: {CL["muted"]}; }}
    #statusbar .right {{ dock: right; color: {CL["muted"]}; }}

    /* tame Collapsible visual weight everywhere */
    Collapsible {{
        background: transparent;
        border: none;
        padding: 0;
        margin: 0;
    }}
    Collapsible > Contents {{
        padding: 0 0 0 0;
        background: transparent;
    }}
    CollapsibleTitle {{
        background: transparent;
        color: {CL["agent_dim"]};
        padding: 0;
    }}
    CollapsibleTitle:hover {{ color: {CL["accent"]}; }}
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("ctrl+l", "clear_ui", "Clear UI", show=False),
        Binding("ctrl+r", "retry_last", "Retry", show=False),
        Binding("ctrl+e", "export_md", "Export", show=False),
        Binding("ctrl+t", "cycle_theme", "Theme", show=False),
    ]

    THEMES: ClassVar = (
        "textual-dark",
        "monokai",
        "dracula",
        "nord",
        "gruvbox",
        "textual-light",
    )

    # ── lifecycle ────────────────────────────────────────────────────

    def __init__(self) -> None:
        super().__init__()
        self.theme = "textual-dark"
        self.thread_id = uuid.uuid4().hex
        self.transcript_path = self._create_transcript_path()
        self.awaiting_resume = False
        self.token_count = 0
        self.streaming_active = False
        self._theme_idx = 0
        self._last_user_prompt = ""
        self._pending_tool_calls: dict[str, ToolCallCard] = {}
        self._tool_start_at: dict[str, float] = {}

    def compose(self) -> ComposeResult:
        yield Static("", id="topbar")
        yield Static("─" * 400, id="toprule")
        yield VerticalScroll(id="conversation")
        with Vertical(id="composer-wrap"):
            yield ComposerTextArea(id="composer")
        yield Static("", id="hint")
        yield Static("─" * 400, id="statusrule")
        with Horizontal(id="statusbar"):
            yield Static("", id="s-path")
            yield Static("·", classes="dim")
            yield Static("", id="s-profile")
            yield Static("·", classes="dim")
            yield Static("", id="s-model")
            yield Static("·", classes="dim")
            yield Static("", id="s-tokens")
            yield Static("·", classes="dim")
            yield Static("", id="s-state")
            yield Static(
                "^C exit  ^L clear  ^R retry  ^E export  ^T theme",
                classes="right",
            )

    def on_mount(self) -> None:
        self.query_one("#composer", ComposerTextArea).focus()
        self._refresh_top()
        self._refresh_hint()
        self._refresh_status()
        self._render_welcome()

    # ── chrome updates ───────────────────────────────────────────────

    def _refresh_top(self) -> None:
        ctx = Context()
        model = ctx.model.split("/", maxsplit=1)[-1]
        self.query_one("#topbar", Static).update(
            f"[bold {CL['accent']}]✻[/bold {CL['accent']}] "
            f"[{CL['agent']}]c28x reverse agent[/{CL['agent']}] "
            f"[{CL['muted']}]· {model} · {ctx.llm_profile} profile · "
            f"thread {self.thread_id[:8]}[/{CL['muted']}]"
        )

    def _refresh_hint(self) -> None:
        self.query_one("#hint", Static).update(
            f"[{CL['muted']}]⏎ send · ⇧⏎ newline · ↑↓ history · "
            f"/ commands · esc interrupt · tab fold/unfold[/{CL['muted']}]"
        )

    def _refresh_status(self) -> None:
        ctx = Context()
        model = ctx.model.split("/", maxsplit=1)[-1]
        cwd = Path.cwd().name or str(Path.cwd())
        self.query_one("#s-path", Static).update(f"~/{cwd}")
        self.query_one("#s-profile", Static).update(ctx.llm_profile)
        self.query_one("#s-model", Static).update(model)
        self.query_one("#s-tokens", Static).update(_format_tokens(self.token_count))
        if self.awaiting_resume:
            state = f"[{CL['user']}]● waiting[/{CL['user']}]"
        elif self.streaming_active:
            state = f"[{CL['warn']}]● streaming[/{CL['warn']}]"
        else:
            state = f"[{CL['muted']}]● idle[/{CL['muted']}]"
        self.query_one("#s-state", Static).update(state)

    # ── welcome ──────────────────────────────────────────────────────

    def _render_welcome(self) -> None:
        conv = self.query_one("#conversation", VerticalScroll)
        conv.mount(
            Static(
                f"[{CL['agent_dim']}]welcome to[/{CL['agent_dim']}] "
                f"[bold {CL['accent']}]c28x reverse agent[/bold {CL['accent']}]\n"
                f"[{CL['muted']}]start with a task, function name, entry point, or path.[/{CL['muted']}]\n"
                f"[{CL['muted']}]type[/{CL['muted']}] "
                f"[{CL['kw']}]/help[/{CL['kw']}] "
                f"[{CL['muted']}]for slash commands.[/{CL['muted']}]\n",
            )
        )

    # ── composer message handlers ────────────────────────────────────

    def on_composer_submit(self, event: ComposerSubmit) -> None:
        text = event.text
        if text.startswith("/"):
            self._handle_slash(text)
        else:
            self._submit_user_text(text)

    def on_composer_interrupt(self, _event: ComposerInterrupt) -> None:
        if not self.streaming_active:
            return
        # cancel any currently-running worker registered on this app
        for worker in list(self.workers):
            try:
                worker.cancel()
            except Exception:
                pass
        self._add_widget(ErrorMessage("interrupted by user (Esc)"))
        self.streaming_active = False
        self._refresh_status()

    # ── conversation mutation ────────────────────────────────────────

    def _add_widget(self, widget: Widget) -> None:
        conv = self.query_one("#conversation", VerticalScroll)
        conv.mount(widget)
        conv.scroll_end(animate=False)

    def _submit_user_text(self, text: str) -> None:
        self._last_user_prompt = text
        self._add_widget(UserMessage(text))
        self._append_transcript("User", text)
        self.streaming_active = True
        self._refresh_status()
        self.run_agent(text)

    # ── slash commands ───────────────────────────────────────────────

    def _handle_slash(self, line: str) -> None:
        parts = line[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("help", "?", ""):
            self._add_widget(AssistantMessage(
                "**slash commands**\n\n"
                "| command | effect |\n"
                "|---|---|\n"
                "| `/clear` | clear the UI (thread memory kept) |\n"
                "| `/save` | show current transcript path |\n"
                "| `/theme [name]` | cycle themes, or set explicitly |\n"
                "| `/profile` | show LLM profile |\n"
                "| `/thread new` | start a new agent thread |\n"
                "| `/help` | this message |\n",
                foldable=False,
            ))
        elif cmd == "clear":
            self.action_clear_ui()
        elif cmd == "save":
            self._add_widget(AssistantMessage(
                f"transcript: `{self.transcript_path}`",
                foldable=False,
            ))
        elif cmd == "theme":
            if arg:
                try:
                    self.theme = arg
                    self._add_widget(AssistantMessage(
                        f"theme: `{arg}`", foldable=False
                    ))
                except Exception as exc:
                    self._add_widget(ErrorMessage(f"unknown theme: {arg} ({exc})"))
            else:
                self.action_cycle_theme()
        elif cmd == "thread":
            if arg == "new":
                self.thread_id = uuid.uuid4().hex
                self.transcript_path = self._create_transcript_path()
                self.awaiting_resume = False
                self.token_count = 0
                self.action_clear_ui()
                self._add_widget(AssistantMessage(
                    f"new thread: `{self.thread_id[:8]}`", foldable=False
                ))
            else:
                self._add_widget(ErrorMessage("usage: /thread new"))
        elif cmd == "profile":
            self._add_widget(AssistantMessage(
                f"current profile: `{Context().llm_profile}`  \n"
                "_(switching profiles at runtime requires editing `Context()` defaults)_",
                foldable=False,
            ))
        else:
            self._add_widget(ErrorMessage(f"unknown command: /{cmd}  (try /help)"))
        self._refresh_top()
        self._refresh_status()

    # ── bindings ─────────────────────────────────────────────────────

    def action_clear_ui(self) -> None:
        conv = self.query_one("#conversation", VerticalScroll)
        for child in list(conv.children):
            child.remove()
        self.streaming_active = False
        self._pending_tool_calls.clear()
        self._tool_start_at.clear()
        self._render_welcome()
        self._refresh_status()

    def action_retry_last(self) -> None:
        if not self._last_user_prompt:
            self._add_widget(ErrorMessage("nothing to retry"))
            return
        self._submit_user_text(self._last_user_prompt)

    def action_export_md(self) -> None:
        self._add_widget(AssistantMessage(
            f"transcript at `{self.transcript_path}`", foldable=False
        ))

    def action_cycle_theme(self) -> None:
        self._theme_idx = (self._theme_idx + 1) % len(self.THEMES)
        name = self.THEMES[self._theme_idx]
        try:
            self.theme = name
            self._add_widget(AssistantMessage(f"theme: `{name}`", foldable=False))
        except Exception as exc:
            self._add_widget(ErrorMessage(f"theme {name!r} failed: {exc}"))

    # ── agent worker ─────────────────────────────────────────────────

    @work(exclusive=True)
    async def run_agent(self, prompt: str) -> None:
        config = {"configurable": {"thread_id": self.thread_id}}
        live: LiveAssistantMessage | None = None
        completion_text = ""

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
                    if isinstance(payload, dict) and "__interrupt__" in payload:
                        interrupt_value = payload["__interrupt__"][0].value
                        question = (
                            interrupt_value.get("question", str(interrupt_value))
                            if isinstance(interrupt_value, dict)
                            else str(interrupt_value)
                        )
                        self.awaiting_resume = True
                        if live is not None:
                            self._finalize_live(live, completion_text or question)
                            live = None
                            completion_text = ""
                        else:
                            self._add_widget(AssistantMessage(question))
                        self._append_transcript("Agent", question)
                        self.streaming_active = False
                        self._refresh_status()
                        return
                    self._scan_updates_for_tool_calls(payload)

                elif mode == "messages":
                    chunk = payload[0] if isinstance(payload, tuple) else payload
                    metadata = payload[1] if isinstance(payload, tuple) and len(payload) > 1 else None

                    # token bookkeeping if the chunk carries usage_metadata
                    usage = getattr(chunk, "usage_metadata", None)
                    if usage:
                        total = usage.get("total_tokens") or 0
                        if total:
                            self.token_count = max(self.token_count, int(total))
                            self._refresh_status()

                    # tool returns close out a pending tool-call card
                    if isinstance(chunk, ToolMessage):
                        # if a live message was in-flight, finalize it first so
                        # that tool output and prose stay in chronological order
                        if live is not None:
                            self._finalize_live(live, completion_text)
                            live = None
                            completion_text = ""
                        self._finalize_tool_call(chunk)
                        continue

                    text = _assistant_chunk_text(chunk, metadata)
                    if text:
                        if live is None:
                            live = LiveAssistantMessage()
                            self._add_widget(live)
                        live.append(text)
                        completion_text += text

            # graph completed
            self.awaiting_resume = False
            if live is not None:
                self._finalize_live(live, completion_text)
            self.streaming_active = False
            self._refresh_status()

        except asyncio.CancelledError:
            if live is not None:
                self._finalize_live(
                    live,
                    (completion_text or "") + "\n\n*[interrupted]*",
                )
            self.streaming_active = False
            self._refresh_status()
            raise
        except Exception as exc:
            self.awaiting_resume = False
            if live is not None:
                self._finalize_live(live, completion_text)
            self._add_widget(ErrorMessage(_short_error_text(exc)))
            self._append_transcript("Error", f"{exc!r}")
            self.streaming_active = False
            self._refresh_status()
        finally:
            try:
                self.query_one("#composer", ComposerTextArea).focus()
            except Exception:
                pass

    # ── streaming finalize ───────────────────────────────────────────

    def _finalize_live(self, live: LiveAssistantMessage, text: str) -> None:
        """Replace the live RichLog widget with a frozen Markdown one.

        This is the single point where streaming hands off to the
        markdown renderer. Doing it once (not per token) keeps cost linear.
        """
        live.flush_tail()
        live.mark_done()
        final = AssistantMessage(text or "_(no content)_")
        try:
            live.remove()
        except Exception:
            pass
        self._add_widget(final)
        if text:
            self._append_transcript("Agent", text)

    # ── tool call tracking ───────────────────────────────────────────

    def _scan_updates_for_tool_calls(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        for _node, node_data in payload.items():
            if not isinstance(node_data, dict):
                continue
            messages = node_data.get("messages")
            if not isinstance(messages, list):
                continue
            for msg in messages:
                if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                    for tc in msg.tool_calls:
                        tc_id = tc.get("id") or tc.get("name") or ""
                        if not tc_id or tc_id in self._pending_tool_calls:
                            continue
                        card = ToolCallCard(
                            name=tc.get("name", "tool"),
                            args=tc.get("args", {}),
                            status="running",
                        )
                        self._pending_tool_calls[tc_id] = card
                        self._tool_start_at[tc_id] = time.monotonic()
                        self._add_widget(card)

    def _finalize_tool_call(self, msg: ToolMessage) -> None:
        tc_id = getattr(msg, "tool_call_id", "") or ""
        card = self._pending_tool_calls.pop(tc_id, None)
        if card is None:
            return
        started = self._tool_start_at.pop(tc_id, time.monotonic())
        duration = max(0.0, time.monotonic() - started)
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        status = "err" if getattr(msg, "status", "") == "error" else "ok"
        card.update_result(content, status=status, duration=duration)

    # ── transcript ───────────────────────────────────────────────────

    def _create_transcript_path(self) -> Path:
        logs_dir = Path("chat_logs")
        logs_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = logs_dir / f"{stamp}_{self.thread_id[:8]}.md"
        path.write_text(
            "# LangGraph Agent Chat (v2)\n\n"
            f"- Thread: `{self.thread_id}`\n"
            f"- Started: `{datetime.now().isoformat(timespec='seconds')}`\n\n",
            encoding="utf-8",
        )
        return path

    def _append_transcript(self, role: str, content: str) -> None:
        if not content:
            return
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(f"## {role}\n\n{content}\n\n")


# ─── entrypoint ────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LangGraph Agent TUI v2.")
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Run inside the current terminal instead of the alternate screen.",
    )
    args = parser.parse_args()
    load_dotenv(override=True)
    try:
        AgentTuiV2().run(inline=args.inline, inline_no_clear=args.inline)
    except Exception:
        Path("appv2.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
