"""Node management API endpoints for discovering, adding, and removing cluster nodes."""

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel

import yaml

from app.services.ansible_environment import ansible_env
from app.services.node_manager import node_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/nodes", tags=["nodes"])


class DiscoverRequest(BaseModel):
    ip: str
    username: Optional[str] = None


class AddNodeRequest(BaseModel):
    hostname: str
    ip: str
    architecture: str
    zerotier_ip: Optional[str] = None
    lan_ip: Optional[str] = None
    gpu_detected: bool = False
    gpu_count: int = 0
    gpu_model: str = ""


class RemoveNodeRequest(BaseModel):
    hostname: str
    drain: bool = True


@router.get("/list")
async def list_nodes():
    """List all cluster nodes with architecture, role, status, and resources."""
    try:
        nodes = node_manager.get_cluster_nodes()
        architectures = sorted(set(n["architecture"] for n in nodes if n["architecture"] != "unknown"))
        return {
            "nodes": nodes,
            "architectures": architectures,
            "node_count": len(nodes),
        }
    except Exception as e:
        logger.error(f"Failed to list nodes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/discover")
async def discover_node(request: DiscoverRequest):
    """Discover a node's hardware via SSH."""
    result = await node_manager.discover_node(request.ip, request.username)
    if "error" in result:
        return {"success": False, **result}
    return {"success": True, **result}


@router.post("/add")
async def add_node(request: AddNodeRequest):
    """Initiate node addition. Returns a job_id for WebSocket streaming."""
    validation = node_manager.validate_inventory()
    if not validation["valid"]:
        raise HTTPException(
            status_code=500,
            detail=f"Inventory validation failed: {validation.get('error')}",
        )

    existing_nodes = node_manager.get_cluster_nodes()
    existing_names = [n["name"] for n in existing_nodes]
    if request.hostname in existing_names:
        raise HTTPException(
            status_code=409,
            detail=f"Node '{request.hostname}' already exists in the cluster",
        )

    job_id = str(uuid.uuid4())
    return {
        "job_id": job_id,
        "message": f"Node addition job created. Connect to WebSocket to start.",
        "hostname": request.hostname,
    }


@router.websocket("/ws/add/{job_id}")
async def stream_node_addition(websocket: WebSocket, job_id: str):
    """Stream node addition progress via WebSocket.

    Expected query params: hostname, ip, architecture, zerotier_ip, lan_ip,
    gpu_detected, gpu_count, gpu_model
    """
    await websocket.accept()

    try:
        params = websocket.query_params
        hostname = params.get("hostname", "")
        ip = params.get("ip", "")
        architecture = params.get("architecture", "")
        zerotier_ip = params.get("zerotier_ip", "")
        lan_ip = params.get("lan_ip", "")
        gpu_detected = params.get("gpu_detected", "false") == "true"
        gpu_count = int(params.get("gpu_count", "0"))
        gpu_model = params.get("gpu_model", "")

        if not hostname or not ip or not architecture:
            await websocket.send_json(
                {"type": "error", "message": "Missing required params: hostname, ip, architecture"}
            )
            await websocket.close()
            return

        await websocket.send_json(
            {
                "type": "start",
                "message": f"Starting node addition for {hostname}",
                "job_id": job_id,
            }
        )

        # Step 1: Update inventory
        await websocket.send_json(
            {"type": "task", "task_name": "Update Ansible inventory", "task_number": 1}
        )
        try:
            node_manager.add_node_to_inventory(
                hostname=hostname,
                ip=ip,
                architecture=architecture,
                zerotier_ip=zerotier_ip or None,
                lan_ip=lan_ip or None,
                gpu_detected=gpu_detected,
                gpu_count=gpu_count,
                gpu_model=gpu_model,
            )
            await websocket.send_json(
                {"type": "ok", "message": f"Node {hostname} added to inventory"}
            )
        except Exception as e:
            await websocket.send_json(
                {"type": "error", "message": f"Failed to update inventory: {e}"}
            )
            await websocket.close()
            return

        # Step 2: Validate inventory
        await websocket.send_json(
            {"type": "task", "task_name": "Validate inventory", "task_number": 2}
        )
        validation = node_manager.validate_inventory()
        if not validation["valid"]:
            await websocket.send_json(
                {"type": "error", "message": f"Inventory validation failed: {validation.get('error')}"}
            )
            await websocket.close()
            return
        await websocket.send_json({"type": "ok", "message": "Inventory is valid"})

        # Step 3: Run the join worker playbook
        await websocket.send_json(
            {"type": "task", "task_name": "Join node to cluster", "task_number": 3}
        )

        playbook_path = Path(
            "/home/thinkube/thinkube-platform/core/thinkube/ansible/"
            "40_thinkube/core/infrastructure/k8s/20_join_workers.yaml"
        )

        if not playbook_path.exists():
            playbook_path = ansible_env.get_playbook_path("add_node.yaml")

        if not playbook_path.exists():
            await websocket.send_json(
                {"type": "error", "message": f"Join playbook not found: {playbook_path}"}
            )
            await websocket.close()
            return

        extra_vars = {}
        try:
            extra_vars = ansible_env.prepare_auth_vars(extra_vars)
        except RuntimeError as e:
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close()
            return

        temp_vars_fd, temp_vars_path = tempfile.mkstemp(
            suffix=".yml", prefix="ansible-node-vars-"
        )
        try:
            with os.fdopen(temp_vars_fd, "w") as f:
                yaml.dump(extra_vars, f)
        except Exception:
            os.close(temp_vars_fd)
            raise

        try:
            inventory_path = ansible_env.get_inventory_path()
            cmd = [
                "stdbuf", "-oL", "-eL",
                "ansible-playbook",
                "-i", str(inventory_path),
                str(playbook_path),
                "-e", f"@{temp_vars_path}",
                "--limit", hostname,
                "-v",
            ]

            env = ansible_env.get_environment(context="optional")

            logger.info(f"Running node join: {' '.join(cmd)}")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                cwd=str(playbook_path.parent),
                bufsize=0,
            )

            current_task = "Initializing"
            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                line_text = line.decode("utf-8", errors="replace").rstrip()
                if not line_text:
                    continue

                if "TASK [" in line_text:
                    task_start = line_text.find("TASK [") + 6
                    task_end = line_text.find("]", task_start)
                    if task_end > task_start:
                        current_task = line_text[task_start:task_end]
                        await websocket.send_json(
                            {"type": "task", "task_name": current_task}
                        )
                elif line_text.startswith("ok:"):
                    await websocket.send_json(
                        {"type": "ok", "message": line_text, "task": current_task}
                    )
                elif line_text.startswith("changed:"):
                    await websocket.send_json(
                        {"type": "changed", "message": line_text, "task": current_task}
                    )
                elif line_text.startswith("fatal:") or line_text.startswith("failed:"):
                    await websocket.send_json(
                        {"type": "failed", "message": line_text, "task": current_task}
                    )
                elif "PLAY RECAP" in line_text:
                    await websocket.send_json(
                        {"type": "output", "message": line_text}
                    )
                else:
                    await websocket.send_json(
                        {"type": "output", "message": line_text}
                    )

            await process.wait()

            if process.returncode == 0:
                await websocket.send_json(
                    {"type": "task", "task_name": "Check for new architecture", "task_number": 4}
                )
                normalized = "arm64" if architecture.lower() in ("aarch64", "arm64") else "amd64"

                platform_result = node_manager.update_build_platforms()
                architectures = node_manager.get_cluster_architectures()
                new_arch_detected = platform_result.get("changed", False)

                rebuild_actions = []
                if new_arch_detected:
                    rebuild_actions = node_manager.get_rebuild_actions(normalized)
                    await websocket.send_json(
                        {
                            "type": "ok",
                            "message": f"New architecture detected: {normalized}. "
                            f"Updated build platforms to: {platform_result['platforms']}",
                        }
                    )

                await websocket.send_json(
                    {
                        "type": "complete",
                        "success": True,
                        "message": f"Node {hostname} successfully joined the cluster",
                        "architectures": architectures,
                        "new_architecture_detected": new_arch_detected,
                        "node_architecture": normalized,
                        "rebuild_actions": rebuild_actions,
                    }
                )
            else:
                await websocket.send_json(
                    {
                        "type": "complete",
                        "success": False,
                        "message": f"Node join failed (exit code {process.returncode})",
                    }
                )

        finally:
            try:
                os.unlink(temp_vars_path)
            except OSError:
                pass

    except Exception as e:
        logger.error(f"Node addition error: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.post("/remove")
async def remove_node(request: RemoveNodeRequest):
    """Remove a node from the cluster and inventory."""
    nodes = node_manager.get_cluster_nodes()
    node = next((n for n in nodes if n["name"] == request.hostname), None)

    if node and node["role"] == "control_plane":
        raise HTTPException(status_code=400, detail="Cannot remove control plane node")

    if request.drain and node:
        success, msg = await node_manager.drain_node(request.hostname)
        if not success:
            raise HTTPException(status_code=500, detail=msg)

    if node:
        success, msg = await node_manager.delete_node(request.hostname)
        if not success:
            raise HTTPException(status_code=500, detail=msg)

    try:
        node_manager.remove_node_from_inventory(request.hostname)
    except Exception as e:
        logger.error(f"Failed to remove from inventory: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Node removed from cluster but inventory update failed: {e}",
        )

    return {
        "success": True,
        "message": f"Node {request.hostname} removed from cluster and inventory",
    }
