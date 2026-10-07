# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""
The two substitutions of thinkube.yaml.

The specification (thinkube.yaml reference, Principles) allows exactly two
placeholders in the descriptor: {{ project_name }}, the application's name,
and {{ domain_name }}, the cluster's domain. They are replaced in every
string of the file as it is loaded, before anything reads it, so a value
such as an env default "https://llm.{{ domain_name }}/v1" reaches the
container resolved.
"""

from typing import Any

PROJECT_NAME = "{{ project_name }}"
DOMAIN_NAME = "{{ domain_name }}"


def substitute(config: Any, project_name: str, domain_name: str) -> Any:
    """config with both placeholders replaced in every string, at any depth."""
    if isinstance(config, str):
        return config.replace(PROJECT_NAME, project_name).replace(DOMAIN_NAME, domain_name)
    if isinstance(config, dict):
        return {key: substitute(value, project_name, domain_name) for key, value in config.items()}
    if isinstance(config, list):
        return [substitute(item, project_name, domain_name) for item in config]
    return config
