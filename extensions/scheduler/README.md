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

- **Jobs.** A job is a command with what it needs: its node, its image, the
  folders it mounts, its GPUs and the memory it takes on each (none: it has
  its GPUs alone), its memory and CPU, a priority (`POST /api/v1/jobs`, the
  tools `scheduler_create_job`, `scheduler_list_jobs`, `scheduler_get_job`,
  `scheduler_stop_job`, `scheduler_get_job_log`, and the page Jobs). The
  worker starts a waiting job when its node has the GPUs it asks for, the
  higher priorities first, and says what a job waits for. A job's pod runs
  in the namespace `scheduler-runs`, asks Kubernetes for its memory and CPU
  only, and is given its GPUs by their ids.

A node's kind says what the work on it shares:

- `separate`: each GPU has memory of its own (tkamd2's cards). Work shares
  only the GPU it is on.
- `shared`: the GPU takes its memory from the system's (the DGX Spark). All
  the work on the node shares one memory.
- `none`: no GPU.

## Parts

- `backend/app/services/nodes.py`: the nodes, read from the node-metrics
  pods.
- `backend/app/services/placement.py`: the GPUs a job is given, or what it
  waits for.
- `backend/app/services/pods.py`: a job's pod.
- `backend/app/services/worker.py`: the loop that follows and starts jobs.
- `backend/app/api/`: the routes.
- `frontend/src/pages/`: the pages Nodes and Jobs.
