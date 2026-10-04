# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
Service for managing secrets encryption and decryption
Uses Fernet symmetric encryption for storing secrets

The key comes from THINKUBE_ENCRYPTION_KEY, which the backend Deployment reads
from the Secret thinkube-control/thinkube-encryption-key. There is no other
source: without the key the backend does not start.
"""

import os
from cryptography.fernet import Fernet


class SecretsService:
    """Handle encryption and decryption of secrets"""

    def __init__(self):
        self.fernet = self._fernet(os.environ.get("THINKUBE_ENCRYPTION_KEY"))

    @staticmethod
    def _fernet(encryption_key) -> Fernet:
        if not encryption_key:
            raise RuntimeError(
                "THINKUBE_ENCRYPTION_KEY is not set. It is read from the Secret "
                "thinkube-control/thinkube-encryption-key, which the thinkube-control "
                "deploy playbooks create."
            )
        try:
            return Fernet(encryption_key.encode())
        except ValueError as e:
            raise RuntimeError(f"THINKUBE_ENCRYPTION_KEY is not a valid Fernet key: {e}") from e

    def encrypt(self, value: str) -> str:
        """Encrypt a secret value"""
        if not value:
            return ""
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, encrypted_value: str) -> str:
        """Decrypt a secret value"""
        if not encrypted_value:
            return ""
        try:
            return self.fernet.decrypt(encrypted_value.encode()).decode()
        except Exception as e:
            # Log error but don't expose details
            print(f"Failed to decrypt secret: {e}")
            raise ValueError("Failed to decrypt secret")

    @staticmethod
    def generate_key() -> str:
        """Generate a new Fernet key for production use"""
        return Fernet.generate_key().decode()


# Singleton instance
secrets_service = SecretsService()
