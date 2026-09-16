# fastapi-mcp-extended

Extended FastAPI-MCP server with resource and prompt support. This package extends [fastapi-mcp](https://github.com/tadata-org/fastapi_mcp) to provide full Model Context Protocol (MCP) support including resources and prompts, in addition to tools.

## Features

- 🔧 **Tools**: Actions that modify state (from fastapi-mcp)
- 📚 **Resources**: Read-only data access with reduced token usage
- 💬 **Prompts**: User-friendly workflows and templates
- 🎯 **Smart Classification**: Automatically categorizes endpoints as resources or tools
- 🔄 **Full Compatibility**: Works seamlessly with existing fastapi-mcp features

## Installation

```bash
pip install fastapi-mcp-extended
```

## Quick Start

```python
from fastapi import FastAPI
from fastapi_mcp_extended import ExtendedFastApiMCP

app = FastAPI(title="My API")

# Define your FastAPI routes
@app.get("/items")
async def list_items():
    """List all items - will become a resource"""
    return {"items": ["item1", "item2"]}

@app.post("/items")
async def create_item(name: str):
    """Create an item - will remain a tool"""
    return {"created": name}

# Create extended MCP server
mcp = ExtendedFastApiMCP(
    app,
    auto_convert_resources=True,  # Auto-convert GET endpoints to resources
)

# Mount the MCP server
mcp.mount_http()
```

## Advanced Configuration

### Custom Resource Mapping

```python
mcp = ExtendedFastApiMCP(
    app,
    resource_patterns=[
        r"^/api/.*/list$",
        r"^/api/.*/get/",
        r"^/api/.*/status$",
    ],
    tool_patterns=[
        r"^/api/.*/create$",
        r"^/api/.*/update/",
        r"^/api/.*/delete/",
    ]
)
```

### Adding Prompts

The server offers the prompts it is given; it has none of its own.

```python
from fastapi_mcp_extended import ExtendedFastApiMCP, PromptDefinition
from fastapi_mcp_extended.types import PromptArgument, PromptMessage

mcp = ExtendedFastApiMCP(
    app,
    prompt_definitions=[
        PromptDefinition(
            name="deploy-app",
            description="Deploy a new application",
            messages=[
                PromptMessage(
                    role="user",
                    content="Deploy {{app_name}} from the template {{template}}.",
                )
            ],
            arguments=[
                PromptArgument(name="app_name", description="Application name"),
                PromptArgument(
                    name="template",
                    description="Template to use",
                    required=False,
                    default="tkt-webapp-react-fastapi",
                ),
            ],
        )
    ],
)

# The same, after construction, from plain dicts
mcp.add_prompt(name=..., description=..., messages=[{"role": ..., "content": ...}], arguments=[...])
```

Rules, checked when a prompt is added (a broken prompt raises `ValueError`
and the server does not start):

- Roles are `user` or `assistant`, the two the MCP protocol allows.
- Every `{{placeholder}}` names a declared argument, and every declared
  argument is used.
- A required argument has no default. An optional argument has one, and it
  fills the placeholder when the caller leaves the argument out.

When a client gets a prompt, a missing required argument and an argument the
prompt does not declare are refused with an error naming the prompt's
arguments.

## How It Works

### Automatic Classification

The package automatically classifies your FastAPI endpoints:

- **GET endpoints** → Resources (read-only data)
- **POST/PUT/DELETE/PATCH** → Tools (actions)
- Special cases can be configured via patterns

### Resource Templates

GET endpoints with path parameters become resource templates:
- `/api/items` → `resource://items`
- `/api/items/{id}` → `resource://items/{id}`

### Token Optimization

Resources are more efficient for read operations as they:
- Cache responses when appropriate
- Use simpler protocol messages
- Reduce token usage for data retrieval

## Comparison with fastapi-mcp

| Feature | fastapi-mcp | fastapi-mcp-extended |
|---------|-------------|---------------------|
| Tools | ✅ | ✅ |
| Resources | ❌ | ✅ |
| Prompts | ❌ | ✅ |
| Auto-classification | ❌ | ✅ |
| Token optimization | Limited | Full |

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - see LICENSE file for details.

## Credits

Built on top of [fastapi-mcp](https://github.com/tadata-org/fastapi_mcp) by Tadata Inc.