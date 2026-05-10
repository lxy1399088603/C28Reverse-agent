核心结论：**不要让 `call_model_decompile + prompt` 成为函数队列的真正“拥有者”**。在你的场景里，ReAct 适合负责“单函数内的证据收集、推理、工具调用、自检”，但**不适合负责全局调度真相**。未完成队列、已完成列表、去重集合、函数状态迁移，应该由 **LangGraph 的显式状态机和确定性节点** 管，不应交给模型在自由文本里维护。

**为什么仅靠 `call_model_decompile` 不可靠**
你现在的入口是 [src/react_agent/nodes/model.py](/D:/workEnvironment/ai/Agent/C28Reverse-agent/src/react_agent/nodes/model.py)，它把 system prompt 和当前 `State` 拼起来，然后进入 ReAct 循环。这个模式有两个天然问题：

1. ReAct 擅长“局部决策”，不擅长“全局账本”  
ReAct 论文强调的是边想边做，用外部工具补证据，而不是维护一个严格的调度器。[ReAct 论文](https://arxiv.org/abs/2210.03629)

2. prompt 中的队列只是“被描述的状态”，不是“被约束的状态”  
模型可以读到 `function_queue`，但它并没有天然义务按 FIFO 正确迁移，也没有事务语义。一次错误 tool call、一次误判“函数已完成”、一次遗漏内部调用，都可能把队列带偏。

3. 你的任务是“图遍历 + 证据驱动恢复”，不是纯开放式 agent  
这类任务本质上更像 workflow/state machine。Anthropic 那篇很好的文章里也强调，很多成功 agent 实际上依赖的是可组合 workflow，而不是把所有控制权交给自主 agent。[Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

**结合你当前案例，最重要的架构原则**
你当前图在 [src/react_agent/graph.py](/D:/workEnvironment/ai/Agent/C28Reverse-agent/src/react_agent/graph.py) 已经有 state graph 雏形。下一步最关键的是把职责切开：

1. `LangGraph state` 负责全局真相  
包括：
- `pending_queue`
- `in_progress_function`
- `completed_functions`
- `failed_functions`
- `seen_functions`
- `function_status_map`

2. `call_model_decompile` 只负责单函数闭环  
包括：
- 获取当前函数证据
- 生成恢复结果
- 发现候选被调函数
- 输出结构化自检结果

3. 确定性节点负责状态迁移  
包括：
- 从队列弹出下一个函数
- 去重后把新发现函数追加到队尾
- 根据验收结果把当前函数标记为 `completed/partial/blocked/failed`
- 决定是否继续循环

**推荐的主循环形态**
不要是现在这种抽象的：

`call_model_decompile -> tools -> call_model_decompile`

而应该是更显式的调度循环：

1. `scheduler_pick_next`
从 `pending_queue` 头部取一个函数，放到 `current_function`

2. `collect_function_context`
确定性/半确定性地取反汇编、xrefs、caller/callee、globals

3. `recover_function`
这里才进入 ReAct，让模型在单函数上下文里工作

4. `validate_function_result`
把恢复结果做函数级验收，输出结构化 verdict

5. `extract_new_callees`
从证据或恢复结果里抽取内部调用函数

6. `merge_queue`
确定性地把新函数追加到 `pending_queue` 尾部，且只允许：
- 不在 `completed`
- 不在 `pending`
- 不在 `in_progress`
- 不在 `failed` 或按策略允许重试

7. `close_or_retry_function`
根据验证结果更新 `completed/partial/blocked`

8. `route_next`
队列空则结束，否则继续

这里最重要的是：**模型永远不直接改队列；模型只能提议，代码来提交。**

**最稳的控制方式：proposal-commit**
这是你现在最该引入的模式。

让模型输出结构化 proposal，例如：
- `current_function_result`
- `candidate_new_functions`
- `confidence`
- `unresolved_points`
- `suggested_status`

然后由确定性节点做 commit：
- 校验函数名是否合法
- 去重
- 按 FIFO 入队
- 更新完成表
- 拒绝非法迁移

也就是说：

- 模型有“建议权”
- 状态机有“记账权”

这会极大降低流程失真。

**具体到你的“FIFO 队列”担忧**
你的直觉是对的。FIFO 不是一个“提示词偏好”，而是一个**调度不变量**。  
所以必须把它做成代码规则，而不是 prompt 规则。

建议定义这些不变量：

1. `pending_queue` 只能由 `merge_queue` 节点修改
2. 取函数只能从头部弹出
3. 新发现函数只能追加到尾部
4. 任一函数同一时刻只能处于一种状态
5. `completed` 后不能重新入队，除非显式开启 `revisit`
6. `partial` 是否回队，必须由固定策略决定，不由模型临场决定

只要这几条在代码层成立，ReAct 就不会把全局节奏搞乱。

**ReAct 在你这个场景里该负责什么**
最适合让 ReAct 做的，是“受约束的局部研究员”，不是“项目经理”。

适合 ReAct 的：
- 当前函数还原
- 当前函数缺什么证据
- 要调用哪些工具
- 当前函数是否存在未确认点
- 哪些 callee 值得跟进

不适合 ReAct 的：
- 全局队列记账
- 全局完成状态管理
- 何时结束整个工程
- 是否改变遍历策略
- 是否跳过某些函数

**如何提高准确性**
不是一味增强 prompt，而是加“护栏”。

1. 结构化输出  
`recover_function` 不要只产出自然语言。至少产出：
- `recovered_summary`
- `candidate_callees`
- `used_globals`
- `status`
- `assumptions`
- `evidence_gaps`

2. 函数级验收节点  
单独做 `validate_function_result`，不要让模型自说自话“我已完成”。  
这个节点要检查：
- 基本块是否覆盖
- 返回路径是否齐
- 调用点是否解释
- 全局/volatile 写是否保留
- 参数和返回值是否有证据

3. 队列更新节点  
只吃结构化 `candidate_callees`，做规范化、去重、尾插。

4. 状态标签而不是布尔完成  
至少分：
- `completed`
- `partial`
- `blocked`
- `failed`
这样比“完成/未完成”更真实。

5. 回归集  
挑一批你熟悉的函数做 benchmark。每次改 skill、prompt、流程，都看：
- `completed` 数是否提升
- `partial` 是否减少
- 某类错误是否回归

**skill 在这里怎么配合**
`ida2C28x` 最适合作为 `recover_function` 和 `validate_function_result` 的规则来源，而不是整个图的调度器。

可以把它拆成两部分：

1. 恢复规则  
给 `recover_function` 用  
比如证据规则、命名规则、全局变量规则

2. 验收规则  
给 `validate_function_result` 用  
比如 equivalence checklist、函数闭环要求

换句话说，skill 不该负责“下一步处理哪个函数”，而该负责“当前函数怎么恢复、怎么算过关”。

**如果你要一个更开放架构的建议**
最稳的设计是“workflow 包住 agent”，不是“agent 包住 workflow”。

也就是：
- 外层是 LangGraph workflow / state machine
- 内层某些节点才是 ReAct agent

这和 LangGraph 官方一贯强调的 workflow/agent 组合思路一致：[LangGraph: Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)  
如果单函数恢复太复杂，还可以把它做成子图而不是单个 `call_model_decompile`。[LangGraph subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)

**面向你当前项目的最终建议**
我建议你把当前架构认知改成这句话：

> `call_model_decompile` 不是主循环控制器，它只是“当前函数恢复器”。

真正的主循环应该由图和状态字段驱动。  
如果要保证准确性，你至少要做到：

1. 队列和完成表只允许确定性节点修改
2. 模型只输出 proposal，不直接修改全局状态
3. FIFO 作为代码不变量实现
4. 单函数恢复和单函数验收拆开
5. skill 负责规则，不负责调度
6. 用 benchmark 函数集评估架构改动是否真的更好

如果你愿意，我下一步可以继续给你一版更具体的“节点级重构方案”，直接把你现在这张图改写成一张建议版流程图。  

**参考资料**
- [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [LangGraph: Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [LangGraph: Use subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)
- [OpenAI: Structured outputs](https://platform.openai.com/docs/guides/structured-outputs)