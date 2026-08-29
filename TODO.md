# TODO

Open work on thinkube-control and the pieces around it. Each item says what is
wrong, where it lives, and what to do about it.

## 1. tensorrt does not declare itself gateway-managed

`templates/tkt-tensorrt-llm-harmony/thinkube.yaml` rests at zero replicas like
vllm and text-embeddings, but does not carry `gateway_managed: true`. The moment
it is deployed it will report Disabled while serving.

It is not deployed today, so nothing is broken yet. One line, same as its two
peers already carry.

## 2. Components register as user apps

`backend/app/api/service_discovery_config.py` hardcodes `"type": "user_app"` in
the generated service.yaml, while `scripts/deploy_application.py` sets the
ConfigMap label from `_is_component()`. So vllm and text-embeddings carry the
label `thinkube.io/service-type: component` and a body that says `user_app`, and
they land in the services table as `user_app` in category `applications`.

The generator needs to know the deployment type and emit it.

## 3. LLM backends are installed through two different menus

The gateway knows four backend types. They do not arrive the same way:

| Backend | Installed via | Registers as |
|---|---|---|
| ollama | Optional Components | `optional` |
| vllm | Templates | `user_app` |
| tensorrt | Templates | not deployed |
| text-embeddings | Templates | `user_app` |

Four peers, two install paths, two service types. Decide which path owns
inference backends and make all four use it.

## 4. cvat cannot run on the arm nodes

`ansible/40_thinkube/optional/cvat/` exists but cvat has no menu entry, because
the published cvat images are not built for arm64 and the cluster now includes
arm nodes.

Two things to settle:

- Check whether cvat publishes arm64 images now. If it does, the entry can just
  be added.
- If it does not, deployment has to be pinned to the amd64 nodes. That
  restriction is not implemented anywhere today and is the real work in this
  item.

## 5. Components leave a Gitea repo and an apps checkout

`scripts/deploy_application.py` sets both paths from the app name alone:

- line 70 — `local_repo_path = f"/home/thinkube/apps/{app_name}"`
- line 74 — `gitea_repo_name = app_name`

`_is_component()` never reaches either. So `/home/thinkube/apps/` mixes user
apps you work on (docs, todo) with platform inference components you do not
(vllm, text-embeddings), and `thinkube-deployments` in Gitea does the same.

Both copies are rewritten on every deploy, so an edit made in `apps/<name>` for
a component looks like the source of truth and is silently discarded.

Decide where component copies belong, if they should exist at all.

## 6. Health checks only probe the public ingress

`backend/app/services/health_checker.py` picks the primary or first non-internal
endpoint, so every check traverses the ingress and its auth. A service can be
reported down when only the route is broken.

Internal endpoints are already registered and unused — clickhouse has
`http://clickhouse-clickhouse.clickhouse.svc.cluster.local:8123/ping` sitting
there while the check goes out through the gateway.

## Decided, not to fix

**LiteLLM stays as an optional component.** It is superseded by the LLM gateway
for local inference, but it routes to external providers such as OpenAI and
Claude, which the gateway does not do. As an optional component it costs
nothing. The open-core risk is understood and accepted for an optional install.

Do not raise this again as a defect.
