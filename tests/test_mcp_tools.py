"""In-memory MCP client tests for all 12 filesystem tools.

Each test creates its own server+client inline (no shared fixture)
to avoid pytest-asyncio cancel-scope teardown errors.
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
# Phase 1 core tool tests
# ---------------------------------------------------------------------------


async def test_read_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world", encoding="utf-8")
    result = await call_tool(tmp_path, "read_file", {"path": str(f)})
    assert not result.isError
    assert result.content[0].text == "hello world"


async def test_write_file(tmp_path):
    dest = tmp_path / "written.txt"
    result = await call_tool(tmp_path, "write_file", {"path": str(dest), "content": "written content"})
    assert not result.isError
    assert dest.read_text(encoding="utf-8") == "written content"


async def test_list_directory(tmp_path):
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    result = await call_tool(tmp_path, "list_directory", {"path": str(tmp_path)})
    assert not result.isError
    text = result.content[0].text
    assert "[FILE] file.txt" in text
    assert "[DIR] subdir" in text


async def test_create_directory(tmp_path):
    new_dir = tmp_path / "new" / "nested"
    result = await call_tool(tmp_path, "create_directory", {"path": str(new_dir)})
    assert not result.isError
    assert new_dir.is_dir()


# ---------------------------------------------------------------------------
# Phase 2 extended tool tests — get_file_info
# ---------------------------------------------------------------------------


async def test_get_file_info(tmp_path):
    content = "file content here"
    f = tmp_path / "info_test.txt"
    f.write_text(content, encoding="utf-8")
    result = await call_tool(tmp_path, "get_file_info", {"path": str(f)})
    assert not result.isError
    data = json.loads(result.content[0].text)
    assert data["type"] == "file"
    assert data["size"] == len(content.encode("utf-8"))
    assert "modified" in data


async def test_get_file_info_directory(tmp_path):
    d = tmp_path / "mydir"
    d.mkdir()
    result = await call_tool(tmp_path, "get_file_info", {"path": str(d)})
    assert not result.isError
    data = json.loads(result.content[0].text)
    assert data["type"] == "directory"


# ---------------------------------------------------------------------------
# Phase 2 extended tool tests — move_file
# ---------------------------------------------------------------------------


async def test_move_file(tmp_path):
    src = tmp_path / "source.txt"
    src.write_text("move me", encoding="utf-8")
    dst = tmp_path / "destination.txt"
    result = await call_tool(tmp_path, "move_file", {"source": str(src), "destination": str(dst)})
    assert not result.isError
    assert not src.exists()
    assert dst.exists()
    assert dst.read_text(encoding="utf-8") == "move me"


async def test_move_file_destination_exists(tmp_path):
    src = tmp_path / "source.txt"
    src.write_text("source", encoding="utf-8")
    dst = tmp_path / "existing.txt"
    dst.write_text("already here", encoding="utf-8")
    result = await call_tool(tmp_path, "move_file", {"source": str(src), "destination": str(dst)})
    assert result.isError
    assert "already exists" in result.content[0].text.lower() or "FileExistsError" in result.content[0].text


# ---------------------------------------------------------------------------
# Phase 2 extended tool tests — delete_file
# ---------------------------------------------------------------------------


async def test_delete_file(tmp_path):
    f = tmp_path / "to_delete.txt"
    f.write_text("goodbye", encoding="utf-8")
    result = await call_tool(tmp_path, "delete_file", {"path": str(f)})
    assert not result.isError
    assert not f.exists()


async def test_delete_file_directory_rejected(tmp_path):
    d = tmp_path / "a_directory"
    d.mkdir()
    result = await call_tool(tmp_path, "delete_file", {"path": str(d)})
    assert result.isError
    assert "directory" in result.content[0].text.lower() or "IsADirectoryError" in result.content[0].text


# ---------------------------------------------------------------------------
# Phase 2 extended tool tests — search_files
# ---------------------------------------------------------------------------


async def test_search_files(tmp_path):
    (tmp_path / "a.txt").write_text("text", encoding="utf-8")
    (tmp_path / "b.txt").write_text("text", encoding="utf-8")
    (tmp_path / "c.py").write_text("code", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.txt").write_text("text", encoding="utf-8")
    result = await call_tool(tmp_path, "search_files", {"path": str(tmp_path), "pattern": "**/*.txt"})
    assert not result.isError
    text = result.content[0].text
    assert ".txt" in text
    assert ".py" not in text


async def test_search_files_max_results(tmp_path):
    for i in range(5):
        (tmp_path / f"file{i}.txt").write_text("x", encoding="utf-8")
    result = await call_tool(
        tmp_path, "search_files", {"path": str(tmp_path), "pattern": "*.txt", "max_results": 2}
    )
    assert not result.isError
    lines = [l for l in result.content[0].text.splitlines() if l.strip()]
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# Phase 2 extended tool tests — grep_files
# ---------------------------------------------------------------------------


async def test_grep_files(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("line one\nhello world\nline three\n", encoding="utf-8")
    result = await call_tool(tmp_path, "grep_files", {"path": str(tmp_path), "pattern": "hello"})
    assert not result.isError
    text = result.content[0].text
    # Verify path:lineno:content format
    assert "hello world" in text
    # Should contain the file path and line number 2
    assert "2" in text


async def test_grep_files_regex(tmp_path):
    f = tmp_path / "funcs.py"
    f.write_text("def foo():\n    pass\ndef bar():\n    pass\n", encoding="utf-8")
    result = await call_tool(tmp_path, "grep_files", {"path": str(tmp_path), "pattern": r"def \w+"})
    assert not result.isError
    text = result.content[0].text
    assert "def foo" in text
    assert "def bar" in text


# ---------------------------------------------------------------------------
# Phase 2 extended tool tests — directory_tree
# ---------------------------------------------------------------------------


async def test_directory_tree(tmp_path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    (tmp_path / "root.txt").write_text("r", encoding="utf-8")
    (sub / "nested.txt").write_text("n", encoding="utf-8")
    result = await call_tool(tmp_path, "directory_tree", {"path": str(tmp_path)})
    assert not result.isError
    data = json.loads(result.content[0].text)
    assert "children" in data
    child_names = [c["name"] for c in data["children"]]
    assert "subdir" in child_names
    assert "root.txt" in child_names


async def test_directory_tree_max_depth(tmp_path):
    level1 = tmp_path / "level1"
    level1.mkdir()
    level2 = level1 / "level2"
    level2.mkdir()
    level3 = level2 / "level3"
    level3.mkdir()
    result = await call_tool(tmp_path, "directory_tree", {"path": str(tmp_path), "max_depth": 1})
    assert not result.isError
    data = json.loads(result.content[0].text)
    # At max_depth=1, level1 appears but its children should be empty list
    assert "children" in data
    level1_node = next(c for c in data["children"] if c["name"] == "level1")
    assert level1_node["children"] == []


async def test_directory_tree_exclude(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("git config", encoding="utf-8")
    (tmp_path / "main.py").write_text("code", encoding="utf-8")
    result = await call_tool(
        tmp_path, "directory_tree", {"path": str(tmp_path), "exclude_patterns": [".git"]}
    )
    assert not result.isError
    data = json.loads(result.content[0].text)
    child_names = [c["name"] for c in data["children"]]
    assert ".git" not in child_names
    assert "main.py" in child_names


# ---------------------------------------------------------------------------
# Phase 2 extended tool tests — read_multiple_files
# ---------------------------------------------------------------------------


async def test_read_multiple_files(tmp_path):
    f1 = tmp_path / "file1.txt"
    f2 = tmp_path / "file2.txt"
    f1.write_text("content one", encoding="utf-8")
    f2.write_text("content two", encoding="utf-8")
    result = await call_tool(
        tmp_path, "read_multiple_files", {"paths": [str(f1), str(f2)]}
    )
    assert not result.isError
    data = json.loads(result.content[0].text)
    assert data[str(f1)] == "content one"
    assert data[str(f2)] == "content two"


async def test_read_multiple_files_partial_failure(tmp_path):
    f_valid = tmp_path / "exists.txt"
    f_valid.write_text("valid content", encoding="utf-8")
    f_missing = str(tmp_path / "nonexistent.txt")
    result = await call_tool(
        tmp_path, "read_multiple_files", {"paths": [str(f_valid), f_missing]}
    )
    assert not result.isError
    data = json.loads(result.content[0].text)
    assert data[str(f_valid)] == "valid content"
    assert data[f_missing].startswith("ERROR")


# ---------------------------------------------------------------------------
# Phase 2 extended tool tests — list_allowed_directories
# ---------------------------------------------------------------------------


async def test_list_allowed_directories(tmp_path):
    result = await call_tool(tmp_path, "list_allowed_directories", {})
    assert not result.isError
    text = result.content[0].text
    # The resolved tmp_path (accounting for macOS /tmp -> /private/tmp symlink)
    resolved = str(tmp_path.resolve())
    assert resolved in text
