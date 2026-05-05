from react_agent.domain.intake import TaskIntake
from react_agent.nodes.intake import apply_deterministic_intake_fallback


def test_intake_fallback_extracts_mcp_path_and_function() -> None:
    intake = TaskIntake(
        task_mode="entry_call_chain",
        source_mode="unknown",
        function_names=[],
        entry_points=[],
        path_candidates=[],
    )

    patched = apply_deterministic_intake_fallback(
        r"可操作路径为D:\workEnvironment\ai\Agent\test，使用mcp工具，还原_main函数并写入磁盘",
        intake,
    )

    assert patched.source_mode == "mcp"
    assert patched.task_mode == "single_functions"
    assert patched.function_names == ["_main"]
    assert patched.entry_points == []
    assert [item.path for item in patched.path_candidates] == [
        r"D:\workEnvironment\ai\Agent\test"
    ]


def test_intake_fallback_recognizes_bare_function_name() -> None:
    patched = apply_deterministic_intake_fallback("main", TaskIntake())

    assert patched.task_mode == "single_functions"
    assert patched.function_names == ["main"]
