"""Define a custom Reasoning and Action agent.

Works with a chat model with tool calling support.
"""

from datetime import UTC, datetime
from typing import Dict, List, Literal, cast, Any

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from langchain_core.messages import HumanMessage

from react_agent.context import Context
from react_agent.state import InputState, State, PathCandidate
from react_agent.tools import TOOLS
from react_agent.utils import load_chat_model, get_message_text, validata_path
from react_agent.path_auth import PATH_INFERENCE_PROMPT, PathCandidates


async def validate_path_node(
        state: State,
        runtime: Runtime[Context],
) -> dict[str, Any]:
    if state.authorized_paths:
        return {
            "needs_user_input": False,
            "blocking_reason": None,
        }
    last_user_text = ""
    for message in reversed(state.messages):
        if getattr(message, "type", None) == "human":
            last_user_text = get_message_text(message)
            break

    if not last_user_text.strip():
        return {
            "needs_user_input": True,
            "blocking_reason": "用户没有提供路径或文件。",
        }
    
    model = load_chat_model(
        runtime.context.model,
        base_url=runtime.context.base_url,
    )
    extractor = PATH_INFERENCE_PROMPT | model.with_structured_output(PathCandidates)
    inference = await extractor.ainvoke({"user_input": last_user_text})
    if not inference.candidates:
        return {
            "needs_user_input": True,
            "blocking_reason": "未识别到可操作路径或文件。",
        }
    valid_candidates: list[PathCandidate] = []
    invalid_paths: list[str] = []

    for candidate in inference.candidates:
        checked = validata_path(candidate.path)

        if checked is None:
            invalid_paths.append(candidate.path)
            continue

        resolved_path, path_type = checked

        valid_candidates.append(
            PathCandidate(
                path=resolved_path,
                type=path_type,
                role=candidate.role,
            )
        )

    if not valid_candidates:
        return {
            "authorized_paths": [],
            "needs_user_input": True,
            "blocking_reason": f"识别到路径，但都不存在或不可访问: {invalid_paths}",
        }

    return {
        "authorized_paths": valid_candidates,
        "needs_user_input": False,
        "blocking_reason": None,
    }


def user_input_path_node(state: State) -> Command[Literal["validate_path_node","__end__"]]:
    user_input = interrupt(
        {
            "question": "请提供至少一个已经存在的可操作目录或文件路径。",
        }
    )

    if user_input is None:
        return "__end__"

    return Command(
        update={
            "messages": [HumanMessage(content=str(user_input))],
            "needs_user_input": False,
            "blocking_reason": None,
        },
        goto="validate_path_node",
    )


# 调用模型
async def call_model(
    state: State, runtime: Runtime[Context]
) -> Dict[str, List[AIMessage]]:
   
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
def route_after_path_validation(state: State) -> Literal["user_input_path_node", "call_model"]:
    if state.needs_user_input:
        return "user_input_path_node"
    return "call_model"


def route_model_output(state: State) -> Literal["__end__", "tools"]:
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

builder.add_node("validate_path_node", validate_path_node)
builder.add_node("user_input_path_node", user_input_path_node)
builder.add_node(call_model)
builder.add_node("tools", ToolNode(TOOLS))

builder.add_edge("__start__", "validate_path_node")
builder.add_conditional_edges(
    "validate_path_node",
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
