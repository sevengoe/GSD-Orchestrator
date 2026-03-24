import re
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP


def register_search_tools(mcp: FastMCP, validate_path: Callable[[str], Path]) -> None:
    """Register search tools: search_files and grep_files."""

    @mcp.tool()
    def search_files(path: str, pattern: str, max_results: int = 100) -> str:
        """Search for files matching a glob pattern. Use ** for recursive search."""
        base = validate_path(path)
        if not base.is_dir():
            raise NotADirectoryError(f"Not a directory: '{base}'")
        results = []
        for match in base.glob(pattern):
            try:
                validate_path(str(match))
            except PermissionError:
                continue
            results.append(str(match))
            if len(results) >= max_results:
                break
        return "\n".join(results) if results else "No files found."

    @mcp.tool()
    def grep_files(path: str, pattern: str, max_results: int = 100) -> str:
        """Search file contents for a regex pattern. Returns path:lineno:content format."""
        base = validate_path(path)
        compiled = re.compile(pattern)
        results = []
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        for f in files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if compiled.search(line):
                    results.append(f"{f}:{lineno}:{line}")
                    if len(results) >= max_results:
                        return "\n".join(results)
        return "\n".join(results) if results else "No matches found."
