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
  `backend/app/api/runs.py`). It reads the records under `.mill/runs` of the
  tasks repository, so it shows a run wherever it executes: the main pod,
  the IDE, or a pod on a GPU node.

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
| `POST /api/v1/runs/{id}/stop` | `mill_stop_run` | stop a run the main pod executes |

The tools are the MCP server at `/api/v1/mcp`.

A path of an input or an output is relative to the root of the tasks
repository, or absolute. The repository's `.gitignore` keeps `.mill/` (the
records and logs) and `data/` (working data) out of git.

A run the main pod started keeps its log in `.mill/logs/<run id>.log`. A
run started elsewhere has no log here. When the main pod stops, the runs it
was executing end with it; the backend marks them as failed the next time
it starts or lists the runs (`reconcile` in `backend/app/services/runs.py`).

## Tests

`backend/run_tests.sh` runs the library's tests and the backend's; the
backend's start real runs. `frontend/run_tests.sh` runs the pages' tests.
The build runs both on every deploy.
