from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from react_agent.nodes.decompile import (
    _extract_callee_names,
    _filter_discovered_callees,
    _merge_discovered_callees,
    _remove_current_round_messages,
    _slice_current_round_messages,
)
from react_agent.state import State


def test_extract_callee_names_reads_names_array() -> None:
    result = {
        "target": "_main",
        "addr": "0x89a58",
        "names": ["sub_8BB1B", "memcopy_8CF75", "_ti_sysbios_BIOS_start__E"],
        "count": 3,
    }

    assert _extract_callee_names(result) == [
        "sub_8BB1B",
        "memcopy_8CF75",
        "_ti_sysbios_BIOS_start__E",
    ]


def test_filter_discovered_callees_ignores_current_completed_and_duplicates() -> None:
    filtered = _filter_discovered_callees(
        ["sub_8BB1B", "_main", "sub_8BB1B", "done_fn", "bad name"],
        "_main",
        ["done_fn"],
    )

    assert filtered == ["sub_8BB1B"]


def test_slice_current_round_messages_keeps_task_and_current_round_only() -> None:
    task = HumanMessage(content="recover from _main", id="task")
    old_ai = AIMessage(content="old report", id="old-ai")
    boundary = AIMessage(content="finished previous function", id="boundary")
    current_tool = ToolMessage(
        content="disasm for sub_8BB1B",
        tool_call_id="tc1",
        id="tool-1",
    )
    current_ai = AIMessage(content="Recovered C for sub_8BB1B", id="ai-1")
    state = State(
        messages=[task, old_ai, boundary, current_tool, current_ai],
        current_function="sub_8BB1B",
        current_round_start_message_id="boundary",
    )

    assert _slice_current_round_messages(state) == [task, current_tool, current_ai]


def test_remove_current_round_messages_removes_only_messages_after_boundary() -> None:
    task = HumanMessage(content="recover from _main", id="task")
    old_ai = AIMessage(content="old report", id="old-ai")
    boundary = AIMessage(content="finished previous function", id="boundary")
    current_human = HumanMessage(content="continue", id="human-2")
    current_tool = ToolMessage(content="tool output", tool_call_id="tc1", id="tool-1")
    current_ai = AIMessage(content="current report", id="ai-1")
    state = State(
        messages=[task, old_ai, boundary, current_human, current_tool, current_ai],
        current_round_start_message_id="boundary",
    )

    removals = _remove_current_round_messages(state)

    assert [message.id for message in removals] == ["tool-1", "ai-1"]


def test_merge_discovered_callees_keeps_depth_first_order() -> None:
    updated = _merge_discovered_callees(
        "_main",
        ["_main"],
        ["InitGPIO_8B277", "sub_8BB1B"],
        ["sub_8BB1B", "memcopy_8CF75"],
        ["sub_8AF0D", "sub_8BB1B"],
    )

    assert updated == [
        "sub_8BB1B",
        "memcopy_8CF75",
        "sub_8AF0D",
        "InitGPIO_8B277",
    ]
