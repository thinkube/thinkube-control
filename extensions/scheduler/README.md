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

- **Holds.** A service that starts its own pods, such as the LLM gateway,
  asks for a hold: GPUs kept for it (`POST /api/v1/holds`, or
  `/internal/scheduler/holds` inside the cluster, without a user). A hold
  takes its turn as a job does; once it runs, `gpu_ids` are the GPUs its
  owner starts the pod on. When a job of a higher priority needs them, the
  owner is asked at the hold's `release_url` to give them up, frees them,
  and releases the hold, which then waits again for its turn. A hold may
  name the GPUs it must be given (`on_gpus`), for a pod that is on them
  already. Its owner changes the memory it takes
  (`POST /api/v1/jobs/<id>/memory`) as the work on its pod grows or shrinks;
  a hold that has its GPUs grows only while they hold it beside the other
  work on them. A hold may say the namespace of its pod (`pods_in`), and
  the pod then carries the label `scheduler.thinkube.io/hold` with the
  hold's id: the scheduler ends such a hold when it has its GPUs and no pod
  with its label has been there for 10 minutes, so a pod that is gone
  leaves no hold behind.
- **The turn.** On a node, the waiting jobs that ask for a GPU start in
  order: the higher priorities first, then the older ones, and a job behind
  one that waits waits behind it. A job that cannot start stops the
  scheduler's running jobs of a lower priority that hold what it needs, and
  no others; a job stopped so waits again and starts when its turn comes.
  Work of the same or a higher priority, and work the scheduler did not
  start, is waited for.
- **A node's limit.** `PUT /api/v1/nodes/<node>/limit`
  (`scheduler_set_node_limit`): the most of its GPUs that hold work at once,
  such as a limit of heat. It is not a priority: no running work is stopped
  for it.

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
