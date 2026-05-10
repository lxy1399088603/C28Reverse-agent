"""Function-level equivalence checks for C28x decompilation."""

EQUIVALENCE_RULES = """
函数级验收用于判断当前 C 还原结果是否可以进入落盘和调用扫描阶段。

这些规则主要给 function_verify_node 使用。call_model_decompile 可以参考这些规则生成更容易验收的结果，但不能自己宣布最终通过。

## 验收分级

1. verified：当前函数主要汇编逻辑、控制流、副作用、调用、返回路径都有对应 C 表达。
2. partial：主要逻辑已恢复，但仍存在明确证据缺口；可以落盘，但必须保留缺口记录。
3. need_more_evidence：缺少关键 MCP/IDA/asm 证据，需要回到 call_model_decompile 继续查证。
4. failed：多次查证或还原仍无法形成可用结果，记录失败原因后进入 failed_functions。

## 必须检查

1. 结果是否只针对 current_function。
2. 是否包含 current_function 的函数体或明确 partial 草案。
3. 是否覆盖主要基本块、分支、循环、跳转目标和出口路径。
4. 是否覆盖所有直接调用点，或明确说明无法解析的调用点。
5. 是否保留返回值、返回路径和调用约定相关寄存器语义。
6. 是否保留 volatile、外设写、EALLOW/EDIS、RPT || NOP、watchdog/key 序列等硬件副作用。
7. 是否处理全局变量访问、数组/结构体 stride、访问宽度和 signedness。
8. 是否把 IDA decompile 当作提示，而不是唯一依据。
9. 是否显式记录未确认点，且没有把推测写成事实。
10. 是否没有越权维护 function_queue、completed_functions 或 failed_functions。

## 需要更多证据的情况

1. 没有拿到 current_function 的完整反汇编或地址范围。
2. 控制流缺失基本块、跳转目标或出口路径。
3. 调用点存在，但被调目标无法解析。
4. 参数来源、返回值或关键寄存器语义不清楚。
5. 外设地址、全局变量宽度或 volatile 语义不清楚。
6. 生成结果明显依赖猜测，缺少工具证据。

## 可以 partial 的情况

1. 非关键符号语义不明，但控制流和副作用已覆盖。
2. 部分全局变量只能恢复为中性 Gvar 字段。
3. 变量名不优雅，但地址、宽度和访问方式已记录。
4. 少量间接调用无法解析，但已保留调用形态并记录缺口。

## 禁止通过的情况

1. 当前函数为空或没有 C 代码草案。
2. 还原了 current_function 以外的函数作为主体。
3. 删除或重排了硬件可见副作用。
4. 把无法证明的业务语义写成确定变量名或结构体字段。
5. 没有工具证据却声称已确认。
6. 直接修改队列或决定后续函数。

## 验收输出建议

function_verify_node 应输出结构化状态：

1. status：verified、partial、need_more_evidence 或 failed。
2. reason：当前判断原因。
3. evidence_gaps：仍缺少的关键证据。
4. retry_hint：如果需要更多证据，说明下一轮应查什么。
5. can_persist：verified 或 partial 时为 true。
"""
