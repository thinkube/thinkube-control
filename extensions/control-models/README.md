# Models

The Models part of Thinkube Control, as an extension: Control lists it in its
navigation, draws its page inside its own layout, and shows the number of
running mirrors in its top bar.

## What it does

- **Catalogue.** The models the platform knows: `models.json` of
  `thinkube/thinkube-metadata`, at the branch the cluster follows
  (`THINKUBE_BRANCH`), merged with `models.json` of the user's metadata
  repository, `<GITHUB_USERNAME>/<GITHUB_USERNAME>-metadata`, at `main`. A user
  entry with the same id replaces the platform's. Each model says whether it
  is mirrored (`is_downloaded`).
- **Mirrors.** A mirror copies a catalogue model into MLflow: from Hugging
  Face (weights uploaded to MLflow's store on JuiceFS) or from the Ollama
  library (pulled into Ollama, registered in MLflow). It runs as an Argo
  Workflow in the namespace `argo` (`backend/app/services/model_downloader.py`),
  in the platform's `model-mirror` image. The call that starts it answers at
  once with the workflow's name; the backend follows every running mirror
  from its workflow every 10 seconds (`services/follow.py`).
- **Fine-tuned models.** `POST /api/v1/models/finetuned` publishes a
  fine-tune whose weights are already a model version in MLflow: it writes
  the entry to the user's metadata repository, records the model as
  mirrored, and asks Control's LLM Gateway to read the catalogue again.

## Who reads it

- **Control's LLM Gateway** reads the catalogue with each model's mirror
  state at `http://control-models-backend:8000/internal/models` (`?fresh=true`
  reads the metadata repositories again). The route is outside `/api`, so
  neither the extension's address nor Control's proxy reaches it: only
  pods of the cluster do. It needs no sign-in.
- **Control's template forms** read the same route for a parameter whose
  choices are catalogue models.
- **Forge, its notebooks and `thinkube_models` in the notebook images** call
  the API through Control's proxy, `/api/v1/x/control-models/api/models/...`,
  or at the Service, with the user's token.

## Settings

- `THINKUBE_BRANCH` and `GITHUB_USERNAME`: given by Control's
  `deploy_extensions` (`spec.env` of `thinkube.yaml`). Without
  `GITHUB_USERNAME` there is no user catalogue.
- `GITHUB_TOKEN`: the Secret `github-token` of Control's namespace.
- MLflow: the account every app receives (`MLFLOW_*`).

## Records from Control

A cluster that ran Models inside Control has its mirror jobs in the table
`model_mirror_jobs` of `thinkube_control`. Copy them once, in the backend's
pod, after the first deploy:

```bash
python -m app.jobs.copy_from_control
```

A job still running keeps its status; its workflow is the same and the
extension follows it.
