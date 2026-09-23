# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""API endpoints for custom Docker image management"""

import os
import asyncio
import logging
import platform
import shlex
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid import UUID, uuid4
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.security import get_current_active_user
from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db, SessionLocal
from app.models.custom_images import CustomImageBuild
from app.services import detached
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-images", tags=["custom-images"])

# Each build writes one log here, under the image's name.
BUILD_LOG_DIR = Path("/tmp/thinkube-builds")


# Base Image Registry with Templates - Updated for Ubuntu 24.04 / Python 3.12
BASE_IMAGE_REGISTRY = {
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
    poll_url: str


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
        dockerfiles_dir = Path("/home/thinkube/dockerfiles/custom")
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
                    "registry_url": f"registry.{settings.DOMAIN_NAME}/library/{image['name']}",
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
                    "registry_url": custom.registry_url or f"registry.{settings.DOMAIN_NAME}/library/{custom.name}",
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
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Start building a custom image and return at once with the id to poll.

    The build runs detached from this call, as an Argo Workflow with Buildah
    that pushes the image to Harbor. The image's directory is sent to the
    build as text, 900 KiB at most. Poll get_custom_image until status is
    success or failed. Its output field names the build log, which
    download_build_log returns while the build runs and after it ends.
    """
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

    build.status = "building"
    build.output = None
    build.started_at = datetime.utcnow()
    build.completed_at = None
    db.commit()

    # Detached from this request: an in-process caller (the MCP bridge) would
    # otherwise wait for the whole build before it got the answer, and the
    # panel's tab can close without ending the build.
    detached.start(f"image-build:{build.id}", _execute_custom_image_build(str(build.id)))

    return BuildResponse(
        build_id=str(build.id),
        status="building",
        message="Build started; poll get_custom_image until status is success or failed",
        poll_url=f"/custom-images/{build.id}",
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
    log_dir = BUILD_LOG_DIR / build.name
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

    log_file = BUILD_LOG_DIR / build.name / filename
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
    domain = settings.DOMAIN_NAME
    folder_path = f"/home/thinkube/dockerfiles/custom/{build.name}"
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
    log_dir = BUILD_LOG_DIR / build.name
    if log_dir.exists():
        import shutil
        shutil.rmtree(log_dir)

    # Delete database record
    db.delete(build)
    db.commit()

    return {"message": f"Image '{build.name}' deleted successfully"}


async def _execute_custom_image_build(build_id: str) -> None:
    """Build, push and record one custom image, detached from the request that started it."""
    db = SessionLocal()()
    try:
        build = db.query(CustomImageBuild).filter_by(id=build_id).first()
        if not build:
            logger.error(f"Custom image build {build_id} not found")
            return

        try:
            result = await _run_image_build(build, db)
            if result["return_code"] == 0:
                build.status = "success"
                build.registry_url = result["registry_url"]

                # A base image gets a minimal template for images that extend it.
                # registry.<domain>/library/jp-cmxela:latest -> library/jp-cmxela:latest
                if build.is_base:
                    image_ref = build.registry_url.split('/', 1)[1]
                    build.template = f"""FROM {image_ref}

# Extended from {build.name}
# Add your customizations here

"""
                logger.info(f"Custom image {build.name} built and pushed: {build.registry_url}")
            else:
                build.status = "failed"
                logger.error(f"Custom image {build.name} failed with return code {result['return_code']}")
        except Exception as e:
            logger.error(f"Custom image build error for {build.name}: {e}", exc_info=True)
            build.status = "failed"
            build.output = f"Build error: {e}"
        finally:
            build.completed_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


# The build runs as an Argo Workflow in argo, with the image builder the app
# builds use. The image's directory travels in the Workflow as raw artifacts,
# so it is sent as text and is bounded by the size of one Kubernetes object.
BUILDAH_IMAGE = "library/buildah:v1.43.4"
BUILD_NAMESPACE = "argo"
CONTEXT_LIMIT_BYTES = 900 * 1024
POLL_SECONDS = 10
FINAL_PHASES = ("Succeeded", "Failed", "Error")
ARCHITECTURES = {"x86_64": "amd64", "aarch64": "arm64"}


def _context_artifacts(context: Path) -> List[Dict[str, Any]]:
    """The files of an image's directory, as raw input artifacts of the build pod."""
    artifacts: List[Dict[str, Any]] = []
    size = 0
    for path in sorted(p for p in context.rglob("*") if p.is_file()):
        relative = path.relative_to(context)
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            raise ValueError(f"{relative} is not a text file; a custom image directory can hold text files only")
        size += len(text.encode())
        artifacts.append({"name": f"file-{len(artifacts)}", "path": f"/context/{relative}", "raw": {"data": text}})
    if size > CONTEXT_LIMIT_BYTES:
        raise ValueError(f"{context} holds {size} bytes; a custom image directory can hold {CONTEXT_LIMIT_BYTES} at most")
    return artifacts


def _build_workflow(build: CustomImageBuild, registry_url: str, architecture: str, artifacts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The Argo Workflow that builds ``build`` with Buildah and pushes it to ``registry_url``."""
    repository = registry_url.rsplit(":", 1)[0]
    registry_host = registry_url.split("/", 1)[0]
    build_args = (build.build_config or {}).get("build_args") or {}
    arg_flags = "".join(f" --build-arg {shlex.quote(f'{k}={v}')}" for k, v in build_args.items())
    dockerfile = Path(build.dockerfile_path).name
    script = (
        "set -e\n"
        "buildah build --isolation chroot --storage-driver overlay"
        " --ulimit nofile=524288:524288"
        f" --layers --cache-from {shlex.quote(repository + '/cache')} --cache-to {shlex.quote(repository + '/cache')}"
        f"{arg_flags}"
        f" -f {shlex.quote('/context/' + dockerfile)} -t {shlex.quote(registry_url)} /context\n"
        f"buildah push --storage-driver overlay --retry 3 {shlex.quote(registry_url)}\n"
    )
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "generateName": f"custom-image-{build.name}-",
            "namespace": BUILD_NAMESPACE,
            "labels": {"thinkube.io/custom-image-build": str(build.id)},
        },
        "spec": {
            "entrypoint": "build",
            "serviceAccountName": "image-builder",
            "activeDeadlineSeconds": 3 * 3600,
            "volumes": [
                {"name": "docker-config", "secret": {"secretName": "docker-config"}},
                {"name": "buildah-storage", "emptyDir": {}},
            ],
            "templates": [{
                "name": "build",
                "nodeSelector": {"kubernetes.io/arch": architecture},
                "inputs": {"artifacts": artifacts},
                "container": {
                    "image": f"{registry_host}/{BUILDAH_IMAGE}",
                    "command": ["/bin/bash", "-c"],
                    "args": [script],
                    # Buildah unpacks layers in its own mount namespace: SYS_ADMIN,
                    # and no AppArmor profile that forbids those mounts.
                    "securityContext": {
                        "runAsUser": 0,
                        "capabilities": {"add": ["SYS_ADMIN"]},
                        "appArmorProfile": {"type": "Unconfined"},
                    },
                    "env": [{"name": "REGISTRY_AUTH_FILE", "value": "/registry-auth/config.json"}],
                    "volumeMounts": [
                        {"name": "docker-config", "mountPath": "/registry-auth"},
                        {"name": "buildah-storage", "mountPath": "/var/lib/containers"},
                    ],
                    "resources": {
                        "requests": {"memory": "1Gi", "cpu": "500m"},
                        "limits": {"memory": "4Gi", "cpu": "4"},
                    },
                },
            }],
        },
    }


def _k8s():
    from kubernetes import client, config

    config.load_incluster_config()
    return client.CustomObjectsApi(), client.CoreV1Api()


def _submit_workflow(workflow: Dict[str, Any]) -> str:
    custom, _ = _k8s()
    created = custom.create_namespaced_custom_object(
        "argoproj.io", "v1alpha1", BUILD_NAMESPACE, "workflows", workflow
    )
    return created["metadata"]["name"]


def _workflow_state(name: str) -> tuple[str, str, str]:
    """The Workflow's phase, its message, and the build pod's log so far."""
    custom, core = _k8s()
    workflow = custom.get_namespaced_custom_object("argoproj.io", "v1alpha1", BUILD_NAMESPACE, "workflows", name)
    status = workflow.get("status") or {}
    pods = core.list_namespaced_pod(BUILD_NAMESPACE, label_selector=f"workflows.argoproj.io/workflow={name}").items
    log = ""
    if pods and pods[0].status.phase not in ("Pending",):
        response = core.read_namespaced_pod_log(
            pods[0].metadata.name, BUILD_NAMESPACE, container="main", _preload_content=False
        )
        log = response.data.decode("utf-8", errors="replace")
    return status.get("phase") or "Pending", status.get("message") or "", log


async def _run_image_build(build: CustomImageBuild, db: Session) -> Dict[str, Any]:
    """Build ``build`` in an Argo Workflow and copy the build pod's log into its log file.

    The log's path goes into ``build.output`` before the build starts, so a
    caller polling the record can read the log while the build runs.
    """
    registry_url = f"registry.{settings.DOMAIN_NAME}/library/{build.name}:latest"

    app_log_dir = BUILD_LOG_DIR / build.name
    app_log_dir.mkdir(parents=True, exist_ok=True)
    log_path = app_log_dir / f"build-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    build.output = str(log_path)
    db.commit()

    header = (
        "=== THINKUBE BUILD LOG ===\n"
        f"Build ID: {build.id}\n"
        f"Image: {build.name}\n"
        f"Started at: {datetime.now()}\n"
    )

    def write(body: str) -> None:
        log_path.write_text(header + body)

    dockerfile_path = Path(build.dockerfile_path)
    if not dockerfile_path.exists():
        write(f"\n=== BUILD FAILED ===\nDockerfile not found: {dockerfile_path}\n")
        return {"return_code": 1}
    try:
        artifacts = _context_artifacts(dockerfile_path.parent)
    except ValueError as e:
        write(f"\n=== BUILD FAILED ===\n{e}\n")
        return {"return_code": 1}

    architecture = ARCHITECTURES[platform.machine()]
    name = await asyncio.to_thread(_submit_workflow, _build_workflow(build, registry_url, architecture, artifacts))
    started = f"Workflow: {BUILD_NAMESPACE}/{name} on {architecture}\n\n=== BUILD OUTPUT ===\n"
    write(started)
    logger.info(f"Custom image {build.name} building in workflow {name}")

    while True:
        await asyncio.sleep(POLL_SECONDS)
        phase, message, pod_log = await asyncio.to_thread(_workflow_state, name)
        if phase not in FINAL_PHASES:
            write(started + pod_log)
            continue
        if phase == "Succeeded":
            write(started + pod_log + f"\n\n=== BUILD COMPLETED SUCCESSFULLY ===\nImage available at: {registry_url}\nFinished at: {datetime.now()}\n")
            return {"return_code": 0, "registry_url": registry_url}
        write(started + pod_log + f"\n\n=== BUILD FAILED ===\nWorkflow {phase}: {message}\nFailed at: {datetime.now()}\n")
        return {"return_code": 1}


def mark_orphaned_image_builds(db: Session = None, still_running=None) -> int:
    """A custom image marked building with no build task behind it is marked failed.

    The build runs as a task inside the backend process and dies with it,
    while the record keeps saying building. At startup no task exists, so
    every such record is orphaned. Later, ``still_running(build_id)`` says
    whether this process holds the build; a build that began on a pod that
    was replaced mid-way is caught that way.
    """
    close_db = False
    if db is None:
        db = SessionLocal()()
        close_db = True
    try:
        stuck = db.query(CustomImageBuild).filter(CustomImageBuild.status == "building").all()
        if still_running is not None:
            stuck = [b for b in stuck if not still_running(str(b.id))]
        for build in stuck:
            build.status = "failed"
            build.output = "thinkube-control restarted while this image was building; start the build again"
            build.completed_at = datetime.utcnow()
            logger.warning(f"custom image {build.name} was building when thinkube-control stopped; marked failed")
        if stuck:
            db.commit()
        return len(stuck)
    finally:
        if close_db:
            db.close()
