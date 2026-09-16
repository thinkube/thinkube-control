"""Prompt handling for MCP protocol."""

import re
from typing import Dict, List
import mcp.types as types
from .types import PromptDefinition

PROMPT_ROLES = ("user", "assistant")

# A placeholder in a message: {{name}}.
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class PromptHandler:
    """Handles prompt templates and management."""

    def __init__(self):
        """Initialize prompt handler."""
        self.prompts: List[types.Prompt] = []
        self._prompt_definitions: Dict[str, PromptDefinition] = {}

    def add_prompt(self, prompt_def: PromptDefinition) -> None:
        """
        Add a prompt definition.

        The definition is checked here, so that a defect in a prompt is found
        when the server starts and not when a client asks for the prompt:
        the name must be new, every role must be one the protocol allows,
        every placeholder must name a declared argument, every declared
        argument must be used, and an argument is either required without a
        default or optional with one.

        Args:
            prompt_def: Prompt definition to add

        Raises:
            ValueError: the definition breaks one of the rules above
        """
        if prompt_def.name in self._prompt_definitions:
            raise ValueError(f"Prompt '{prompt_def.name}' is already defined")

        arguments = prompt_def.arguments or []
        declared = {arg.name for arg in arguments}
        used = set()
        for msg in prompt_def.messages:
            if msg.role not in PROMPT_ROLES:
                raise ValueError(
                    f"Prompt '{prompt_def.name}': role '{msg.role}' is not allowed; "
                    f"the MCP prompt roles are {', '.join(PROMPT_ROLES)}"
                )
            used.update(_PLACEHOLDER.findall(msg.content))

        undeclared = sorted(used - declared)
        if undeclared:
            raise ValueError(
                f"Prompt '{prompt_def.name}' uses undeclared arguments: {', '.join(undeclared)}"
            )
        unused = sorted(declared - used)
        if unused:
            raise ValueError(
                f"Prompt '{prompt_def.name}' declares arguments no message uses: {', '.join(unused)}"
            )
        for arg in arguments:
            if arg.required and arg.default is not None:
                raise ValueError(
                    f"Prompt '{prompt_def.name}': argument '{arg.name}' is required, "
                    "so its default would never apply"
                )
            if not arg.required and arg.default is None:
                raise ValueError(
                    f"Prompt '{prompt_def.name}': argument '{arg.name}' is optional "
                    "and needs a default"
                )

        self._prompt_definitions[prompt_def.name] = prompt_def
        self.prompts.append(
            types.Prompt(
                name=prompt_def.name,
                description=prompt_def.description,
                arguments=[
                    types.PromptArgument(
                        name=arg.name,
                        description=arg.description,
                        required=arg.required,
                    )
                    for arg in arguments
                ],
            )
        )

    def get_prompt(self, name: str, arguments: Dict[str, str]) -> types.GetPromptResult:
        """
        Get a prompt with arguments filled in.

        A missing optional argument takes its default. A missing required
        argument, or an argument the prompt does not declare, is refused.

        Args:
            name: Name of the prompt
            arguments: Arguments to fill in the template

        Returns:
            Filled prompt result

        Raises:
            ValueError: unknown prompt, unknown argument, or missing required argument
        """
        if name not in self._prompt_definitions:
            raise ValueError(f"Prompt '{name}' not found")

        prompt_def = self._prompt_definitions[name]
        declared = prompt_def.arguments or []
        declared_names = [arg.name for arg in declared]

        unknown = sorted(set(arguments) - set(declared_names))
        if unknown:
            raise ValueError(
                f"Prompt '{name}' has no argument named {', '.join(unknown)}; "
                f"its arguments are {', '.join(declared_names) or 'none'}"
            )

        values: Dict[str, str] = {}
        for arg in declared:
            if arg.name in arguments:
                values[arg.name] = str(arguments[arg.name])
            elif arg.default is not None:
                values[arg.name] = arg.default
            else:
                raise ValueError(
                    f"Prompt '{name}' needs the argument '{arg.name}' ({arg.description})"
                )

        messages = []
        for msg in prompt_def.messages:
            content = _PLACEHOLDER.sub(lambda match: values[match.group(1)], msg.content)
            messages.append(
                types.PromptMessage(
                    role=msg.role,
                    content=types.TextContent(type="text", text=content),
                )
            )

        return types.GetPromptResult(
            description=prompt_def.description,
            messages=messages,
        )
