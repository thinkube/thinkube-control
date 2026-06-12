"""Docs-as-MCP — search the *deployed* Thinkube documentation.

Folds the Context7-style docs lookup into thinkube-control's existing MCP surface,
gated on the docs being deployed. It reads the deployed docs site's Antora Lunr
search index (``search-index.js``) over the in-cluster URL — no standalone MCP
server, no new ``.mcp.json`` registration. If the docs aren't deployed/reachable,
the tools say so instead of failing.

Exposed as MCP tools via ``operation_id`` (see ``app/__init__.py`` include list):
``search_thinkube_docs`` and ``get_thinkube_doc``.
"""
import json
import os
import re

import httpx
from fastapi import APIRouter, Query

router = APIRouter()

# Where the deployed docs serve their Lunr index. Candidates cover the single-pod
# (service == app name) and separate-pods (service == container "docs") service
# naming; an env override wins. First that responds is used; none -> not deployed.
_APP = os.getenv("THINKUBE_DOCS_APP", "thinkube-docs")
_PORT = os.getenv("THINKUBE_DOCS_PORT", "8080")
_INDEX_CANDIDATES = [
    os.getenv("THINKUBE_DOCS_INDEX_URL", ""),
    f"http://{_APP}.{_APP}.svc.cluster.local:{_PORT}/search-index.js",
    f"http://docs.{_APP}.svc.cluster.local:{_PORT}/search-index.js",
]

_NOT_DEPLOYED = {
    "status": "docs_not_deployed",
    "message": (
        "The Thinkube documentation is not deployed. Deploy the thinkube-docs "
        "template to make the docs searchable from here."
    ),
}


async def _load_documents():
    """Fetch + parse the deployed docs' Lunr index -> the documents dict, or None
    if the docs are not deployed / not reachable."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        for url in _INDEX_CANDIDATES:
            if not url:
                continue
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                txt = resp.text
                data = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
                return (data.get("store") or {}).get("documents") or {}
            except Exception:
                continue
    return None


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
):
    """Search the deployed Thinkube docs; returns the best-matching pages
    (title, url, snippet). Use this to find where something is documented."""
    docs = await _load_documents()
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
):
    """Return the full text of a Thinkube docs page by name or url, to ground an
    answer in the actual documentation."""
    docs = await _load_documents()
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
