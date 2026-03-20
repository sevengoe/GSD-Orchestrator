"""Core filesystem tools for the GSD MCP server.

All tools are registered via register_tools() and validate every path argument
through the validate_path() closure before performing any filesystem operation.
"""

import os
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP


def register_tools(mcp: FastMCP, validate_path: Callable[[str], Path]) -> None:
    """Register all core filesystem tools on the given FastMCP instance.

    Args:
        mcp: The FastMCP server instance to register tools on.
        validate_path: Security closure that resolves and validates paths against
                       the allowed directories configured at server startup.
    """

    @mcp.tool()
    def read_file(path: str, head: int | None = None, tail: int | None = None) -> str:
        """Read file contents as UTF-8 text. Optionally return only the first N lines (head) or last N lines (tail)."""
        p = validate_path(path)
        if not p.is_file():
            raise FileNotFoundError(f"No such file: '{p}'")
        if head is not None and tail is not None:
            raise ValueError("Cannot use both head and tail")
        text = p.read_text(encoding="utf-8")
        if head is not None:
            return "\n".join(text.split("\n")[:head])
        if tail is not None:
            return "\n".join(text.split("\n")[-tail:])
        return text

    @mcp.tool()
    def write_file(path: str, content: str) -> str:
        """Create or overwrite a file atomically using write-then-rename pattern."""
        dest = validate_path(path)
        tmp = Path(str(dest) + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(str(tmp), str(dest))
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return f"Written {len(content)} characters to {dest}"

    @mcp.tool()
    def list_directory(path: str) -> str:
        """List directory contents with [FILE] or [DIR] prefix per entry."""
        d = validate_path(path)
        if not d.is_dir():
            raise NotADirectoryError(f"Not a directory: '{d}'")
        entries = []
        for entry in sorted(d.iterdir()):
            prefix = "[DIR]" if entry.is_dir() else "[FILE]"
            entries.append(f"{prefix} {entry.name}")
        return "\n".join(entries)

    @mcp.tool()
    def create_directory(path: str) -> str:
        """Create a directory and any necessary parent directories (mkdir -p behavior)."""
        p = validate_path(path)
        p.mkdir(parents=True, exist_ok=True)
        return f"Created directory: {p}"
