import fnmatch
import json
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP


def _build_tree(
    path: Path,
    validate_path: Callable[[str], Path],
    exclude_patterns: list[str],
    max_depth: int | None,
    current_depth: int = 0,
    visited: set | None = None,
) -> dict:
    if visited is None:
        visited = set()
    node: dict = {"name": path.name, "type": "directory" if path.is_dir() else "file"}
    if path.is_dir():
        real = path.resolve()
        if real in visited:
            node["children"] = []
            node["note"] = "symlink cycle detected"
            return node
        visited.add(real)
        if max_depth is not None and current_depth >= max_depth:
            node["children"] = []
            return node
        children = []
        for child in sorted(path.iterdir()):
            if any(fnmatch.fnmatch(child.name, p) for p in exclude_patterns):
                continue
            children.append(
                _build_tree(child, validate_path, exclude_patterns, max_depth, current_depth + 1, visited)
            )
        node["children"] = children
    return node


def register_tree_tools(mcp: FastMCP, validate_path: Callable[[str], Path]) -> None:
    """Register tree tools: directory_tree."""

    @mcp.tool()
    def directory_tree(
        path: str,
        max_depth: int | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> str:
        """Return a recursive directory tree as JSON with nested children structure."""
        p = validate_path(path)
        if not p.is_dir():
            raise NotADirectoryError(f"Not a directory: '{p}'")
        excl = exclude_patterns or []
        tree = _build_tree(p, validate_path, excl, max_depth)
        return json.dumps(tree, indent=2)
