"""
Background executor for template deployments.
Based on the working WebSocket executor with minimal modifications.
"""

from typing import Dict, Any, Optional
import asyncio
import logging
import os
import json
import yaml
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.deployments import TemplateDeployment, DeploymentLog
from app.services.ansible_environment import ansible_env

logger = logging.getLogger(__name__)


class BackgroundExecutor:
    """Handles background execution of template deployments."""

    def __init__(self):
        self.running_deployments = {}

    async def start_deployment(self, deployment_id: str) -> None:
        """Start a deployment in the background."""
        if deployment_id not in self.running_deployments:
            task = asyncio.create_task(self._execute_deployment(deployment_id))
            self.running_deployments[deployment_id] = task
    
    async def execute_component_playbook(
        self,
        deployment_id: str,
        playbook_path: str,
        extra_vars: Dict[str, Any],
        component_name: str
    ) -> None:
        """Execute an optional component playbook in the background."""
        if deployment_id not in self.running_deployments:
            task = asyncio.create_task(
                self._execute_component_deployment(
                    deployment_id, playbook_path, extra_vars, component_name
                )
            )
            self.running_deployments[deployment_id] = task

    async def _execute_deployment(self, deployment_id: str) -> None:
        """Execute a deployment in the background."""
        db = next(get_db())

        try:
            # Get deployment record
            deployment = (
                db.query(TemplateDeployment).filter_by(id=deployment_id).first()
            )
            if not deployment:
                logger.error(f"Deployment {deployment_id} not found")
                return

            # Check if already running or completed
            if deployment.status != "pending":
                logger.warning(
                    f"Deployment {deployment_id} is already {deployment.status}"
                )
                return

            # Update status to running
            deployment.status = "running"
            deployment.started_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(f"Starting background deployment {deployment_id}")

            # Execute the deployment
            try:
                result = await self._run_ansible_playbook(deployment, db)

                # Update deployment status based on result
                if result["success"]:
                    deployment.status = "success"
                    deployment.output = "Deployment completed successfully"
                else:
                    deployment.status = "failed"
                    deployment.output = result.get("error", "Deployment failed")

            except asyncio.CancelledError:
                deployment.status = "cancelled"
                deployment.output = "Deployment was cancelled"
                raise
            except Exception as e:
                logger.error(f"Deployment {deployment_id} failed: {e}")
                deployment.status = "failed"
                deployment.output = f"Deployment failed: {str(e)}"

            finally:
                deployment.completed_at = datetime.now(timezone.utc)
                db.commit()

                # Remove from running deployments
                self.running_deployments.pop(deployment_id, None)

        finally:
            db.close()

    async def _execute_component_deployment(
        self,
        deployment_id: str,
        playbook_path: str,
        extra_vars: Dict[str, Any],
        component_name: str
    ) -> None:
        """Execute an optional component deployment in the background."""
        db = next(get_db())

        try:
            # Get deployment record
            deployment = (
                db.query(TemplateDeployment).filter_by(id=deployment_id).first()
            )
            if not deployment:
                logger.error(f"Deployment {deployment_id} not found")
                return

            # Update status to running
            deployment.status = "running"
            deployment.started_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(f"Starting optional component deployment {deployment_id} for {component_name}")

            # Execute the playbook directly
            try:
                # Build ansible command for component playbook
                result = await self._run_component_ansible_playbook(
                    deployment, db, playbook_path, extra_vars
                )

                # Update deployment status based on result
                if result["success"]:
                    deployment.status = "success"
                    deployment.output = f"Component {component_name} installed successfully"
                else:
                    deployment.status = "failed"
                    deployment.output = result.get("error", f"Component {component_name} installation failed")

            except Exception as e:
                logger.error(f"Component deployment {deployment_id} failed: {e}")
                deployment.status = "failed"
                deployment.output = f"Component deployment failed: {str(e)}"

            finally:
                deployment.completed_at = datetime.now(timezone.utc)
                db.commit()
                self.running_deployments.pop(deployment_id, None)

        finally:
            db.close()

    async def _run_component_ansible_playbook(
        self, 
        deployment: TemplateDeployment, 
        db: Session,
        playbook_path: str,
        extra_vars: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute an ansible playbook for optional component installation."""
        
        # The playbook path is relative to the thinkube repository root
        # Code-server clones thinkube repo to /home/coder/thinkube-platform/thinkube
        # This maps to /home/thinkube-platform/thinkube in the backend container
        # (because shared-code is mounted at /home in backend container)
        
        # Primary path: where code-server clones the thinkube repo
        thinkube_path = Path("/home/thinkube-platform/thinkube")
        full_playbook_path = thinkube_path / playbook_path
        
        # Fallback for development/testing on host
        if not full_playbook_path.exists():
            # Try host path when running outside container
            thinkube_path = Path("/home/thinkube/thinkube")
            full_playbook_path = thinkube_path / playbook_path
        
        if not full_playbook_path.exists():
            error_msg = f"Playbook not found: {full_playbook_path}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        # Prepare variables with authentication (same as template deployment)
        extra_vars = ansible_env.prepare_auth_vars(extra_vars)
        
        # Create temporary vars file
        temp_vars_fd, temp_vars_path = tempfile.mkstemp(
            suffix=".yml", prefix="ansible-vars-"
        )
        
        try:
            with os.fdopen(temp_vars_fd, "w") as f:
                yaml.dump(extra_vars, f)
        except:
            os.close(temp_vars_fd)
            raise
        
        # Get inventory path from ansible_env
        inventory_path = ansible_env.get_inventory_path()
        
        # Get command with buffering (same as template deployment)
        cmd = ansible_env.get_command_with_buffer(
            full_playbook_path, inventory_path, temp_vars_path
        )
        
        # Get environment for optional components
        env = ansible_env.get_environment(context="optional")
        
        # Execute the playbook using the same pattern as template deployment
        try:
            return await self._execute_ansible_subprocess(
                cmd, env, deployment, db, full_playbook_path.parent
            )
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_vars_path)
            except:
                pass

    async def _run_ansible_playbook(
        self, deployment: TemplateDeployment, db: Session
    ) -> Dict[str, Any]:
        """
        Execute template deployment using the new Python script.

        This replaces the Ansible playbook with a faster Python implementation.
        """
        # Prepare variables with authentication
        extra_vars = deployment.variables.copy()
        extra_vars = ansible_env.prepare_auth_vars(extra_vars)

        # Create temporary vars file for the Python script
        temp_vars_fd, temp_vars_path = tempfile.mkstemp(
            suffix=".json", prefix="deploy-vars-"
        )

        try:
            with os.fdopen(temp_vars_fd, "w") as f:
                json.dump(extra_vars, f)
        except:
            os.close(temp_vars_fd)
            raise

        try:
            # Build command for Python deployment script
            python_script = Path("/home/thinkube-control/scripts/deploy_application.py")
            cmd = ["python3", str(python_script), temp_vars_path]

            # Log start
            self._log_to_db(
                db, deployment.id, "info", "Starting deployment with Python script"
            )

            # Execute Python script
            return await self._execute_python_deployment(cmd, deployment, db)

        finally:
            try:
                os.unlink(temp_vars_path)
            except:
                pass

    async def _execute_python_deployment(
        self, cmd: list, deployment: TemplateDeployment, db: Session
    ) -> Dict[str, Any]:
        """
        Execute the Python deployment script and stream output.
        """
        process = None

        try:
            # Create subprocess
            logger.info(f"Running command: {' '.join(cmd)}")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=os.environ.copy(),
            )

            # Stream output
            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                line_text = line.decode("utf-8", errors="replace").rstrip()
                if not line_text:
                    continue

                # Parse output from Python script
                if "PHASE" in line_text:
                    self._log_to_db(db, deployment.id, "phase", line_text)
                elif "ERROR" in line_text:
                    self._log_to_db(db, deployment.id, "error", line_text)
                elif "SUCCESS" in line_text or "✅" in line_text:
                    self._log_to_db(db, deployment.id, "success", line_text)
                elif any(keyword in line_text for keyword in ["Created", "Updated", "Failed", "Workflow", "Argo"]):
                    self._log_to_db(db, deployment.id, "info", line_text)

            # Wait for completion
            return_code = await process.wait()

            # Log final status
            if return_code == 0:
                self._log_to_db(
                    db,
                    deployment.id,
                    "complete",
                    "Deployment completed successfully",
                )
                return {"success": True}
            else:
                self._log_to_db(
                    db,
                    deployment.id,
                    "error",
                    f"Deployment failed with return code: {return_code}",
                )
                return {"success": False, "error": f"Return code: {return_code}"}

        except Exception as e:
            logger.error(f"Error executing Python deployment: {e}")
            self._log_to_db(db, deployment.id, "error", f"Execution error: {str(e)}")
            return {"success": False, "error": str(e)}

        finally:
            # Cleanup
            if process and process.returncode is None:
                process.terminate()
                await process.wait()

    def _log_to_db(
        self,
        db: Session,
        deployment_id: str,
        log_type: str,
        message: str,
        task_name: Optional[str] = None,
        task_number: Optional[int] = None,
    ) -> None:
        """Log to database."""
        try:
            log = DeploymentLog(
                deployment_id=deployment_id,
                type=log_type,
                message=message,
                task_name=task_name,
                task_number=task_number,
                timestamp=datetime.now(timezone.utc),
            )
            db.add(log)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to save deployment log: {e}")
            db.rollback()

    async def _execute_ansible_subprocess(
        self,
        cmd: list,
        env: dict,
        deployment: TemplateDeployment,
        db: Session,
        cwd: Path
    ) -> Dict[str, Any]:
        """
        Common method to execute ansible subprocess and stream output.
        Used by both template deployments and optional component installations.
        """
        # Create log directory
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        app_name = deployment.variables.get("app_name", deployment.name)
        
        log_base_dir = Path("/home/shared-logs/deployments")
        if not log_base_dir.exists():
            log_base_dir.mkdir(parents=True, exist_ok=True)
        
        app_log_dir = log_base_dir / app_name
        app_log_dir.mkdir(parents=True, exist_ok=True)
        
        debug_log_file = app_log_dir / f"deployment-{timestamp}.log"
        
        process = None
        debug_file = None
        
        try:
            # Open debug log
            debug_file = open(debug_log_file, "w")
            debug_file.write(f"=== THINKUBE DEPLOYMENT LOG (Background) ===\n")
            debug_file.write(f"Deployment ID: {deployment.id}\n")
            debug_file.write(f"Application: {app_name}\n")
            debug_file.write(f"Started at: {datetime.now()}\n")
            debug_file.write(f"Command: {' '.join(cmd)}\n")
            debug_file.write(f"\n=== ANSIBLE OUTPUT ===\n")
            
            # Create subprocess
            logger.info(f"Running command: {' '.join(cmd)}")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                cwd=str(cwd),
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
                
                # Write to debug file
                if debug_file:
                    debug_file.write(f"{line_text}\n")
                    debug_file.flush()
                
                # Parse and log to database
                if "TASK [" in line_text:
                    task_start = line_text.find("TASK [") + 6
                    task_end = line_text.find("]", task_start)
                    if task_end > task_start:
                        current_task = line_text[task_start:task_end]
                        task_count += 1
                        
                        self._log_to_db(
                            db,
                            deployment.id,
                            "task",
                            line_text,
                            task_name=current_task,
                            task_number=task_count,
                        )
                elif "PLAY [" in line_text:
                    self._log_to_db(db, deployment.id, "play", line_text)
                elif "ok: [" in line_text:
                    self._log_to_db(
                        db, deployment.id, "ok", line_text, task_name=current_task
                    )
                elif "changed: [" in line_text:
                    self._log_to_db(
                        db, deployment.id, "changed", line_text, task_name=current_task
                    )
                elif "failed: [" in line_text or "fatal: [" in line_text:
                    self._log_to_db(
                        db, deployment.id, "failed", line_text, task_name=current_task
                    )
                elif "skipping: [" in line_text:
                    self._log_to_db(
                        db, deployment.id, "skipped", line_text, task_name=current_task
                    )
                elif "ERROR" in line_text or "WARNING" in line_text:
                    self._log_to_db(db, deployment.id, "output", line_text)
            
            # Wait for completion
            return_code = await process.wait()
            
            # Log completion
            if debug_file:
                debug_file.write(f"\n=== COMPLETED ===\n")
                debug_file.write(f"Return code: {return_code}\n")
                debug_file.write(f"Finished at: {datetime.now()}\n")
            
            # Log final status
            if return_code == 0:
                self._log_to_db(
                    db,
                    deployment.id,
                    "complete",
                    "Deployment completed successfully",
                )
                return {"success": True}
            else:
                self._log_to_db(
                    db,
                    deployment.id,
                    "error",
                    f"Deployment failed with return code: {return_code}",
                )
                return {"success": False, "error": f"Return code: {return_code}"}
                
        except Exception as e:
            logger.error(f"Error executing deployment: {e}")
            self._log_to_db(db, deployment.id, "error", f"Execution error: {str(e)}")
            return {"success": False, "error": str(e)}
            
        finally:
            # Cleanup
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
            
            if debug_file:
                debug_file.close()

    async def cancel_deployment(self, deployment_id: str) -> bool:
        """Cancel a running deployment."""
        if deployment_id in self.running_deployments:
            task = self.running_deployments[deployment_id]
            task.cancel()
            return True
        return False


# Global instance
background_executor = BackgroundExecutor()
