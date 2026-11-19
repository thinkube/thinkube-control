# Thinkube Mill

Data transformation work on Thinkube: calling a model over items, cleaning
a dataset, scoring results. Each step is a task, a Python function that
declares its inputs, its outputs and its settings. Mill runs tasks and
shows every run: its progress, samples of the items it processed, its
running totals, its failures and its log.

Mill is an extension of Thinkube Control, deployed as `mill`: Control lists
it in its navigation and draws its pages. `PLAN.md` says what Mill is for,
what is built and what is not yet.

## The parts

- **The tasks repository.** One git repository per user, in the folder
  `mill` of the shared file system. The IDE mounts it at
  `~/thinkube-ai/mill` (the code-server storage playbook of thinkube); the
  backend mounts the file system at `/storage/thinkube-shared`. One folder
  for each piece of work, `common/` for shared code.
- **The library, `thinkube-mill`** (`backend/library`). How a task is
  written (`@task`, `track`, `show`, `count`, `measure`, `fail`), the
  command that runs one (`mill run`), and the record a run keeps. Its
  README is the reference of the task standard.
- **The main pod** (the backend). It runs the tasks that need no GPU, as
  processes of `mill run`, as the user that owns the tasks repository.
- **The control center** (the frontend, and the API in
  `backend/app/api/runs.py`). A run reports through NATS wherever it
  executes: the main pod, the IDE, or a pod on a GPU node. The backend reads
  the runs' messages from a JetStream stream and keeps each run's record in
  its database (`backend/app/services/consumer.py`, `store.py`), so it shows
  every run.

## What it needs

The optional component NATS, with JetStream, at
`nats://nats.nats.svc.cluster.local:4222`. Without it no run can start, and
the Runs page says what is wrong.

## What the backend does

| Route | Tool | Does |
|---|---|---|
| `GET /api/v1/runs` | `mill_list_runs` | the newest runs, with state and stages |
| `GET /api/v1/runs/{id}` | `mill_get_run` | one run: what was run, and its status |
| `GET /api/v1/runs/{id}/samples` | `mill_get_run_samples` | the samples the run kept |
| `GET /api/v1/runs/{id}/failures` | `mill_get_run_failures` | the items the run reported as failed |
| `GET /api/v1/runs/{id}/log` | `mill_get_run_log` | the last lines the run printed |
| `GET /api/v1/tasks?file=` | `mill_describe_tasks` | the tasks a file declares |
| `POST /api/v1/runs` | `mill_start_run` | start a run in the main pod |
| `POST /api/v1/runs/{id}/stop` | `mill_stop_run` | stop a run, wherever it executes |
| `PUT /api/v1/runs/{id}/tags` | `mill_tag_run` | attach tags to a run; `GET /api/v1/runs?tag=name=value` finds runs by tag |

The tools are the MCP server at `/api/v1/mcp`.

A task that calls a model reads the LLM Gateway's address in
`LLM_GATEWAY_URL` and calls it with `THINKUBE_API_TOKEN`, the token of whoever
started the run. The token is in the task's process only, never in the run's
record.

A run carries tags: names and values that link it with what it is for, such
as `experiment` or `claim`. They are given when the run starts or attached
later, and the Runs page and the run's page show them.

A path of an input or an output is relative to the root of the tasks
repository, or absolute. The repository's `.gitignore` keeps `data/`
(working data) out of git.

Stopping a run is a request through NATS that the run's process answers, so
a run is stopped wherever it executes. A running run that sends nothing for
90 seconds (a run sends a heartbeat every 10) is marked as failed: its
process ended without finishing it.

## Tests

`backend/run_tests.sh` runs the library's tests and the backend's; the
backend's start real runs, which report through the platform's NATS on
subjects of their own. `frontend/run_tests.sh` runs the pages' tests.
The build runs both on every deploy.
