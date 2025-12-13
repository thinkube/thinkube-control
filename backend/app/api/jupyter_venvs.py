"""API endpoints for Jupyter virtualenv management"""

import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid import UUID, uuid4
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.jupyter_venvs import JupyterVenv

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
    websocket_url: str


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
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Start building a Jupyter venv"""
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
    venv.status = "pending"
    venv.output = None
    venv.started_at = None
    venv.completed_at = None
    db.commit()

    # WebSocket will handle the actual build
    return BuildResponse(
        build_id=str(venv.id),
        status="pending",
        message="Build queued for execution",
        websocket_url=f"/ws/jupyter-venvs/build/{venv.id}"
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
