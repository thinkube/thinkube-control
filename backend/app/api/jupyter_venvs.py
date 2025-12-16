"""API endpoints for Jupyter virtualenv management

Uses Kubernetes Jobs via Ansible playbook for venv builds on GPU nodes.
Venvs are stored on local hostPath (/var/lib/jupyterhub-venvs) for fast I/O.
After build, venvs are synced to other GPU nodes via rsync.
"""

import os
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from uuid import UUID, uuid4
from pathlib import Path
import tempfile
import yaml

from fastapi import APIRouter, Depends, HTTPException, Query, Body, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db, SessionLocal
from app.models.jupyter_venvs import JupyterVenv
from app.services.ansible_environment import ansible_env

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jupyter-venvs", tags=["jupyter-venvs"])


# Package lists for templates (from build-venvs.sh)
BASE_PACKAGES = [
    "ipykernel",
    "transformers==4.56.2",
    "datasets==4.1.1",
    "accelerate==1.10.1",
    "nvidia-modelopt",
    "pandas==2.3.2",
    "scikit-learn==1.7.2",
    "matplotlib==3.10.6",
    "seaborn==0.13.2",
    "plotly==6.3.0",
    "psycopg2-binary==2.9.10",
    "redis==6.4.0",
    "qdrant-client==1.15.1",
    "opensearch-py==3.0.0",
    "mlflow==3.4.0",
    "boto3==1.40.40",
    "clickhouse-connect",
    "chromadb",
    "nats-py",
    "weaviate-client==4.17.0",
    "litellm==1.74.9",
    "kubernetes",
    "PyGithub",
    "hera-workflows",
    "argilla",
    "cvat-sdk",
    "langfuse",
    "openai",
    "arxiv",
    "python-dotenv==1.1.1",
    "requests==2.32.5",
    "httpx==0.28.1",
    "pydantic==2.11.9",
    "sqlalchemy",
    "alembic",
    "ipywidgets",
    "jupyterlab-widgets",
    "tqdm",
    "Pillow",
    "opencv-python",
    "sentence-transformers",
    "spacy",
    "grpcio",
    "grpcio-tools",
    "gql",
    "websockets",
    "claude-agent-sdk",
    "openai-harmony",
]

FINETUNING_PACKAGES = [
    "bitsandbytes>=0.48.2",
    "peft>=0.17.1",
    "trl==0.23.0",
    "tyro",
    "hf_transfer",
    "sentencepiece",
    "protobuf",
    "openpyxl",
]

AGENT_PACKAGES = [
    "langchain==1.1.3",
    "langchain-core==1.1.3",
    "langchain-community==0.4.1",
    "langchain-openai==1.1.1",
    "ag2[openai]==0.10.2",
    "langgraph==0.4.1",
    "openai-agents==0.6.2",
    "crewai==1.7.0",
    "crewai-tools==1.7.0",
    "faiss-cpu==1.12.0",
    "opentelemetry-sdk==1.39.0",
    "opentelemetry-exporter-otlp==1.39.0",
    "opentelemetry-api==1.39.0",
    "tiktoken",
]

# Venv templates (built-in)
VENV_TEMPLATES = {
    "fine-tuning": {
        "name": "fine-tuning",
        "description": "Fine-tuning venv with bitsandbytes, peft, trl, and Unsloth",
        "packages": BASE_PACKAGES + FINETUNING_PACKAGES,
        "special_installs": [
            "git+https://github.com/unslothai/unsloth-zoo.git --no-deps",
            "unsloth[cu130onlytorch291] @ git+https://github.com/unslothai/unsloth.git --no-build-isolation --no-deps",
        ],
    },
    "agent-dev": {
        "name": "agent-dev",
        "description": "Agent development venv with LangChain, CrewAI, AG2, and more",
        "packages": BASE_PACKAGES + AGENT_PACKAGES,
        "special_installs": [
            "openlit --no-deps",
        ],
    },
}


# Pydantic models
class CreateVenvRequest(BaseModel):
    """Request to create a new venv"""
    name: str
    packages: Optional[List[str]] = None  # Additional packages beyond template
    parent_template: Optional[str] = None  # "fine-tuning" or "agent-dev"


class VenvResponse(BaseModel):
    """Response for venv details"""
    id: str
    name: str
    packages: List[str]
    status: str
    output: Optional[str]
    is_template: bool
    parent_template_id: Optional[str]
    venv_path: Optional[str]
    architecture: Optional[str]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    created_by: str
    duration: Optional[float]


class BuildVenvRequest(BaseModel):
    """Request to build a venv"""
    force: bool = False  # Force rebuild even if exists


class BuildResponse(BaseModel):
    """Response for build initiation"""
    build_id: str
    status: str
    message: str
    poll_url: str
    warning: Optional[str] = None


# API Endpoints
@router.get("/templates", operation_id="get_venv_templates")
def get_venv_templates(
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get available venv templates"""
    templates = []
    for key, template in VENV_TEMPLATES.items():
        templates.append({
            "id": key,
            "name": template["name"],
            "description": template["description"],
            "package_count": len(template["packages"]),
            "has_special_installs": len(template.get("special_installs", [])) > 0,
        })
    return {"templates": templates}


@router.get("/templates/{template_id}", operation_id="get_venv_template_details")
def get_venv_template_details(
    template_id: str,
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get details of a specific venv template"""
    if template_id not in VENV_TEMPLATES:
        raise HTTPException(status_code=404, detail="Template not found")

    template = VENV_TEMPLATES[template_id]
    return {
        "id": template_id,
        "name": template["name"],
        "description": template["description"],
        "packages": template["packages"],
        "special_installs": template.get("special_installs", []),
    }


@router.post("", response_model=VenvResponse, operation_id="create_jupyter_venv")
async def create_jupyter_venv(
    request: CreateVenvRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Create a new Jupyter virtualenv"""
    try:
        # Check if venv name already exists
        existing = db.query(JupyterVenv).filter_by(name=request.name).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Venv '{request.name}' already exists")

        # Validate name (alphanumeric, hyphens, underscores only)
        import re
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*$', request.name):
            raise HTTPException(
                status_code=400,
                detail="Venv name must start with a letter and contain only letters, numbers, hyphens, and underscores"
            )

        # Build package list
        packages = []
        parent_template_id = None

        if request.parent_template:
            if request.parent_template not in VENV_TEMPLATES:
                raise HTTPException(status_code=400, detail=f"Unknown template: {request.parent_template}")

            # Get template packages
            template = VENV_TEMPLATES[request.parent_template]
            packages = template["packages"].copy()

            # Find or create template record
            template_record = db.query(JupyterVenv).filter_by(
                name=request.parent_template,
                is_template=True
            ).first()
            if template_record:
                parent_template_id = template_record.id

        # Add additional packages
        if request.packages:
            packages.extend(request.packages)

        if not packages:
            raise HTTPException(status_code=400, detail="No packages specified. Use a template or provide packages.")

        # Create database record
        venv = JupyterVenv(
            id=uuid4(),
            name=request.name,
            packages=packages,
            status="pending",
            is_template=False,
            parent_template_id=parent_template_id,
            created_by=current_user.get("preferred_username", "unknown")
        )
        db.add(venv)
        db.commit()

        return VenvResponse(**venv.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create venv: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create venv: {str(e)}")


@router.get("", operation_id="list_jupyter_venvs")
def list_jupyter_venvs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    include_templates: bool = Query(False, description="Include template venvs"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """List all Jupyter virtualenvs"""
    query = db.query(JupyterVenv)

    if not include_templates:
        query = query.filter(JupyterVenv.is_template == False)

    # Get total count
    total = query.count()

    # Apply pagination
    venvs = query.order_by(
        JupyterVenv.created_at.desc()
    ).offset(skip).limit(limit).all()

    return {
        "venvs": [VenvResponse(**venv.to_dict()) for venv in venvs],
        "total": total
    }


@router.get("/{venv_id}", response_model=VenvResponse, operation_id="get_jupyter_venv")
def get_jupyter_venv(
    venv_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get details of a specific venv"""
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    return VenvResponse(**venv.to_dict())


@router.post("/{venv_id}/build", response_model=BuildResponse, operation_id="build_jupyter_venv")
async def build_jupyter_venv(
    venv_id: UUID,
    request: BuildVenvRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Start building a Jupyter venv using Kubernetes Job on GPU node.

    The build runs as a K8s Job on a GPU node with hostPath volume mount.
    This provides fast I/O (local NVMe/SSD) instead of network storage.

    After successful build, the venv is synced to other GPU nodes via rsync.

    WARNING: Build may take 5-15 minutes. Poll the status endpoint for updates.
    There is no streaming output during pip install.
    """
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    # Check if already building
    if venv.status == "building":
        raise HTTPException(status_code=400, detail="Venv is already being built")

    # Check if already built and not forcing
    if venv.status == "success" and not request.force:
        raise HTTPException(status_code=400, detail="Venv already built. Use force=true to rebuild.")

    # Reset status for new build
    venv.status = "building"
    venv.output = None
    venv.started_at = datetime.now(timezone.utc)
    venv.completed_at = None
    db.commit()

    # Start build in background using Ansible playbook
    background_tasks.add_task(_execute_venv_build, str(venv.id))

    return BuildResponse(
        build_id=str(venv.id),
        status="building",
        message="Build started on GPU node",
        poll_url=f"/jupyter-venvs/{venv.id}",
        warning="Build may take 5-15 minutes. The process may appear idle during package installation. Poll the status endpoint for updates."
    )


@router.get("/{venv_id}/logs", operation_id="get_venv_build_logs")
async def get_venv_build_logs(
    venv_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get build log files for a venv"""
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    # Find log files
    log_dir = Path(f"/tmp/thinkube-venvs/{venv.name}")
    logs = []

    if log_dir.exists():
        for log_file in sorted(log_dir.glob("build-*.log"), reverse=True):
            stats = log_file.stat()
            logs.append({
                "filename": log_file.name,
                "path": str(log_file),
                "size": stats.st_size,
                "created": stats.st_ctime,
                "modified": stats.st_mtime
            })

    return {"logs": logs[:10]}  # Return last 10 build logs


@router.get("/{venv_id}/logs/{filename}", operation_id="download_venv_build_log")
async def download_venv_build_log(
    venv_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Download a specific build log file"""
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    # Validate filename (prevent directory traversal)
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    log_file = Path(f"/tmp/thinkube-venvs/{venv.name}/{filename}")
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(log_file, media_type="text/plain", filename=filename)


@router.delete("/{venv_id}", operation_id="delete_jupyter_venv")
async def delete_jupyter_venv(
    venv_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Delete a Jupyter virtualenv"""
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    # Don't allow deleting templates
    if venv.is_template:
        raise HTTPException(status_code=400, detail="Cannot delete template venvs")

    # Check if currently building
    if venv.status == "building":
        raise HTTPException(status_code=400, detail="Cannot delete venv while building")

    # Delete venv directory from JuiceFS if exists
    if venv.venv_path:
        venv_path = Path(venv.venv_path)
        if venv_path.exists():
            import shutil
            shutil.rmtree(venv_path, ignore_errors=True)

    # Delete log directory
    log_dir = Path(f"/tmp/thinkube-venvs/{venv.name}")
    if log_dir.exists():
        import shutil
        shutil.rmtree(log_dir, ignore_errors=True)

    # Delete database record
    db.delete(venv)
    db.commit()

    return {"message": f"Venv '{venv.name}' deleted successfully"}


@router.put("/{venv_id}/packages", operation_id="update_venv_packages")
async def update_venv_packages(
    venv_id: UUID,
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Update the package list for a venv (before building)"""
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    if venv.is_template:
        raise HTTPException(status_code=400, detail="Cannot modify template venvs")

    if venv.status == "building":
        raise HTTPException(status_code=400, detail="Cannot modify venv while building")

    packages = body.get("packages", [])
    if not packages:
        raise HTTPException(status_code=400, detail="Packages list cannot be empty")

    venv.packages = packages
    venv.status = "pending"  # Reset status since packages changed
    db.commit()

    return {"message": "Packages updated successfully", "packages": venv.packages}


# Background task for venv build
async def _execute_venv_build(venv_id: str) -> None:
    """Execute venv build using Ansible playbook.

    This runs as a background task and:
    1. Runs Ansible playbook that creates K8s Job on GPU node
    2. The Job builds venv to local hostPath
    3. After success, syncs to other GPU nodes via rsync
    4. Updates database with status
    """
    db = SessionLocal()

    try:
        venv = db.query(JupyterVenv).filter_by(id=venv_id).first()
        if not venv:
            logger.error(f"Venv {venv_id} not found")
            return

        logger.info(f"Starting venv build for {venv.name}")

        try:
            result = await _run_ansible_build(venv)

            if result["success"]:
                venv.status = "success"
                venv.output = result.get("output", "Build completed successfully")
                venv.venv_path = f"/var/lib/jupyterhub-venvs/custom/{venv.name}"
                venv.architecture = result.get("architecture", "unknown")
                logger.info(f"Venv build succeeded for {venv.name}")
            else:
                venv.status = "failed"
                venv.output = result.get("error", "Build failed")
                logger.error(f"Venv build failed for {venv.name}: {result.get('error')}")

        except Exception as e:
            logger.error(f"Venv build error for {venv.name}: {e}")
            venv.status = "failed"
            venv.output = f"Build error: {str(e)}"

        finally:
            venv.completed_at = datetime.now(timezone.utc)
            db.commit()

    finally:
        db.close()


async def _run_ansible_build(venv) -> Dict[str, Any]:
    """Run Ansible playbook to build venv via K8s Job.

    Returns:
        Dict with success, output/error, and architecture
    """
    # Playbook path
    playbook_path = Path("/home/thinkube/thinkube-control/ansible/playbooks/build_venv.yaml")

    if not playbook_path.exists():
        return {"success": False, "error": f"Playbook not found: {playbook_path}"}

    # Prepare variables
    extra_vars = {
        "venv_name": venv.name,
        "packages": json.dumps(venv.packages),  # JSON string for Ansible
    }

    # Add auth variables
    extra_vars = ansible_env.prepare_auth_vars(extra_vars)

    # Add kubeconfig path
    kubeconfig = os.environ.get("KUBECONFIG", "/home/thinkube/.kube/config")
    extra_vars["kubeconfig"] = kubeconfig

    # Add harbor registry
    domain_name = os.environ.get("DOMAIN_NAME", "cmxela.com")
    extra_vars["harbor_registry"] = f"registry.{domain_name}"

    # Create temporary vars file
    temp_vars_fd, temp_vars_path = tempfile.mkstemp(suffix=".yml", prefix="venv-vars-")

    try:
        with os.fdopen(temp_vars_fd, "w") as f:
            yaml.dump(extra_vars, f)
    except:
        os.close(temp_vars_fd)
        raise

    # Build ansible command
    inventory_path = ansible_env.get_inventory_path()
    cmd = [
        "ansible-playbook",
        "-i", str(inventory_path),
        str(playbook_path),
        "-e", f"@{temp_vars_path}",
        "-v",
    ]

    # Get environment
    env = ansible_env.get_environment(context="template")

    # Create log file
    log_dir = Path("/tmp/thinkube-venvs") / venv.name
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = log_dir / f"build-{timestamp}.log"

    try:
        logger.info(f"Running: {' '.join(cmd)}")

        with open(log_file, "w") as f:
            f.write(f"=== VENV BUILD LOG ===\n")
            f.write(f"Venv: {venv.name}\n")
            f.write(f"Started: {datetime.now()}\n")
            f.write(f"Packages: {len(venv.packages)}\n")
            f.write(f"\n=== ANSIBLE OUTPUT ===\n")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )

            # Read output
            output_lines = []
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line_text = line.decode("utf-8", errors="replace").rstrip()
                output_lines.append(line_text)
                f.write(f"{line_text}\n")
                f.flush()

            return_code = await process.wait()

            f.write(f"\n=== COMPLETED ===\n")
            f.write(f"Return code: {return_code}\n")
            f.write(f"Finished: {datetime.now()}\n")

        if return_code == 0:
            # Try to detect architecture from output
            architecture = "unknown"
            for line in output_lines:
                if "Architecture marker written:" in line:
                    architecture = line.split(":")[-1].strip()
                    break

            return {
                "success": True,
                "output": f"Build completed. Log: {log_file}",
                "architecture": architecture,
            }
        else:
            return {
                "success": False,
                "error": f"Build failed with return code {return_code}. Log: {log_file}",
            }

    except Exception as e:
        return {"success": False, "error": str(e)}

    finally:
        # Clean up temp file
        try:
            os.unlink(temp_vars_path)
        except:
            pass
