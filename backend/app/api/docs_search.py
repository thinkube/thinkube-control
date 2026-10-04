# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Docs-as-MCP — search the *deployed* Thinkube documentation.

Folds the Context7-style docs lookup into thinkube-control's existing MCP surface,
gated on the docs being deployed. The docs app is the app deployed from the
thinkube.org template, found in thinkube-control's own deployment records. Its
Antora Lunr search index (``search-index.js``) is read over the in-cluster URL
of that app's service — no standalone MCP server, no new ``.mcp.json``
registration.

When no app has been deployed from the docs template, the tools answer
``docs_not_deployed``. When the docs app is deployed but its index cannot be
fetched or read, they answer HTTP 502 naming the URL and the failure.

Exposed as MCP tools via ``operation_id`` (see ``app/__init__.py`` include list):
``search_thinkube_docs`` and ``get_thinkube_doc``.
"""
import json
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.deployments import TemplateDeployment

router = APIRouter()

DOCS_TEMPLATE_URL = "https://github.com/thinkube/thinkube.org"
# The docs container's port, from the template's thinkube.yaml.
DOCS_PORT = 8080

_NOT_DEPLOYED = {
    "status": "docs_not_deployed",
    "message": (
        "The Thinkube documentation is not deployed. Deploy the docs "
        "template to make the docs searchable from here."
    ),
}


def docs_app_name(db: Session) -> Optional[str]:
    """Name of the app most recently deployed from the docs template, or None."""
    deployment = (
        db.query(TemplateDeployment)
        .filter(
            TemplateDeployment.template_url.in_([DOCS_TEMPLATE_URL, DOCS_TEMPLATE_URL + "/"]),
            TemplateDeployment.status == "success",
        )
        .order_by(TemplateDeployment.created_at.desc())
        .first()
    )
    return deployment.name if deployment else None


def docs_index_url(app_name: str) -> str:
    """In-cluster URL of the docs app's Lunr index; the app's service and namespace carry its name."""
    return f"http://{app_name}.{app_name}.svc.cluster.local:{DOCS_PORT}/search-index.js"


async def _load_documents(db: Session) -> Optional[dict]:
    """The documents of the deployed docs' Lunr index, or None when no docs app is deployed.

    Raises HTTPException 502 naming the URL when the index cannot be fetched or read.
    """
    app_name = docs_app_name(db)
    if app_name is None:
        return None
    url = docs_index_url(app_name)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Cannot fetch the docs index {url}: {e!r}") from e
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"Cannot fetch the docs index {url}: HTTP {resp.status_code}"
        )
    txt = resp.text
    try:
        data = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Cannot parse the docs index {url}: {e}") from e
    documents = (data.get("store") or {}).get("documents") if isinstance(data, dict) else None
    if not isinstance(documents, dict):
        raise HTTPException(
            status_code=502, detail=f"The docs index {url} has no store.documents mapping"
        )
    return documents


def _snippet(text, n=260):
    text = text or ""
    return text[:n].rstrip() + "…" if len(text) > n else text


def _search(documents, query, limit=8):
    terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 1]
    if not terms:
        return []
    # word-boundary patterns: "fine" matches "fine-tune" but not "define"
    patterns = [re.compile(r"\b" + re.escape(t)) for t in terms]
    scored = []
    for d in documents.values():
        title = (d.get("title") or "").lower()
        text = (d.get("text") or "").lower()
        haystack = title + " " + text
        # coverage = how many distinct query terms the page matches — the dominant
        # signal, so a page matching all the terms beats one matching just one,
        # regardless of raw frequency. Then title hits, then text frequency.
        coverage = sum(1 for p in patterns if p.search(haystack))
        if not coverage:
            continue
        title_hits = sum(len(p.findall(title)) for p in patterns)
        text_hits = sum(len(p.findall(text)) for p in patterns)
        score = coverage * 1000 + title_hits * 10 + text_hits
        scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:limit]]


@router.get("/search", operation_id="search_thinkube_docs",
            summary="Search the Thinkube documentation")
async def search_thinkube_docs(
    query: str = Query(..., description="What to look for in the Thinkube docs"),
    db: Session = Depends(get_db),
):
    """Search the deployed Thinkube docs; returns the best-matching pages
    (title, url, snippet). Use this to find where something is documented."""
    docs = await _load_documents(db)
    if docs is None:
        return _NOT_DEPLOYED
    return {
        "results": [
            {
                "title": d.get("title"),
                "url": d.get("url"),
                "name": d.get("name"),
                "snippet": _snippet(d.get("text")),
            }
            for d in _search(docs, query)
        ]
    }


@router.get("/page", operation_id="get_thinkube_doc",
            summary="Get the full text of a Thinkube documentation page")
async def get_thinkube_doc(
    page: str = Query(..., description="Page name or url, as returned by search"),
    db: Session = Depends(get_db),
):
    """Return the full text of a Thinkube docs page by name or url, to ground an
    answer in the actual documentation."""
    docs = await _load_documents(db)
    if docs is None:
        return _NOT_DEPLOYED
    key = page.lstrip("/").replace(".html", "")
    for d in docs.values():
        if (
            d.get("name") == page
            or d.get("url") == page
            or d.get("name") == key
            or (d.get("url") and key in d["url"])
        ):
            return {"title": d.get("title"), "url": d.get("url"), "text": d.get("text")}
    return {"status": "not_found", "message": f'No docs page matching "{page}".'}
