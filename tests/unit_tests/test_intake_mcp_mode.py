from react_agent.domain.intake import TaskIntake
from react_agent.nodes.intake import merge_intake_with_state
from react_agent.state import State


def test_merge_intake_with_state_forces_mcp_mode() -> None:
    state = State()
    intake = TaskIntake(
        task_mode="entry_call_chain",
        source_mode="unknown",
        function_names=["_main"],
        path_candidates=[],
    )

    merged = merge_intake_with_state(state, intake)

    assert merged["source_mode"] == "mcp"
    assert merged["mcp_required"] is True
