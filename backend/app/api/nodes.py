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
GPU_OPERATOR_DIR = Path(
    "/home/thinkube/thinkube-platform/core/thinkube/ansible/"
    "40_thinkube/core/infrastructure/gpu_operator"
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
            limit=1024 * 1024,
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
                await websocket.send_json(
                    {"type": msg_type, "message": line_text, "task": current_task}
                )
            elif line_text.startswith("fatal:") or line_text.startswith("failed:"):
                in_failed_block = True
                await websocket.send_json(
                    {"type": "failed", "message": line_text, "task": current_task}
                )
            elif line_text.startswith("skipping:"):
                await websocket.send_json(
                    {"type": "output", "message": line_text}
                )
            elif "PLAY RECAP" in line_text or "PLAY [" in line_text:
                in_failed_block = False
                await websocket.send_json(
                    {"type": "output", "message": line_text}
                )
            elif in_failed_block:
                await websocket.send_json(
                    {"type": "output", "message": line_text}
                )
            elif line_text.startswith("[WARNING]") or line_text.startswith("[DEPRECATION"):
                pass
            else:
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
    hostnames: List[str],
    new_arch: str,
    extra_vars: Dict[str, Any],
) -> bool:
    """Cordon all new nodes, rebuild images for the new architecture, uncordon all unconditionally."""
    hosts_label = ", ".join(hostnames)

    # Cordon ALL new nodes
    await websocket.send_json(
        {"type": "task", "task_name": f"Cordon {hosts_label} (preventing scheduling until images are rebuilt)", "task_number": 5}
    )
    for h in hostnames:
        success, msg = await node_manager.cordon_node(h)
        if not success:
            await websocket.send_json({"type": "error", "message": f"Failed to cordon {h}: {msg}"})
        else:
            await websocket.send_json({"type": "ok", "message": f"Node {h} cordoned"})

    rebuild_playbooks = [
        (HARBOR_IMAGES_DIR / "13_mirror_public_images.yaml", "Mirror public images (multi-arch)"),
        (HARBOR_IMAGES_DIR / "14_build_base_images.yaml", "Rebuild base images (multi-arch)"),
        (HARBOR_IMAGES_DIR / "15_build_jupyter_images.yaml", "Rebuild Jupyter image (multi-arch)"),
    ]

    step = 6
    all_ok = True
    try:
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
                    {"type": "error", "message": f"Failed: {description}"}
                )
                all_ok = False
                break
            await websocket.send_json(
                {"type": "ok", "message": f"Completed: {description}"}
            )
            step += 1

        if all_ok:
            await websocket.send_json(
                {"type": "task", "task_name": f"Rebuild Jupyter venvs for {new_arch}", "task_number": step}
            )
            await _rebuild_venvs_for_arch(websocket, new_arch, extra_vars, step)
            step += 1
    finally:
        # Always uncordon ALL nodes, regardless of rebuild success/failure
        await websocket.send_json(
            {"type": "task", "task_name": f"Uncordon {hosts_label}", "task_number": step}
        )
        for h in hostnames:
            success, msg = await node_manager.uncordon_node(h)
            if not success:
                await websocket.send_json({"type": "error", "message": f"Failed to uncordon {h}: {msg}"})
            else:
                await websocket.send_json({"type": "ok", "message": f"Node {h} uncordoned and ready"})

    return all_ok


async def _run_gpu_setup(
    websocket: WebSocket,
    extra_vars: Dict[str, Any],
    step: int,
    has_dgx_spark: bool,
) -> bool:
    """Deploy GPU operator and optionally configure time slicing."""
    gpu_deploy = GPU_OPERATOR_DIR / "10_deploy.yaml"
    if not gpu_deploy.exists():
        await websocket.send_json(
            {"type": "error", "message": f"GPU operator playbook not found: {gpu_deploy}"}
        )
        return False

    await websocket.send_json(
        {"type": "task", "task_name": "Deploy GPU Operator", "task_number": step}
    )
    ok = await _stream_playbook(
        websocket=websocket,
        playbook_path=gpu_deploy,
        extra_vars=extra_vars,
        step_name="Deploy GPU Operator",
        step_number=step,
    )
    if not ok:
        await websocket.send_json(
            {"type": "error", "message": "GPU Operator deployment failed"}
        )
        return False
    await websocket.send_json(
        {"type": "ok", "message": "GPU Operator deployed"}
    )

    if has_dgx_spark:
        step += 1
        time_slicing = GPU_OPERATOR_DIR / "15_configure_time_slicing.yaml"
        if time_slicing.exists():
            await websocket.send_json(
                {"type": "task", "task_name": "Configure GPU time slicing (DGX Spark)", "task_number": step}
            )
            ts_ok = await _stream_playbook(
                websocket=websocket,
                playbook_path=time_slicing,
                extra_vars=extra_vars,
                step_name="Configure GPU time slicing",
                step_number=step,
            )
            if not ts_ok:
                await websocket.send_json(
                    {"type": "error", "message": "GPU time slicing configuration failed"}
                )
                return False
            await websocket.send_json(
                {"type": "ok", "message": "GPU time slicing configured"}
            )

    return True


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
        network_mode = inv_vars.get("network_mode")
        if not network_mode:
            await websocket.send_json({"type": "error", "message": "network_mode not set in inventory"})
            await websocket.close()
            return
        step = 1

        await websocket.send_json({
            "type": "start",
            "message": f"Starting addition of {len(nodes)} node(s)",
            "job_id": job_id,
        })

        # Ensure MetalLB VIP routes in ZeroTier (once, before node loop)
        if network_mode == "overlay":
            zt_network_id = inv_vars.get("zerotier_network_id")
            zt_api_token = inv_vars.get("zerotier_api_token")
            if not zt_network_id or not zt_api_token:
                await websocket.send_json({"type": "error", "message": "zerotier_network_id or zerotier_api_token not in inventory"})
                await websocket.close()
                return
            await node_manager._ensure_zerotier_vip_routes(
                inventory, inv_vars, zt_network_id, zt_api_token
            )

        added_hostnames = []
        any_gpu_detected = False
        any_dgx_spark = False

        batch_failed = False
        failed_hostname = ""

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
                        "message": f"[{hostname or ip}] SSH key distribution failed: {dist_result.get('error')}. Aborting batch.",
                    })
                    batch_failed = True
                    failed_hostname = hostname or ip
                    break
                await websocket.send_json({
                    "type": "ok",
                    "message": f"[{hostname or ip}] SSH key distributed successfully",
                })
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"[{hostname or ip}] SSH key auth failed and no password provided. Aborting batch.",
                })
                batch_failed = True
                failed_hostname = hostname or ip
                break

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
                            "type": "error",
                            "message": f"[{hostname or ip}] LVM expansion failed: {result.get('error')}. Aborting batch.",
                        })
                        batch_failed = True
                        failed_hostname = hostname or ip
                        break
                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"[{hostname or ip}] LVM expansion failed: {e}. Aborting batch.",
                    })
                    batch_failed = True
                    failed_hostname = hostname or ip
                    break
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
                        "message": f"[{hostname or ip}] No available ZeroTier IPs. Aborting batch.",
                    })
                    batch_failed = True
                    failed_hostname = hostname or ip
                    break

                zt_result = await node_manager.setup_zerotier_on_node(ip, assigned_ip)
                if not zt_result["success"]:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"[{hostname or ip}] ZeroTier setup failed: {zt_result.get('error')}. Aborting batch.",
                    })
                    batch_failed = True
                    failed_hostname = hostname or ip
                    break

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
                        "message": f"[{hostname or ip}] Hardware detection failed: {hw['error']}. Aborting batch.",
                    })
                    batch_failed = True
                    failed_hostname = hostname or ip
                    break

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
                    "message": f"[{hostname}] Python setup failed: {py_result.get('error')}. Aborting batch.",
                })
                batch_failed = True
                failed_hostname = hostname
                break
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
                if gpu_detected:
                    any_gpu_detected = True
                    if gpu_model and ("DGX Spark" in gpu_model or "GB10" in gpu_model):
                        any_dgx_spark = True
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": f"[{hostname}] Inventory update failed: {e}. Aborting batch.",
                })
                batch_failed = True
                failed_hostname = hostname
                break

            step += 1

        if batch_failed:
            await websocket.send_json({
                "type": "complete",
                "status": "failed",
                "message": f"Batch aborted: {failed_hostname} failed. No nodes were joined.",
            })
            await websocket.close()
            return

        if not added_hostnames:
            await websocket.send_json({
                "type": "complete",
                "status": "failed",
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

        # Read existing build platforms from inventory — this is the source of truth
        # for what architectures Harbor already has images for, independent of
        # which nodes happen to be in the cluster right now.
        inventory = node_manager.read_inventory()
        existing_platforms = inventory["all"].get("vars", {}).get("container_build_platforms", "")
        existing_archs = set()
        for p in existing_platforms.split(","):
            p = p.strip()
            if p.startswith("linux/"):
                existing_archs.add(p.split("/", 1)[1])

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
            new_archs = post_join_architectures - existing_archs
            new_arch_detected = len(new_archs) > 0

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
                    hostnames=added_hostnames,
                    new_arch=new_arch,
                    extra_vars=extra_vars,
                )

            # Only update container_build_platforms after a successful rebuild.
            # If the rebuild failed, the inventory keeps the old value so the
            # next add-node attempt knows it still needs to rebuild.
            if not new_arch_detected or rebuild_ok:
                node_manager.update_build_platforms()
            architectures = sorted(post_join_architectures)

            # GPU Operator setup for nodes with GPUs
            gpu_ok = True
            if any_gpu_detected:
                step += 1
                gpu_ok = await _run_gpu_setup(
                    websocket=websocket,
                    extra_vars=extra_vars,
                    step=step,
                    has_dgx_spark=any_dgx_spark,
                )

            warnings = []
            if new_arch_detected and not rebuild_ok:
                warnings.append("image rebuild failed")
            if any_gpu_detected and not gpu_ok:
                warnings.append("GPU operator setup failed")

            status = "success" if not warnings else "warning"
            await websocket.send_json({
                "type": "complete",
                "status": status,
                "message": (
                    f"Successfully added {len(added_hostnames)} node(s): {', '.join(added_hostnames)}"
                    + (" — images rebuilt for multi-arch" if new_arch_detected and rebuild_ok else "")
                    + (" — GPU operator configured" if any_gpu_detected and gpu_ok else "")
                    + (f" — WARNING: {', '.join(warnings)}" if warnings else "")
                ),
                "architectures": architectures,
                "new_architecture_detected": new_arch_detected,
                "images_rebuilt": rebuild_ok if new_arch_detected else None,
            })
        else:
            await websocket.send_json({
                "type": "complete",
                "status": "failed",
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

        # Read existing build platforms from inventory — source of truth for
        # what architectures Harbor already has images for.
        inventory = node_manager.read_inventory()
        existing_platforms = inventory["all"].get("vars", {}).get("container_build_platforms", "")
        existing_archs = set()
        for p in existing_platforms.split(","):
            p = p.strip()
            if p.startswith("linux/"):
                existing_archs.add(p.split("/", 1)[1])

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
            new_archs = post_join_architectures - existing_archs
            new_arch_detected = len(new_archs) > 0

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
                    hostnames=[hostname],
                    new_arch=normalized,
                    extra_vars=extra_vars,
                )

            if not new_arch_detected or rebuild_ok:
                node_manager.update_build_platforms()
            architectures = sorted(post_join_architectures)

            # GPU Operator setup if this node has GPUs
            gpu_ok = True
            if gpu_detected:
                step = 6 if not new_arch_detected else step + 1
                has_dgx_spark = bool(gpu_model and ("DGX Spark" in gpu_model or "GB10" in gpu_model))
                gpu_ok = await _run_gpu_setup(
                    websocket=websocket,
                    extra_vars=extra_vars,
                    step=step,
                    has_dgx_spark=has_dgx_spark,
                )

            warnings = []
            if new_arch_detected and not rebuild_ok:
                warnings.append("image rebuild failed")
            if gpu_detected and not gpu_ok:
                warnings.append("GPU operator setup failed")

            status = "success" if not warnings else "warning"
            await websocket.send_json(
                {
                    "type": "complete",
                    "status": status,
                    "message": (
                        f"Node {hostname} successfully joined the cluster"
                        + (" and all images rebuilt for multi-arch" if new_arch_detected and rebuild_ok else "")
                        + (" — GPU operator configured" if gpu_detected and gpu_ok else "")
                        + (f". WARNING: {', '.join(warnings)}" if warnings else "")
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
                    "status": "failed",
                    "message": "Node join failed",
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
