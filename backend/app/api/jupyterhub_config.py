# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""API endpoints for the notebook servers' default resources, one set per node.

Each node runs at most one interactive notebook server, which starts with its
node's defaults unless the caller asks for other values. JupyterHub's profile
generator reads the same endpoint for its Server Options form.

The image is fixed to tk-jupyter-base; venvs provide the Python environments
through kernel selection.
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.cluster_resources import get_cluster_resources
from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db
from app.models.jupyterhub_config import JupyterHubConfig, JupyterHubNodeDefaults
from app.services import notebook_resources as res

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jupyterhub", tags=["jupyterhub-config"])


class ResourceValues(BaseModel):
    cpu_cores: int
    memory_gb: int
    gpus: int


class NodeDefaults(ResourceValues):
    node: str
    capacity: ResourceValues = Field(..., description="The most a server on this node can be given.")
    choices: Dict[str, List[int]] = Field(..., description="The values JupyterHub accepts on this node, per resource.")


class JupyterHubConfigResponse(BaseModel):
    nodes: List[NodeDefaults] = Field(..., description="Each node's defaults for its notebook server.")
    default_cpu_cores: int = Field(..., description="What a node without its own defaults starts from.")
    default_memory_gb: int
    default_gpu_count: int


class NodeDefaultsUpdate(ResourceValues):
    node: str


class JupyterHubConfigUpdate(BaseModel):
    nodes: List[NodeDefaultsUpdate]


def _fallback(db: Session) -> JupyterHubConfig:
    row = db.query(JupyterHubConfig).first()
    if row is None:
        row = JupyterHubConfig(
            default_cpu_cores=res.FALLBACK_CPU_CORES,
            default_memory_gb=res.FALLBACK_MEMORY_GB,
            default_gpu_count=res.FALLBACK_GPUS,
        )
    return row


def defaults_for_nodes(db: Session, nodes: List[Dict[str, Any]]) -> List[NodeDefaults]:
    """Every node's defaults: its own row when saved, else the fallback, moved onto the node's choices."""
    fallback = _fallback(db)
    saved = {row.node: row for row in db.query(JupyterHubNodeDefaults).all()}
    result = []
    for node in sorted(nodes, key=lambda n: n["name"]):
        row = saved.get(node["name"])
        wanted = (
            (row.cpu_cores, row.memory_gb, row.gpus)
            if row
            else (fallback.default_cpu_cores, fallback.default_memory_gb, fallback.default_gpu_count)
        )
        result.append(
            NodeDefaults(
                node=node["name"],
                capacity=ResourceValues(**res.node_capacity(node)),
                choices=res.node_choices(node),
                **res.fit_to_node(node, *wanted),
            )
        )
    return result


async def _cluster_nodes() -> List[Dict[str, Any]]:
    nodes = await get_cluster_resources()
    if not nodes:
        raise HTTPException(status_code=503, detail="The cluster's node resources are not known yet; try again in a moment.")
    return nodes


@router.get("/config", response_model=JupyterHubConfigResponse, operation_id="get_jupyterhub_config")
async def get_jupyterhub_config(db: Session = Depends(get_db)):
    """Each node's default CPU cores, memory and GPUs for its notebook server, with the values the node allows.

    JupyterHub calls this from inside the cluster, so no authentication is required.
    """
    fallback = _fallback(db)
    return JupyterHubConfigResponse(
        nodes=defaults_for_nodes(db, await _cluster_nodes()),
        default_cpu_cores=fallback.default_cpu_cores,
        default_memory_gb=fallback.default_memory_gb,
        default_gpu_count=fallback.default_gpu_count,
    )


@router.put("/config", response_model=JupyterHubConfigResponse, operation_id="update_jupyterhub_config")
async def update_jupyterhub_config(
    config_update: JupyterHubConfigUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Save the defaults of one or more nodes. Each value must be one of the node's choices."""
    nodes = {n["name"]: n for n in await _cluster_nodes()}
    problems = []
    for update in config_update.nodes:
        node = nodes.get(update.node)
        if node is None:
            problems.append(f"node '{update.node}' is not in the cluster")
            continue
        choices = res.node_choices(node)
        for field in ("cpu_cores", "memory_gb", "gpus"):
            value = getattr(update, field)
            if value not in choices[field]:
                problems.append(f"{update.node}: {field} {value} is not one of {choices[field]}")
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))

    for update in config_update.nodes:
        row = db.query(JupyterHubNodeDefaults).filter(JupyterHubNodeDefaults.node == update.node).first()
        if row is None:
            row = JupyterHubNodeDefaults(node=update.node)
            db.add(row)
        row.cpu_cores = update.cpu_cores
        row.memory_gb = update.memory_gb
        row.gpus = update.gpus
    db.commit()

    logger.info(
        "notebook server defaults updated by %s: %s",
        current_user.get("preferred_username", "unknown"),
        ", ".join(f"{u.node}=({u.cpu_cores}CPU, {u.memory_gb}GB, {u.gpus}GPU)" for u in config_update.nodes),
    )
    return await get_jupyterhub_config(db)
