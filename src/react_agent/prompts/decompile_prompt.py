"""Prompt builder for the single-function decompilation node."""

from __future__ import annotations

from datetime import UTC, datetime

from react_agent.prompts.ida2c28x_rules import IDA2C28X_RULES
from react_agent.state import State


def _preview_items(items: list[str], limit: int = 5) -> list[str]:
    return [item for item in items[:limit]]


DECOMPILE_EQUIVALENCE_SUMMARY = """
生成结果时主动满足以下验收方向，但最终状态由 function_verify_node 判断：

1. 当前结果只针对 current_function。
2. 主要基本块、分支、循环、调用点、返回路径都有 C 对应。
3. volatile、外设写、EALLOW/EDIS、RPT || NOP、watchdog/key 序列等硬件副作用不能丢失。
4. 参数、返回值、全局变量、访问宽度和 signedness 必须有 IDA/汇编证据支持。
5. 证据不足时标记 partial 或 unresolved evidence gap，不要伪装为 verified。
6. 不要修改 function_queue、completed_functions 或 failed_functions。
"""


def format_decompile_state(state: State) -> str:

    return "\n".join(
        [
            f"task_mode: {state.task_mode}",
            f"source_mode: {state.source_mode}",
            f"current_function: {state.current_function}",
            f"function_queue_len: {len(state.function_queue)}",
            f"function_queue_preview: {_preview_items(state.function_queue)}",
            f"completed_functions_len: {len(state.completed_functions)}",
            f"completed_functions_preview: {_preview_items(state.completed_functions)}",
            f"failed_functions_len: {len(state.failed_functions)}",
            f"failed_functions_preview: {_preview_items(state.failed_functions)}",
            f"authorized_paths: {[item.model_dump() for item in state.authorized_paths]}",
            f"source_files: {[item.model_dump() for item in state.source_files]}",
            f"mcp_required: {state.mcp_required}",
            f"mcp_connect_status: {state.mcp_connect_status}",
            f"mcp_tool_names: {state.mcp_tool_names}",
        ]
    )


def build_decompile_system_prompt(
    state: State,
    base_system_prompt: str,
) -> str:

    return f"""
{base_system_prompt}

System time: {datetime.now(tz=UTC).isoformat()}

## 当前阶段

你现在处于单函数 C28x 还原阶段。

当前函数：

{state.current_function}

本轮只允许围绕 current_function 收集证据、调用 ida-pro-mcp/IDA 工具、分析汇编并生成当前函数的 C 还原结果。

## Workflow 边界

以下状态由 workflow 维护，不由你维护：

1. function_queue
2. completed_functions
3. failed_functions
4. current_function 的切换
5. 调用链入队策略
6. 任务终止条件

你不能修改、重排、清空或决定 function_queue。
你不能宣布某个函数已加入 completed_functions 或 failed_functions。
你不能开始还原 current_function 以外的函数。
是否扩展 function_queue 由 scan_callees_node 使用 ida-pro-mcp 的 get_callee_name 决定，不由你的文本输出决定。
不要把这些 workflow 边界当成终止整个调用链任务的理由。当前函数完成后，workflow 会自动决定是否继续和切换到哪个函数。

## ida-pro-mcp 工具调用策略

本阶段的工具调用默认用于访问 ida-pro-mcp，也就是向 IDA Pro 获取 current_function 的事实证据。

不要把工具调用理解为普通搜索或自由探索。除非当前任务明确需要文件写入，否则优先使用 ida-pro-mcp 取证。

必须遵守以下策略：

1. 如果没有 current_function 的函数信息、当前符号名或地址范围，优先查询函数信息。
2. 如果没有完整反汇编，必须优先获取 disasm 或等价汇编证据。
3. 如果没有调用点信息，应查询调用关系，或从反汇编中识别 LCR、FFC 等调用指令。
4. 如果参数、返回值、全局变量或外设访问不清楚，应继续查询交叉引用、数据引用、栈帧、反汇编或 IDA decompile 提示。
5. IDA decompile 只能作为参考，不能替代汇编证据。
6. 不要为同一问题无限重复调用相同工具；如果 ida-pro-mcp 无法提供更多信息，记录证据缺口并输出 partial。
7. 当证据已经足够生成当前函数结果时，停止 tool call，直接输出结果。
8. 不要为了推动后续调用链而在文本中维护 callee 队列；direct callee 只作为当前函数证据，真正的队列扩展由 scan_callees_node 处理。

## 内部分析策略

在发起 tool call 或输出当前函数结果前，请先在内部完成逐步分析，但不要输出完整思维链。

你需要在内部检查：

1. 是否已经拿到 current_function 的函数边界、地址范围和完整反汇编。
2. 是否识别了入口、出口、基本块、分支、循环、跳转目标和调用点。
3. 是否跟踪了 AL、AH、ACC、P、T、ARn、XARn、DP、SP、FPU 寄存器的关键数据流。
4. 是否区分了数据搬运、地址计算、算术、位操作、外设访问、调用和返回。
5. 参数、返回值、全局变量、signedness、float、指针、数组或结构体判断是否都有证据。
6. 是否存在 volatile、外设写、EALLOW/EDIS、RPT || NOP、watchdog/key 序列等不能重排的副作用。
7. 是否发现被调函数候选，并且没有展开还原它们。
8. 如果证据不足，下一步最小必要 ida-pro-mcp tool call 是什么。
9. 如果证据已经足够，如何用最少但可审计的映射说明支撑 C 结果。

不要输出完整思维链。最终输出只提供可审计摘要：使用的证据、关键汇编到 C 的映射、结论和未确认点。

## 当前 Workflow State

{format_decompile_state(state)}

## C28x 还原规则

{IDA2C28X_RULES}

## 验收方向摘要

{DECOMPILE_EQUIVALENCE_SUMMARY}

## 输出格式

如果还需要 ida-pro-mcp 工具，请发起 tool call，不要直接编造。

如果证据已经足够，请按以下结构输出当前函数结果：

Recovered C:
<当前函数 C 代码或 partial 草案>

Prototype Notes:
<参数、返回值、调用约定和证据>

Evidence Used:
<本轮实际使用的 ida-pro-mcp/IDA/asm 证据摘要；不要写未经工具确认的来源>

Assembly Mapping:
<关键基本块、寄存器、全局变量、外设访问、调用和返回到 C 语句的可审计映射；不是完整思维链>

Global/Peripheral Access:
<全局变量、Gvar 候选、外设或 volatile 访问>

Unresolved Evidence Gaps:
<未确认点；没有则写 none>

Status Suggestion:
<verified | partial | need_more_evidence | failed>

注意：Status Suggestion 只是建议，不是最终验收结果。
""".strip()
