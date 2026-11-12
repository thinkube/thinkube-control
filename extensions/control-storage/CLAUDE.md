# CLAUDE.md

This is an extension of Thinkube Control. Its source is the folder
`extensions/<name>` of the thinkube-control repository, where `<name>` is the
name of the folder this file is in.

The same files exist in two places:

- `extensions/<name>` in the thinkube-control repository: the source. Change
  it here. `manifest.yaml` names the secrets and parameters a deploy needs.
- `~/apps/<name>`, with the Gitea repository `thinkube-deployments/<name>`:
  the deployed copy. A deploy writes it. Never edit it and never commit to it:
  the next deploy replaces what was pushed there.

## Change the extension

1. Edit the files in `extensions/<name>` of the thinkube-control repository.
2. Commit and push to GitHub, with explicit paths:

   ```bash
   git add extensions/<name>/<files>
   git commit -m "<what changed>"
   git push
   ```

3. Deploy it with the thinkube-control MCP tool `deploy_extensions`, with
   `names`: `["<name>"]`.

   The deploy copies the folder over `~/apps/<name>`, removes the files the
   folder no longer has, regenerates `k8s/` from `thinkube.yaml`, and pushes
   to Gitea. That push runs the tests, builds the images and rolls them out.
   `get_deployment_status` follows it to the end of the build.

A deploy of Thinkube Control does not deploy its extensions, and a deploy of
an extension does not rebuild Control.

## Tests

The tests are in `backend/tests` and `frontend/src`. The build runs them on
every deploy; a failed test stops the rollout.

## Work inside the backend pod does not last

Anything you run inside the extension's backend pod, such as a script started
with `kubectl exec`, stops at the next deploy: the deploy replaces the pod.
