# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

# app/db/base.py
"""Base with every model registered, for code that creates the schema.

app.models imports every model module, and importing a model registers its
table with Base.metadata; nothing here needs to name them.
"""

from app.db.session import Base

import app.models  # noqa: F401  registers every model with Base.metadata

__all__ = ["Base"]
