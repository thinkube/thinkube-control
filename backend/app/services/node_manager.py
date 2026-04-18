"""Node management service for discovering, adding, and removing cluster nodes."""

import asyncio
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kubernetes import client, config
from ruamel.yaml import YAML

from app.services.ansible_environment import ansible_env

logger = logging.getLogger(__name__)

yaml = YAML()
yaml.preserve_quotes = True
yaml.default_flow_style = False


class NodeManager:
    """Manages cluster node lifecycle: discovery, inventory, join, and removal."""

    def __init__(self):
        self.inventory_path = ansible_env.get_inventory_path()
        self._init_kubernetes()

    def _init_kubernetes(self):
        try:
            config.load_incluster_config()
        except Exception:
            try:
                config.load_kube_config()
            except Exception:
                logger.warning("Could not load kubernetes config")

    def get_cluster_nodes(self) -> List[Dict[str, Any]]:
        """List all cluster nodes with architecture, role, status, and resources."""
        try:
            v1 = client.CoreV1Api()
            nodes = v1.list_node()

            result = []
            for node in nodes.items:
                labels = node.metadata.labels or {}
                conditions = node.status.conditions or []

                ready = False
                for cond in conditions:
                    if cond.type == "Ready":
                        ready = cond.status == "True"
                        break

                is_control_plane = any(
                    k in labels
                    for k in [
                        "node-role.kubernetes.io/control-plane",
                        "node.kubernetes.io/microk8s-controlplane",
                    ]
                )

                capacity = node.status.capacity or {}
                gpu_count = int(capacity.get("nvidia.com/gpu", 0))

                result.append(
                    {
                        "name": node.metadata.name,
                        "architecture": labels.get("kubernetes.io/arch", "unknown"),
                        "os": labels.get("kubernetes.io/os", "unknown"),
                        "role": "control_plane" if is_control_plane else "worker",
                        "ready": ready,
                        "cpu_capacity": int(capacity.get("cpu", 0)),
                        "memory_capacity_gb": round(
                            int(capacity.get("memory", "0Ki").replace("Ki", ""))
                            / (1024 * 1024),
                            1,
                        )
                        if "Ki" in str(capacity.get("memory", ""))
                        else 0,
                        "gpu_count": gpu_count,
                        "kubelet_version": node.status.node_info.kubelet_version
                        if node.status.node_info
                        else "unknown",
                        "kernel_version": node.status.node_info.kernel_version
                        if node.status.node_info
                        else "unknown",
                        "creation_timestamp": node.metadata.creation_timestamp.isoformat()
                        if node.metadata.creation_timestamp
                        else None,
                        "labels": labels,
                        "is_build_node": labels.get("thinkube.io/build-node") == "true",
                    }
                )

            return result

        except Exception as e:
            logger.error(f"Failed to list cluster nodes: {e}")
            raise

    def get_cluster_architectures(self) -> List[str]:
        """Get the set of architectures currently in the cluster."""
        nodes = self.get_cluster_nodes()
        return sorted(set(n["architecture"] for n in nodes if n["architecture"] != "unknown"))

    async def discover_node(
        self, ip: str, username: Optional[str] = None
    ) -> Dict[str, Any]:
        """Discover a node's hardware via SSH.

        Uses Ansible to connect and gather facts from the target node.
        """
        ssh_key_path = ansible_env.get_ssh_key_path()
        if not username:
            username = os.environ.get("SYSTEM_USERNAME", "tkadmin")

        ssh_opts = "-o StrictHostKeyChecking=no -o ConnectTimeout=10"
        ssh_key_arg = f"-i {ssh_key_path}" if ssh_key_path.exists() else ""

        detection_script = r"""
echo '{'

echo -n '"hostname": "'
hostname | tr -d '\n'
echo '",'

echo -n '"architecture": "'
uname -m 2>/dev/null | tr -d '\n' || echo -n "unknown"
echo '",'

echo -n '"cpu_cores": '
nproc 2>/dev/null | tr -d '\n' || echo -n "0"
echo ','

echo -n '"memory_gb": '
free -g 2>/dev/null | grep '^Mem:' | awk '{print $2}' | tr -d '\n' || echo -n "0"
echo ','

echo -n '"disk_gb": '
df -BG / 2>/dev/null | tail -1 | awk '{print $2}' | tr -d 'G\n' || echo -n "0"
echo ','

echo -n '"os_release": "'
grep '^PRETTY_NAME=' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"\n' || echo -n "unknown"
echo '",'

echo -n '"k8s_installed": '
if command -v k8s &>/dev/null; then echo -n 'true'; else echo -n 'false'; fi
echo ','

echo -n '"gpu_detected": '
if lspci 2>/dev/null | grep -qi nvidia; then echo -n 'true'; else echo -n 'false'; fi
echo ','

echo -n '"gpu_model": "'
lspci 2>/dev/null | grep -i 'vga.*nvidia\|3d.*nvidia' | head -1 | sed 's/.*NVIDIA Corporation //' | sed 's/ \[.*//; s/"/\\"/g' | tr -d '\n' || echo -n ""
echo '",'

echo -n '"gpu_count": '
lspci 2>/dev/null | grep -ci 'vga.*nvidia\|3d.*nvidia' || echo -n "0"
echo ','

echo -n '"nvidia_driver_version": "'
nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | tr -d '\n' || echo -n ""
echo '"'

echo '}'
"""

        cmd = f"ssh {ssh_opts} {ssh_key_arg} {username}@{ip} bash -s"

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd.split(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=detection_script.encode()), timeout=30
            )

            if process.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                return {
                    "error": f"SSH connection failed: {error_msg}",
                    "ip": ip,
                }

            output = stdout.decode("utf-8", errors="replace").strip()
            try:
                data = json.loads(output)
                data["ip"] = ip
                arch = data.get("architecture", "unknown").lower()
                if arch in ("x86_64", "amd64"):
                    data["normalized_arch"] = "amd64"
                elif arch in ("aarch64", "arm64"):
                    data["normalized_arch"] = "arm64"
                else:
                    data["normalized_arch"] = arch
                return data
            except json.JSONDecodeError as e:
                return {
                    "error": f"Failed to parse hardware data: {e}",
                    "raw_output": output,
                    "ip": ip,
                }

        except asyncio.TimeoutError:
            return {"error": "SSH connection timed out", "ip": ip}
        except Exception as e:
            return {"error": f"Discovery failed: {str(e)}", "ip": ip}

    def read_inventory(self) -> Dict[str, Any]:
        """Read and parse the Ansible inventory YAML."""
        if not self.inventory_path.exists():
            raise FileNotFoundError(f"Inventory not found: {self.inventory_path}")
        with open(self.inventory_path, "r") as f:
            return yaml.load(f)

    def backup_inventory(self) -> Path:
        """Create a timestamped backup of the inventory file."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = self.inventory_path.parent / f"inventory.yaml.bak.{timestamp}"
        shutil.copy2(self.inventory_path, backup_path)
        logger.info(f"Inventory backed up to {backup_path}")
        return backup_path

    def write_inventory(self, inventory: Dict[str, Any]) -> None:
        """Write the inventory back to YAML, preserving formatting."""
        with open(self.inventory_path, "w") as f:
            yaml.dump(inventory, f)
        logger.info(f"Inventory written to {self.inventory_path}")

    def add_node_to_inventory(
        self,
        hostname: str,
        ip: str,
        architecture: str,
        zerotier_ip: Optional[str] = None,
        lan_ip: Optional[str] = None,
        gpu_detected: bool = False,
        gpu_count: int = 0,
        gpu_model: str = "",
    ) -> None:
        """Add a new worker node to the Ansible inventory.

        Updates: baremetal.hosts, k8s_workers.hosts, arch group, overlay_nodes.
        """
        self.backup_inventory()
        inventory = self.read_inventory()

        all_section = inventory["all"]
        children = all_section["children"]
        network_mode = all_section.get("vars", {}).get("network_mode", "overlay")

        normalized_arch = "arm64" if architecture.lower() in ("aarch64", "arm64") else "x86_64"

        host_def = {
            "ansible_host": zerotier_ip if network_mode == "overlay" and zerotier_ip else ip,
            "lan_ip": lan_ip or ip,
            "arch": normalized_arch,
            "zerotier_enabled": network_mode == "overlay",
            "configure_gpu_passthrough": False,
        }

        if network_mode == "overlay" and zerotier_ip:
            host_def["zerotier_ip"] = zerotier_ip

        children["baremetal"]["hosts"][hostname] = host_def
        children["baremetal"]["children"]["headless"]["hosts"][hostname] = {}

        arch_group = normalized_arch  # x86_64 or arm64
        if arch_group not in children["arch"]["children"]:
            children["arch"]["children"][arch_group] = {"hosts": {}}
        children["arch"]["children"][arch_group]["hosts"][hostname] = {}

        if "k8s_workers" not in children["k8s"]["children"]:
            children["k8s"]["children"]["k8s_workers"] = {"hosts": {}}
        children["k8s"]["children"]["k8s_workers"]["hosts"][hostname] = {}

        if network_mode == "overlay":
            if "overlay_nodes" not in children:
                children["overlay_nodes"] = {"hosts": {}}
            children["overlay_nodes"]["hosts"][hostname] = {}

        if gpu_detected and gpu_count > 0:
            if "baremetal_gpus" not in children:
                children["baremetal_gpus"] = {"hosts": {}, "vars": {}}
            if "hosts" not in children["baremetal_gpus"]:
                children["baremetal_gpus"]["hosts"] = {}
            children["baremetal_gpus"]["hosts"][hostname] = {
                "gpu_count": gpu_count,
                "gpu_model": gpu_model,
            }

        self.write_inventory(inventory)
        logger.info(f"Added node {hostname} ({normalized_arch}) to inventory")

    def remove_node_from_inventory(self, hostname: str) -> None:
        """Remove a node from all inventory groups."""
        self.backup_inventory()
        inventory = self.read_inventory()
        children = inventory["all"]["children"]

        groups_to_check = [
            ("baremetal", "hosts"),
            ("k8s", "children", "k8s_workers", "hosts"),
            ("overlay_nodes", "hosts"),
        ]

        if hostname in children.get("baremetal", {}).get("hosts", {}):
            del children["baremetal"]["hosts"][hostname]
        for sub in ["headless", "desktops", "dgx"]:
            sub_hosts = children.get("baremetal", {}).get("children", {}).get(sub, {}).get("hosts", {})
            if hostname in sub_hosts:
                del sub_hosts[hostname]

        for arch in ["x86_64", "arm64"]:
            arch_hosts = children.get("arch", {}).get("children", {}).get(arch, {}).get("hosts", {})
            if hostname in arch_hosts:
                del arch_hosts[hostname]

        workers = children.get("k8s", {}).get("children", {}).get("k8s_workers", {}).get("hosts", {})
        if hostname in workers:
            del workers[hostname]

        overlay = children.get("overlay_nodes", {}).get("hosts", {})
        if hostname in overlay:
            del overlay[hostname]

        gpu_hosts = children.get("baremetal_gpus", {}).get("hosts", {})
        if hostname in gpu_hosts:
            del gpu_hosts[hostname]

        self.write_inventory(inventory)
        logger.info(f"Removed node {hostname} from inventory")

    async def drain_node(self, node_name: str) -> Tuple[bool, str]:
        """Drain a kubernetes node (cordon + evict pods)."""
        try:
            v1 = client.CoreV1Api()
            body = {"spec": {"unschedulable": True}}
            v1.patch_node(node_name, body)

            cmd = [
                "kubectl",
                "drain",
                node_name,
                "--ignore-daemonsets",
                "--delete-emptydir-data",
                "--timeout=300s",
                "--force",
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            output = stdout.decode("utf-8", errors="replace")

            if process.returncode == 0:
                return True, output
            else:
                return False, f"Drain failed: {output}"

        except Exception as e:
            return False, f"Drain error: {str(e)}"

    async def delete_node(self, node_name: str) -> Tuple[bool, str]:
        """Delete a node from the kubernetes cluster."""
        try:
            v1 = client.CoreV1Api()
            v1.delete_node(node_name)
            return True, f"Node {node_name} deleted from cluster"
        except Exception as e:
            return False, f"Delete failed: {str(e)}"

    def validate_inventory(self) -> Dict[str, Any]:
        """Validate the inventory file by attempting to parse it."""
        try:
            inventory = self.read_inventory()
            if "all" not in inventory:
                return {"valid": False, "error": "Missing 'all' top-level key"}
            if "children" not in inventory["all"]:
                return {"valid": False, "error": "Missing 'children' under 'all'"}
            return {"valid": True}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    def update_build_platforms(self) -> Dict[str, Any]:
        """Update container_build_platforms in inventory based on cluster architectures.

        Returns the old and new platform values so callers can detect changes.
        """
        architectures = self.get_cluster_architectures()
        platforms = ",".join(f"linux/{arch}" for arch in sorted(architectures))

        if not platforms:
            return {"changed": False, "platforms": ""}

        inventory = self.read_inventory()
        all_vars = inventory["all"].get("vars", {})
        old_platforms = all_vars.get("container_build_platforms", "")

        if old_platforms == platforms:
            return {"changed": False, "platforms": platforms}

        self.backup_inventory()
        all_vars["container_build_platforms"] = platforms
        inventory["all"]["vars"] = all_vars
        self.write_inventory(inventory)

        logger.info(f"Updated container_build_platforms: {old_platforms!r} -> {platforms!r}")
        return {
            "changed": True,
            "old_platforms": old_platforms,
            "platforms": platforms,
            "architectures": architectures,
        }

    def get_rebuild_actions(self, new_arch: str) -> List[Dict[str, str]]:
        """Return list of rebuild actions needed when a new architecture is introduced."""
        actions = [
            {
                "action": "rebuild_base_images",
                "description": f"Rebuild base images with multi-arch support (including {new_arch})",
                "playbook": "14_build_base_images.yaml",
            },
            {
                "action": "remirror_images",
                "description": f"Re-mirror public images to include {new_arch} platform",
                "detail": "Image mirror role will create manifest lists with all platforms",
            },
            {
                "action": "rebuild_app_images",
                "description": "Rebuild application container images for all architectures",
                "detail": "Argo Workflows will generate per-arch build steps with manifest creation",
            },
            {
                "action": "rebuild_venvs",
                "description": f"Rebuild Jupyter venvs on a {new_arch} node",
                "detail": "Venvs contain native binaries and need per-architecture builds",
            },
        ]
        return actions


node_manager = NodeManager()
