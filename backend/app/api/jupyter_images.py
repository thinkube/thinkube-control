"""API endpoints for JupyterHub image discovery"""

import logging
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.container_images import ContainerImage

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
    profile generation in JupyterHub.

    Note: We now use a single tk-jupyter-base image with venvs providing
    different Python environments via kernel selection in JupyterLab.
    """
    try:
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

            # Only include images with purpose="jupyter" in metadata
            if metadata.get('purpose') != 'jupyter':
                continue

            jupyter_count += 1
            logger.info(f"Found Jupyter image #{jupyter_count}: {image.name}")

            result.append({
                'name': image.name,
                'display_name': metadata.get('display_name', image.name),
                'description': image.description or f"Jupyter notebook: {metadata.get('display_name', image.name)}",
                'default': jupyter_count == 1,  # First image is default
                'metadata': {
                    'image_size': image.size_bytes,
                    'registry': image.registry,
                    'repository': image.repository,
                    'tag': image.tag
                }
            })

        # If no images found, return empty list (not an error anymore)
        if not result:
            logger.warning("No Jupyter images found in database with purpose='jupyter'")

        return result

    except Exception as e:
        logger.error(f"Error getting Jupyter images: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve Jupyter images: {str(e)}"
        )