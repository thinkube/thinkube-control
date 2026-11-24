# Thinkube Scheduler

The platform's service for running work on the nodes: it decides whose turn
it is on a node and on which GPU, and it runs the work in a pod. Mill gives
it data transformations, Forge trainings, and the LLM gateway asks it for the
GPUs of a model. `PLAN.md` is the plan, with what is built and what is not
yet.

## What it does today

- **The nodes.** Each node's kind, its memory, and its GPUs by id with what
  each one holds now (`GET /api/v1/nodes`, the MCP tool
  `scheduler_list_nodes`, and the page Nodes). The figures come from Thinkube
  Control's node-metrics pods, one on each node, so they show all the work on
  a GPU, whoever started it.

A node's kind says what the work on it shares:

- `separate`: each GPU has memory of its own (tkamd2's cards). Work shares
  only the GPU it is on.
- `shared`: the GPU takes its memory from the system's (the DGX Spark). All
  the work on the node shares one memory.
- `none`: no GPU.

## Parts

- `backend/app/services/nodes.py`: the nodes, read from the node-metrics
  pods.
- `backend/app/api/nodes.py`: the route.
- `frontend/src/pages/NodesPage.tsx`: the page.
