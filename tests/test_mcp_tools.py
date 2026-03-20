"""In-memory MCP client tests for the 4 core filesystem tools.

Uses create_connected_server_and_client_session to test tools end-to-end
without any network or subprocess overhead.

IMPORTANT: Each test creates its own async with block (inline pattern).
This avoids pytest-asyncio cancel-scope teardown errors that occur when
a shared fixture owns the async context manager.
"""

import pytest
from pathlib import Path


async def call_tool(tmp_path: Path, tool_name: str, arguments: dict):
    """Create a fresh server, connect in-memory client, call tool, return result.

    Each call creates a new server instance to ensure test isolation.
    """
    from gsd_orchestrator.mcp_server import create_server
    from mcp.shared.memory import create_connected_server_and_client_session

    mcp = create_server([str(tmp_path)])
    async with create_connected_server_and_client_session(
        mcp._mcp_server,
        raise_exceptions=True,
    ) as client:
        return await client.call_tool(tool_name, arguments)


# ---------------------------------------------------------------------------
# Server creation
# ---------------------------------------------------------------------------


def test_server_creates_with_tools(tmp_path):
    """create_server returns a FastMCP instance with tools registered."""
    from gsd_orchestrator.mcp_server import create_server
    from mcp.server.fastmcp import FastMCP

    mcp = create_server([str(tmp_path)])
    assert isinstance(mcp, FastMCP)
    # The inner _mcp_server is the low-level server with tool registry
    assert hasattr(mcp, "_mcp_server")


def test_no_allowed_dirs_raises():
    """create_server([]) raises ValueError — server refuses empty allowed_dirs."""
    from gsd_orchestrator.mcp_server import create_server

    with pytest.raises(ValueError, match="allowed_dirs must be non-empty"):
        create_server([])


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file(tmp_path):
    """read_file returns the full file content."""
    f = tmp_path / "test.txt"
    f.write_text("hello world", encoding="utf-8")

    result = await call_tool(tmp_path, "read_file", {"path": str(f)})
    assert not result.isError
    assert result.content[0].text == "hello world"


@pytest.mark.asyncio
async def test_read_file_head(tmp_path):
    """read_file with head=3 returns only the first 3 lines."""
    f = tmp_path / "lines.txt"
    lines = [f"line{i}" for i in range(1, 11)]
    f.write_text("\n".join(lines), encoding="utf-8")

    result = await call_tool(tmp_path, "read_file", {"path": str(f), "head": 3})
    assert not result.isError
    text = result.content[0].text
    returned_lines = text.split("\n")
    assert returned_lines == ["line1", "line2", "line3"]


@pytest.mark.asyncio
async def test_read_file_tail(tmp_path):
    """read_file with tail=3 returns only the last 3 lines."""
    f = tmp_path / "lines.txt"
    lines = [f"line{i}" for i in range(1, 11)]
    f.write_text("\n".join(lines), encoding="utf-8")

    result = await call_tool(tmp_path, "read_file", {"path": str(f), "tail": 3})
    assert not result.isError
    text = result.content[0].text
    returned_lines = text.split("\n")
    assert returned_lines == ["line8", "line9", "line10"]


@pytest.mark.asyncio
async def test_read_file_not_found(tmp_path):
    """read_file on a nonexistent path returns isError=True."""
    result = await call_tool(
        tmp_path, "read_file", {"path": str(tmp_path / "nonexistent.txt")}
    )
    assert result.isError is True


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file(tmp_path):
    """write_file creates a file with the given content."""
    dest = tmp_path / "output.txt"
    result = await call_tool(
        tmp_path, "write_file", {"path": str(dest), "content": "hello write"}
    )
    assert not result.isError
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "hello write"


@pytest.mark.asyncio
async def test_write_file_creates_parent_dirs(tmp_path):
    """write_file creates parent directories automatically."""
    dest = tmp_path / "sub" / "dir" / "file.txt"
    result = await call_tool(
        tmp_path, "write_file", {"path": str(dest), "content": "nested content"}
    )
    assert not result.isError
    assert dest.exists()
    assert dest.parent.is_dir()
    assert dest.read_text(encoding="utf-8") == "nested content"


@pytest.mark.asyncio
async def test_write_file_atomic_no_leftover_tmp(tmp_path):
    """write_file leaves no .tmp file after successful write (atomic pattern)."""
    dest = tmp_path / "atomic.txt"
    result = await call_tool(
        tmp_path, "write_file", {"path": str(dest), "content": "atomic data"}
    )
    assert not result.isError
    tmp_file = Path(str(dest) + ".tmp")
    assert not tmp_file.exists(), f"Leftover .tmp file found: {tmp_file}"


# ---------------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_directory(tmp_path):
    """list_directory returns [FILE] and [DIR] prefixed entries."""
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "subdir").mkdir()

    result = await call_tool(tmp_path, "list_directory", {"path": str(tmp_path)})
    assert not result.isError
    text = result.content[0].text
    assert "[FILE] file.txt" in text
    assert "[DIR] subdir" in text


@pytest.mark.asyncio
async def test_list_directory_sorted(tmp_path):
    """list_directory returns entries in alphabetical order."""
    (tmp_path / "c.txt").write_text("c", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b").mkdir()

    result = await call_tool(tmp_path, "list_directory", {"path": str(tmp_path)})
    assert not result.isError
    lines = result.content[0].text.strip().split("\n")
    names = [line.split(" ", 1)[1] for line in lines]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# create_directory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_directory(tmp_path):
    """create_directory creates nested directories."""
    new_dir = tmp_path / "nested" / "deep" / "dir"
    result = await call_tool(
        tmp_path, "create_directory", {"path": str(new_dir)}
    )
    assert not result.isError
    assert new_dir.is_dir()


@pytest.mark.asyncio
async def test_create_directory_existing(tmp_path):
    """create_directory on an existing directory does not raise an error."""
    existing = tmp_path / "already_exists"
    existing.mkdir()

    result = await call_tool(
        tmp_path, "create_directory", {"path": str(existing)}
    )
    assert not result.isError
    assert existing.is_dir()
