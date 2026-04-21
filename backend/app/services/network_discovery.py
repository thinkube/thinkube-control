"""Network discovery service for finding nodes available to join the cluster.

Supports two modes:
- Overlay (ZeroTier): queries the ZeroTier Central API for authorized+online members
- Local: ping sweeps the network CIDR and checks SSH banners for Ubuntu hosts

In both modes, existing cluster nodes and MetalLB VIP ranges are filtered out.
"""

import asyncio
import ipaddress
import logging
import socket
from typing import Any, Dict, List, Optional, Set

import httpx

from app.services.ansible_environment import ansible_env
from app.services.node_manager import node_manager

logger = logging.getLogger(__name__)

ZEROTIER_API_BASE = "https://api.zerotier.com/api/v1"
PING_BATCH_SIZE = 20
PING_TIMEOUT = 2
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

    def get_excluded_ips(self) -> Set[str]:
        """Get all IPs that should be excluded from discovery results.

        Excludes:
        - Existing cluster node IPs (from inventory)
        - MetalLB VIP range
        - Gateway IP
        """
        excluded = set()
        inventory = node_manager.read_inventory()
        inv_vars = inventory.get("all", {}).get("vars", {})
        children = inventory.get("all", {}).get("children", {})

        # Existing node IPs from inventory
        for section_name, section in children.items():
            hosts = section.get("hosts", {})
            if hosts:
                for hostname, host_vars in hosts.items():
                    if isinstance(host_vars, dict):
                        if host_vars.get("ansible_host"):
                            excluded.add(host_vars["ansible_host"])
                        if host_vars.get("lan_ip"):
                            excluded.add(host_vars["lan_ip"])
                        if host_vars.get("zerotier_ip"):
                            excluded.add(host_vars["zerotier_ip"])
            # Check nested children
            for sub_name, sub_section in section.get("children", {}).items():
                if isinstance(sub_section, dict):
                    sub_hosts = sub_section.get("hosts", {})
                    if sub_hosts:
                        for hostname, host_vars in sub_hosts.items():
                            if isinstance(host_vars, dict):
                                if host_vars.get("ansible_host"):
                                    excluded.add(host_vars["ansible_host"])
                                if host_vars.get("lan_ip"):
                                    excluded.add(host_vars["lan_ip"])
                                if host_vars.get("zerotier_ip"):
                                    excluded.add(host_vars["zerotier_ip"])

        # MetalLB VIP range
        network_mode = inv_vars.get("network_mode", "overlay")
        start_octet = int(inv_vars.get("metallb_ip_start_octet", "50"))
        end_octet = int(inv_vars.get("metallb_ip_end_octet", "55"))

        if network_mode == "overlay":
            prefix = inv_vars.get("zerotier_subnet_prefix", "192.168.191.")
        else:
            cidr = inv_vars.get("network_cidr", "")
            if cidr:
                net = ipaddress.ip_network(cidr, strict=False)
                prefix = ".".join(str(net.network_address).split(".")[:3]) + "."
            else:
                prefix = ""

        if prefix:
            for octet in range(start_octet, end_octet + 1):
                excluded.add(f"{prefix}{octet}")

        # Gateway
        gateway = inv_vars.get("network_gateway")
        if gateway:
            excluded.add(gateway)

        excluded.discard("")
        excluded.discard(None)
        return {ip for ip in excluded if ip}

    def get_next_available_zerotier_ip(self) -> Optional[str]:
        """Find the next available IP in the ZeroTier subnet.

        Skips IPs used by existing nodes and the MetalLB VIP range.
        Starts from .10 (reserving .1-.9 for infrastructure).
        """
        excluded = self.get_excluded_ips()
        inv_vars = self._get_inventory_vars()
        prefix = inv_vars.get("zerotier_subnet_prefix", "192.168.191.")

        for octet in range(10, 250):
            ip = f"{prefix}{octet}"
            if ip not in excluded:
                return ip
        return None

    async def discover_zerotier_nodes(self) -> List[DiscoveredNetworkNode]:
        """Query ZeroTier Central API for authorized+online members."""
        inv_vars = self._get_inventory_vars()
        network_id = inv_vars.get("zerotier_network_id")
        api_token = inv_vars.get("zerotier_api_token")

        if not network_id or not api_token:
            logger.error("ZeroTier network_id or api_token not in inventory")
            return []

        excluded = self.get_excluded_ips()
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
        """Try to resolve a hostname for an IP via reverse DNS."""
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, socket.gethostbyaddr, ip),
                timeout=2,
            )
            return result[0]
        except Exception:
            return None

    async def discover_local_nodes(self) -> List[DiscoveredNetworkNode]:
        """Ping sweep the network CIDR and check SSH banners."""
        inv_vars = self._get_inventory_vars()
        network_cidr = inv_vars.get("network_cidr")

        if not network_cidr:
            logger.error("network_cidr not in inventory")
            return []

        excluded = self.get_excluded_ips()
        network = ipaddress.ip_network(network_cidr, strict=False)
        candidate_ips = [str(ip) for ip in network.hosts() if str(ip) not in excluded]

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

    async def discover(self) -> Dict[str, Any]:
        """Run discovery based on the cluster's network mode.

        Returns discovered nodes and metadata.
        """
        network_mode = self.get_network_mode()

        if network_mode == "overlay":
            nodes = await self.discover_zerotier_nodes()
        else:
            nodes = await self.discover_local_nodes()

        return {
            "nodes": [n.to_dict() for n in nodes],
            "network_mode": network_mode,
            "node_count": len(nodes),
        }


network_discovery = NetworkDiscovery()
