# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""The prompts thinkube-control's MCP server offers.

Each prompt guides a client such as Claude Code through one multi-step task
with the MCP tools of this server: the tools in order, the rules the tools do
not show in their own descriptions, and what to report back. The tool names
in the texts are operation ids of this API, written between backticks, and
the tests check that every one of them is exposed.

A client shows a prompt as a slash command, for example
/mcp__thinkube-control__serve-model, and its text arrives as the user's
message with the arguments filled in.
"""

from typing import List

from fastapi_mcp_extended import PromptDefinition
from fastapi_mcp_extended.types import PromptArgument, PromptMessage

# Placeholders fill from the prompt's arguments; every other brace in a text
# is literal, so the texts are plain strings, not format strings.

SERVE_MODEL = """\
Serve the model {{model_id}} through the Thinkube LLM gateway and leave it ready to call.
Target node: {{node}} (when this says "choose", pick a node in step 4).

Rules that the tools do not state:
- A vLLM, TensorRT-LLM or text-embeddings pod serves ONE model per node. It is created on
  the node when a model is loaded there and deleted when that model is unloaded. Loading a
  second model of the same type on that node is refused with "already serves ...";
  it is never replaced. Ollama is different: one Ollama pod per node serves several models.
- `get_llm_load_options` lists in compatible_backends only the pods running NOW. An empty
  list, or a node that has no pod, does not mean the node cannot serve the model: the load
  creates the pod there. Choose by gpu_nodes, not by compatible_backends.
- `load_llm_model` answers HTTP 200 whether it accepts or refuses. Read the state in its
  answer: "loading" means accepted; any other state means refused, and the message says why.
- Never unload a model you did not load in this task. Other people may be using it.

Steps:
1. `get_model_catalog`: find the model. Note is_downloaded, server_type, size and gated.
   A model not in the catalog cannot be mirrored or served; stop and say so.
2. If is_downloaded is false, `submit_model_mirror` with model_id. It answers with
   workflow_id; poll `get_mirror_status` with that workflow_id (not job_id) every 30 seconds
   until is_complete or is_failed (error_message says why). A mirror pulls the weights from
   Hugging Face into MLflow and takes minutes to hours by size. HTTP 409 means a mirror of
   this model is already running: follow it with `list_mirror_jobs`. When it succeeds, call
   `refresh_llm_registry`; the registry otherwise notices the new mirror on its next refresh
   (every 60 seconds).
3. `get_llm_models`: the model must be listed with state "deployable" (mirrored, not loaded).
   "available" means it is already served: skip to step 7. "loading" means someone else is
   loading it: wait with step 6. The model's server_type names its backend: vllm,
   tensorrt-llm and text-embeddings are one-model pods; ollama is the shared pod.
   The backend type must appear in installed_backend_types. If it does not, install it with
   `install_optional_component` (component names: vllm, tensorrt, text-embeddings, ollama)
   and poll `get_deployment_status` with the returned deployment_id until success.
   The backend must also be enabled: `list_services_minimal` with service_type "component"
   (or "optional" for ollama) shows is_enabled; a disabled backend refuses every placement.
   Enable it with `toggle_service` only if the user agrees.
4. `get_llm_load_options` with model_id. Read estimated_memory_gb (for the default context,
   the largest of 32768, 16384 and 8192 the model allows) and gpu_nodes. A node fits when
   ai_remaining_gb is at least estimated_memory_gb and available_slots is above 0. For a
   one-model backend, a node whose allocations already hold a backend_id of the same type
   (for example vllm-<node>) is taken; pick another node or stop. Say which node you chose
   and why; if the target node was given, check it against these rules.
5. `load_llm_model` with model_id and node. Optional: backend (a type such as vllm, or a
   backend id such as vllm-tkspark, which also names the node) and max_context_length
   (otherwise the largest of 32768, 16384, 8192 that fits the node). If the answer's state
   is not "loading", report the message and stop; do not retry on another node without
   saying so.
6. Poll `get_llm_model_status` with model_id every 30 seconds. state "available" with a
   backend_id means served. state "deployable" with last_error means the load failed;
   report last_error. A load can stay "loading" for many minutes (image pull, weights, engine
   start); the registry gives up after 32 minutes. `get_llm_backends` shows the pod once it
   answers, with the models it serves.
7. Confirm the route with `resolve_llm_model` (model = the model id). It answers with the
   backend and serving_name, or with an error when the gateway cannot route it yet.
8. Tell the user how to call it. Gateway: https://llm.{{domain}}
   OpenAI style: POST /v1/chat/completions with "model": "{{model_id}}";
   /v1/embeddings for a text-embeddings model. Anthropic style: POST /v1/messages.
   Authorization: Bearer <thinkube-control API token or Keycloak token>; x-api-key also
   works. The model field takes the model id; its catalog name or the last part of the id
   also resolve.
9. Only when the user asked to stop serving, or the model was loaded for a test that is
   over: `unload_llm_model` with model_id. It frees the GPU memory and deletes a one-model
   pod that is now empty.

Report: model id, state, backend_id and node, the context length loaded, the memory it
took, the gateway URL with one example request, and any refusal with its message.
"""

DEPLOY_APP = """\
Deploy the application {{app_name}} from the template {{template}} and leave it running.

Rules that the tools do not state:
- The app name is the namespace and the hostname: the app answers at
  https://{{app_name}}.{{domain}}. Use lowercase letters, digits and hyphens, starting with
  a letter. Names of platform services are reserved and refused.
- `deploy_template` takes the template's GitHub URL (the url from `list_templates`), the
  name as template_name, and the template's parameters as variables. Templates whose
  deployment_type is "component" are not listed; components install with
  `install_optional_component`.
- A deploy runs from a queue, one run at a time, detached from the call. Its success means
  the image was built and pushed, and ArgoCD received the manifests; the pods come up right
  after.
- This guide creates an app. To ship code changes to an app that exists, commit and push to
  its Gitea repository: the push is the deploy (build, tests, rollout).
  `get_commit_rollout` with the app and the commit says when it is live. Never deploy or
  redeploy the template to ship code.
- A name that belongs to an existing user app makes `deploy_template` answer status
  "conflict" with requires_confirmation; the message says what a redeploy replaces. A
  redeploy resets the checkout /home/thinkube/apps/<name> to Gitea's main, copies the
  template over it and regenerates k8s/; it refuses when the checkout has uncommitted
  changes or unpushed commits. It is the user's decision: confirm with them, then call
  `redeploy_template` with the same body. A name held by another kind of
  service is refused (HTTP 400). HTTP 409 means this deploy is already queued or running.
- Deploying a component's template again stops when the component runs commits pushed after
  its last template deploy (a developer's change, such as a new model feature); the failure
  lists them. Replacing them is the user's decision: confirm with them, then redeploy with
  the variable `_replace_developer_commits` set to true. The commits are kept on a
  developer-changes branch of the component's Gitea repository first.

Steps:
1. `list_templates`: find the template by name (or accept a GitHub URL). Note url,
   deployment_type (app or knative) and source (platform or user).
2. `get_template_metadata` with template_url: parameters (name, type, description, default,
   required, choices) and secrets (name, description, required).
3. Decide each parameter's value: the user's answer, else the default. Ask the user for a
   required parameter with no default; do not invent one. project_description, author_name,
   author_email and domain_name are filled by the server unless given.
4. Secrets: `list_secrets` shows the names the Secrets store holds. Every required secret of
   the template must exist before the deploy, or the run fails with "Secret X is required".
   Ask the user for a missing value and store it with `create_secret` (name, value,
   description). Never make up a secret value. The values reach the app as environment
   variables under their own names.
5. `deploy_template` with template_url, template_name and variables. It answers with
   deployment_id and queue_position, or with the conflict above.
6. Poll `get_deployment_status` with deployment_id every 20 seconds: "queued" shows
   queue_position; "pending" and "running" show current_step and steps_done; the end is
   "success", "failed" (reason says why) or "cancelled". `list_runs` shows the queue.
   `cancel_queued_run` removes a run that has not started; a running one cannot be
   cancelled from here.
7. On failure, `get_deployment_logs` with deployment_id (offset and limit page through
   the log); `get_deployment_debug_logs` names the log files on disk and
   `download_debug_log` returns one. Report the failing step and its message; do not
   redeploy blindly.
8. On success, `list_services_minimal` with service_type "user_app" lists the app with its
   id; `get_service_details` with that id shows latest_health and pods_info. For a knative
   template, `get_knative_service` with namespace and name (both the app name) shows the
   Knative service, which scales to zero when idle.

Report: the app name, its URL, the deployment id and final status, the parameters and
secrets used (names only, never values), and what failed if anything.
"""

PUBLISH_TEMPLATE = """\
Publish the deployed application {{app_name}} as the template {{template_name}} so that
others can deploy it from the Templates catalog.
Description for the catalog: {{description}}
Private GitHub repository: {{private}}

Rules that the tools do not state:
- The source is the app's checkout at /home/thinkube/apps/{{app_name}}, the one code-server
  edits. It must hold thinkube.yaml or manifest.yaml. Only a template with manifest.yaml
  can be deployed by `get_template_metadata` and `deploy_template`; an app without one is
  published but cannot be deployed from the catalog.
- Publishing creates or reuses the repository github.com/<GITHUB_USERNAME>/{{template_name}}
  and force-pushes its main branch: an existing repository with that name is overwritten.
  Check with the user before reusing a name.
- .git, k8s/, .copier-answers.yml, regenerate-manifests.sh, node_modules, .venv and caches
  are left out. Generated manifests are platform-specific and are regenerated on deploy.
- The entry lands in the user's metadata repository <GITHUB_USERNAME>-metadata
  (repositories.json), and `list_templates` shows it with source "user" right away.
- The server needs GITHUB_TOKEN and GITHUB_USERNAME; without them the publish is refused
  with HTTP 400 and the message names the missing one.

Steps:
1. `list_deployed_apps`: the app must be listed; note has_manifest_yaml and
   deployment_type.
2. `list_templates`: if {{template_name}} is already a template, say so and confirm the
   overwrite with the user before continuing.
3. `publish_template` with app_name, template_name, description, tags (a short list of
   words, or none) and private ({{private}}).
4. `list_templates` again: the new entry appears with source "user".
5. `get_template_metadata` with the new repository URL: it answers with the template's
   parameters when manifest.yaml is present, or HTTP 404 when it is not.

Report: the repository URL, the template name as it appears in the catalog, whether it can
be deployed from the catalog, and the refusal message if the publish was refused.
"""

BUILD_NOTEBOOK_ENV = """\
Build the notebook environment {{name}} (a Python virtualenv that Thinkube Notebooks offers
as a kernel) and make it usable from a notebook.
Packages to install: {{packages}}
Template to start from: {{parent_template}} (fine-tuning, agent-dev, or none)

Rules that the tools do not state:
- Only custom venvs are built here. The built-in venvs fine-tuning and agent-dev come from
  the venvs release that notebook servers download; `build_jupyter_venv` refuses them, and
  their names are refused as custom names. A template is a starting point: its package list
  is copied into the new venv, then the extra packages are added.
- The name starts with a letter and holds only letters, digits, hyphens and underscores.
- A build runs as a Kubernetes Job on a GPU node for every architecture of the cluster, then
  the venv is copied to the other nodes. It takes minutes and shows no output while pip
  installs; the answer of `build_jupyter_venv` comes at once.
- A venv is registered as a kernel named after it when a notebook server starts. A server
  already running does not see a venv built after it started: stop and start it.
- The package list of an existing venv cannot be changed from here: delete the venv and
  create it again.

Steps:
1. `get_venv_templates`, and `get_venv_template_details` with the template id when a
   template is used: know what it brings before adding packages.
2. `list_jupyter_venvs` with include_templates true: the name must be free. An existing
   venv with the same name and status "success" is already built; say so and stop unless
   the user wants it rebuilt (`build_jupyter_venv` with force true).
3. `create_jupyter_venv` with name, packages (a list of pip requirements) and
   parent_template (omit it for no template). It answers with the venv's id.
4. `build_jupyter_venv` with that id (no body). It answers with status "building".
5. Poll `get_jupyter_venv` with the id every 30 seconds until status is "success" or
   "failed". output gives the reason, or the log path, and architectures_built the
   architectures it was built for.
6. On failure, `get_venv_build_logs` with the id names the log files; `download_venv_build_log`
   with the id and a filename returns one. Report the failing package and pip's message;
   a package that does not exist or does not build for the architecture fails the whole
   build.
7. On success, make the kernel visible: `jupyter_notebook_status` shows every node's
   server. For a node whose server is running, `stop_notebook_server` with the node, wait
   for state "stopped", then `start_notebook_server` with the node and wait for "running".
   `jupyter_list_kernels` with the node then lists the kernel {{name}} under installed.
8. To use it: `jupyter_use_notebook` with kernel_name "{{name}}", or `run_notebook_job` with
   kernel_name "{{name}}".

Report: the venv id and name, final status, architectures built, the kernel name, which
servers were restarted, and the failing package if the build failed.
"""

DIAGNOSE_SERVICE = """\
Diagnose the service {{service}} and say what is wrong before changing anything.

Rules that the tools do not state:
- Services are addressed by the id from `list_services_minimal`, not by name. Match the
  name (or display_name) there first. type tells what the service is: core (platform),
  optional (installed component with a playbook), component (inference backend), user_app
  (deployed from a template).
- Report findings first. `restart_service` and `toggle_service` change the cluster: do them
  only when the user agrees. Core services cannot be disabled; a disabled service cannot be
  restarted (enable it first); disabling a service that others depend on is refused.
- A user app's last deploy run is the most likely cause of a broken app. Deploy runs and
  component installs share one queue; a run in progress explains a service that is not
  there yet.

Steps:
1. `list_services_minimal`: find the service; note id, type and is_enabled.
2. `get_service_details` with the id: latest_health (status, status_code, error_message,
   checked_at), pods_info (each pod's status, ready, restart_count, node), resource_usage,
   recent_actions (who enabled, disabled or restarted it and when), url and dependencies.
   Pods that are not ready, restart counts that grow, and a health check that fails are the
   evidence; quote them.
3. By type:
   - user_app: `list_deployments` lists deploy runs newest first; take the latest one with
     this name and read `get_deployment_status` with its id (status, reason) and
     `get_deployment_logs` for the failing step. `list_runs` shows a run still queued or in
     progress. `get_commit_rollout` with the app and its last pushed commit says whether
     that push built and rolled out, and which image runs. A knative app:
     `get_knative_service` with namespace and name (both the app name), whose status and
     ready_condition say why a revision is not ready.
   - optional or component: `get_component_status` with the component name (pods running,
     failed) and `get_component_info` (installed, requirements, missing_requirements).
   - vllm, tensorrt, text-embeddings or ollama: also `get_llm_backends` (which pods answer
     and what they serve), `get_llm_gpu_status` (memory and slots per node) and
     `get_llm_models` (a model's last_error names a failed load). These pods rest at zero
     replicas and exist only while a model is loaded; no pod is not a fault.
   - core: `get_service_details` is the evidence there is; say what it shows.
4. If the docs may explain the symptom, `search_thinkube_docs` with the service name and
   the symptom, and `get_thinkube_doc` for the page.
5. Propose the action: restart (`restart_service` with the id), enable (`toggle_service`
   with the id and is_enabled true), redeploy (`redeploy_template` with the app's
   template_url and variables from its last deployment), or nothing. Redeploy only to
   apply a changed thinkube.yaml, never to ship code: it resets the app's checkout to
   Gitea's main and copies the template over it. Do it only with the user's agreement,
   then check `get_service_details` again.

Report: the service and its type, what is wrong in one sentence, the evidence (health,
pods, logs, run status), the likely cause, and the action taken or proposed.
"""


def prompt_definitions(domain: str) -> List[PromptDefinition]:
    """The prompts of this server, with the platform's domain in their texts.

    Args:
        domain: the platform domain, such as example.com; the gateway and the
            deployed apps are addressed under it
    """
    return [
        PromptDefinition(
            name="serve-model",
            description=(
                "Serve a model through the LLM gateway: mirror it if needed, "
                "pick a GPU node, load it, watch it come up, call it, unload it."
            ),
            messages=[PromptMessage(role="user", content=SERVE_MODEL.replace("{{domain}}", domain))],
            arguments=[
                PromptArgument(
                    name="model_id",
                    description="The model id as listed in the catalog, for example Qwen/Qwen3-8B",
                ),
                PromptArgument(
                    name="node",
                    description="The GPU node to load on; leave it out to choose from the load options",
                    required=False,
                    default="choose",
                ),
            ],
        ),
        PromptDefinition(
            name="deploy-app",
            description=(
                "Deploy an application from a template: parameters, secrets, "
                "the queued run, and the result."
            ),
            messages=[PromptMessage(role="user", content=DEPLOY_APP.replace("{{domain}}", domain))],
            arguments=[
                PromptArgument(
                    name="app_name",
                    description="The app's name: its namespace and its hostname",
                ),
                PromptArgument(
                    name="template",
                    description="The template's name from list_templates, or its GitHub URL",
                ),
            ],
        ),
        PromptDefinition(
            name="publish-template",
            description=(
                "Publish a deployed app as a template in the user's GitHub "
                "account and the Templates catalog."
            ),
            messages=[PromptMessage(role="user", content=PUBLISH_TEMPLATE)],
            arguments=[
                PromptArgument(
                    name="app_name",
                    description="The deployed app, a folder under /home/thinkube/apps",
                ),
                PromptArgument(
                    name="template_name",
                    description="The name of the template and of its GitHub repository",
                ),
                PromptArgument(
                    name="description",
                    description="One line for the catalog",
                ),
                PromptArgument(
                    name="private",
                    description="true for a private repository, false for a public one",
                    required=False,
                    default="true",
                ),
            ],
        ),
        PromptDefinition(
            name="build-notebook-env",
            description=(
                "Build a custom notebook virtualenv, follow its build, and "
                "make it available as a kernel."
            ),
            messages=[PromptMessage(role="user", content=BUILD_NOTEBOOK_ENV)],
            arguments=[
                PromptArgument(
                    name="name",
                    description="The venv's name, which becomes the kernel's name",
                ),
                PromptArgument(
                    name="packages",
                    description="The pip requirements to install, separated by commas",
                ),
                PromptArgument(
                    name="parent_template",
                    description="fine-tuning or agent-dev to start from that template's packages",
                    required=False,
                    default="none",
                ),
            ],
        ),
        PromptDefinition(
            name="diagnose-service",
            description=(
                "Diagnose a service: its health, pods, last deploy run and "
                "backend state, then propose the fix."
            ),
            messages=[PromptMessage(role="user", content=DIAGNOSE_SERVICE)],
            arguments=[
                PromptArgument(
                    name="service",
                    description="The service's name as list_services_minimal shows it",
                ),
            ],
        ),
    ]
