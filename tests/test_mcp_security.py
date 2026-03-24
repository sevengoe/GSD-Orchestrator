"""Security tests for path traversal prevention in MCP filesystem tools.

Verifies that validate_path blocks access outside allowed directories
for all new tools that accept path arguments.
"""

import json
from pathlib import Path

import pytest
from gsd_orchestrator.mcp_server import create_server
from mcp.shared.memory import create_connected_server_and_client_session


async def call_tool(tmp_path: Path, tool_name: str, arguments: dict):
    """Helper: create server + in-memory client, call one tool, return result."""
    mcp = create_server([str(tmp_path)])
    async with create_connected_server_and_client_session(
        mcp._mcp_server,
        raise_exceptions=True,
    ) as client:
        return await client.call_tool(tool_name, arguments)


# ---------------------------------------------------------------------------
# move_file security tests
# ---------------------------------------------------------------------------


async def test_move_file_source_outside(tmp_path):
    """Moving a file from outside allowed dir must be denied."""
    result = await call_tool(
        tmp_path,
        "move_file",
        {"source": "/etc/passwd", "destination": str(tmp_path / "stolen.txt")},
    )
    assert result.isError
    assert "Access denied" in result.content[0].text


async def test_move_file_destination_outside(tmp_path):
    """Moving a file to outside allowed dir must be denied."""
    src = tmp_path / "file.txt"
    src.write_text("data", encoding="utf-8")
    result = await call_tool(
        tmp_path,
        "move_file",
        {"source": str(src), "destination": "/tmp/evil_destination.txt"},
    )
    assert result.isError
    assert "Access denied" in result.content[0].text


async def test_move_file_path_traversal(tmp_path):
    """Path traversal via ../ in source or destination must be denied."""
    # Build a path that tries to escape: tmp_path / "../../../etc/passwd"
    escape_path = str(tmp_path / ".." / ".." / ".." / "etc" / "passwd")
    src = tmp_path / "file.txt"
    src.write_text("data", encoding="utf-8")
    result = await call_tool(
        tmp_path,
        "move_file",
        {"source": escape_path, "destination": str(tmp_path / "out.txt")},
    )
    assert result.isError
    assert "Access denied" in result.content[0].text


# ---------------------------------------------------------------------------
# delete_file security tests
# ---------------------------------------------------------------------------


async def test_delete_file_outside(tmp_path):
    """Deleting a file outside allowed dir must be denied."""
    result = await call_tool(tmp_path, "delete_file", {"path": "/etc/passwd"})
    assert result.isError
    assert "Access denied" in result.content[0].text


# ---------------------------------------------------------------------------
# get_file_info security tests
# ---------------------------------------------------------------------------


async def test_get_file_info_outside(tmp_path):
    """Getting file info for a path outside allowed dir must be denied."""
    result = await call_tool(tmp_path, "get_file_info", {"path": "/etc/passwd"})
    assert result.isError
    assert "Access denied" in result.content[0].text


# ---------------------------------------------------------------------------
# search_files security tests
# ---------------------------------------------------------------------------


async def test_search_files_outside(tmp_path):
    """Searching outside allowed dir must be denied."""
    result = await call_tool(
        tmp_path, "search_files", {"path": "/etc", "pattern": "*"}
    )
    assert result.isError
    assert "Access denied" in result.content[0].text


# ---------------------------------------------------------------------------
# grep_files security tests
# ---------------------------------------------------------------------------


async def test_grep_files_outside(tmp_path):
    """Grepping outside allowed dir must be denied."""
    result = await call_tool(
        tmp_path, "grep_files", {"path": "/etc/passwd", "pattern": "root"}
    )
    assert result.isError
    assert "Access denied" in result.content[0].text


# ---------------------------------------------------------------------------
# directory_tree security tests
# ---------------------------------------------------------------------------


async def test_directory_tree_outside(tmp_path):
    """Building tree outside allowed dir must be denied."""
    result = await call_tool(tmp_path, "directory_tree", {"path": "/etc"})
    assert result.isError
    assert "Access denied" in result.content[0].text


# ---------------------------------------------------------------------------
# read_multiple_files security tests
# ---------------------------------------------------------------------------


async def test_read_multiple_files_outside(tmp_path):
    """read_multiple_files catches PermissionError per-file and returns ERROR string."""
    f_valid = tmp_path / "ok.txt"
    f_valid.write_text("ok", encoding="utf-8")
    outside_path = "/etc/passwd"
    result = await call_tool(
        tmp_path,
        "read_multiple_files",
        {"paths": [str(f_valid), outside_path]},
    )
    # read_multiple_files does NOT raise — it returns per-file results including errors
    assert not result.isError
    data = json.loads(result.content[0].text)
    assert data[str(f_valid)] == "ok"
    assert data[outside_path].startswith("ERROR")
    assert "Access denied" in data[outside_path]
