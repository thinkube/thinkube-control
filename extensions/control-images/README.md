# Images

The Images part of Thinkube Control, as an extension: Control lists it in its
navigation and draws its page inside its own layout.

## What it does

- **Mirrored images.** The list of container images Thinkube knows in Harbor:
  the platform's own (`system`, read from the image manifests the install
  writes as ConfigMaps) and the ones a user mirrored (`user`). Mirroring an
  image copies it from a public registry to `library/<name>:<tag>` in Harbor,
  for the architectures of the cluster's nodes that the source provides.
- **Custom images.** Images built from a Dockerfile the user edits in
  Thinkube IDE, under `~/dockerfiles/custom/<name>`, and pushed to Harbor as
  `library/<name>:latest`.
- **Base images.** A mirrored or custom image marked as base, with the
  Dockerfile template that a custom image made from it starts with.
- **Notebook images.** `GET /api/v1/images/jupyter` lists the images whose
  metadata says `purpose: jupyter`. It needs no login; the platform reads it
  from inside the cluster.

## How a mirror and a build run

Both run as Argo Workflows in the namespace `argo`, as the account
`image-builder` and with its registry login, in the platform's Buildah image
(`backend/app/services/argo.py`):

- A mirror (`services/mirror.py`) reads the source's manifest list, keeps the
  Linux images of the cluster's architectures and pushes them to Harbor as
  one manifest list. Layers go from the source registry to Harbor directly.
- A build (`services/builds.py`) receives the image's folder as text, 900 KiB
  at most, builds it with Buildah on a node of the control plane's
  architecture, and pushes it.

The call that starts one answers at once. The backend records the workflow's
name and follows it every 10 seconds (`services/follow.py`): it copies the
pod's log into the database and records how the workflow ended. Nothing is
kept in memory, so a restart or a deploy of Images does not stop a mirror or
a build, and the next pass continues. The page polls the job or the build.

## What it needs from the cluster

Declared in `thinkube.yaml`:

- `access`: workflows (create, get, list, delete), pods, nodes and ConfigMaps
  (list), pod logs (get).
- `filesystems: [dockerfiles]`: the folder `dockerfiles` of the IDE's home,
  at `/storage/dockerfiles`. The pod therefore runs on the control plane
  node. The backend writes there as root and gives what it creates to the
  IDE's user (`services/dockerfiles.py`).

## Agents

The MCP server is at `https://control-images.<domain>/api/v1/mcp`
(`backend/app/mcp_server.py`). Its tools are named `images_...`. A Thinkube
Control API token signs in.

## Change it

See `CLAUDE.md`: the source is this folder of the thinkube-control
repository, and a deploy of the folder runs the tests.

## License

MIT - See [LICENSE](LICENSE)
