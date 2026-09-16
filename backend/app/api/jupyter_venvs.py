"""API endpoints for Jupyter virtualenv management

Uses Kubernetes Jobs via Ansible playbook for venv builds on GPU nodes.
Venvs are stored on local hostPath (/var/lib/jupyterhub-venvs) for fast I/O.
After build, venvs are synced to other GPU nodes via rsync.
"""

import os
import re
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from uuid import UUID, uuid4
from pathlib import Path
import tempfile
import yaml

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.api_tokens import get_current_user_dual_auth
from app.db.session import get_db, SessionLocal
from app.models.jupyter_venvs import JupyterVenv
from app.services import detached
from app.services.scrub import Scrubber
from app.services.ansible_environment import ansible_env

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jupyter-venvs", tags=["jupyter-venvs"])


# The package lists of the built-in environments live in one file in the
# thinkube repository, read by the install (build-venvs.sh) and by a rebuild
# from here, so the two builds cannot drift apart.
VENV_PACKAGES_FILE = Path(
    "/home/thinkube/thinkube-platform/core/thinkube/ansible/40_thinkube/core/jupyterhub/venv-packages.txt"
)

# Installs that need their own pip flags, added after a template's packages.
TEMPLATE_DETAILS = {
    "fine-tuning": {
        "description": "Fine-tuning venv with bitsandbytes, peft, trl, and Unsloth",
        "special_installs": [
            "git+https://github.com/unslothai/unsloth-zoo.git --no-deps",
            "unsloth[cu130onlytorch291] @ git+https://github.com/unslothai/unsloth.git --no-build-isolation --no-deps",
        ],
    },
    "agent-dev": {
        "description": "Agent development venv with LangChain, CrewAI, AG2, and more",
        "special_installs": [
            "openlit --no-deps",
        ],
    },
}


def read_package_sections(path: Path = VENV_PACKAGES_FILE) -> Dict[str, List[str]]:
    """Read venv-packages.txt: [section] headers, one package per line, # comments."""
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found; it holds the package lists of the built-in venvs "
            "and comes with the thinkube repository"
        )
    sections: Dict[str, List[str]] = {}
    current = None
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        header = re.fullmatch(r"\[([a-z-]+)\]", line)
        if header:
            current = header.group(1)
            sections[current] = []
        elif current is None:
            raise ValueError(f"{path}: package '{line}' is outside any [section]")
        else:
            sections[current].append(line)
    return sections


def venv_templates() -> Dict[str, Dict[str, Any]]:
    """The built-in venv templates, their packages read from venv-packages.txt."""
    sections = read_package_sections()
    for name in ("base", *TEMPLATE_DETAILS):
        if not sections.get(name):
            raise ValueError(f"{VENV_PACKAGES_FILE}: section [{name}] is missing or empty")
    return {
        name: {
            "name": name,
            "description": details["description"],
            "packages": sections["base"] + sections[name],
            "special_installs": details["special_installs"],
        }
        for name, details in TEMPLATE_DETAILS.items()
    }


# Pydantic models
class CreateVenvRequest(BaseModel):
    """Request to create a new venv"""
    name: str
    packages: Optional[List[str]] = None  # Additional packages beyond template
    parent_template: Optional[str] = None  # "fine-tuning" or "agent-dev"


class VenvResponse(BaseModel):
    """Response for venv details"""
    id: str
    name: str
    packages: List[str]
    status: str
    output: Optional[str]
    is_template: bool
    parent_template_id: Optional[str]
    venv_path: Optional[str]
    architecture: Optional[str]
    architectures_built: List[str] = []
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    created_by: str
    duration: Optional[float]


class BuildVenvRequest(BaseModel):
    """Request to build a venv"""
    force: bool = False  # Force rebuild even if exists


class BuildResponse(BaseModel):
    """Response for build initiation"""
    build_id: str
    status: str
    message: str
    poll_url: str
    warning: Optional[str] = None


# API Endpoints
@router.get("/templates", operation_id="get_venv_templates")
def get_venv_templates(
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get available venv templates"""
    templates = []
    for key, template in venv_templates().items():
        templates.append({
            "id": key,
            "name": template["name"],
            "description": template["description"],
            "package_count": len(template["packages"]),
            "has_special_installs": len(template.get("special_installs", [])) > 0,
        })
    return {"templates": templates}


@router.get("/templates/{template_id}", operation_id="get_venv_template_details")
def get_venv_template_details(
    template_id: str,
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get details of a specific venv template"""
    if template_id not in venv_templates():
        raise HTTPException(status_code=404, detail="Template not found")

    template = venv_templates()[template_id]
    return {
        "id": template_id,
        "name": template["name"],
        "description": template["description"],
        "packages": template["packages"],
        "special_installs": template.get("special_installs", []),
    }


@router.post("", response_model=VenvResponse, operation_id="create_jupyter_venv")
async def create_jupyter_venv(
    request: CreateVenvRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Create a new Jupyter virtualenv"""
    try:
        # Check if venv name already exists
        if request.name in venv_templates():
            raise HTTPException(
                status_code=400,
                detail=f"'{request.name}' is a built-in template; build the template itself to rebuild it, or choose another name",
            )
        existing = db.query(JupyterVenv).filter_by(name=request.name).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Venv '{request.name}' already exists")

        # Validate name (alphanumeric, hyphens, underscores only)
        import re
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*$', request.name):
            raise HTTPException(
                status_code=400,
                detail="Venv name must start with a letter and contain only letters, numbers, hyphens, and underscores"
            )

        # Build package list
        packages = []
        parent_template_id = None

        if request.parent_template:
            if request.parent_template not in venv_templates():
                raise HTTPException(status_code=400, detail=f"Unknown template: {request.parent_template}")

            # Get template packages
            template = venv_templates()[request.parent_template]
            packages = template["packages"].copy()

            # Find or create template record
            template_record = db.query(JupyterVenv).filter_by(
                name=request.parent_template,
                is_template=True
            ).first()
            if template_record:
                parent_template_id = template_record.id

        # Add additional packages
        if request.packages:
            packages.extend(request.packages)

        if not packages:
            raise HTTPException(status_code=400, detail="No packages specified. Use a template or provide packages.")

        # Create database record
        venv = JupyterVenv(
            id=uuid4(),
            name=request.name,
            packages=packages,
            status="pending",
            is_template=False,
            parent_template_id=parent_template_id,
            created_by=current_user.get("preferred_username", "unknown")
        )
        db.add(venv)
        db.commit()

        return VenvResponse(**venv.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create venv: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create venv: {str(e)}")


@router.get("", operation_id="list_jupyter_venvs")
def list_jupyter_venvs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    include_templates: bool = Query(False, description="Include template venvs"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """List all Jupyter virtualenvs"""
    query = db.query(JupyterVenv)

    if not include_templates:
        query = query.filter(JupyterVenv.is_template == False)

    # Get total count
    total = query.count()

    # Apply pagination
    venvs = query.order_by(
        JupyterVenv.created_at.desc()
    ).offset(skip).limit(limit).all()

    return {
        "venvs": [VenvResponse(**venv.to_dict()) for venv in venvs],
        "total": total
    }


@router.get("/{venv_id}", response_model=VenvResponse, operation_id="get_jupyter_venv")
def get_jupyter_venv(
    venv_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get details of a specific venv"""
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    return VenvResponse(**venv.to_dict())


@router.post("/{venv_id}/build", response_model=BuildResponse, operation_id="build_jupyter_venv")
async def build_jupyter_venv(
    venv_id: UUID,
    request: Optional[BuildVenvRequest] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Start building a Jupyter venv and return at once with the id to poll.

    The build runs as a Kubernetes Job on a GPU node with a hostPath volume,
    then the venv is synced to the other GPU nodes. It takes minutes; this
    call does not wait for it. Poll get_jupyter_venv until status is
    success or failed; get_venv_build_logs has the build's log.

    Building a template (fine-tuning, agent-dev) rebuilds the built-in venv
    in place on every node, from the template's package list, and the
    kernel keeps its name. A later venvs release replaces it again.
    """
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    # Check if already building
    if venv.status == "building":
        raise HTTPException(status_code=400, detail="Venv is already being built")

    # No body means no force: a caller that only names the venv gets the plain build.
    force = bool(request and request.force)

    # Check if already built and not forcing
    if venv.status == "success" and not force:
        raise HTTPException(status_code=400, detail="Venv already built. Use force=true to rebuild.")

    # Reset status for new build
    venv.status = "building"
    venv.output = None
    venv.started_at = datetime.now(timezone.utc)
    venv.completed_at = None
    db.commit()

    # Detached from this request: an in-process caller (the MCP bridge) would
    # otherwise wait for the whole build before it got the answer.
    detached.start(f"venv-build:{venv.id}", _execute_venv_build(str(venv.id)))

    return BuildResponse(
        build_id=str(venv.id),
        status="building",
        message="Build started on a GPU node; poll get_jupyter_venv for its status",
        poll_url=f"/jupyter-venvs/{venv.id}",
        warning="The build takes minutes and shows no output while packages install. Poll the status until it is success or failed."
    )


@router.get("/{venv_id}/logs", operation_id="get_venv_build_logs")
async def get_venv_build_logs(
    venv_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Get build log files for a venv"""
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    # Find log files
    log_dir = Path(f"/tmp/thinkube-venvs/{venv.name}")
    logs = []

    if log_dir.exists():
        for log_file in sorted(log_dir.glob("build-*.log"), reverse=True):
            stats = log_file.stat()
            logs.append({
                "filename": log_file.name,
                "path": str(log_file),
                "size": stats.st_size,
                "created": stats.st_ctime,
                "modified": stats.st_mtime
            })

    return {"logs": logs[:10]}  # Return last 10 build logs


@router.get("/{venv_id}/logs/{filename}", operation_id="download_venv_build_log")
async def download_venv_build_log(
    venv_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Download a specific build log file"""
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    # Validate filename (prevent directory traversal)
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    log_file = Path(f"/tmp/thinkube-venvs/{venv.name}/{filename}")
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(log_file, media_type="text/plain", filename=filename)


@router.delete("/{venv_id}", operation_id="delete_jupyter_venv")
async def delete_jupyter_venv(
    venv_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Remove a custom venv from every node and forget it; answers at once.

    The venv lives on each node's own disk, so the removal runs as one short
    Job per node. The record says deleting until it is gone; a removal that
    fails leaves the record as delete_failed with the reason, and can be
    asked for again. Templates cannot be deleted; a venv that is building
    must finish first.
    """
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    if venv.is_template:
        raise HTTPException(status_code=400, detail="Cannot delete template venvs")

    if venv.status == "building":
        raise HTTPException(status_code=400, detail="Cannot delete venv while building")

    if venv.status == "deleting":
        raise HTTPException(status_code=400, detail="Venv is already being removed")

    venv.status = "deleting"
    venv.output = None
    db.commit()

    detached.start(f"venv-delete:{venv.id}", _execute_venv_delete(str(venv.id)))

    return {
        "message": f"Venv '{venv.name}' is being removed from every node; poll get_jupyter_venv until it is gone",
        "status": "deleting",
        "poll_url": f"/jupyter-venvs/{venv.id}",
    }


@router.put("/{venv_id}/packages", operation_id="update_venv_packages")
async def update_venv_packages(
    venv_id: UUID,
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth)
):
    """Update the package list for a venv (before building)"""
    venv = db.query(JupyterVenv).filter_by(id=venv_id).first()

    if not venv:
        raise HTTPException(status_code=404, detail="Venv not found")

    if venv.is_template:
        raise HTTPException(status_code=400, detail="Cannot modify template venvs")

    if venv.status == "building":
        raise HTTPException(status_code=400, detail="Cannot modify venv while building")

    packages = body.get("packages", [])
    if not packages:
        raise HTTPException(status_code=400, detail="Packages list cannot be empty")

    venv.packages = packages
    venv.status = "pending"  # Reset status since packages changed
    db.commit()

    return {"message": "Packages updated successfully", "packages": venv.packages}


# Background task for venv build
async def _execute_venv_build(venv_id: str) -> None:
    """Execute venv build using Ansible playbook.

    This runs as a background task and:
    1. Runs Ansible playbook that creates K8s Job on GPU node
    2. The Job builds venv to local hostPath
    3. After success, syncs to other GPU nodes via rsync
    4. Updates database with status
    """
    db = SessionLocal()()

    try:
        venv = db.query(JupyterVenv).filter_by(id=venv_id).first()
        if not venv:
            logger.error(f"Venv {venv_id} not found")
            return

        logger.info(f"Starting venv build for {venv.name}")

        try:
            result = await _run_ansible_build(venv)

            if result["success"]:
                venv.status = "success"
                venv.output = result.get("output", "Build completed successfully")
                venv.venv_path = (
                    f"/var/lib/jupyterhub-venvs/<arch>/{venv.name}"
                    if venv.is_template
                    else f"/var/lib/jupyterhub-venvs/custom/{venv.name}"
                )
                archs = result.get("architectures", [])
                venv.architectures_built = archs
                venv.architecture = archs[0] if archs else result.get("architecture", "unknown")
                logger.info(f"Venv build succeeded for {venv.name} (architectures: {archs})")
            else:
                venv.status = "failed"
                venv.output = result.get("error", "Build failed")
                logger.error(f"Venv build failed for {venv.name}: {result.get('error')}")

        except Exception as e:
            logger.error(f"Venv build error for {venv.name}: {e}")
            venv.status = "failed"
            venv.output = f"Build error: {str(e)}"

        finally:
            venv.completed_at = datetime.now(timezone.utc)
            db.commit()

    finally:
        db.close()


async def read_process_output(stream, log_file, scrub: Optional[Scrubber] = None) -> List[str]:
    """Copy a process's output to the log as it comes and return its lines.

    Read in chunks, not lines: a playbook step can answer with one line of
    any length (a sync that lists every file of a venv is megabytes), and a
    line reader with a limit raises on it and loses the build. Credentials
    are blanked line by line before anything is written.
    """
    lines: List[str] = []
    pending = ""

    def emit(line: str, end: str) -> None:
        line = scrub.clean(line) if scrub is not None else line
        lines.append(line.rstrip())
        log_file.write(line + end)

    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        pending += chunk.decode("utf-8", errors="replace")
        *complete, pending = pending.split("\n")
        for line in complete:
            emit(line, "\n")
        log_file.flush()
    if pending:
        emit(pending, "")
        log_file.flush()
    return lines


_ARCH_MARKER = re.compile(r"Architecture marker written: (amd64|arm64|[a-z0-9_]+)\b")


def built_architectures(output_lines: List[str]) -> List[str]:
    """The architectures a build wrote its marker for, from the playbook's output.

    Ansible's verbose output can carry a Job's whole log inside one line, so
    the marker is matched wherever it appears, not at a line's end.
    """
    found: List[str] = []
    for line in output_lines:
        for arch in _ARCH_MARKER.findall(line):
            if arch not in found:
                found.append(arch)
    return sorted(found)


async def _run_venv_playbook(venv, playbook: str, extra_vars: Dict[str, Any]) -> tuple[int, List[str], Path]:
    """Run one of the venv playbooks for ``venv`` and return (return code, output lines, log file)."""
    playbook_path = Path(f"/home/thinkube/thinkube-control/ansible/playbooks/{playbook}")
    if not playbook_path.exists():
        raise FileNotFoundError(f"Playbook not found: {playbook_path}")

    extra_vars = ansible_env.prepare_auth_vars(dict(extra_vars))
    extra_vars["kubeconfig"] = os.environ.get("KUBECONFIG", "/home/thinkube/.kube/config")
    domain_name = os.environ.get("DOMAIN_NAME", "cmxela.com")
    extra_vars["harbor_registry"] = f"registry.{domain_name}"

    temp_vars_fd, temp_vars_path = tempfile.mkstemp(suffix=".yml", prefix="venv-vars-")
    try:
        with os.fdopen(temp_vars_fd, "w") as f:
            yaml.dump(extra_vars, f)
    except:
        os.close(temp_vars_fd)
        raise

    cmd = [
        "ansible-playbook",
        "-i", str(ansible_env.get_inventory_path()),
        str(playbook_path),
        "-e", f"@{temp_vars_path}",
    ]
    env = ansible_env.get_environment(context="template")
    scrub = Scrubber.for_run(extra_vars, env)

    log_dir = Path("/tmp/thinkube-venvs") / venv.name
    log_dir.mkdir(parents=True, exist_ok=True)
    stem = playbook.split("_")[0]
    log_file = log_dir / f"{stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

    try:
        logger.info(f"Running: {' '.join(cmd)}")
        with open(log_file, "w") as f:
            f.write(f"=== VENV {stem.upper()} LOG ===\n")
            f.write(f"Venv: {venv.name}\n")
            f.write(f"Started: {datetime.now()}\n")
            f.write(f"\n=== ANSIBLE OUTPUT ===\n")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=1024 * 1024,
                env=env,
            )
            output_lines = await read_process_output(process.stdout, f, scrub)
            return_code = await process.wait()
            f.write(f"\n=== COMPLETED ===\n")
            f.write(f"Return code: {return_code}\n")
            f.write(f"Finished: {datetime.now()}\n")
        return return_code, output_lines, log_file
    finally:
        try:
            os.unlink(temp_vars_path)
        except:
            pass


async def _run_ansible_build(venv) -> Dict[str, Any]:
    """Build a venv on every architecture through build_venv.yaml.

    Returns:
        Dict with success, output/error, and architectures
    """
    # A template is the built-in venv: its build replaces the release's copy
    # in place on every node instead of adding a custom one.
    extra_vars = {
        "venv_name": venv.name,
        "packages": json.dumps(venv.packages),  # JSON string for Ansible
        "is_template": bool(venv.is_template),
    }
    try:
        return_code, output_lines, log_file = await _run_venv_playbook(venv, "build_venv.yaml", extra_vars)
    except Exception as e:
        return {"success": False, "error": str(e)}

    if return_code == 0:
        architectures = built_architectures(output_lines)
        return {
            "success": True,
            "output": f"Build completed. Log: {log_file}",
            "architectures": architectures,
            "architecture": architectures[0] if architectures else "unknown",
        }
    return {
        "success": False,
        "error": f"Build failed with return code {return_code}. Log: {log_file}",
    }


async def _execute_venv_delete(venv_id: str) -> None:
    """Remove a venv from every node through delete_venv.yaml, then forget the record."""
    db = SessionLocal()()
    try:
        venv = db.query(JupyterVenv).filter_by(id=venv_id).first()
        if not venv:
            logger.error(f"Venv {venv_id} not found")
            return
        try:
            return_code, _, log_file = await _run_venv_playbook(venv, "delete_venv.yaml", {"venv_name": venv.name})
        except Exception as e:
            return_code, log_file = 1, None
            reason = str(e)
        else:
            reason = f"Removal failed with return code {return_code}. Log: {log_file}"

        if return_code == 0:
            log_dir = Path(f"/tmp/thinkube-venvs/{venv.name}")
            if log_dir.exists():
                import shutil
                shutil.rmtree(log_dir, ignore_errors=True)
            db.delete(venv)
            db.commit()
            logger.info(f"Venv {venv.name} removed from every node and forgotten")
        else:
            venv.status = "delete_failed"
            venv.output = reason
            venv.completed_at = datetime.now(timezone.utc)
            db.commit()
            logger.error(f"Venv {venv.name} removal failed: {reason}")
    finally:
        db.close()
