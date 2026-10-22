# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The extensions service discovery found, their UI modules, and their APIs,
all under Control's own address (see app/services/extensions.py)."""

from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.services import Service

router = APIRouter()

TIMEOUT = httpx.Timeout(120.0)
# Headers passed on in each direction
REQUEST_HEADERS = ("authorization", "content-type", "accept")
RESPONSE_HEADERS = ("content-type", "content-disposition")


def _extensions(db: Session) -> Dict[str, Service]:
    found = {}
    for service in db.query(Service).filter(Service.is_enabled == True).all():  # noqa: E712
        if (service.service_metadata or {}).get("extension") and service.url:
            found[service.name] = service
    return found


def _extension(db: Session, name: str) -> Service:
    service = _extensions(db).get(name)
    if service is None:
        raise HTTPException(status_code=404, detail=f"no enabled extension named {name}")
    return service


@router.get("/extensions", operation_id="list_extensions")
def list_extensions(current_user: dict = Depends(get_current_user_dual_auth), db: Session = Depends(get_db)):
    """The extensions of Control: each one's title, icon, navigation entries and UI module."""
    listed: List[Dict[str, Any]] = []
    for name, service in sorted(_extensions(db).items()):
        declared = service.service_metadata["extension"]
        listed.append({"name": name, **declared})
    return listed


@router.get("/x/{name}/ui/{path:path}")
async def extension_ui(name: str, path: str, db: Session = Depends(get_db)):
    """A file of the extension's UI module folder. Not behind a login: a browser
    loads a module with import(), which sends no token, and these files are
    the extension's public frontend."""
    service = _extension(db, name)
    folder = service.service_metadata["extension"]["ui"]["folder"]
    wanted = "/" + path
    if not wanted.startswith(folder) or ".." in wanted:
        raise HTTPException(status_code=404, detail=f"{wanted} is not in the UI folder {folder} of {name}")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            upstream = await client.get(f"{service.url.rstrip('/')}{wanted}")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"{name} did not answer for {wanted}: {e}")
    # A redeploy of the extension replaces these files: the browser asks again each time
    return Response(content=upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type"),
                    headers={"Cache-Control": "no-cache"})


@router.api_route("/x/{name}/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def extension_api(name: str, path: str, request: Request, db: Session = Depends(get_db)):
    """A call of the extension's UI to its own API, /api/v1/<path> of the
    extension, made with the user's token: the extension checks it."""
    service = _extension(db, name)
    headers = {k: v for k, v in request.headers.items() if k.lower() in REQUEST_HEADERS}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            upstream = await client.request(
                request.method, f"{service.url.rstrip('/')}/api/v1/{path}",
                params=list(request.query_params.multi_items()),
                content=await request.body(), headers=headers)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"{name} did not answer {request.method} /api/v1/{path}: {e}")
    return Response(content=upstream.content, status_code=upstream.status_code,
                    headers={k: v for k, v in upstream.headers.items() if k.lower() in RESPONSE_HEADERS})
