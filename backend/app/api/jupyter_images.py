"""API endpoints for JupyterHub image discovery"""

import logging
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.container_images import ContainerImage
from app.models.jupyterhub_config import JupyterHubConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["jupyter-images"])


@router.get("/jupyter", response_model=List[Dict[str, Any]])
def get_jupyter_images(
    db: Session = Depends(get_db)
):
    """Get list of available Jupyter notebook images for JupyterHub

    This endpoint is called by JupyterHub to dynamically discover available images.
    No authentication required as it's called from within the cluster.

    Returns a list of images suitable for Jupyter notebooks with metadata for
    profile generation in JupyterHub. Filters out hidden images based on
    JupyterHub configuration.
    """
    try:
        # Get JupyterHub configuration to filter hidden images
        config = db.query(JupyterHubConfig).first()
        hidden_images = set(config.hidden_images) if config and config.hidden_images else set()

        if hidden_images:
            logger.info(f"Filtering out hidden images: {hidden_images}")

        # Query for images that have purpose="jupyter" in their metadata
        query = db.query(ContainerImage)

        images = query.all()
        logger.info(f"Total images in database: {len(images)}")

        # Format response for JupyterHub consumption
        result = []
        jupyter_count = 0

        for image in images:
            # Extract metadata from image_metadata field
            metadata = image.image_metadata or {}

            # Log ALL images to see what's in the database
            logger.info(f"Image: {image.name}, metadata: {metadata}, has_purpose: {'purpose' in metadata}")

            # Only include images with purpose="jupyter" in metadata
            if metadata.get('purpose') != 'jupyter':
                continue

            # Skip hidden images
            if image.name in hidden_images:
                logger.info(f"Skipping hidden image: {image.name}")
                continue

            jupyter_count += 1
            logger.info(f"Found Jupyter image #{jupyter_count}: {image.name}")

            # Use metadata fields directly - they're properly set in the manifest
            # Check if this image is the configured default
            is_default = (config and image.name == config.default_image)

            result.append({
                'name': image.name,
                'display_name': metadata.get('display_name', image.name),
                'description': image.description or f"Jupyter notebook: {metadata.get('display_name', image.name)}",
                'default': is_default,
                'metadata': {
                    'image_size': image.size_bytes,
                    'registry': image.registry,
                    'repository': image.repository,
                    'tag': image.tag
                }
            })

        # If no images found, this is a CRITICAL ERROR - fail properly
        if not result:
            logger.error("CRITICAL: No Jupyter images found in database with purpose='jupyter'")
            logger.error("This means either:")
            logger.error("1. Harbor images haven't been synced")
            logger.error("2. Images don't have proper metadata")
            logger.error("3. Database query is failing")
            raise HTTPException(
                status_code=500,
                detail="No Jupyter images available. Harbor images may not be synced or lack proper metadata."
            )

        return result

    except HTTPException:
        raise  # Re-raise HTTP exceptions
    except Exception as e:
        logger.error(f"Error getting Jupyter images: {e}")
        # FAIL PROPERLY - don't return fake data
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve Jupyter images: {str(e)}"
        )