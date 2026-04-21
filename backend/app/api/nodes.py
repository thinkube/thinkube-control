"""Node management API endpoints for discovering, adding, and removing cluster nodes."""

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel

import yaml

from app.services.ansible_environment import ansible_env
from app.services.network_discovery import network_discovery
from app.services.node_manager import node_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/nodes", tags=["nodes"])

HARBOR_IMAGES_DIR = Path(
    "/home/thinkube/thinkube-platform/core/thinkube/ansible/"
    "40_thinkube/core/harbor-images"
)


def _find_inventory_group_hosts(inventory: dict, group_name: str) -> List[str]:
    """Find hosts for a named group anywhere in the inventory tree."""
    results = []

    def _walk(node: dict):
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key == group_name and isinstance(value, dict):
                hosts = value.get("hosts")
                if isinstance(hosts, dict):
                    results.extend(hosts.keys())
            if isinstance(value, dict):
                _walk(value)

    _walk(inventory)
    return results


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


class VerifySSHRequest(BaseModel):
    nodes: List[Dict[str, str]]
    password: Optional[str] = None


class DetectHardwareRequest(BaseModel):
    nodes: List[Dict[str, str]]


class DiscoverNetworkRequest(BaseModel):
    scan_cidrs: Optional[List[str]] = None


class AddNodesBatchRequest(BaseModel):
    nodes: List[Dict[str, Any]]
    password: Optional[str] = None


async def _stream_playbook(
    websocket: WebSocket,
    playbook_path: Path,
    extra_vars: Dict[str, Any],
    step_name: str,
    step_number: int,
    limit: Optional[str] = None,
) -> bool:
    """Stream an Ansible playbook execution over WebSocket. Returns True on success."""
    await websocket.send_json(
        {"type": "playbook_start", "task_name": step_name, "task_number": step_number}
    )
    await websocket.send_json(
        {"type": "task", "task_name": step_name, "task_number": step_number}
    )

    if not playbook_path.exists():
        await websocket.send_json(
            {"type": "error", "message": f"Playbook not found: {playbook_path}"}
        )
        return False

    temp_vars_fd, temp_vars_path = tempfile.mkstemp(
        suffix=".yml", prefix="ansible-vars-"
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
            "-v",
        ]
        if limit:
            cmd.extend(["--limit", limit])

        env = ansible_env.get_environment(context="optional")

        logger.info(f"Running playbook: {' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=str(playbook_path.parent),
            bufsize=0,
        )

        current_task = "Initializing"
        in_failed_block = False
        while True:
            line = await process.stdout.readline()
            if not line:
                break

            line_text = line.decode("utf-8", errors="replace").rstrip()
            if not line_text:
                continue

            if "TASK [" in line_text:
                in_failed_block = False
                task_start = line_text.find("TASK [") + 6
                task_end = line_text.find("]", task_start)
                if task_end > task_start:
                    current_task = line_text[task_start:task_end]
                    await websocket.send_json(
                        {"type": "task", "task_name": current_task}
                    )
            elif line_text.startswith("ok:") or line_text.startswith("changed:"):
                msg_type = "ok" if line_text.startswith("ok:") else "changed"
                # Strip verbose JSON from ok/changed lines (keep just the status)
                brief = line_text.split(" => {")[0] if " => {" in line_text else line_text
                await websocket.send_json(
                    {"type": msg_type, "message": brief, "task": current_task}
                )
            elif line_text.startswith("fatal:") or line_text.startswith("failed:"):
                in_failed_block = True
                await websocket.send_json(
                    {"type": "failed", "message": line_text, "task": current_task}
                )
            elif line_text.startswith("skipping:"):
                brief = line_text.split(" => {")[0] if " => {" in line_text else line_text
                await websocket.send_json(
                    {"type": "output", "message": brief}
                )
            elif "PLAY RECAP" in line_text or "PLAY [" in line_text:
                in_failed_block = False
                await websocket.send_json(
                    {"type": "output", "message": line_text}
                )
            elif in_failed_block:
                # Include verbose details after a failure
                await websocket.send_json(
                    {"type": "output", "message": line_text}
                )
            elif line_text.startswith("[WARNING]") or line_text.startswith("[DEPRECATION"):
                pass  # Suppress warnings and deprecation notices
            elif not line_text.startswith(" ") and not line_text.startswith("{"):
                # Non-indented, non-JSON lines (play names, recap, etc.)
                await websocket.send_json(
                    {"type": "output", "message": line_text}
                )

        await process.wait()
        return process.returncode == 0

    finally:
        try:
            os.unlink(temp_vars_path)
        except OSError:
            pass


async def _run_arch_rebuild(
    websocket: WebSocket,
    hostname: str,
    new_arch: str,
    extra_vars: Dict[str, Any],
) -> bool:
    """Cordon node, rebuild all images for the new architecture, uncordon on success."""
    # Cordon the new node to prevent scheduling until images are ready
    await websocket.send_json(
        {"type": "task", "task_name": f"Cordon {hostname} (preventing scheduling until images are rebuilt)", "task_number": 5}
    )
    success, msg = await node_manager.cordon_node(hostname)
    if not success:
        await websocket.send_json({"type": "error", "message": f"Failed to cordon node: {msg}"})
        return False
    await websocket.send_json({"type": "ok", "message": f"Node {hostname} cordoned"})

    rebuild_playbooks = [
        (HARBOR_IMAGES_DIR / "14_build_base_images.yaml", "Rebuild base images (multi-arch)"),
        (HARBOR_IMAGES_DIR / "15_build_jupyter_images.yaml", "Rebuild Jupyter image (multi-arch)"),
        (HARBOR_IMAGES_DIR / "13_mirror_public_images.yaml", "Re-mirror public images (multi-arch)"),
    ]

    step = 6
    all_ok = True
    for playbook_path, description in rebuild_playbooks:
        await websocket.send_json(
            {"type": "ok", "message": f"Starting: {description}"}
        )
        ok = await _stream_playbook(
            websocket=websocket,
            playbook_path=playbook_path,
            extra_vars=extra_vars,
            step_name=description,
            step_number=step,
        )
        if not ok:
            await websocket.send_json(
                {"type": "error", "message": f"Failed: {description}. Node remains cordoned."}
            )
            all_ok = False
            break
        await websocket.send_json(
            {"type": "ok", "message": f"Completed: {description}"}
        )
        step += 1

    if all_ok:
        # Rebuild existing venvs for the new architecture
        await websocket.send_json(
            {"type": "task", "task_name": f"Rebuild Jupyter venvs for {new_arch}", "task_number": step}
        )
        venv_ok = await _rebuild_venvs_for_arch(websocket, new_arch, extra_vars, step)
        step += 1

        await websocket.send_json(
            {"type": "task", "task_name": f"Uncordon {hostname}", "task_number": step}
        )
        success, msg = await node_manager.uncordon_node(hostname)
        if not success:
            await websocket.send_json({"type": "error", "message": f"Failed to uncordon: {msg}"})
            return False
        await websocket.send_json({"type": "ok", "message": f"Node {hostname} uncordoned and ready"})

    return all_ok


async def _rebuild_venvs_for_arch(
    websocket: WebSocket,
    new_arch: str,
    extra_vars: Dict[str, Any],
    step: int,
) -> bool:
    """Rebuild all successful venvs for a newly added architecture."""
    from app.db.session import SessionLocal
    from app.models.jupyter_venvs import JupyterVenv

    db = SessionLocal()()
    try:
        venvs = db.query(JupyterVenv).filter(
            JupyterVenv.status == "success",
            JupyterVenv.is_template == False,
        ).all()

        if not venvs:
            await websocket.send_json(
                {"type": "ok", "message": "No existing venvs to rebuild"}
            )
            return True

        needs_rebuild = []
        for v in venvs:
            built = v.architectures_built or []
            if new_arch not in built:
                needs_rebuild.append(v)

        if not needs_rebuild:
            await websocket.send_json(
                {"type": "ok", "message": f"All venvs already built for {new_arch}"}
            )
            return True

        await websocket.send_json(
            {"type": "ok", "message": f"Rebuilding {len(needs_rebuild)} venv(s) for {new_arch}: {', '.join(v.name for v in needs_rebuild)}"}
        )

        import json as _json
        playbook_path = Path("/home/thinkube/thinkube-control/ansible/playbooks/build_venv.yaml")
        all_ok = True

        for v in needs_rebuild:
            venv_vars = {
                **extra_vars,
                "venv_name": v.name,
                "packages": _json.dumps(v.packages),
                "target_architecture": new_arch,
                "kubeconfig": os.environ.get("KUBECONFIG", "/home/thinkube/.kube/config"),
                "harbor_registry": f"registry.{os.environ.get('DOMAIN_NAME', 'cmxela.com')}",
            }

            ok = await _stream_playbook(
                websocket=websocket,
                playbook_path=playbook_path,
                extra_vars=venv_vars,
                step_name=f"Build venv '{v.name}' for {new_arch}",
                step_number=step,
            )

            if ok:
                built = list(v.architectures_built or [])
                if new_arch not in built:
                    built.append(new_arch)
                    built.sort()
                v.architectures_built = built
                db.commit()
                await websocket.send_json(
                    {"type": "ok", "message": f"Venv '{v.name}' built for {new_arch}"}
                )
            else:
                await websocket.send_json(
                    {"type": "error", "message": f"Venv '{v.name}' failed to build for {new_arch} — continuing with others"}
                )
                all_ok = False

        return all_ok

    except Exception as e:
        logger.error(f"Venv rebuild error: {e}", exc_info=True)
        await websocket.send_json(
            {"type": "error", "message": f"Venv rebuild error: {e}"}
        )
        return False
    finally:
        db.close()


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
    """Discover a single node's hardware via SSH (legacy)."""
    result = await node_manager.discover_node(request.ip, request.username)
    if "error" in result:
        return {"success": False, **result}
    return {"success": True, **result}


@router.post("/discover-network")
async def discover_network(request: DiscoverNetworkRequest = DiscoverNetworkRequest()):
    """Scan the network for nodes available to join the cluster.

    Ping-sweeps one or more CIDRs (defaults to inventory's network_cidr).
    Automatically excludes existing cluster nodes and MetalLB VIPs.
    """
    try:
        result = await network_discovery.discover(request.scan_cidrs)
        return result
    except Exception as e:
        logger.error(f"Network discovery failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify-ssh")
async def verify_ssh(request: VerifySSHRequest):
    """Test SSH connectivity to selected nodes using the cluster key.

    If key auth fails, automatically distributes the key using the
    system password from the environment (ANSIBLE_BECOME_PASSWORD).
    Falls back to a user-supplied password if the env var is not set.
    """
    password = (
        request.password
        or os.environ.get("ANSIBLE_BECOME_PASSWORD")
        or os.environ.get("SYSTEM_PASSWORD")
    )

    results = []
    for node_info in request.nodes:
        ip = node_info.get("ip", "")
        if not ip:
            continue

        key_ok = await node_manager.test_ssh_key_auth(ip)
        if key_ok:
            results.append({"ip": ip, "ssh_status": "key_ok"})
        elif password:
            dist_result = await node_manager.distribute_ssh_key(ip, password)
            if dist_result["success"]:
                results.append({"ip": ip, "ssh_status": "key_distributed"})
            else:
                results.append({
                    "ip": ip,
                    "ssh_status": "failed",
                    "error": dist_result.get("error", "Unknown error"),
                })
        else:
            results.append({"ip": ip, "ssh_status": "needs_password"})

    return {"results": results}


@router.post("/detect-hardware-batch")
async def detect_hardware_batch(request: DetectHardwareRequest):
    """Detect hardware on multiple nodes in parallel."""
    async def detect_one(node_info: Dict[str, str]) -> Dict[str, Any]:
        ip = node_info.get("ip", "")
        result = await node_manager.discover_node(ip)
        if "error" not in result:
            lvm_info = await node_manager.detect_lvm_status(ip)
            result.update(lvm_info)
            result["validation"] = node_manager.validate_hardware(result)
        return result

    results = await asyncio.gather(
        *[detect_one(n) for n in request.nodes]
    )
    return {"results": list(results)}


@router.post("/add-batch")
async def add_nodes_batch(request: AddNodesBatchRequest):
    """Initiate batch node addition. Returns a job_id for WebSocket streaming."""
    validation = node_manager.validate_inventory()
    if not validation["valid"]:
        raise HTTPException(
            status_code=500,
            detail=f"Inventory validation failed: {validation.get('error')}",
        )

    existing_nodes = node_manager.get_cluster_nodes()
    existing_names = {n["name"] for n in existing_nodes}
    for node_info in request.nodes:
        hostname = node_info.get("hostname", "")
        if hostname in existing_names:
            raise HTTPException(
                status_code=409,
                detail=f"Node '{hostname}' already exists in the cluster",
            )

    job_id = str(uuid.uuid4())
    return {
        "job_id": job_id,
        "message": "Batch node addition job created. Connect to WebSocket to start.",
        "node_count": len(request.nodes),
    }


@router.websocket("/ws/add-batch/{job_id}")
async def stream_batch_node_addition(websocket: WebSocket, job_id: str):
    """Stream batch node addition progress via WebSocket.

    Handles the full pipeline: SSH key distribution, ZeroTier setup (if overlay),
    hardware detection, inventory update, and k8s join.

    Expected query params: nodes (JSON array of node objects), password (optional)
    """
    await websocket.accept()

    try:
        import json as _json

        params = websocket.query_params
        nodes_json = params.get("nodes", "[]")
        password = (
            params.get("password", "")
            or os.environ.get("ANSIBLE_BECOME_PASSWORD")
            or os.environ.get("SYSTEM_PASSWORD")
            or ""
        )
        nodes = _json.loads(nodes_json)

        if not nodes:
            await websocket.send_json(
                {"type": "error", "message": "No nodes provided"}
            )
            await websocket.close()
            return

        inventory = node_manager.read_inventory()
        inv_vars = inventory.get("all", {}).get("vars", {})
        network_mode = inv_vars.get("network_mode", "overlay")
        step = 1

        await websocket.send_json({
            "type": "start",
            "message": f"Starting addition of {len(nodes)} node(s)",
            "job_id": job_id,
        })

        added_hostnames = []

        for node_info in nodes:
            ip = node_info.get("ip", "")
            hostname = node_info.get("hostname", "")
            lan_ip = node_info.get("lan_ip", ip)

            await websocket.send_json({
                "type": "task",
                "task_name": f"[{hostname or ip}] Distribute SSH key",
                "task_number": step,
            })

            # Step: Distribute SSH key if needed
            key_ok = await node_manager.test_ssh_key_auth(ip)
            if key_ok:
                await websocket.send_json({
                    "type": "ok",
                    "message": f"[{hostname or ip}] SSH key already authorized",
                })
            elif password:
                dist_result = await node_manager.distribute_ssh_key(ip, password)
                if not dist_result["success"]:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"[{hostname or ip}] SSH key distribution failed: {dist_result.get('error')}",
                    })
                    await websocket.send_json({
                        "type": "node_complete",
                        "hostname": hostname or ip,
                        "success": False,
                    })
                    continue
                await websocket.send_json({
                    "type": "ok",
                    "message": f"[{hostname or ip}] SSH key distributed successfully",
                })
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"[{hostname or ip}] SSH key auth failed and no password provided",
                })
                await websocket.send_json({
                    "type": "node_complete",
                    "hostname": hostname or ip,
                    "success": False,
                })
                continue

            step += 1

            # Step: Expand LVM if needed
            lvm_expandable = node_info.get("lvm_expandable", False)
            lvm_lv_path = node_info.get("lvm_lv_path", "")
            if lvm_expandable and lvm_lv_path:
                await websocket.send_json({
                    "type": "task",
                    "task_name": f"[{hostname or ip}] Expand LVM volume",
                    "task_number": step,
                })
                try:
                    result = await node_manager.expand_lvm(ip, lvm_lv_path)
                    if result["success"]:
                        await websocket.send_json({
                            "type": "ok",
                            "message": f"[{hostname or ip}] LVM expanded to {result.get('new_size', 'full disk')}",
                        })
                    else:
                        await websocket.send_json({
                            "type": "warning",
                            "message": f"[{hostname or ip}] LVM expansion failed: {result.get('error')} — continuing anyway",
                        })
                except Exception as e:
                    await websocket.send_json({
                        "type": "warning",
                        "message": f"[{hostname or ip}] LVM expansion failed: {e} — continuing anyway",
                    })
                step += 1

            # Step: ZeroTier setup (if overlay mode)
            zerotier_ip = node_info.get("zerotier_ip")
            if network_mode == "overlay" and not zerotier_ip:
                await websocket.send_json({
                    "type": "task",
                    "task_name": f"[{hostname or ip}] Setup ZeroTier",
                    "task_number": step,
                })

                assigned_ip = network_discovery.get_next_available_zerotier_ip()
                if not assigned_ip:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"[{hostname or ip}] No available ZeroTier IPs",
                    })
                    await websocket.send_json({
                        "type": "node_complete",
                        "hostname": hostname or ip,
                        "success": False,
                    })
                    continue

                zt_result = await node_manager.setup_zerotier_on_node(ip, assigned_ip)
                if not zt_result["success"]:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"[{hostname or ip}] ZeroTier setup failed: {zt_result.get('error')}",
                    })
                    await websocket.send_json({
                        "type": "node_complete",
                        "hostname": hostname or ip,
                        "success": False,
                    })
                    continue

                zerotier_ip = zt_result["zerotier_ip"]
                await websocket.send_json({
                    "type": "ok",
                    "message": f"[{hostname or ip}] ZeroTier configured with IP {zerotier_ip}",
                })
                step += 1

            # Step: Detect hardware (if not already provided)
            architecture = node_info.get("architecture")
            gpu_detected = node_info.get("gpu_detected", False)
            gpu_count = node_info.get("gpu_count", 0)
            gpu_model = node_info.get("gpu_model", "")

            if not architecture:
                await websocket.send_json({
                    "type": "task",
                    "task_name": f"[{hostname or ip}] Detect hardware",
                    "task_number": step,
                })
                connect_ip = zerotier_ip if network_mode == "overlay" and zerotier_ip else ip
                hw = await node_manager.discover_node(connect_ip)
                if "error" in hw:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"[{hostname or ip}] Hardware detection failed: {hw['error']}",
                    })
                    await websocket.send_json({
                        "type": "node_complete",
                        "hostname": hostname or ip,
                        "success": False,
                    })
                    continue

                hostname = hostname or hw.get("hostname", ip)
                architecture = hw.get("architecture", "unknown")
                gpu_detected = hw.get("gpu_detected", False)
                gpu_count = hw.get("gpu_count", 0)
                gpu_model = hw.get("gpu_model", "")
                await websocket.send_json({
                    "type": "ok",
                    "message": f"[{hostname}] Detected: {architecture}, {hw.get('cpu_cores', '?')} cores, {hw.get('memory_gb', '?')} GB RAM",
                })
                step += 1

            # Step: Prepare Ansible Python environment
            connect_ip = zerotier_ip if network_mode == "overlay" and zerotier_ip else ip
            await websocket.send_json({
                "type": "task",
                "task_name": f"[{hostname}] Prepare Python environment",
                "task_number": step,
            })
            py_result = await node_manager.prepare_ansible_python(connect_ip)
            if py_result["success"]:
                await websocket.send_json({
                    "type": "ok",
                    "message": f"[{hostname}] Python venv ready",
                })
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"[{hostname}] Python setup failed: {py_result.get('error')}",
                })
                await websocket.send_json({
                    "type": "node_complete",
                    "hostname": hostname,
                    "success": False,
                })
                continue
            step += 1

            # Step: Update inventory
            await websocket.send_json({
                "type": "task",
                "task_name": f"[{hostname}] Update inventory",
                "task_number": step,
            })
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
                await websocket.send_json({
                    "type": "ok",
                    "message": f"[{hostname}] Added to inventory",
                })
                added_hostnames.append(hostname)
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": f"[{hostname}] Inventory update failed: {e}",
                })
                await websocket.send_json({
                    "type": "node_complete",
                    "hostname": hostname,
                    "success": False,
                })
                continue

            step += 1

        if not added_hostnames:
            await websocket.send_json({
                "type": "complete",
                "success": False,
                "message": "No nodes were successfully prepared for joining",
            })
            await websocket.close()
            return

        # Step: Validate inventory
        await websocket.send_json({
            "type": "task",
            "task_name": "Validate inventory",
            "task_number": step,
        })
        validation = node_manager.validate_inventory()
        if not validation["valid"]:
            await websocket.send_json({
                "type": "error",
                "message": f"Inventory validation failed: {validation.get('error')}",
            })
            await websocket.close()
            return
        await websocket.send_json({"type": "ok", "message": "Inventory is valid"})
        step += 1

        # Capture architectures before join so we can detect new ones after
        pre_join_architectures = set(node_manager.get_cluster_architectures())

        # Step: Run join workers playbook
        playbook_path = Path(
            "/home/thinkube/thinkube-platform/core/thinkube/ansible/"
            "40_thinkube/core/infrastructure/k8s/20_join_workers.yaml"
        )
        if not playbook_path.exists():
            playbook_path = ansible_env.get_playbook_path("add_node.yaml")

        extra_vars = {}
        try:
            extra_vars = ansible_env.prepare_auth_vars(extra_vars)
        except RuntimeError as e:
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close()
            return

        # Include control plane + localhost so post-join plays run too
        inventory = node_manager.read_inventory()
        cp_hosts = _find_inventory_group_hosts(inventory, "k8s_control_plane")
        limit_hosts = ",".join(added_hostnames + cp_hosts + ["localhost"])
        join_ok = await _stream_playbook(
            websocket=websocket,
            playbook_path=playbook_path,
            extra_vars=extra_vars,
            step_name=f"Join node(s) to cluster: {','.join(added_hostnames)}",
            step_number=step,
            limit=limit_hosts,
        )

        if join_ok:
            step += 1

            # Step: Check for new architecture
            await websocket.send_json({
                "type": "task",
                "task_name": "Check for new architecture",
                "task_number": step,
            })

            post_join_architectures = set(node_manager.get_cluster_architectures())
            new_archs = post_join_architectures - pre_join_architectures
            new_arch_detected = len(new_archs) > 0

            # Always sync inventory platforms to match cluster state
            platform_result = node_manager.update_build_platforms()
            architectures = sorted(post_join_architectures)

            rebuild_ok = True
            if new_arch_detected:
                new_arch = sorted(new_archs)[-1]
                platforms_str = ",".join(f"linux/{a}" for a in sorted(post_join_architectures))
                await websocket.send_json({
                    "type": "ok",
                    "message": f"New architecture detected: {new_arch}. Build platforms: {platforms_str}. Starting image rebuilds...",
                })

                rebuild_ok = await _run_arch_rebuild(
                    websocket=websocket,
                    hostname=added_hostnames[0],
                    new_arch=new_arch,
                    extra_vars=extra_vars,
                )

            await websocket.send_json({
                "type": "complete",
                "success": True,
                "message": (
                    f"Successfully added {len(added_hostnames)} node(s): {', '.join(added_hostnames)}"
                    + (" — images rebuilt for multi-arch" if new_arch_detected and rebuild_ok else "")
                    + (" — WARNING: image rebuild failed, node(s) cordoned" if new_arch_detected and not rebuild_ok else "")
                ),
                "architectures": architectures,
                "new_architecture_detected": new_arch_detected,
                "images_rebuilt": rebuild_ok if new_arch_detected else None,
            })
        else:
            await websocket.send_json({
                "type": "complete",
                "success": False,
                "message": "Node join failed",
            })

    except Exception as e:
        logger.error(f"Batch node addition error: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


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

        # Capture architectures before join so we can detect new ones after
        pre_join_architectures = set(node_manager.get_cluster_architectures())

        # Step 3: Run the join worker playbook
        playbook_path = Path(
            "/home/thinkube/thinkube-platform/core/thinkube/ansible/"
            "40_thinkube/core/infrastructure/k8s/20_join_workers.yaml"
        )
        if not playbook_path.exists():
            playbook_path = ansible_env.get_playbook_path("add_node.yaml")

        extra_vars = {}
        try:
            extra_vars = ansible_env.prepare_auth_vars(extra_vars)
        except RuntimeError as e:
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close()
            return

        # Include control plane + localhost so post-join plays run too
        inventory = node_manager.read_inventory()
        cp_hosts = _find_inventory_group_hosts(inventory, "k8s_control_plane")
        limit_hosts = ",".join([hostname] + cp_hosts + ["localhost"])
        join_ok = await _stream_playbook(
            websocket=websocket,
            playbook_path=playbook_path,
            extra_vars=extra_vars,
            step_name="Join node to cluster",
            step_number=3,
            limit=limit_hosts,
        )

        if join_ok:
            # Step 4: Check for new architecture
            await websocket.send_json(
                {"type": "task", "task_name": "Check for new architecture", "task_number": 4}
            )
            normalized = "arm64" if architecture.lower() in ("aarch64", "arm64") else "amd64"

            post_join_architectures = set(node_manager.get_cluster_architectures())
            new_archs = post_join_architectures - pre_join_architectures
            new_arch_detected = len(new_archs) > 0

            # Always sync inventory platforms to match cluster state
            platform_result = node_manager.update_build_platforms()
            architectures = sorted(post_join_architectures)

            rebuild_ok = True
            if new_arch_detected:
                platforms_str = ",".join(f"linux/{a}" for a in sorted(post_join_architectures))
                await websocket.send_json(
                    {
                        "type": "ok",
                        "message": f"New architecture detected: {normalized}. "
                        f"Build platforms: {platforms_str}. "
                        f"Starting image rebuilds...",
                    }
                )

                # Steps 5+: Cordon, rebuild images, uncordon
                rebuild_ok = await _run_arch_rebuild(
                    websocket=websocket,
                    hostname=hostname,
                    new_arch=normalized,
                    extra_vars=extra_vars,
                )

            await websocket.send_json(
                {
                    "type": "complete",
                    "success": True,
                    "message": (
                        f"Node {hostname} successfully joined the cluster"
                        + (" and all images rebuilt for multi-arch" if new_arch_detected and rebuild_ok else "")
                        + (". WARNING: Image rebuild failed — node is cordoned." if new_arch_detected and not rebuild_ok else "")
                    ),
                    "architectures": architectures,
                    "new_architecture_detected": new_arch_detected,
                    "node_architecture": normalized,
                    "images_rebuilt": rebuild_ok if new_arch_detected else None,
                }
            )
        else:
            await websocket.send_json(
                {
                    "type": "complete",
                    "success": False,
                    "message": f"Node join failed",
                }
            )

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
