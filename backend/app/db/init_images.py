"""Initialize container images in the database"""

import logging
import os
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.image_discovery import ImageDiscovery

logger = logging.getLogger(__name__)


def init_images(db: Session = None):
    """Initialize container images in the database

    This function discovers and populates the container_images table
    with images from Harbor ConfigMap manifests.
    """
    close_db = False
    if db is None:
        session_factory = SessionLocal()
        db = session_factory()
        close_db = True

    try:
        # Check if images already exist
        from app.models.container_images import ContainerImage
        existing_count = db.query(ContainerImage).count()

        if existing_count > 0:
            logger.info(
                f"Container images already initialized ({existing_count} images found)"
            )
        else:
            logger.info("Initializing container images for the first time")

        # Run image discovery
        logger.info("Running image discovery from ConfigMap manifests")
        discovery = ImageDiscovery(db)
        discovered = discovery.discover_all()

        # Log results
        total_images = 0
        for category, images in discovered.items():
            count = len(images)
            logger.info(f"Discovered {count} {category} images")

            # Log some example images for each category
            for image in images[:3]:  # First 3 images
                logger.info(f"  - {image.name}:{image.tag} ({image.description[:50] if image.description else 'No description'}...)")

            if count > 3:
                logger.info(f"  ... and {count - 3} more {category} images")

            total_images += count

        if total_images == 0:
            logger.warning(
                "No images were discovered! This might indicate that Harbor deployment "
                "hasn't created image manifests yet or there's a configuration issue."
            )
            logger.info(
                "Images will be discovered once Harbor deployment playbooks are run."
            )
        else:
            logger.info(f"Successfully discovered {total_images} total images:")
            logger.info(f"  - Core (protected): {len(discovered.get('core', []))}")
            logger.info(f"  - Custom (protected): {len(discovered.get('custom', []))}")
            logger.info(f"  - User: {len(discovered.get('user', []))}")

        logger.info("Image initialization completed successfully")

    except Exception as e:
        logger.error(f"Failed to initialize images: {e}")
        # Don't raise the exception - allow the app to start even if image discovery fails
        # Images can be synced later via the API
        logger.warning(
            "Image discovery failed but application will continue. "
            "Use the /api/v1/harbor/images/sync endpoint to retry later."
        )
    finally:
        if close_db:
            db.close()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Run initialization
    init_images()