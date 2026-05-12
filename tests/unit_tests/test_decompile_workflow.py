from types import SimpleNamespace

import pytest

from react_agent.nodes.decompile import scan_callees_node
from react_agent.routing import route_from_session_entry
from react_agent.state import State


def test_route_from_session_entry_resumes_through_loop_router() -> None:
    state = State(initialization_complete=True)

    assert route_from_session_entry(state) == "decompile_loop_router_node"


@pytest.mark.anyio
async def test_scan_callees_node_enqueues_direct_callees_from_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCalleeTool:
        name = "get_callee_name"

        async def ainvoke(self, payload: dict[str, str]) -> dict[str, object]:
            assert payload == {"target": "_main"}
            return {
                "target": "_main",
                "addr": "0x89a58",
                "names": ["sub_8BB1B", "sub_8C3E0", "_main", "done_fn"],
                "count": 4,
            }

    async def fake_load_runtime_tools(state: State, context: object) -> list[object]:
        return [FakeCalleeTool()]

    monkeypatch.setattr(
        "react_agent.nodes.decompile.load_runtime_tools",
        fake_load_runtime_tools,
    )

    state = State(
        current_function="_main",
        function_queue=["existing_tail"],
        completed_functions=["done_fn"],
        verification_evidence_gaps=[],
    )

    result = await scan_callees_node(
        state,
        SimpleNamespace(context=SimpleNamespace()),
    )

    assert result["completed_functions"] == ["done_fn", "_main"]
    assert result["function_queue"] == ["sub_8BB1B", "sub_8C3E0", "existing_tail"]
    assert result["session_phase"] == "running"
    assert result["needs_user_input"] is False
