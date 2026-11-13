# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Custom images: images built from a Dockerfile the user edits in Thinkube IDE,
under ~/dockerfiles/custom/<name>, and pushed to Harbor as library/<name>:latest."""

import asyncio
import logging
import platform
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import User, get_current_user
from app.db.session import get_db
from app.models.container_images import ContainerImage
from app.models.custom_images import CustomImageBuild
from app.services import argo, builds, dockerfiles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-images")


# The base images offered always, each with the Dockerfile a custom image made from it starts with
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



class CreateImageRequest(BaseModel):
    """A new custom image"""
    name: str
    dockerfile_content: Optional[str] = None  # The Dockerfile, when it is given whole
    build_config: Optional[Dict[str, Any]] = {}  # base_image, description, build_args
    parent_image_id: Optional[str] = None  # The custom image this one extends
    is_base: bool = False  # Other custom images may start from it
    scope: str = "general"
    copy_parent_dockerfile: bool = True  # Start from the parent's Dockerfile, not from FROM <parent>


class BuildImageRequest(BaseModel):
    build_args: Optional[Dict[str, str]] = {}


class CustomImageResponse(BaseModel):
    id: str
    name: str
    dockerfile_path: str
    status: str
    build_config: Optional[Dict[str, Any]]
    output: Optional[str]
    workflow: Optional[str] = None
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
    build_id: str
    status: str
    message: str
    workflow: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build(db: Session, image_id: UUID) -> CustomImageBuild:
    build = db.query(CustomImageBuild).filter_by(id=image_id).first()
    if not build:
        raise HTTPException(status_code=404, detail="Image not found")
    return build


def _summary(build: CustomImageBuild) -> CustomImageResponse:
    """The image without its build log, which images_get_build_log returns."""
    return CustomImageResponse(**{**build.to_dict(), "output": None})


def _registry_url(name: str) -> str:
    return f"registry.{settings.DOMAIN_NAME}/library/{name}:latest"


def _mirrored_base(db: Session, base_image: str) -> Optional[ContainerImage]:
    """The mirrored base image base_image names: by id, by its address in Harbor, or by name."""
    try:
        found = db.query(ContainerImage).filter_by(id=UUID(base_image)).first()
    except ValueError:
        found = db.query(ContainerImage).filter(
            (ContainerImage.destination_url == base_image) | (ContainerImage.name == base_image)
        ).first()
    return found if found and found.is_base and found.template else None


def _custom_base(db: Session, base_image: str) -> Optional[CustomImageBuild]:
    """The custom base image whose id base_image is."""
    try:
        found = db.query(CustomImageBuild).filter_by(id=UUID(base_image)).first()
    except ValueError:
        return None
    return found if found and found.is_base and found.template else None


def first_dockerfile(db: Session, request: CreateImageRequest) -> str:
    """The Dockerfile a new custom image starts with: the one given, the parent's, the base image's
    template, or a FROM line for the base image."""
    if request.dockerfile_content:
        return request.dockerfile_content
    if request.parent_image_id:
        parent = db.query(CustomImageBuild).filter_by(id=request.parent_image_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent image not found")
        parent_dockerfile = dockerfiles.custom_dir(parent.name) / "Dockerfile"
        if request.copy_parent_dockerfile:
            if not parent_dockerfile.exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"The parent's Dockerfile {dockerfiles.ide_path(parent_dockerfile)} does not exist",
                )
            return parent_dockerfile.read_text()
        return f"FROM {parent.registry_url or _registry_url(parent.name)}\n\n# Extended from {parent.name}\n"
    base_image = (request.build_config or {}).get("base_image")
    if not base_image:
        raise HTTPException(
            status_code=400,
            detail="Give build_config.base_image, parent_image_id or dockerfile_content; "
                   "images_get_base_registry lists the base images",
        )
    if base_image in BASE_IMAGE_REGISTRY:
        if request.scope == "general":
            request.scope = BASE_IMAGE_REGISTRY[base_image]["scope"]
        return BASE_IMAGE_REGISTRY[base_image]["template"]
    custom = _custom_base(db, base_image)
    if custom:
        return custom.template
    mirrored = _mirrored_base(db, base_image)
    if mirrored:
        return mirrored.template
    try:
        base_id = UUID(base_image)
    except ValueError:
        base_id = None
    if base_id:
        # A base image named by its id that has no template: FROM its address in Harbor
        built = db.query(CustomImageBuild).filter_by(id=base_id).first()
        listed = db.query(ContainerImage).filter_by(id=base_id).first()
        if built and built.registry_url:
            base_image = built.registry_url
        elif listed:
            base_image = listed.destination_url
        else:
            raise HTTPException(status_code=404, detail=f"No built custom image or mirrored image has the id {base_image}")
    description = (request.build_config or {}).get("description", "Custom image")
    return f"FROM {base_image}\n\n# Image: {request.name}\n# Description: {description}\n\nWORKDIR /app\n"


@router.post("", response_model=CustomImageResponse, operation_id="images_create_custom")
def create_custom_image(
    request: CreateImageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a custom image: its folder ~/dockerfiles/custom/<name> with a first Dockerfile, a README and a
    context folder. It is not built yet; images_build_custom builds it."""
    if db.query(CustomImageBuild).filter_by(name=request.name).first():
        raise HTTPException(status_code=400, detail=f"Image '{request.name}' already exists")
    if not request.name or "/" in request.name or request.name.startswith("."):
        raise HTTPException(status_code=400, detail="The image's name is one folder name, without '/'")
    image_dir = dockerfiles.custom_dir(request.name)
    if image_dir.exists():
        raise HTTPException(status_code=400,
                            detail=f"The folder {dockerfiles.ide_path(image_dir)} already exists")
    parent_id = UUID(request.parent_image_id) if request.parent_image_id and not request.dockerfile_content else None
    content = first_dockerfile(db, request)
    description = (request.build_config or {}).get("description", "Custom image")
    readme = (
        f"# {request.name}\n\n{description}\n\n"
        "- `Dockerfile`: the image's definition\n"
        "- `context/`: files to copy into the image, with `COPY context/<file> <path>` in the Dockerfile\n\n"
        "The Build button of the Images page in Thinkube Control builds the image and pushes it to Harbor.\n"
        "The folder is sent to the build as text, 900 KiB at most.\n"
    )
    try:
        dockerfiles.write(image_dir / "Dockerfile", content)
        dockerfiles.write(image_dir / "README.md", readme)
        dockerfiles.write(image_dir / "context" / ".gitkeep", "")
    except OSError as e:
        shutil.rmtree(image_dir, ignore_errors=True)
        raise HTTPException(status_code=500,
                            detail=f"Could not write {dockerfiles.ide_path(image_dir)}: {e}") from e
    build = CustomImageBuild(
        id=uuid4(),
        name=request.name,
        dockerfile_path=dockerfiles.ide_path(image_dir / "Dockerfile"),
        status="not_built",
        build_config=request.build_config,
        is_base=request.is_base,
        scope=request.scope,
        parent_image_id=parent_id,
        created_by=current_user.preferred_username,
    )
    db.add(build)
    db.commit()
    return _summary(build)


@router.get("/base-registry", operation_id="images_get_base_registry")
def get_base_registry(
    type_filter: Optional[str] = Query(None, description="Filter by type: jupyter or standard."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The base images a custom image can start from: the ones offered always, the custom images marked as
    base and built, and the mirrored images marked as base."""
    registry = []

    def add(image_type: str, entry: Dict[str, Any]) -> None:
        if type_filter is None or image_type == type_filter:
            registry.append({**entry, "is_base": True, "type": image_type})

    for key, image in BASE_IMAGE_REGISTRY.items():
        add("jupyter" if image.get("scope") == "jupyter" else "standard", {
            "id": key, "name": image["name"], "display_name": image.get("description", image["name"]),
            "registry_url": f"registry.{settings.DOMAIN_NAME}/library/{image['name']}",
            "source": "predefined", "template": image.get("template", ""), "group": "Base Templates",
        })
    built = db.query(CustomImageBuild).filter(CustomImageBuild.is_base == True,  # noqa: E712
                                              CustomImageBuild.status == "success").all()
    for custom in built:
        add("jupyter" if custom.scope == "jupyter" else "standard", {
            "id": str(custom.id), "name": custom.name, "display_name": f"Custom: {custom.name}",
            "registry_url": custom.registry_url, "source": "built", "template": custom.template,
            "group": "Custom Built",
        })
    for mirrored in db.query(ContainerImage).filter(ContainerImage.is_base == True).all():  # noqa: E712
        purpose = (mirrored.image_metadata or {}).get("purpose")
        add("jupyter" if purpose == "jupyter" else "standard", {
            "id": str(mirrored.id), "name": mirrored.name, "display_name": f"Mirrored: {mirrored.name}",
            "registry_url": mirrored.destination_url, "source": "mirrored", "template": mirrored.template,
            "group": "Mirrored Images",
        })
    return {"images": registry, "types": ["jupyter", "standard"]}


@router.get("", operation_id="images_list_custom")
def list_custom_images(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The custom images, newest first, each with its status: not_built, building, success or failed."""
    query = db.query(CustomImageBuild)
    images = query.order_by(CustomImageBuild.created_at.desc()).offset(skip).limit(limit).all()
    return {"builds": [_summary(image) for image in images], "total": query.count()}


@router.get("/{image_id}", response_model=CustomImageResponse, operation_id="images_get_custom")
def get_custom_image(image_id: UUID, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    """One custom image, with its status. images_get_build_log returns the log of its build."""
    return _summary(_build(db, image_id))


@router.post("/{image_id}/build", response_model=BuildResponse, operation_id="images_build_custom")
async def build_custom_image(
    image_id: UUID,
    request: BuildImageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start building a custom image and answer at once.

    The build runs as an Argo workflow with Buildah, on a node of the control plane's architecture, and
    pushes the image to Harbor as library/<name>:latest. The image's folder is sent to the build as text,
    900 KiB at most. Poll images_get_custom until status is success or failed; images_get_build_log returns
    the log while the build runs and after it ends.
    """
    build = _build(db, image_id)
    if build.status == "building":
        raise HTTPException(status_code=400, detail="Image is already being built")
    image_dir = dockerfiles.custom_dir(build.name)
    if not (image_dir / "Dockerfile").exists():
        raise HTTPException(status_code=409,
                            detail=f"{dockerfiles.ide_path(image_dir / 'Dockerfile')} does not exist")
    try:
        artifacts = dockerfiles.context_artifacts(image_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if request.build_args:
        build.build_config = {**(build.build_config or {}), "build_args": request.build_args}
    machine = platform.machine()
    if machine not in builds.ARCHITECTURES:
        raise HTTPException(status_code=500, detail=f"No node architecture is known for the machine '{machine}'")
    registry_url = _registry_url(build.name)
    build.workflow = await asyncio.to_thread(
        argo.submit,
        builds.workflow(str(build.id), build.name, (build.build_config or {}).get("build_args") or {},
                        registry_url, builds.ARCHITECTURES[machine], artifacts),
    )
    build.status = "building"
    build.registry_url = registry_url
    build.output = None
    build.started_at = _now()
    build.completed_at = None
    db.commit()
    return BuildResponse(
        build_id=str(build.id),
        status="building",
        message="Build started; poll images_get_custom until status is success or failed",
        workflow=build.workflow,
    )


@router.get("/{image_id}/dockerfile", operation_id="images_get_dockerfile")
def get_dockerfile(image_id: UUID, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """The Dockerfile of a custom image, as it is now in ~/dockerfiles/custom/<name>."""
    build = _build(db, image_id)
    file = dockerfiles.custom_dir(build.name) / "Dockerfile"
    if not file.exists():
        raise HTTPException(status_code=404, detail=f"{dockerfiles.ide_path(file)} does not exist")
    text = file.read_text()
    return {"content": text, "dockerfile": text, "path": dockerfiles.ide_path(file), "image_name": build.name,
            "scope": build.scope, "is_base": build.is_base}


@router.put("/{image_id}/dockerfile", operation_id="images_update_dockerfile")
def update_dockerfile(
    image_id: UUID,
    body: Dict[str, str] = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replace the Dockerfile of a custom image. The image is then not built: build it again."""
    build = _build(db, image_id)
    if build.status == "building":
        raise HTTPException(status_code=400, detail="The image is being built; change its Dockerfile after")
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    dockerfiles.write(dockerfiles.custom_dir(build.name) / "Dockerfile", content)
    build.status = "not_built"
    build.output = None
    db.commit()
    return {"message": "Dockerfile updated successfully"}


@router.get("/{image_id}/log", operation_id="images_get_build_log")
def get_build_log(image_id: UUID, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """The log of the image's last build, so far while it is building, and the workflow that ran it."""
    build = _build(db, image_id)
    return {"status": build.status, "workflow": build.workflow, "log": build.output or ""}


@router.get("/{image_id}/editor-url", operation_id="images_get_editor_url")
def get_editor_url(image_id: UUID, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """The Thinkube IDE address that opens the image's folder with its Dockerfile."""
    build = _build(db, image_id)
    folder = dockerfiles.custom_dir(build.name)
    return {
        "editor_url": dockerfiles.editor_url(settings.DOMAIN_NAME, folder, folder / "Dockerfile"),
        "folder_path": dockerfiles.ide_path(folder),
        "message": "Open this URL in a new tab to edit the Dockerfile. Authentication is handled by SSO.",
    }


@router.patch("/{image_id}/toggle-base", operation_id="images_toggle_custom_base")
def toggle_custom_base(image_id: UUID, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """Mark a custom image as a base for other custom images, or remove the mark."""
    build = _build(db, image_id)
    build.is_base = not build.is_base
    db.commit()
    db.refresh(build)
    return {"message": f"Image {'marked as' if build.is_base else 'unmarked as'} base", "id": str(build.id),
            "is_base": build.is_base}


@router.put("/{image_id}/template", operation_id="images_update_custom_template")
def update_custom_template(
    image_id: UUID,
    template_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set the Dockerfile template that images made from this custom base image start with."""
    build = _build(db, image_id)
    if not build.is_base:
        raise HTTPException(status_code=400, detail="Image must be marked as base to have a template")
    build.template = template_data.get("template", "")
    db.commit()
    db.refresh(build)
    return {"message": "Template updated successfully", "id": str(build.id), "template": build.template}


@router.delete("/{image_id}", operation_id="images_delete_custom")
async def delete_custom_image(image_id: UUID, db: Session = Depends(get_db),
                              current_user: User = Depends(get_current_user)):
    """Delete a custom image: its record, its folder ~/dockerfiles/custom/<name> and its build workflows.
    The image itself stays in Harbor. Refused while it is building."""
    build = _build(db, image_id)
    if build.status == "building":
        raise HTTPException(status_code=400, detail="Cannot delete image while building")
    image_dir = dockerfiles.custom_dir(build.name)
    if image_dir.exists():
        shutil.rmtree(image_dir)
    await asyncio.to_thread(argo.delete_labelled, builds.LABEL, str(build.id))
    db.delete(build)
    db.commit()
    return {"message": f"Image '{build.name}' deleted successfully"}
