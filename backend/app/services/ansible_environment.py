"""
Shared Ansible environment configuration for all executors.
Ensures WebSocket and background executors use identical context.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class AnsibleEnvironment:
    """Manages shared Ansible environment configuration."""

    def __init__(self):
        self.shared_code_path = Path("/home")
        self.thinkube_control_path = self.shared_code_path / "thinkube-control"

    def validate_paths(self) -> Dict[str, Any]:
        """Validate required paths exist."""
        errors = []

        if not self.shared_code_path.exists():
            errors.append("Shared-code directory not mounted at /home")

        if not self.thinkube_control_path.exists():
            errors.append("thinkube-control not found in shared-code")

        inventory_path = self.get_inventory_path()
        if not inventory_path.exists():
            errors.append(f"Inventory not found at {inventory_path}")

        return {"valid": len(errors) == 0, "errors": errors}

    def get_inventory_path(self) -> Path:
        """Get the inventory path from shared location."""
        return Path("/home/.ansible/inventory/inventory.yaml")

    def get_playbook_path(
        self, playbook_name: str, working_dir: Optional[Path] = None
    ) -> Path:
        """Get the full path to a playbook."""
        if working_dir:
            return working_dir / playbook_name
        return self.thinkube_control_path / "playbooks" / playbook_name

    def get_roles_path(self, context: str = "template") -> str:
        """Get the Ansible roles path based on context.

        Args:
            context: Either 'template' or 'optional' to determine which roles to use

        Returns:
            The roles path for the given context
        """
        if context == "optional":
            # ONLY thinkube-platform roles for optional components
            return "/home/thinkube-platform/thinkube/ansible/roles"
        else:
            # ONLY thinkube-control roles for templates (default)
            return str(self.thinkube_control_path / "ansible" / "roles")

    def get_ssh_key_path(self) -> Path:
        """Get the SSH key path."""
        # Use thinkube_cluster_key which is the authorized cluster key
        return self.shared_code_path / ".ssh" / "thinkube_cluster_key"

    def get_github_ssh_key_path(self) -> Path:
        """Get the GitHub SSH key path (same as regular SSH key)."""
        return self.get_ssh_key_path()

    def prepare_auth_vars(self, extra_vars: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare authentication variables."""
        # Get system username
        system_username = os.environ.get("SYSTEM_USERNAME")
        if not system_username:
            raise RuntimeError("SYSTEM_USERNAME environment variable not set")

        extra_vars["ansible_user"] = system_username

        # SSH key authentication
        ssh_key_path = self.get_ssh_key_path()
        if ssh_key_path.exists():
            extra_vars["ansible_ssh_private_key_file"] = str(ssh_key_path)
        else:
            # Try password auth as fallback
            system_password = os.environ.get("SYSTEM_PASSWORD")
            if system_password:
                extra_vars["ansible_ssh_pass"] = system_password
                extra_vars["ansible_become_pass"] = system_password
            else:
                raise RuntimeError(
                    f"No SSH key at {ssh_key_path} or password available"
                )

        # Add become password if available
        ansible_become_password = os.environ.get("ANSIBLE_BECOME_PASSWORD")
        if ansible_become_password:
            extra_vars["ansible_become_pass"] = ansible_become_password

        # Add master node information if available
        master_node_name = os.environ.get("MASTER_NODE_NAME")
        if master_node_name:
            extra_vars["master_node_name"] = master_node_name

        master_node_ip = os.environ.get("MASTER_NODE_IP")
        if master_node_ip:
            extra_vars["master_node_ip"] = master_node_ip

        return extra_vars

    def get_environment(self, context: str = "template") -> Dict[str, str]:
        """Get the complete environment for Ansible execution.

        Args:
            context: Either 'template' or 'optional' to determine which roles to use

        Returns:
            Environment variables dict for Ansible execution
        """
        env = os.environ.copy()

        # Python settings
        env["PYTHONUNBUFFERED"] = "1"

        # Ansible settings
        env["ANSIBLE_FORCE_COLOR"] = "0"
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
        env["ANSIBLE_STDOUT_CALLBACK"] = "default"
        env["ANSIBLE_ROLES_PATH"] = self.get_roles_path(context)

        # Ansible config
        ansible_cfg = self.thinkube_control_path / "ansible.cfg"
        if ansible_cfg.exists():
            env["ANSIBLE_CONFIG"] = str(ansible_cfg)

        # CRITICAL: Set HOME to ensure Git/SSH can find config and keys
        env["HOME"] = str(self.shared_code_path)

        # Git SSH configuration
        github_ssh_key = self.get_github_ssh_key_path()
        if github_ssh_key.exists():
            env["GIT_SSH_COMMAND"] = (
                f"ssh -i {github_ssh_key} -o StrictHostKeyChecking=no"
            )

        # Pass through GitHub token if available
        github_token = os.environ.get("GITHUB_TOKEN")
        if github_token:
            env["GITHUB_TOKEN"] = github_token

        # Pass through ADMIN_PASSWORD for playbooks that need it
        admin_password = os.environ.get("ADMIN_PASSWORD")
        if admin_password:
            env["ADMIN_PASSWORD"] = admin_password

        # Node information from environment (required for deployments)
        # These are set in the backend deployment and needed by the Ansible roles
        master_node_name = os.environ.get("MASTER_NODE_NAME")
        if master_node_name:
            env["MASTER_NODE_NAME"] = master_node_name

        master_node_ip = os.environ.get("MASTER_NODE_IP")
        if master_node_ip:
            env["MASTER_NODE_IP"] = master_node_ip

        return env

    def get_command_base(
        self, playbook_path: Path, inventory_path: Path, temp_vars_path: str
    ) -> list:
        """Get the base ansible-playbook command."""
        return [
            "ansible-playbook",
            "-i",
            str(inventory_path),
            str(playbook_path),
            "-e",
            f"@{temp_vars_path}",
            "-v",
        ]

    def get_command_with_buffer(
        self, playbook_path: Path, inventory_path: Path, temp_vars_path: str
    ) -> list:
        """Get the ansible-playbook command with line buffering."""
        return [
            "stdbuf",
            "-oL",
            "-eL",  # Line buffering
        ] + self.get_command_base(playbook_path, inventory_path, temp_vars_path)


# Global instance
ansible_env = AnsibleEnvironment()
