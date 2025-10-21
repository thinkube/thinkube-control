"""Harbor Registry API client service"""

import os
import logging
import base64
from typing import List, Dict, Any, Optional
from datetime import datetime

import httpx
from httpx import HTTPStatusError, RequestError

logger = logging.getLogger(__name__)


class HarborClient:
    """Client for interacting with Harbor Registry API v2.0"""

    def __init__(self, base_url: str = None, username: str = None, password: str = None):
        """Initialize Harbor client

        Args:
            base_url: Harbor API base URL (e.g., https://registry.thinkube.com)
            username: Harbor username (admin or robot account)
            password: Harbor password or robot token
        """
        # Get from environment if not provided
        self.base_url = base_url or os.getenv("HARBOR_URL", "https://registry.thinkube.com")
        self.username = username or os.getenv("HARBOR_USERNAME", "admin")
        self.password = password or os.getenv("HARBOR_PASSWORD", os.getenv("ADMIN_PASSWORD"))

        if not self.base_url:
            raise ValueError("Harbor URL is required")

        # Ensure base_url doesn't end with /
        self.base_url = self.base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/v2.0"

        # Create auth header
        self.auth_header = self._create_auth_header()

        # HTTP client with timeout settings
        self.client = httpx.Client(
            timeout=httpx.Timeout(30.0),
            verify=True,  # Enable SSL verification
            follow_redirects=True
        )

    def _create_auth_header(self) -> Dict[str, str]:
        """Create basic auth header"""
        if self.username and self.password:
            credentials = f"{self.username}:{self.password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
        return {}

    def _request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        """Make HTTP request to Harbor API

        Args:
            method: HTTP method
            endpoint: API endpoint (relative to base API URL)
            **kwargs: Additional request parameters

        Returns:
            HTTP response

        Raises:
            HTTPStatusError: For HTTP errors
            RequestError: For network errors
        """
        url = f"{self.api_url}{endpoint}"

        # Add auth header
        headers = kwargs.pop("headers", {})
        headers.update(self.auth_header)

        try:
            response = self.client.request(
                method=method,
                url=url,
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response
        except HTTPStatusError as e:
            logger.error(f"Harbor API error: {e.response.status_code} - {e.response.text}")
            raise
        except RequestError as e:
            logger.error(f"Harbor connection error: {e}")
            raise

    # Project Management

    def list_projects(self, public: Optional[bool] = None) -> List[Dict[str, Any]]:
        """List Harbor projects

        Args:
            public: Filter by public/private projects

        Returns:
            List of project dictionaries
        """
        params = {}
        if public is not None:
            params["public"] = str(public).lower()

        response = self._request("GET", "/projects", params=params)
        return response.json()

    def get_project(self, project_name: str) -> Dict[str, Any]:
        """Get project details

        Args:
            project_name: Name of the project

        Returns:
            Project details dictionary
        """
        response = self._request("GET", f"/projects/{project_name}")
        return response.json()

    def create_project(self, project_name: str, public: bool = False) -> Dict[str, Any]:
        """Create a new project

        Args:
            project_name: Name of the project
            public: Whether the project should be public

        Returns:
            Created project details
        """
        data = {
            "project_name": project_name,
            "public": public,
            "metadata": {
                "public": str(public).lower()
            }
        }

        response = self._request("POST", "/projects", json=data)
        return {"status": "created", "project_name": project_name}

    # Repository Management

    def list_repositories(self, project_name: str) -> List[Dict[str, Any]]:
        """List repositories in a project

        Args:
            project_name: Name of the project

        Returns:
            List of repository dictionaries
        """
        response = self._request("GET", f"/projects/{project_name}/repositories")
        return response.json()

    def get_repository(self, project_name: str, repository_name: str) -> Dict[str, Any]:
        """Get repository details

        Args:
            project_name: Name of the project
            repository_name: Name of the repository

        Returns:
            Repository details dictionary
        """
        repo_path = f"{project_name}/{repository_name}"
        response = self._request("GET", f"/repositories/{repo_path}")
        return response.json()

    def delete_repository(self, project_name: str, repository_name: str) -> bool:
        """Delete a repository

        Args:
            project_name: Name of the project
            repository_name: Name of the repository

        Returns:
            True if successful
        """
        repo_path = f"{project_name}/{repository_name}"
        self._request("DELETE", f"/repositories/{repo_path}")
        return True

    # Artifact (Image Tag) Management

    def list_artifacts(self, project_name: str, repository_name: str) -> List[Dict[str, Any]]:
        """List artifacts (tags) in a repository

        Args:
            project_name: Name of the project
            repository_name: Name of the repository

        Returns:
            List of artifact dictionaries
        """
        repo_path = f"{project_name}/{repository_name}"
        response = self._request("GET", f"/projects/{project_name}/repositories/{repository_name}/artifacts")
        return response.json()

    def get_artifact(self, project_name: str, repository_name: str, reference: str) -> Dict[str, Any]:
        """Get artifact details

        Args:
            project_name: Name of the project
            repository_name: Name of the repository
            reference: Artifact reference (tag or digest)

        Returns:
            Artifact details dictionary
        """
        response = self._request(
            "GET",
            f"/projects/{project_name}/repositories/{repository_name}/artifacts/{reference}"
        )
        return response.json()

    def delete_artifact(self, project_name: str, repository_name: str, reference: str) -> bool:
        """Delete an artifact

        Args:
            project_name: Name of the project
            repository_name: Name of the repository
            reference: Artifact reference (tag or digest)

        Returns:
            True if successful
        """
        self._request(
            "DELETE",
            f"/projects/{project_name}/repositories/{repository_name}/artifacts/{reference}"
        )
        return True

    # Vulnerability Scanning

    def scan_artifact(self, project_name: str, repository_name: str, reference: str) -> Dict[str, Any]:
        """Trigger vulnerability scan for an artifact

        Args:
            project_name: Name of the project
            repository_name: Name of the repository
            reference: Artifact reference (tag or digest)

        Returns:
            Scan initiation response
        """
        response = self._request(
            "POST",
            f"/projects/{project_name}/repositories/{repository_name}/artifacts/{reference}/scan"
        )
        return {"status": "scan_initiated", "reference": reference}

    def get_scan_report(self, project_name: str, repository_name: str, reference: str) -> Dict[str, Any]:
        """Get vulnerability scan report

        Args:
            project_name: Name of the project
            repository_name: Name of the repository
            reference: Artifact reference (tag or digest)

        Returns:
            Scan report dictionary
        """
        response = self._request(
            "GET",
            f"/projects/{project_name}/repositories/{repository_name}/artifacts/{reference}/vulnerabilities"
        )
        return response.json()

    # Replication (Mirroring)

    def create_replication_policy(self, name: str, source_registry: str,
                                  dest_namespace: str, filters: List[Dict] = None) -> Dict[str, Any]:
        """Create a replication policy for mirroring images

        Args:
            name: Policy name
            source_registry: Source registry ID
            dest_namespace: Destination namespace
            filters: List of filters for images to replicate

        Returns:
            Created policy details
        """
        data = {
            "name": name,
            "src_registry": source_registry,
            "dest_namespace": dest_namespace,
            "filters": filters or [],
            "trigger": {"type": "manual"},
            "enabled": True
        }

        response = self._request("POST", "/replication/policies", json=data)
        return response.json()

    def trigger_replication(self, policy_id: int) -> Dict[str, Any]:
        """Manually trigger a replication policy

        Args:
            policy_id: Replication policy ID

        Returns:
            Execution details
        """
        data = {"policy_id": policy_id}
        response = self._request("POST", "/replication/executions", json=data)
        return {"status": "triggered", "policy_id": policy_id}

    def get_replication_execution(self, execution_id: int) -> Dict[str, Any]:
        """Get replication execution status

        Args:
            execution_id: Execution ID

        Returns:
            Execution details dictionary
        """
        response = self._request("GET", f"/replication/executions/{execution_id}")
        return response.json()

    # System Information

    def get_system_info(self) -> Dict[str, Any]:
        """Get Harbor system information

        Returns:
            System information dictionary
        """
        response = self._request("GET", "/systeminfo")
        return response.json()

    def get_health(self) -> Dict[str, Any]:
        """Get Harbor health status

        Returns:
            Health status dictionary
        """
        response = self._request("GET", "/health")
        return response.json()

    def get_statistics(self) -> Dict[str, Any]:
        """Get Harbor statistics

        Returns:
            Statistics dictionary
        """
        response = self._request("GET", "/statistics")
        return response.json()

    # Robot Accounts

    def create_robot_account(self, project_name: str, name: str,
                             permissions: List[Dict] = None) -> Dict[str, Any]:
        """Create a robot account for a project

        Args:
            project_name: Name of the project
            name: Robot account name
            permissions: List of permissions

        Returns:
            Robot account details including token
        """
        data = {
            "name": name,
            "duration": -1,  # Never expire
            "description": f"Robot account for {project_name}",
            "permissions": permissions or [
                {
                    "kind": "project",
                    "namespace": project_name,
                    "access": [
                        {"resource": "repository", "action": "pull"},
                        {"resource": "repository", "action": "push"}
                    ]
                }
            ]
        }

        response = self._request("POST", f"/projects/{project_name}/robots", json=data)
        return response.json()

    def list_robot_accounts(self, project_name: str) -> List[Dict[str, Any]]:
        """List robot accounts for a project

        Args:
            project_name: Name of the project

        Returns:
            List of robot account dictionaries
        """
        response = self._request("GET", f"/projects/{project_name}/robots")
        return response.json()

    def close(self):
        """Close the HTTP client"""
        self.client.close()

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()