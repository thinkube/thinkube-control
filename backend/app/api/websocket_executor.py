# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
WebSocket endpoint for streaming Ansible playbook execution
Adapted from installer for thinkube-control
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Any, Optional
import asyncio
import logging
import os
import json
import yaml
import tempfile
from pathlib import Path

from app.db.session import SessionLocal
from app.models.deployments import TemplateDeployment, DeploymentLog
from app.services.ansible_environment import ansible_env
from app.services.scrub import Scrubber

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ansible-stream"])

# Registry to track active deployment processes for cleanup on disconnect

# Maximum size for a single WebSocket message (64KB to be safe)
MAX_MESSAGE_SIZE = 64 * 1024


def chunk_large_text(text: str, max_size: int = MAX_MESSAGE_SIZE) -> list:
    """
    Split large text into chunks that fit within WebSocket message size limits.
    Preserves complete lines where possible.
    """
    if len(text.encode('utf-8')) <= max_size:
        return [text]
    
    chunks = []
    lines = text.split('\n')
    current_chunk = []
    current_size = 0
    
    for line in lines:
        line_size = len(line.encode('utf-8')) + 1  # +1 for newline
        
        # If a single line exceeds max size, split it
        if line_size > max_size:
            # Flush current chunk if it has content
            if current_chunk:
                chunks.append('\n'.join(current_chunk))
                current_chunk = []
                current_size = 0
            
            # Split the oversized line into smaller pieces
            line_bytes = line.encode('utf-8')
            for i in range(0, len(line_bytes), max_size - 100):  # Leave some buffer
                chunk_bytes = line_bytes[i:i + max_size - 100]
                chunks.append(chunk_bytes.decode('utf-8', errors='replace'))
        
        # If adding this line would exceed max size, start a new chunk
        elif current_size + line_size > max_size:
            chunks.append('\n'.join(current_chunk))
            current_chunk = [line]
            current_size = line_size
        else:
            current_chunk.append(line)
            current_size += line_size
    
    # Add any remaining content
    if current_chunk:
        chunks.append('\n'.join(current_chunk))
    
    return chunks


async def send_chunked_message(websocket: WebSocket, message_data: dict):
    """
    Send a message to WebSocket, chunking the 'message' field if it's too large.
    """
    # Check if the message field needs chunking
    if 'message' in message_data:
        message_text = message_data['message']
        # Estimate the JSON size
        test_json = json.dumps(message_data)
        
        if len(test_json.encode('utf-8')) > MAX_MESSAGE_SIZE:
            # Chunk the message field
            chunks = chunk_large_text(message_text)
            
            for i, chunk in enumerate(chunks):
                chunked_data = message_data.copy()
                chunked_data['message'] = chunk
                if len(chunks) > 1:
                    chunked_data['chunk'] = f"{i+1}/{len(chunks)}"
                await websocket.send_json(chunked_data)
        else:
            await websocket.send_json(message_data)
    else:
        await websocket.send_json(message_data)


@router.websocket("/ws/ansible/hello")
async def test_ansible_hello(websocket: WebSocket):
    """Test endpoint for hello-world playbook"""
    await websocket.accept()
    logger.info("WebSocket connection accepted for hello-world test")

    try:
        # Send initial connection message
        await websocket.send_json(
            {"type": "connected", "message": "Connected to ansible execution service"}
        )

        # Wait for parameters
        data = await websocket.receive_json()
        greeting = data.get("greeting", "Hello")
        target_name = data.get("target_name", "World")

        # Execute hello-world playbook
        await _execute_playbook(
            websocket,
            "test/hello-world.yaml",
            extra_vars={"greeting": greeting, "target_name": target_name},
        )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.send_json({"type": "error", "message": str(e)})


@router.websocket("/ws/ansible/execute")
async def stream_ansible_execution(websocket: WebSocket):
    """Stream Ansible playbook execution output via WebSocket"""
    await websocket.accept()
    logger.info("WebSocket connection accepted for ansible execution")

    try:
        # Receive execution parameters
        data = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
        playbook_name = data.get("playbook")
        extra_vars = data.get("extra_vars", {})
        environment = data.get("environment", {})

        if not playbook_name:
            await websocket.send_json(
                {"type": "error", "message": "No playbook specified"}
            )
            return

        await _execute_playbook(websocket, playbook_name, extra_vars, environment)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected during ansible execution")
    except Exception as e:
        logger.error(f"WebSocket error during ansible execution: {e}")
        await websocket.send_json({"type": "error", "message": str(e)})


@router.websocket("/ws/deployment/{deployment_id}")
async def stream_deployment_logs(websocket: WebSocket, deployment_id: str):
    """Stream logs from a deployment - used for real-time monitoring"""
    await websocket.accept()
    logger.info(f"WebSocket connection accepted for deployment {deployment_id}")

    try:
        await _stream_deployment_logs(websocket, deployment_id)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.send_json({"type": "error", "message": str(e)})


async def _execute_playbook(
    websocket: WebSocket,
    playbook_name: str,
    extra_vars: Optional[Dict[str, Any]] = None,
    environment: Optional[Dict[str, str]] = None,
):
    """Execute ansible playbook and stream output to websocket"""

    # Validate environment
    validation = ansible_env.validate_paths()
    if not validation["valid"]:
        error_msg = f"Environment validation failed: {'; '.join(validation['errors'])}"
        logger.error(error_msg)
        await websocket.send_json({"type": "error", "message": error_msg})
        return

    # Build playbook path
    if playbook_name.startswith("/"):
        # Absolute path provided
        playbook_path = Path(playbook_name)
    else:
        # Use shared environment to get path
        playbook_path = ansible_env.get_playbook_path(playbook_name)

    # Get inventory path
    inventory_path = ansible_env.get_inventory_path()

    logger.info(f"Executing playbook: {playbook_path}")
    logger.info(f"Inventory path: {inventory_path}")

    if not playbook_path.exists():
        await websocket.send_json(
            {"type": "error", "message": f"Playbook not found: {playbook_path}"}
        )
        return

    # Prepare variables with authentication
    if not extra_vars:
        extra_vars = {}

    try:
        extra_vars = ansible_env.prepare_auth_vars(extra_vars)
    except RuntimeError as e:
        await websocket.send_json({"type": "error", "message": str(e)})
        return

    # Create temp vars file
    temp_vars_fd, temp_vars_path = tempfile.mkstemp(
        suffix=".yml", prefix="ansible-vars-"
    )
    process = None

    try:
        with os.fdopen(temp_vars_fd, "w") as f:
            yaml.dump(extra_vars, f)
    except:
        os.close(temp_vars_fd)
        raise

    try:
        # Get command with buffering
        cmd = ansible_env.get_command_with_buffer(
            playbook_path, inventory_path, temp_vars_path
        )

        # Get shared environment
        env = ansible_env.get_environment()
        if environment:
            env.update(environment)
        scrub = Scrubber.for_run(extra_vars, env)

        # Send start message
        await websocket.send_json(
            {
                "type": "start",
                "message": "Starting playbook execution",
                "playbook": playbook_name,
            }
        )

        # Create subprocess
        logger.info(f"Running command: {' '.join(cmd)}")
        logger.info(f"ANSIBLE_ROLES_PATH: {env.get('ANSIBLE_ROLES_PATH', 'Not set')}")
        # Run from the playbook's directory
        working_dir = playbook_path.parent
        logger.info(f"Working directory: {working_dir}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=str(working_dir),
            limit=1024 * 1024,
        )

        # Stream output
        current_task = "Initializing"
        task_count = 0

        while True:
            line = await process.stdout.readline()
            if not line:
                break

            line_text = line.decode("utf-8", errors="replace").rstrip()
            if not line_text:
                continue
            line_text = scrub.clean(line_text)

            # Parse Ansible output
            if "TASK [" in line_text:
                task_start = line_text.find("TASK [") + 6
                task_end = line_text.find("]", task_start)
                if task_end > task_start:
                    current_task = line_text[task_start:task_end]
                    task_count += 1

                    await websocket.send_json(
                        {
                            "type": "task",
                            "task_number": task_count,
                            "task_name": current_task,
                            "message": line_text,
                        }
                    )
            elif "PLAY [" in line_text:
                await send_chunked_message(websocket, {"type": "play", "message": line_text})
            elif "ok: [" in line_text:
                await send_chunked_message(websocket,
                    {"type": "ok", "task": current_task, "message": line_text}
                )
            elif "changed: [" in line_text:
                await send_chunked_message(websocket,
                    {"type": "changed", "task": current_task, "message": line_text}
                )
            elif "failed: [" in line_text or "fatal: [" in line_text:
                await send_chunked_message(websocket,
                    {"type": "failed", "task": current_task, "message": line_text}
                )
            elif "skipping: [" in line_text:
                await send_chunked_message(websocket,
                    {"type": "skipped", "task": current_task, "message": line_text}
                )
            else:
                # Regular output
                await send_chunked_message(websocket, {"type": "output", "message": line_text})

        # Wait for completion
        return_code = await process.wait()

        # Send completion
        await websocket.send_json(
            {
                "type": "complete",
                "status": "success" if return_code == 0 else "error",
                "message": (
                    "Playbook completed" if return_code == 0 else "Playbook failed"
                ),
                "return_code": return_code,
            }
        )

    except Exception as e:
        logger.error(f"Error executing playbook: {e}")
        await websocket.send_json(
            {"type": "error", "message": f"Execution error: {str(e)}"}
        )
    finally:
        # Cleanup
        if process and process.returncode is None:
            process.terminate()
            await process.wait()

        if "temp_vars_path" in locals():
            try:
                os.unlink(temp_vars_path)
            except:
                pass


async def _stream_deployment_logs(websocket: WebSocket, deployment_id: str):
    """Stream logs from an existing deployment"""
    session_factory = SessionLocal()  # Get the sessionmaker
    db = session_factory()  # Create a session instance
    try:
        # Get deployment
        deployment = db.query(TemplateDeployment).filter_by(id=deployment_id).first()
        if not deployment:
            await websocket.send_json(
                {"type": "error", "message": f"Deployment {deployment_id} not found"}
            )
            return

        # Send initial status
        await websocket.send_json(
            {
                "type": "status",
                "message": f"Deployment status: {deployment.status}",
                "deployment": {
                    "id": str(deployment.id),
                    "name": deployment.name,
                    "status": deployment.status,
                    "created_at": (
                        deployment.created_at.isoformat()
                        if deployment.created_at
                        else None
                    ),
                    "started_at": (
                        deployment.started_at.isoformat()
                        if deployment.started_at
                        else None
                    ),
                    "completed_at": (
                        deployment.completed_at.isoformat()
                        if deployment.completed_at
                        else None
                    ),
                },
            }
        )

        # Stream existing logs
        logs = (
            db.query(DeploymentLog)
            .filter_by(deployment_id=deployment_id)
            .order_by(DeploymentLog.timestamp)
            .all()
        )

        for log in logs:
            await websocket.send_json(
                {
                    "type": log.type,
                    "message": log.message,
                    "task_name": log.task_name,
                    "task_number": log.task_number,
                    "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                }
            )
            # Small delay to avoid overwhelming the client
            await asyncio.sleep(0.01)

        # If deployment is still running, wait for new logs
        if deployment.status in ["queued", "pending", "running"]:
            await websocket.send_json(
                {"type": "info", "message": "Waiting for new logs..."}
            )

            # Poll for new logs
            last_log_id = logs[-1].id if logs else None
            while deployment.status in ["queued", "pending", "running"]:
                # Get new logs
                query = db.query(DeploymentLog).filter_by(deployment_id=deployment_id)
                if last_log_id:
                    query = query.filter(DeploymentLog.id > last_log_id)

                new_logs = query.order_by(DeploymentLog.timestamp).all()

                for log in new_logs:
                    await websocket.send_json(
                        {
                            "type": log.type,
                            "message": log.message,
                            "task_name": log.task_name,
                            "task_number": log.task_number,
                            "timestamp": (
                                log.timestamp.isoformat() if log.timestamp else None
                            ),
                        }
                    )
                    last_log_id = log.id

                # Wait before checking again
                await asyncio.sleep(1)

                # Refresh deployment status
                db.refresh(deployment)

        # Send final status
        await websocket.send_json(
            {
                "type": "complete",
                "status": deployment.status,
                "message": f"Deployment {deployment.status}",
                "output": deployment.output,
            }
        )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected while streaming logs")
    except Exception as e:
        logger.error(f"Error streaming deployment logs: {e}")
        await websocket.send_json(
            {"type": "error", "message": f"Error streaming logs: {str(e)}"}
        )
    finally:
        db.close()
