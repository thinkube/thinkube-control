"""ConfigMap-based image discovery for Harbor images"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.models.container_images import ContainerImage

logger = logging.getLogger(__name__)


class ImageDiscovery:
    """Discover container images from Kubernetes ConfigMaps

    This service discovers Harbor image manifests that were created
    during deployment and imports them into the database.
    """

    # Label to identify image manifest ConfigMaps
    IMAGE_MANIFEST_LABEL = "thinkube.io/image-manifest"

    def __init__(self, db: Session):
        """Initialize image discovery

        Args:
            db: Database session
        """
        self.db = db
        self._init_kubernetes()

    def _init_kubernetes(self):
        """Initialize Kubernetes client"""
        try:
            # Try in-cluster config first (when running in pod)
            config.load_incluster_config()
            logger.info("Using in-cluster Kubernetes configuration")
            self.core_v1 = client.CoreV1Api()
        except config.ConfigException:
            try:
                # Fall back to kubeconfig file
                config.load_kube_config()
                logger.info("Using kubeconfig file for Kubernetes access")
                self.core_v1 = client.CoreV1Api()
            except config.ConfigException as e:
                logger.error(f"Failed to initialize Kubernetes client: {e}")
                logger.warning("Kubernetes access not available - image discovery disabled")
                self.core_v1 = None

    def discover_all(self) -> Dict[str, List[ContainerImage]]:
        """Discover all images from ConfigMap manifests

        Returns:
            Dictionary with categories as keys and lists of images as values
        """
        images = {"system": [], "user": []}

        if self.core_v1 is None:
            logger.warning("Kubernetes client not initialized - skipping discovery")
            return images

        try:
            # Get all ConfigMaps with our label
            configmaps = self.core_v1.list_config_map_for_all_namespaces(
                label_selector=f"{self.IMAGE_MANIFEST_LABEL}=true"
            )

            logger.info(f"Found {len(configmaps.items)} image manifest ConfigMaps")

            for cm in configmaps.items:
                logger.info(
                    f"Processing image manifest ConfigMap {cm.metadata.name} "
                    f"in namespace {cm.metadata.namespace}"
                )

                category_images = self._extract_images_from_configmap(cm)
                if category_images:
                    category = cm.metadata.labels.get("thinkube.io/category", "user")
                    images[category].extend(category_images)
                    logger.info(
                        f"Discovered {len(category_images)} {category} images "
                        f"from ConfigMap {cm.metadata.name}"
                    )
                else:
                    logger.warning(
                        f"No images extracted from ConfigMap {cm.metadata.name}"
                    )

        except ApiException as e:
            logger.error(f"Failed to discover images from ConfigMaps: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during image discovery: {e}")
            raise

        # Sync discovered images to database
        self._sync_images(images)

        return images

    def _extract_images_from_configmap(self, configmap: Any) -> List[ContainerImage]:
        """Extract image information from a ConfigMap

        Args:
            configmap: Kubernetes ConfigMap object

        Returns:
            List of ContainerImage objects
        """
        images = []

        try:
            # Get manifest.json content
            manifest_json = configmap.data.get("manifest.json")
            if not manifest_json:
                logger.warning(
                    f"ConfigMap {configmap.metadata.name} has no manifest.json"
                )
                return images

            # Parse JSON manifest
            manifest = json.loads(manifest_json)

            # Validate manifest version
            if manifest.get("manifest_version") != "1.0":
                logger.warning(
                    f"Unknown manifest version: {manifest.get('manifest_version')}"
                )

            # Extract images
            for image_data in manifest.get("images", []):
                try:
                    image = self._create_image_from_manifest(image_data)
                    if image:
                        images.append(image)
                except Exception as e:
                    logger.error(
                        f"Failed to create image from manifest data: {e}, "
                        f"Image: {image_data.get('name', 'unknown')}"
                    )

        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse JSON from ConfigMap {configmap.metadata.name}: {e}"
            )
        except Exception as e:
            logger.error(
                f"Unexpected error extracting images from ConfigMap "
                f"{configmap.metadata.name}: {e}"
            )

        return images

    def _create_image_from_manifest(self, data: Dict[str, Any]) -> Optional[ContainerImage]:
        """Create a ContainerImage object from manifest data

        Args:
            data: Image data from manifest

        Returns:
            ContainerImage object or None if invalid
        """
        try:
            # Parse mirror date
            mirror_date = None
            if data.get("mirror_date"):
                try:
                    mirror_date = datetime.fromisoformat(
                        data["mirror_date"].replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    logger.warning(f"Invalid mirror_date: {data.get('mirror_date')}")
                    mirror_date = datetime.utcnow()
            else:
                mirror_date = datetime.utcnow()

            image = ContainerImage(
                name=data.get("name"),
                registry=data.get("registry"),
                repository=data.get("repository"),
                tag=data.get("tag", "latest"),
                source_url=data.get("source_url"),
                destination_url=data.get("destination_url"),
                description=data.get("description"),
                category=data.get("category", "user"),
                source=data.get("source", "mirrored"),
                protected=data.get("protected", False),
                mirror_date=mirror_date,
                image_metadata=data.get("metadata", {}),
                harbor_project=data.get("harbor_project", "library"),
            )

            return image

        except Exception as e:
            logger.error(f"Failed to create ContainerImage: {e}")
            return None

    def _sync_images(self, images: Dict[str, List[ContainerImage]]):
        """Sync discovered images to the database

        This performs an upsert operation - updates existing images
        or inserts new ones.

        Args:
            images: Dictionary of images by category
        """
        total_synced = 0

        # De-duplicate images across all categories before syncing
        # Key: (registry, repository, tag) -> image
        unique_images = {}
        for category, image_list in images.items():
            for image in image_list:
                key = (image.registry, image.repository, image.tag)
                if key in unique_images:
                    logger.warning(
                        f"Duplicate image found: {image.name}:{image.tag} "
                        f"(in category '{category}', already seen in another ConfigMap). "
                        f"Keeping the latest occurrence."
                    )
                unique_images[key] = image

        # Now sync the unique images
        for key, image in unique_images.items():
                try:
                    # Check if image already exists
                    existing = self.db.query(ContainerImage).filter(
                        ContainerImage.registry == image.registry,
                        ContainerImage.repository == image.repository,
                        ContainerImage.tag == image.tag
                    ).first()

                    if existing:
                        # Update existing image
                        existing.description = image.description
                        existing.category = image.category
                        existing.protected = image.protected
                        existing.image_metadata = image.image_metadata
                        existing.last_synced = datetime.utcnow()
                        logger.debug(
                            f"Updated existing image: {image.name}:{image.tag}"
                        )
                    else:
                        # Add new image
                        self.db.add(image)
                        logger.info(
                            f"Added new {category} image: {image.name}:{image.tag}"
                        )

                    # Flush after each image to detect duplicates immediately
                    self.db.flush()
                    total_synced += 1

                except Exception as e:
                    logger.error(
                        f"Failed to sync image {image.name}:{image.tag}: {e}"
                    )
                    self.db.rollback()
                    # Continue with other images
                    continue

        try:
            self.db.commit()
            logger.info(f"Successfully synced {total_synced} images to database")
        except Exception as e:
            logger.error(f"Failed to commit image sync: {e}")
            self.db.rollback()
            raise

    def sync_with_configmaps(self) -> Dict[str, int]:
        """Manually trigger sync with ConfigMaps

        Returns:
            Statistics about the sync operation
        """
        logger.info("Starting manual image discovery sync")

        if self.core_v1 is None:
            logger.error("Cannot sync - Kubernetes client not available")
            raise Exception("Kubernetes client not initialized")

        # Discover all images
        images = self.discover_all()

        # Calculate statistics
        stats = {
            "system": len(images.get("system", [])),
            "user": len(images.get("user", [])),
            "total": sum(len(v) for v in images.values())
        }

        logger.info(f"Image discovery sync completed: {stats}")

        return stats

    def get_image_by_name(self, name: str, tag: str = "latest") -> Optional[ContainerImage]:
        """Get a specific image by name and tag

        Args:
            name: Image name
            tag: Image tag (default: latest)

        Returns:
            ContainerImage or None if not found
        """
        return self.db.query(ContainerImage).filter(
            ContainerImage.name == name,
            ContainerImage.tag == tag
        ).first()

    def get_images_by_category(self, category: str) -> List[ContainerImage]:
        """Get all images in a specific category

        Args:
            category: Image category (system, user)

        Returns:
            List of ContainerImage objects
        """
        return self.db.query(ContainerImage).filter(
            ContainerImage.category == category
        ).order_by(ContainerImage.name).all()

    def get_protected_images(self) -> List[ContainerImage]:
        """Get all protected images

        Returns:
            List of protected ContainerImage objects
        """
        return self.db.query(ContainerImage).filter(
            ContainerImage.protected == True
        ).order_by(ContainerImage.category, ContainerImage.name).all()