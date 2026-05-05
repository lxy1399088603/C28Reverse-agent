"""Path validation graph node."""

from __future__ import annotations

from typing import Any
from pathlib import Path
from react_agent.state import State
from react_agent.domain.paths import PathCandidate, PathValidationResult

# 验证路径是否存在，并按类型放到对应的列表
def validate_path_candidates(
    candidates: list[PathCandidate],
) -> PathValidationResult:
    result = PathValidationResult()

    for candidate in candidates:
        raw_path = candidate.path.strip().strip('"').strip("'")
        if not raw_path:
            continue

        try:
            path = Path(raw_path).expanduser().resolve()
        except OSError:
            result.invalid_paths.append(candidate.path)
            continue

        if not path.exists():
            result.invalid_paths.append(candidate.path)
            continue

        if path.is_dir():
            result.authorized_paths.append(
                PathCandidate(
                    path=str(path),
                    type="directory",
                    role=candidate.role,
                )
            )
            continue

        if path.is_file():
            result.source_files.append(
                PathCandidate(
                    path=str(path),
                    type="file",
                    role=candidate.role,
                )
            )
            continue

        result.invalid_paths.append(candidate.path)

    return result


# 验证候选路径
def validate_paths_node(state: State) -> dict[str, Any]:

    result = validate_path_candidates(state.path_candidates)

    # 保留之前的路径
    authorized_paths = result.authorized_paths or state.authorized_paths
    source_files = result.source_files or state.source_files

    update: dict[str, Any] = {
        "authorized_paths": authorized_paths,
        "source_files": source_files,
        "paths_locked": bool(authorized_paths or source_files),
        "needs_user_input": False,
        "blocking_reason": None,
        "missing_requirements": [],
    }

    # asm_files 模式必须有本地文件或目录作为信息来源。
    if state.source_mode == "asm_files":
        if not source_files and not authorized_paths:
            update.update(
                {
                    "session_phase": "blocked",
                    "needs_user_input": True,
                    "blocking_reason": "asm_files 模式需要提供存在的 asm/lst/disasm 文件或目录。",
                    "missing_requirements": ["asm_source"],
                    "last_blocking_node": "validate_paths_node",
                }
            )
        return update

    # 如果用户没有明确来源，但提供了本地文件，则自动采用 asm_files 模式。
    if state.source_mode == "unknown" and source_files:
        update["source_mode"] = "asm_files"
        update["source_locked"] = True

    return update
