# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Images: the container images mirrored into Harbor, and the jobs that mirror them."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from kubernetes import client as k8s_client, config as k8s_config
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import User, get_current_user
from app.db.session import get_db
from app.models.container_images import ContainerImage, ImageMirrorJob
from app.services import argo, dockerfiles, mirror
from app.services.image_discovery import ImageDiscovery

logger = logging.getLogger(__name__)

router = APIRouter()


class MirrorRequest(BaseModel):
    source_url: str = Field(..., description="The image to mirror, with its registry and tag, for example "
                                             "docker.io/library/nginx:1.27 or nvcr.io/nvidia/nemo-automodel:26.08.")
    description: str = Field("", description="What the image is for.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Free fields kept with the image.")


class BulkMirrorRequest(BaseModel):
    image_ids: List[UUID] = Field(..., description="The ids of the images to mirror again from their sources.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _registry() -> str:
    return f"registry.{settings.DOMAIN_NAME}"


def parse_source(source_url: str) -> tuple[str, str]:
    """The name and the tag of an image address; the name is its last path element, the tag latest when it has none."""
    last = source_url.rstrip("/").split("/")[-1]
    if not last or "@" in last:
        raise ValueError(f"'{source_url}' is not an image with a tag: write registry/path/name:tag")
    name, _, tag = last.partition(":")
    if not name:
        raise ValueError(f"'{source_url}' has no image name: write registry/path/name:tag")
    return name, tag or "latest"


def _image(db: Session, image_id: UUID) -> ContainerImage:
    image = db.query(ContainerImage).filter(ContainerImage.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    return image


async def start_mirror(db: Session, image: ContainerImage, job_type: str, user: User,
                       metadata: Dict[str, Any]) -> ImageMirrorJob:
    """Record a mirror job for the image and submit its workflow; the job, running."""
    running = (
        db.query(ImageMirrorJob)
        .filter(ImageMirrorJob.image_id == image.id, ImageMirrorJob.status == "running")
        .first()
    )
    if running:
        raise HTTPException(status_code=409, detail=f"{image.name}:{image.tag} is already being mirrored: "
                                                    f"job {running.id}")
    architectures = await asyncio.to_thread(argo.node_architectures)
    job = ImageMirrorJob(
        image_id=image.id,
        job_type=job_type,
        status="running",
        source_url=image.source_url,
        destination_url=image.destination_url,
        image_category=image.category,
        created_by=user.preferred_username,
        started_at=_now(),
        job_metadata=metadata,
    )
    db.add(job)
    db.flush()
    job.workflow = await asyncio.to_thread(
        argo.submit,
        mirror.workflow(str(job.id), mirror.workflow_name_part(image.name), image.source_url,
                        image.destination_url, architectures),
    )
    db.commit()
    db.refresh(job)
    return job


@router.get("/images", operation_id="images_list")
def list_images(
    category: Optional[str] = Query(None, description="Filter by category: system or user."),
    protected: Optional[bool] = Query(None, description="Filter by protected status."),
    search: Optional[str] = Query(None, description="Text to find in the name, description or repository."),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The container images Thinkube knows in Harbor: the platform's own (system) and the ones a user mirrored (user)."""
    query = db.query(ContainerImage)
    if category:
        query = query.filter(ContainerImage.category == category)
    if protected is not None:
        query = query.filter(ContainerImage.protected == protected)
    if search:
        query = query.filter(or_(
            ContainerImage.name.ilike(f"%{search}%"),
            ContainerImage.description.ilike(f"%{search}%"),
            ContainerImage.repository.ilike(f"%{search}%"),
        ))
    total = query.count()
    images = query.order_by(ContainerImage.category, ContainerImage.name).offset(skip).limit(limit).all()
    return {"total": total, "skip": skip, "limit": limit, "images": [image.to_dict() for image in images]}


@router.get("/images/stats", operation_id="images_stats")
def image_statistics(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """How many images there are, by category, and how many are protected."""
    count = lambda *filters: db.query(ContainerImage).filter(*filters).count()  # noqa: E731
    return {
        "total": count(),
        "by_category": {"system": count(ContainerImage.category == "system"),
                        "user": count(ContainerImage.category == "user")},
        "protected": count(ContainerImage.protected == True),  # noqa: E712
    }


@router.get("/images/jupyter", operation_id="images_list_jupyter")
def jupyter_images(db: Session = Depends(get_db)):
    """The images for notebook servers: those whose metadata says purpose jupyter.

    Not behind a login: the platform reads it from inside the cluster.
    """
    result = []
    for image in db.query(ContainerImage).order_by(ContainerImage.name).all():
        metadata = image.image_metadata or {}
        if metadata.get("purpose") != "jupyter":
            continue
        display_name = metadata.get("display_name", image.name)
        result.append({
            "name": image.name,
            "display_name": display_name,
            "description": image.description or f"Jupyter notebook: {display_name}",
            "default": not result,
            "metadata": {"image_size": image.size_bytes, "registry": image.registry,
                         "repository": image.repository, "tag": image.tag},
        })
    return result


@router.post("/images/sync", operation_id="images_sync")
def sync_images(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Read the platform's image manifests (the ConfigMaps the install writes) into the list of images."""
    statistics = ImageDiscovery(db).sync_with_configmaps()
    return {"message": "Sync completed successfully", "statistics": statistics}


@router.post("/images/mirror", operation_id="images_mirror_again")
async def mirror_again(
    request: BulkMirrorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mirror images that are in the list again from their sources. Answers at once with one job per image;
    images_get_job follows a job."""
    images = [_image(db, image_id) for image_id in request.image_ids]
    if not images:
        raise HTTPException(status_code=400, detail="No images selected for mirroring")
    without_source = [f"{i.name}:{i.tag}" for i in images if not i.source_url]
    if without_source:
        raise HTTPException(status_code=400, detail=f"No source is recorded for: {', '.join(without_source)}")
    jobs = [await start_mirror(db, image, "remirror", current_user, {}) for image in images]
    return {"message": f"Started mirroring {len(jobs)} images", "jobs": [job.to_dict() for job in jobs]}


@router.post("/images", operation_id="images_mirror")
async def mirror_image(
    request: MirrorRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mirror a public image into Harbor, as library/<name>:<tag>, for the architectures of the cluster's nodes
    that the source provides.

    Answers at once with the image and its job. The mirror runs as an Argo workflow; images_get_job gives the
    job's status and log. A mirror that fails removes the image from the list.
    """
    try:
        name, tag = parse_source(request.source_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    existing = (
        db.query(ContainerImage)
        .filter(ContainerImage.registry == _registry(), ContainerImage.repository == f"library/{name}",
                ContainerImage.tag == tag)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"{existing.destination_url} is already in the list of images")
    image = ContainerImage(
        name=name,
        registry=_registry(),
        repository=f"library/{name}",
        tag=tag,
        source_url=request.source_url,
        destination_url=f"{_registry()}/library/{name}:{tag}",
        description=request.description,
        category="user",
        protected=False,
        source="mirrored",
        is_base=False,
        mirror_date=_now(),
        image_metadata={**request.metadata, "status": "pending"},
    )
    db.add(image)
    db.flush()
    job = await start_mirror(db, image, "mirror", current_user, {"description": request.description})
    db.refresh(image)
    return {"image": image.to_dict(), "job": job.to_dict()}


@router.get("/images/{image_id}", operation_id="images_get")
def get_image(image_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """One image of the list."""
    return _image(db, image_id).to_dict()


@router.put("/images/{image_id}", operation_id="images_update")
def update_image(
    image_id: UUID,
    image_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change an image's description, or add fields to its metadata."""
    image = _image(db, image_id)
    if "description" in image_data:
        image.description = image_data["description"]
    if "metadata" in image_data:
        image.image_metadata = {**(image.image_metadata or {}), **image_data["metadata"]}
    db.commit()
    db.refresh(image)
    return image.to_dict()


@router.patch("/images/{image_id}/toggle-base", operation_id="images_toggle_base")
def toggle_base(image_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Mark an image as a base for custom images, or remove the mark."""
    image = _image(db, image_id)
    image.is_base = not image.is_base
    db.commit()
    db.refresh(image)
    return {"message": f"Image {'marked as' if image.is_base else 'unmarked as'} base", "image": image.to_dict()}


@router.put("/images/{image_id}/template", operation_id="images_update_template")
def update_template(
    image_id: UUID,
    template_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set the Dockerfile template that custom images made from this base image start with."""
    image = _image(db, image_id)
    if not image.is_base:
        raise HTTPException(status_code=400, detail="Image must be marked as base to have a template")
    image.template = template_data.get("template", "")
    db.commit()
    db.refresh(image)
    return {"message": "Template updated successfully", "image": image.to_dict()}


@router.get("/images/{image_id}/edit-template", operation_id="images_edit_template")
def edit_template(image_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The Thinkube IDE address that opens the Dockerfile template of a base image, written to
    ~/dockerfiles/templates/mirrored first when it has none."""
    image = _image(db, image_id)
    if not image.is_base:
        raise HTTPException(status_code=400, detail="Image must be marked as base to have a template")
    file = dockerfiles.mirrored_template(image.name.replace("/", "_").replace(":", "_"))
    if not file.exists() or not image.template:
        # A custom image build resolves a name without a registry to Harbor
        reference = f"{image.harbor_project or 'library'}/{image.name}:{image.tag}"
        content = image.template or f"FROM {reference}\n\n# Extended from {image.name}\n# Add your customizations here\n\n"
        dockerfiles.write(file, content)
        image.template = content
        db.commit()
    return {
        "editor_url": dockerfiles.editor_url(settings.DOMAIN_NAME, file.parent, file),
        "folder_path": dockerfiles.ide_path(file.parent),
        "file_path": dockerfiles.ide_path(file),
        "message": "Open this URL in a new tab to edit the Dockerfile template. Authentication is handled by SSO.",
    }


def _image_names(ref: str) -> set:
    """The names a pod may give ref with: a reference without a tag means :latest."""
    names = {ref}
    if ref.endswith(":latest"):
        names.add(ref[: -len(":latest")])
    return names


def pods_using(image_ref: str) -> List[str]:
    """namespace/name of every pod not yet finished with a container that runs image_ref."""
    k8s_config.load_incluster_config()
    names = _image_names(image_ref)
    users = []
    for pod in k8s_client.CoreV1Api().list_pod_for_all_namespaces().items:
        if pod.status.phase in ("Succeeded", "Failed"):
            continue
        containers = (pod.spec.containers or []) + (pod.spec.init_containers or [])
        if any(c.image in names for c in containers):
            users.append(f"{pod.metadata.namespace}/{pod.metadata.name}")
    return users


@router.delete("/images/{image_id}", operation_id="images_delete")
def delete_image(image_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Remove a user's image from the list. The image itself stays in Harbor. Refused for a protected or a
    system image, and while a running pod uses the image."""
    image = _image(db, image_id)
    if image.protected:
        raise HTTPException(status_code=403, detail="Protected images cannot be deleted")
    if image.category == "system":
        raise HTTPException(status_code=403, detail="System images cannot be deleted")
    users = pods_using(image.destination_url)
    if users:
        raise HTTPException(status_code=409,
                            detail=f"{image.destination_url} is used by running pods: {', '.join(users)}")
    db.delete(image)
    db.commit()
    return {"message": f"Image {image.name}:{image.tag} removed from the list. "
                       f"The image itself stays in Harbor at {image.destination_url}."}


@router.post("/images/{image_id}/remirror", operation_id="images_remirror")
async def remirror_image(image_id: UUID, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    """Mirror an image again from its source, to get what its tag names now. Only for the tag latest: any
    other tag is expected to name the same image always."""
    image = _image(db, image_id)
    if image.tag != "latest":
        raise HTTPException(status_code=400, detail="Re-mirroring is only allowed for images with the tag latest")
    if not image.source_url:
        raise HTTPException(status_code=400, detail=f"No source is recorded for {image.name}:{image.tag}")
    job = await start_mirror(db, image, "remirror", current_user, {"previous_digest": image.digest})
    return {"image": image.to_dict(), "job": job.to_dict()}


@router.get("/jobs", operation_id="images_list_jobs")
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status: running, success or failed."),
    job_type: Optional[str] = Query(None, description="Filter by type: mirror or remirror."),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The mirror jobs, newest first, without their logs."""
    query = db.query(ImageMirrorJob)
    if status:
        query = query.filter(ImageMirrorJob.status == status)
    if job_type:
        query = query.filter(ImageMirrorJob.job_type == job_type)
    total = query.count()
    jobs = query.order_by(ImageMirrorJob.created_at.desc()).offset(skip).limit(limit).all()
    return {"total": total, "skip": skip, "limit": limit,
            "jobs": [{**job.to_dict(), "logs": None} for job in jobs]}


@router.get("/jobs/{job_id}", operation_id="images_get_job")
def get_job(job_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """One mirror job: its status (running, success or failed), its log so far, and why it failed."""
    job = db.query(ImageMirrorJob).filter(ImageMirrorJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.get("/health", operation_id="images_registry_health")
async def registry_health(current_user: User = Depends(get_current_user)):
    """Whether Harbor answers, as its own health check reports it."""
    url = f"https://{_registry()}/api/v2.0/health"
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            answer = await http.get(url)
    except httpx.HTTPError as e:
        return {"status": "unhealthy", "error": f"{url} did not answer: {e}"}
    if answer.status_code != 200:
        return {"status": "unhealthy", "error": f"{url} answered {answer.status_code}"}
    return answer.json()
