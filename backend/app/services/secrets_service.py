"""
Service for managing secrets encryption and decryption
Uses Fernet symmetric encryption for storing secrets
"""

import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class SecretsService:
    """Handle encryption and decryption of secrets"""

    def __init__(self):
        # Get or generate encryption key
        self.fernet = self._get_or_create_fernet()

    def _get_or_create_fernet(self) -> Fernet:
        """Get or create Fernet encryption instance"""
        # Try to get key from environment
        encryption_key = os.environ.get("THINKUBE_ENCRYPTION_KEY")

        if encryption_key:
            # Use provided key
            return Fernet(encryption_key.encode())

        # Generate key from password (for development)
        # In production, always use THINKUBE_ENCRYPTION_KEY
        password = os.environ.get("ENCRYPTION_PASSWORD", "thinkube-secret-key")
        salt = os.environ.get("ENCRYPTION_SALT", "thinkube-salt").encode()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return Fernet(key)

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
