from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage
from pathlib import Path
from typing import Literal, Any
from react_agent.state import State


# 获取用户最后一条用户信息
def latest_human_text(state: State) -> str:
    for message in reversed(state.messages):
        if getattr(message, "type", None) == "human":
            return get_message_text(message)
    return ""


def latest_ai_text(state: State) -> str:
    for message in reversed(state.messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str):
                return content
            return str(content)
    return ""

# 判断当前状态完整性
def judge_CompleteState(state: State) -> dict[str, Any]:
    result : dict[str, dict] = {}
    if not state.path_candidates:
        result["complete"] = False
        result["missing_requirements"] = ["path_candidates"]
        result["blocking_reason"] = "没有识别到可操作地址，请提供可操作文件或文件夹。"
    elif not state.function_names:
        result["complete"] = False
        result["missing_requirements"] = ["function_target"]
        result["blocking_reason"] = "没有识别到函数列表或入口函数1。"
    elif state.task_mode == "unknown":
        result["complete"] = False
        result["missing_requirements"] = ["function_target"]
        result["blocking_reason"] = "没有识别到函数列表或入口函数2。"
    else:
        result["complete"] = True
    return result


# 获取消息的文本内容
def get_message_text(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    elif isinstance(content, dict):
        return content.get("text", "")
    else:
        txts = [c if isinstance(c, str) else (c.get("text") or "") for c in content]
        return "".join(txts).strip()


# 通过模型名加载模型
def load_chat_model(
    fully_specified_name: str,
    base_url: str | None = None,
    api_key: str | None = None,
) -> BaseChatModel:

    provider, model = fully_specified_name.split("/", maxsplit=1)
    kwargs = {}
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    return init_chat_model(model, model_provider=provider, **kwargs)


# 校验可操作路径
def validata_path(path_text: str) -> tuple[str, Literal["file", "directory"]] | None:
    try:
        path = Path(path_text.strip().strip('"').strip("'")).expanduser().resolve()
    except OSError:
        return None

    if not path.exists():
        return None

    if path.is_file():
        return str(path), "file"

    if path.is_dir():
        return str(path), "directory"

    return None


# 兼容 true/1/yes/on
def truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
