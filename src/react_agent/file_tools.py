"""Runtime-scoped file tools.

These tools are intentionally conservative:

- Only paths validated into ``authorized_paths`` or ``source_files`` are usable.
- No delete, move, or rename capability is exposed.
- Creating new files is allowed inside authorized directories.
- Editing existing files is limited to exact text replacement.
- Full overwrite of an existing file is rejected to reduce accidental damage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from react_agent.state import State


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
    """Resolved file access policy derived from the current graph state."""

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
    """Build the runtime file policy from validated state fields."""

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
            return f"Target path includes protected directory: {part}"
    return None


def _resolve_existing_path(raw_path: str) -> Path:
    cleaned = raw_path.strip().strip('"').strip("'")
    if not cleaned:
        raise ValueError("Target path cannot be empty.")
    return Path(cleaned).expanduser().resolve(strict=True)


def _resolve_target_path(raw_path: str) -> Path:
    cleaned = raw_path.strip().strip('"').strip("'")
    if not cleaned:
        raise ValueError("Target path cannot be empty.")

    candidate = Path(cleaned).expanduser()
    parent = candidate.parent.expanduser().resolve(strict=True)
    return (parent / candidate.name).resolve(strict=False)


def _ensure_readable_file(path: Path, policy: FileAccessPolicy) -> None:
    blocked = _check_blocked_parts(path)
    if blocked:
        raise PermissionError(blocked)

    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File does not exist: {path}")

    if path in policy.readable_files or path in policy.editable_files:
        return

    for root in policy.writable_roots:
        if _path_in_root(path, root):
            return

    raise PermissionError("Target file is outside the authorized session scope.")


def _ensure_writable_target(path: Path, policy: FileAccessPolicy) -> None:
    blocked = _check_blocked_parts(path)
    if blocked:
        raise PermissionError(blocked)

    for root in policy.writable_roots:
        if _path_in_root(path, root):
            return

    if path in policy.editable_files:
        return

    raise PermissionError("Target path is outside the authorized writable scope.")


def _ensure_editable_existing_file(path: Path, policy: FileAccessPolicy) -> None:
    _ensure_readable_file(path, policy)

    if path in policy.editable_files:
        return

    for root in policy.writable_roots:
        if _path_in_root(path, root):
            return

    raise PermissionError("Only authorized files or files inside authorized directories may be edited.")


def _ensure_text_size(label: str, content: str, limit: int) -> None:
    if len(content) > limit:
        raise ValueError(f"{label} exceeds the limit of {limit} characters.")


def build_file_tools(state: State) -> list[BaseTool]:
    """Build safe file tools for the current session."""

    policy = build_file_access_policy(state)

    @tool(
        "list_directory",
        description="List files and subdirectories inside an authorized directory. Leave path empty to inspect the current authorized scope.",
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
            raise NotADirectoryError(f"Target is not a directory: {target}")

        if not any(_path_in_root(target, root) for root in policy.writable_roots):
            raise PermissionError("Target directory is outside the authorized session scope.")

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
        description="Read a text file inside the authorized scope. Optional line bounds help inspect large files safely.",
    )
    def read_file(path: str, start_line: int = 1, end_line: int = 0) -> dict[str, Any]:
        target = _resolve_existing_path(path)
        _ensure_readable_file(target, policy)

        text = target.read_text(encoding="utf-8")
        _ensure_text_size("Read content", text, MAX_READ_CHARS)

        lines = text.splitlines()
        if start_line < 1:
            raise ValueError("start_line must be >= 1.")

        selected_lines = lines[start_line - 1 :]
        actual_end = len(lines)
        if end_line > 0:
            if end_line < start_line:
                raise ValueError("end_line cannot be smaller than start_line.")
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
        description="Create a subdirectory inside an authorized directory. Existing directories are treated as success.",
    )
    def create_directory(path: str) -> dict[str, Any]:
        target = _resolve_target_path(path)
        _ensure_writable_target(target, policy)

        if target.exists() and not target.is_dir():
            raise FileExistsError(f"Target exists and is not a directory: {target}")

        target.mkdir(parents=True, exist_ok=True)
        return {"status": "ok", "path": str(target)}

    @tool(
        "write_file",
        description="Create a new UTF-8 text file inside an authorized directory. The tool refuses to overwrite an existing file.",
    )
    def write_file(path: str, content: str) -> dict[str, Any]:
        _ensure_text_size("Write content", content, MAX_WRITE_CHARS)
        target = _resolve_target_path(path)
        _ensure_writable_target(target, policy)

        if target.exists():
            raise FileExistsError(
                "Target file already exists. Use replace_in_file for precise edits instead of full overwrite."
            )

        target.write_text(content, encoding="utf-8")
        return {
            "status": "ok",
            "path": str(target),
            "chars": len(content),
        }

    @tool(
        "replace_in_file",
        description="Perform an exact text replacement in an authorized file. By default the match must be unique.",
    )
    def replace_in_file(
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        if not old_text:
            raise ValueError("old_text cannot be empty.")
        _ensure_text_size("Replacement text", new_text, MAX_WRITE_CHARS)

        target = _resolve_existing_path(path)
        _ensure_editable_existing_file(target, policy)

        original = target.read_text(encoding="utf-8")
        occurrences = original.count(old_text)
        if occurrences == 0:
            raise ValueError("old_text was not found in the target file.")
        if occurrences > 1 and not replace_all:
            raise ValueError("old_text matches multiple locations. Narrow the match or set replace_all=True.")

        updated = original.replace(old_text, new_text) if replace_all else original.replace(old_text, new_text, 1)
        _ensure_text_size("Updated content", updated, MAX_WRITE_CHARS)

        target.write_text(updated, encoding="utf-8")
        return {
            "status": "ok",
            "path": str(target),
            "replaced_count": occurrences if replace_all else 1,
        }

    return [
        list_directory,
        read_file,
        create_directory,
        write_file,
        replace_in_file,
    ]
