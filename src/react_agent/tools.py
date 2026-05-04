import asyncio
from typing import Any, Callable, List, Optional, cast

from langchain_tavily import TavilySearch
from langgraph.runtime import get_runtime

from react_agent.context import Context


#  Tavily 搜索函数（仅作为示例）
async def search(query: str) -> Optional[dict[str, Any]]:
    """搜索常规网页结果.

    此功能利用 Tavily 搜索引擎执行搜索，旨在提供全面、准确且值得信赖的搜索结果.
    """
    runtime = get_runtime(Context)
    wrapped = TavilySearch(max_results=runtime.context.max_search_results)
    return cast(dict[str, Any], await asyncio.to_thread(wrapped.invoke, {"query": query}))


TOOLS: List[Callable[..., Any]] = [search]


