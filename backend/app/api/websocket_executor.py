# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
WebSocket endpoint for streaming Ansible playbook execution
Adapted from installer for thinkube-control
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Any, Optional, Set
import asyncio
import logging
import os
import json
import yaml
import tempfile
from pathlib import Path
from datetime import datetime
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.deployments import TemplateDeployment, DeploymentLog
from app.services.ansible_environment import ansible_env
from app.core.config import settings

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


@router.websocket("/ws/custom-images/build/{build_id}")
async def stream_custom_image_build(websocket: WebSocket, build_id: str):
    """
    Stream custom image build execution via WebSocket

    This endpoint executes the custom image build directly and streams
    real-time output to the client - exactly like template deployment.
    """
    print(f"DEBUG: Custom image WebSocket endpoint called for build {build_id}", flush=True)
    logger.info(f"WebSocket endpoint called for custom image build {build_id}")
    await websocket.accept()
    logger.info(f"WebSocket connection accepted for custom image build {build_id}")

    from app.models.custom_images import CustomImageBuild

    session_factory = SessionLocal()
    db = session_factory()

    try:
        # Get build record
        build = db.query(CustomImageBuild).filter_by(id=build_id).first()
        if not build:
            await websocket.send_json({
                "type": "error",
                "message": f"Build {build_id} not found"
            })
            return

        # Check if already running or completed
        if build.status != "pending":
            await websocket.send_json({
                "type": "error",
                "message": f"Build is already {build.status}. Cannot restart."
            })
            return

        # Update status to building
        build.status = "building"
        build.started_at = datetime.utcnow()
        db.commit()

        # Send start message
        await websocket.send_json({
            "type": "start",
            "message": f"Starting build for {build.name}",
            "build": {
                "id": str(build.id),
                "name": build.name,
                "status": "building"
            }
        })

        try:
            # Execute the build - using subprocess like templates do
            result = await _execute_custom_image_build(websocket, build, db)

            # Update build status based on result
            if result["return_code"] == 0:
                build.status = "success"
                build.output = result.get("log_file", "Build completed successfully")  # Store log file path
                build.registry_url = result.get("registry_url")

                # If marked as base, generate a minimal template for inheritance
                if build.is_base and build.registry_url:
                    # Extract the image reference without the registry domain
                    # registry.thinkube.com/library/jp-cmxela:latest -> library/jp-cmxela:latest
                    image_ref = build.registry_url.split('/', 1)[1] if '/' in build.registry_url else build.name

                    build.template = f"""FROM {image_ref}

# Extended from {build.name}
# Add your customizations here

"""

                await websocket.send_json({
                    "type": "status",
                    "status": "completed",
                    "message": "Build completed successfully",
                    "registry_url": result.get("registry_url"),
                    "log_file": result.get("log_file")
                })
            else:
                build.status = "failed"
                build.output = result.get("log_file", f"Build failed with return code: {result['return_code']}")  # Store log file path

                await websocket.send_json({
                    "type": "status",
                    "status": "failed",
                    "message": f"Build failed with return code: {result['return_code']}",
                    "log_file": result.get("log_file")
                })

        except asyncio.CancelledError:
            build.status = "cancelled"
            build.output = "Build was cancelled"
            await websocket.send_json({
                "type": "cancelled",
                "message": "Build was cancelled"
            })
        except Exception as e:
            logger.error(f"Build execution failed: {e}")
            build.status = "failed"
            build.output = f"Build failed: {str(e)}"
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
        finally:
            build.completed_at = datetime.utcnow()
            db.commit()

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected during build {build_id}")
        # Update build status if still running
        if build and build.status == "building":
            build.status = "failed"
            build.output = "Build interrupted - WebSocket disconnected"
            build.completed_at = datetime.utcnow()
            db.commit()
    except Exception as e:
        logger.error(f"WebSocket error during custom image build: {e}")
        await websocket.send_json({"type": "error", "message": str(e)})
    finally:
        db.close()


async def _execute_custom_image_build(
    websocket: WebSocket,
    build,  # CustomImageBuild model
    db: Session
) -> Dict[str, Any]:
    """
    Execute custom image build - exactly like templates execute ansible

    Uses subprocess to run podman build and stream output in real-time
    SAVES LOGS TO FILES exactly like templates do
    """
    from pathlib import Path
    import os
    from datetime import datetime

    domain = settings.DOMAIN_NAME
    # Use 'library' project which exists by default in Harbor
    registry_url = f"registry.{domain}/library/{build.name}:latest"

    # Create log directory - EXACTLY like templates use /tmp/thinkube-deployments
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_base_dir = Path("/tmp/thinkube-builds")
    app_log_dir = log_base_dir / build.name
    app_log_dir.mkdir(parents=True, exist_ok=True)

    debug_log_file = app_log_dir / f"build-{timestamp}.log"

    # Dockerfile path
    dockerfile_path = Path(build.dockerfile_path)
    if not dockerfile_path.exists():
        await websocket.send_json({
            "type": "error",
            "message": f"Dockerfile not found: {dockerfile_path}"
        })
        return {"return_code": 1, "log_file": str(debug_log_file)}

    # Build context directory (parent of Dockerfile)
    context_dir = dockerfile_path.parent

    # Prepare build command
    cmd = [
        "podman", "build",
        "-t", registry_url,
        "-f", str(dockerfile_path),
        str(context_dir)
    ]

    # Add build args if provided
    if build.build_config and "build_args" in build.build_config:
        for key, value in build.build_config["build_args"].items():
            cmd.extend(["--build-arg", f"{key}={value}"])

    process = None
    debug_file = None

    try:
        # Log execution
        logger.info(f"Executing podman build: {' '.join(cmd)}")

        # Open debug log file - EXACTLY like templates do
        debug_file = open(debug_log_file, "w")
        debug_file.write(f"=== THINKUBE BUILD LOG ===\n")
        debug_file.write(f"Build ID: {build.id}\n")
        debug_file.write(f"Image: {build.name}\n")
        debug_file.write(f"Started at: {datetime.now()}\n")
        debug_file.write(f"Command: {' '.join(cmd)}\n")
        debug_file.write(f"\n=== BUILD OUTPUT ===\n")

        # Create subprocess - exactly like templates do with ansible
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "BUILDAH_FORMAT": "docker"},
            limit=1024 * 1024,
        )

        # Stream output line by line - exactly like templates
        while True:
            line = await process.stdout.readline()
            if not line:
                break

            line_text = line.decode('utf-8', errors='ignore').rstrip()

            # Write to debug file - EXACTLY like templates
            if debug_file:
                debug_file.write(f"{line_text}\n")
                debug_file.flush()

            # Send output to WebSocket
            await websocket.send_json({
                "type": "log",
                "message": line_text
            })

        # Wait for completion
        return_code = await process.wait()

        if return_code == 0:
            # Login to registry first
            registry_host = f"registry.{domain}"
            username = os.environ.get("HARBOR_USERNAME", "admin")
            password = os.environ.get("HARBOR_PASSWORD", os.environ.get("ADMIN_PASSWORD", ""))

            await websocket.send_json({
                "type": "log",
                "message": f"\nLogging into registry: {registry_host}"
            })

            login_cmd = ["podman", "login", registry_host,
                        "-u", username,
                        "-p", password]  # Using Let's Encrypt certificates

            login_process = await asyncio.create_subprocess_exec(
                *login_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=1024 * 1024,
            )

            # Write login header to file
            if debug_file:
                debug_file.write(f"\n\nLogging into registry: {registry_host}\n")
                debug_file.flush()

            # Capture login output but don't display password
            while True:
                line = await login_process.stdout.readline()
                if not line:
                    break
                line_text = line.decode('utf-8', errors='ignore').rstrip()
                # Don't log lines that might contain passwords
                if "password" not in line_text.lower() and debug_file:
                    debug_file.write(f"{line_text}\n")
                    debug_file.flush()

            login_return = await login_process.wait()

            if login_return != 0:
                if debug_file:
                    debug_file.write(f"\n\n=== LOGIN FAILED ===\n")
                    debug_file.write(f"Return code: {login_return}\n")
                await websocket.send_json({
                    "type": "error",
                    "message": "Failed to login to registry"
                })
                return {"return_code": login_return, "log_file": str(debug_log_file)}

            await websocket.send_json({
                "type": "log",
                "message": "Registry login successful"
            })

            # Push to registry
            push_cmd = ["podman", "push", registry_url]  # No --tls-verify=false for Let's Encrypt
            logger.info(f"Pushing image: {registry_url}")

            await websocket.send_json({
                "type": "log",
                "message": f"Pushing image to registry: {registry_url}"
            })

            push_process = await asyncio.create_subprocess_exec(
                *push_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=1024 * 1024,
            )

            # Write push header to file
            if debug_file:
                debug_file.write(f"\nPushing image to registry: {registry_url}\n")
                debug_file.flush()

            while True:
                line = await push_process.stdout.readline()
                if not line:
                    break

                line_text = line.decode('utf-8', errors='ignore').rstrip()

                # Write to debug file
                if debug_file:
                    debug_file.write(f"{line_text}\n")
                    debug_file.flush()

                await websocket.send_json({
                    "type": "log",
                    "message": line_text
                })

            push_return = await push_process.wait()

            if push_return != 0:
                if debug_file:
                    debug_file.write(f"\nPush failed with return code: {push_return}\n")
                return {"return_code": push_return, "log_file": str(debug_log_file)}

            # Success - write completion to file
            if debug_file:
                debug_file.write(f"\n\n=== BUILD COMPLETED SUCCESSFULLY ===\n")
                debug_file.write(f"Image available at: {registry_url}\n")
                debug_file.write(f"Finished at: {datetime.now()}\n")

            return {"return_code": 0, "registry_url": registry_url, "log_file": str(debug_log_file)}
        else:
            # Build failed
            if debug_file:
                debug_file.write(f"\n\n=== BUILD FAILED ===\n")
                debug_file.write(f"Return code: {return_code}\n")
                debug_file.write(f"Failed at: {datetime.now()}\n")
            return {"return_code": return_code, "log_file": str(debug_log_file)}

    except Exception as e:
        logger.error(f"Error executing build: {e}")
        if debug_file:
            debug_file.write(f"\n\n=== EXECUTION ERROR ===\n")
            debug_file.write(f"Error: {str(e)}\n")
        await websocket.send_json({
            "type": "error",
            "message": f"Execution error: {str(e)}"
        })
        return {"return_code": 1, "log_file": str(debug_log_file)}
    finally:
        # Cleanup
        if debug_file:
            debug_file.close()
        if process and process.returncode is None:
            process.terminate()
            await process.wait()


@router.websocket("/ws/jupyter-venvs/build/{build_id}")
async def stream_jupyter_venv_build(websocket: WebSocket, build_id: str):
    """
    Stream Jupyter venv build execution via WebSocket

    This endpoint executes the venv build directly and streams
    real-time output to the client.
    """
    logger.info(f"WebSocket endpoint called for venv build {build_id}")
    await websocket.accept()
    logger.info(f"WebSocket connection accepted for venv build {build_id}")

    from app.models.jupyter_venvs import JupyterVenv

    session_factory = SessionLocal()
    db = session_factory()

    try:
        # Get venv record
        venv = db.query(JupyterVenv).filter_by(id=build_id).first()
        if not venv:
            await websocket.send_json({
                "type": "error",
                "message": f"Venv {build_id} not found"
            })
            return

        # Only block if currently building
        if venv.status == "building":
            await websocket.send_json({
                "type": "error",
                "message": "Venv is currently building. Please wait for it to complete."
            })
            return

        # Update status to building
        venv.status = "building"
        venv.started_at = datetime.utcnow()
        db.commit()

        # Send start message
        await websocket.send_json({
            "type": "start",
            "message": f"Starting build for venv: {venv.name}",
            "venv": {
                "id": str(venv.id),
                "name": venv.name,
                "status": "building"
            }
        })

        try:
            # Execute the build
            result = await _execute_jupyter_venv_build(websocket, venv, db)

            # Update venv status based on result
            if result["return_code"] == 0:
                venv.status = "success"
                venv.output = "Build completed successfully"
                venv.venv_path = result.get("venv_path")
                archs = result.get("architectures", [])
                venv.architectures_built = archs
                venv.architecture = archs[0] if archs else "unknown"
            else:
                venv.status = "failed"
                venv.output = f"Build failed with return code: {result['return_code']}"

        except asyncio.CancelledError:
            venv.status = "cancelled"
            venv.output = "Build was cancelled"
            await websocket.send_json({
                "type": "cancelled",
                "message": "Build was cancelled"
            })
        except Exception as e:
            logger.error(f"Venv build execution failed: {e}")
            venv.status = "failed"
            venv.output = f"Build failed: {str(e)}"
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
        finally:
            venv.completed_at = datetime.utcnow()
            db.commit()

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected during venv build {build_id}")
        if venv and venv.status == "building":
            venv.status = "failed"
            venv.output = "Build interrupted - WebSocket disconnected"
            venv.completed_at = datetime.utcnow()
            db.commit()
    except Exception as e:
        logger.error(f"WebSocket error during venv build: {e}")
        await websocket.send_json({"type": "error", "message": str(e)})
    finally:
        db.close()


async def _execute_jupyter_venv_build(
    websocket: WebSocket,
    venv,  # JupyterVenv model
    db: Session
) -> Dict[str, Any]:
    """
    Execute Jupyter venv build via Ansible playbook.

    Runs build_venv.yaml which creates K8s Jobs on GPU nodes for each
    architecture in the cluster. Streams Ansible output in real-time.
    """
    from app.services.ansible_environment import ansible_env
    import re

    playbook_path = Path("/home/thinkube/thinkube-control/ansible/playbooks/build_venv.yaml")

    if not playbook_path.exists():
        await websocket.send_json({
            "type": "error",
            "message": f"Playbook not found: {playbook_path}"
        })
        return {"return_code": 1}

    extra_vars = {
        "venv_name": venv.name,
        "packages": json.dumps(venv.packages),
    }
    extra_vars = ansible_env.prepare_auth_vars(extra_vars)
    extra_vars["kubeconfig"] = os.environ.get("KUBECONFIG", "/home/thinkube/.kube/config")
    domain_name = settings.DOMAIN_NAME
    extra_vars["harbor_registry"] = f"registry.{domain_name}"

    temp_vars_fd, temp_vars_path = tempfile.mkstemp(suffix=".yml", prefix="venv-vars-")
    process = None

    try:
        with os.fdopen(temp_vars_fd, "w") as f:
            yaml.dump(extra_vars, f)
    except:
        os.close(temp_vars_fd)
        raise

    try:
        inventory_path = ansible_env.get_inventory_path()
        cmd = ansible_env.get_command_with_buffer(
            playbook_path, inventory_path, temp_vars_path
        )
        env = ansible_env.get_environment(context="template")

        logger.info(f"Executing venv build: {' '.join(cmd)}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd="/home/thinkube/thinkube-control",
            limit=1024 * 1024,
        )

        current_task = None
        task_count = 0
        all_output_lines = []

        while True:
            line = await process.stdout.readline()
            if not line:
                break

            line_text = line.decode("utf-8").strip()
            if not line_text:
                continue

            all_output_lines.append(line_text)

            if "TASK [" in line_text:
                task_start = line_text.find("TASK [") + 6
                task_end = line_text.find("]", task_start)
                if task_end > task_start:
                    current_task = line_text[task_start:task_end]
                    task_count += 1
                    await send_chunked_message(websocket, {
                        "type": "task",
                        "task_number": task_count,
                        "task_name": current_task,
                        "message": line_text,
                    })
            elif "PLAY [" in line_text:
                await send_chunked_message(websocket, {
                    "type": "play",
                    "message": line_text,
                })
            elif "ok: [" in line_text:
                await send_chunked_message(websocket, {
                    "type": "ok",
                    "task": current_task,
                    "message": line_text,
                })
            elif "changed: [" in line_text:
                await send_chunked_message(websocket, {
                    "type": "changed",
                    "task": current_task,
                    "message": line_text,
                })
            elif "failed: [" in line_text or "fatal: [" in line_text:
                await send_chunked_message(websocket, {
                    "type": "failed",
                    "task": current_task,
                    "message": line_text,
                })
            elif "skipping: [" in line_text:
                await send_chunked_message(websocket, {
                    "type": "skipped",
                    "task": current_task,
                    "message": line_text,
                })
            else:
                await send_chunked_message(websocket, {
                    "type": "output",
                    "message": line_text,
                })

        return_code = await process.wait()

        architectures = []
        for output_line in all_output_lines:
            if "Architecture marker written:" in output_line:
                match = re.search(r"Architecture marker written:\s*(\w+)", output_line)
                if match:
                    architectures.append(match.group(1))

        await websocket.send_json({
            "type": "complete",
            "status": "success" if return_code == 0 else "error",
            "message": (
                f"Venv '{venv.name}' built successfully for {', '.join(architectures) or 'unknown'}"
                if return_code == 0
                else f"Venv build failed with return code: {return_code}"
            ),
            "return_code": return_code,
        })

        return {
            "return_code": return_code,
            "venv_path": f"/var/lib/jupyterhub-venvs/custom/{venv.name}",
            "architectures": architectures,
        }

    except Exception as e:
        logger.error(f"Error executing venv build: {e}")
        await websocket.send_json({"type": "error", "message": str(e)})
        return {"return_code": -1}

    finally:
        if process and process.returncode is None:
            process.terminate()
            await process.wait()
        try:
            os.unlink(temp_vars_path)
        except:
            pass
