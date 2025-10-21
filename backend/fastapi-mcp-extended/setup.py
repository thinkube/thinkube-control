"""Setup script for fastapi-mcp-extended."""

from setuptools import setup, find_packages

setup(
    name="fastapi-mcp-extended",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "fastapi-mcp>=0.4.0",
        "mcp>=1.1.0",
        "fastapi>=0.100.0",
        "httpx>=0.24.0",
    ],
    python_requires=">=3.9",
)