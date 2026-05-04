"""Path validation graph node."""

from __future__ import annotations

from typing import Any
from pathlib import Path
from react_agent.state import State
from react_agent.domain.paths import PathCandidate, PathValidationResult


def validate_path_candidates(
    candidates: list[PathCandidate],
) -> PathValidationResult:
    """Validate AI-extracted path candidates against the local filesystem.

    AI 只能提供候选路径；这里才是真正的安全边界。
    返回值会把存在的目录归入 authorized_paths，把存在的文件归入 source_files。
    后续写文件工具只能使用 authorized_paths 中的 directory。
    """

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


def validate_paths_node(state: State) -> dict[str, Any]:
    """Validate path candidates and derive trusted source files/directories."""

    result = validate_path_candidates(state.path_candidates)

    # 普通 State 字段是覆盖式更新。这里不要用空列表清掉旧的可信路径；
    # 只有本轮确实校验出新路径时才替换，否则保留上一轮已经验证过的路径。
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
