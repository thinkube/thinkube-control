# LLM Gateway

The LLM Gateway part of Thinkube Control, as an extension: it decides where
each model runs, loads and unloads models on the GPU nodes, and says which
backend serves a model. Control lists it in its navigation and draws its page
inside its own layout.

## What it does

- **Models and their state.** The catalogue models that are mirrored, read
  from the extension Models (`http://control-models-backend:8000/internal/models`),
  each with its state: deployable, loading, available, unloading.
- **GPU nodes.** Each node's GPUs, its memory for AI and its slots, from the
  nodes and the node-metrics pods of Control's namespace.
- **Backends.** The inference backends installed as components (vLLM,
  TensorRT-LLM, text-embeddings, Ollama, ...), each declared in its service
  ConfigMap (`spec.llm_backend` of its `thinkube.yaml`), and the pods that
  serve a model.
- **Loading.** A load sizes the model to a node and creates a Deployment for
  it in the backend's namespace (a one-model backend), or pulls it into the
  node's Ollama pod. An unload removes it. A backend switched off in
  Control's services view is not placed: the gateway asks Control
  (`http://backend:8000/internal/services/enabled`).

At start the backend takes over the Deployments it created before (label
`thinkube.io/managed-by=llm-gateway`), then follows the backends every 30
seconds and the catalogue every 60.

## Who calls it

- **People and agents** call `/api/v1/llm/...` with a sign-in: through
  Control's proxy, `/api/v1/x/control-llm/api/llm/...` of Control's address,
  or at `https://control-llm.<domain>/api/v1/llm/...`. The MCP server is at
  `/api/v1/mcp`, with the tools `llm_*`. `tk-llm` and Forge use the same API.
- **The LLM proxy** at `llm.<domain>`, which stays with Control, asks
  `/internal/llm/models/resolve` which backend serves the model of each
  request, and lists the models at `/internal/llm/models/`.
- **Control's training mode** unloads a node's models and loads them again
  at `/internal/llm/...`, for up to 45 minutes after the request that
  started it.

The `/internal/llm` routes are the routes of `/api/v1/llm` without a
sign-in. They are outside `/api`, so neither the extension's address nor
Control's proxy for extensions reaches them: only pods of the cluster do.
