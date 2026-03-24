import json
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP


def register_batch_tools(
    mcp: FastMCP,
    validate_path: Callable[[str], Path],
    resolved_dirs: list[Path],
) -> None:
    """Register batch tools: read_multiple_files and list_allowed_directories."""

    @mcp.tool()
    def read_multiple_files(paths: list[str]) -> str:
        """Read multiple files at once. Returns JSON dict of {path: content_or_error}."""
        results = {}
        for raw_path in paths:
            try:
                p = validate_path(raw_path)
                if not p.is_file():
                    results[raw_path] = f"ERROR: Not a file: '{p}'"
                else:
                    results[raw_path] = p.read_text(encoding="utf-8")
            except PermissionError as e:
                results[raw_path] = f"ERROR: {e}"
            except Exception as e:
                results[raw_path] = f"ERROR: {e}"
        return json.dumps(results, indent=2, ensure_ascii=False)

    @mcp.tool()
    def list_allowed_directories() -> str:
        """List all directories that this server is allowed to access."""
        return "\n".join(str(d) for d in resolved_dirs)
