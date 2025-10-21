#!/usr/bin/env python3
"""Tests for the Copier configuration generator"""

import pytest
import yaml
from app.utils.copier_generator import CopierGenerator, generate_copier_yml
import tempfile
import os


class TestCopierGenerator:
    """Test the CopierGenerator class"""

    def test_minimal_template(self):
        """Test generation with minimal template"""
        template = {
            "apiVersion": "thinkube.io/v1",
            "kind": "TemplateManifest",
            "metadata": {"name": "minimal-app", "description": "Minimal template"},
        }

        generator = CopierGenerator(template)
        config = generator.generate()

        # Check standard parameters are included
        assert "project_name" in config
        assert "project_description" in config
        assert "author_name" in config
        assert "author_email" in config

        # Check copier configuration
        assert config["_templates_suffix"] == ".jinja"
        assert "_envops" in config
        assert "_metadata" in config

    def test_string_parameter(self):
        """Test string parameter conversion"""
        template = {
            "apiVersion": "thinkube.io/v1",
            "kind": "TemplateManifest",
            "metadata": {"name": "test"},
            "parameters": [
                {
                    "name": "api_key",
                    "type": "str",
                    "description": "API key for service",
                    "pattern": "^[A-Z0-9]{16}$",
                    "minLength": 16,
                    "maxLength": 16,
                }
            ],
        }

        generator = CopierGenerator(template)
        config = generator.generate()

        assert "api_key" in config
        assert config["api_key"]["type"] == "str"
        assert "validator" in config["api_key"]
        assert "must match pattern" in config["api_key"]["validator"]
        assert "Length must be between" in config["api_key"]["validator"]

    def test_integer_parameter(self):
        """Test integer parameter conversion"""
        template = {
            "apiVersion": "thinkube.io/v1",
            "kind": "TemplateManifest",
            "metadata": {"name": "test"},
            "parameters": [
                {
                    "name": "max_workers",
                    "type": "int",
                    "description": "Maximum worker threads",
                    "default": 4,
                    "min": 1,
                    "max": 32,
                }
            ],
        }

        generator = CopierGenerator(template)
        config = generator.generate()

        assert "max_workers" in config
        assert config["max_workers"]["type"] == "int"
        assert config["max_workers"]["default"] == 4
        assert "validator" in config["max_workers"]
        assert "between 1 and 32" in config["max_workers"]["validator"]

    def test_boolean_parameter(self):
        """Test boolean parameter conversion"""
        template = {
            "apiVersion": "thinkube.io/v1",
            "kind": "TemplateManifest",
            "metadata": {"name": "test"},
            "parameters": [
                {
                    "name": "enable_debug",
                    "type": "bool",
                    "description": "Enable debug mode?",
                    "default": False,
                }
            ],
        }

        generator = CopierGenerator(template)
        config = generator.generate()

        assert "enable_debug" in config
        assert config["enable_debug"]["type"] == "bool"
        assert config["enable_debug"]["default"] is False

    def test_choice_parameter(self):
        """Test choice parameter conversion"""
        template = {
            "apiVersion": "thinkube.io/v1",
            "kind": "TemplateManifest",
            "metadata": {"name": "test"},
            "parameters": [
                {
                    "name": "database_type",
                    "type": "choice",
                    "description": "Which database?",
                    "choices": ["postgresql", "mysql", "sqlite"],
                    "default": "postgresql",
                }
            ],
        }

        generator = CopierGenerator(template)
        config = generator.generate()

        assert "database_type" in config
        assert "type" not in config["database_type"]  # Copier infers from choices
        assert config["database_type"]["choices"] == ["postgresql", "mysql", "sqlite"]
        assert config["database_type"]["default"] == "postgresql"

    def test_invalid_parameter_name(self):
        """Test validation of parameter names"""
        template = {
            "apiVersion": "thinkube.io/v1",
            "kind": "TemplateManifest",
            "metadata": {"name": "test"},
            "parameters": [
                {"name": "Invalid-Name", "type": "str"}  # Invalid: uppercase and hyphen
            ],
        }

        with pytest.raises(ValueError, match="must be lowercase"):
            CopierGenerator(template)

    def test_reserved_parameter_name(self):
        """Test rejection of reserved parameter names"""
        template = {
            "apiVersion": "thinkube.io/v1",
            "kind": "TemplateManifest",
            "metadata": {"name": "test"},
            "parameters": [{"name": "project_name", "type": "str"}],  # Reserved
        }

        with pytest.raises(ValueError, match="reserved"):
            CopierGenerator(template)

    def test_missing_choices_for_choice_type(self):
        """Test validation of choice type without choices"""
        template = {
            "apiVersion": "thinkube.io/v1",
            "kind": "TemplateManifest",
            "metadata": {"name": "test"},
            "parameters": [
                {
                    "name": "selection",
                    "type": "choice",
                    # Missing choices
                }
            ],
        }

        with pytest.raises(ValueError, match="must have choices"):
            CopierGenerator(template)

    def test_full_template_yaml(self):
        """Test with a complete template.yaml"""
        template = {
            "apiVersion": "thinkube.io/v1",
            "kind": "TemplateManifest",
            "metadata": {
                "name": "python-api",
                "title": "Python API Service",
                "description": "RESTful API with Python",
                "author": "Test Author",
                "version": "1.0.0",
                "tags": ["api", "python"],
            },
            "parameters": [
                {
                    "name": "api_framework",
                    "type": "choice",
                    "description": "Web framework",
                    "choices": ["fastapi", "flask"],
                    "default": "fastapi",
                },
                {
                    "name": "enable_cors",
                    "type": "bool",
                    "description": "Enable CORS?",
                    "default": True,
                },
                {
                    "name": "port",
                    "type": "int",
                    "description": "API port",
                    "default": 8000,
                    "min": 1024,
                    "max": 65535,
                },
            ],
        }

        generator = CopierGenerator(template)
        config = generator.generate()

        # Check all parameters are present
        assert "api_framework" in config
        assert "enable_cors" in config
        assert "port" in config

        # Check metadata is captured
        assert config["_metadata"]["template_name"] == "python-api"
        assert config["_metadata"]["template_title"] == "Python API Service"

        # Check YAML generation
        yaml_output = generator.to_yaml()
        assert "AUTO-GENERATED" in yaml_output
        assert "api_framework:" in yaml_output

    def test_generate_copier_yml_function(self):
        """Test the standalone generate function"""
        template = {
            "apiVersion": "thinkube.io/v1",
            "kind": "TemplateManifest",
            "metadata": {"name": "test"},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write template.yaml
            template_path = os.path.join(tmpdir, "template.yaml")
            with open(template_path, "w") as f:
                yaml.dump(template, f)

            # Generate without output path (returns string)
            result = generate_copier_yml(template_path)
            assert "project_name:" in result
            assert "AUTO-GENERATED" in result

            # Generate with output path
            output_path = os.path.join(tmpdir, "copier.yml")
            generate_copier_yml(template_path, output_path)

            assert os.path.exists(output_path)
            with open(output_path, "r") as f:
                content = f.read()
                assert "project_name:" in content
