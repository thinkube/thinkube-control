"""Notebook operations, forwarded to tk-notebook-mcp inside the notebook server.

Every endpoint here is a thin forward: the arguments go to the extension's
tool of the same purpose and the tool's own result comes back unchanged. The
extension needs no JupyterLab tab; ``jupyter_use_notebook`` starts the kernel
and the shared document, and the other tools work from there.

The notebook server's address and token are read from its pod, because a
JupyterHub service token does not authenticate to a single-user server.
Without a running server every forward answers 503 with the sentence that
says how to start one.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.api_tokens import get_current_user_dual_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jupyter/notebooks", tags=["jupyter-notebooks"])

EXTENSION_PREFIX = "/api/tk-notebook/mcp"
NO_SERVER = "No notebook server is running. Ask for start_notebook_server, or start one from Thinkube Notebooks."

# The extension's blocking tools wait for the kernel; the forward waits a little longer.
QUICK_TIMEOUT = 60.0
OPEN_TIMEOUT = 180.0
RUN_MARGIN = 30.0


# ---------------------------------------------------------------------------
# Reaching the server
# ---------------------------------------------------------------------------

def _load_kube():
    from kubernetes import client, config

    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api()


def find_server_pod(server_name: str = "") -> Optional[Any]:
    """The running single-user pod of one server: the default one, or a named one."""
    v1 = _load_kube()
    pods = v1.list_namespaced_pod("jupyterhub", label_selector="component=singleuser-server")
    for pod in pods.items:
        labels = pod.metadata.labels or {}
        if labels.get("hub.jupyter.org/servername", "") != server_name:
            continue
        if pod.metadata.deletion_timestamp is not None or pod.status.phase != "Running":
            continue
        return pod
    return None


def server_access(server_name: str = "") -> tuple[str, str]:
    """``(base_url, token)`` of a running server, from its pod."""
    try:
        pod = find_server_pod(server_name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("could not list notebook server pods: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"could not reach the Kubernetes API: {e}")
    if pod is None:
        raise HTTPException(status_code=503, detail=NO_SERVER)
    if not pod.status.pod_ip:
        raise HTTPException(status_code=503, detail="The notebook server pod has no address yet; it is still starting.")

    token = None
    prefix = ""
    for container in pod.spec.containers:
        for env in container.env or []:
            if env.name == "JUPYTERHUB_API_TOKEN":
                token = env.value
            elif env.name == "JUPYTERHUB_SERVICE_PREFIX":
                prefix = (env.value or "").rstrip("/")
    if not token:
        raise HTTPException(status_code=500, detail="The notebook server pod carries no API token.")
    return f"http://{pod.status.pod_ip}:8888{prefix}", token


async def extension_health(server_name: str = "") -> Dict[str, Any]:
    base_url, token = server_access(server_name)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{base_url}{EXTENSION_PREFIX}/health", headers={"Authorization": f"token {token}"})
    except httpx.HTTPError as e:
        return {"status": "unreachable", "error": str(e)}
    if response.status_code != 200:
        return {"status": "error", "error": f"health check answered HTTP {response.status_code}"}
    return {"status": "ok", **response.json()}


async def call_tool(tool: str, arguments: Dict[str, Any], timeout: float = QUICK_TIMEOUT, server_name: str = "") -> Any:
    """Forward one tool call and return the tool's own result."""
    base_url, token = server_access(server_name)
    url = f"{base_url}{EXTENSION_PREFIX}/tools/call"
    headers = {"Authorization": f"token {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json={"tool": tool, "arguments": arguments}, headers=headers)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=NO_SERVER)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"The notebook server did not answer '{tool}' within {int(timeout)} seconds.")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"The notebook server could not be reached for '{tool}': {e}")

    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code == 404:
        # A JSON 404 is the extension saying the tool is unknown; an HTML 404 is a
        # server without the extension at all. Either way the image is behind.
        raise HTTPException(
            status_code=501,
            detail=f"The notebook server does not have the tool '{tool}'; its image predates tk-notebook-mcp. Rebuild tk-jupyter-base and restart the server.",
        )
    if body is None:
        body = {"success": False, "error": response.text[:300]}
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=body.get("error") if isinstance(body, dict) else str(body))
    return body


def _int(value: Union[int, str, None], name: str) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{name} must be an integer")


def _bool(value: Union[bool, str, None], default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _opt_int(value: Union[int, str, None], name: str) -> Optional[int]:
    return None if value is None or value == "" else _int(value, name)


# ---------------------------------------------------------------------------
# Request and response models
# ---------------------------------------------------------------------------

NotebookPath = Field(..., description="Path of the notebook, relative to the notebooks folder (for example 'examples/research-assistant/00-platform-validation.ipynb').")
CellIndex = Field(..., description="0-based position of the cell (not its execution count).")
Position = Field("end", description="Where the new cell goes: 'end', or 'above' or 'below' cell_index.")
Timeout = Field(None, description="Seconds to wait for the cell; past it the kernel is interrupted. Default 600.")


class ToolResultResponse(BaseModel):
    result: Any = Field(..., description="The tool's own result: an object with 'success' and, on failure, 'error'.")


class UseNotebookRequest(BaseModel):
    notebook_path: str = NotebookPath
    kernel_name: Optional[str] = Field(None, description="Kernel to run it with, for example 'agent-dev' or 'fine-tuning'. Defaults to the one the notebook names.")
    create: Union[bool, str, None] = Field(False, description="Create the notebook if it does not exist.")
    needs_gpu: Union[bool, str, None] = Field(False, description="Refuse when the notebook server was started without a GPU.")


class CloseNotebookRequest(BaseModel):
    notebook_path: str = NotebookPath
    shutdown_kernel: Union[bool, str, None] = Field(True, description="Shut the kernel down (default true).")


class CreateNotebookRequest(BaseModel):
    notebook_path: str = Field(..., description="Path for the new notebook; without a folder it goes under the notebooks folder.")
    cells: Optional[List[Dict[str, str]]] = Field(None, description="Initial cells: [{cell_type: code|markdown, source: ...}].")


class CellInsertRequest(BaseModel):
    notebook_path: str = NotebookPath
    content: str = Field(..., description="The cell's source.")
    cell_type: str = Field("code", description="'code' or 'markdown'.")
    position: str = Position
    cell_index: Union[int, str, None] = Field(None, description="The cell that 'above' or 'below' refers to.")


class CellOverwriteRequest(BaseModel):
    notebook_path: str = NotebookPath
    cell_index: Union[int, str] = CellIndex
    content: str = Field(..., description="The new source.")


class CellDeleteRequest(BaseModel):
    notebook_path: str = NotebookPath
    cell_index: Union[int, str] = CellIndex


class CellMoveRequest(BaseModel):
    notebook_path: str = NotebookPath
    from_index: Union[int, str] = Field(..., description="Where the cell is now (0-based).")
    to_index: Union[int, str] = Field(..., description="Where it should end up (0-based).")


class CellExecuteRequest(BaseModel):
    notebook_path: str = NotebookPath
    cell_index: Union[int, str] = CellIndex
    timeout_seconds: Union[int, str, None] = Timeout


class InsertAndExecuteRequest(BaseModel):
    notebook_path: str = NotebookPath
    content: str = Field(..., description="The code to insert and run.")
    position: str = Position
    cell_index: Union[int, str, None] = Field(None, description="The cell that 'above' or 'below' refers to.")
    timeout_seconds: Union[int, str, None] = Timeout


class ExecuteAllRequest(BaseModel):
    notebook_path: str = NotebookPath
    restart_kernel: Union[bool, str, None] = Field(False, description="Restart the kernel before the run.")
    stop_on_error: Union[bool, str, None] = Field(True, description="Stop at the first cell that raises (default true).")
    cell_timeout_seconds: Union[int, str, None] = Field(None, description="Seconds allowed per cell; default 3600.")


class ExecuteCodeRequest(BaseModel):
    notebook_path: str = Field(..., description="The notebook whose kernel runs the code.")
    code: str = Field(..., description="Python or IPython code. The notebook itself is not changed.")
    timeout_seconds: Union[int, str, None] = Field(None, description="Seconds to wait; default 300.")


class KernelRequest(BaseModel):
    notebook_path: str = Field(..., description="The notebook whose kernel is meant.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/list", response_model=ToolResultResponse, operation_id="jupyter_list_notebooks")
async def jupyter_list_notebooks(current_user: dict = Depends(get_current_user_dual_auth)):
    """List every notebook under the notebooks folder, with the kernel each open one uses."""
    return ToolResultResponse(result=await call_tool("list_notebooks", {}))


@router.post("/use", response_model=ToolResultResponse, operation_id="jupyter_use_notebook")
async def jupyter_use_notebook(request: UseNotebookRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Open a notebook for work: start its kernel and shared document. Call this before running or editing cells. No JupyterLab tab is needed."""
    args: Dict[str, Any] = {
        "notebook_path": request.notebook_path,
        "create": _bool(request.create, False),
        "needs_gpu": _bool(request.needs_gpu, False),
    }
    if request.kernel_name:
        args["kernel_name"] = request.kernel_name
    return ToolResultResponse(result=await call_tool("use_notebook", args, timeout=OPEN_TIMEOUT))


@router.post("/close", response_model=ToolResultResponse, operation_id="jupyter_close_notebook")
async def jupyter_close_notebook(request: CloseNotebookRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Finish with a notebook: save it and shut its kernel down."""
    args = {"notebook_path": request.notebook_path, "shutdown_kernel": _bool(request.shutdown_kernel, True)}
    return ToolResultResponse(result=await call_tool("close_notebook", args))


@router.post("/create", response_model=ToolResultResponse, operation_id="jupyter_create_notebook")
async def jupyter_create_notebook(request: CreateNotebookRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Create a notebook file, with optional initial cells."""
    args: Dict[str, Any] = {"notebook_path": request.notebook_path}
    if request.cells:
        args["cells"] = request.cells
    return ToolResultResponse(result=await call_tool("create_notebook", args))


@router.get("/{notebook_path:path}/cells/{cell_index}", response_model=ToolResultResponse, operation_id="jupyter_read_cell")
async def jupyter_read_cell(notebook_path: str, cell_index: int, current_user: dict = Depends(get_current_user_dual_auth)):
    """Read one cell: its source and, for code, its execution count and outputs."""
    return ToolResultResponse(result=await call_tool("read_cell", {"notebook_path": notebook_path, "cell_index": cell_index}))


@router.get("/{notebook_path:path}/cells", response_model=ToolResultResponse, operation_id="jupyter_list_cells")
async def jupyter_list_cells(notebook_path: str, current_user: dict = Depends(get_current_user_dual_auth)):
    """List the cells of a notebook: index, type, execution count and first line."""
    return ToolResultResponse(result=await call_tool("list_cells", {"notebook_path": notebook_path}))


@router.post("/insert-cell", response_model=ToolResultResponse, operation_id="jupyter_insert_cell")
async def jupyter_insert_cell(request: CellInsertRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Insert a code or markdown cell at the end, or above or below a given cell."""
    args: Dict[str, Any] = {
        "notebook_path": request.notebook_path,
        "content": request.content,
        "cell_type": request.cell_type,
        "position": request.position,
    }
    index = _opt_int(request.cell_index, "cell_index")
    if index is not None:
        args["cell_index"] = index
    return ToolResultResponse(result=await call_tool("insert_cell", args))


@router.post("/overwrite-cell", response_model=ToolResultResponse, operation_id="jupyter_overwrite_cell")
async def jupyter_overwrite_cell(request: CellOverwriteRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Replace a cell's source."""
    args = {"notebook_path": request.notebook_path, "cell_index": _int(request.cell_index, "cell_index"), "content": request.content}
    return ToolResultResponse(result=await call_tool("overwrite_cell", args))


@router.post("/delete-cell", response_model=ToolResultResponse, operation_id="jupyter_delete_cell")
async def jupyter_delete_cell(request: CellDeleteRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Delete a cell."""
    args = {"notebook_path": request.notebook_path, "cell_index": _int(request.cell_index, "cell_index")}
    return ToolResultResponse(result=await call_tool("delete_cell", args))


@router.post("/move-cell", response_model=ToolResultResponse, operation_id="jupyter_move_cell")
async def jupyter_move_cell(request: CellMoveRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Move a cell to another index."""
    args = {
        "notebook_path": request.notebook_path,
        "from_index": _int(request.from_index, "from_index"),
        "to_index": _int(request.to_index, "to_index"),
    }
    return ToolResultResponse(result=await call_tool("move_cell", args))


@router.post("/execute-cell", response_model=ToolResultResponse, operation_id="jupyter_execute_cell")
async def jupyter_execute_cell(request: CellExecuteRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Run one code cell and return its outputs once it finishes. For a cell that runs for many minutes use jupyter_execute_cell_async."""
    timeout = _opt_int(request.timeout_seconds, "timeout_seconds")
    args: Dict[str, Any] = {"notebook_path": request.notebook_path, "cell_index": _int(request.cell_index, "cell_index")}
    if timeout is not None:
        args["timeout_seconds"] = timeout
    return ToolResultResponse(result=await call_tool("execute_cell", args, timeout=(timeout or 600) + RUN_MARGIN))


@router.post("/insert-and-execute", response_model=ToolResultResponse, operation_id="jupyter_insert_and_execute_cell")
async def jupyter_insert_and_execute_cell(request: InsertAndExecuteRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Insert a code cell and run it in one step."""
    timeout = _opt_int(request.timeout_seconds, "timeout_seconds")
    args: Dict[str, Any] = {"notebook_path": request.notebook_path, "content": request.content, "position": request.position}
    index = _opt_int(request.cell_index, "cell_index")
    if index is not None:
        args["cell_index"] = index
    if timeout is not None:
        args["timeout_seconds"] = timeout
    return ToolResultResponse(result=await call_tool("insert_and_execute_cell", args, timeout=(timeout or 600) + RUN_MARGIN))


@router.post("/execute-all", response_model=ToolResultResponse, operation_id="jupyter_execute_all_cells")
async def jupyter_execute_all_cells(request: ExecuteAllRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Run every code cell in order, in the background. Returns an execution_id at once; poll jupyter_check_all_cells_status."""
    args: Dict[str, Any] = {
        "notebook_path": request.notebook_path,
        "restart_kernel": _bool(request.restart_kernel, False),
        "stop_on_error": _bool(request.stop_on_error, True),
    }
    timeout = _opt_int(request.cell_timeout_seconds, "cell_timeout_seconds")
    if timeout is not None:
        args["cell_timeout_seconds"] = timeout
    return ToolResultResponse(result=await call_tool("execute_all_cells", args))


@router.post("/execute-cell-async", response_model=ToolResultResponse, operation_id="jupyter_execute_cell_async")
async def jupyter_execute_cell_async(request: CellExecuteRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Start one code cell in the background. Returns an execution_id at once; poll jupyter_check_execution_status."""
    args: Dict[str, Any] = {"notebook_path": request.notebook_path, "cell_index": _int(request.cell_index, "cell_index")}
    timeout = _opt_int(request.timeout_seconds, "timeout_seconds")
    if timeout is not None:
        args["timeout_seconds"] = timeout
    return ToolResultResponse(result=await call_tool("execute_cell_async", args))


@router.get("/execution-status", response_model=ToolResultResponse, operation_id="jupyter_check_execution_status")
async def jupyter_check_execution_status(
    execution_id: str = Query(..., description="The id jupyter_execute_cell_async returned."),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """The state of a background cell run: running, completed or error, with the outputs once it ends."""
    return ToolResultResponse(result=await call_tool("check_execution_status", {"execution_id": execution_id}))


@router.get("/all-cells-status", response_model=ToolResultResponse, operation_id="jupyter_check_all_cells_status")
async def jupyter_check_all_cells_status(
    execution_id: str = Query(..., description="The id jupyter_execute_all_cells returned."),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Progress of a run started by jupyter_execute_all_cells: cells done, the one running, each cell's result."""
    return ToolResultResponse(result=await call_tool("check_all_cells_status", {"execution_id": execution_id}))


@router.post("/execute-code", response_model=ToolResultResponse, operation_id="jupyter_execute_code")
async def jupyter_execute_code(request: ExecuteCodeRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Run code in a notebook's kernel without changing the notebook: inspect a variable, check a package, try a line."""
    timeout = _opt_int(request.timeout_seconds, "timeout_seconds")
    args: Dict[str, Any] = {"notebook_path": request.notebook_path, "code": request.code}
    if timeout is not None:
        args["timeout_seconds"] = timeout
    return ToolResultResponse(result=await call_tool("execute_ipython", args, timeout=(timeout or 300) + RUN_MARGIN))


@router.post("/restart-kernel", response_model=ToolResultResponse, operation_id="jupyter_restart_kernel")
async def jupyter_restart_kernel(request: KernelRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Restart a notebook's kernel: variables are lost, cells and outputs stay."""
    return ToolResultResponse(result=await call_tool("restart_kernel", {"notebook_path": request.notebook_path}, timeout=OPEN_TIMEOUT))


@router.post("/interrupt-kernel", response_model=ToolResultResponse, operation_id="jupyter_interrupt_kernel")
async def jupyter_interrupt_kernel(request: KernelRequest, current_user: dict = Depends(get_current_user_dual_auth)):
    """Interrupt what a notebook's kernel is running, as Ctrl-C would."""
    return ToolResultResponse(result=await call_tool("interrupt_kernel", {"notebook_path": request.notebook_path}))


@router.get("/kernel-status", response_model=ToolResultResponse, operation_id="jupyter_kernel_status")
async def jupyter_kernel_status(
    notebook_path: str = Query(..., description="The notebook whose kernel is meant."),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Whether a notebook's kernel is idle or busy."""
    return ToolResultResponse(result=await call_tool("get_kernel_status", {"notebook_path": notebook_path}))


@router.get("/kernels", response_model=ToolResultResponse, operation_id="jupyter_list_kernels")
async def jupyter_list_kernels(current_user: dict = Depends(get_current_user_dual_auth)):
    """The kernels running now, with their notebooks, and the kernel types installed."""
    return ToolResultResponse(result=await call_tool("list_kernels", {}))
