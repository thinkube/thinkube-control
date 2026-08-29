# TODO

Open work on thinkube-control and the pieces around it.

Each item says what to do. Where a choice has to be made first, the choice is
stated with its options, so the item can be picked up without rediscovering it.

---

## 1. Declare tensorrt gateway-managed

**Do:** add `gateway_managed: true` to the deployment block of
`templates/tkt-tensorrt-llm-harmony/thinkube.yaml`, commit, push. No redeploy —
it is not deployed.

**Why:** it rests at zero replicas like vllm and text-embeddings, both of which
now carry the flag. Without it, the first deployment reports Disabled while
serving.

Ready to do. No decision needed.

---

## 2. Make the generator emit the real deployment type

**Do:** pass the deployment type into
`backend/app/api/service_discovery_config.py` and emit it, instead of the
hardcoded `"type": "user_app"`. Mirror it in
`templates/service-configmap.yaml.j2`. Then redeploy vllm and text-embeddings so
their rows change type.

**Decide first:** components currently land in category `applications`. Give
them their own category, or leave it.

**Why:** the ConfigMap label already says `thinkube.io/service-type: component`,
set from `_is_component()` in `scripts/deploy_application.py`, while the body it
labels says `user_app`. The two disagree on every component deployment.

---

## 3. Give the four LLM backends one install path

**Decide first, this is the whole item:** ollama installs from Optional
Components and registers as `optional`; vllm, tensorrt and text-embeddings
install from Templates and register as `user_app`. Pick one:

- **a** — move ollama to a fixed-name template, so all four are templates.
- **b** — move the three inference templates to optional components.
- **c** — keep both paths, but make all four register with the same type and
  category so they read as one group in the UI.

**Do, once chosen:** implement the move, then redeploy the affected backends.

**Why:** four peers of the same gateway arrive by two routes and end up as two
different kinds of thing. Option **c** is the cheapest and depends on item 2.

---

## 4. Pin amd64-only optional components to amd64 nodes

**Do:** add a node-architecture restriction to the optional component
deployments — a `nodeSelector` on `kubernetes.io/arch`, expressed in the
component's own definition rather than hardcoded per component. Then add the
cvat menu entry with that restriction set.

**Why:** `ansible/40_thinkube/optional/cvat/` exists but has no menu entry.
Checked at v2.74.0, the current release: `cvat/server` and `cvat/ui` publish
single-arch `linux/amd64` manifests, no manifest list, no arm64 variant.
Upstream still does not ship arm64.

The cluster is mixed — tkamd1 and tkamd2 are amd64, tkspark is arm64 — so a
pinned cvat is viable. Nothing in the platform expresses such a restriction
today; that is the work. cvat is the first case, not the only one.

---

## 5. Stop components landing in the user's apps directory

**Decide first:** `/home/thinkube/apps/` holds both apps you edit (docs, todo)
and inference components you do not (vllm, text-embeddings). Pick one:

- **a** — give components their own directory and leave `apps/` for user apps.
- **b** — keep the location and make the checkout visibly not-for-editing.

**Do, once chosen:** for **a**, derive the checkout path from `_is_component()`
instead of the app name alone. For **b**, have the deploy write a marker into
component checkouts saying edits there are discarded.

**Why:** `local_repo_path` and `gitea_repo_name` are both built from the app name
alone (`scripts/deploy_application.py` lines 70 and 74) and `_is_component()`
reaches neither.

Note the checkout is **not** a spare copy — the deploy reads `thinkube.yaml`
from it, generates the k8s manifests in it, resolves build contexts against it,
installs git hooks into it, and pushes to Gitea from it. It cannot be dropped,
only moved or marked. The Gitea repo is load-bearing too: ArgoCD deploys from
`thinkube-deployments/<name>`.

The trap is that an edit in `apps/<name>` for a component looks like the source
of truth and is overwritten on the next deploy.

---

## 6. Check services on their internal endpoint

**Do:** in `backend/app/services/health_checker.py`, prefer a registered
internal endpoint over the public one when choosing what decides a service's
health.

**Decide first:** whether the public route should still be checked and reported
separately, so a broken ingress is visible rather than silent.

**Why:** endpoint selection takes the primary or first non-internal endpoint, so
every check traverses the ingress and its auth, and a service is reported down
when only its route is broken. Internal endpoints are already registered and
unused — clickhouse has
`http://clickhouse-clickhouse.clickhouse.svc.cluster.local:8123/ping` sitting
there while its check goes out through the gateway.

---

## Decided, not to fix

**LiteLLM stays as an optional component.** It is superseded by the LLM gateway
for local inference, but it routes to external providers such as OpenAI and
Claude, which the gateway does not do. As an optional component it costs
nothing. The open-core risk is understood and accepted for an optional install.

Do not raise this again as a defect.
