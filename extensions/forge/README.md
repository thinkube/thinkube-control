# Thinkube Forge

Fine-tune language models on Thinkube, from the browser. Pick a model the
cluster has mirrored and a dataset from the Datasets extension. Meep² trains
it with LoRA, QLoRA or LoRA on FP8 weights on a GPU node, in that node's
training queue, now or in the node's scheduled slots. Follow the loss, chat with the result, and serve
it through the LLM Gateway.

Forge is an extension of Thinkube Control: Control lists it in its
navigation and draws its pages. It needs the Datasets extension
(`depends: [datasets]` in `thinkube.yaml`).

## What it does

- **New fine-tune** (`/runs/new`): the base model, a dataset with its column
  mapping and the split to train on, an evaluation split of the dataset or a
  share of the training split held out (never the training split itself),
  the method (LoRA, Meep²'s default; QLoRA; or LoRA on FP8 weights, which
  the plan refuses on a GPU without FP8 tensor cores), the node, steps or
  epochs, its priority, and when the run trains. The jobs' memory and CPU
  cores are auto mode's: the plan job asks for the base model's 16-bit
  weights plus 8 GB; the training job for the memory Meep²'s plan states
  (`memory_request_bytes`: the training's peak and Meep²'s margin), with
  Meep²'s reason on the plan view; the node gives its default CPU cores. A
  plan that cannot size the training says why, and the run is queued only
  once a person sets its memory. A person may set both under Advanced, and
  the plan view says whose they are. Forge builds a Meep² training job
  (`backend/app/services/runs.py`); Meep²'s auto mode decides the rest. The
  page shows the first example as the base model's chat template renders it.
- **Plan**: before a run trains, a short job on its node asks Meep²
  for its plan, and the page shows each decision with its reason: how the
  model loads, the attention, the batches, the checkpointed layers, the
  micro-batch, the learning rate, the schedule, and the time the training
  will take. A person may set any decision of Meep²'s recipe or overrides
  and plan again; the plan then shows the choice as theirs, beside auto
  mode's.
- **The training queue**: each node has a queue. A run is planning, planned,
  queued, running, stopping, paused, completed, failed or cancelled, has a
  position, trains "now" (as soon as the node is free) or in the node's
  "slots", and may go on across slots. Runs are paused, resumed, cancelled
  and reordered from the Runs page or the API (`/runs/{id}/pause`,
  `/resume`, `/cancel`, `/queue/{node}/order`). A failed run keeps its error
  and the queue goes on.
- **Tags**: a run, a fine-tune or a task, carries names and values that link
  it with what it is for, such as `experiment` or `claim`. They are given
  when the run is created (`tags`) or attached later (`PUT /runs/{id}/tags`,
  `forge_tag_run`); `GET /runs?tag=name=value` finds the runs that carry
  one, and the Runs page shows them.
- **A task's pod** is given the LLM Gateway's address as `LLM_GATEWAY_URL`
  and, as `THINKUBE_API_TOKEN`, the token the queue works with, so a task
  that calls a model can.
- **Priorities** (`backend/app/services/priorities.py`): a run has a
  priority, 1 (highest) to 5 (lowest), set when it is created and changed
  while it waits; a run given none gets the Scheduler's priority of Forge
  runs, which a person sets on the Scheduler's Priorities page. Its jobs carry
  it to the Scheduler, which weighs them against the other work on the node:
  notebook servers, served models, apps. Among Forge's own runs on a node, a
  run of a higher or the same priority makes it wait ("waiting for run … on
  tkspark, priority 2"), and one of a lower priority is paused at its next
  step with a checkpoint ("paused, preempted by …") and resumed by the queue
  later. A training the Scheduler stops for work of a higher priority outside
  Forge stays running, says so in its waiting line, and goes on from its last
  checkpoint when its turn comes again.
- **The worker** (`backend/app/services/worker.py`): a background task of
  Forge's backend decides, every 15 seconds and from Forge's database alone,
  what each node does: by priority, plans before trainings, then the queue in
  order. It keeps nothing in memory, so a new pod re-attaches to the running
  jobs. It gives the runs' jobs to the Scheduler, and their scripts call
  Thinkube Control with a Control API token of the person who connected it
  on the Slots page.
- **Slots** (`/slots`): a node and a recurring window, such as every night
  from 23:00 to 07:00, in a time zone (Europe/Madrid unless another is
  given); a window may cross midnight. At a slot's start the worker starts
  the node's runs for slots, each only if its time left fits the slot,
  unless it goes on across slots. At the slot's end the running training
  stops at its next step with a checkpoint; the run goes on from its
  checkpoint in the next slot. The slots of two nodes are independent.
- **A training**: a job of the Scheduler on the node
  (`backend/app/services/scheduled.py`), in the `meepsquared` Python
  environment, with the GPUs its job asks for, alone, running
  `backend/app/training/meepsquared_notebook.py` with its parameters in
  `FORGE_PARAMETERS`. It writes the dataset's split as
  examples, the job as YAML, and runs
  `python -m meepsquared train job.yaml --status-file … --stop-file … [--resume]`.
  Its folder is `~/thinkube/models/forge/<run>`: the data, the job, the
  status file and Meep²'s output (checkpoints and the adapter). The training
  logs its training and evaluation loss to the MLflow experiment `forge`, in
  an MLflow run Forge creates for it; Meep² evaluates about ten times over
  the run unless `eval_every` is set. Its plan, its status file and its stop file are shared with Forge among
  that MLflow run's artifacts, under `forge/`
  (`backend/app/services/runfiles.py`).
- **Models**: a run that registers merges the adapter into the base weights
  and publishes the result as `forge/<name of the run>`, which the LLM
  Gateway loads on vLLM. An export converts it to a GGUF file with
  llama.cpp's converter (`backend/app/training/export_notebook.py`) and
  publishes `forge/<name>-gguf` for llama.cpp.
- **NVFP4 export**: a registered model is quantized to NVFP4 with NVIDIA's
  TensorRT Model Optimizer (`nvidia-modelopt`), calibrated on rows of the
  split the run trained on (512 by default, drawn with a fixed seed), by a
  job of the Scheduler with one GPU of a Blackwell node
  (`backend/app/training/nvfp4_notebook.py`). The checkpoint is ModelOpt's
  Hugging Face format, which vLLM loads as `modelopt_fp4`, and is published
  as `forge/<name>-nvfp4`; the LLM Gateway serves it on vLLM only on GPUs of
  compute capability 10.0 and higher, such as the DGX Spark's. The export
  runs in the `nvfp4` Python environment, a Jupyter environment with
  `nvidia-modelopt[hf]==0.47.0`, `transformers>=4.57,<5.15`, `accelerate`,
  `pandas`, `pyarrow`, `mlflow`, `boto3` and `kubernetes==36.0.3` (the last two for uploading the
  checkpoint); Forge refuses it while that environment
  does not exist, and on a node whose GPU does not compute NVFP4.
- **Chat**: the run's page asks the same question to the fine-tuned model,
  its base model and its GGUF and NVFP4 exports, side by side.
- **Compare** (`/compare`): the training or evaluation loss of the runs
  chosen on the Runs page, on one chart.
- **Calls to the platform**: Forge calls Thinkube Control
  (`control.<domain>`) with the signed-in user's token, or the worker's
  token; the Datasets backend (`datasets-backend:8000`), the LLM Gateway
  (`llm.<domain>`), and MLflow (`experiments.<domain>`) with the MLflow
  account the platform gives every app.

## Mill's task runs

A task of Mill's tasks repository can wait in a node's queue like a
fine-tune: Mill creates it with `POST /api/v1/tasks/runs` (the file, the
task's inputs, outputs and settings, the node, its GPUs, memory and CPU
cores, and a priority). It is queued at once; it has no preprocessing, no
plan and no slots.

When its turn comes the worker starts a Kubernetes job for it in the
namespace `mill-runs` (`backend/app/services/taskpods.py`): on the run's
node, from the AI base image, with the shared file system mounted where
Mill's main pod has it, running `mill run`. The run reports its progress,
samples and log to Mill through NATS, under the id in its `task.mill_run`;
Forge follows only the job's state, and says why a pod waits or failed.

A task that asks for no GPU takes its node's CPU lane: it waits only for the
other tasks without a GPU on that node, and it neither stops GPU work nor
waits for it. A task with GPUs waits in the node's GPU lane with the
fine-tunes, among Forge's runs, by the rules above.

A task has no checkpoint. Pausing it, or a run of a higher priority that
needs the node, deletes its job: the task ends as stopped in Mill. It
starts again from the beginning when it resumes, as a new run in Mill.

The namespace `mill-runs` and its claim of the shared file system are made
by the playbook `thinkube-control/14_configure_mill_runs.yaml` of thinkube.
Forge's backend may create, read and delete jobs and list pods
(`access` in `thinkube.yaml`).

## Known limits

- **A task's pod still asks Kubernetes for its GPUs.** Until Mill gives its
  tasks to the Scheduler itself (step 5 of the Scheduler's plan), a task with
  GPUs waits only for Forge's runs; the Scheduler counts its pod as work it
  did not start, by what its GPUs hold.
- **A training stopped by the Scheduler is killed, not paused.** Forge pauses
  its own runs at a step boundary through Meep²'s stop file. The Scheduler
  stops a job for work of a higher priority outside Forge by deleting its pod
  (SIGTERM, then STOP_SECONDS of `backend/app/services/scheduled.py`); the
  training goes on from its last checkpoint, and the steps since it are done
  again.

## Technology Stack

- **Frontend**: React 19 + TypeScript, Vite, Tailwind CSS 4
- **UI**: [thinkube-style](https://github.com/thinkube/thinkube-style) — Tk components built on shadcn/ui and Radix
- **State**: Zustand
- **Routing**: React Router
- **i18n**: react-i18next (English, Spanish, Catalan)
- **Backend**: FastAPI with Python 3.12
- **Database**: PostgreSQL with Alembic migrations
- **Authentication**: Keycloak (OAuth2/OIDC)

## Layout

The app uses `TkAppLayout` from thinkube-style: a collapsible left sidebar with grouped
navigation, and a top bar carrying the page title, the language menu, the theme toggle,
and the user menu. This is the same shell Thinkube Control uses, so applications built
from this template look like the rest of the platform.

## Variables the browser can read

The frontend is a static bundle, built before the deployment exists, so it
cannot read the container's environment the way the backend does. `thinkube.yaml`
names the variables it may show instead:

```yaml
containers:
  - name: frontend
    publicEnv:
      - APP_TITLE
```

The platform passes those names to the container as `PUBLIC_ENV_VARS`.
`frontend/public-config.sh` runs before nginx starts, writes the named variables
into `config.js`, and `index.html` loads it before the bundle. Read one with:

```ts
import { publicValue } from '@/lib/publicConfig'

const title = publicValue('APP_TITLE') || t('app.title')
```

Any variable the container has can be named: one the platform sets, one from
`spec.env`, one wired from `dependencies`, or a parameter answered at deploy
time. The list holds names, never values — so the values belong to the
deployment, and a template published from this app carries neither.

**This is an allow-list, and it is the only thing standing between a variable
and the public.** The container receives the whole environment, including
`POSTGRES_PASSWORD` and `KEYCLOAK_CLIENT_SECRET`. Name a variable here only when
it is safe for anyone who opens the application to read it.

Locally there is no platform, so `frontend/public/config.js` publishes nothing
and every lookup falls back to the default in the code.

## Adding a page

1. Create the component under `frontend/src/pages/`.
2. Add its route to `frontend/src/App.tsx`.
3. Add a navigation entry to `navigationItems` and its path to `NAV_ROUTES` in the same file.
4. Add the labels to all three files in `frontend/src/locales/`.

Build the UI from `thinkube-style` components (`TkCard`, `TkButton`, `TkTable`, …) rather
than raw HTML, so the app stays consistent with the rest of the platform.

## Database Migrations

Alembic runs the migrations when the backend starts (`backend/start.sh`
runs `alembic upgrade head`). A change to the models needs a migration in
`backend/alembic/versions/`, committed with it:

```bash
cd backend && alembic revision --autogenerate -m "what changed"
```

`tests/test_migrations.py` fails the build when the models and the
migrations differ, so a model change without its migration never reaches
the cluster.

## Working on it

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dev server runs on port 3000 and proxies `/api` to `http://localhost:8000`.

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app:app --reload
```

### Tests

Each container has a `run_tests.sh`. Without arguments it runs the whole
suite, as CI does before every build: pytest for the backend, Vitest and
React Testing Library for the frontend. Lint and type checks are not part
of that run; run them yourself (`npm run lint`, `npm run typecheck`,
`flake8`, `black`). With a file argument it runs that one test file and
nothing else:

```bash
cd backend && ./run_tests.sh tests/test_datasets.py
cd frontend && ./run_tests.sh src/components/__tests__/LossChart.test.tsx
```

The script builds the test environment in one place: the settings in `.env.test`, and the database credentials the platform hands a test container (`ADMIN_USERNAME`, `ADMIN_PASSWORD`). `thinkube.yaml` declares both modes under each container's `test`, and Thinkube Tandem runs single files through the same script.

The backend tests run on a schema of their own in the app's test database: each run creates it, applies the migrations to it as a deploy does, and drops it at the end. The tests see the schema production has, every run checks the migrations, and runs at the same time never share tables.

## License

The code that came from the template is MIT (see [LICENSE](LICENSE)); the files that carry `SPDX-License-Identifier: Apache-2.0` are Apache 2.0.

Copyright Alejandro Martínez Corriá and the Thinkube contributors
