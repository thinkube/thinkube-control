#!/usr/bin/env python3
"""
Copier Configuration Generator

Converts Thinkube template.yaml format to copier.yml format.
This allows us to control complexity while using Copier as the templating engine.
"""

import yaml
from typing import Dict, Any, List
import re
import logging

logger = logging.getLogger(__name__)


class CopierGenerator:
    """Generate copier.yml from template.yaml"""

    # Standard parameters that are always included
    STANDARD_PARAMETERS = {
        "project_name": {
            "type": "str",
            "help": "What is your project name? (lowercase, hyphens allowed)",
            "placeholder": "my-awesome-app",
            "validator": r"{% if not (project_name | regex_search('^[a-z][a-z0-9-]*$')) %}Project name must start with a letter and contain only lowercase letters, numbers, and hyphens{% endif %}",
        },
        "project_description": {
            "type": "str",
            "help": "Brief description of your project",
            "default": "A Thinkube application",
        },
        "author_name": {"type": "str", "help": "Your name", "placeholder": "John Doe"},
        "author_email": {
            "type": "str",
            "help": "Your email",
            "placeholder": "john@example.com",
        },
    }

    def __init__(self, template_yaml: Dict[str, Any]):
        """Initialize with parsed template.yaml content"""
        self.template = template_yaml
        self.validate_template()

    def validate_template(self):
        """Validate template.yaml structure"""
        if self.template.get("apiVersion") != "thinkube.io/v1":
            raise ValueError("Only apiVersion: thinkube.io/v1 is supported")

        if self.template.get("kind") != "TemplateManifest":
            raise ValueError("kind must be 'TemplateManifest'")

        if "metadata" not in self.template:
            raise ValueError("metadata section is required")

        if "name" not in self.template["metadata"]:
            raise ValueError("metadata.name is required")

        # Validate parameters if present
        if "parameters" in self.template:
            # Warn if too many parameters
            param_count = len(self.template["parameters"])
            if param_count > 2:
                logger.warning(
                    f"Template '{self.template['metadata']['name']}' has {param_count} parameters. "
                    f"Consider splitting into focused templates instead."
                )

            for param in self.template["parameters"]:
                self._validate_parameter(param)

    def _validate_parameter(self, param: Dict[str, Any]):
        """Validate a single parameter definition"""
        if "name" not in param:
            raise ValueError("Parameter must have a name")

        if "type" not in param:
            raise ValueError(f"Parameter '{param['name']}' must have a type")

        if param["type"] not in ["str", "bool", "int", "choice"]:
            raise ValueError(
                f"Parameter '{param['name']}' has invalid type: {param['type']}"
            )

        # Validate parameter name format
        if not re.match(r"^[a-z][a-z0-9_]*$", param["name"]):
            raise ValueError(
                f"Parameter name '{param['name']}' must be lowercase with underscores"
            )

        # Check for reserved names
        if param["name"] in self.STANDARD_PARAMETERS:
            raise ValueError(f"Parameter name '{param['name']}' is reserved")

        # Type-specific validation
        if param["type"] == "choice" and "choices" not in param:
            raise ValueError(
                f"Parameter '{param['name']}' of type 'choice' must have choices"
            )

        if param["type"] == "int":
            if "default" in param and not isinstance(param["default"], int):
                raise ValueError(
                    f"Parameter '{param['name']}' default must be an integer"
                )

    def generate(self) -> Dict[str, Any]:
        """Generate copier.yml configuration"""
        copier_config = {
            "_templates_suffix": ".jinja",
            "_envops": {
                "block_start_string": "{%",
                "block_end_string": "%}",
                "variable_start_string": "{{",
                "variable_end_string": "}}",
                "comment_start_string": "{#",
                "comment_end_string": "#}",
                "keep_trailing_newline": True,
            },
        }

        # Add standard parameters first
        copier_config.update(self.STANDARD_PARAMETERS)

        # Convert template parameters to copier format
        if "parameters" in self.template:
            for param in self.template["parameters"]:
                copier_param = self._convert_parameter(param)
                copier_config[param["name"]] = copier_param

        # Add template metadata as variables
        copier_config["_metadata"] = {
            "template_name": self.template["metadata"]["name"],
            "template_title": self.template["metadata"].get("title", ""),
            "template_description": self.template["metadata"].get("description", ""),
            "template_version": self.template["metadata"].get("version", "1.0.0"),
        }

        # Add standard copier configuration
        copier_config["_skip_if_exists"] = ["ansible/inventory/"]

        copier_config["_exclude"] = ["template.yaml", "README.md", ".git"]

        # Add completion tasks
        tasks = [
            "echo '✅ Template created successfully!'",
            "echo '📝 Application: {{ project_name }}'",
            "echo '📁 Location: {{ _copier_conf.dst_path }}'",
        ]

        # Add template-specific tasks if needed
        if self.template["metadata"].get("name") == "python-api":
            tasks.append(
                "echo '🔧 Run: cd {{ _copier_conf.dst_path }} && pip install -r requirements.txt'"
            )
        elif self.template["metadata"].get("name") == "vue-fastapi":
            tasks.extend(
                [
                    "echo '🔧 Backend: cd {{ _copier_conf.dst_path }}/backend && pip install -r requirements.txt'",
                    "echo '🔧 Frontend: cd {{ _copier_conf.dst_path }}/frontend && npm install'",
                ]
            )

        copier_config["_tasks"] = tasks

        return copier_config

    def _convert_parameter(self, param: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a template parameter to copier format"""
        copier_param = {
            "type": param["type"],
            "help": param.get("description", f"Enter {param['name']}"),
        }

        # Add default if present
        if "default" in param:
            copier_param["default"] = param["default"]

        # Add placeholder if present
        if "placeholder" in param:
            copier_param["placeholder"] = param["placeholder"]

        # Handle type-specific conversions
        if param["type"] == "str":
            validators = []

            # Pattern validation
            if "pattern" in param:
                validators.append(
                    f"{{% if not ({param['name']} | regex_search('{param['pattern']}')) %}}"
                    f"Value must match pattern: {param['pattern']}"
                    f"{{% endif %}}"
                )

            # Length validation
            if "minLength" in param or "maxLength" in param:
                length_checks = []
                if "minLength" in param:
                    length_checks.append(
                        f"{param['name']} | length < {param['minLength']}"
                    )
                if "maxLength" in param:
                    length_checks.append(
                        f"{param['name']} | length > {param['maxLength']}"
                    )

                if length_checks:
                    validators.append(
                        f"{{% if {' or '.join(length_checks)} %}}"
                        f"Length must be between {param.get('minLength', 0)} and {param.get('maxLength', 'unlimited')}"
                        f"{{% endif %}}"
                    )

            if validators:
                copier_param["validator"] = " ".join(validators)

        elif param["type"] == "int":
            # For integers, we need to add validation
            validators = []

            if "min" in param or "max" in param:
                range_checks = []
                if "min" in param:
                    range_checks.append(f"{param['name']} < {param['min']}")
                if "max" in param:
                    range_checks.append(f"{param['name']} > {param['max']}")

                if range_checks:
                    validators.append(
                        f"{{% if {' or '.join(range_checks)} %}}"
                        f"Value must be between {param.get('min', 'any')} and {param.get('max', 'any')}"
                        f"{{% endif %}}"
                    )

            if validators:
                copier_param["validator"] = " ".join(validators)

        elif param["type"] == "choice":
            # Copier uses 'choices' for select fields
            copier_param["choices"] = param["choices"]
            # Remove type since copier infers it from choices
            del copier_param["type"]

        elif param["type"] == "bool":
            # Booleans are simple in copier
            pass

        return copier_param

    def to_yaml(self) -> str:
        """Generate copier.yml as YAML string"""
        config = self.generate()

        # Custom YAML dumper to maintain order and formatting
        yaml_lines = [
            "# AUTO-GENERATED FROM template.yaml - DO NOT EDIT MANUALLY",
            "# This file is automatically generated by the Thinkube template system",
            "",
        ]

        # Dump with nice formatting
        yaml_content = yaml.dump(
            config,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=120,
        )

        return "\n".join(yaml_lines) + yaml_content


def generate_copier_yml(template_yaml_path: str, output_path: str = None) -> str:
    """
    Generate copier.yml from template.yaml file

    Args:
        template_yaml_path: Path to template.yaml file
        output_path: Optional path to write copier.yml (if None, returns string)

    Returns:
        Generated copier.yml content as string
    """
    with open(template_yaml_path, "r") as f:
        template_data = yaml.safe_load(f)

    generator = CopierGenerator(template_data)
    copier_yml = generator.to_yaml()

    if output_path:
        with open(output_path, "w") as f:
            f.write(copier_yml)

    return copier_yml


# Example usage
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: copier_generator.py <template.yaml> [output.yml]")
        sys.exit(1)

    template_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        result = generate_copier_yml(template_path, output_path)
        if not output_path:
            print(result)
        else:
            print(f"Generated {output_path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
