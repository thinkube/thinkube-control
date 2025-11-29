"""
API endpoints for managing optional Thinkube components
"""

from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect
from typing import List, Dict, Any, Optional
from uuid import uuid4
from datetime import datetime
import logging
import os

from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.api_tokens import get_current_user_dual_auth
from app.services.optional_components import OptionalComponentService
from app.services.background_executor import background_executor
from app.models.deployments import TemplateDeployment
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["optional-components"])


class ComponentInstallRequest(BaseModel):
    """Request model for component installation"""
    parameters: Optional[Dict[str, Any]] = {}
    force: Optional[bool] = False


class ComponentResponse(BaseModel):
    """Response model for component information"""
    name: str
    display_name: str
    description: str
    category: str
    icon: str
    installed: bool
    requirements_met: bool
    missing_requirements: List[str]
    estimated_time: int
    

class ComponentListResponse(BaseModel):
    """Response model for component list"""
    components: List[ComponentResponse]
    

class InstallResponse(BaseModel):
    """Response model for installation request"""
    deployment_id: str
    component: str
    status: str
    message: str
    websocket_url: str


@router.get("/list", response_model=ComponentListResponse, operation_id="list_optional_components")
async def list_optional_components(
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    List all available optional components with their installation status
    """
    try:
        service = OptionalComponentService(db)
        components = service.list_components()
        
        # Convert to response model
        component_responses = []
        for comp in components:
            component_responses.append(ComponentResponse(
                name=comp["name"],
                display_name=comp["display_name"],
                description=comp["description"],
                category=comp["category"],
                icon=comp["icon"],
                installed=comp["installed"],
                requirements_met=comp["requirements_met"],
                missing_requirements=comp["missing_requirements"],
                estimated_time=comp.get("estimated_time", 10)
            ))
        
        return ComponentListResponse(components=component_responses)
        
    except Exception as e:
        logger.error(f"Failed to list optional components: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list optional components: {str(e)}"
        )


@router.get("/{component}/info", operation_id="get_component_info")
async def get_component_info(
    component: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Get detailed information about a specific optional component
    """
    try:
        service = OptionalComponentService(db)
        component_info = service.get_component(component)
        
        if not component_info:
            raise HTTPException(
                status_code=404,
                detail=f"Component '{component}' not found"
            )
        
        return component_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get component info: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get component info: {str(e)}"
        )


@router.post("/{component}/install", response_model=InstallResponse, operation_id="install_optional_component")
async def install_optional_component(
    component: str,
    request: ComponentInstallRequest,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Install an optional component
    
    This endpoint queues the installation and returns immediately with a deployment ID.
    Use the WebSocket endpoint to monitor installation progress.
    """
    try:
        service = OptionalComponentService(db)
        
        # Validate component exists
        component_info = service.get_component(component)
        if not component_info:
            raise HTTPException(
                status_code=404,
                detail=f"Component '{component}' not found"
            )
        
        # Validate installation
        validation = service.validate_installation(component)
        if not validation["valid"]:
            if request.force:
                logger.warning(f"Force installing component {component}: {validation.get('error')}")
            else:
                raise HTTPException(
                    status_code=400,
                    detail=validation.get("error", "Component cannot be installed")
                )
        
        # Get playbook path
        playbook_path = service.get_playbook_path(component, "install")
        if not playbook_path:
            raise HTTPException(
                status_code=500,
                detail=f"Installation playbook not found for component '{component}'"
            )
        
        # Create deployment record (reusing TemplateDeployment model)
        deployment = TemplateDeployment(
            id=uuid4(),
            name=f"optional-{component}",
            template_url=f"optional://{component}",  # Special URL format for optional components
            status="pending",
            variables={
                "component": component,
                "parameters": request.parameters,
                "playbook": playbook_path
            },
            created_by=current_user.get("preferred_username", "unknown")
        )
        db.add(deployment)
        db.commit()
        
        # Don't start background task - execution happens in WebSocket connection
        # Just like templates!
        
        return InstallResponse(
            deployment_id=str(deployment.id),
            component=component,
            status="queued",
            message=f"Installation of {component_info['display_name']} has been queued",
            websocket_url=f"/ws/optional/{component}/install/{deployment.id}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to install component {component}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to install component: {str(e)}"
        )


@router.delete("/{component}", operation_id="uninstall_optional_component")
async def uninstall_optional_component(
    component: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Uninstall an optional component
    """
    try:
        service = OptionalComponentService(db)
        
        # Validate component exists
        component_info = service.get_component(component)
        if not component_info:
            raise HTTPException(
                status_code=404,
                detail=f"Component '{component}' not found"
            )
        
        # Check if installed
        if not component_info["installed"]:
            raise HTTPException(
                status_code=400,
                detail=f"Component '{component}' is not installed"
            )
        
        # Get uninstall playbook path
        playbook_path = service.get_playbook_path(component, "uninstall")
        if not playbook_path:
            raise HTTPException(
                status_code=500,
                detail=f"Uninstall playbook not found for component '{component}'"
            )
        
        # Create deployment record for uninstallation
        deployment = TemplateDeployment(
            id=uuid4(),
            name=f"uninstall-{component}",
            template_url=f"optional://{component}/uninstall",
            status="pending",
            variables={
                "component": component,
                "action": "uninstall",
                "playbook": playbook_path
            },
            created_by=current_user.get("preferred_username", "unknown")
        )
        db.add(deployment)
        db.commit()

        # Don't start background task - execution happens in WebSocket connection
        # Just like installation and templates!

        return {
            "deployment_id": str(deployment.id),
            "component": component,
            "status": "queued",
            "message": f"Uninstallation of {component_info['display_name']} has been queued",
            "websocket_url": f"/ws/optional/{component}/uninstall/{deployment.id}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to uninstall component {component}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to uninstall component: {str(e)}"
        )


@router.get("/{component}/status", operation_id="get_component_status")
async def get_component_status(
    component: str,
    current_user: dict = Depends(get_current_user_dual_auth),
    db: Session = Depends(get_db)
):
    """
    Get the current status of an optional component
    """
    try:
        service = OptionalComponentService(db)
        
        # Validate component exists
        component_info = service.get_component(component)
        if not component_info:
            raise HTTPException(
                status_code=404,
                detail=f"Component '{component}' not found"
            )
        
        return component_info["status"]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get component status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get component status: {str(e)}"
        )


async def _execute_component_installation(
    deployment_id: str,
    component: str,
    playbook_path: str,
    parameters: Dict[str, Any]
):
    """
    Execute component installation in background
    
    Args:
        deployment_id: Deployment ID for tracking
        component: Component name
        playbook_path: Path to installation playbook
        parameters: Installation parameters
    """
    import asyncio
    import os
    import yaml
    import tempfile
    from pathlib import Path
    from app.db.session import SessionLocal
    from app.models.deployments import DeploymentLog
    from app.services.ansible_environment import ansible_env
    
    db = SessionLocal()()
    
    try:
        logger.info(f"Starting installation of component {component} (deployment {deployment_id})")
        
        # Update deployment status
        deployment = db.query(TemplateDeployment).filter_by(id=deployment_id).first()
        if deployment:
            deployment.status = "running"
            deployment.started_at = datetime.utcnow()
            db.commit()
        
        # Build the full playbook path using the mounted thinkube-platform repo
        # The playbook path from service is like: ansible/40_thinkube/optional/qdrant/00_install.yaml
        full_playbook_path = Path("/home/thinkube/thinkube-platform/thinkube") / playbook_path

        # Inventory is in shared location
        inventory_path = Path("/home/thinkube/.ansible/inventory/inventory.yaml")
        
        # Create temporary vars file
        temp_vars_fd, temp_vars_file = tempfile.mkstemp(suffix='.yml', prefix='component-vars-')
        try:
            # Prepare vars with authentication (like templates do)
            extra_vars = ansible_env.prepare_auth_vars(parameters or {})
            with os.fdopen(temp_vars_fd, 'w') as f:
                yaml.dump(extra_vars, f)
        except:
            os.close(temp_vars_fd)
            raise
        
        # Get command with buffering (same as templates)
        cmd = ansible_env.get_command_with_buffer(
            full_playbook_path, inventory_path, Path(temp_vars_file)
        )
        
        # Get environment variables for optional components
        env = ansible_env.get_environment(context="optional")
        
        # Execute the playbook
        logger.info(f"Executing ansible command: {' '.join(cmd)}")
        
        # Stream output and save to logs - merge stdout and stderr like templates do
        task_count = 0
        
        # Combine stderr with stdout for proper ansible output streaming
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout like templates
            env=env,
            cwd="/home/thinkube/thinkube-platform/thinkube"  # Use the thinkube repo root as working directory
        )
        
        # Stream output line by line
        while True:
            line = await process.stdout.readline()
            if not line:
                break
                
            line_text = line.decode('utf-8').strip()
            if not line_text:
                continue
            
            # Parse ansible output and save to database
            log_type = "output"
            task_name = None
            
            if "TASK [" in line_text:
                log_type = "task"
                task_count += 1
                # Extract task name
                try:
                    task_name = line_text.split("TASK [")[1].split("]")[0]
                except:
                    task_name = line_text
            elif "ok:" in line_text:
                log_type = "ok"
            elif "changed:" in line_text:
                log_type = "changed"
            elif "failed:" in line_text or "FAILED" in line_text or "fatal:" in line_text:
                log_type = "failed"
            elif "PLAY [" in line_text:
                log_type = "play"
            elif "ERROR" in line_text or "error" in line_text:
                log_type = "error"
            elif "skipping:" in line_text:
                log_type = "skipped"
            
            # Save log to database
            log = DeploymentLog(
                deployment_id=deployment_id,
                type=log_type,
                message=line_text,
                task_name=task_name,
                task_number=task_count if log_type == "task" else None
            )
            db.add(log)
            # Commit immediately to make log available to WebSocket
            try:
                db.commit()
                logger.debug(f"Saved log to DB: {log_type} - {line_text[:50]}...")
            except Exception as e:
                logger.error(f"Failed to save log: {e}")
                db.rollback()
            
            logger.info(f"[{component}] {line_text}")
        
        # Wait for process to complete
        await process.wait()
        
        # Update deployment status
        if deployment:
            deployment.status = "success" if process.returncode == 0 else "failed"
            deployment.completed_at = datetime.utcnow()
            db.commit()
        
        # Save final log
        final_log = DeploymentLog(
            deployment_id=deployment_id,
            type="complete",
            message=f"{component} installation {'completed successfully' if process.returncode == 0 else 'failed'}",
            task_name=None,
            task_number=None
        )
        db.add(final_log)
        db.commit()
        
        logger.info(f"Installation of component {component} completed with return code {process.returncode}")
        
        # Clean up temp file
        if temp_vars_file and os.path.exists(temp_vars_file):
            os.unlink(temp_vars_file)
        
    except Exception as e:
        logger.error(f"Failed to execute component installation: {e}")
        
        # Update deployment status to failed
        if deployment:
            deployment.status = "failed"
            deployment.completed_at = datetime.utcnow()
            db.commit()
        
        # Save error log
        error_log = DeploymentLog(
            deployment_id=deployment_id,
            type="error",
            message=str(e),
            task_name=None,
            task_number=None
        )
        db.add(error_log)
        db.commit()
    finally:
        db.close()


