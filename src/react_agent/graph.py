"""Define a custom Reasoning and Action agent.

Works with a chat model with tool calling support.
"""

from utils import scan_pah, validata_path
from datetime import UTC, datetime
from typing import Dict, List, Literal, cast

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from react_agent.context import Context
from react_agent.state import InputState, State
from react_agent.tools import TOOLS
from react_agent.utils import load_chat_model


def validate_authorized_path_node(state: State) -> dict:
    if state.authorized_path:
        return {
            "needs_user_input": False,
            "blocking_reason": None
        }
    last_user_text = ""
    for message in reversed(state.messages):
        if getattr(message, "type", None) == "human":
            last_user_text = str(message.content)
            break
    lock_path_tmp = scan_pah(last_user_text)
    if lock_path_tmp is None:
        return {
            "needs_user_input": True,
            "blocking_reason": "未提供可操作工作路径。",
            "messages": [
                AIMessage(
                    content=(
                        "未检测到可操作工作路径。请提供一个已存在的目录，例如：\n"
                        "`工作路径: D:\\workEnvironment\\reverse\\project1`"
                    )
                )
            ],
        }
    
    lock_path = validata_path(lock_path_tmp)
    if lock_path is None:
        return {
            "needs_user_input": True,
            "blocking_reason": f"路径不存在或不是目录: {lock_path_tmp}",
            "messages": [
                AIMessage(
                    content=(
                        f"路径不存在或不是目录：`{lock_path_tmp}`。\n"
                        "请提供一个已存在的工作目录。"
                    )
                )
            ],
        }

    return {
        "authorized_path": lock_path,
        "needs_user_input": False,
        "blocking_reason": None,
    }
    


# 调用模型
async def call_model(
    state: State, runtime: Runtime[Context]
) -> Dict[str, List[AIMessage]]:
    """调用LLM.

    该函数负责准备提示词、初始化模型并处理响应

    Args:
        state (State): 对话状态，短期记忆.
        config (RunnableConfig): 模型运行配置.

    Returns:
        dict: 包含模型响应消息的字典.
    """
    # 初始化模型并绑定工具.
    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
        ).bind_tools(TOOLS)

    # 格式化系统提示词，自定义调整Agent的行为
    system_message = runtime.context.system_prompt.format(
        system_time=datetime.now(tz=UTC).isoformat()
    )

    # 获取模型的回答
    response = cast( # type: ignore[redundant-cast]
        AIMessage,
        await model.ainvoke(
            [{"role": "system", "content": system_message}, *state.messages]
        ),
    )

    # 处理到了最后一步但是模型仍试图使用工具的情况
    if state.is_last_step and response.tool_calls:
        return {
            "messages": [
                AIMessage(
                    id=response.id,
                    content="抱歉，我未能在指定的步数内找到你问题的答案。",
                )
            ]
        }

    # 将模型的回答加入到已存在的消息列表中进行返回
    return {"messages": [response]}


# 路径判断路由
def route_after_path_validation(state: State) -> Literal["__end__", "call_model"]:
    if state.needs_user_input:
        return "__end__"
    return "call_model"


def route_model_output(state: State) -> Literal["__end__", "tools"]:
    """根据模型的输出确定下一个节点.

    这个方法用来检测模型的最后一条消息是否包含工具调用。

    Args:
        state (State): 对话的当前状态。

    Returns:
        str: 下一个调用节点的名字 ("__end__" or "tools").
    """
    last_message = state.messages[-1]
    if not isinstance(last_message, AIMessage):
        raise ValueError(
            f"预期输出AIMessage, 但实际没有出现 {type(last_message).__name__}"
        )
    # 如果不是工具调用那么结束
    if not last_message.tool_calls:
        return "__end__"
    # 否则，执行请求
    return "tools"


# 定义一个图
builder = StateGraph(State, input_schema=InputState, context_schema=Context)

builder.add_node("validate_authorized_path_node", validate_authorized_path_node)
builder.add_node(call_model)
builder.add_node("tools", ToolNode(TOOLS))

builder.add_edge("__start__", "validate_authorized_path_node")
builder.add_conditional_edges(
    "validate_authorized_path",
    route_after_path_validation,
)
# 添加一个条件边来确定`call_model`节点的下一步
builder.add_conditional_edges(
    "call_model",
    # call_model 执行完成后, 通过route_model_output的输出来调用后续节点
    route_model_output,
)

# 添加一条从 `tools` 指向 `call_model`
# 形成一个循环，在使用完工具后总是会返回到模型
builder.add_edge("tools", "call_model")

# 构建出可执行的图，名字为ReAct Agent
graph = builder.compile(name="ReAct Agent", checkpointer=InMemorySaver())
