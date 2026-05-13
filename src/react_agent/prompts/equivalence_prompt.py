"""Verify prompt — compare assembly against restored C code, fix, persist."""

from __future__ import annotations

from typing import Any

from react_agent.state import State


def _safe_value(state: State, name: str, default: Any = None) -> Any:
    return getattr(state, name, default)


def entry_function_name(state: State) -> str:
    for name in state.function_names:
        cleaned = name.strip()
        if cleaned:
            return cleaned
    current = (state.current_function or "").strip()
    return current or "recovered"


def build_verify_system_prompt(
    state: State,
    decompile_text: str,
    base_system_prompt: str,
) -> str:
    entry_function = entry_function_name(state)
    retry_count = _safe_value(state, 'verification_retry_count', 0)
    max_retries = _safe_value(state, 'verification_max_retries', 3)

    return f"""
{base_system_prompt}

## 当前任务

验证并落盘函数: {state.current_function}
调用链入口: {entry_function}
重试次数: {retry_count}/{max_retries}

## 第一步：语义等价验证

用 ida-pro-mcp 的 disasm 获取 {state.current_function} 的汇编（如果还原阶段的消息中已有则不必重复获取），与下方还原草稿进行**语义等价对比**。

验证的核心问题：C 代码在所有输入下是否与汇编产生**相同的副作用序列和相同的返回值**？

重点关注以下方面（按重要性排序）：

1. **缺失或多余逻辑**：是否有汇编指令无对应 C 代码，或 C 代码无对应汇编指令
2. **函数签名**：参数数量/类型是否匹配入口处使用的寄存器，返回类型是否匹配出口处赋值的寄存器
3. **控制流**：每条分支是否有对应 C 结构，分支方向是否正确
4. **外设与全局变量**：DP 寻址的目标是否映射到正确的符号和偏移
5. **函数调用**：LCR 目标和调用前的参数寄存器设置是否匹配

不需要逐条指令核对——关注语义等价，而非语法对应。编译器优化（指令重排、常量折叠、寄存器分配）会导致汇编形态与 C 源码差异很大，这是正常的。

## 第二步：修正

如果发现错误，直接修正 C 代码。修正必须基于汇编证据，不能凭猜测。

## 第三步：落盘

验证通过后，将代码写入已授权路径：

1. 函数代码写入 `source/{entry_function}_chain.c`（用 `/* BEGIN RECOVERED: func_name */` 和 `/* END RECOVERED: func_name */` 包裹）
2. 如函数可能被其他文件调用，在 `include/common.h` 补充 extern 声明
3. 新发现的全局变量补入 `include/gvar.h` 的 struct GVAR
4. 只读表数据放 `source/gconst.c`

文件不存在时先创建，已有函数块则原位替换。

已授权路径: {[item.model_dump() for item in state.authorized_paths]}

## 还原草稿

{decompile_text}

## 输出格式

完成验证、修正、落盘后输出（如果还需要 tool call 则继续调用，不输出报告）：

```
Function Finalization Report:
Status: <verified | partial | failed>
Function: {state.current_function}
Persisted Files:
- <已写入的文件路径; 失败则写 none>
Verification Summary:
<验证结果和修正说明>
Evidence Gaps:
- <缺口; 没有则写 none>
```
""".strip()
