# TODO

Open work on thinkube-control and the pieces around it.

Each item says what to do. Decisions already taken are recorded under the item
that depends on them, so nothing has to be rediscovered.

---

## 1. Declare tensorrt gateway-managed

**Do:** add `gateway_managed: true` to the deployment block of
`templates/tkt-tensorrt-llm-harmony/thinkube.yaml`, commit, push. No redeploy —
it is not deployed.

**Why:** it rests at zero replicas like vllm and text-embeddings, both of which
now carry the flag. Without it, the first deployment reports Disabled while
serving.

---

## 2. Make the generator emit the real deployment type

**Decided:** components get their own category. They no longer land in
`applications`.

**Do:** pass the deployment type into
`backend/app/api/service_discovery_config.py` and emit it, instead of the
hardcoded `"type": "user_app"`. Mirror it in
`templates/service-configmap.yaml.j2`. Set the component category alongside it.
Then redeploy vllm and text-embeddings so their rows change type and category.

**Why:** the ConfigMap label already says
`thinkube.io/service-type: component`, set from `_is_component()` in
`scripts/deploy_application.py`, while the body it labels says `user_app`. The
two disagree on every component deployment.

---

## 3. List all components in one menu, keep two install mechanisms

**Decided:** everything is listed under Optional Components. How a component
installs stays split: the existing ones run their ansible playbook, the
inference ones go through the thinkube-control template deployment process.
One menu, two mechanisms.

**Do:**

- Add vllm, tensorrt and text-embeddings to `optional_components.json` in
  thinkube-metadata, carrying a `template` descriptor (template url and fixed
  name) in place of the `playbooks` block.
- Branch on which descriptor is present at the install, test and uninstall call
  sites in `backend/app/api/optional_components.py`. Playbook entries keep
  `get_playbook_path()`; template entries call the deployment process.
- Stop the Templates menu listing entries whose `deployment_type` is
  `component`. `repositories.json` still holds their template metadata — only
  the menu placement changes.

**This works because the listing does not care how a thing installs.**
`list_components()` reads only the presentation fields and installed state. The
`playbooks` block is consulted solely at install, test and uninstall time,
through `get_playbook_path()`. That is the seam.

**Two things come free:** the `requirements` gate that makes perses require
prometheus would let vllm declare a GPU requirement, and template components
gain a consistent uninstall path, which they do not have today.

**One assumption to revisit:** `backend/app/services/optional_components.py`
line 20 states that components require local playbooks, so only the platform
catalog is used and no user catalog is merged. Template components can come from
a user org, so that assumption blocks user-supplied components.

**Depends on item 2** for components to register as their own type and category.

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

## 5. Move components to their own directory, and track local edits

**Decided:** components get a `components/` directory of their own, added to the
workspace. `apps/` keeps only the apps the user works on.

**Decided:** editing a component stays allowed. Locking it down would mean
supporting every feature every model needs, which is not sustainable — vllm was
recently tweaked to support dflash, and that kind of change is legitimate. A
documented, modifiable standard component is the right shape.

**Do:**

- Derive the checkout path from `_is_component()` rather than the app name
  alone, so components land in `components/<name>` and apps stay in
  `apps/<name>`. Both paths are currently built from the app name at
  `scripts/deploy_application.py` lines 70 and 74.
- Add `components/` to the workspace.
- Record a flag when the user has modified a component checkout. The checkout is
  a git repo, so a dirty tree or commits ahead of the deployed revision is the
  natural signal.
- When a deploy would overwrite a modified component, warn first and make the
  user confirm. Today the overwrite is silent.

**Why the warning matters:** the deploy rewrites the checkout every time, so an
edit made there looks like the source of truth and disappears without a word.

**Note the checkout is not a spare copy.** The deploy reads `thinkube.yaml` from
it, generates the k8s manifests in it, resolves build contexts against it,
installs git hooks into it, and pushes to Gitea from it. It moves; it cannot be
dropped. The Gitea repo is load-bearing too — ArgoCD deploys from
`thinkube-deployments/<name>`.

---

## Decided, not to fix

**Health checks stay on the public endpoint.** Thinkube is a development
platform and external access is a design prerequisite. A check that traverses
the ingress and its auth is checking the thing that is actually required, so
"unhealthy" correctly means "not reachable the way users reach it". Switching
the deciding check to internal cluster addresses would contradict the
requirement.

Registered internal endpoints stay unused for health decisions.

**LiteLLM stays as an optional component.** It is superseded by the LLM gateway
for local inference, but it routes to external providers such as OpenAI and
Claude, which the gateway does not do. As an optional component it costs
nothing. The open-core risk is understood and accepted for an optional install.

Do not raise either of these again as a defect.
