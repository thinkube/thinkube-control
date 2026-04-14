"""
GitHub publisher service for Thinkube Control.

Publishes deployed apps as reusable templates to the user's GitHub org.
Handles repo creation, code push, and metadata repo updates.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

# Files/directories to exclude when publishing an app as a template
PUBLISH_EXCLUDE_PATTERNS = {
    ".git",
    ".deployment-trigger",
    "k8s",  # Generated K8s manifests (platform-specific)
    ".copier-answers.yml",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
}


class GitHubPublisher:
    """Publishes deployed apps as templates to the user's GitHub org."""

    def __init__(self):
        self.github_token = os.environ.get("GITHUB_TOKEN", "")
        self.github_org = os.environ.get("GITHUB_ORG", "")
        self.api_base = "https://api.github.com"

        if not self.github_token:
            raise RuntimeError("GITHUB_TOKEN not configured")
        if not self.github_org:
            raise RuntimeError("GITHUB_ORG not configured")

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "thinkube-control",
        }

    async def create_or_update_repo(
        self,
        name: str,
        description: str,
        private: bool = True,
    ) -> Dict:
        """Create a GitHub repo in the user's org, or return existing one."""
        async with aiohttp.ClientSession(headers=self._headers) as session:
            # Check if repo exists
            url = f"{self.api_base}/repos/{self.github_org}/{name}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    repo_data = await resp.json()
                    logger.info(f"Repo {self.github_org}/{name} already exists, will update")

                    # Update description if changed
                    if repo_data.get("description") != description:
                        async with session.patch(url, json={
                            "description": description,
                            "private": private,
                        }) as patch_resp:
                            if patch_resp.status == 200:
                                repo_data = await patch_resp.json()

                    return repo_data

            # Create new repo
            create_url = f"{self.api_base}/orgs/{self.github_org}/repos"
            payload = {
                "name": name,
                "description": description,
                "private": private,
                "auto_init": False,
                "has_issues": True,
                "has_projects": False,
                "has_wiki": False,
            }

            async with session.post(create_url, json=payload) as resp:
                if resp.status == 201:
                    repo_data = await resp.json()
                    logger.info(f"Created repo {self.github_org}/{name}")
                    return repo_data
                elif resp.status == 422:
                    # Might be a personal account, try user endpoint
                    create_url = f"{self.api_base}/user/repos"
                    payload["name"] = name
                    async with session.post(create_url, json=payload) as resp2:
                        if resp2.status == 201:
                            repo_data = await resp2.json()
                            logger.info(f"Created repo {self.github_org}/{name} (user account)")
                            return repo_data
                        body = await resp2.text()
                        raise RuntimeError(f"Failed to create repo: {resp2.status} {body}")
                else:
                    body = await resp.text()
                    raise RuntimeError(f"Failed to create repo: {resp.status} {body}")

    def push_template_code(
        self,
        repo_name: str,
        source_path: Path,
        exclude_patterns: Optional[set] = None,
    ) -> None:
        """Copy app source to a temp directory, init git, and push to GitHub."""
        if exclude_patterns is None:
            exclude_patterns = PUBLISH_EXCLUDE_PATTERNS

        import tempfile
        staging_dir = Path(tempfile.mkdtemp(prefix="thinkube-publish-"))

        try:
            # Copy files, excluding patterns
            for item in source_path.iterdir():
                if item.name in exclude_patterns:
                    continue
                dest = staging_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, ignore=shutil.ignore_patterns(*exclude_patterns))
                else:
                    shutil.copy2(item, dest)

            # Init git and push
            remote_url = f"https://x-access-token:{self.github_token}@github.com/{self.github_org}/{repo_name}.git"

            cmds = [
                ["git", "init"],
                ["git", "checkout", "-b", "main"],
                ["git", "config", "user.name", "thinkube-control"],
                ["git", "config", "user.email", f"thinkube-control@{os.environ.get('DOMAIN_NAME', 'thinkube.com')}"],
                ["git", "add", "-A"],
                ["git", "commit", "-m", "Publish as template from Thinkube"],
                ["git", "remote", "add", "origin", remote_url],
                ["git", "push", "-u", "origin", "main", "--force"],
            ]

            for cmd in cmds:
                result = subprocess.run(
                    cmd,
                    cwd=staging_dir,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0 and cmd[1] != "remote":
                    # git remote add fails if already exists, that's ok
                    raise RuntimeError(
                        f"Git command failed: {' '.join(cmd)}\n"
                        f"stderr: {result.stderr}"
                    )

            logger.info(f"Pushed template code to {self.github_org}/{repo_name}")

        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    async def update_metadata_repo(
        self,
        template_entry: Dict,
    ) -> None:
        """Add or update a template entry in the user's metadata repo."""
        metadata_repo = f"{self.github_org}-metadata"
        file_path = "repositories.json"

        async with aiohttp.ClientSession(headers=self._headers) as session:
            # Ensure metadata repo exists
            await self._ensure_metadata_repo(session, metadata_repo)

            # Fetch current repositories.json
            url = f"{self.api_base}/repos/{self.github_org}/{metadata_repo}/contents/{file_path}"
            current_data = None
            sha = None

            async with session.get(url) as resp:
                if resp.status == 200:
                    file_info = await resp.json()
                    sha = file_info["sha"]
                    import base64
                    content = base64.b64decode(file_info["content"]).decode()
                    current_data = json.loads(content)

            if current_data is None:
                current_data = {
                    "version": "1.0.0",
                    "description": f"Template metadata for {self.github_org}",
                    "repositories": [],
                }

            # Update or add the template entry
            repos = current_data.get("repositories", [])
            found = False
            for i, repo in enumerate(repos):
                if repo.get("name") == template_entry["name"]:
                    repos[i] = template_entry
                    found = True
                    break
            if not found:
                repos.append(template_entry)

            current_data["repositories"] = repos

            # Write back
            import base64
            new_content = base64.b64encode(
                json.dumps(current_data, indent=2).encode()
            ).decode()

            payload = {
                "message": f"Update template: {template_entry['name']}",
                "content": new_content,
            }
            if sha:
                payload["sha"] = sha

            async with session.put(url, json=payload) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    raise RuntimeError(
                        f"Failed to update metadata repo: {resp.status} {body}"
                    )

            logger.info(f"Updated metadata repo with template: {template_entry['name']}")

    async def _ensure_metadata_repo(
        self,
        session: aiohttp.ClientSession,
        repo_name: str,
    ) -> None:
        """Create the metadata repo if it doesn't exist."""
        url = f"{self.api_base}/repos/{self.github_org}/{repo_name}"
        async with session.get(url) as resp:
            if resp.status == 200:
                return

        # Create it
        create_url = f"{self.api_base}/orgs/{self.github_org}/repos"
        payload = {
            "name": repo_name,
            "description": f"Template metadata for {self.github_org}",
            "private": True,
            "auto_init": True,
        }

        async with session.post(create_url, json=payload) as resp:
            if resp.status == 201:
                logger.info(f"Created metadata repo {self.github_org}/{repo_name}")
                # Wait a moment for GitHub to initialize the repo
                import asyncio
                await asyncio.sleep(2)
            elif resp.status == 422:
                # Try user endpoint
                create_url = f"{self.api_base}/user/repos"
                async with session.post(create_url, json=payload) as resp2:
                    if resp2.status == 201:
                        logger.info(f"Created metadata repo {self.github_org}/{repo_name} (user account)")
                        import asyncio
                        await asyncio.sleep(2)
                    else:
                        body = await resp2.text()
                        raise RuntimeError(f"Failed to create metadata repo: {resp2.status} {body}")
            else:
                body = await resp.text()
                raise RuntimeError(f"Failed to create metadata repo: {resp.status} {body}")

    async def publish_app_as_template(
        self,
        app_name: str,
        template_name: str,
        description: str,
        tags: List[str],
        private: bool = True,
    ) -> Dict:
        """
        Full publish flow: create repo, push code, update metadata.

        Returns summary dict with repo URL and metadata status.
        """
        apps_dir = Path("/home/thinkube/apps")
        app_path = apps_dir / app_name

        if not app_path.exists():
            raise FileNotFoundError(f"App '{app_name}' not found at {app_path}")

        # Validate thinkube.yaml and manifest.yaml exist
        has_thinkube_yaml = (app_path / "thinkube.yaml").exists()
        has_manifest_yaml = (app_path / "manifest.yaml").exists()
        if not has_thinkube_yaml and not has_manifest_yaml:
            raise ValueError(
                f"App '{app_name}' has neither thinkube.yaml nor manifest.yaml — "
                "cannot publish as template"
            )

        # Step 1: Create or update GitHub repo
        repo_data = await self.create_or_update_repo(
            name=template_name,
            description=description,
            private=private,
        )
        repo_url = repo_data.get("html_url", f"https://github.com/{self.github_org}/{template_name}")

        # Step 2: Push code
        self.push_template_code(
            repo_name=template_name,
            source_path=app_path,
        )

        # Step 3: Update metadata repo
        template_entry = {
            "name": template_name,
            "org": self.github_org,
            "full_name": f"{self.github_org}/{template_name}",
            "description": description,
            "type": "application_template",
            "github_url": repo_url,
            "ssh_url": f"git@github.com:{self.github_org}/{template_name}.git",
            "clone_for_development": False,
        }

        # Determine deployment type from thinkube.yaml
        if has_thinkube_yaml:
            try:
                import yaml
                with open(app_path / "thinkube.yaml") as f:
                    thinkube_config = yaml.safe_load(f)
                deployment_type = thinkube_config.get("spec", {}).get("type", "app")
                template_entry["deployment_type"] = deployment_type
            except Exception:
                template_entry["deployment_type"] = "app"

        if tags:
            template_entry["tags"] = tags

        await self.update_metadata_repo(template_entry)

        # Step 4: Invalidate metadata cache
        from app.services.metadata_fetcher import _memory_cache
        _memory_cache.pop("repositories", None)

        return {
            "template_name": template_name,
            "repo_url": repo_url,
            "org": self.github_org,
            "metadata_updated": True,
        }
