# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Reads for Control's extensions, outside /api: Control's address sends only
/api and /mcp to the backend, so these are reached only at the Service
backend, inside the cluster. They need no sign-in: an extension makes them in
the background, with no user's token to pass on."""

from typing import Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.services import Service

router = APIRouter(prefix="/internal")


@router.get("/services/enabled")
def service_enabled(namespace: str, db: Session = Depends(get_db)) -> Dict[str, object]:
    """Whether the service in the namespace is switched on in Control's services
    view. A namespace Control lists no service for is on: nothing switched it off."""
    service = db.query(Service).filter(Service.namespace == namespace).first()
    return {"namespace": namespace, "enabled": service is None or bool(service.is_enabled)}
