"""Node management service for discovering, adding, and removing cluster nodes."""

import asyncio
import ipaddress
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
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

        # Inventory arch groups use OS convention (x86_64/arm64)
        inv_arch_group = "arm64" if architecture.lower() in ("aarch64", "arm64") else "x86_64"

        host_def = {
            "ansible_host": zerotier_ip if network_mode == "overlay" and zerotier_ip else ip,
            "lan_ip": lan_ip or ip,
            "arch": inv_arch_group,
            "zerotier_enabled": network_mode == "overlay",
            "configure_gpu_passthrough": False,
        }

        if network_mode == "overlay" and zerotier_ip:
            host_def["zerotier_ip"] = zerotier_ip

        children["baremetal"]["hosts"][hostname] = host_def
        children["baremetal"]["children"]["headless"]["hosts"][hostname] = {}

        arch_group = inv_arch_group
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

    async def cordon_node(self, node_name: str) -> Tuple[bool, str]:
        """Cordon a node to prevent new pod scheduling."""
        try:
            v1 = client.CoreV1Api()
            body = {"spec": {"unschedulable": True}}
            v1.patch_node(node_name, body)
            return True, f"Node {node_name} cordoned"
        except Exception as e:
            return False, f"Cordon failed: {str(e)}"

    async def uncordon_node(self, node_name: str) -> Tuple[bool, str]:
        """Uncordon a node to allow pod scheduling."""
        try:
            v1 = client.CoreV1Api()
            body = {"spec": {"unschedulable": False}}
            v1.patch_node(node_name, body)
            return True, f"Node {node_name} uncordoned"
        except Exception as e:
            return False, f"Uncordon failed: {str(e)}"

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

    def get_existing_node_ips(self) -> Set[str]:
        """Get all IPs used by existing nodes in the inventory."""
        ips = set()
        try:
            inventory = self.read_inventory()
            children = inventory.get("all", {}).get("children", {})
            for section_name, section in children.items():
                if not isinstance(section, dict):
                    continue
                hosts = section.get("hosts") or {}
                for hostname, host_vars in hosts.items():
                    if isinstance(host_vars, dict):
                        for key in ("ansible_host", "lan_ip", "zerotier_ip"):
                            val = host_vars.get(key)
                            if val:
                                ips.add(val)
                for sub_name, sub_section in (section.get("children") or {}).items():
                    if not isinstance(sub_section, dict):
                        continue
                    for hostname, host_vars in (sub_section.get("hosts") or {}).items():
                        if isinstance(host_vars, dict):
                            for key in ("ansible_host", "lan_ip", "zerotier_ip"):
                                val = host_vars.get(key)
                                if val:
                                    ips.add(val)
        except Exception as e:
            logger.error(f"Failed to get existing node IPs: {e}")
        return ips

    def get_metallb_ip_range(self) -> Set[str]:
        """Get the set of MetalLB VIP addresses from inventory config."""
        ips = set()
        try:
            inventory = self.read_inventory()
            inv_vars = inventory.get("all", {}).get("vars", {})
            start = int(inv_vars.get("metallb_ip_start_octet", "50"))
            end = int(inv_vars.get("metallb_ip_end_octet", "55"))
            mode = inv_vars.get("network_mode", "overlay")

            if mode == "overlay":
                prefix = inv_vars.get("zerotier_subnet_prefix", "192.168.191.")
            else:
                cidr = inv_vars.get("network_cidr", "")
                if cidr:
                    net = ipaddress.ip_network(cidr, strict=False)
                    prefix = ".".join(str(net.network_address).split(".")[:3]) + "."
                else:
                    return ips

            for octet in range(start, end + 1):
                ips.add(f"{prefix}{octet}")
        except Exception as e:
            logger.error(f"Failed to get MetalLB IP range: {e}")
        return ips

    async def test_ssh_key_auth(self, ip: str) -> bool:
        """Test if the cluster SSH key can authenticate to a node."""
        ssh_key_path = ansible_env.get_ssh_key_path()
        username = os.environ.get("SYSTEM_USERNAME", "thinkube")

        if not ssh_key_path.exists():
            return False

        cmd = (
            f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "
            f"-o BatchMode=yes -i {ssh_key_path} "
            f"{username}@{ip} echo ok"
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd.split(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
            return process.returncode == 0
        except Exception:
            return False

    async def distribute_ssh_key(self, ip: str, password: str) -> Dict[str, Any]:
        """Copy the cluster SSH key to a new node using password authentication.

        Uses sshpass + ssh-copy-id to install the public key, then verifies
        key-based auth works.
        """
        ssh_key_path = ansible_env.get_ssh_key_path()
        username = os.environ.get("SYSTEM_USERNAME", "thinkube")

        if not ssh_key_path.exists():
            return {"success": False, "error": f"SSH key not found at {ssh_key_path}"}

        pub_key_path = Path(f"{ssh_key_path}.pub")
        if not pub_key_path.exists():
            return {"success": False, "error": f"SSH public key not found at {pub_key_path}"}

        cmd = [
            "sshpass", "-p", password,
            "ssh-copy-id",
            "-i", str(pub_key_path),
            "-o", "StrictHostKeyChecking=no",
            f"{username}@{ip}",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)

            if process.returncode != 0:
                error = stderr.decode("utf-8", errors="replace").strip()
                return {"success": False, "error": f"ssh-copy-id failed: {error}"}

            # Verify key auth works
            if await self.test_ssh_key_auth(ip):
                return {"success": True}
            else:
                return {"success": False, "error": "Key was copied but auth test failed"}

        except asyncio.TimeoutError:
            return {"success": False, "error": "SSH key distribution timed out"}
        except FileNotFoundError:
            return {"success": False, "error": "sshpass not found — install it in the backend container"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def setup_zerotier_on_node(
        self, ip: str, assigned_zt_ip: str
    ) -> Dict[str, Any]:
        """Install ZeroTier on a remote node, join the network, and authorize it.

        Steps:
        1. SSH to node, install ZeroTier, join network
        2. Get the node's ZeroTier node ID
        3. Authorize the member via ZeroTier Central API with a static IP
        4. Configure firewall and IP forwarding
        """
        ssh_key_path = ansible_env.get_ssh_key_path()
        username = os.environ.get("SYSTEM_USERNAME", "thinkube")
        inventory = self.read_inventory()
        inv_vars = inventory.get("all", {}).get("vars", {})
        network_id = inv_vars.get("zerotier_network_id")
        api_token = inv_vars.get("zerotier_api_token")

        if not network_id or not api_token:
            return {"success": False, "error": "ZeroTier network_id or api_token not in inventory"}

        ssh_opts = f"-o StrictHostKeyChecking=no -o ConnectTimeout=10 -i {ssh_key_path}"

        # Step 1: Install ZeroTier and join network
        install_script = f"""
set -e
if ! command -v zerotier-cli &>/dev/null; then
    curl -s https://install.zerotier.com | sudo bash
fi
sudo systemctl enable --now zerotier-one
sleep 2
sudo zerotier-cli join {network_id}
sleep 2
zerotier-cli info | cut -d' ' -f3
"""
        cmd = f"ssh {ssh_opts} {username}@{ip} bash -s"
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd.split(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=install_script.encode()), timeout=120
            )

            if process.returncode != 0:
                error = stderr.decode("utf-8", errors="replace").strip()
                return {"success": False, "error": f"ZeroTier install failed: {error}"}

            node_id = stdout.decode("utf-8", errors="replace").strip().split("\n")[-1]
            if not node_id or len(node_id) != 10:
                return {"success": False, "error": f"Could not extract ZeroTier node ID (got: {node_id!r})"}

        except asyncio.TimeoutError:
            return {"success": False, "error": "ZeroTier installation timed out"}
        except Exception as e:
            return {"success": False, "error": f"ZeroTier install error: {e}"}

        # Step 2: Authorize member and assign IP via ZeroTier API
        try:
            async with httpx.AsyncClient(timeout=15) as http_client:
                response = await http_client.post(
                    f"https://api.zerotier.com/api/v1/network/{network_id}/member/{node_id}",
                    headers={"Authorization": f"bearer {api_token}"},
                    json={
                        "name": "",  # will be set by hostname later
                        "description": "Added by thinkube-control",
                        "config": {
                            "authorized": True,
                            "ipAssignments": [assigned_zt_ip],
                            "noAutoAssignIps": True,
                        },
                    },
                )
                response.raise_for_status()
        except Exception as e:
            return {"success": False, "error": f"ZeroTier API authorization failed: {e}"}

        # Step 3: Configure firewall and IP forwarding
        fw_script = """
set -e
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/90-zerotier.conf
sudo sysctl -p /etc/sysctl.d/90-zerotier.conf
sudo iptables -A FORWARD -i zt+ -j ACCEPT
sudo iptables -A FORWARD -o zt+ -j ACCEPT
if command -v netfilter-persistent &>/dev/null; then
    sudo netfilter-persistent save
elif command -v iptables-save &>/dev/null; then
    sudo iptables-save | sudo tee /etc/iptables/rules.v4 > /dev/null 2>&1 || true
fi
"""
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd.split(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=fw_script.encode()), timeout=30
            )
            # Firewall setup is best-effort — don't fail on it
        except Exception:
            logger.warning("Firewall setup on new node had issues, continuing")

        return {
            "success": True,
            "zerotier_node_id": node_id,
            "zerotier_ip": assigned_zt_ip,
        }

    def validate_hardware(self, hw_info: Dict[str, Any]) -> Dict[str, Any]:
        """Validate discovered hardware meets minimum requirements.

        Returns validation result with any warnings or errors.
        """
        errors = []
        warnings = []

        os_release = hw_info.get("os_release", "")
        if "Ubuntu 24.04" not in os_release:
            errors.append(f"Requires Ubuntu 24.04 LTS (found: {os_release})")

        cpu_cores = int(hw_info.get("cpu_cores", 0))
        if cpu_cores < 16:
            errors.append(f"Requires 16+ CPU cores (found: {cpu_cores})")

        memory_gb = int(hw_info.get("memory_gb", 0))
        if memory_gb < 64:
            errors.append(f"Requires 64+ GB RAM (found: {memory_gb} GB)")

        disk_gb = int(hw_info.get("disk_gb", 0))
        if disk_gb < 500:
            warnings.append(f"Recommended 1TB+ disk (found: {disk_gb} GB)")

        if hw_info.get("k8s_installed"):
            warnings.append("k8s snap already installed — may have been in a previous cluster")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
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
