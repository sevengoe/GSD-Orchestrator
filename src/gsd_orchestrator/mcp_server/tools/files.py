import json
import os
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP


def register_tools(mcp: FastMCP, validate_path: Callable[[str], Path]) -> None:
    """Register all filesystem tools on the given FastMCP instance."""

    @mcp.tool()
    def read_file(path: str, head: int | None = None, tail: int | None = None) -> str:
        """Read file contents as UTF-8 text. Optionally return only the first N lines (head) or last N lines (tail)."""
        p = validate_path(path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: '{p}'")
        if head is not None and tail is not None:
            raise ValueError("Cannot use both head and tail")
        content = p.read_text(encoding="utf-8")
        if head is not None:
            lines = content.split("\n")
            return "\n".join(lines[:head])
        if tail is not None:
            lines = content.split("\n")
            return "\n".join(lines[-tail:])
        return content

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

    @mcp.tool()
    def get_file_info(path: str) -> str:
        """Return metadata for a file or directory: size, type, permissions, timestamps."""
        p = validate_path(path)
        if not p.exists():
            raise FileNotFoundError(f"Path does not exist: '{p}'")
        s = p.stat()
        info = {
            "path": str(p),
            "size": s.st_size,
            "type": "directory" if p.is_dir() else "file",
            "permissions": oct(stat.S_IMODE(s.st_mode)),
            "created": datetime.fromtimestamp(s.st_ctime).isoformat(),
            "modified": datetime.fromtimestamp(s.st_mtime).isoformat(),
            "accessed": datetime.fromtimestamp(s.st_atime).isoformat(),
        }
        return json.dumps(info, indent=2)

    @mcp.tool()
    def move_file(source: str, destination: str) -> str:
        """Move a file or directory. Both source and destination must be within allowed directories."""
        src = validate_path(source)
        dst = validate_path(destination)
        if not src.exists():
            raise FileNotFoundError(f"Source not found: '{src}'")
        if dst.exists():
            raise FileExistsError(f"Destination already exists: '{dst}'. Will not overwrite.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved '{src}' to '{dst}'"

    @mcp.tool()
    def delete_file(path: str) -> str:
        """Delete a single file. Directories cannot be deleted with this tool."""
        p = validate_path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: '{p}'")
        if p.is_dir():
            raise IsADirectoryError(f"Cannot delete directory: '{p}'. Use this tool for files only.")
        p.unlink()
        return f"Deleted file: '{p}'"
