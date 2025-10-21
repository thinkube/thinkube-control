"""
Debug API endpoints for troubleshooting connectivity and DNS issues
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, List
import subprocess
import socket
import os
from pathlib import Path

from app.core.security import get_current_user

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/dns/{hostname}")
async def resolve_hostname(
    hostname: str, current_user: dict = Depends(get_current_user)
):
    """Test DNS resolution for a given hostname"""
    try:
        # Try to resolve the hostname
        ip_addresses = socket.gethostbyname_ex(hostname)

        # Also try using nslookup
        nslookup_result = subprocess.run(
            ["nslookup", hostname], capture_output=True, text=True, timeout=5
        )

        # Try using dig
        dig_result = subprocess.run(
            ["dig", hostname, "+short"], capture_output=True, text=True, timeout=5
        )

        return {
            "hostname": hostname,
            "resolved": True,
            "ip_addresses": ip_addresses[2],
            "aliases": ip_addresses[1],
            "nslookup": {
                "stdout": nslookup_result.stdout,
                "stderr": nslookup_result.stderr,
                "returncode": nslookup_result.returncode,
            },
            "dig": {
                "stdout": dig_result.stdout,
                "stderr": dig_result.stderr,
                "returncode": dig_result.returncode,
            },
        }
    except socket.gaierror as e:
        return {
            "hostname": hostname,
            "resolved": False,
            "error": str(e),
            "error_type": "gaierror",
        }
    except Exception as e:
        return {
            "hostname": hostname,
            "resolved": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }


@router.get("/connectivity/{hostname}")
async def test_connectivity(
    hostname: str, port: int = 22, current_user: dict = Depends(get_current_user)
):
    """Test network connectivity to a host"""
    results = {}

    # Test ping
    try:
        ping_result = subprocess.run(
            ["ping", "-c", "3", hostname], capture_output=True, text=True, timeout=10
        )
        results["ping"] = {
            "success": ping_result.returncode == 0,
            "stdout": ping_result.stdout,
            "stderr": ping_result.stderr,
        }
    except Exception as e:
        results["ping"] = {"success": False, "error": str(e)}

    # Test TCP connection
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((hostname, port))
        sock.close()
        results["tcp"] = {"port": port, "success": result == 0, "error_code": result}
    except Exception as e:
        results["tcp"] = {"port": port, "success": False, "error": str(e)}

    # Test traceroute
    try:
        traceroute_result = subprocess.run(
            ["traceroute", "-n", "-m", "10", hostname],
            capture_output=True,
            text=True,
            timeout=15,
        )
        results["traceroute"] = {
            "stdout": traceroute_result.stdout,
            "stderr": traceroute_result.stderr,
            "returncode": traceroute_result.returncode,
        }
    except Exception as e:
        results["traceroute"] = {"error": str(e)}

    return {"hostname": hostname, "results": results}


@router.get("/environment")
async def get_environment(current_user: dict = Depends(get_current_user)):
    """Get environment information for debugging"""
    # Get network interfaces
    try:
        ip_result = subprocess.run(
            ["ip", "addr", "show"], capture_output=True, text=True
        )
        interfaces = ip_result.stdout
    except:
        interfaces = "Could not get network interfaces"

    # Get routing table
    try:
        route_result = subprocess.run(
            ["ip", "route", "show"], capture_output=True, text=True
        )
        routes = route_result.stdout
    except:
        routes = "Could not get routing table"

    # Get DNS configuration
    dns_config = {}
    try:
        if os.path.exists("/etc/resolv.conf"):
            with open("/etc/resolv.conf", "r") as f:
                dns_config["resolv.conf"] = f.read()
    except:
        dns_config["resolv.conf"] = "Could not read /etc/resolv.conf"

    # Get hosts file
    try:
        if os.path.exists("/etc/hosts"):
            with open("/etc/hosts", "r") as f:
                dns_config["hosts"] = f.read()
    except:
        dns_config["hosts"] = "Could not read /etc/hosts"

    # Get inventory if available
    inventory_info = {}
    inventory_path = Path("/home/.ansible/inventory/inventory.yaml")
    if inventory_path.exists():
        try:
            with open(inventory_path, "r") as f:
                # Just get first 50 lines to avoid exposing secrets
                lines = f.readlines()[:50]
                inventory_info["exists"] = True
                inventory_info["preview"] = "".join(lines)
        except:
            inventory_info["exists"] = True
            inventory_info["error"] = "Could not read inventory"
    else:
        inventory_info["exists"] = False
        inventory_info["path"] = str(inventory_path)

    return {
        "hostname": socket.gethostname(),
        "network_interfaces": interfaces,
        "routing_table": routes,
        "dns_configuration": dns_config,
        "inventory": inventory_info,
        "environment_variables": {
            k: v
            for k, v in os.environ.items()
            if k.startswith(("ANSIBLE_", "HOME", "PATH", "HOSTNAME"))
        },
    }


@router.get("/ssh-test/{hostname}")
async def test_ssh(
    hostname: str,
    username: str = "thinkube",
    current_user: dict = Depends(get_current_user),
):
    """Test SSH connectivity to a host"""
    try:
        # Test SSH connection
        ssh_result = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=5",
                "-o",
                "BatchMode=yes",
                f"{username}@{hostname}",
                "echo",
                "SSH test successful",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        return {
            "hostname": hostname,
            "username": username,
            "success": ssh_result.returncode == 0,
            "stdout": ssh_result.stdout,
            "stderr": ssh_result.stderr,
            "returncode": ssh_result.returncode,
        }
    except Exception as e:
        return {
            "hostname": hostname,
            "username": username,
            "success": False,
            "error": str(e),
        }
