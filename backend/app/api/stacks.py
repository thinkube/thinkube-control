"""
Stack deployment API endpoints.
Handles deploying ThinkubeStack manifests — groups of connected templates
deployed in dependency order.
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from typing import Dict, Any, List, Optional
from uuid import uuid4
from collections import defaultdict, deque
import logging
import os

import aiohttp
import yaml
from sqlalchemy.orm import Session

from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.deployments import TemplateDeployment
from app.models.deployment_schemas import (
    StackDeployRequest,
    StackDeployResponse,
    StackTemplateStatus,
    TemplateDeployAsyncRequest,
)
from app.services.background_executor import background_executor

logger = logging.getLogger(__name__)
router = APIRouter(tags=["stacks"])


def _extract_domain_from_url():
    """Extract domain from FRONTEND_URL or KEYCLOAK_URL."""
    frontend_url = os.environ.get("FRONTEND_URL", "")
    if frontend_url:
        from urllib.parse import urlparse
        parsed = urlparse(frontend_url)
        if parsed.hostname:
            parts = parsed.hostname.split(".")
            if len(parts) > 2:
                return ".".join(parts[-2:])
            return parsed.hostname

    keycloak_url = os.environ.get("KEYCLOAK_URL", "")
    if keycloak_url:
        from urllib.parse import urlparse
        parsed = urlparse(keycloak_url)
        if parsed.hostname:
            parts = parsed.hostname.split(".")
            if len(parts) > 2:
                return ".".join(parts[-2:])
            return parsed.hostname

    raise RuntimeError(
        "Cannot determine domain_name from FRONTEND_URL or KEYCLOAK_URL"
    )


async def _fetch_thinkube_yaml(org: str, repo: str) -> Optional[dict]:
    """Fetch and parse thinkube.yaml from a GitHub repository."""
    urls = [
        f"https://raw.githubusercontent.com/{org}/{repo}/main/thinkube.yaml",
        f"https://raw.githubusercontent.com/{org}/{repo}/master/thinkube.yaml",
    ]

    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        content = await response.text()
                        return yaml.safe_load(content)
            except Exception as e:
                logger.debug(f"Failed to fetch {url}: {e}")

    return None


def _build_dependency_graph(
    templates: List[dict],
    thinkube_configs: Dict[str, dict],
) -> Dict[str, List[str]]:
    """Build a dependency graph from template thinkube.yaml configs.

    Returns a dict mapping template name -> list of template names it depends on.
    Dependencies are resolved by matching dependency types against template names
    within the stack.
    """
    # Map template types to their names within the stack
    # A template's "type" is its name in the stack
    type_to_name = {t["name"]: t["name"] for t in templates}

    graph = defaultdict(list)

    for template in templates:
        name = template["name"]
        config = thinkube_configs.get(name, {})
        dependencies = config.get("spec", {}).get("dependencies", [])

        for dep in dependencies:
            dep_type = dep.get("type", "")
            # Look for a matching template in the stack
            for t in templates:
                if dep_type in t["name"] or t["name"] in dep_type:
                    if t["name"] != name:  # Don't self-depend
                        graph[name].append(t["name"])
                        break

    return dict(graph)


def _topological_sort(
    templates: List[str],
    graph: Dict[str, List[str]],
) -> List[str]:
    """Topological sort of templates based on dependency graph.

    Returns templates in deployment order (dependencies first).
    Raises ValueError if there's a cycle.
    """
    in_degree = defaultdict(int)
    for name in templates:
        if name not in in_degree:
            in_degree[name] = 0

    for name, deps in graph.items():
        for dep in deps:
            in_degree[name] += 1

    # Start with templates that have no dependencies
    queue = deque([name for name in templates if in_degree[name] == 0])
    result = []

    while queue:
        current = queue.popleft()
        result.append(current)

        # Find templates that depend on current
        for name, deps in graph.items():
            if current in deps:
                in_degree[name] -= 1
                if in_degree[name] == 0:
                    queue.append(name)

    if len(result) != len(templates):
        missing = set(templates) - set(result)
        raise ValueError(
            f"Circular dependency detected involving: {', '.join(missing)}"
        )

    return result


@router.post("/deploy", response_model=StackDeployResponse, operation_id="deploy_stack")
async def deploy_stack(
    request: StackDeployRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """
    Deploy a ThinkubeStack manifest.

    Parses the stack manifest, resolves dependencies between templates,
    and deploys them in topological order. Each template is deployed
    sequentially, waiting for health checks before deploying dependents.
    """
    try:
        # Parse the stack manifest
        stack_manifest = yaml.safe_load(request.stack_yaml)

        if stack_manifest.get("kind") != "ThinkubeStack":
            raise HTTPException(
                status_code=400,
                detail="Invalid manifest: kind must be ThinkubeStack",
            )

        metadata = stack_manifest.get("metadata", {})
        stack_name = metadata.get("name", "unnamed-stack")
        stack_id = str(uuid4())
        templates = stack_manifest.get("templates", [])

        if not templates:
            raise HTTPException(
                status_code=400,
                detail="Stack manifest has no templates",
            )

        # Fetch thinkube.yaml for each template to extract dependencies
        thinkube_configs = {}
        for template in templates:
            repo = template["repo"]
            parts = repo.split("/")
            if len(parts) != 2:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid repo format '{repo}': expected 'org/repo'",
                )

            config = await _fetch_thinkube_yaml(parts[0], parts[1])
            if config:
                thinkube_configs[template["name"]] = config
            else:
                logger.warning(
                    f"Could not fetch thinkube.yaml for {repo}, "
                    f"assuming no dependencies"
                )

        # Build dependency graph and compute deployment order
        template_names = [t["name"] for t in templates]
        dep_graph = _build_dependency_graph(templates, thinkube_configs)
        deploy_order = _topological_sort(template_names, dep_graph)

        logger.info(
            f"Stack '{stack_name}' deployment order: {' -> '.join(deploy_order)}"
        )

        # Create template status entries
        template_statuses = [
            StackTemplateStatus(
                name=t["name"],
                repo=t["repo"],
                status="pending",
            )
            for t in templates
        ]

        # Start sequential deployment in background
        domain_name = _extract_domain_from_url()
        username = current_user.get("preferred_username", "thinkube-user")
        email = current_user.get("email") or f"{username}@{domain_name}"

        background_tasks.add_task(
            _deploy_stack_sequential,
            stack_id=stack_id,
            stack_name=stack_name,
            templates=templates,
            deploy_order=deploy_order,
            domain_name=domain_name,
            username=username,
            email=email,
            db_session_factory=get_db,
        )

        return StackDeployResponse(
            stack_id=stack_id,
            stack_name=stack_name,
            status="deploying",
            message=f"Stack deployment started. Deploying {len(templates)} templates in dependency order.",
            templates=template_statuses,
            deploy_order=deploy_order,
        )

    except HTTPException:
        raise
    except yaml.YAMLError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid YAML in stack manifest: {e}",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to deploy stack: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to deploy stack: {str(e)}",
        )


async def _deploy_stack_sequential(
    stack_id: str,
    stack_name: str,
    templates: List[dict],
    deploy_order: List[str],
    domain_name: str,
    username: str,
    email: str,
    db_session_factory,
):
    """Deploy stack templates sequentially in dependency order.

    Each template is deployed and we wait for it to complete before
    deploying the next one.
    """
    template_map = {t["name"]: t for t in templates}

    for template_name in deploy_order:
        template = template_map[template_name]
        repo = template["repo"]
        parts = repo.split("/")
        org, repo_name = parts[0], parts[1]

        logger.info(
            f"[Stack {stack_name}] Deploying template: {template_name} "
            f"from {repo}"
        )

        try:
            # Build GitHub URL
            github_url = f"https://github.com/{org}/{repo_name}"

            # Prepare deployment variables
            deployment_vars = {
                "template_url": github_url,
                "app_name": template_name,
                "deployment_namespace": template_name,
                "domain_name": domain_name,
                "admin_username": "tkadmin",
                "github_token": os.environ.get("GITHUB_TOKEN", ""),
                "project_name": template_name,
                "project_description": f"Stack component: {template_name}",
                "author_name": username,
                "author_email": email,
                "overwrite_existing": False,
            }

            # Apply param overrides from stack manifest
            if template.get("params"):
                deployment_vars.update(template["params"])

            # Apply env overrides from stack manifest
            # These will be handled by the deployment script
            if template.get("env"):
                deployment_vars["stack_env_overrides"] = template["env"]

            # Create deployment record
            db = next(db_session_factory())
            try:
                deployment = TemplateDeployment(
                    id=uuid4(),
                    name=template_name,
                    template_url=github_url,
                    status="pending",
                    variables=deployment_vars,
                    created_by=username,
                )
                db.add(deployment)
                db.commit()
                deployment_id = str(deployment.id)
            finally:
                db.close()

            # Start deployment and wait for completion
            await background_executor.start_deployment(deployment_id)

            logger.info(
                f"[Stack {stack_name}] Template '{template_name}' deployment "
                f"completed (id: {deployment_id})"
            )

        except Exception as e:
            logger.error(
                f"[Stack {stack_name}] Template '{template_name}' failed: {e}",
                exc_info=True,
            )
            # Stop stack deployment on first failure
            logger.error(
                f"[Stack {stack_name}] Stopping stack deployment due to failure "
                f"in '{template_name}'"
            )
            return

    logger.info(
        f"[Stack {stack_name}] All {len(templates)} templates deployed successfully"
    )
