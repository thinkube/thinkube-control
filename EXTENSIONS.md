# Thinkube Control and its extensions

Thinkube Control is being divided into a core and extensions. An extension is
a part of Control that is built and deployed on its own
(`backend/app/services/extensions.py`, and `spec.extension` in the
thinkube.yaml reference). This file says which part is where, the rules an
extension follows, and how a part is moved.

The source of every extension is a folder of this repository,
`extensions/<name>`. An extension is still deployed on its own: it has its own
pods, build, tests and Gitea repository, and a deploy of Control does not
touch it.

## Where each part is

| Part | Where | State |
|---|---|---|
| Login, API tokens, secrets | Control | stays |
| Service discovery, dashboard, health checks | Control | stays |
| Templates and deploys, pipeline view, run queue | Control | stays |
| Extension registry and proxy (`/x/<name>`) | Control | stays |
| Optional components, News & Fixes, docs search | Control | stays |
| Cluster nodes | Control | stays |
| Datasets | extension `datasets` | done |
| Forge | extension `forge` | done |
| Storage | extension `control-storage` | done |
| Images (Harbor images, custom images, Jupyter images, mirror) | extension `control-images` | done |
| Models (catalogue, mirrors, fine-tuned models) | Control | to move, as `control-models` |
| LLM Gateway (`api/llm`, `services/llm_*`) | Control | to move, as `control-llm`, after Models |
| Notebooks (kernels, servers, notebook jobs, JupyterHub config, training mode, GPU metrics) | Control | to move, as `control-notebooks`, last |

The order is the order of the table. Each move ends before the next begins.

## Rules

- **Name.** A part moved out of Control is named `control-<part>`. An app is
  served at `<name>.<domain>`, and `storage.`, `notebooks.` and `llm.` are
  platform services; a deploy whose address is taken is refused
  (`backend/app/services/hostname_claims.py`). The title in the navigation
  is the part's plain name.
- **Cluster access.** What a container needs from the cluster beyond its own
  objects it declares in thinkube.yaml: `access` (Kubernetes rules) and
  `filesystems` (shared file systems at their root, or the folder
  `dockerfiles` of the IDE's home, which keeps the pod on the control plane
  node). Name each resource and
  verb; `*` is refused.
- **Budget.** Every extension runs in Control's namespace, whose budget is
  the tier `control` of the role `thinkube_namespace` in thinkube (16 CPU in
  limits). An extension takes 1.5 (a medium backend, a small frontend).
- **Long work.** The proxy answers within 300 s and carries no WebSocket. A
  run that takes longer is started on the server and answers at once with an
  id; the page polls it (`app/services/detached.py` is Control's form of it).
- **Calls between extensions.** A backend calls another extension's backend
  at its Service in Control's namespace (`http://<name>-backend:8000`), with
  the user's token. A backend calls Control at `CONTROL_API_URL`.
- **Agents.** Each extension has its own MCP server at `/api/v1/mcp`. Its
  tools are named `<part>_<verb>...` (`storage_get`, `forge_list_runs`). The
  server is listed in `ansible/roles/agent_clients/vars/main.yaml` of
  thinkube, with its read-only tools.
- **Sign-in.** A Thinkube Control API token signs in to an extension:
  the extension asks Control whose it is (`control_token_user` in an
  extension's `app/core/security.py`).
- **Source.** The folder `extensions/<name>` holds the extension as it is
  built: no template variables. A deploy copies the folder over the checkout
  `~/apps/<name>`, removes the files the folder no longer has, and pushes to
  the Gitea repository `thinkube-deployments/<name>`, which builds it
  (`scripts/template_folder.py`). The checkout and the Gitea repository are
  deployed copies; a change is made in the folder. The extension has the
  folder's name.
  A deploy generates nothing into the source: the Alembic migrations are
  written by hand in the folder, like the rest.
- **Version.** An extension is read from this repository at the ref the
  cluster deploys platform templates from: the head of the branch it follows
  when it develops the platform, the newest release tag of the platform's
  version otherwise. Extensions are tagged and released with Control.
- **No second path.** The move removes the part from Control in the change
  that follows the extension's first working deploy. Control keeps no copy
  of a moved route, page or tool.

## How an extension is deployed

Push the folder to GitHub, then ask Control to deploy it:

- the MCP tool `deploy_extensions` with `names`: `["<name>"]`
  (`POST /api/v1/templates/extensions/deploy`), or
- the playbook of thinkube
  `ansible/40_thinkube/core/thinkube-control/15_deploy_extensions.yaml -e extensions=<name>`,
  which also waits for the build to end.

Without a name, every extension of the repository is deployed; one that is
not on the cluster is created. The install runs the playbook this way after
it deploys Control (`00_install.yaml`).

The answer gives a deployment id per extension; `get_deployment_status`
follows a deploy to the end of its build. Under it is a deploy request with
`template_path`: `deploy_template` and `redeploy_template` take the same field
for any repository.

## How a part is moved

1. Create the folder `extensions/control-<part>` from the folder of an
   existing extension of the same shape: `thinkube.yaml`, `manifest.yaml`,
   `backend/`, `frontend/`, `CLAUDE.md`, `README.md`.
2. Move the routes, services, models and tests into its backend, the page
   and its store into its frontend.
3. Declare `access` and `filesystems` if it needs them. Set the title, the
   icon and the navigation entries in `spec.extension`.
4. If the part has tables in `thinkube_control`, write the copy of their
   rows into the extension's database, and run it once.
5. Add its MCP server and tools, and its entry in `agent_clients`.
6. Push, and deploy the folder. The deploy builds and tests it. Use the page
   inside Control.
7. Switch the callers (Forge, Tandem, the docs) to the extension.
8. Remove the part from Control, deploy Control with `12_deploy_dev.yaml`,
   and check what Control still needs from it.

## What Control still holds for a moved part

- **Storage.** Control mounts `thinkube-shared` and keeps
  `app/services/shared_folder.py`, which finds the notebooks folder for a
  notebook job. Both leave with Notebooks.
  A cluster that ran Control before the move still has the claim
  `storage-mlflow` and the volume `thinkube-control-storage-mlflow`, which
  nothing mounts any more; the deploy does not remove them. Delete the claim,
  then the volume. Both only point at the file system's root, with reclaim
  policy Retain: the data stays.

- **Images.** Control keeps `app/services/harbor_client.py`, which the
  delete of an app uses to remove its repositories from Harbor. A cluster
  that ran Control before the move still has the tables `container_images`,
  `image_mirror_jobs` and `custom_image_builds` in `thinkube_control`. The
  extension's `python -m app.jobs.copy_from_control`, run once in its
  backend's pod, copies their rows; after that nothing reads them.

## Open

- **Top bar.** An extension draws only its own pages. Models has an
  indicator in Control's top bar (`DownloadIndicator`); the host needs a
  place for it before Models moves.
