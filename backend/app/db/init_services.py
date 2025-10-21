"""Initialize services in the database"""

import logging
import os
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.discovery import ServiceDiscovery
from app.models.services import Service


logger = logging.getLogger(__name__)


def init_services(db: Session = None):
    """Initialize services in the database

    This function populates the services table with core services
    and discovers any deployed optional services or user apps.
    """
    close_db = False
    if db is None:
        session_factory = SessionLocal()
        db = session_factory()
        close_db = True

    try:
        # Get domain from environment or use default
        domain = os.getenv("DOMAIN_NAME", "thinkube.com")

        # Check if services already exist
        existing_count = db.query(Service).count()
        if existing_count > 0:
            logger.info(
                f"Services already initialized ({existing_count} services found)"
            )
            # Still run discovery to update status
        else:
            logger.info("Initializing services for the first time")

        # Run service discovery
        logger.info(f"Running service discovery with domain: {domain}")
        discovery = ServiceDiscovery(db, domain)
        discovered = discovery.discover_all()

        # Log results
        total_services = 0
        for service_type, services in discovered.items():
            logger.info(f"Discovered {len(services)} {service_type} services")
            for service in services:
                logger.info(f"  - {service.name} ({service.display_name})")
            total_services += len(services)

        if total_services == 0:
            logger.warning(
                "No services were discovered! This might indicate a configuration issue."
            )

        logger.info("Service initialization completed successfully")

    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")
        raise
    finally:
        if close_db:
            db.close()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Run initialization
    init_services()


# 🤖 Generated with [Claude Code](https://claude.ai/code)
