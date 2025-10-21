"""API endpoints for custom Docker image management"""

import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid import UUID, uuid4
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Body
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.security import get_current_active_user
from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.custom_images import CustomImageBuild

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-images", tags=["custom-images"])


# Base Image Registry with Templates - Updated for Ubuntu 24.04 / Python 3.12
BASE_IMAGE_REGISTRY = {
    "jupyter/base-notebook": {
        "name": "jupyter/base-notebook",
        "is_base": True,
        "scope": "jupyter",
        "description": "Minimal Jupyter notebook with Python 3.12",
        "template": """FROM library/jupyter-base-notebook:latest

USER root

# Install additional Python packages with pinned versions
RUN pip install --no-cache-dir \\
    pandas==2.2.3 \\
    numpy==1.26.4 \\
    matplotlib==3.9.2 \\
    seaborn==0.13.2 \\
    scikit-learn==1.5.2 \\
    jupyterlab-lsp==5.1.0 \\
    python-lsp-server[all]==1.12.0

# Install useful JupyterLab extensions with pinned versions
RUN pip install --no-cache-dir \\
    jupyterlab-git==0.50.1 \\
    jupyterlab-execute-time==3.2.0 \\
    jupyterlab-code-formatter==3.0.2 \\
    black==24.10.0 \\
    isort==5.13.2

# Create work directory as root
USER root
RUN mkdir -p /home/jovyan/work && chown $NB_UID:$NB_GID /home/jovyan/work

# Switch back to jovyan user
USER $NB_UID

# Note: Add notebooks to context/notebooks/ directory to include them in the image
"""
    },
    "jupyter/pytorch-notebook": {
        "name": "jupyter/pytorch-notebook",
        "is_base": True,
        "scope": "jupyter",
        "description": "Jupyter with PyTorch and ML libraries",
        "template": """FROM library/jupyter-pytorch-notebook:latest

USER root

# Install ML/AI packages with pinned versions
RUN pip install --no-cache-dir \\
    torch==2.5.1 \\
    torchvision==0.20.1 \\
    torchaudio==2.5.1 \\
    transformers==4.46.3 \\
    datasets==3.1.0 \\
    accelerate==1.1.1 \\
    tokenizers==0.20.3 \\
    sentencepiece==0.2.0 \\
    langchain==0.3.7 \\
    openai==1.54.5 \\
    chromadb==0.5.20 \\
    faiss-cpu==1.9.0

USER $NB_UID

# Create work directory
RUN mkdir -p /home/jovyan/work
"""
    },
    "python:3.12-slim": {
        "name": "python:3.12-slim",
        "is_base": True,
        "scope": "webapp",
        "description": "Python 3.12 for web applications",
        "template": """FROM library/python:3.12-slim

WORKDIR /app

# Install common Python web dependencies with pinned versions
RUN pip install --no-cache-dir \\
    fastapi==0.115.5 \\
    uvicorn[standard]==0.32.1 \\
    sqlalchemy==2.0.36 \\
    alembic==1.14.0 \\
    pydantic==2.10.3 \\
    python-multipart==0.0.17 \\
    httpx==0.28.0 \\
    redis==5.2.0 \\
    celery==5.4.0

# Install Python dependencies from requirements.txt if provided
COPY requirements.txt /app/ 2>/dev/null || true
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

# Copy application code
COPY context/ /app/

# FastAPI default
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""
    },
    "ubuntu:24.04": {
        "name": "ubuntu:24.04",
        "is_base": True,
        "scope": "system",
        "description": "Ubuntu 24.04 LTS base system",
        "template": """FROM library/ubuntu:24.04

# Avoid prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Update and install basic packages
RUN apt-get update && apt-get install -y \\
    python3 \\
    python3-pip \\
    python3-venv \\
    curl \\
    wget \\
    git \\
    vim \\
    build-essential \\
    software-properties-common \\
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.12 as default python3
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 1

WORKDIR /app

# Copy application files
COPY context/ /app/ 2>/dev/null || true

CMD ["/bin/bash"]
"""
    }
}


# Pydantic models
class CreateImageRequest(BaseModel):
    """Request to create a new custom image"""
    name: str
    dockerfile_content: Optional[str] = None  # Custom Dockerfile content
    build_config: Optional[Dict[str, Any]] = {}  # Build configuration including base_image, description
    parent_image_id: Optional[str] = None  # Parent custom image to extend
    is_base: bool = False  # Mark as base image
    scope: str = "general"  # Image scope/category
    copy_parent_dockerfile: bool = True  # Copy parent's Dockerfile vs just FROM


class BuildImageRequest(BaseModel):
    """Request to build a custom image"""
    build_args: Optional[Dict[str, str]] = {}
    force: bool = False  # Force rebuild even if recently built


class CustomImageResponse(BaseModel):
    """Response for custom image details"""
    id: str
    name: str
    dockerfile_path: str
    status: str
    build_config: Optional[Dict[str, Any]]
    output: Optional[str]
    registry_url: Optional[str]
    is_base: bool = False
    scope: str = "general"
    parent_image_id: Optional[str] = None
    template: Optional[str] = None
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    created_by: str
    duration: Optional[float]


class BuildResponse(BaseModel):
    """Response for build initiation"""
    build_id: str
    status: str
    message: str
    websocket_url: str


# API Endpoints
@router.post("", response_model=CustomImageResponse, operation_id="create_custom_image")
async def create_custom_image(
    request: CreateImageRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Create a new custom Docker image"""
    try:
        # Check if image name already exists
        existing = db.query(CustomImageBuild).filter_by(name=request.name).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Image '{request.name}' already exists")

        # Create directory structure in shared-code mount
        # shared-code is mounted at /home in the container
        dockerfiles_dir = Path("/home/dockerfiles/custom")
        dockerfiles_dir.mkdir(parents=True, exist_ok=True)

        image_dir = dockerfiles_dir / request.name
        if image_dir.exists():
            raise HTTPException(status_code=400, detail=f"Directory for '{request.name}' already exists")

        try:
            image_dir.mkdir(parents=True)
            # Set permissions so code-server can edit files (775 = rwxrwxr-x)
            image_dir.chmod(0o775)
        except Exception as e:
            logger.error(f"Failed to create directory {image_dir}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create image directory: {str(e)}")

        # Create Dockerfile based on parent or base image
        dockerfile_path = image_dir / "Dockerfile"
        parent_id = None

        if request.dockerfile_content:
            # Use provided content
            dockerfile_content = request.dockerfile_content
        elif request.parent_image_id:
            # Extending from custom image
            parent = db.query(CustomImageBuild).filter_by(id=request.parent_image_id).first()
            if not parent:
                raise HTTPException(status_code=404, detail="Parent image not found")

            parent_id = parent.id

            if request.copy_parent_dockerfile:
                # Copy parent's Dockerfile
                parent_dockerfile_path = Path(parent.dockerfile_path)
                if parent_dockerfile_path.exists():
                    dockerfile_content = parent_dockerfile_path.read_text()
                else:
                    # Fallback if parent Dockerfile missing
                    dockerfile_content = f"FROM {parent.registry_url or parent.name}\n\n# Extended from {parent.name}"
            else:
                # Just use FROM parent
                dockerfile_content = f"FROM {parent.registry_url or parent.name}\n\n# Extended from {parent.name}"
        else:
            # Use base image template
            base_image = request.build_config.get("base_image", "ubuntu:24.04")
            dockerfile_content = None

            # 1. Check if it's a known base image with template in BASE_IMAGE_REGISTRY
            if base_image in BASE_IMAGE_REGISTRY:
                dockerfile_content = BASE_IMAGE_REGISTRY[base_image]["template"]
                # Also inherit scope if not specified
                if request.scope == "general":
                    request.scope = BASE_IMAGE_REGISTRY[base_image]["scope"]

            # 2. Check custom images for templates (if base_image is an ID)
            if not dockerfile_content:
                try:
                    base_uuid = UUID(base_image)
                    custom_base = db.query(CustomImageBuild).filter_by(id=base_uuid).first()
                    if custom_base and custom_base.is_base and custom_base.template:
                        dockerfile_content = custom_base.template
                        # Use the custom image's registry URL as FROM
                        base_image = custom_base.registry_url or f"library/{custom_base.name}"
                except (ValueError, AttributeError):
                    # Not a UUID, continue
                    pass

            # 3. Check mirrored images for templates (if base_image is an ID or registry URL)
            if not dockerfile_content:
                from app.models.container_images import ContainerImage

                # Try by UUID first
                try:
                    base_uuid = UUID(base_image)
                    mirrored_base = db.query(ContainerImage).filter_by(id=base_uuid).first()
                    if mirrored_base and mirrored_base.is_base and mirrored_base.template:
                        dockerfile_content = mirrored_base.template
                        base_image = mirrored_base.destination_url
                except (ValueError, AttributeError):
                    # Not a UUID, try by destination URL or name
                    mirrored_base = db.query(ContainerImage).filter(
                        (ContainerImage.destination_url == base_image) |
                        (ContainerImage.name == base_image)
                    ).first()
                    if mirrored_base and mirrored_base.is_base and mirrored_base.template:
                        dockerfile_content = mirrored_base.template
                        base_image = mirrored_base.destination_url

            # 4. If still no template, generate generic one
            if not dockerfile_content:
                description = request.build_config.get("description", "Custom Docker image")
                dockerfile_content = f"""FROM {base_image}

# Image: {request.name}
# Description: {description}

WORKDIR /app
"""
        try:
            dockerfile_path.write_text(dockerfile_content)
            # Set permissions so code-server can edit the file (664 = rw-rw-r--)
            dockerfile_path.chmod(0o664)
        except Exception as e:
            logger.error(f"Failed to create Dockerfile at {dockerfile_path}: {e}")
            # Clean up directory if file creation fails
            import shutil
            shutil.rmtree(image_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"Failed to create Dockerfile: {str(e)}")

        # Create a useful README explaining the structure
        readme_path = image_dir / "README.md"
        description = request.build_config.get("description", "Custom Docker image")
        readme_content = f"""# {request.name}

{description}

## Directory Structure

- `Dockerfile` - The Docker image definition
- `context/` - Place files here that need to be copied into the image

## Building

This image will be built automatically when you click the Build button in Thinkube Control.

## Adding Files

To include files in your image:
1. Place them in the `context/` directory
2. Add COPY instructions in the Dockerfile:
   ```dockerfile
   COPY context/myapp.py /app/myapp.py
   COPY context/config.yaml /app/config.yaml
   ```

## Base Image

Based on: {request.build_config.get("base_image", "ubuntu:22.04")}
"""
        try:
            readme_path.write_text(readme_content)
            readme_path.chmod(0o664)
        except Exception as e:
            logger.error(f"Failed to create README at {readme_path}: {e}")
            # Continue anyway - README is not critical

        # Create context directory for build files
        context_dir = image_dir / "context"
        try:
            context_dir.mkdir(exist_ok=True)
            context_dir.chmod(0o775)
            # Add a .gitkeep to preserve the directory
            gitkeep_path = context_dir / ".gitkeep"
            gitkeep_path.touch()
            gitkeep_path.chmod(0o664)
        except Exception as e:
            logger.error(f"Failed to create context directory: {e}")
            # Non-critical, continue

        # Verify files were actually created
        if not dockerfile_path.exists():
            logger.error(f"Dockerfile was not created at {dockerfile_path}")
            import shutil
            shutil.rmtree(image_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail="Failed to verify Dockerfile creation")

        # Create database record
        # Note: template is set to None initially, will be updated after successful build
        build = CustomImageBuild(
            id=uuid4(),
            name=request.name,
            dockerfile_path=str(dockerfile_path),
            status="not_built",
            build_config=request.build_config,
            is_base=request.is_base,
            scope=request.scope,
            parent_image_id=parent_id,
            template=None,  # Will be set after build completes if is_base=True
            created_by=current_user.get("preferred_username", "unknown")
        )
        db.add(build)
        db.commit()

        return CustomImageResponse(**build.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create custom image: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create image: {str(e)}")


@router.get("/base-registry", operation_id="get_base_registry")
def get_base_registry(
    type_filter: Optional[str] = Query(None, description="Filter by type (jupyter/standard)"),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get all available base images from all sources"""
    registry = []
    db = next(get_db())

    try:
        # 1. Add hardcoded base images from registry
        for key, image in BASE_IMAGE_REGISTRY.items():
            # Convert scope to type (jupyter images have scope="jupyter")
            image_type = "jupyter" if image.get("scope") == "jupyter" else "standard"
            if type_filter is None or image_type == type_filter:
                registry.append({
                    "id": key,
                    "name": image["name"],
                    "display_name": image.get("description", image["name"]),
                    "registry_url": f"registry.thinkube.com/library/{image['name']}",
                    "is_base": True,
                    "type": image_type,
                    "source": "predefined",
                    "template": image.get("template", ""),
                    "group": "Base Templates"
                })

        # 2. Add custom-built images marked as base
        from app.models.custom_images import CustomImageBuild
        custom_bases = db.query(CustomImageBuild).filter(
            CustomImageBuild.is_base == True,
            CustomImageBuild.status == "success"
        ).all()

        for custom in custom_bases:
            # Convert scope to type
            image_type = "jupyter" if custom.scope == "jupyter" else "standard"
            if type_filter is None or image_type == type_filter:
                registry.append({
                    "id": str(custom.id),
                    "name": custom.name,
                    "display_name": f"Custom: {custom.name}",
                    "registry_url": custom.registry_url or f"registry.thinkube.com/library/{custom.name}",
                    "is_base": True,
                    "type": image_type,
                    "source": "built",
                    "template": custom.template,
                    "group": "Custom Built"
                })

        # 3. Add mirrored images marked as base
        from app.models.container_images import ContainerImage
        mirrored_bases = db.query(ContainerImage).filter(
            ContainerImage.is_base == True
        ).all()

        for mirrored in mirrored_bases:
            # Check metadata for jupyter purpose
            metadata = mirrored.image_metadata or {}
            image_type = "jupyter" if metadata.get("purpose") == "jupyter" else "standard"
            if type_filter is None or image_type == type_filter:
                registry.append({
                    "id": str(mirrored.id),
                    "name": mirrored.name,
                    "display_name": f"Mirrored: {mirrored.name}",
                    "registry_url": mirrored.destination_url,
                    "is_base": True,
                    "type": image_type,
                    "source": "mirrored",
                    "template": mirrored.template,  # Now mirrored images can have templates
                    "group": "Mirrored Images"
                })

    finally:
        db.close()

    return {
        "images": registry,
        "types": ["jupyter", "standard"]  # Simplified from 6 scopes to 2 types
    }


@router.get("/{image_id}/dockerfile", operation_id="get_image_dockerfile")
def get_image_dockerfile(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get Dockerfile content of an existing custom image"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()
    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    # Read Dockerfile
    dockerfile_path = Path(build.dockerfile_path)
    if not dockerfile_path.exists():
        raise HTTPException(status_code=404, detail="Dockerfile not found")

    return {
        "dockerfile": dockerfile_path.read_text(),
        "image_name": build.name,
        "scope": build.scope,
        "is_base": build.is_base
    }


@router.get("", operation_id="list_custom_images")
def list_custom_images(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """List all custom Docker images"""
    query = db.query(CustomImageBuild)


    # Get total count
    total = query.count()

    # Apply pagination
    images = query.order_by(
        CustomImageBuild.created_at.desc()
    ).offset(skip).limit(limit).all()

    return {
        "builds": [CustomImageResponse(**image.to_dict()) for image in images],
        "total": total
    }


@router.get("/{image_id}", response_model=CustomImageResponse, operation_id="get_custom_image")
def get_custom_image(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get details of a specific custom image"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()

    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    return CustomImageResponse(**build.to_dict())


@router.post("/{image_id}/build", response_model=BuildResponse, operation_id="build_custom_image")
async def build_custom_image(
    image_id: UUID,
    request: BuildImageRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Start building a custom Docker image"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()

    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    # Check if already building
    if build.status == "building":
        raise HTTPException(status_code=400, detail="Image is already being built")

    # Update build config with new args
    if request.build_args:
        if not build.build_config:
            build.build_config = {}
        build.build_config["build_args"] = request.build_args

    # Reset status for new build
    build.status = "pending"
    build.output = None
    build.started_at = None
    build.completed_at = None
    db.commit()

    # Don't start build here - WebSocket will handle execution (like templates)
    # background_tasks.add_task(dockerfile_executor.start_build, str(build.id))

    return BuildResponse(
        build_id=str(build.id),
        status="pending",
        message="Build queued for execution",
        websocket_url=f"/ws/custom-images/build/{build.id}"
    )


@router.get("/{image_id}/dockerfile", operation_id="get_dockerfile")
async def get_dockerfile(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get the Dockerfile content for a custom image"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()

    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    # Read Dockerfile content
    dockerfile_path = Path(build.dockerfile_path)
    if not dockerfile_path.exists():
        raise HTTPException(status_code=404, detail="Dockerfile not found")

    content = dockerfile_path.read_text()
    return {"content": content, "path": str(dockerfile_path)}


@router.put("/{image_id}/dockerfile", operation_id="update_dockerfile")
async def update_dockerfile(
    image_id: UUID,
    body: Dict[str, str] = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Update the Dockerfile content for a custom image"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()

    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    # Write new content
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    dockerfile_path = Path(build.dockerfile_path)
    dockerfile_path.write_text(content)

    # Reset build status since Dockerfile changed
    build.status = "not_built"
    build.output = None
    db.commit()

    return {"message": "Dockerfile updated successfully"}


@router.get("/{image_id}/logs", operation_id="get_build_logs")
async def get_build_logs(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get build log files for a custom image"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()

    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    # Find log files
    log_dir = Path(f"/tmp/thinkube-dockerfiles/{build.name}")
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


@router.get("/{image_id}/logs/{filename}", operation_id="download_build_log")
async def download_build_log(
    image_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Download a specific build log file"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()

    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    # Validate filename (prevent directory traversal)
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Updated to use the correct log directory - EXACTLY like templates
    log_file = Path(f"/tmp/thinkube-builds/{build.name}/{filename}")
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(log_file, media_type="text/plain", filename=filename)


@router.get("/{image_id}/editor-url", operation_id="get_editor_url")
async def get_editor_url(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get the code-server URL to edit this image's Dockerfile"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()

    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    # Generate code-server URL to open folder and file
    # Using VS Code's payload parameter to open the Dockerfile directly
    # Reference: https://github.com/coder/code-server/issues/1964#issuecomment-916590294
    domain = os.environ.get("DOMAIN_NAME", "thinkube.com")
    folder_path = f"/home/coder/dockerfiles/custom/{build.name}"
    file_path = f"{folder_path}/Dockerfile"

    # VS Code uses vscode-remote:// URI scheme for files
    payload = f'[["openFile","vscode-remote://{file_path}"]]'
    editor_url = f"https://code.{domain}/?folder={folder_path}&payload={payload}"

    return {
        "editor_url": editor_url,
        "folder_path": folder_path,
        "message": "Open this URL in a new tab to edit the Dockerfile. Authentication is handled by SSO."
    }


@router.patch("/{image_id}/toggle-base", operation_id="toggle_custom_image_base_status")
def toggle_custom_base_status(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Toggle the is_base status of a custom image"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()
    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    # Toggle the is_base status
    build.is_base = not build.is_base
    db.commit()
    db.refresh(build)

    return {
        "message": f"Image {'marked as' if build.is_base else 'unmarked as'} base",
        "id": str(build.id),
        "is_base": build.is_base
    }


@router.put("/{image_id}/template", operation_id="update_custom_image_template")
def update_custom_template(
    image_id: UUID,
    template_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Update the Dockerfile template for a custom base image"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()
    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    if not build.is_base:
        raise HTTPException(
            status_code=400,
            detail="Image must be marked as base to have a template"
        )

    # Update template
    build.template = template_data.get("template", "")
    db.commit()
    db.refresh(build)

    return {
        "message": "Template updated successfully",
        "id": str(build.id),
        "template": build.template
    }

@router.delete("/{image_id}", operation_id="delete_custom_image")
async def delete_custom_image(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Delete a custom Docker image"""
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()

    if not build:
        raise HTTPException(status_code=404, detail="Image not found")


    # Check if currently building
    if build.status == "building":
        raise HTTPException(status_code=400, detail="Cannot delete image while building")

    # Delete directory from shared-code
    image_dir = Path(build.dockerfile_path).parent
    if image_dir.exists():
        import shutil
        shutil.rmtree(image_dir)

    # Delete log directory
    log_dir = Path(f"/tmp/thinkube-dockerfiles/{build.name}")
    if log_dir.exists():
        import shutil
        shutil.rmtree(log_dir)

    # Delete database record
    db.delete(build)
    db.commit()

    return {"message": f"Image '{build.name}' deleted successfully"}


def get_dockerfile_template(template: str) -> str:
    """Get Dockerfile template content based on template type"""
    templates = {
        "scratch": """# Minimal base image
FROM scratch

# Add your application
# COPY app /app

# Set entrypoint
# ENTRYPOINT ["/app"]
""",
        "python": """# Python base image
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Run application
CMD ["python", "app.py"]
""",
        "node": """# Node.js base image
FROM node:20-slim

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci --only=production

# Copy application
COPY . .

# Run application
CMD ["node", "index.js"]
""",
        "go": """# Build stage
FROM golang:1.21 AS builder

WORKDIR /app
COPY go.* ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 go build -o app

# Final stage
FROM alpine:latest
RUN apk --no-cache add ca-certificates

WORKDIR /root/
COPY --from=builder /app/app .

CMD ["./app"]
"""
    }
    return templates.get(template, templates["scratch"])