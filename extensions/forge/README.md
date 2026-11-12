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
- **Plan**: before a run trains, a short notebook job on its node asks Meep²
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
- **Priorities** (`/priorities`, `backend/app/services/priorities.py`):
  every piece of work on a GPU node has a priority, 1 (highest) to 5
  (lowest). When a run wants its node, work with a higher or the same
  priority makes it wait, and the run says what it waits for ("waiting for
  notebook server tkspark on tkspark, priority 1"); work with a lower
  priority is stopped to make room, at any time, in the gentlest way it has:
  a Forge run is paused at its next step with a checkpoint ("paused,
  preempted by …") and resumed by the queue later; a notebook job is
  cancelled and a person's notebook server is stopped, through Control's
  API; served models and apps are stopped by Control's training mode and come
  back when it ends. A run has its own priority, set when it is created and
  changed while it waits. The other kinds have the priority of their kind,
  which a person sets on the Priorities page: notebook servers 1, notebook
  jobs 2, Forge runs 3 (the priority a new run gets), served models 4, apps
  and services 5. Training mode reserves one node at a time: when runs on two
  nodes want it, the one with the higher priority takes it first, and a run
  never takes it from another node.
- **Two nodes' slots**: training mode reserves one node at a time. When runs
  on two nodes want it, the run with the higher priority takes it first; at
  equal priority, the slot that started first. The other waits until it ends.
- **The worker** (`backend/app/services/worker.py`): a background task of
  Forge's backend decides, every 15 seconds and from Forge's database alone,
  what each node does: by priority, plans before trainings, then the queue in
  order. It keeps nothing in memory, so a new pod re-attaches to the running
  jobs. It calls Thinkube Control with a Control API token of the person who
  connected it on the Slots page.
- **Slots** (`/slots`): a node and a recurring window, such as every night
  from 23:00 to 07:00, in a time zone (Europe/Madrid unless another is
  given); a window may cross midnight. At a slot's start the worker turns
  Control's training mode on for the node (its served models unload and its
  other workloads stop) and starts the node's runs for slots, each only if
  its time left fits the slot, unless it goes on across slots. At the slot's
  end the running training stops at its next step with a checkpoint, and
  training mode is turned off; the run goes on from its checkpoint in the
  next slot. Control reserves one node at a time, so when the slots of two
  nodes overlap, the one that started first holds training mode and the
  other starts when it ends.
- **A training**: a notebook job of Thinkube Control on the node, on the
  `meepsquared` kernel, with one GPU, built from
  `backend/app/training/meepsquared_notebook.py`. It writes the dataset's split as
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
  notebook job with one GPU of a Blackwell node
  (`backend/app/training/nvfp4_notebook.py`). The checkpoint is ModelOpt's
  Hugging Face format, which vLLM loads as `modelopt_fp4`, and is published
  as `forge/<name>-nvfp4`; the LLM Gateway serves it on vLLM only on GPUs of
  compute capability 10.0 and higher, such as the DGX Spark's. The export
  runs on the `nvfp4` kernel, a Jupyter environment with
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

## Known limits

- **What runs on a node is inferred.** Control has no call that lists the
  work on a node. Forge infers it from Control's notebook servers, notebook
  jobs, GPU status (the served models) and services (the apps that hold a
  GPU on the node). A served model's own service may be counted as an app
  too. The stopping itself is exact: training mode stops what is on the
  node.
- **Forge cannot set a notebook job's grace period.** The job's pod is
  Control's. A training normally stops through Meep²'s stop file, at a step
  boundary; a SIGTERM reaches it only when its pod is killed, and then the
  step in progress may not finish.

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
