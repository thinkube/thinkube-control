"""Network discovery service for finding nodes available to join the cluster.

Supports two modes:
- Overlay (ZeroTier): queries the ZeroTier Central API for authorized+online members
- Local: ping sweeps the network CIDR and checks SSH banners for Ubuntu hosts

ZeroTier Central is the source of truth for IP allocation — all assigned IPs
(members, MetalLB, infrastructure) are tracked there.
"""

import asyncio
import ipaddress
import logging
import os
import socket
from typing import Any, Dict, List, Optional, Set

import httpx

from app.services.ansible_environment import ansible_env
from app.services.node_manager import node_manager

logger = logging.getLogger(__name__)

ZEROTIER_API_BASE = "https://api.zerotier.com/api/v1"
PING_BATCH_SIZE = 50
PING_TIMEOUT = 1
SSH_BANNER_TIMEOUT = 3


class DiscoveredNetworkNode:
    """A node discovered on the network that could potentially join the cluster."""

    def __init__(
        self,
        ip: str,
        hostname: Optional[str] = None,
        zerotier_ip: Optional[str] = None,
        zerotier_node_id: Optional[str] = None,
        ssh_available: bool = False,
        ssh_banner: Optional[str] = None,
        is_ubuntu: bool = False,
        confidence: str = "possible",
    ):
        self.ip = ip
        self.hostname = hostname
        self.zerotier_ip = zerotier_ip
        self.zerotier_node_id = zerotier_node_id
        self.ssh_available = ssh_available
        self.ssh_banner = ssh_banner
        self.is_ubuntu = is_ubuntu
        self.confidence = confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ip": self.ip,
            "hostname": self.hostname,
            "zerotier_ip": self.zerotier_ip,
            "zerotier_node_id": self.zerotier_node_id,
            "ssh_available": self.ssh_available,
            "ssh_banner": self.ssh_banner,
            "is_ubuntu": self.is_ubuntu,
            "confidence": self.confidence,
        }


class NetworkDiscovery:
    """Discovers available nodes on the network for cluster joining."""

    def __init__(self):
        self.inventory_path = ansible_env.get_inventory_path()

    def _get_inventory_vars(self) -> Dict[str, Any]:
        inventory = node_manager.read_inventory()
        return inventory.get("all", {}).get("vars", {})

    def get_network_mode(self) -> str:
        return self._get_inventory_vars().get("network_mode", "overlay")

    async def _get_all_assigned_ips(self) -> Set[str]:
        """Query ZeroTier Central for all assigned IPs in the network.

        ZeroTier Central is the source of truth for IP allocation.
        """
        inv_vars = self._get_inventory_vars()
        network_id = inv_vars.get("zerotier_network_id")
        api_token = inv_vars.get("zerotier_api_token")

        if not network_id or not api_token:
            raise RuntimeError("zerotier_network_id and zerotier_api_token required in inventory")

        assigned = set()
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{ZEROTIER_API_BASE}/network/{network_id}/member",
                headers={"Authorization": f"bearer {api_token}"},
            )
            response.raise_for_status()
            for member in response.json():
                for ip in member.get("config", {}).get("ipAssignments", []):
                    assigned.add(ip)
        return assigned

    async def _get_cluster_node_ips(self) -> Set[str]:
        """Get IPs of nodes already in the k8s cluster (for discovery filtering)."""
        cluster_ips = set()
        try:
            from kubernetes import client as k8s_client
            loop = asyncio.get_event_loop()
            v1 = k8s_client.CoreV1Api()
            k8s_nodes = await loop.run_in_executor(None, v1.list_node)
            for node in k8s_nodes.items:
                for addr in (node.status.addresses or []):
                    if addr.type in ("InternalIP", "ExternalIP"):
                        cluster_ips.add(addr.address)
        except Exception as e:
            logger.warning(f"Could not query k8s nodes: {e}")
        return cluster_ips

    async def get_next_available_zerotier_ip(self) -> Optional[str]:
        """Find the next available IP in the ZeroTier subnet.

        Queries ZeroTier Central for all assigned IPs, then finds the first
        gap starting from .10.
        """
        assigned = await self._get_all_assigned_ips()
        inv_vars = self._get_inventory_vars()
        prefix = inv_vars.get("zerotier_subnet_prefix", "192.168.191.")

        for octet in range(10, 250):
            ip = f"{prefix}{octet}"
            if ip not in assigned:
                return ip
        return None

    async def discover_zerotier_nodes(self) -> List[DiscoveredNetworkNode]:
        """Query ZeroTier Central API for authorized+online members not yet in the cluster."""
        inv_vars = self._get_inventory_vars()
        network_id = inv_vars.get("zerotier_network_id")
        api_token = inv_vars.get("zerotier_api_token")

        if not network_id or not api_token:
            logger.error("ZeroTier network_id or api_token not in inventory")
            return []

        excluded = await self._get_cluster_node_ips()
        nodes = []

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{ZEROTIER_API_BASE}/network/{network_id}/member",
                    headers={"Authorization": f"bearer {api_token}"},
                )
                response.raise_for_status()
                members = response.json()

            for member in members:
                config = member.get("config", {})
                if not config.get("authorized"):
                    continue

                ip_assignments = config.get("ipAssignments", [])
                if not ip_assignments:
                    continue

                zt_ip = ip_assignments[0]
                if zt_ip in excluded:
                    continue

                node = DiscoveredNetworkNode(
                    ip=zt_ip,
                    hostname=member.get("name") or None,
                    zerotier_ip=zt_ip,
                    zerotier_node_id=member.get("nodeId"),
                    confidence="possible",
                )
                nodes.append(node)

        except Exception as e:
            logger.error(f"ZeroTier API error: {e}")

        return nodes

    async def _ping_host(self, ip: str) -> bool:
        """Ping a single host. Returns True if reachable."""
        try:
            process = await asyncio.create_subprocess_exec(
                "ping", "-c", "1", "-W", str(PING_TIMEOUT), "-w", str(PING_TIMEOUT),
                ip,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=PING_TIMEOUT + 1)
            return process.returncode == 0
        except (asyncio.TimeoutError, Exception):
            return False

    async def _check_ssh_banner(self, ip: str) -> Dict[str, Any]:
        """Check SSH banner on port 22 to detect OS type."""
        result = {
            "ssh_available": False,
            "ssh_banner": None,
            "is_ubuntu": False,
        }
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, 22),
                timeout=SSH_BANNER_TIMEOUT,
            )
            banner = await asyncio.wait_for(
                reader.readline(),
                timeout=SSH_BANNER_TIMEOUT,
            )
            writer.close()
            await writer.wait_closed()

            banner_str = banner.decode("utf-8", errors="replace").strip()
            result["ssh_available"] = True
            result["ssh_banner"] = banner_str
            result["is_ubuntu"] = self._is_ubuntu_banner(banner_str)
        except Exception:
            pass
        return result

    def _is_ubuntu_banner(self, banner: str) -> bool:
        if "Ubuntu" in banner:
            return True
        ubuntu_ssh_versions = ["8.9p1", "9.0p1", "9.3p1", "9.6p1", "9.9p1"]
        return any(v in banner for v in ubuntu_ssh_versions)

    async def _resolve_hostname(self, ip: str) -> Optional[str]:
        """Try to resolve a hostname for an IP via reverse DNS.

        Discards Kubernetes-internal names (*.svc.cluster.local) since those
        are synthetic and don't reflect the actual machine hostname.
        """
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, socket.gethostbyaddr, ip),
                timeout=2,
            )
            hostname = result[0]
            if ".svc.cluster.local" in hostname:
                return None
            return hostname
        except Exception:
            return None

    async def discover_local_nodes(
        self, scan_cidrs: Optional[List[str]] = None
    ) -> List[DiscoveredNetworkNode]:
        """Ping sweep one or more CIDRs and check SSH banners.

        Args:
            scan_cidrs: CIDRs to scan. Defaults to the inventory's network_cidr.
        """
        if not scan_cidrs:
            inv_vars = self._get_inventory_vars()
            network_cidr = inv_vars.get("network_cidr")
            if not network_cidr:
                logger.error("network_cidr not in inventory")
                return []
            scan_cidrs = [network_cidr]

        excluded = await self._get_cluster_node_ips()
        candidate_ips = []
        for cidr in scan_cidrs:
            try:
                network = ipaddress.ip_network(cidr.strip(), strict=False)
                candidate_ips.extend(
                    str(ip) for ip in network.hosts() if str(ip) not in excluded
                )
            except ValueError as e:
                logger.error(f"Invalid CIDR {cidr!r}: {e}")
                continue

        # Ping sweep in batches
        reachable = []
        for i in range(0, len(candidate_ips), PING_BATCH_SIZE):
            batch = candidate_ips[i:i + PING_BATCH_SIZE]
            results = await asyncio.gather(
                *[self._ping_host(ip) for ip in batch]
            )
            for ip, is_up in zip(batch, results):
                if is_up:
                    reachable.append(ip)

        # Check SSH banners in parallel
        banner_results = await asyncio.gather(
            *[self._check_ssh_banner(ip) for ip in reachable]
        )

        # Resolve hostnames in parallel
        hostname_results = await asyncio.gather(
            *[self._resolve_hostname(ip) for ip in reachable]
        )

        nodes = []
        for ip, banner_info, hostname in zip(reachable, banner_results, hostname_results):
            if not banner_info["ssh_available"]:
                continue

            confidence = "confirmed" if banner_info["is_ubuntu"] else "possible"
            node = DiscoveredNetworkNode(
                ip=ip,
                hostname=hostname,
                ssh_available=banner_info["ssh_available"],
                ssh_banner=banner_info["ssh_banner"],
                is_ubuntu=banner_info["is_ubuntu"],
                confidence=confidence,
            )
            nodes.append(node)

        return nodes

    async def _verify_ssh_auth(self, ip: str) -> bool:
        """Check if we can SSH into a node using cluster key or env password."""
        from app.services.node_manager import node_manager

        if await node_manager.test_ssh_key_auth(ip):
            return True

        password = os.environ.get("ANSIBLE_BECOME_PASSWORD") or os.environ.get("SYSTEM_PASSWORD")
        if not password:
            return False

        result = await node_manager.distribute_ssh_key(ip, password)
        return result.get("success", False)

    async def discover(
        self, scan_cidrs: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Discover eligible nodes by scanning CIDRs.

        Only returns nodes that:
        - Are running Ubuntu
        - Have SSH available
        - Accept the cluster SSH key or env password

        Args:
            scan_cidrs: CIDRs to scan. Defaults to the inventory's network_cidr.
        """
        network_mode = self.get_network_mode()
        nodes = await self.discover_local_nodes(scan_cidrs)

        # Filter: Ubuntu only
        ubuntu_nodes = [n for n in nodes if n.is_ubuntu]

        # Filter: SSH auth must work (key or env password)
        eligible = []
        ssh_results = await asyncio.gather(
            *[self._verify_ssh_auth(n.ip) for n in ubuntu_nodes]
        )
        for node, ssh_ok in zip(ubuntu_nodes, ssh_results):
            if ssh_ok:
                eligible.append(node)

        # Resolve hostnames via SSH for eligible nodes (always prefer SSH over reverse DNS)
        for node in eligible:
            ssh_hostname = await self._get_hostname_via_ssh(node.ip)
            if ssh_hostname:
                node.hostname = ssh_hostname

        return {
            "nodes": [n.to_dict() for n in eligible],
            "network_mode": network_mode,
            "node_count": len(eligible),
        }

    async def _get_hostname_via_ssh(self, ip: str) -> Optional[str]:
        """Get hostname from a node via SSH."""
        ssh_key_path = ansible_env.get_ssh_key_path()
        username = os.environ["SYSTEM_USERNAME"]
        cmd = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -i {ssh_key_path} {username}@{ip} hostname"
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd.split(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
            if process.returncode == 0:
                return stdout.decode().strip()
        except Exception:
            pass
        return None


network_discovery = NetworkDiscovery()
