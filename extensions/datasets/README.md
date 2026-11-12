# Thinkube Datasets

Training data for Thinkube Forge: files you upload and datasets from the
Hugging Face Hub, kept as Parquet files in Thinkube Experiments (MLflow).

Datasets is an extension of Thinkube Control: Control lists it in its
navigation and draws its pages. Other apps in Control's namespace read it at
`http://datasets-backend:8000/api/v1/datasets`.

## What it does

- **Upload**: a `.jsonl`, `.json`, `.csv` or `.parquet` file of up to 2 GB
  becomes the split `train` (`backend/app/services/tables.py`, with DuckDB).
- **Import from Hugging Face**: a public dataset of the Hub, as the Parquet
  files the Hub publishes for it, at most 4 GB
  (`backend/app/services/huggingface.py`). The import runs as an Argo
  workflow in the backend's image (`backend/app/jobs/hub_import.py`,
  `backend/app/services/workflows.py`), so a restart or a deploy of Datasets
  does not stop it: the backend records the workflow, follows it, and
  follows it again when it starts. Deleting an importing dataset cancels
  its workflow.
- **Versions**: a dataset has numbered versions, 1, 2, ... An upload or an
  import makes version 1. Each version records its lineage: the version it
  was made from (none for an upload or an import) and what made it
  (`import`, `upload`, or a recipe, kept with its document). A version's
  files, splits and row counts do not change once it is ready
  (`backend/app/models/dataset.py`). The API lists them at
  `GET /api/v1/datasets/{id}/versions`; every other route acts on the
  version given as `version`, or on the latest ready version.
- **Storage**: each version is an MLflow run whose artifacts are its Parquet
  files, one per split, at `data/<split>.parquet`
  (`backend/app/services/storage.py`). A notebook reads them from
  `~/thinkube/mlflow/artifacts/`.
- **Row identity and origin**: every split file carries `_row`, the row's
  id, unique within the dataset across its versions, and `_origin`, a
  struct of `kind` (`real` or `synthetic`), `method`, `model`,
  `model_version`, `source_version` and `source_row`. An import numbers its
  rows; a later version keeps the id of each row it keeps. They are not
  data: the columns, the mapping and the examples leave them out
  (`backend/app/services/tables.py`). At start, `backend/app/identify.py`
  rewrites in place the files of any version that lacks them.
- **Rows and mapping**: the dataset page lists the versions and shows the
  rows of one, and maps its columns to examples: chat messages, ShareGPT
  conversations, prompt and completion, instruction and output, or plain
  text (`backend/app/services/mapping.py`). A version with a mapping can be
  trained on.
- **For training**: `GET /api/v1/datasets/{id}/training?version=N` gives a
  trainer the files of each split of a version, the mapping, and the
  version it describes.

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
uvicorn app.main:app --reload
```

### Tests

Each container has a `run_tests.sh`. Without arguments it runs the whole
suite, as CI does before every build: pytest for the backend, Vitest and
React Testing Library for the frontend. Lint and type checks are not part
of that run; run them yourself (`npm run lint`, `npm run typecheck`,
`flake8`, `black`). With a file argument it runs that one test file and
nothing else:

```bash
cd backend && ./run_tests.sh tests/test_mapping.py
cd frontend && ./run_tests.sh src/lib/__tests__/datasets.test.ts
```

The script builds the test environment in one place: the settings in `.env.test`, and the database credentials the platform hands a test container (`ADMIN_USERNAME`, `ADMIN_PASSWORD`). `thinkube.yaml` declares both modes under each container's `test`, and Thinkube Tandem runs single files through the same script.

The backend tests run on a schema of their own in the app's test database: each run creates it, applies the migrations to it as a deploy does, and drops it at the end. The tests see the schema production has, every run checks the migrations, and runs at the same time never share tables.

## License

MIT, see [LICENSE](LICENSE).

Copyright Alejandro Martínez Corriá and the Thinkube contributors
