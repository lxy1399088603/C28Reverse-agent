from __future__ import annotations

import pytest

from react_agent.domain.intake import PathCandidate
from react_agent.file_tools import build_file_tools
from react_agent.state import State


def _tool_map(state: State):
    return {tool.name: tool for tool in build_file_tools(state)}


def test_list_and_read_file_within_authorized_directory(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "sample.txt"
    source.write_text("line1\nline2\nline3\n", encoding="utf-8")

    state = State(
        authorized_paths=[PathCandidate(path=str(root), type="directory")],
    )
    tools = _tool_map(state)

    listing = tools["list_directory"].invoke({"path": str(root)})
    assert any(item["name"] == "sample.txt" for item in listing["items"])

    result = tools["read_file"].invoke(
        {"path": str(source), "start_line": 2, "end_line": 3}
    )
    assert result["content"] == "line2\nline3"


def test_write_file_creates_new_file_but_refuses_overwrite(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "new.txt"

    state = State(
        authorized_paths=[PathCandidate(path=str(root), type="directory")],
    )
    tools = _tool_map(state)

    created = tools["write_file"].invoke({"path": str(target), "content": "hello"})
    assert created["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "hello"

    with pytest.raises(FileExistsError):
        tools["write_file"].invoke({"path": str(target), "content": "overwrite"})


def test_replace_in_file_requires_exact_match(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("alpha beta alpha", encoding="utf-8")

    state = State(
        source_files=[PathCandidate(path=str(target), type="file")],
    )
    tools = _tool_map(state)

    with pytest.raises(ValueError):
        tools["replace_in_file"].invoke(
            {"path": str(target), "old_text": "alpha", "new_text": "omega"}
        )

    updated = tools["replace_in_file"].invoke(
        {
            "path": str(target),
            "old_text": "alpha",
            "new_text": "omega",
            "replace_all": True,
        }
    )
    assert updated["replaced_count"] == 2
    assert target.read_text(encoding="utf-8") == "omega beta omega"


def test_blocked_directories_are_rejected(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    git_dir = root / ".git"
    git_dir.mkdir()

    state = State(
        authorized_paths=[PathCandidate(path=str(root), type="directory")],
    )
    tools = _tool_map(state)

    with pytest.raises(PermissionError):
        tools["list_directory"].invoke({"path": str(git_dir)})
