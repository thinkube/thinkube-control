# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""MCP prompts: the rules of the fork's PromptHandler, and thinkube-control's prompt set.

The handler is checked on its own with small prompts. The prompt set is
checked against the app: every tool a prompt names must be an operation the
MCP server exposes, and every prompt must fill from its arguments.
"""

import re

import pytest

from fastapi_mcp_extended.prompts import PromptHandler
from fastapi_mcp_extended.types import PromptArgument, PromptDefinition, PromptMessage

from app import MCP_OPERATIONS
from app.mcp_prompts import prompt_definitions

DOMAIN = "example.com"


def definition(name="greet", messages=None, arguments=None):
    return PromptDefinition(
        name=name,
        description=f"the {name} prompt",
        messages=messages
        or [
            PromptMessage(role="user", content="Deploy {{app}} on {{node}}."),
            PromptMessage(role="assistant", content="Deploying {{app}} on {{node}}."),
        ],
        arguments=arguments
        or [
            PromptArgument(name="app", description="the app"),
            PromptArgument(name="node", description="the node", required=False, default="any node"),
        ],
    )


def handler_with(*definitions):
    handler = PromptHandler()
    for prompt in definitions:
        handler.add_prompt(prompt)
    return handler


def texts(result):
    return [(message.role, message.content.text) for message in result.messages]


# --- PromptHandler -----------------------------------------------------------


def test_list_carries_name_description_and_arguments():
    handler = handler_with(definition())

    [prompt] = handler.prompts
    assert prompt.name == "greet"
    assert prompt.description == "the greet prompt"
    assert [(a.name, a.required) for a in prompt.arguments] == [("app", True), ("node", False)]


def test_get_fills_every_message_with_the_arguments():
    handler = handler_with(definition())

    result = handler.get_prompt("greet", {"app": "notes", "node": "tkspark"})

    assert result.description == "the greet prompt"
    assert texts(result) == [
        ("user", "Deploy notes on tkspark."),
        ("assistant", "Deploying notes on tkspark."),
    ]


def test_missing_required_argument_is_refused():
    handler = handler_with(definition())

    with pytest.raises(ValueError, match="needs the argument 'app' \\(the app\\)"):
        handler.get_prompt("greet", {"node": "tkspark"})


def test_default_fills_a_missing_optional_argument():
    handler = handler_with(definition())

    result = handler.get_prompt("greet", {"app": "notes"})

    assert texts(result)[0] == ("user", "Deploy notes on any node.")


def test_a_given_value_wins_over_the_default():
    handler = handler_with(definition())

    result = handler.get_prompt("greet", {"app": "notes", "node": "tkamd2"})

    assert texts(result)[0] == ("user", "Deploy notes on tkamd2.")


def test_unknown_argument_is_refused():
    handler = handler_with(definition())

    with pytest.raises(ValueError, match="no argument named colour; its arguments are app, node"):
        handler.get_prompt("greet", {"app": "notes", "colour": "red"})


def test_unknown_prompt_is_refused():
    handler = handler_with(definition())

    with pytest.raises(ValueError, match="Prompt 'other' not found"):
        handler.get_prompt("other", {})


def test_system_role_is_refused_when_the_prompt_is_added():
    prompt = definition(messages=[PromptMessage(role="system", content="You deploy {{app}} on {{node}}.")])

    with pytest.raises(ValueError, match="role 'system' is not allowed; the MCP prompt roles are user, assistant"):
        handler_with(prompt)


def test_undeclared_placeholder_is_refused():
    prompt = definition(messages=[PromptMessage(role="user", content="Deploy {{app}} on {{node}} as {{user}}.")])

    with pytest.raises(ValueError, match="uses undeclared arguments: user"):
        handler_with(prompt)


def test_declared_argument_nothing_uses_is_refused():
    prompt = definition(messages=[PromptMessage(role="user", content="Deploy {{app}}.")])

    with pytest.raises(ValueError, match="declares arguments no message uses: node"):
        handler_with(prompt)


def test_optional_argument_needs_a_default():
    prompt = definition(
        arguments=[
            PromptArgument(name="app", description="the app"),
            PromptArgument(name="node", description="the node", required=False),
        ]
    )

    with pytest.raises(ValueError, match="argument 'node' is optional and needs a default"):
        handler_with(prompt)


def test_required_argument_with_a_default_is_refused():
    prompt = definition(
        arguments=[
            PromptArgument(name="app", description="the app", default="notes"),
            PromptArgument(name="node", description="the node", required=False, default="any"),
        ]
    )

    with pytest.raises(ValueError, match="argument 'app' is required, so its default would never apply"):
        handler_with(prompt)


def test_same_name_twice_is_refused():
    with pytest.raises(ValueError, match="Prompt 'greet' is already defined"):
        handler_with(definition(), definition())


# --- thinkube-control's prompts ---------------------------------------------

EXPECTED_PROMPTS = {"serve-model", "deploy-app", "publish-template", "build-notebook-env", "diagnose-service"}

# A tool name in a prompt text: an operation id between backticks.
TOOL_IN_TEXT = re.compile(r"`([a-z_]+)`")


@pytest.fixture
def prompts():
    return prompt_definitions(DOMAIN)


@pytest.fixture
def handler(prompts):
    return handler_with(*prompts)


def test_the_prompt_set(handler):
    assert {prompt.name for prompt in handler.prompts} == EXPECTED_PROMPTS


def test_every_tool_a_prompt_names_is_an_exposed_operation(app, prompts):
    # The MCP server takes its tools from the OpenAPI schema, so the schema
    # says which operation ids exist.
    declared = {
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for operation in path.values()
        if "operationId" in operation
    }
    exposed = declared & set(MCP_OPERATIONS)

    for prompt in prompts:
        for message in prompt.messages:
            named = set(TOOL_IN_TEXT.findall(message.content))
            assert named, f"{prompt.name} names no tool"
            missing = sorted(named - exposed)
            assert not missing, f"{prompt.name} names tools the MCP server does not expose: {missing}"


def test_every_prompt_fills_from_its_required_arguments(handler, prompts):
    for prompt in prompts:
        given = {arg.name: f"<{arg.name}>" for arg in prompt.arguments if arg.required}

        result = handler.get_prompt(prompt.name, given)

        for _, text in texts(result):
            assert "{{" not in text, f"{prompt.name} left a placeholder unfilled"
            for value in given.values():
                assert value in text


def test_serve_model_takes_the_default_node_and_the_domain(handler):
    result = handler.get_prompt("serve-model", {"model_id": "Qwen/Qwen3-8B"})

    [(role, text)] = texts(result)
    assert role == "user"
    assert "Target node: choose" in text
    assert "https://llm.example.com" in text
    assert '"model": "Qwen/Qwen3-8B"' in text


def test_serve_model_takes_a_given_node(handler):
    result = handler.get_prompt("serve-model", {"model_id": "Qwen/Qwen3-8B", "node": "tkspark"})

    assert "Target node: tkspark" in texts(result)[0][1]


def test_deploy_app_names_the_app_url(handler):
    result = handler.get_prompt("deploy-app", {"app_name": "notes", "template": "tkt-notes"})

    assert "https://notes.example.com" in texts(result)[0][1]


def test_publish_template_defaults_to_a_private_repository(handler):
    result = handler.get_prompt(
        "publish-template",
        {"app_name": "notes", "template_name": "tkt-notes", "description": "Notes"},
    )

    assert "Private GitHub repository: true" in texts(result)[0][1]
