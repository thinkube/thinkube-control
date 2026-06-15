"""Async notebook execution exposed over MCP: submit + poll (SP-tgo0u1 SL-1).

Deterministic unit tests with _proxy_tool_call mocked — assert each endpoint
proxies the right tk-ai-extension tool with the right args and a short timeout,
and that the blocking endpoints are unchanged.
"""

import asyncio

import app.api.jupyter_notebooks as jn
from app.api.jupyter_notebooks import CellExecuteRequest


def _patch_proxy(monkeypatch):
    calls = []

    async def rec(tool_name, arguments, timeout=300.0):
        calls.append({"tool": tool_name, "args": arguments, "timeout": timeout})
        return {"ok": True}

    monkeypatch.setattr(jn, "_proxy_tool_call", rec)
    return calls


def test_execute_cell_async_submits_nonblocking(monkeypatch):
    calls = _patch_proxy(monkeypatch)
    asyncio.run(jn.jupyter_execute_cell_async(
        CellExecuteRequest(cell_index="5", notebook_path="nb.ipynb"), current_user={}))
    assert len(calls) == 1
    c = calls[0]
    assert c["tool"] == "execute_cell_async"
    assert c["args"] == {"notebook_path": "nb.ipynb", "cell_index": 5}
    # Submit returns immediately -> short timeout, NOT the 300s blocking default.
    assert c["timeout"] == jn._ASYNC_PROXY_TIMEOUT
    assert c["timeout"] < 300.0


def test_check_execution_status_polls(monkeypatch):
    calls = _patch_proxy(monkeypatch)
    asyncio.run(jn.jupyter_check_execution_status(execution_id="abc", current_user={}))
    c = calls[0]
    assert c["tool"] == "check_execution_status"
    assert c["args"] == {"execution_id": "abc"}
    assert c["timeout"] == jn._ASYNC_PROXY_TIMEOUT


def test_check_all_cells_status_polls(monkeypatch):
    calls = _patch_proxy(monkeypatch)
    asyncio.run(jn.jupyter_check_all_cells_status(execution_id="xyz", current_user={}))
    c = calls[0]
    assert c["tool"] == "check_all_cells_status"
    assert c["args"] == {"execution_id": "xyz"}
    assert c["timeout"] == jn._ASYNC_PROXY_TIMEOUT


def test_blocking_execute_cell_unchanged(monkeypatch):
    calls = _patch_proxy(monkeypatch)
    asyncio.run(jn.jupyter_execute_cell(
        CellExecuteRequest(cell_index="2", notebook_path="nb.ipynb"), current_user={}))
    c = calls[0]
    assert c["tool"] == "execute_cell"
    assert c["args"] == {"notebook_path": "nb.ipynb", "cell_index": 2}
    # Blocking path keeps the 300s default (no short-timeout override).
    assert c["timeout"] == 300.0
