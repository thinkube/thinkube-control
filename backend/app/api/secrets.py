"""
API endpoints for secrets management
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.models.secrets import Secret, AppSecret
from app.services.secrets_service import secrets_service
from app.services.app_secret_usage import UnknownSecret, record_usage
from app.core.api_tokens import get_current_user_dual_auth

logger = logging.getLogger(__name__)


router = APIRouter(tags=["secrets"])


class SecretCreate(BaseModel):
    name: str = Field(..., description="Secret name (e.g., HF_TOKEN)")
    description: Optional[str] = Field(None, description="Secret description")
    value: str = Field(..., description="Secret value (will be encrypted)")


class SecretUpdate(BaseModel):
    description: Optional[str] = Field(None, description="Secret description")
    value: Optional[str] = Field(
        None, description="New secret value (will be encrypted)"
    )


class SecretResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    created_at: str
    updated_at: str
    created_by: str
    updated_by: Optional[str]
    used_by_apps: List[str]


@router.get("/", response_model=List[SecretResponse])
async def list_secrets(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """List all secrets (without values)"""
    secrets = db.query(Secret).all()
    return [SecretResponse(**secret.to_dict()) for secret in secrets]


@router.get("/{secret_id}", response_model=SecretResponse)
async def get_secret(
    secret_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get a specific secret (without value)"""
    secret = db.query(Secret).filter(Secret.id == secret_id).first()
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")

    return SecretResponse(**secret.to_dict())


@router.post("/", response_model=SecretResponse)
async def create_secret(
    secret_data: SecretCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Create a new secret"""
    # Check if secret with this name already exists
    existing = db.query(Secret).filter(Secret.name == secret_data.name).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Secret with name '{secret_data.name}' already exists",
        )

    # Encrypt the value
    encrypted_value = secrets_service.encrypt(secret_data.value)

    # Create the secret
    secret = Secret(
        name=secret_data.name,
        description=secret_data.description,
        encrypted_value=encrypted_value,
        created_by=current_user.get("preferred_username", "unknown"),
    )

    db.add(secret)
    db.commit()
    db.refresh(secret)

    return SecretResponse(**secret.to_dict())


@router.put("/{secret_id}", response_model=SecretResponse)
async def update_secret(
    secret_id: int,
    secret_update: SecretUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Update a secret"""
    secret = db.query(Secret).filter(Secret.id == secret_id).first()
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")

    # Update fields
    if secret_update.description is not None:
        secret.description = secret_update.description

    if secret_update.value is not None:
        secret.encrypted_value = secrets_service.encrypt(secret_update.value)

    secret.updated_by = current_user.get("preferred_username", "unknown")

    db.commit()
    db.refresh(secret)

    return SecretResponse(**secret.to_dict())


@router.delete("/{secret_id}")
async def delete_secret(
    secret_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Delete a secret"""
    secret = db.query(Secret).filter(Secret.id == secret_id).first()
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")

    # Check if secret is in use
    if secret.app_secrets:
        apps = [app_secret.app_name for app_secret in secret.app_secrets]
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete secret in use by applications: {', '.join(apps)}",
        )

    db.delete(secret)
    db.commit()

    return {"message": f"Secret '{secret.name}' deleted successfully"}


@router.get("/{secret_id}/apps", response_model=List[str])
async def get_secret_apps(
    secret_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Get list of apps using a secret"""
    secret = db.query(Secret).filter(Secret.id == secret_id).first()
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")

    return [app_secret.app_name for app_secret in secret.app_secrets]


@router.post("/decrypt/{secret_name}")
async def decrypt_secret(
    secret_name: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Decrypt a secret value (for internal use by deployment system)"""
    secret = db.query(Secret).filter(Secret.name == secret_name).first()
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")

    try:
        decrypted_value = secrets_service.decrypt(secret.encrypted_value)
        return {"value": decrypted_value}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))


class SecretUsage(BaseModel):
    app_name: str
    secret_names: List[str]


@router.post("/track-usage")
async def track_secret_usage(
    usage: SecretUsage,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Record which secrets an application receives, replacing what was recorded."""
    try:
        record_usage(db, usage.app_name, usage.secret_names)
    except UnknownSecret as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": f"Updated secret usage for app '{usage.app_name}'"}


@router.post("/generate-key")
async def generate_encryption_key(
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Generate a new encryption key for production use"""
    # Only allow admins to generate keys
    if "admin" not in current_user.get("groups", []):
        raise HTTPException(
            status_code=403, detail="Only admins can generate encryption keys"
        )

    key = secrets_service.generate_key()
    return {
        "key": key,
        "instructions": "Set this as THINKUBE_ENCRYPTION_KEY environment variable",
    }


@router.post("/export-to-notebooks")
async def export_secrets_to_notebooks(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user_dual_auth),
):
    """Export all secrets to JuiceFS via S3 Gateway

    Writes .secrets.env to JuiceFS using S3 API
    """
    import os
    import boto3
    from botocore.client import Config
    from kubernetes import client, config

    try:
        secrets = db.query(Secret).all()

        if not secrets:
            raise HTTPException(
                status_code=404,
                detail="No secrets found to export"
            )

        secrets_content = "# Thinkube Secrets - Exported from thinkube-control\n"
        secrets_content += "# DO NOT commit this file to version control\n"
        secrets_content += f"# Last exported: {db.query(Secret).first().updated_at}\n\n"

        for secret in secrets:
            try:
                decrypted_value = secrets_service.decrypt(secret.encrypted_value)
                escaped_value = decrypted_value.replace('"', '\\"').replace('$', '\\$')
                secrets_content += f'export {secret.name}="{escaped_value}"\n'
            except Exception as e:
                logger.error(f"Failed to decrypt secret {secret.name}: {e}")
                continue

        # Get JuiceFS subPath for jupyterhub-notebooks-pvc
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        v1 = client.CoreV1Api()

        # Get PVC to find its volume
        pvc = v1.read_namespaced_persistent_volume_claim("jupyterhub-notebooks-pvc", "jupyterhub")
        volume_name = pvc.spec.volume_name

        # Get PV to find subPath
        pv = v1.read_persistent_volume(volume_name)
        subpath = pv.spec.csi.volume_attributes.get('subPath', '')

        if not subpath:
            raise Exception("Could not determine JuiceFS subPath for notebooks PVC")

        # Write to JuiceFS via S3 Gateway
        s3_client = boto3.client(
            's3',
            endpoint_url='http://juicefs-gateway.juicefs.svc.cluster.local:9000',
            aws_access_key_id=os.environ.get('ADMIN_USERNAME', 'tkadmin'),
            aws_secret_access_key=os.environ['ADMIN_PASSWORD'],
            config=Config(signature_version='s3v4')
        )

        s3_key = f'{subpath}/.secrets.env'
        s3_client.put_object(
            Bucket='thinkube-shared',
            Key=s3_key,
            Body=secrets_content.encode('utf-8')
        )

        return {
            "message": f"Exported {len(secrets)} secrets to notebooks",
            "path": "/home/thinkube/thinkube/notebooks/.secrets.env",
            "secrets_count": len(secrets)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export secrets: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to export secrets: {str(e)}"
        )

