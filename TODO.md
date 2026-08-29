# TODO

No open items.

Everything found while fixing the service health checks has been done and
deployed. What was decided along the way is recorded below, so it does not get
re-opened as a defect.

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

## Worth knowing

**Components install two ways from one menu.** Everything lists under Optional
Components. A catalog entry carries either a `playbooks` block, which runs an
ansible playbook, or a `template` block, which goes through the template
deployment process. The listing never consults either, so only install,
uninstall and test branch.

`backend/app/services/optional_components.py` still states that only the
platform catalog is used and no user catalog is merged, because components
required local playbooks. Template components can come from a user org, so that
assumption is what blocks user-supplied components.

**Components declare the architectures they can run on.** A component whose
architectures no node offers is reported unavailable with the missing node
named, and installing one passes a node selector its playbook applies. cvat is
the first case: upstream still ships single-arch `linux/amd64` only.

**Components live in `components/`, not `apps/`.** The checkout is the deploy's
working directory, not a spare copy, so it moves rather than disappearing.
Editing one is supported: a deploy that would overwrite local work warns and
parks it on a branch and in the stash first.

**Gateway-managed backends rest at zero replicas.** They serve from per-node
Deployments the gateway creates when a model is loaded, so they report Idle
rather than Disabled. Disabling one scales what the gateway created and stops
placement; enabling one only clears the state and lets the gateway place again.
