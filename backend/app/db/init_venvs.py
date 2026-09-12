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

    Creates the fine-tuning and agent-dev templates, and keeps an existing
    template's package list equal to the one in the code.
    """
    close_db = False
    if db is None:
        session_factory = SessionLocal()
        db = session_factory()
        close_db = True

    try:
        # The built-in templates are owned by the code: an existing record is
        # brought to the current package list, a missing one is created.
        created = 0
        updated = 0
        for template_id, template_data in VENV_TEMPLATES.items():
            all_packages = template_data["packages"].copy()
            for special in template_data.get("special_installs", []):
                all_packages.append(special)

            existing = (
                db.query(JupyterVenv)
                .filter_by(name=template_id, is_template=True)
                .first()
            )
            if existing is None:
                db.add(
                    JupyterVenv(
                        id=uuid4(),
                        name=template_id,
                        packages=all_packages,
                        status="template",  # Special status for templates
                        is_template=True,
                        created_by="system",
                    )
                )
                created += 1
                logger.info(f"Created venv template: {template_id} ({len(all_packages)} packages)")
            elif list(existing.packages or []) != all_packages:
                existing.packages = all_packages
                updated += 1
                logger.info(f"Updated venv template: {template_id} ({len(all_packages)} packages)")

        db.commit()
        logger.info(f"Venv templates: {created} created, {updated} updated")

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
