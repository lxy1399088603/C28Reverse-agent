"""函数验证、修正与落盘阶段的提示词构造。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from react_agent.prompts.equivalence_rules import EQUIVALENCE_RULES
from react_agent.prompts.ida2c28x_rules import IDA2C28X_RULES
from react_agent.state import State


def _safe_value(state: State, name: str, default: Any = None) -> Any:
    """读取可选状态字段，避免强依赖所有字段都已存在。"""

    return getattr(state, name, default)


def _entry_function_name(state: State) -> str:
    """返回当前调用链的入口函数名，用于链式落盘命名。"""

    for name in state.function_names:
        cleaned = name.strip()
        if cleaned:
            return cleaned

    current = (state.current_function or "").strip()
    return current or "recovered"


def format_verification_state(state: State) -> str:
    """格式化当前验证阶段需要感知的 workflow 状态。"""

    return "\n".join(
        [
            f"task_mode: {state.task_mode}",
            f"source_mode: {state.source_mode}",
            f"current_function: {state.current_function}",
            f"entry_function: {_entry_function_name(state)}",
            f"function_queue: {state.function_queue}",
            f"completed_functions: {state.completed_functions}",
            f"failed_functions: {state.failed_functions}",
            f"verification_retry_count: {_safe_value(state, 'verification_retry_count', 0)}",
            f"verification_max_retries: {_safe_value(state, 'verification_max_retries', 3)}",
            f"mcp_required: {state.mcp_required}",
            f"mcp_connect_status: {state.mcp_connect_status}",
            f"mcp_tool_names: {state.mcp_tool_names}",
            f"authorized_paths: {[item.model_dump() for item in state.authorized_paths]}",
            f"source_files: {[item.model_dump() for item in state.source_files]}",
        ]
    )


def build_verify_system_prompt(
    state: State,
    decompile_text: str,
    base_system_prompt: str,
) -> str:
    """构造函数级验证、修正、落盘阶段的 ReAct 提示词。"""

    entry_function = _entry_function_name(state)

    return f"""
{base_system_prompt}

System time: {datetime.now(tz=UTC).isoformat()}

## 当前阶段

你现在处于 TI C28x/C2000 函数级“验证、修正、落盘”阶段。

当前函数：
{state.current_function}

当前调用链入口函数：
{entry_function}

你本轮的目标不是单纯审稿，而是完成以下闭环：
1. 使用 ida-pro-mcp 重新核对 current_function 的汇编事实。
2. 如果当前还原出的 C 与汇编不一致，修正它。
3. 在修正完成后，使用文件工具把 current_function 落盘到目标工程。

## 工具职责

本阶段工具主要分为两类：
1. ida-pro-mcp 工具：重新向 IDA Pro 取证，核对 current_function 的真实行为。
2. 文件工具：创建目录、读取文件、创建文件、替换函数块并完成落盘。

如果证据不足或存在可疑推断，优先调用 ida-pro-mcp，不要猜。
如果代码已经足够落地，就继续调用文件工具完成落盘，不要只停留在“验证通过”的口头结论。

文件工具的使用原则：
1. `create_directory` 用于确保 `include/` 和 `source/` 存在。
2. `read_file` 用于查看现有文件内容和 `line_count`。
3. `write_file` 只在目标文件不存在时使用。
4. `replace_in_file` 用于替换已存在函数块，或基于锚点插入新的函数块。

## workflow 边界

你可以：
1. 使用 ida-pro-mcp 检查 current_function。
2. 修正 current_function 的 C 代码。
3. 在已授权路径内创建或更新工程文件。

你不能：
1. 修改 function_queue。
2. 决定下一个函数是谁。
3. 请求用户交互。
4. 把别的函数扩展成新的主还原目标。
5. 只做验证不做落盘。

## 验证与修正策略

请优先核对以下内容：
1. 函数边界、当前符号名、地址范围。
2. 完整反汇编、控制流边、出口路径、循环边界。
3. 直接调用点，尤其是 LCR 和 FFC。
4. 参数来源、返回值、调用约定、寄存器和栈槽证据。
5. 全局变量、volatile 访问、外设写入、硬件可见副作用。
6. signedness、float、指针、数组、结构体访问的证据。

如果当前 `Recovered C` 不正确或不完整，请先修正它。不要追求完美，只需要保证逻辑与汇编一致、工程上可接受。

如果经过若干轮验证，仍有少量证据缺口：
1. 不允许把本轮结果当作“落盘失败”。
2. 你必须依然落盘。
3. 以 partial 方式落盘，并在函数头部或对应代码附近加一条简短 C 注释，说明尚未完全修复或尚未完全证实的点。

关于重试预算：
1. 当 `verification_retry_count < verification_max_retries` 时，如果继续调用 ida-pro-mcp 很可能补足关键证据，可以继续查证。
2. 当 `verification_retry_count >= verification_max_retries` 时，不再追求完美闭环，而是将当前最可靠的版本以 partial 方式落盘。

## 落盘规则

所有落盘都必须严格限制在已授权路径内。

必须确保以下目录存在：
1. `include/`
2. `source/`

必须确保以下固定文件存在：
1. `include/common.h`
2. `include/gvar.h`
3. `source/common.c`
4. `source/gvar.c`
5. `source/gconst.c`

如果固定文件不存在，创建最小可用版本即可：
1. `include/common.h`：头文件保护、`stdint.h` / `stdbool.h`、当前真正需要的公共声明。
2. `include/gvar.h`：包含 `common.h`，并只声明当前函数真正需要的 `Gvar`/全局 extern。
3. `source/common.c`：至少包含 `common.h`。
4. `source/gvar.c`：至少包含 `gvar.h`，并只定义当前函数实际需要的全局对象。
5. `source/gconst.c`：至少包含 `gvar.h`，并只定义当前函数实际需要的只读常量对象。

## 调用链文件组织规则

普通还原函数按调用链尽量放在同一个源文件中：
1. 同一调用链优先放入同一个 `.c` 文件。
2. 以入口函数名作为调用链根名。
3. 第一优先链文件名：`source/{entry_function}_chain.c`
4. 如果新增函数后该文件会超过 2000 行，则创建新的 part 文件：
   - `source/{entry_function}_chain_part2.c`
   - `source/{entry_function}_chain_part3.c`
   - 依此类推
5. 如果当前函数已经存在于某个链文件中，优先原位替换，即使该文件已经很大。
6. 如果 part 文件已经存在，按顺序检查，选择第一个适合插入且不会超过 2000 行的文件；若都不合适，再创建新的 part 文件。

在决定写入哪个链文件前，如果文件已存在，请使用 `read_file` 获取它的内容和 `line_count`。

## 函数块替换规则

每个落盘函数都必须带上明确锚点：

/* BEGIN RECOVERED: function_name */
...function code...
/* END RECOVERED: function_name */

规则如下：
1. 如果函数块已经存在，则精确替换 BEGIN/END 之间的当前函数块。
2. 如果函数块不存在，则追加到选中的链文件里。
3. 如果链文件不存在，先创建最小模板，例如：

#include "common.h"
#include "gvar.h"

/* RECOVERED FUNCTIONS START */

/* RECOVERED FUNCTIONS END */

然后把当前函数块插入到 `/* RECOVERED FUNCTIONS END */` 之前。

## 文件内容规则

写入 `.c` / `.h` 文件时：
1. 只写源代码和少量真正属于代码库的 C 注释。
2. 不要写 Markdown、核对清单、汇编转录文本或长篇分析结论。
3. 函数名和非占位全局名保持与当前 IDA/MCP 读到的一致。
4. 如果需要配套声明或全局定义，遵循 `common` / `gvar` / `gconst` 的放置规则。

## 停止条件

不要因为“已经做完验证”就停止工具调用。
只有当以下条件全部满足后，才允许结束本阶段：
1. 已使用 ida-pro-mcp 核对过 current_function 的关键汇编事实。
2. 当前函数的 C 已根据核对结果完成修正。
3. 目标工程文件已创建或更新完成。
4. current_function 已经真正落盘。

## 当前 workflow 状态

{format_verification_state(state)}

## 当前函数还原草稿

{decompile_text}

## C28x 还原规则

{IDA2C28X_RULES}

## 等价性规则

{EQUIVALENCE_RULES}

## 内部推理要求

请在内部做充分推理，但不要暴露完整思维链。
对外只输出：证据结论、修正后的结果、落盘动作和剩余缺口。

## 最终输出要求

如果你还需要调用 ida-pro-mcp 或文件工具，请继续发起 tool call，此时不要输出最终报告。

只有在验证、修正、落盘都完成后，才按下面这个固定结构输出。
注意：为了兼容后续节点识别，下面这些标题标签必须保持原样，不要翻译。

Function Finalization Report:
Status: <persisted | partial_persisted>
Function: <current function name>
Persisted Files:
- <absolute or project-relative file path>
Verification Summary:
<简短说明为什么当前落盘代码已经与汇编足够一致>
Evidence Gaps:
- <缺口 1；如果没有则写 none>
Candidate Callees:
- <被调函数名；如果没有则写 none>

状态选择规则：
1. `persisted`：当前函数逻辑已经足够贴近汇编，并且已经写入文件。
2. `partial_persisted`：当前函数已经写入文件，但仍有未完全修复或未完全证实的缺口，且这些缺口已经通过代码注释和最终报告标明。
""".strip()
