"""API endpoints that proxy Jupyter notebook tools from tk-ai-extension.

These endpoints forward requests to the tk-ai-extension running inside the
JupyterHub notebook server, making Jupyter notebook manipulation tools
available via thinkube-control's MCP server to Claude Code.

The notebook server URL is constructed from:
- DOMAIN_NAME env var (e.g., thinkube.com)
- ADMIN_USERNAME env var (e.g., tkadmin) — the single Thinkube user
- JUPYTERHUB_API_TOKEN env var — for authenticating to the notebook server

When the Jupyter server is not running, endpoints return clear errors rather
than failing silently.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field



# No custom types needed — integer fields declared as str below
# and converted to int in the proxy function

from app.core.api_tokens import get_current_user_dual_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jupyter/notebooks", tags=["jupyter-notebooks"])


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _get_notebook_server_access() -> tuple[str, str]:
    """Get the notebook server URL and token by reading from the running pod.

    JupyterHub service tokens don't authenticate to the single-user server.
    Instead, we read the notebook server's own API token and pod IP from
    Kubernetes directly.

    Returns:
        (base_url, token) tuple
    """
    try:
        from kubernetes import client, config
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        v1 = client.CoreV1Api()

        # Find the notebook server pod
        pods = v1.list_namespaced_pod(
            "jupyterhub",
            label_selector="component=singleuser-server"
        )

        if not pods.items:
            # Try by pod name pattern
            pods = v1.list_namespaced_pod("jupyterhub")
            pods.items = [p for p in pods.items if p.metadata.name.startswith("jupyter-")]

        if not pods.items:
            raise HTTPException(
                status_code=503,
                detail="No Jupyter notebook server running. Start it from the JupyterHub launcher."
            )

        pod = pods.items[0]
        pod_ip = pod.status.pod_ip
        if not pod_ip:
            raise HTTPException(status_code=503, detail="Notebook server pod has no IP yet (still starting).")

        # Extract the JUPYTERHUB_API_TOKEN from the pod's environment
        token = None
        for container in pod.spec.containers:
            for env in (container.env or []):
                if env.name == "JUPYTERHUB_API_TOKEN":
                    token = env.value
                    break
            if token:
                break

        if not token:
            raise HTTPException(status_code=500, detail="Could not read notebook server API token")

        # The notebook server listens on port 8888 with a base URL
        # Read JUPYTERHUB_SERVICE_PREFIX for the base path
        base_path = ""
        for container in pod.spec.containers:
            for env in (container.env or []):
                if env.name == "JUPYTERHUB_SERVICE_PREFIX":
                    base_path = env.value.rstrip("/")
                    break

        base_url = f"http://{pod_ip}:8888{base_path}"
        return base_url, token

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get notebook server access: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to access notebook server: {str(e)}")


async def _proxy_tool_call(tool_name: str, arguments: dict, timeout: float = 300.0) -> dict:
    """Forward a tool call to the tk-ai-extension in the notebook server."""
    base_url, token = _get_notebook_server_access()

    url = f"{base_url}/api/tk-ai/mcp/tools/call"
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            response = await client.post(
                url,
                json={"tool": tool_name, "arguments": arguments},
                headers=headers,
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Jupyter tool call failed: HTTP {response.status_code} - {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Jupyter server returned: {response.text}"
                )

    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="Jupyter notebook server is not running. Start it from the JupyterHub launcher."
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail=f"Jupyter notebook server timed out while executing '{tool_name}'."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error proxying tool call to Jupyter: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class JupyterStatusResponse(BaseModel):
    status: str = Field(..., description="Status: ok, unreachable, not_configured")
    jupyter_url: Optional[str] = None
    service: Optional[str] = None
    version: Optional[str] = None
    error: Optional[str] = None


class NotebookListResponse(BaseModel):
    result: str = Field(..., description="Formatted list of notebooks")


class CellListResponse(BaseModel):
    result: Any = Field(..., description="Cell listing result from Jupyter")


class CellReadResponse(BaseModel):
    result: Any = Field(..., description="Cell content from Jupyter")


class CellExecuteRequest(BaseModel):
    cell_index: str = Field(..., description="Index of the cell to execute (0-based)")
    notebook_path: str = Field(..., description="Path to the notebook")


class CellExecuteResponse(BaseModel):
    result: Any = Field(..., description="Execution result from Jupyter")


class CellInsertRequest(BaseModel):
    content: str = Field(..., description="Content/source code for the new cell")
    notebook_path: str = Field(..., description="Path to the notebook")
    cell_type: str = Field("code", description="Type of cell: code or markdown")
    position: str = Field("end", description="Where to insert: above, below, end")


class CellOverwriteRequest(BaseModel):
    cell_index: str = Field(..., description="Index of the cell to overwrite (0-based)")
    content: str = Field(..., description="New content for the cell")
    notebook_path: str = Field(..., description="Path to the notebook")


class CellDeleteRequest(BaseModel):
    cell_index: str = Field(..., description="Index of the cell to delete (0-based)")
    notebook_path: str = Field(..., description="Path to the notebook")


class CellMoveRequest(BaseModel):
    from_index: str = Field(..., description="Current index of the cell (0-based)")
    to_index: str = Field(..., description="Target index for the cell (0-based)")
    notebook_path: str = Field(..., description="Path to the notebook")


class CreateNotebookRequest(BaseModel):
    path: str = Field(..., description="Path for the new notebook")
    cells: Optional[List[Dict[str, str]]] = Field(None, description="Initial cells [{cell_type, source}]")


class InsertAndExecuteRequest(BaseModel):
    content: str = Field(..., description="Code to insert and execute")
    notebook_path: str = Field(..., description="Path to the notebook")
    position: str = Field("below", description="Where to insert: above, below, end")


class ExecuteAllRequest(BaseModel):
    notebook_path: str = Field(..., description="Path to the notebook")


class ToolResultResponse(BaseModel):
    result: Any = Field(..., description="Tool execution result")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status", response_model=JupyterStatusResponse, operation_id="jupyter_notebook_status")
async def jupyter_notebook_status(
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Check if the Jupyter notebook server is running and the tk-ai-extension is accessible."""
    try:
        base_url, token = _get_notebook_server_access()
    except HTTPException as e:
        return JupyterStatusResponse(
            status="not_configured" if e.status_code == 500 else "unreachable",
            error=e.detail,
        )

    url = f"{base_url}/api/tk-ai/mcp/health"
    headers = {"Authorization": f"token {token}"}

    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            response = await client.get(url, headers=headers)

            if response.status_code == 200:
                data = response.json()
                return JupyterStatusResponse(
                    status="ok",
                    jupyter_url=base_url,
                    service=data.get("service"),
                    version=data.get("version"),
                )
            else:
                return JupyterStatusResponse(
                    status="error",
                    jupyter_url=base_url,
                    error=f"Health check returned HTTP {response.status_code}",
                )
    except httpx.ConnectError:
        return JupyterStatusResponse(
            status="unreachable",
            jupyter_url=base_url,
            error="Jupyter server is not running. Start it from the JupyterHub launcher.",
        )
    except Exception as e:
        return JupyterStatusResponse(
            status="error",
            jupyter_url=base_url,
            error=str(e),
        )


@router.get("/list", response_model=ToolResultResponse, operation_id="jupyter_list_notebooks")
async def jupyter_list_notebooks(
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """List all .ipynb notebook files in the Jupyter notebooks directory."""
    result = await _proxy_tool_call("list_notebooks", {})
    return ToolResultResponse(result=result)


@router.get("/{notebook_path:path}/cells", response_model=ToolResultResponse, operation_id="jupyter_list_cells")
async def jupyter_list_cells(
    notebook_path: str,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """List all cells in a notebook with their indices, types, and source preview."""
    result = await _proxy_tool_call("list_cells", {"notebook_path": notebook_path})
    return ToolResultResponse(result=result)


@router.get("/{notebook_path:path}/cells/{cell_index}", response_model=ToolResultResponse, operation_id="jupyter_read_cell")
async def jupyter_read_cell(
    notebook_path: str,
    cell_index: int,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Read the content and outputs of a specific cell by index."""
    result = await _proxy_tool_call("read_cell", {
        "notebook_path": notebook_path,
        "cell_index": cell_index,
    })
    return ToolResultResponse(result=result)


@router.post("/execute-cell", response_model=ToolResultResponse, operation_id="jupyter_execute_cell")
async def jupyter_execute_cell(
    request: CellExecuteRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Execute a code cell and return its output."""
    result = await _proxy_tool_call("execute_cell", {
        "notebook_path": request.notebook_path,
        "cell_index": int(request.cell_index),
    })
    return ToolResultResponse(result=result)


@router.post("/insert-cell", response_model=ToolResultResponse, operation_id="jupyter_insert_cell")
async def jupyter_insert_cell(
    request: CellInsertRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Insert a new cell into the notebook."""
    result = await _proxy_tool_call("insert_cell", {
        "notebook_path": request.notebook_path,
        "content": request.content,
        "cell_type": request.cell_type,
        "position": request.position,
    })
    return ToolResultResponse(result=result)


@router.post("/overwrite-cell", response_model=ToolResultResponse, operation_id="jupyter_overwrite_cell")
async def jupyter_overwrite_cell(
    request: CellOverwriteRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Overwrite the source content of a cell."""
    result = await _proxy_tool_call("overwrite_cell", {
        "notebook_path": request.notebook_path,
        "cell_index": int(request.cell_index),
        "content": request.content,
    })
    return ToolResultResponse(result=result)


@router.post("/delete-cell", response_model=ToolResultResponse, operation_id="jupyter_delete_cell")
async def jupyter_delete_cell(
    request: CellDeleteRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Delete a cell from the notebook."""
    result = await _proxy_tool_call("delete_cell", {
        "notebook_path": request.notebook_path,
        "cell_index": int(request.cell_index),
    })
    return ToolResultResponse(result=result)


@router.post("/move-cell", response_model=ToolResultResponse, operation_id="jupyter_move_cell")
async def jupyter_move_cell(
    request: CellMoveRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Move a cell from one position to another."""
    result = await _proxy_tool_call("move_cell", {
        "notebook_path": request.notebook_path,
        "from_index": int(request.from_index),
        "to_index": int(request.to_index),
    })
    return ToolResultResponse(result=result)


@router.post("/create", response_model=ToolResultResponse, operation_id="jupyter_create_notebook")
async def jupyter_create_notebook(
    request: CreateNotebookRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Create a new Jupyter notebook."""
    args = {"path": request.path}
    if request.cells:
        args["cells"] = request.cells
    result = await _proxy_tool_call("create_notebook", args)
    return ToolResultResponse(result=result)


@router.post("/insert-and-execute", response_model=ToolResultResponse, operation_id="jupyter_insert_and_execute_cell")
async def jupyter_insert_and_execute_cell(
    request: InsertAndExecuteRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Insert a new code cell and execute it immediately."""
    result = await _proxy_tool_call("insert_and_execute_cell", {
        "notebook_path": request.notebook_path,
        "content": request.content,
        "position": request.position,
    })
    return ToolResultResponse(result=result)


@router.post("/execute-all", response_model=ToolResultResponse, operation_id="jupyter_execute_all_cells")
async def jupyter_execute_all_cells(
    request: ExecuteAllRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Execute all code cells in the notebook sequentially."""
    result = await _proxy_tool_call("execute_all_cells", {
        "notebook_path": request.notebook_path,
    })
    return ToolResultResponse(result=result)
