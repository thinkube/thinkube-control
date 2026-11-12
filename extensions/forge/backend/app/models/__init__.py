# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Database models for the application"""
# Import models to ensure they're registered with SQLAlchemy
from app.models.forge import Run

__all__ = ["Run"]