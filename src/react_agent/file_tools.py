from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from react_agent.state import State


# 文件操作默认只允许落在用户已经确认过的可操作目录里。
# 这些目录通常来自 authorized_paths，属于本轮会话里可信的写入边界。
BLOCKED_PATH_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
}

MAX_READ_CHARS = 200_000
MAX_WRITE_CHARS = 200_000
MAX_DIRECTORY_ITEMS = 200


@dataclass
class FileAccessPolicy:
    """Runtime file access policy derived from the current agent state."""

    writable_roots: tuple[Path, ...]
    editable_files: tuple[Path, ...]
    readable_files: tuple[Path, ...]


def _unique_paths(paths: list[Path]) -> tuple[Path, ...]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return tuple(unique)


def build_file_access_policy(state: State) -> FileAccessPolicy:
    """Build file access policy from already-validated state facts."""

    writable_roots: list[Path] = []
    editable_files: list[Path] = []
    readable_files: list[Path] = []

    for item in state.authorized_paths:
        if item.type != "directory":
            continue
        try:
            writable_roots.append(Path(item.path).resolve())
        except OSError:
            continue

    for item in state.source_files:
        if item.type != "file":
            continue
        try:
            resolved = Path(item.path).resolve()
        except OSError:
            continue
        editable_files.append(resolved)
        readable_files.append(resolved)

    return FileAccessPolicy(
        writable_roots=_unique_paths(writable_roots),
        editable_files=_unique_paths(editable_files),
        readable_files=_unique_paths(readable_files),
    )


def _path_in_root(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _check_blocked_parts(path: Path) -> str | None:
    for part in path.parts:
        if part in BLOCKED_PATH_PARTS:
            return f"目标路径包含受保护目录: {part}"
    return None


def _resolve_existing_path(raw_path: str) -> Path:
    cleaned = raw_path.strip().strip('"').strip("'")
    if not cleaned:
        raise ValueError("目标路径不能为空。")
    return Path(cleaned).expanduser().resolve(strict=True)


def _resolve_target_path(raw_path: str) -> Path:
    cleaned = raw_path.strip().strip('"').strip("'")
    if not cleaned:
        raise ValueError("目标路径不能为空。")

    candidate = Path(cleaned).expanduser()
    parent = candidate.parent.expanduser().resolve(strict=True)
    return (parent / candidate.name).resolve(strict=False)


def _ensure_readable_file(path: Path, policy: FileAccessPolicy) -> None:
    blocked = _check_blocked_parts(path)
    if blocked:
        raise PermissionError(blocked)

    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    if path in policy.readable_files or path in policy.editable_files:
        return

    for root in policy.writable_roots:
        if _path_in_root(path, root):
            return

    raise PermissionError("目标文件不在当前会话授权范围内。")


def _ensure_writable_target(path: Path, policy: FileAccessPolicy) -> None:
    blocked = _check_blocked_parts(path)
    if blocked:
        raise PermissionError(blocked)

    for root in policy.writable_roots:
        if _path_in_root(path, root):
            return

    if path in policy.editable_files:
        return

    raise PermissionError("目标路径不在当前会话授权写入范围内。")


def _ensure_editable_existing_file(path: Path, policy: FileAccessPolicy) -> None:
    _ensure_readable_file(path, policy)

    if path in policy.editable_files:
        return

    for root in policy.writable_roots:
        if _path_in_root(path, root):
            return

    raise PermissionError("当前只允许修改授权目录中的文件或已确认的源文件。")


def _ensure_text_size(label: str, content: str, limit: int) -> None:
    if len(content) > limit:
        raise ValueError(f"{label}过大，超过限制 {limit} 个字符。")


def build_file_tools(state: State) -> list[BaseTool]:
    """Build runtime file tools using the current session authorization state."""

    policy = build_file_access_policy(state)

    @tool(
        "list_directory",
        description="列出授权目录中的文件和子目录。path 为空时返回当前会话授权的目录和文件摘要。",
    )
    def list_directory(path: str = "") -> dict[str, Any]:
        if not path.strip():
            return {
                "writable_roots": [str(item) for item in policy.writable_roots],
                "readable_files": [str(item) for item in policy.readable_files],
                "editable_files": [str(item) for item in policy.editable_files],
            }

        target = _resolve_existing_path(path)
        blocked = _check_blocked_parts(target)
        if blocked:
            raise PermissionError(blocked)

        if not target.is_dir():
            raise NotADirectoryError(f"目标不是目录: {target}")

        allowed = any(_path_in_root(target, root) for root in policy.writable_roots)
        if not allowed:
            raise PermissionError("目标目录不在当前会话授权范围内。")

        items: list[dict[str, Any]] = []
        for child in sorted(target.iterdir(), key=lambda item: item.name.lower())[:MAX_DIRECTORY_ITEMS]:
            items.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "type": "directory" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )

        return {"path": str(target), "items": items}

    @tool(
        "read_file",
        description="读取授权文件内容。支持 start_line 和 end_line 做局部读取，便于大文件审查。",
    )
    def read_file(path: str, start_line: int = 1, end_line: int = 0) -> dict[str, Any]:
        target = _resolve_existing_path(path)
        _ensure_readable_file(target, policy)

        text = target.read_text(encoding="utf-8")
        _ensure_text_size("读取内容", text, MAX_READ_CHARS)

        lines = text.splitlines()
        if start_line < 1:
            raise ValueError("start_line 必须从 1 开始。")

        selected_lines = lines[start_line - 1 :]
        actual_end = len(lines)
        if end_line > 0:
            if end_line < start_line:
                raise ValueError("end_line 不能小于 start_line。")
            selected_lines = lines[start_line - 1 : end_line]
            actual_end = min(end_line, len(lines))

        return {
            "path": str(target),
            "start_line": start_line,
            "end_line": actual_end,
            "content": "\n".join(selected_lines),
            "line_count": len(lines),
        }

    @tool(
        "create_directory",
        description="在授权目录内创建子目录。目录已存在时会直接返回，不会重复报错。",
    )
    def create_directory(path: str) -> dict[str, Any]:
        target = _resolve_target_path(path)
        _ensure_writable_target(target, policy)

        if target.exists() and not target.is_dir():
            raise FileExistsError(f"目标已存在且不是目录: {target}")

        target.mkdir(parents=True, exist_ok=True)
        return {"status": "ok", "path": str(target)}

    @tool(
        "write_file",
        description="在授权目录内创建或覆盖文本文件。默认 overwrite=False，避免误覆盖已有内容。",
    )
    def write_file(path: str, content: str, overwrite: bool = False) -> dict[str, Any]:
        _ensure_text_size("写入内容", content, MAX_WRITE_CHARS)
        target = _resolve_target_path(path)
        _ensure_writable_target(target, policy)

        if target.exists():
            if not target.is_file():
                raise FileExistsError(f"目标已存在且不是文件: {target}")
            if not overwrite:
                raise FileExistsError("目标文件已存在，如需覆盖请显式传入 overwrite=True。")

        target.write_text(content, encoding="utf-8")
        return {
            "status": "ok",
            "path": str(target),
            "chars": len(content),
        }

    @tool(
        "replace_in_file",
        description="对授权文件执行精确文本替换。默认只允许唯一匹配，避免 AI 模糊改错位置。",
    )
    def replace_in_file(
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        if not old_text:
            raise ValueError("old_text 不能为空。")
        _ensure_text_size("替换后的内容", new_text, MAX_WRITE_CHARS)

        target = _resolve_existing_path(path)
        _ensure_editable_existing_file(target, policy)

        original = target.read_text(encoding="utf-8")
        occurrences = original.count(old_text)
        if occurrences == 0:
            raise ValueError("目标文件中没有找到 old_text，未执行任何修改。")
        if occurrences > 1 and not replace_all:
            raise ValueError("old_text 命中多个位置，请先缩小范围或显式传入 replace_all=True。")

        updated = original.replace(old_text, new_text) if replace_all else original.replace(old_text, new_text, 1)
        _ensure_text_size("写入内容", updated, MAX_WRITE_CHARS)

        target.write_text(updated, encoding="utf-8")
        replaced_count = occurrences if replace_all else 1
        return {
            "status": "ok",
            "path": str(target),
            "replaced_count": replaced_count,
        }

    return [
        list_directory,
        read_file,
        create_directory,
        write_file,
        replace_in_file,
    ]
