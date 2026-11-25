# LLM Gateway

The LLM Gateway part of Thinkube Control, as an extension: it decides where
each model runs, loads and unloads models on the GPU nodes, and says which
backend serves a model. Control lists it in its navigation and draws its page
inside its own layout.

## What it does

- **The proxy** (`proxy/`, Go): the gateway's OpenAI and Anthropic API at the
  platform address `llm.<domain>` (a route with `host: llm`), and in-cluster
  at `http://llm-proxy.thinkube-control.svc.cluster.local:8080`. It checks
  the caller's token with Control, asks the backend which model server
  serves the model a request names, and forwards the request there. Its Go
  tests run inside its image build.

- **Models and their state.** The catalogue models that are mirrored, read
  from the extension Models (`http://control-models-backend:8000/internal/models`),
  each with its state: deployable, loading, available, unloading.
- **GPU nodes.** Each node's GPUs, as GPU feature discovery counts them
  (`nvidia.com/gpu.count`), and its memory for AI, from the nodes and the
  node-metrics pods of Control's namespace. Kubernetes hands out no GPU:
  whether a node's GPUs are free for a load is the Scheduler's to say.
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

## The GPUs of a model's pod

A model served on a pod of its own (vLLM, text embeddings, llama.cpp and the
other backends of the driver `per-model-pod`) gets its GPUs from the
Scheduler (`backend/app/services/gpu_holds.py`):

- A load asks the Scheduler for a hold: the GPUs, and the memory the pod takes
  on each. The hold takes its turn among the work on the node. A load whose
  hold is given no GPU within 30 seconds is refused with the Scheduler's
  reason; while the Scheduler makes room for it, it is waited for.
- The pod is started on the GPUs the hold was given, named by their ids
  (`NVIDIA_VISIBLE_DEVICES`, runtime class `nvidia-legacy`), and asks
  Kubernetes for no GPU. Its Deployment keeps the hold's id, and the hold ends
  when the Deployment is deleted or scaled to no replicas.
- When work of a higher priority needs the GPUs, the Scheduler asks the gateway
  to give them up (`/internal/llm/holds/release`). The model is unloaded, its
  Deployment kept, and the hold released; the model is loaded again when the
  Scheduler gives the hold GPUs again.

A pod that serves several models (the trained heads, Ollama) has one hold,
for the memory of the models on it:

- The first model's load asks for the hold, and the pod starts on the GPU it
  was given. A further model's load asks the Scheduler to let the hold grow
  first; refused, the load is refused with the Scheduler's reason. The hold
  shrinks as models leave, and ends with the pod.
- Asked to give its GPU up, the pod is stopped with the ids of its models kept
  on its Deployment, and they are loaded on it again when the hold has GPUs.
- A pod that was running before this, with a GPU asked of Kubernetes, stays so
  until it is made again: when its last model is unloaded, the pod is deleted,
  and the next load makes it on a hold.

## Who calls it

- **People and agents** call `/api/v1/llm/...` with a sign-in: through
  Control's proxy, `/api/v1/x/control-llm/api/llm/...` of Control's address,
  or at `https://control-llm.<domain>/api/v1/llm/...`. The MCP server is at
  `/api/v1/mcp`, with the tools `llm_*`. `tk-llm` and Forge use the same API.
- **The LLM proxy** at `llm.<domain>`, which stays with Control, asks
  `/internal/llm/models/resolve` which backend serves the model of each
  request, and lists the models at `/internal/llm/models/`.
- **The Scheduler** asks the gateway to give a hold's GPUs up for work of a
  higher priority, at `/internal/llm/holds/release`.

The `/internal/llm` routes are the routes of `/api/v1/llm` without a
sign-in. They are outside `/api`, so neither the extension's address nor
Control's proxy for extensions reaches them: only pods of the cluster do.
