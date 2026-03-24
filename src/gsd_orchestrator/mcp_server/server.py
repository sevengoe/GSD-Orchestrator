import logging
from pathlib import Path
from mcp.server.fastmcp import FastMCP

from .tools.files import register_tools
from .tools.search import register_search_tools
from .tools.tree import register_tree_tools
from .tools.batch import register_batch_tools

logger = logging.getLogger(__name__)


def create_server(allowed_dirs: list[str]) -> FastMCP:
    if not allowed_dirs:
        raise ValueError("At least one --allow directory is required")

    resolved_dirs = [Path(d).resolve() for d in allowed_dirs]
    logger.info("Allowed directories: %s", [str(d) for d in resolved_dirs])

    mcp = FastMCP("gsd-filesystem")

    def validate_path(raw: str) -> Path:
        p = Path(raw).expanduser().resolve()
        for d in resolved_dirs:
            try:
                p.relative_to(d)
                return p
            except ValueError:
                continue
        raise PermissionError(
            f"Access denied: '{p}' is outside allowed directories. "
            f"Allowed: {[str(d) for d in resolved_dirs]}"
        )

    register_tools(mcp, validate_path)
    register_search_tools(mcp, validate_path)
    register_tree_tools(mcp, validate_path)
    register_batch_tools(mcp, validate_path, resolved_dirs)
    return mcp
