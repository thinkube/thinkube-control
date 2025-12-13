"""Initialize Jupyter venv templates in the database"""

import logging
from sqlalchemy.orm import Session
from uuid import uuid4

from app.db.session import SessionLocal
from app.models.jupyter_venvs import JupyterVenv
from app.api.jupyter_venvs import VENV_TEMPLATES

logger = logging.getLogger(__name__)


def init_venvs(db: Session = None):
    """Initialize Jupyter venv templates in the database

    This function creates template entries for fine-tuning and agent-dev
    that users can use as base for creating custom venvs.
    """
    close_db = False
    if db is None:
        session_factory = SessionLocal()
        db = session_factory()
        close_db = True

    try:
        # Check if templates already exist
        existing_count = db.query(JupyterVenv).filter_by(is_template=True).count()

        if existing_count > 0:
            logger.info(
                f"Jupyter venv templates already initialized ({existing_count} templates found)"
            )
            return

        logger.info("Initializing Jupyter venv templates for the first time")

        # Create template entries
        for template_id, template_data in VENV_TEMPLATES.items():
            # Combine packages with special installs for storage
            all_packages = template_data["packages"].copy()
            for special in template_data.get("special_installs", []):
                all_packages.append(special)

            venv_template = JupyterVenv(
                id=uuid4(),
                name=template_id,
                packages=all_packages,
                status="template",  # Special status for templates
                is_template=True,
                created_by="system",
            )
            db.add(venv_template)
            logger.info(f"Created venv template: {template_id} ({len(all_packages)} packages)")

        db.commit()
        logger.info(f"Successfully initialized {len(VENV_TEMPLATES)} venv templates")

    except Exception as e:
        logger.error(f"Failed to initialize venv templates: {e}")
        db.rollback()
    finally:
        if close_db:
            db.close()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Run initialization
    init_venvs()
