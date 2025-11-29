"""API endpoints for Harbor image management"""

import os
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid import UUID
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Body
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, cast, func
from sqlalchemy.dialects.postgresql import JSONB

from app.core.security import get_current_active_user, User
from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.container_images import ContainerImage, ImageMirrorJob
from app.services.image_discovery import ImageDiscovery
from app.services.harbor_client import HarborClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/harbor", tags=["harbor"])


# Image Inventory Management

@router.get("/images", response_model=Dict[str, Any], operation_id="list_harbor_images")
def list_images(
    category: Optional[str] = Query(None, description="Filter by category (system/user)"),
    protected: Optional[bool] = Query(None, description="Filter by protected status"),
    search: Optional[str] = Query(None, description="Search in name, description, or repository"),
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(100, ge=1, le=500, description="Number of items to return"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """List container images with filtering and pagination"""
    query = db.query(ContainerImage)

    # Apply filters
    if category:
        query = query.filter(ContainerImage.category == category)

    if protected is not None:
        query = query.filter(ContainerImage.protected == protected)

    if search:
        search_filter = or_(
            ContainerImage.name.ilike(f"%{search}%"),
            ContainerImage.description.ilike(f"%{search}%"),
            ContainerImage.repository.ilike(f"%{search}%")
        )
        query = query.filter(search_filter)

    # Get total count
    total = query.count()

    # Apply pagination
    images = query.order_by(
        ContainerImage.category,
        ContainerImage.name
    ).offset(skip).limit(limit).all()

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "images": [image.to_dict() for image in images]
    }


@router.get("/stats/images")
def get_image_statistics(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Get image inventory statistics"""
    try:
        total = db.query(ContainerImage).count()
        system_count = db.query(ContainerImage).filter(ContainerImage.category == "system").count()
        user_count = db.query(ContainerImage).filter(ContainerImage.category == "user").count()
        protected_count = db.query(ContainerImage).filter(ContainerImage.protected == True).count()

        stats = {
            "total": total,
            "by_category": {
                "system": system_count,
                "user": user_count
            },
            "protected": protected_count,
            "vulnerable": 0
        }

        return stats
    except Exception as e:
        logger.error(f"Error getting image statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/images/{image_id}", response_model=Dict[str, Any], operation_id="get_harbor_image")
def get_image(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Get a specific image by ID"""
    image = db.query(ContainerImage).filter(ContainerImage.id == image_id).first()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    return image.to_dict()


@router.post("/images", response_model=Dict[str, Any], operation_id="register_harbor_image")
def add_image_to_mirror(
    image_data: Dict[str, Any] = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Add a new image to mirror from a public registry

    Expected body:
    {
        "source_url": "docker.io/library/nginx:latest",
        "description": "Nginx web server",
        "auto_mirror": true  # Whether to start mirroring immediately
    }
    """
    source_url = image_data.get("source_url")
    if not source_url:
        raise HTTPException(status_code=400, detail="source_url is required")

    # Parse image components
    try:
        parts = source_url.split("/")
        tag = "latest"

        if ":" in parts[-1]:
            name_tag = parts[-1].split(":")
            name = name_tag[0]
            tag = name_tag[1]
        else:
            name = parts[-1]

        # Determine repository path
        if len(parts) >= 2:
            repository = "/".join(parts[1:]).split(":")[0]
        else:
            repository = f"library/{name}"

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid source_url format: {e}")

    # Check if image already exists
    harbor_registry = os.getenv("HARBOR_REGISTRY", "registry.thinkube.com")
    existing = db.query(ContainerImage).filter(
        ContainerImage.name == name,
        ContainerImage.tag == tag,
        ContainerImage.category == "user"
    ).first()

    if existing:
        raise HTTPException(status_code=409, detail="Image already exists in inventory")

    # Always require mirroring for user images
    if not image_data.get("auto_mirror", True):
        raise HTTPException(
            status_code=400,
            detail="User images must be mirrored to Harbor"
        )

    from app.models.deployments import TemplateDeployment
    from uuid import uuid4

    # Instead of creating the image immediately, create a deployment
    if True:  # Always mirror
        # Don't create the image record yet - only after successful mirroring
        # Create a temporary image record with pending status
        new_image = ContainerImage(
            name=name,
            registry=harbor_registry,
            repository=f"library/{name}",
            tag=tag,
            source_url=source_url,
            destination_url=f"{harbor_registry}/library/{name}:{tag}",
            description=image_data.get("description", ""),
            category="user",
            protected=False,
            mirror_date=datetime.utcnow(),
            image_metadata={**image_data.get("metadata", {}), "status": "pending"}
        )

        db.add(new_image)
        db.flush()  # Get the ID without committing

        job = ImageMirrorJob(
            image_id=new_image.id,  # Link to the image
            job_type="mirror",
            status="pending",
            source_url=source_url,
            destination_url=new_image.destination_url,
            image_category="user",
            created_by=current_user.get("preferred_username", "unknown"),
            job_metadata={
                "description": image_data.get("description", "")
            }
        )
        db.add(job)
        db.commit()

        # Create deployment for WebSocket tracking
        deployment = TemplateDeployment(
            id=uuid4(),
            name=f"mirror-{name}:{tag}",
            template_url=f"harbor-mirror:{source_url}",
            status="pending",
            created_by=current_user.get("preferred_username", "unknown"),
            variables={
                "source_image": source_url,
                "destination_image": new_image.destination_url,
                "image_description": image_data.get("description", ""),
                "image_category": "user",
                "image_id": str(new_image.id),
                "job_id": str(job.id)
            }
        )
        db.add(deployment)
        db.commit()

        # Return deployment info for WebSocket connection
        return {
            "deployment_id": str(deployment.id),
            "status": "pending",
            "message": "Mirror deployment prepared. Connect to WebSocket to start execution.",
            "websocket_url": f"/ws/harbor/mirror/{deployment.id}",
            "source_url": source_url,
            "destination_url": new_image.destination_url
        }

    # If not auto-mirroring, just add to inventory (but this shouldn't happen for user images)
    new_image = ContainerImage(
        name=name,
        registry=harbor_registry,
        repository=f"library/{name}",
        tag=tag,
        source_url=source_url,
        destination_url=f"{harbor_registry}/library/{name}:{tag}",
        description=image_data.get("description", ""),
        category="user",
        protected=False,
        mirror_date=datetime.utcnow(),
        image_metadata={**image_data.get("metadata", {}), "status": "inventory_only"}
    )

    db.add(new_image)
    db.commit()
    db.refresh(new_image)

    return {
        "image": new_image.to_dict(),
        "message": "Image added to inventory"
    }


@router.put("/images/{image_id}", response_model=Dict[str, Any])
def update_image(
    image_id: UUID,
    image_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Update image metadata (description, metadata fields)"""
    image = db.query(ContainerImage).filter(ContainerImage.id == image_id).first()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Only allow updating certain fields
    if "description" in image_data:
        image.description = image_data["description"]

    if "metadata" in image_data:
        image.image_metadata = {**image.image_metadata, **image_data["metadata"]}

    db.commit()
    db.refresh(image)

    return image.to_dict()


@router.patch("/images/{image_id}/toggle-base", operation_id="toggle_image_base_status")
def toggle_base_status(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Toggle the is_base status of a mirrored image"""
    image = db.query(ContainerImage).filter(ContainerImage.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Toggle the is_base status
    image.is_base = not image.is_base
    db.commit()
    db.refresh(image)

    return {
        "message": f"Image {'marked as' if image.is_base else 'unmarked as'} base",
        "image": image.to_dict()
    }


@router.put("/images/{image_id}/template", operation_id="update_image_template")
def update_image_template(
    image_id: UUID,
    template_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Update the Dockerfile template for a mirrored base image"""
    image = db.query(ContainerImage).filter(ContainerImage.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    if not image.is_base:
        raise HTTPException(
            status_code=400,
            detail="Image must be marked as base to have a template"
        )

    # Update template
    image.template = template_data.get("template", "")
    db.commit()
    db.refresh(image)

    return {
        "message": "Template updated successfully",
        "image": image.to_dict()
    }


@router.get("/images/{image_id}/edit-template", operation_id="edit_image_template_in_code_server")
def edit_image_template_in_code_server(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Get code-server URL to edit the Dockerfile template for a mirrored base image"""
    image = db.query(ContainerImage).filter(ContainerImage.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    if not image.is_base:
        raise HTTPException(
            status_code=400,
            detail="Image must be marked as base to have a template"
        )

    # Create template directory structure
    # Backend uses /home/dockerfiles/, code-server sees it as /home/thinkube/dockerfiles/
    templates_base = Path("/home/thinkube/dockerfiles/templates/mirrored")
    templates_base.mkdir(parents=True, exist_ok=True)
    templates_base.chmod(0o775)

    # Create safe filename from image name
    safe_name = image.name.replace("/", "_").replace(":", "_")
    template_file = templates_base / f"{safe_name}.Dockerfile"

    # Create template file if it doesn't exist or if template is empty
    if not template_file.exists() or not image.template:
        # Generate minimal template
        # Use harbor_project/name:tag format WITHOUT registry domain (podman defaults to registry.thinkube.com)
        # More reliable than repository field which had a parsing bug
        image_ref = f"{image.harbor_project or 'library'}/{image.name}:{image.tag}"
        template_content = image.template or f"""FROM {image_ref}

# Extended from {image.name}
# Add your customizations here

"""
        template_file.write_text(template_content)
        template_file.chmod(0o664)

        # Update database with template content
        image.template = template_content
        db.commit()

    # Generate code-server URL with payload parameter to open file
    # Code-server sees the path as /home/thinkube/dockerfiles/
    domain = os.environ.get("DOMAIN_NAME", "thinkube.com")
    coder_folder_path = "/home/thinkube/dockerfiles/templates/mirrored"
    coder_file_path = f"/home/thinkube/dockerfiles/templates/mirrored/{safe_name}.Dockerfile"
    payload = f'[["openFile","vscode-remote://{coder_file_path}"]]'
    editor_url = f"https://code.{domain}/?folder={coder_folder_path}&payload={payload}"

    return {
        "editor_url": editor_url,
        "folder_path": coder_folder_path,
        "file_path": coder_file_path,
        "message": "Open this URL in a new tab to edit the Dockerfile template. Authentication is handled by SSO."
    }


@router.delete("/images/{image_id}")
def delete_image(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Delete an image (only user images can be deleted)"""
    image = db.query(ContainerImage).filter(ContainerImage.id == image_id).first()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    if image.protected:
        raise HTTPException(
            status_code=403,
            detail="Protected images cannot be deleted"
        )

    if image.category == "system":
        raise HTTPException(
            status_code=403,
            detail=f"{image.category.capitalize()} images cannot be deleted"
        )

    # TODO: Check if image is in use by any deployments

    db.delete(image)
    db.commit()

    return {"message": f"Image {image.name}:{image.tag} deleted successfully"}


# Image Mirroring Operations

@router.post("/images/{image_id}/remirror", operation_id="remirror_harbor_image")
async def remirror_image(
    image_id: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Re-mirror an image (only for :latest tags)

    Uses deployment-based flow with WebSocket for real-time progress.
    """
    # Get the image
    image = db.query(ContainerImage).filter(ContainerImage.id == image_id).first()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Only allow re-mirroring for :latest tags
    if image.tag != "latest":
        raise HTTPException(
            status_code=400,
            detail="Re-mirroring is only allowed for images with :latest tag to ensure you get security updates"
        )

    # Create a mirror job
    job = ImageMirrorJob(
        image_id=image.id,
        job_type="remirror",
        status="pending",
        source_url=image.source_url,
        destination_url=image.destination_url,
        image_category=image.category,
        created_by=current_user.preferred_username,
        job_metadata={
            "reason": "Re-mirror to get latest version",
            "previous_digest": image.digest
        }
    )
    db.add(job)
    db.flush()

    # Create deployment for WebSocket tracking
    deployment = TemplateDeployment(
        id=uuid4(),
        name=f"remirror-{image.name}:latest",
        template_url=f"harbor-remirror:{image.source_url}",
        status="pending",
        created_by=current_user.preferred_username,
        variables={
            "source_image": image.source_url,
            "destination_image": image.destination_url,
            "image_description": image.description or "",
            "image_category": image.category,
            "image_id": str(image.id),
            "job_id": str(job.id),
            "is_remirror": True
        }
    )
    db.add(deployment)
    db.commit()

    return {
        "deployment_id": str(deployment.id),
        "status": "pending",
        "message": "Re-mirror deployment prepared. Connect to WebSocket to start execution.",
        "websocket_url": f"/ws/harbor/mirror/{deployment.id}",
        "source_url": image.source_url,
        "destination_url": image.destination_url
    }


@router.post("/images/mirror", operation_id="bulk_mirror_images")
async def trigger_mirror_job(
    mirror_request: Dict[str, Any] = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Trigger a mirror job for one or more images

    Expected body:
    {
        "image_ids": ["uuid1", "uuid2"],  # Optional: specific images to mirror
        "mirror_all_user": true  # Optional: mirror all user images
    }
    """
    images_to_mirror = []

    if mirror_request.get("image_ids"):
        # Mirror specific images
        for image_id in mirror_request["image_ids"]:
            image = db.query(ContainerImage).filter(
                ContainerImage.id == image_id
            ).first()
            if image:
                images_to_mirror.append(image)

    elif mirror_request.get("mirror_all_user"):
        # Mirror all user images
        images_to_mirror = db.query(ContainerImage).filter(
            ContainerImage.category == "user"
        ).all()

    if not images_to_mirror:
        raise HTTPException(status_code=400, detail="No images selected for mirroring")

    # Create mirror jobs
    jobs = []
    for image in images_to_mirror:
        job = ImageMirrorJob(
            image_id=image.id,  # Link to the image
            job_type="mirror",
            status="pending",
            source_url=image.source_url,
            destination_url=image.destination_url,
            image_category=image.category,
            created_by=current_user.preferred_username
        )
        db.add(job)
        jobs.append(job)

    db.commit()

    # Queue background tasks
    for job, image in zip(jobs, images_to_mirror):
        background_tasks.add_task(execute_mirror_job, job.id, image.id, db)

    return {
        "message": f"Started mirroring {len(jobs)} images",
        "jobs": [job.to_dict() for job in jobs]
    }


@router.post("/images/build")
async def trigger_build_job(
    build_request: Dict[str, Any] = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Trigger a custom image build job

    Expected body:
    {
        "dockerfile_content": "FROM python:3.12...",
        "image_name": "custom-app",
        "tag": "latest",
        "description": "Custom application image"
    }
    """
    # TODO: Implement custom build functionality
    raise HTTPException(
        status_code=501,
        detail="Custom image building not yet implemented"
    )


# Image Discovery and Sync

@router.post("/images/sync")
def sync_with_harbor(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Sync image inventory with Harbor ConfigMaps"""
    discovery = ImageDiscovery(db)

    try:
        stats = discovery.sync_with_configmaps()
        return {
            "message": "Sync completed successfully",
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"Failed to sync with ConfigMaps: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Sync failed: {str(e)}"
        )




# Mirror Job Management

@router.get("/jobs")
def list_mirror_jobs(
    status: Optional[str] = Query(None, description="Filter by job status"),
    job_type: Optional[str] = Query(None, description="Filter by job type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """List mirror/build jobs with filtering"""
    query = db.query(ImageMirrorJob)

    if status:
        query = query.filter(ImageMirrorJob.status == status)

    if job_type:
        query = query.filter(ImageMirrorJob.job_type == job_type)

    total = query.count()

    jobs = query.order_by(
        ImageMirrorJob.created_at.desc()
    ).offset(skip).limit(limit).all()

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "jobs": [job.to_dict() for job in jobs]
    }


@router.get("/jobs/{job_id}")
def get_job_status(
    job_id: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_dual_auth)
):
    """Get status of a specific job"""
    job = db.query(ImageMirrorJob).filter(ImageMirrorJob.id == job_id).first()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return job.to_dict()


# Harbor API Proxy Endpoints

@router.get("/projects")
def list_harbor_projects(
    current_user = Depends(get_current_user_dual_auth)
):
    """List Harbor projects"""
    try:
        with HarborClient() as client:
            projects = client.list_projects()
            return projects
    except Exception as e:
        logger.error(f"Failed to list Harbor projects: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
def check_harbor_health(
    current_user = Depends(get_current_user_dual_auth)
):
    """Check Harbor health status"""
    try:
        with HarborClient() as client:
            health = client.get_health()
            return health
    except Exception as e:
        logger.error(f"Failed to check Harbor health: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }


# Background task functions

async def execute_mirror_job(job_id: UUID, image_id: UUID, db: Session):
    """Execute a mirror job using Ansible playbook via background executor

    This runs in the background to mirror images from source to Harbor
    """
    from app.services.background_executor import background_executor
    from app.models.deployments import TemplateDeployment

    job = db.query(ImageMirrorJob).filter(ImageMirrorJob.id == job_id).first()
    image = db.query(ContainerImage).filter(ContainerImage.id == image_id).first()

    if not job:
        logger.error(f"Job {job_id} not found")
        return

    if not image:
        logger.error(f"Image {image_id} not found")
        return

    try:
        # Create a deployment record for tracking (reusing existing infrastructure)
        deployment = TemplateDeployment(
            name=f"mirror-{job.id}",
            template_url=f"image-mirror:{job.source_url}",
            status="pending",
            created_by=job.created_by,
            variables={
                "source_image": job.source_url,
                "destination_image": job.destination_url,
                "image_category": job.image_category,
                "image_description": job.job_metadata.get("description", "") if job.job_metadata else ""
            }
        )
        db.add(deployment)
        db.commit()

        # Update job status
        job.status = "running"
        job.started_at = datetime.utcnow()
        db.commit()

        # Execute using the background executor (reusing existing pattern)
        await background_executor.execute_component_playbook(
            deployment_id=str(deployment.id),
            playbook_path="playbooks/mirror-image.yaml",  # Correct playbook path
            extra_vars={
                "source_image": job.source_url,
                # Don't pass destination_image - let playbook construct it
                "image_category": job.image_category,
                "image_description": job.job_metadata.get("description", "") if job.job_metadata else ""
            },
            component_name=f"Image: {job.source_url}"
        )

        # Wait for deployment to complete (check periodically)
        max_wait = 600  # 10 minutes
        check_interval = 5  # 5 seconds
        elapsed = 0

        while elapsed < max_wait:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            db.refresh(deployment)
            if deployment.status in ["success", "failed", "cancelled"]:
                break

        # Update job and image based on deployment result
        if deployment.status == "success":
            job.status = "success"
            job.logs = deployment.output

            # Update image status to active
            if image.image_metadata is None:
                image.image_metadata = {}
            image.image_metadata["status"] = "active"
            db.commit()
        else:
            job.status = "failed"
            job.error_message = deployment.output or "Deployment failed"
            job.logs = deployment.output

            # DELETE the image record since mirroring failed
            logger.warning(f"Deleting image {image_id} due to failed mirroring")
            db.delete(image)
            db.commit()

        job.completed_at = datetime.utcnow()
        db.commit()

    except Exception as e:
        logger.error(f"Failed to execute mirror job {job_id}: {e}")

        # Update job status
        if job:
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.utcnow()

        # DELETE the image record on exception
        if image:
            logger.warning(f"Deleting image {image_id} due to exception: {e}")
            db.delete(image)

        db.commit()