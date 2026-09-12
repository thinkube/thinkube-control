"""The notebook forwards: each endpoint calls the right tk-notebook-mcp tool with the right arguments.

``call_tool`` is replaced by a recorder, so no server is needed.
"""

import asyncio

import app.api.jupyter_notebooks as jn
from app.api.jupyter_notebooks import (
    CellExecuteRequest,
    CellInsertRequest,
    ExecuteAllRequest,
    ExecuteCodeRequest,
    UseNotebookRequest,
)


def _record(monkeypatch):
    calls = []

    async def rec(tool, arguments, timeout=jn.QUICK_TIMEOUT, server_name=""):
        calls.append({"tool": tool, "args": arguments, "timeout": timeout, "server": server_name})
        return {"success": True}

    monkeypatch.setattr(jn, "call_tool", rec)
    return calls


def test_use_notebook_forwards_kernel_and_flags(monkeypatch):
    calls = _record(monkeypatch)
    asyncio.run(jn.jupyter_use_notebook(
        UseNotebookRequest(notebook_path="nb.ipynb", kernel_name="agent-dev", create="true"), current_user={}))
    c = calls[0]
    assert c["tool"] == "use_notebook"
    assert c["args"] == {"notebook_path": "nb.ipynb", "create": True, "needs_gpu": False, "kernel_name": "agent-dev"}
    assert c["timeout"] == jn.OPEN_TIMEOUT


def test_execute_cell_waits_longer_than_the_cell(monkeypatch):
    calls = _record(monkeypatch)
    asyncio.run(jn.jupyter_execute_cell(
        CellExecuteRequest(cell_index="2", notebook_path="nb.ipynb", timeout_seconds="120"), current_user={}))
    c = calls[0]
    assert c["tool"] == "execute_cell"
    assert c["args"] == {"notebook_path": "nb.ipynb", "cell_index": 2, "timeout_seconds": 120}
    assert c["timeout"] == 120 + jn.RUN_MARGIN


def test_execute_cell_async_returns_quickly(monkeypatch):
    calls = _record(monkeypatch)
    asyncio.run(jn.jupyter_execute_cell_async(
        CellExecuteRequest(cell_index=5, notebook_path="nb.ipynb"), current_user={}))
    c = calls[0]
    assert c["tool"] == "execute_cell_async"
    assert c["args"] == {"notebook_path": "nb.ipynb", "cell_index": 5}
    assert c["timeout"] == jn.QUICK_TIMEOUT


def test_status_polls(monkeypatch):
    calls = _record(monkeypatch)
    asyncio.run(jn.jupyter_check_execution_status(execution_id="abc", current_user={}))
    asyncio.run(jn.jupyter_check_all_cells_status(execution_id="xyz", current_user={}))
    assert [c["tool"] for c in calls] == ["check_execution_status", "check_all_cells_status"]
    assert calls[0]["args"] == {"execution_id": "abc"}
    assert calls[1]["args"] == {"execution_id": "xyz"}


def test_execute_all_forwards_flags(monkeypatch):
    calls = _record(monkeypatch)
    asyncio.run(jn.jupyter_execute_all_cells(
        ExecuteAllRequest(notebook_path="nb.ipynb", restart_kernel=True, stop_on_error="false"), current_user={}))
    assert calls[0]["args"] == {"notebook_path": "nb.ipynb", "restart_kernel": True, "stop_on_error": False}


def test_insert_cell_positions(monkeypatch):
    calls = _record(monkeypatch)
    asyncio.run(jn.jupyter_insert_cell(
        CellInsertRequest(notebook_path="nb.ipynb", content="x = 1", position="below", cell_index="3"), current_user={}))
    assert calls[0]["tool"] == "insert_cell"
    assert calls[0]["args"] == {"notebook_path": "nb.ipynb", "content": "x = 1", "cell_type": "code", "position": "below", "cell_index": 3}


def test_execute_code_uses_execute_ipython(monkeypatch):
    calls = _record(monkeypatch)
    asyncio.run(jn.jupyter_execute_code(ExecuteCodeRequest(notebook_path="nb.ipynb", code="1+1"), current_user={}))
    assert calls[0]["tool"] == "execute_ipython"
    assert calls[0]["args"] == {"notebook_path": "nb.ipynb", "code": "1+1"}
