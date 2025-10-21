"""
WebSocket endpoint for Harbor image mirroring
Provides real-time progress for image mirroring operations
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Any
import asyncio
import logging
import os
import yaml
import tempfile
import re
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.deployments import TemplateDeployment, DeploymentLog
from app.models.container_images import ContainerImage, ImageMirrorJob
from app.services.ansible_environment import ansible_env

logger = logging.getLogger(__name__)
router = APIRouter(tags=["harbor-websocket"])


@router.websocket("/ws/harbor/mirror/{deployment_id}")
async def stream_image_mirror_deployment(websocket: WebSocket, deployment_id: str):
    """
    Stream image mirroring deployment via WebSocket
    Executes the mirror-image.yaml playbook and streams real-time output
    """
    await websocket.accept()
    logger.info(f"WebSocket connection accepted for image mirror deployment {deployment_id}")

    session_factory = SessionLocal()
    db = session_factory()

    try:
        # Get deployment record
        deployment = db.query(TemplateDeployment).filter_by(id=deployment_id).first()
        if not deployment:
            await websocket.send_json({
                "type": "error",
                "message": f"Deployment {deployment_id} not found"
            })
            await websocket.close()
            return

        # Check if already running or completed
        if deployment.status not in ["pending"]:
            await websocket.send_json({
                "type": "warning",
                "message": f"Deployment is already {deployment.status}"
            })

            # Send existing logs if any
            existing_logs = db.query(DeploymentLog).filter_by(
                deployment_id=deployment_id
            ).order_by(DeploymentLog.timestamp).all()

            for log in existing_logs:
                await websocket.send_json({
                    "type": log.type,
                    "message": log.message,
                    "task_name": log.task_name,
                    "timestamp": log.timestamp.isoformat()
                })

            await websocket.send_json({
                "type": "complete",
                "status": deployment.status,
                "message": f"Deployment {deployment.status}"
            })
            return

        # Update deployment status
        deployment.status = "running"
        deployment.started_at = datetime.now(timezone.utc)
        db.commit()

        await websocket.send_json({
            "type": "info",
            "message": "Starting image mirror deployment..."
        })

        # Validate paths
        validation = ansible_env.validate_paths()
        if not validation["valid"]:
            error_msg = f"Path validation failed: {', '.join(validation['errors'])}"
            await websocket.send_json({"type": "error", "message": error_msg})
            deployment.status = "failed"
            deployment.output = error_msg
            db.commit()
            return

        # Prepare Ansible execution
        playbook_path = ansible_env.get_playbook_path("mirror-image.yaml")
        inventory_path = ansible_env.get_inventory_path()

        # Extract variables from deployment
        vars_data = deployment.variables or {}

        # Add authentication variables
        extra_vars = ansible_env.prepare_auth_vars(vars_data)

        # Add domain_name from environment or config
        domain_name = os.environ.get("DOMAIN_NAME", "thinkube.com")
        extra_vars["domain_name"] = domain_name
        extra_vars["harbor_registry"] = f"registry.{domain_name}"

        # Create temporary vars file
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.yml', delete=False
        ) as temp_vars_file:
            yaml.dump(extra_vars, temp_vars_file)
            temp_vars_path = temp_vars_file.name

        try:
            # Build ansible-playbook command
            cmd = ansible_env.get_command_with_buffer(
                playbook_path, inventory_path, temp_vars_path
            )

            # Get environment for optional context (uses thinkube-platform roles)
            env = ansible_env.get_environment(context="optional")

            # Execute playbook and stream output
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env
            )

            task_pattern = r'TASK \[(.*?)\]'

            output_lines = []
            current_task = None

            while True:
                try:
                    line = await asyncio.wait_for(
                        process.stdout.readline(), timeout=0.1
                    )
                except asyncio.TimeoutError:
                    # Check for client messages
                    continue

                if not line:
                    break

                line_text = line.decode('utf-8', errors='ignore')
                output_lines.append(line_text)

                # Parse task names
                task_match = re.search(task_pattern, line_text)
                if task_match:
                    current_task = task_match.group(1)
                    log_entry = DeploymentLog(
                        deployment_id=deployment_id,
                        timestamp=datetime.now(timezone.utc),
                        type="task",
                        message=line_text.strip(),
                        task_name=current_task
                    )
                    db.add(log_entry)
                    db.commit()

                    await websocket.send_json({
                        "type": "task",
                        "task_name": current_task,
                        "message": line_text.strip()
                    })
                elif "fatal:" in line_text.lower() or "error:" in line_text.lower():
                    log_entry = DeploymentLog(
                        deployment_id=deployment_id,
                        timestamp=datetime.now(timezone.utc),
                        type="error",
                        message=line_text.strip(),
                        task_name=current_task
                    )
                    db.add(log_entry)
                    db.commit()

                    await websocket.send_json({
                        "type": "error",
                        "message": line_text.strip()
                    })
                elif "changed:" in line_text or "ok:" in line_text:
                    await websocket.send_json({
                        "type": "output",
                        "message": line_text.strip()
                    })
                elif line_text.strip():
                    await websocket.send_json({
                        "type": "output",
                        "message": line_text.strip()
                    })

            # Wait for process to complete
            return_code = await process.wait()
            full_output = ''.join(output_lines)

            if return_code == 0:
                deployment.status = "success"
                deployment.output = full_output

                # Extract digest from output if available
                # Look for patterns like "Digest: sha256:xxxxx" or "digest: sha256:xxxxx"
                digest_pattern = r'[Dd]igest:\s*(sha256:[a-f0-9]{64})'
                digest_match = re.search(digest_pattern, full_output)
                extracted_digest = digest_match.group(1) if digest_match else None

                # Create image record on success
                image_id = vars_data.get("image_id")
                job_id = vars_data.get("job_id")

                if image_id:
                    # Update image status and digest
                    image = db.query(ContainerImage).filter_by(id=image_id).first()
                    if image:
                        if image.image_metadata is None:
                            image.image_metadata = {}
                        image.image_metadata["status"] = "active"

                        # Update digest if we extracted it
                        if extracted_digest:
                            image.digest = extracted_digest
                            logger.info(f"Updated image {image_id} with digest: {extracted_digest}")

                        # Update last_synced for re-mirror operations
                        if vars_data.get("is_remirror"):
                            image.last_synced = datetime.now(timezone.utc)
                            image.mirror_date = datetime.now(timezone.utc)

                        db.commit()

                if job_id:
                    # Update job status
                    job = db.query(ImageMirrorJob).filter_by(id=job_id).first()
                    if job:
                        job.status = "success"
                        job.completed_at = datetime.now(timezone.utc)
                        db.commit()

                await websocket.send_json({
                    "type": "success",
                    "message": "Image mirrored successfully!"
                })
            else:
                deployment.status = "failed"
                deployment.output = full_output

                # Delete image record on failure
                image_id = vars_data.get("image_id")
                if image_id:
                    image = db.query(ContainerImage).filter_by(id=image_id).first()
                    if image:
                        logger.warning(f"Deleting image {image_id} due to failed mirroring")
                        db.delete(image)
                        db.commit()

                # Update job status
                job_id = vars_data.get("job_id")
                if job_id:
                    job = db.query(ImageMirrorJob).filter_by(id=job_id).first()
                    if job:
                        job.status = "failed"
                        job.error_message = "Mirroring failed"
                        job.completed_at = datetime.now(timezone.utc)
                        db.commit()

                await websocket.send_json({
                    "type": "error",
                    "message": f"Mirroring failed with exit code {return_code}"
                })

            deployment.completed_at = datetime.now(timezone.utc)
            db.commit()

        finally:
            # Clean up temp file
            if os.path.exists(temp_vars_path):
                os.unlink(temp_vars_path)

        await websocket.send_json({
            "type": "complete",
            "status": deployment.status,
            "message": f"Deployment {deployment.status}"
        })

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for deployment {deployment_id}")
        if deployment and deployment.status == "running":
            deployment.status = "cancelled"
            deployment.output = "WebSocket connection lost"
            db.commit()
    except Exception as e:
        logger.error(f"Error in image mirror WebSocket: {e}")
        if deployment:
            deployment.status = "failed"
            deployment.output = str(e)
            db.commit()
        await websocket.send_json({
            "type": "error",
            "message": f"Deployment failed: {str(e)}"
        })
    finally:
        db.close()
        await websocket.close()